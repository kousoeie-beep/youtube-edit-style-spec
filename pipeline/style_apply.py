#!/usr/bin/env python3
"""styles/*.yaml（15本）を、実際のキャンバスで使える px 値まで解決する。

    uv run --with pyyaml --with pillow python3 style_apply.py --audit 1080 1920
    uv run --with pyyaml --with pillow python3 style_apply.py --plan kirinuki \
        --caps ../IMG_2514_v2/work/captions.json --canvas 1080 1920 --png 4

11周かけた `styles/*.yaml` は **15本すべて 1920x1080（横）前提**である
（schema.md「キャンバスは『回転後』の寸法で決める。全15スタイルは横前提」）。
実素材は 1080x1920（縦・iPhone rotation=-90）。そのまま適用すると座標が丸ごとずれる。

── 縦横問題への方針（採用したのは (a) 座標変換）─────────────────────
  1. `position.centerX/centerY` は **正規化値**なので、そのまま新キャンバスに掛ける。
     「centerY 0.85 = 下から15%」は設計意図であって 1080 の産物ではない。
  2. px 系フィールド（font_px* / stroke_px / padding_px / max_width_px / size /
     char_width_px / max_height_px）は **一様スケール s = canvas_w / 1920** を掛ける。
     - 幅を律速に取る理由: `max_width_px` は折返し幅（schema.md「max_width_px は
       折返し幅」）であり、W に追随しないと字幕が必ず溢れる。
     - **一様**にする理由: W比とH比を別々に掛けると `max_chars ≒ max_width_px/font_px`
       が変わり、11周分の max_chars / max_lines / 4辺検算・すきま検算の整合が
       **全部無効になる**。一様なら比が保存され、検算の前提が持ち越せる。
  3. ただし **16px は絶対要求**（schema.md の4辺検算・すきま検算）。比は保存されても
     16px は保存されない。s=0.5625 では横方向の実余裕が 0.5625 倍に縮む。
     → 「縦では横方向がきつくなり、縦方向はむしろ楽になる」。これが破綻の主因。
  4. **可読性の下限**（caption 系のみ）。s·font_px が下限を割ったら下限まで持ち上げ、
     schema.md「フィールドを直したら、そこから導いた値を全部やり直す」に従い
     max_chars / stroke_px / size.height を **再導出**してから検算し直す。
     下限の出所は run.py capseq の実測値（横 FS=92 / 縦 FS=72、ST=12 / 10）。

(b)「縦用の既定値を別に持つ」は、結局スタイル定義を使わない＝資産が繋がらない。
(c)「縦は基準プリセットへフォールバック」も同じ。(a) だけが 15本の position /
max_chars / z_order / 排他宣言をそのまま生かせる。破綻したものは表で落とす。
────────────────────────────────────────────────

`run.py` は一切変更しない。このファイルは import して使う独立モジュール。
"""
import glob
import json
import math
import os
import sys

REF_W, REF_H = 1920, 1080          # 15本すべてが前提にしている基準キャンバス
SHADOW_LT, SHADOW_RB = 3, 12       # schema.md 実測（alpha>0 の包絡値）
MIN_MARGIN = 16                    # 4辺検算の要求余裕
MIN_CLEARANCE = 16                 # role 間すきまの要求
CHAEN_FONT, CHAEN_STROKE = 92, 12  # chaen デフォルト（caption_font の基準ペア）

STYLES_DIR = os.path.expanduser(
    "~/kousuke-vault/.claude/skills/chaen-youtube-edit/styles")
FONT_CANDIDATES = (
    os.path.expanduser("~/Library/Fonts/NotoSansJP-Bold.ttf"),
    "/Library/Fonts/NotoSansJP-Bold.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
)

# lint.py と同一の実測行高（NotoSansJP ascent+descent）。ここを別表にすると
# 検算結果が lint と食い違うので値ごと写す。
LINE_HEIGHT = {23: 34, 30: 44, 34: 50, 36: 53, 44: 65, 45: 66, 48: 70, 50: 73,
               52: 76, 56: 82, 60: 88, 64: 94, 70: 103, 72: 106, 75: 110,
               92: 134, 112: 164, 150: 218, 280: 409}
FONT_KEYS = ("font_px", "font_px_max", "font_px_en", "font_px_ja", "font_px_name",
             "font_px_attr", "font_px_sub", "font_px_logo", "font_px_title",
             "font_px_item")
PX_SCALAR_KEYS = FONT_KEYS + ("stroke_px", "padding_px", "max_width_px",
                              "char_width_px", "max_height_px")

# 発話に追従する主役字幕の role 名。優先順（先にあるものを採る）。
CAPTION_ROLES = ("caption", "speech_caption", "dialogue_caption", "base_caption",
                 "quote_caption", "lower_third", "fact_caption",
                 "bilingual_caption")

# run.py capseq の実測ベースライン。可読性下限の出所はここ。
BASELINE = {"landscape": {"font": 92, "stroke": 12, "canvas_w": 1920},
            "portrait":  {"font": 72, "stroke": 10, "canvas_w": 1080}}


class NoCaptionRoleError(Exception):
    """そのスタイルが発話字幕を持たない（テロップ装置スタイル）。

    2026-08-01: 「不可」と同じ扱いにしていたが別物。
    entertainment_variety は caption_font 自身が「このスタイルには全発話を
    カバーする標準字幕ロールが存在しない」と書いており、role `caption` は
    `layer_kind: video`（リアクションPiP窓）。educational_explainer も
    6roleすべてがテロップ装置で発話字幕が無い。
    **直せる欠陥ではなく、そのスタイルの設計事実。**
    再現率の分母に入れると、直しようのないものを永久に未達として数えることになる。
    """


class StyleUnfitError(Exception):
    """そのキャンバスでは検算を通らないスタイル。"""


# ── ユーティリティ ─────────────────────────────────────────
def line_height(font):
    if font in LINE_HEIGHT:
        return LINE_HEIGHT[font]
    return round(font * 1.465)


def _num(v):
    """yaml の値が数値なら返す。散文・"変数"・"非該当"・None は None。"""
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _sc(v, s):
    n = _num(v)
    return None if n is None else int(round(n * s))


def default_stroke(font):
    """`stroke_px` 未宣言時のフチ幅。chaen の 92:12 の比を保つ。

    切り上げで 92→12 / 72→10 となり、run.py capseq の実測値と一致する
    （72*12/92 = 9.39。四捨五入だと 9 になり run.py と 1px ずれる）。
    """
    return max(1, math.ceil(font * CHAEN_STROKE / CHAEN_FONT))


def load_yaml(style_id):
    import yaml
    p = os.path.join(STYLES_DIR, f"{style_id}.yaml")
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def all_style_ids():
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(STYLES_DIR, "*.yaml")))


def declared_aspect(doc):
    """`format.aspect`。クォートし忘れた `16:9` は YAML1.1 の60進で 969 になる。"""
    a = (doc.get("format") or {}).get("aspect")
    return "16:9" if a in (969, "16:9") else str(a)


def variants_of(layers):
    """ファイル内の変種名を出現順に。`variant` はスカラも配列も来る。"""
    seen = []
    for l in layers:
        v = l.get("variant")
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x not in seen:
                seen.append(x)
    return seen


def in_scope(layer, variant):
    v = layer.get("variant")
    if v is None:
        return True                      # 変種を持たない role は全変種で出る
    vs = v if isinstance(v, list) else [v]
    return variant in vs


# ── 1role の解決 ──────────────────────────────────────────
def resolve_role(layer, s, cw, ch, orientation, variant=None):
    """yaml の1レイヤを、そのキャンバスの px 実数値まで確定させる。"""
    # 【2026-08-01】variant_geometry を一度も読んでいなかった（grep で0件）。
    #  lint._rect は R? の時点で対応済み。変種ごとに位置と幅が変わる role を
    #  role直下の値で評価していたため、ai_tool_avatar は mode_A の caption を
    #  mode_B の座標で見て nameplate と −87.6px 交差しているように見えていた。
    vg = layer.get("variant_geometry")
    if variant and isinstance(vg, dict) and isinstance(vg.get(variant), dict):
        ov = vg[variant]
        po_ = {**(layer.get("position") or {}),
               **{k: ov[k] for k in ("centerX", "centerY") if k in ov}}
        layer = {**layer, **{k: v for k, v in ov.items()
                             if k not in ("centerX", "centerY")}, "position": po_}
    role = layer.get("role")
    r = {"role": role, "resolved": False, "reason": None, "notes": [],
         "z_order": layer.get("z_order"),
         "persistent": bool(layer.get("persistent")),
         "variant": layer.get("variant"),
         "layer_kind": layer.get("layer_kind"),
         "opaque_fullscreen": bool(layer.get("opaque_fullscreen")),
         "edge_bleed": bool(layer.get("edge_bleed")),
         "overflow_policy": layer.get("overflow_policy"),
         "is_caption": role in CAPTION_ROLES,
         "mutually_exclusive_with": layer.get("mutually_exclusive_with") or [],
         "intentional_overlap_with": layer.get("intentional_overlap_with") or []}

    po = layer.get("position")
    if not isinstance(po, dict):
        r["reason"] = "position が無い"
        return r
    cx_n, cy_n = _num(po.get("centerX")), _num(po.get("centerY"))
    if cx_n is None or cy_n is None:
        r["reason"] = f"position が数値でない（{po.get('centerX')!r},{po.get('centerY')!r}）"
        return r

    # ① 正規化座標はそのまま新キャンバスへ
    r["center"] = (cx_n * cw, cy_n * ch)
    r["position_norm"] = (cx_n, cy_n)

    # ② px 系は一様スケール
    for k in PX_SCALAR_KEYS:
        if k in layer:
            r[k] = _sc(layer[k], s)
    fonts = [r[k] for k in FONT_KEYS if isinstance(r.get(k), int)]
    if fonts:
        r["font_px"] = max(fonts)
    else:
        # schema.md「caption_font は font_px を持たない role の既定値として読む」。
        # lint.py の FONT_PX_DEFAULT=92 と同値にして、検算結果が lint とずれないようにする。
        r["font_px"] = max(1, int(round(CHAEN_FONT * s)))
        r["font_default"] = True
        r["notes"].append(f"font_px 未宣言→chaenデフォルト{CHAEN_FONT}×{s:.4f}"
                          f"={r['font_px']}px（lint.FONT_PX_DEFAULT と同値）")

    sz = layer.get("size")
    if r["opaque_fullscreen"]:
        # 「画面を覆う」のが仕事の role は**スケールしてはいけない**。
        # 一様スケールを掛けると 1920x1080 → 1080x607 = 被覆31.6% になり、
        # schema.md「opaque_fullscreen を名乗るなら size で全画面を示す」
        # （overlap_qc は被覆98%未満の宣言を却下する）を自分で破る。
        r["size"], r["size_declared"] = (cw, ch), True
        r["center"] = (cw / 2, ch / 2)
        r["notes"].append(f"opaque_fullscreen → size を実キャンバス {cw}x{ch} に置換"
                          "（スケールすると被覆31.6%になる）")
    elif isinstance(sz, dict):
        w, h = _sc(sz.get("width"), s), _sc(sz.get("height"), s)
        r["size"] = (w, h) if (w is not None and h is not None) else None
        r["size_declared"] = r["size"] is not None
    else:
        r["size"], r["size_declared"] = None, False

    ml = layer.get("max_lines")
    r["max_lines"] = ml if isinstance(ml, int) else None
    mc = layer.get("max_chars")
    r["max_chars_declared"] = mc if isinstance(mc, int) else None

    # ③ 可読性下限（caption 系のみ）。持ち上げたら派生値を全部やり直す
    if r["is_caption"] and r["font_px"]:
        floor = round(BASELINE[orientation]["font"] * cw
                      / BASELINE[orientation]["canvas_w"])
        # 【2026-08-01】この下限は「縮小して読めなくなるのを防ぐ」ためのもので、
        #  スタイル自身が基準キャンバスで実測した値を上書きするためのものではない。
        #  s=1.0（横そのまま）でも floor=92 が常に発火し、yamlの 75/72/50 を潰していた。
        #  documentary_investigative と documentary_narrated_jp はこれだけで「不可」になり、
        #  screen_tutorial は**「可」と判定されたまま描画だけ 50→92 に壊れていた**。
        _dec = [x for x in (_num(layer.get(k)) for k in FONT_KEYS) if x is not None]
        floor = min(floor, int(round(max(_dec)))) if _dec else floor
        if r["font_px"] < floor:
            old = r["font_px"]
            k = floor / old
            # 先に従属フォントを倍率で動かし、**最後に** font_px を下限へ置く。
            # 順序を逆にすると font_px 自身が二重に掛かる（実際にやった: 72→123px）
            for fk in FONT_KEYS:
                if isinstance(r.get(fk), int):
                    r[fk] = max(1, int(round(r[fk] * k)))
            r["font_px"] = floor
            if isinstance(r.get("stroke_px"), int):
                r["stroke_px"] = max(1, int(round(r["stroke_px"] * k)))
            if isinstance(r.get("char_width_px"), int):
                r["char_width_px"] = max(1, int(round(r["char_width_px"] * k)))
            r["notes"].append(
                f"font {old}→{floor}px（{orientation} 可読性下限・出所 run.py capseq）"
                f"。派生の stroke/char_width/max_chars/size.height を再導出")
            r["font_lifted"] = True

    if r.get("stroke_px") is None:
        r["stroke_px"] = default_stroke(r["font_px"]) if r["font_px"] else 0
        if r["font_px"]:
            r["notes"].append(f"stroke_px 未宣言→{r['stroke_px']}px（92:12比）")
    r["padding_px"] = r.get("padding_px") or 0

    # ④ max_chars は宣言をそのまま使わず、解決後の幾何から再導出する
    #    （schema.md「派生値は分母を動かした周で全部やり直す」）
    adv = r.get("char_width_px") or r["font_px"]
    if r.get("max_width_px") and adv:
        r["max_chars"] = max(1, int(r["max_width_px"] // adv))
        if (r["max_chars_declared"] is not None
                and r["max_chars"] != r["max_chars_declared"]):
            r["notes"].append(
                f"max_chars {r['max_chars_declared']}→{r['max_chars']}"
                f"（{r['max_width_px']}px ÷ {adv}px の再導出）")
    else:
        r["max_chars"] = r["max_chars_declared"]

    # ⑤ size。宣言があれば尊重するが、font を持ち上げた caption は箱も作り直す
    #    （schema.md「箱と中身のどちらか一方だけ決めると必ずもう一方が破綻する」）
    pad_st = r["padding_px"] + (r["stroke_px"] or 0)
    if r["size"] and r.get("font_lifted"):
        need_h = line_height(r["font_px"]) * (r["max_lines"] or 1) + pad_st * 2
        if need_h > r["size"][1]:
            r["notes"].append(f"size.height {r['size'][1]}→{need_h}（font持ち上げ分）")
            r["size"] = (r["size"][0], need_h)
    if not r["size"]:
        if r.get("max_width_px") and r["font_px"]:
            ml_ = r["max_lines"] or 1
            r["size"] = (r["max_width_px"] + pad_st * 2,
                         int(round(line_height(r["font_px"]) * ml_ + pad_st * 2)))
            r["size_estimated"] = True
            r["notes"].append("size 未宣言→max_width_px と行高から推定（lint._rect と同式）")
        else:
            r["reason"] = "size も max_width_px も無く矩形を確定できない"
            return r

    w, h = r["size"]
    cx, cy = r["center"]
    r["rect"] = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    no_shadow = str(layer.get("color_shadow", "")).strip('"') == "none"
    lt, rb = (0, 0) if no_shadow else (SHADOW_LT, SHADOW_RB)
    r["rect_shadow"] = (r["rect"][0] - lt, r["rect"][1] - lt,
                        r["rect"][2] + rb, r["rect"][3] + rb)
    r["shadow"] = (lt, rb)
    r["resolved"] = True
    return r


# ── 検算 ────────────────────────────────────────────────
def check_four_edges(roles, cw, ch):
    """4辺検算。lint.check_four_edges と同じ除外規則・同じ影項。"""
    ng = []
    for r in roles:
        if not r["resolved"] or r["opaque_fullscreen"] or r["edge_bleed"]:
            continue
        x0, y0, x1, y1 = r["rect"]
        lt, rb = r["shadow"]
        m = {"左": x0 - lt, "右": cw - (x1 + rb),
             "上": y0 - lt, "下": ch - (y1 + rb)}
        bad = {k: round(v, 1) for k, v in m.items() if v < MIN_MARGIN}
        if bad:
            # 宣言 size か推定 size かで証拠の質が違う。lint.check_four_edges は
            # **宣言 size を持つ role しか見ない**ので、推定側は lint に出てこない
            ng.append((r["role"], bad, {k: round(v, 1) for k, v in m.items()},
                       "推定" if r.get("size_estimated") else "宣言"))
    return ng


def _linked(a, b):
    for x, y in ((a, b), (b, a)):
        for key in ("mutually_exclusive_with", "intentional_overlap_with"):
            v = x.get(key) or []
            if y["role"] in (v if isinstance(v, list) else [v]):
                return True
    return False


def check_clearance(roles):
    """同時表示しうる role 間のすきま。交差(<0)と近接(<16)を両方返す。"""
    ng = []
    live = [r for r in roles if r["resolved"] and not r["opaque_fullscreen"]]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a["role"] == b["role"] or _linked(a, b):
                continue
            ra, rb = a["rect_shadow"], b["rect_shadow"]
            gx = max(ra[0], rb[0]) - min(ra[2], rb[2])
            gy = max(ra[1], rb[1]) - min(ra[3], rb[3])
            gap = max(gx, gy)
            if gap < MIN_CLEARANCE - 1e-6:
                ng.append((a["role"], b["role"], round(gap, 1),
                           "交差" if gap < 0 else "近接"))
    return ng


# ── 公開API ────────────────────────────────────────────
def resolve_style(style_id, canvas_w, canvas_h, variant=None):
    """検算結果つきの完全レポートを返す（判定表はこれを使う）。"""
    doc = load_yaml(style_id)
    layers = [l for l in (doc.get("telop_layers") or []) if isinstance(l, dict)]
    orientation = "portrait" if canvas_h > canvas_w else "landscape"
    s = canvas_w / REF_W
    vs = variants_of(layers)

    def build(v):
        rs = [resolve_role(l, s, canvas_w, canvas_h, orientation, v)
              for l in layers if in_scope(l, v)]
        return rs

    if vs and variant is None:
        # schema.md「適用前に variant 判定が必須」。caption 系が解決する変種を選ぶ
        variant = next((v for v in vs
                        if any(r["is_caption"] and r["resolved"] for r in build(v))),
                       vs[0])
    roles = build(variant)

    edges = check_four_edges(roles, canvas_w, canvas_h)
    gaps = check_clearance(roles)
    # caption 役の選定。全画面カード（opaque_fullscreen）と動画レイヤ（PiP）は
    # role 名が `caption` でも発話字幕ではないので除く
    # （business_talk.caption=全画面カード / entertainment_variety.caption=ワイプ映像）。
    cap = None
    for name in CAPTION_ROLES:
        cap = next((r for r in roles
                    if r["role"] == name and r["resolved"]
                    and not r["opaque_fullscreen"]
                    and r["layer_kind"] != "video"), None)
        if cap:
            break

    bad_roles = {n for n, _, _, _ in edges} | {n for a, b, _, _ in gaps for n in (a, b)}
    cap_bad = bool(cap and cap["role"] in bad_roles)
    if cap is None:
        verdict = "字幕role無し"
        why = "発話字幕のroleを持たない（テロップ装置スタイル）"
    elif cap_bad:
        verdict = "不可"
        why = f"caption({cap['role']}) 自身が検算NG"
    elif edges or gaps:
        verdict = "条件付き可"
        why = f"caption は成立。補助role {len(bad_roles)}件がNG"
    else:
        verdict = "可"
        why = "全roleが4辺16px・すきま16pxを満たす"

    zs = [r["z_order"] for r in roles if r["resolved"] and r["z_order"] is not None]
    dup_z = len(zs) != len(set(zs))

    return {"style_id": style_id, "aspect_declared": declared_aspect(doc),
            "canvas": (canvas_w, canvas_h), "orientation": orientation,
            "scale": s, "variant": variant, "variants": vs,
            "roles": roles, "caption_role": cap,
            "ng_edges": edges, "ng_gaps": gaps, "dup_z_order": dup_z,
            "verdict": verdict, "why": why,
            "unresolved": [(r["role"], r["reason"])
                           for r in roles if not r["resolved"]]}


def load_style(style_id, canvas_w, canvas_h, variant=None, strict=True):
    """styles/<style_id>.yaml を読み、そのキャンバスで使える形に解決して返す。

    role ごとに font_px / stroke_px / max_chars / max_lines / position(px) /
    size(px) / overflow_policy / z_order を px の実数値まで確定させる。
    縦横の変換規則を適用し、4辺検算とすきま検算を通した結果だけを返す。
    """
    rep = resolve_style(style_id, canvas_w, canvas_h, variant)
    if rep["verdict"] == "字幕role無し":
        raise NoCaptionRoleError(f"{style_id}: {rep['why']}")
    if rep["verdict"] == "不可" or (strict and rep["verdict"] != "可"):
        raise StyleUnfitError(
            f"{style_id} は {canvas_w}x{canvas_h} で成立しない: {rep['why']}\n"
            + "".join(f"  4辺NG {n} {bad}（{src}矩形）\n"
                      for n, bad, _, src in rep["ng_edges"])
            + "".join(f"  すきまNG {a}×{b} {g}px（{k}）\n"
                      for a, b, g, k in rep["ng_gaps"]))
    return rep


# ── 折返しと描画計画 ────────────────────────────────────────
_FONT_CACHE = {}


def _pil_font(px):
    from PIL import ImageFont
    if px not in _FONT_CACHE:
        fp = next((q for q in FONT_CANDIDATES if os.path.exists(q)), None)
        if not fp:
            raise FileNotFoundError("NotoSansJP-Bold.ttf が見つからない")
        _FONT_CACHE[px] = ImageFont.truetype(fp, px)
    return _FONT_CACHE[px]


def _measurer(font_px, stroke_px):
    """実フォントで幅を測る。Pillow が無ければ全角1字=font_px で近似。"""
    try:
        from PIL import Image, ImageDraw
        f = _pil_font(font_px)
        d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

        def width(t):
            b = d.textbbox((0, 0), t, font=f, stroke_width=stroke_px)
            return b[2] - b[0]
        return width, d, f
    except Exception:
        return (lambda t: len(t) * font_px), None, None


# 行頭に置いてはいけない字（禁則処理）。environment-notes.md L23 が
# 「どの折返し実装にも必ず入れる」と定めている。
KINSOKU_HEAD = "。、，．！？!?」』）)〕】〉》”’ぁぃぅぇぉっゃゅょゎヵヶーぐ々"

_TAGGER = None

def bunsetsu(text):
    """自立語＋付属語で区切る。**折返しもここで切らないと語が割れる。**

    2026-08-01: 字幕の切り出し（chunk）は文節でやっていたのに、
    1つの字幕を2行に折り返す側が文字単位のままで、
    「新人研修とか絶対いいで／すね。」のように語の途中で改行されていた。
    切り出しと折返しは**別の実装**なので、片方だけ直しても直らない。
    """
    global _TAGGER
    if _TAGGER is None:
        try:
            from fugashi import Tagger
            _TAGGER = Tagger()
        except Exception:
            _TAGGER = False
    if not _TAGGER:
        return list(text)                      # 形態素解析が無ければ従来どおり
    JIRITSU = ("名詞", "動詞", "形容詞", "副詞", "連体詞", "接続詞", "感動詞", "代名詞")
    out = []
    for m in _TAGGER(text):
        pos = m.feature.pos1 or ""
        if out and not (pos in JIRITSU and m.surface and m.surface[0] not in KINSOKU_HEAD):
            out[-1] += m.surface
        else:
            out.append(m.surface)
    return [x for x in out if x]


def wrap_text(text, width_of, max_width):
    units = bunsetsu(text)
    lines, cur = [], ""
    for u in units:
        if cur and width_of(cur + u) > max_width:
            # 文節1つで幅を超えるなら、その文節だけ字で割る（他に手が無い）
            if not cur and width_of(u) > max_width:
                pass
            lines.append(cur); cur = u
            while width_of(cur) > max_width and len(cur) > 1:
                k = len(cur)
                while k > 1 and width_of(cur[:k]) > max_width: k -= 1
                lines.append(cur[:k]); cur = cur[k:]
        else:
            cur += u
    if cur:
        lines.append(cur)
    # 禁則: 行頭に来てしまった字は前の行の末尾へ送る（幅は1字分まで超過を許容）
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in KINSOKU_HEAD and lines[i-1]:
            lines[i-1] += lines[i][0]; lines[i] = lines[i][1:]
    return [l for l in lines if l]


def split_caption(cap, width_of, max_width, max_lines):
    """max_lines に収まらない発話を、時間ごと複数イベントへ割る。

    schema.md「max_lines を超えるテキストは折り返さずイベントを分割する」。
    区間は半開 [start, end)。分割は文字数按分。
    """
    lines = wrap_text(cap["text"], width_of, max_width)
    if len(lines) <= max_lines:
        return [dict(cap, _lines=lines)]
    out, span = [], cap["end"] - cap["start"]
    total = sum(len(l) for l in lines) or 1
    used, t = 0, cap["start"]
    for i in range(0, len(lines), max_lines):
        grp = lines[i:i + max_lines]
        n = sum(len(l) for l in grp)
        t1 = cap["start"] + span * (used + n) / total
        out.append({"start": round(t, 3), "end": round(t1, 3),
                    "text": "".join(grp), "_lines": grp, "_split": True})
        used += n
        t = t1
    out[-1]["end"] = cap["end"]
    return out


def render_plan(style, caps, canvas_w, canvas_h):
    """字幕チャンクを、そのスタイルの caption role へ割り当てた描画計画を返す。

    1エントリ = 実際に描く1行（x,y はそのまま ImageDraw.text に渡せる座標）。
    同一発話の行は event_index で束ねられる。
    """
    cap_role = style.get("caption_role")
    if not cap_role:
        raise StyleUnfitError(f"{style['style_id']}: caption 系 role が無い")
    font_px, stroke_px = cap_role["font_px"], cap_role["stroke_px"] or 0
    max_lines = cap_role["max_lines"] or 2
    max_w = cap_role.get("max_width_px") or (cap_role["size"][0]
                                             - (cap_role["padding_px"] + stroke_px) * 2)
    cx, cy = cap_role["center"]
    lh = line_height(font_px)
    width_of, draw, font = _measurer(font_px, stroke_px)

    events = []
    for c in caps:
        events += split_caption(c, width_of, max_w, max_lines)

    plan = []
    for ei, ev in enumerate(events):
        lines = ev.get("_lines") or wrap_text(ev["text"], width_of, max_w)
        lines = lines[:max_lines]
        top = cy - lh * len(lines) / 2
        for k, ln in enumerate(lines):
            if draw is not None:
                b = draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_px)
                w = b[2] - b[0]
                x = cx - w / 2 - b[0]
                y = top + lh * k - b[1]
                ink = (cx - w / 2, top + lh * k,
                       cx + w / 2, top + lh * k + (b[3] - b[1]))
            else:
                w = width_of(ln)
                x, y = cx - w / 2, top + lh * k
                ink = (x, y, x + w, y + lh)
            plan.append({
                "role": cap_role["role"], "text": ln,
                "x": int(round(x)), "y": int(round(y)),
                "font_px": font_px, "stroke_px": stroke_px,
                "start": ev["start"], "end": ev["end"],   # 半開区間 [start, end)
                "z_order": cap_role["z_order"],
                "event_index": ei, "line_index": k, "lines": len(lines),
                "bbox": tuple(round(v, 1) for v in ink),
                "split": bool(ev.get("_split")),
            })
    return plan


def verify_plan(plan, style, canvas_w, canvas_h):
    """描画計画そのものを 4辺16px・他role とのすきま16px で検算する。"""
    edges, gaps = [], []
    others = [r for r in style["roles"]
              if r["resolved"] and not r["opaque_fullscreen"]
              and r["role"] != style["caption_role"]["role"]]
    for p in plan:
        x0, y0, x1, y1 = p["bbox"]
        m = {"左": x0 - SHADOW_LT, "右": canvas_w - (x1 + SHADOW_RB),
             "上": y0 - SHADOW_LT, "下": canvas_h - (y1 + SHADOW_RB)}
        bad = {k: round(v, 1) for k, v in m.items() if v < MIN_MARGIN}
        if bad:
            edges.append((p["event_index"], p["line_index"], p["text"], bad))
        for o in others:
            if _linked(style["caption_role"], o):
                continue
            ro = o["rect_shadow"]
            ra = (x0 - SHADOW_LT, y0 - SHADOW_LT, x1 + SHADOW_RB, y1 + SHADOW_RB)
            g = max(max(ra[0], ro[0]) - min(ra[2], ro[2]),
                    max(ra[1], ro[1]) - min(ra[3], ro[3]))
            if g < MIN_CLEARANCE - 1e-6:
                gaps.append((p["event_index"], o["role"], round(g, 1)))
    mins = [min(p["bbox"][0] - SHADOW_LT, canvas_w - (p["bbox"][2] + SHADOW_RB),
                p["bbox"][1] - SHADOW_LT, canvas_h - (p["bbox"][3] + SHADOW_RB))
            for p in plan] or [0]
    return {"edge_ng": edges, "gap_ng": gaps, "min_margin": round(min(mins), 1)}


def draw_png(plan, style, canvas_w, canvas_h, out_dir, n=4, base=None):
    """目視用に、実キャンバス寸法で数枚だけ描く。"""
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    evs = sorted({p["event_index"] for p in plan})
    pick = evs[::max(1, len(evs) // n)][:n]
    made = []
    for ei in pick:
        im = Image.new("RGBA", (canvas_w, canvas_h), (24, 26, 30, 255))
        d = ImageDraw.Draw(im)
        # セーフエリア（16px）を薄く引いて、目視で余裕を確認できるようにする
        d.rectangle([MIN_MARGIN, MIN_MARGIN, canvas_w - MIN_MARGIN,
                     canvas_h - MIN_MARGIN], outline=(80, 90, 110, 255), width=2)
        for r in style["roles"]:
            if not r["resolved"] or r["role"] == style["caption_role"]["role"]:
                continue
            x0, y0, x1, y1 = r["rect"]
            d.rectangle([x0, y0, x1, y1], outline=(120, 200, 255, 200), width=3)
            d.text((x0 + 6, y0 + 4), r["role"], fill=(120, 200, 255, 255))
        for p in plan:
            if p["event_index"] != ei:
                continue
            f = _pil_font(p["font_px"])
            d.text((p["x"] + 3, p["y"] + 3), p["text"], font=f, fill=(0, 0, 0, 115),
                   stroke_width=p["stroke_px"], stroke_fill=(0, 0, 0, 115))
            d.text((p["x"], p["y"]), p["text"], font=f, fill=(255, 255, 255, 255),
                   stroke_width=p["stroke_px"], stroke_fill=(0, 0, 0, 255))
        q = os.path.join(out_dir, f"{style['style_id']}_{canvas_w}x{canvas_h}_ev{ei:03d}.png")
        im.convert("RGB").save(q)
        made.append(q)
    return made


# ── CLI ────────────────────────────────────────────────
def _audit(cw, ch):
    print(f"# 15スタイル適用可否 @ {cw}x{ch}"
          f"（scale={cw / REF_W:.4f}・基準 {REF_W}x{REF_H}）\n")
    print("| style_id | 宣言aspect | 変種 | 判定 | caption役 | font | max_chars×行 | 4辺NG | すきまNG | 主因 |")
    print("|---|---|---|:--:|---|--:|---|--:|--:|---|")
    rows = []
    for sid in all_style_ids():
        try:
            r = resolve_style(sid, cw, ch)
        except Exception as e:
            print(f"| `{sid}` | — | — | エラー | — | — | — | — | — | {e} |")
            continue
        c = r["caption_role"]
        cn = f"`{c['role']}`" if c else "—"
        cf = c["font_px"] if c else "—"
        cm = f"{c['max_chars']}×{c['max_lines'] or '?'}" if c else "—"
        print(f"| `{sid}` | {r['aspect_declared']} | {r['variant'] or '—'} "
              f"| **{r['verdict']}** | {cn} | {cf} | {cm} "
              f"| {len(r['ng_edges'])} | {len(r['ng_gaps'])} | {r['why']} |")
        rows.append(r)
    print()
    for r in rows:
        if r["ng_edges"] or r["ng_gaps"] or r["unresolved"]:
            print(f"### {r['style_id']}（{r['verdict']}）")
            for n, bad, full, src in r["ng_edges"]:
                print(f"- 4辺[{src}矩形]: `{n}` {bad} / 全辺 {full}")
            for a, b, g, k in r["ng_gaps"]:
                print(f"- すきま: `{a}` × `{b}` = {g}px（{k}）")
            for n, why in r["unresolved"]:
                print(f"- 未解決: `{n}` — {why}")
            print()
    return rows


def _compare(cw, ch):
    """横(1920x1080)と縦の判定を並べ、**縦にしたせいで壊れたもの**だけを切り出す。"""
    print(f"# 横 {REF_W}x{REF_H} → 縦 {cw}x{ch} の差分\n")
    print("| style_id | 横の判定 | 縦の判定 | 縦で新たに割れたrole（辺/実余裕px） |")
    print("|---|:--:|:--:|---|")
    for sid in all_style_ids():
        a, b = resolve_style(sid, REF_W, REF_H), resolve_style(sid, cw, ch)
        old = {n for n, _, _, _ in a["ng_edges"]} | {x for p in a["ng_gaps"]
                                                     for x in p[:2]}
        new = []
        for n, bad, _, _ in b["ng_edges"]:
            if n not in old:
                new.append(f"`{n}`（{'/'.join(f'{k}{v}' for k, v in bad.items())}）")
        for x, y, g, _ in b["ng_gaps"]:
            if x not in old and y not in old:
                new.append(f"`{x}`×`{y}`（すきま{g}）")
        print(f"| `{sid}` | {a['verdict']} | {b['verdict']} | "
              f"{'、'.join(new) if new else '—'} |")


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", nargs=2, type=int, metavar=("W", "H"))
    ap.add_argument("--compare", nargs=2, type=int, metavar=("W", "H"))
    ap.add_argument("--plan")
    ap.add_argument("--caps")
    ap.add_argument("--canvas", nargs=2, type=int, default=[1080, 1920])
    ap.add_argument("--variant")
    ap.add_argument("--png", type=int, default=0)
    ap.add_argument("--out", default="./style_preview")
    a = ap.parse_args(argv)

    if a.audit:
        _audit(*a.audit)
        return 0
    if a.compare:
        _compare(*a.compare)
        return 0
    if a.plan:
        cw, ch = a.canvas
        st = resolve_style(a.plan, cw, ch, a.variant)
        print(f"{a.plan} @ {cw}x{ch}: {st['verdict']} — {st['why']}")
        c = st["caption_role"]
        print(f"  caption role={c['role']} font={c['font_px']} stroke={c['stroke_px']} "
              f"max_width={c.get('max_width_px')} max_chars={c['max_chars']} "
              f"max_lines={c['max_lines']} center={tuple(round(v) for v in c['center'])}")
        for n in c["notes"]:
            print(f"    note: {n}")
        caps = json.load(open(a.caps, encoding="utf-8")) if a.caps else []
        plan = render_plan(st, caps, cw, ch)
        v = verify_plan(plan, st, cw, ch)
        ev = len({p["event_index"] for p in plan})
        print(f"  発話{len(caps)}件 → イベント{ev}件 / 描画行{len(plan)}行 "
              f"（分割 {sum(1 for p in plan if p['split'] and p['line_index'] == 0)}件）")
        print(f"  4辺検算NG {len(v['edge_ng'])}件 / すきまNG {len(v['gap_ng'])}件 "
              f"/ 最小余裕 {v['min_margin']}px")
        for e in v["edge_ng"][:10]:
            print(f"    NG ev{e[0]} L{e[1]} {e[2]!r} {e[3]}")
        for g in v["gap_ng"][:10]:
            print(f"    NG ev{g[0]} × {g[1]} = {g[2]}px")
        if a.png:
            for q in draw_png(plan, st, cw, ch, a.out, a.png):
                print(f"  PNG {q}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
