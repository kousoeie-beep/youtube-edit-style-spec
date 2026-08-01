#!/usr/bin/env python3
"""styles/*.yaml の機械照合。実レンダリング前に必ず流す。

    uv run --with pyyaml --offline python3 styles/lint.py

ここに入っている検査は、すべて**2026-07-27 に実害を出した型**である。
「規約に書いた」だけでは守られないことが同日に何度も証明されたので、機械で見る。

終了コード: 0=クリーン / 1=違反あり
"""
import sys, glob, os, re, math
import yaml
from yaml.constructor import SafeConstructor

STYLES = os.path.dirname(os.path.abspath(__file__))
FONT_PX_DEFAULT = 92  # chaenデフォルト。全角1字≒フォントサイズで概算する

# 【2026-07-27 R11】フォントのキーは二段・多段roleが増えるたびに増える。
# **3箇所に別々の一覧を書いていたため、running_outline に font_px_title を
# 足しても検査が「font_px が無い」と言い続けた**（同じ列挙を分散させた自分のミス）。
FONT_KEYS = ("font_px", "font_px_max", "font_px_en", "font_px_ja", "font_px_name",
             "font_px_attr", "font_px_sub", "font_px_logo", "font_px_title", "font_px_item")


def _font_of(layer):
    """そのroleで想定すべきフォントサイズ。

    【2026-07-27】当初これを92px決め打ちにしていたため、独自サイズを持つroleで
    偽陽性を出した（screen_tutorial の chapter_tab は font_px_max 45 で
    14字×45=630＝max_width_px 630 にちょうど収まるのに「6字しか入らない」と誤報した）。
    幅の検査は**最大フォントで見る**のが正しい（最大で入るなら小さい方でも入る）。

    【2026-07-27 R8追加・2つ目の穴】二段構成のroleは `font_px_en` / `font_px_ja` を
    持ち `font_px` を持たない。それを見ていなかったため**92pxの既定に落ちて**、
    narrated の bilingual_* に「max_chars 25 は死に値」という偽陽性を出した
    （実際は EN 56 / JA 48 で 25字はちょうど拘束する値）。
    複数フォントを持つroleも最大側で見る。
    """
    # 【R11】二段・多段roleのフォントキーは増えていく。1箇所にまとめる
    cands = [layer.get(k) for k in
             FONT_KEYS]
    cands = [v for v in cands if isinstance(v, int)]
    if cands:
        return max(cands)
    return FONT_PX_DEFAULT


class DupDetectLoader(yaml.SafeLoader):
    """PyYAMLは既定で重複キーを黙って後勝ちにするため、専用ローダーで検出する。"""


def _make_mapping_ctor(sink):
    def construct_mapping(loader, node, deep=False):
        seen = {}
        for key_node, _ in node.value:
            k = loader.construct_object(key_node, deep=deep)
            if k in seen:
                sink.append((loader.name, k, seen[k], key_node.start_mark.line + 1))
            seen[k] = key_node.start_mark.line + 1
        return SafeConstructor.construct_mapping(loader, node, deep)
    return construct_mapping


def check_duplicate_keys():
    """重複キー。先に書いた値が黙って消える。2026-07-27 に3度作った。"""
    found = []
    DupDetectLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _make_mapping_ctor(found))
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        with open(f, encoding="utf-8") as fh:
            yaml.load(fh, DupDetectLoader)
    return [f"{os.path.basename(n)[:-5]}: '{k}' L{a} が L{b} に上書きされている"
            for n, k, a, b in found]


def _layers(path):
    d = yaml.safe_load(open(path, encoding="utf-8"))
    return os.path.basename(path)[:-5], (d.get("telop_layers") or []), d


def check_exclusivity_symmetry():
    """排他は両側に書く。片側だけだと、もう一方だけ読んだ実装者に伝わらない。

    【2026-07-27 R8訂正・検査自身の穴】旧実装は同名roleの複数エントリを
    `decl.setdefault(role, set()).update(...)` で**1つにマージ**していた。
    そのため cinematic の sidebar のように、A2側のエントリに1行あるだけで
    **A3側のエントリの欠落が隠れていた**（片側だけ読む実装者には伝わらないという、
    この検査が防ぐはずだった事象そのもの）。
    エントリ単位で検査し、報告にも何番目のエントリかを出す。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        # エントリ単位（同名roleは別物として扱う）
        entries = []
        for i, l in enumerate(layers):
            # 【2026-07-27 R10】`intentional_overlap_with` も同じ理由で両側に要る。
            # ai_biz_pitch は highlight_marker → base_caption の片側だけで、
            # **base_caption だけを読んだ実装者は「意図的に重なる装置がある」と知らない**。
            # 字幕を先に焼いてからマーカーを足すと、マーカーが文字の上に乗る。
            e = []
            for k in ("mutually_exclusive_with", "intentional_overlap_with"):
                v = l.get(k) or []
                e += v if isinstance(v, list) else [v]
            entries.append((l["role"], set(e)))
        roles = {r for r, _ in entries}
        # 相手側は「同名roleのどのエントリか」を特定できないので、
        # **同名エントリすべてに書かれている**ことを要求する（安全側）
        for i, (a, targets) in enumerate(entries):
            nth = f"[{sum(1 for r, _ in entries[:i] if r == a) + 1}番目]" \
                if sum(1 for r, _ in entries if r == a) > 1 else ""
            for t in targets:
                if t == a:
                    continue  # 同一role名の変種どうしは自己参照でよい
                if t not in roles:
                    out.append(f"{name}: {a}{nth} -> {t}（存在しないrole）")
                    continue
                for j, (b, back) in enumerate(entries):
                    if b != t or a in back:
                        continue
                    bnth = f"[{sum(1 for r, _ in entries[:j] if r == t) + 1}番目]" \
                        if sum(1 for r, _ in entries if r == t) > 1 else ""
                    out.append(f"{name}: {a}{nth} -> {t} だが {t}{bnth} -> {a} が無い")
    return out


def check_max_lines_present():
    """幅や文字数を縛るなら行数も縛る。幅だけ縛ると行数が増えて悪化する。"""
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            has_w = l.get("max_width_px") not in (None, "非該当")
            if (has_w or l.get("max_chars") is not None) and l.get("max_lines") is None:
                out.append(f"{name}/{l['role']}: max_width_px/max_chars があるのに max_lines が無い")
    return out


def check_one_line_design():
    """【2026-07-27 R6・検査から外した】役目を終えてノイズになったため CHECKS/WARNINGS から除外。

    この検査は immersive / ai_tool_avatar / ai_biz_pitch の**3件の実在の欠陥を見つけた**が、
    そのあとは `max_width_px` から `max_chars` を逆算した正常な role
    （フル字幕系5件）だけをヒットし続けた。
    `max_chars` は「1行あたりの上限」なので `max_chars×font ≒ max_width_px` は
    **正常な関係**であり、これで1行設計かを判定することは原理的にできない。
    関数は記録として残すが呼ばない。

    ---- 以下、元の説明 ----
    max_chars×フォント ≒ max_width_px の role に max_lines≧2 が付いている。

    【2026-07-27 R4・これは違反でなく申し送りに降格した】
    当初これを「1行設計の証拠」として違反扱いにしたが、**思い違いだった**。
    `max_chars` は schema.md で「**1行あたり**の上限」と定義されているので、
    `max_chars × フォント ≒ max_width_px` は**正常な関係**であって
    1行設計の証拠ではない（各行が幅に収まるという当たり前の話）。

    実際、`max_width_px` から `max_chars` を逆算した role
    （startup.caption: 1500px → 16字）で偽陽性を出した。

    ただし immersive / ai_tool_avatar / ai_biz_pitch の3件はこの検査で見つかった実在の欠陥なので、
    **申し送りとして残す**。ヒットしたら「本当に2行を出す設計か」を人が判断すること。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            mc, mw, ml = l.get("max_chars"), l.get("max_width_px"), l.get("max_lines")
            font = _font_of(l)
            if not (isinstance(mc, int) and isinstance(mw, int) and isinstance(ml, int)):
                continue
            if ml >= 2 and mc * font <= mw * 1.05:
                out.append(
                    f"{name}/{l['role']}: {mc}字×{font}px={mc*font} ≦ max_width_px {mw}。"
                    f"max_lines={ml} は**本当に2行出す設計か**（1行で収まる幅なので要確認）")
    return out


def check_width_fits_chars():
    """max_chars×フォント > max_width_px なら、その文字数は物理的に1行に入らない。

    【2026-07-27 追加・lintの穴】私が ai_tool_avatar に入れた
    `variant_geometry.mode_A.max_width_px: 1040` は `max_chars 12`×92=1104px より狭く、
    12字が2行に折り返されて `max_lines: 1` に違反した。
    **ネストしたフィールドを見ていなかったため lint はクリーンと判定した。**
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            mc = l.get("max_chars")
            # 【2026-07-27 R8追加】欧文キャップスなど**1字がフォントサイズより狭い**
            # roleは `char_width_px`（実測の1字あたり送り幅）を宣言できる。
            # 宣言が無ければ従来どおり「全角1字＝font_px」の最悪ケースで見る。
            # investigative の evidence_heading は実測 777px/17字 = 45.7px/字で、
            # 全角前提だと 17×75=1275 となり**正しい値が「死に値」と誤報された**。
            font = l.get("char_width_px") if isinstance(l.get("char_width_px"), int) \
                else _font_of(l)
            ml = l.get("max_lines")
            if not isinstance(mc, int):
                continue
            need = mc * font
            # 直下の max_width_px と、variant_geometry 配下の各バリアントを両方見る
            # variant_geometry があれば、そのバリアント固有の max_chars / max_width_px を優先する
            vg = l.get("variant_geometry")
            # variant_geometry があるroleは、role直下の値は「既定」であって
            # 実際に使われるのはバリアント側。上位だけを見ると誤報になる
            targets = [] if isinstance(vg, dict) else [("", l.get("max_width_px"), mc)]
            if isinstance(vg, dict):
                for vname, vconf in vg.items():
                    if isinstance(vconf, dict):
                        targets.append((f"[{vname}]", vconf.get("max_width_px"),
                                        vconf.get("max_chars", mc)))
            for label, mw, chars in targets:
                if not isinstance(mw, int) or not isinstance(chars, int):
                    continue
                if not isinstance(ml, int):
                    continue
                if chars * font > mw:
                    fits = mw // font
                    if ml == 1:
                        out.append(
                            f"{name}/{l['role']}{label}: max_chars {chars}×{font}px={chars*font} > "
                            f"max_width_px {mw} なのに max_lines=1（{fits}字しか入らない）")
                    else:
                        # 【2026-07-27 R5・lintの穴】max_lines≧2 でも、1行に入る字数を
                        # max_chars が上回っていれば**その値は一度も拘束しない死に値**になる。
                        # tutorial の lower_third は max_chars 20 / font 92 / 幅1500 で
                        # 1行上限16字＝**幅を足して文字数を直していなかった**。
                        out.append(
                            f"{name}/{l['role']}{label}: max_chars {chars} は1行に入る"
                            f"{fits}字を超えており**一度も拘束しない死に値**"
                            f"（{chars}×{font}px={chars*font} > max_width_px {mw}）")
    return out


def check_unmeasured_width_with_chars():
    """max_chars があるのに max_width_px が「未計測」なら、1行設計の判定ができない。

    【2026-07-27 追加・lintの穴】文字列「未計測」を数値チェックが素通りするため、
    ai_biz_pitch の base_caption / shock_keyword / positive_keyword は3roleとも検査対象外だった。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            mc, mw = l.get("max_chars"), l.get("max_width_px")
            # 「非該当」は正当な例外。wipe の keyword は1-2文字の巨大見出しで
            # そもそも折返しの概念が無く、幅で縛る対象ではない
            if mw == "非該当":
                continue
            if isinstance(mc, int) and not isinstance(mw, int):
                out.append(
                    f"{name}/{l['role']}: max_chars {mc} があるのに max_width_px が "
                    f"{mw!r}＝1行設計かどうかを機械判定できない")
    return out


def check_same_geometry_needs_exclusivity():
    """同じ position かつ同じ size の role ペアに排他が無い。

    【2026-07-27 R4追加・私が作った実害】`quote_card` に size 1536x864 を書いたところ、
    同じ (0.5,0.5)・同じ 1536x864 の `archival_insert_card` と **交差100.0%**になった。
    しかも z_order_policy が「テキストカードが資料映像の上に重なる構成」と書いているため、
    **yamlを素直に読んだ実装がこの重ね方を選ぶ**。
    寸法が無かったR3時点では別サイズもあり得たので、
    **寸法を固定したことで100%衝突が確定した**。

    「size を書け」という規約は、**同座標roleとの総当たり検算を伴わないと
    新しい100%衝突を作る**。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        decl = {}
        for l in layers:
            e = l.get("mutually_exclusive_with")
            if e:
                decl.setdefault(l["role"], set()).update(e if isinstance(e, list) else [e])
        for i, a in enumerate(layers):
            for b in layers[i + 1:]:
                if a["role"] == b["role"]:
                    continue  # 同一role名の変種は別検査でみる
                if not (a.get("size") and b.get("size")):
                    continue
                if a["size"] != b["size"] or a.get("position") != b.get("position"):
                    continue
                if b["role"] in decl.get(a["role"], set()):
                    continue
                # 変種が違えば同時表示されない
                va, vb = a.get("variant"), b.get("variant")
                if va and vb and va != vb:
                    continue
                out.append(
                    f"{name}: {a['role']} と {b['role']} が**同じ position・同じ size**"
                    f"（{a['size']}）なのに排他が無い＝交差100%になる")
    return out


def check_size_covers_width():
    """size.width が max_width_px + フチ を収容できているか。

    【2026-07-27 R5追加】ai_biz_pitch の `shock_keyword` は size.width 1809 が、
    同ファイルが positive_keyword で使った関係式 `1792+24=1816` を **7px下回る**。
    箱が中身より小さいと、実装者は中身を潰すか箱をはみ出させるしかない。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            size, mw = l.get("size"), l.get("max_width_px")
            if not (isinstance(size, dict) and isinstance(mw, int)):
                continue
            w = size.get("width")
            if not isinstance(w, int):
                continue
            pad = l.get("padding_px") or 0
            # 【2026-07-27 R6・lint自身の実装バグ】フチ（袋文字のストローク）を
            # 数えていなかったため、ai_biz_pitch shock の旧値1809（正しくは1792+24=1816）を
            # **この検査のために入れたのに検出できなかった**。
            # stroke_px があればそれ、無ければ chaen デフォルトの12pxを両側に見る。
            stroke = l.get("stroke_px")
            if not isinstance(stroke, int):
                stroke = 12 if "袋文字" in str(l.get("style", "")) else 0
            need = mw + pad * 2 + stroke * 2
            if w < need:
                out.append(
                    f"{name}/{l['role']}: size.width {w} < max_width_px {mw} "
                    f"+ padding{pad}×2 + フチ{stroke}×2 = {need}（箱が中身より小さい）")
    return out


def check_prose_exclusivity_has_field():
    """コメントに「同時に出さない／排他」と書いてあるのにフィールドが無い role。

    【2026-07-27 R5追加】cinematic は `map × insert` の散文が
    「時間排他運用とする」と書いているのに**両側ともフィールドが無い**まま、
    しかも**今回フィールド化したペアと同じ段落の中**に残っていた。
    総当たり6ペアのうち1ペアだけが塞がれた状態だった。
    """
    KEYS = ("同時に出さない", "同時表示しない", "時間排他", "排他的", "同時表示されない",
            "同時に出してはいけない")
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        raw = open(f, encoding="utf-8").read()
        name, layers, _ = _layers(f)
        for l in layers:
            if l.get("mutually_exclusive_with"):
                continue
            # variant を持つ role は変種スコープで分離済み。
            # 散文の「同時表示されない」は変種の説明であって排他宣言ではない
            if l.get("variant"):
                continue
            # opaque_fullscreen は「表示中は他を覆う」ことが定義そのもの。
            # 散文の「同時表示しない」は宣言の言い換えであって欠落ではない
            if l.get("opaque_fullscreen"):
                continue
            # 単一スロットで状態を切り替える role（role内の状態切替であって
            # 他roleとの排他ではない）
            if l.get("slot_states"):
                continue
            role = l["role"]
            blocks = re.findall(
                rf'^  - role: {re.escape(role)}\b.*?(?=^  - role:|^\w)', raw, re.M | re.S)
            for b in blocks:
                # 「撤回した」「旧版」を含む行は過去の記述への言及なので除外する
                # （screen_tutorial の click_highlight_* は
                #  「旧版の『排他的分岐』という断定は撤回した」にヒットしていた）
                live = "\n".join(
                    ln for ln in b.split("\n")
                    if not any(w in ln for w in ("撤回", "旧版", "旧記述", "誤りだった")))
                hit = [k for k in KEYS if k in live]
                if hit:
                    out.append(
                        f"{name}/{role}: コメントに {hit} と書いてあるのに "
                        f"mutually_exclusive_with が無い")
                    break
    return out


def check_comment_bracket_balance():
    """コメント行の括弧の対応。一括置換の跡を機械で捕まえる。

    【2026-07-27 R6追加】narrated の L382-384 が一括置換で文として成立せず、
    **閉じ括弧が1つ余っていた**（開き10 / 閉じ11）。
    人が読んで気づくのは難しいが、機械なら確実に出る。
    """
    PAIRS = [("（", "）"), ("(", ")"), ("「", "」"), ("**", None)]
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name = os.path.basename(f)[:-5]
        lines = open(f, encoding="utf-8").read().split("\n")
        # role ブロック単位で数える（ファイル全体だと粒度が粗すぎる）
        blocks, cur, cur_start = [], [], 0
        for i, ln in enumerate(lines):
            if re.match(r'^  - role: ', ln):
                if cur:
                    blocks.append((cur_start, cur))
                cur, cur_start = [], i + 1
            cur.append(ln)
        if cur:
            blocks.append((cur_start, cur))
        for start, blk in blocks:
            role_m = re.match(r'^  - role: ([a-z_]+)', blk[0]) if blk else None
            role = role_m.group(1) if role_m else "(先頭)"
            # 【2026-07-27 R8訂正】旧: `if "#" in l` で絞っていた。この1行が
            # (a) ブロックスカラーの散文を丸ごと落とし、**この検査が捕まえるために
            #     作られた narrated L382-385 の実欠陥を見逃していた**
            #     （#を含む行だけなら10/10で釣り合い、ブロック全行なら83/84で不一致）
            # (b) HEXコードの # を「コメント」と誤認して ai_tool_avatar に偽陽性を2件出していた
            # ブロック全行を数えれば両方消える。絞り込み自体が誤りだった
            text = "\n".join(blk)
            # 【2026-07-27 R8】記法と引用は括弧の対応を崩すが欠陥ではない。
            # 数えるのは「散文としての括弧」だけにする。
            #  - 半開区間 [t0,t1) / (a,b] — 6件の申し送りのうち2件がこれだった
            #  - バッククォートで囲んだコード片（壊れた記述を**わざと**引用している）
            #  - 「」そのものを字として説明している箇所
            # 区間記法のみ（数字・小数点・カンマ・空白だけの中身。改行を跨がない）。
            # 【R8】当初はどんな括弧対も剥がしていたため、**改行を跨いで無関係な
            #  開き括弧まで巻き込み**、startup/pip に「(×0 / )×2」という幻の
            #  不均衡を作っていた。剥がす対象は狭く定義する
            text = re.sub(r'[\[(][A-Za-z0-9_.,\s×+\-]*[\])]', "", text)
            text = re.sub(r'`[^`]*`', "", text)                  # コード片
            text = re.sub(r'「[「」]」?', "", text)                 # 括弧自体への言及
            for o, c in PAIRS:
                if c is None:
                    continue
                if text.count(o) != text.count(c):
                    out.append(
                        f"{name}/{role} (L{start}〜): コメント内の {o}{c} が非対応"
                        f"（{o}×{text.count(o)} / {c}×{text.count(c)}）")
    return out


def check_comment_centery_matches_field():
    """コメントが書いている centerY と、実際のフィールド値の食い違い。

    【2026-07-27 R6追加】narrated の z_order_policy と apply_notes が
    person_nameplate を「0.67」と書き続けていた（フィールドは0.64）。
    **5周にわたり同種の未追随が出続けている**ので機械で見る。
    role ブロック内のコメントに `0.XX` 形式が出てきたら、
    そのroleの centerY と一致するか、明示的に「旧」扱いされているかを見る。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        raw = open(f, encoding="utf-8").read()
        for l in layers:
            cy = (l.get("position") or {}).get("centerY")
            if not isinstance(cy, float):
                continue
            role = l["role"]
            others = {x["role"] for x in layers} - {role}
            # 【2026-07-27 R6・自分のバグ】同一role名が2エントリあるファイル
            # （immersive の sidebar、wipe の nameplate）で**別ブロックの position 行**を
            # 自分の値と比べて偽陽性を出していた。同じroleの何番目かを合わせる
            same = [x for x in layers if x["role"] == role]
            occ = same.index(l)
            blocks = re.findall(
                rf'^  - role: {re.escape(role)}\b.*?(?=^  - role:|^\w)', raw, re.M | re.S)
            blocks = blocks[occ:occ + 1] if occ < len(blocks) else []
            HIST = ("旧", "撤回", "誤り", "以前", "R3", "R4", "R5", "R6", "R8",
                    "だった", "時代", "実効", "安全側", "→", "経緯", "当初")
            for b in blocks:
                # 【2026-07-27 R8訂正】履歴語を**その1行だけ**で判定していたため、
                # 「**旧記述**: centerY 0.5 …」の続きの行（履歴語を持たない）が
                # 偽陽性になっていた。履歴は連続するコメント塊の単位で決まる。
                lines = b.split("\n")
                hist_run = False
                for ln in lines:
                    is_comment = "#" in ln and not re.match(r'^\s*[a-z_]+:', ln)
                    if not is_comment and not re.match(r'^\s*#', ln):
                        hist_run = False  # フィールド行で塊が切れる
                    if any(w in ln for w in HIST):
                        hist_run = True
                    if "#" not in ln:
                        continue
                    if re.match(r'^\s*position:', ln):
                        continue
                    if hist_run:
                        continue
                    # 他roleの centerY に言及している行は自分の値と違って当然
                    if any(o in ln for o in others):
                        continue
                    m = re.search(r'centerY\s*[:：]?\s*(0\.\d+)', ln)
                    if m and abs(float(m.group(1)) - cy) > 1e-9:
                        out.append(
                            f"{name}/{role}: コメントが centerY {m.group(1)} と書いているが "
                            f"フィールドは {cy}")
                        break
    return out


def check_overflow_policy_with_single_line():
    """max_lines: 1 なのに overflow_policy が無い。

    【2026-07-27 R6追加・退行の直接原因】
    ai_tool_avatar で「`max_lines: 1` + `max_chars` でも折返しは止まらない」と実測したのに、
    **その規約を既存ファイルへ掃引していなかった**（R5で自分が書いた
    「規約を追加したらその場で既存を掃引する」の違反）。
    結果、documentary_immersive の B3 caption が2行に折り返し、
    person_nameplate と pixel 0.989 で衝突して**「可」から退行した**。

    折返し器は max_chars で改行するので、結果の各行は max_chars 以下になり
    **制約を満たしたまま2行になる**。止めるには
    `overflow_policy: split_event`（折り返さずイベント分割）が要る。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            if l.get("max_lines") == 1 and not l.get("overflow_policy"):
                out.append(
                    f"{name}/{l['role']}: max_lines:1 なのに overflow_policy が無い"
                    f"（折返し器は max_chars で改行するので**制約を満たしたまま2行になる**）")
    return out


def check_opaque_fullscreen_has_size():
    """宣言だけでは1ピクセルも覆えない。実測で被覆率14.4%〜31.4%だった。"""
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            if l.get("opaque_fullscreen") and not l.get("size"):
                out.append(f"{name}/{l['role']}: opaque_fullscreen なのに size が無い")
    return out


def check_yaml_parses():
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        try:
            yaml.safe_load(open(f, encoding="utf-8"))
        except Exception as e:
            out.append(f"{os.path.basename(f)}: {str(e)[:120]}")
    return out


# 違反（終了コード1にする）

def _rect(l, variant=None):
    """名目矩形。size が無くても max_width_px と行数から推定する。

    【2026-07-27 R9追加】`variant` を渡すと `variant_geometry[variant]` を優先する。
    ai_tool_avatar の `caption` は mode_A で centerX 0.34 / 幅1040、mode_B で 0.5 / 1104 と
    **モードごとに別の幾何**を持つ。role直下の値（mode_B相当）だけで比べると、
    mode_A 専用の `nameplate`（ネコキャラ大）と交差しているように見えるが、
    mode_A の幾何では 92px 離れていて交差しない。

    【2026-07-27 R9訂正・検査の穴】旧実装は `size` を持つrole同士しか見なかった。
    R9で実際にレンダリングを FAIL させた3ペアは**すべて相手が size を持たない**
    （ai_biz_pitch の `base_caption`、cinematic の `caption`）ため、構造的に見えていなかった。
    幅と行数が分かれば矩形は推定できるので、推定してでも見る。
    """
    po = dict(l.get("position") or {})
    vg = l.get("variant_geometry")
    ov = vg.get(variant) if (variant and isinstance(vg, dict)) else None
    if isinstance(ov, dict):
        for k in ("centerX", "centerY"):
            if k in ov:
                po[k] = ov[k]
        l = {**l, **{k: v for k, v in ov.items() if k not in ("centerX", "centerY")}}
    if not po:
        return None
    try:
        cx, cy = float(po["centerX"]) * 1920, float(po["centerY"]) * 1080
    except (KeyError, TypeError, ValueError):
        return None
    sz = l.get("size")
    if isinstance(sz, dict):
        try:
            w, h = float(sz["width"]), float(sz["height"])
        except (KeyError, TypeError, ValueError):
            return None
    else:
        mw = l.get("max_width_px")
        if not isinstance(mw, (int, float)):
            return None
        ml = l.get("max_lines") if isinstance(l.get("max_lines"), int) else 1
        font = _font_of(l)
        pad = l.get("padding_px") if isinstance(l.get("padding_px"), int) else 0
        # 【2026-08-01 R12】既定を0にしていたのは非対称だった。font_px の既定は 92 を
        # 当てているのに、対になるフチを0にすると箱が実物より痩せ、境界すれすれの
        # role が余裕ありに見える。schema と各yamlの caption_font は
        # 「フォント92／黒フチ12px」を**1組で**規定しているので、比で既定を置く。
        st = l.get("stroke_px") if isinstance(l.get("stroke_px"), int) \
             else max(1, math.ceil(font * 12 / 92))
        w = float(mw) + (pad + st) * 2
        h = font * 1.46 * ml + (pad + st) * 2   # 行高 ≒ font×1.46（実測）
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _shadow(l, r):
    """矩形に影を足す。**この1本だけを全検査で使う。**

    2026-08-01 R12: 影を足す処理が check_clearance_between_roles の内側に
    閉じ込められており、交差検査は素の矩形で見ていた。同じ「重なり」を
    検査ごとに違う矩形で見た結果、影込みなら5%を超える交差を4%として捨てていた。
    """
    if str(l.get("color_shadow", "")).strip('"') == "none":
        return r
    return (r[0] - SHADOW_LT, r[1] - SHADOW_LT, r[2] + SHADOW_RB, r[3] + SHADOW_RB)


def check_sized_rects_intersect():
    """size を持つrole同士の名目矩形が交差するのに、排他も意図宣言も無い組。

    【2026-07-27 R8追加】ai_biz_pitch の `shock_keyword` × `reaction_character` を
    **既存のどの検査も捕まえられなかった**。
    `check_same_geometry_needs_exclusivity` は「同じposition かつ 同じsize」しか見ないが、
    この組は position が違う（0.5,0.66 vs 0.12,0.70）まま 390x132px 交差していた。
    しかも双子の positive_keyword 側にだけ排他が入っており、**yaml上は合法な配置**で
    先頭3字が消えた。矩形が重なるなら position が違っても危険である。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for i, a in enumerate(layers):
            if a.get("opaque_fullscreen"):
                continue
            for b in layers[i + 1:]:
                # 片方が変種スコープを持つなら、相手の variant_geometry も
                # そのスコープで評価する（モードごとに幾何が違うため）
                va0, vb0 = a.get("variant"), b.get("variant")
                ra = _rect(a, vb0 if isinstance(vb0, str) else None)
                rb = _rect(b, va0 if isinstance(va0, str) else None)
                if not ra:
                    continue          # 【2026-08-01 R12】break だと相手の変種次第で残りを取りこぼす
                if not rb or b.get("opaque_fullscreen") or a["role"] == b["role"]:
                    continue
                va, vb = va0, vb0
                if va and vb and va != vb:
                    continue  # 別変種は同時表示されない
                if _linked(a, b, "mutually_exclusive_with") or \
                   _linked(a, b, "intentional_overlap_with"):
                    continue
                # 【2026-08-01 R12】素の矩形で見ていたので、影込みなら5%を超える
                #  交差を「4%」として捨てていた（japan_vlog 4%→12%）。
                #  同じ「重なり」を検査ごとに違う矩形で見ない。
                sa, sb = _shadow(a, ra), _shadow(b, rb)
                ox = min(sa[2], sb[2]) - max(sa[0], sb[0])
                oy = min(sa[3], sb[3]) - max(sa[1], sb[1])
                if ox <= 0 or oy <= 0:
                    continue
                smaller = min((ra[2] - ra[0]) * (ra[3] - ra[1]),
                              (rb[2] - rb[0]) * (rb[3] - rb[1]))
                ratio = ox * oy / smaller
                if ratio >= 0.05:
                    out.append(f"  {name}: {a['role']} x {b['role']} が "
                               f"{ox:.0f}x{oy:.0f}px（小さい側の{ratio:.0%}）交差。"
                               f"排他も intentional_overlap_with も無い")
    return out


def _linked(a, b, key):
    for x, y in ((a, b), (b, a)):
        v = x.get(key) or []
        v = v if isinstance(v, list) else [v]
        if y["role"] in v:
            return True
    return False



# NotoSansJP の実測行高（ascent+descent）。2026-07-27 R10 に実測。
LINE_HEIGHT = {23: 34, 30: 44, 34: 50, 36: 53, 44: 65, 45: 66, 48: 70, 50: 73,
               52: 76, 56: 82, 60: 88, 64: 94, 70: 103, 72: 106, 75: 110,
               92: 134, 112: 164, 150: 218, 280: 409}


def _line_height(font):
    if font in LINE_HEIGHT:
        return LINE_HEIGHT[font]
    return round(font * 1.465)   # 実測比の中央値


def check_size_height_fits_lines():
    """`size.height` が「行高×max_lines + padding×2 + フチ×2」を収容できているか。

    【2026-07-27 R10追加】幅の検査（`check_size_covers_width`）はR4からあるのに、
    **高さには一度も検査が無かった**。R10の静的検算で4件出た:
    startup の nameplate（70 vs 必要77）と cta_banner（250 vs 254）、
    tutorial の caption（190 vs 202）、startup の caption（幅が式と24px不一致）。
    箱が中身より低いと、実装者はフォントを落とすか、はみ出させるかを勝手に選ぶ。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            sz = l.get("size")
            if not isinstance(sz, dict) or l.get("layer_kind") == "video":
                continue
            fonts = [l.get(k) for k in
                     FONT_KEYS]
            fonts = [v for v in fonts if isinstance(v, int)]
            if not fonts:
                continue
            pad = l.get("padding_px") if isinstance(l.get("padding_px"), int) else 0
            st = l.get("stroke_px") if isinstance(l.get("stroke_px"), int) else 0
            # 2段構成（name/attr など複数フォント）は各段1行として足す
            if len(fonts) > 1:
                need = sum(_line_height(v) for v in fonts)
            else:
                ml = l.get("max_lines") if isinstance(l.get("max_lines"), int) else 1
                need = _line_height(fonts[0]) * ml
            need += (pad + st) * 2
            try:
                h = float(sz["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if h < need:
                out.append(f"  {name}/{l['role']}: size.height {h:.0f} < "
                           f"行高{need - (pad + st) * 2:.0f} + padding{pad}×2 + フチ{st}×2 "
                           f"= {need:.0f}（箱が中身より低い）")
    return out


CHECKS = [
    ("YAMLとして読めるか", check_yaml_parses),
    ("重複キー（先に書いた値が黙って消える）", check_duplicate_keys),
    ("排他の非対称（片側だけの宣言）", check_exclusivity_symmetry),
    ("max_lines の欠落（幅だけ縛ると行数で悪化する）", check_max_lines_present),
    ("max_chars が max_width_px に入らない（variant_geometry含む）", check_width_fits_chars),
    ("同じposition・同じsizeなのに排他が無い（交差100%）", check_same_geometry_needs_exclusivity),
    ("size付きroleの矩形が交差するのに排他も意図宣言も無い", check_sized_rects_intersect),
    ("size.width が max_width_px+padding を収容できていない", check_size_covers_width),
    ("size.height が 行高×max_lines+padding+フチ を収容できていない",
     check_size_height_fits_lines),
    ("max_lines:1 なのに overflow_policy が無い", check_overflow_policy_with_single_line),
    ("opaque_fullscreen に size が無い", check_opaque_fullscreen_has_size),
]

# 申し送り（終了コードには影響させない。**放置すると幅の検査が効かない**ので減らすこと）

def check_shell_substitution_damage():
    """コマンド置換で消えた記述の跡を捕まえる。

    【2026-07-27 R8追加】**私が一括編集を2度、クォートしていないヒアドキュメントで
    実行し、コメント中の `...` がシェルにコマンドとして食われて消えた**。
    R6の一括掃引で7ファイル・R8で1ファイル。commit されて数周気づかれなかった。
    消えたのは「`max_lines: 1` だけでは折返しは止まらない」の前半など、
    **その注記の主語そのもの**で、読むと意味の通らない文が残る。

    署名は「日本語の直後に2連続空白があり、その先も日本語」。
    列揃えの行末コメントとHEXコードは除外する。
    """
    pat = re.compile(r'[ぁ-んァ-ヶ一-龥」）】]  +[ぁ-んァ-ヶ一-龥「（【]')
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name = os.path.basename(f)[:-5]
        for i, ln in enumerate(open(f, encoding="utf-8").read().split("\n"), 1):
            m = re.search(r'(?:^\s*#|\s#)\s?(.*)$', ln)
            if m and pat.search(m.group(1)):
                out.append(f"  {name}:{i} 記述が消えた跡: {m.group(1).strip()[:60]}")
    return out



def check_size_without_font():
    """`size` があるのに中身（フォント・フチ幅）が無いrole。

    【2026-07-27 R8追加】規約③「size を書くときは、その箱に何を入れるつもりかを
    font_px で示す」を機械照合していなかった。cinematic は size を持つ4roleすべてが
    font_px 未指定で、実装者が別のフォントで組めば箱の意味が消える。
    袋文字（`color_stroke` を持つrole）は `stroke_px` も「中身」である
    （ai_biz_pitch の cta_urge_text はフチ幅が無いために font_px と size.height が両立せず、
    実装者が発明した10pxで画面下端を割った）。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, d = _layers(f)
        for l in layers:
            if not isinstance(l.get("size"), dict):
                continue
            if l.get("layer_kind") == "video":
                continue  # 動画レイヤーに文字は無い
            role = l["role"]
            has_font = any(isinstance(l.get(k), int) for k in
                           FONT_KEYS)
            # 文字を持たない図形・カード類は max_width_px / max_chars が無いことで判別する
            has_text = any(l.get(k) is not None for k in
                           ("max_width_px", "max_chars", "max_lines"))
            if not has_text and not has_font:
                # 【2026-07-27 R8・自分の絞り込みの穴】当初 has_text を前提条件に
                # していたため、**幅も文字数もフォントも一つも無いrole**（cinematic の
                # 全画面カード4つ）が丸ごと落ちていた。schema.md に書いたばかりの
                # 「絞り込みが検査の目的を殺す」を自分でやった。
                # 文字を持たない図形なら `text_content: none` を宣言して除外する。
                if l.get("text_content") != "none":
                    out.append(f"  {name}/{role}: size だけあって中身の指定が"
                               f"一つも無い（font_px も max_width_px も max_chars も無い）"
                               f"。文字を持たない図形なら text_content: none を書く")
            elif has_text and not has_font:
                out.append(f"  {name}/{role}: size があるのに font_px が無い"
                           f"（箱だけ決めても別のフォントで組まれる）")
            elif has_text and has_font and l.get("stroke_px") is None:
                cs = (d.get("color_roles") or {}).get("color_stroke") \
                    if isinstance(d.get("color_roles"), dict) else None
                if cs and cs not in ("none", "null", None):
                    out.append(f"  {name}/{role}: 袋文字（color_stroke あり）なのに "
                               f"stroke_px が無い（フチ1pxで外形が縦2px変わる）")
    return out



def check_transition_seconds():
    """`fade` と書きながら秒数が無いrole、`exit` そのものが無いrole。

    【2026-07-27 R9追加】`entrance: fade` だけでは実装できず、実装者が
    秒数を発明する。全15ファイルで **fade 41件が秒数なし・exit 15件が欠落**していた。
    hard_cut / pop は定義上0秒なので対象外にする（書いても情報が増えない）。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            for k in ("entrance", "exit"):
                if l.get(k) == "fade" and l.get(k + "_sec") is None:
                    out.append(f"  {name}/{l['role']}: {k}: fade なのに "
                               f"{k}_sec が無い（実装者が秒数を発明する）")
            if l.get("entrance") and not l.get("exit"):
                out.append(f"  {name}/{l['role']}: entrance はあるが exit が無い")
    return out



def check_overflow_policy_with_max_chars():
    """`max_chars` があるのに `overflow_policy` が無い。

    【2026-07-27 R9追加】旧検査は `max_lines: 1` のときしか見ていなかった。
    R9の高深刻度4件のうち2件が「**max_chars を足しても行数は止まらない**」型で、
    investigative の caption は 62字が4行・各行17字以下・**違反ゼロ**のまま
    下へ95px見切れ、narrated の quote_caption は 52字が3行で26px見切れた。
    歯止めは max_chars ではなく overflow_policy にしかない。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            if isinstance(l.get("max_chars"), int) and not l.get("overflow_policy"):
                out.append(f"  {name}/{l['role']}: max_chars {l['max_chars']} があるのに "
                           f"overflow_policy が無い（**max_chars は行数を止めない**）")
    return out


EN_HINT = ("英語", "EN", "欧文", "キャップス", "アルファベット", "半角", "英字", "バイリンガル")


def check_english_needs_char_width():
    """欧文を扱うroleに `char_width_px` が無い。

    【2026-07-27 R9追加】幅の検算は既定で「全角1字＝font_px」を使うため、
    欧文roleでは max_chars が**実力の半分**に縛られる。
    narrated の bilingual_nameplate は max_chars 25 が同roleの例示EN（42字）と
    両立せず、守ると3行化して size.height を12px超過した。
    真因は `char_width_px` の宣言漏れで、規約自体は R8 時点で存在していたのに
    掃引が investigative の1箇所で止まっていた。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        raw = open(f, encoding="utf-8").read()
        for l in layers:
            if not isinstance(l.get("max_chars"), int) or l.get("char_width_px"):
                continue
            m = re.search(rf'^  - role: {re.escape(l["role"])}\b.*?(?=^  - role:|^\w)',
                          raw, re.M | re.S)
            blk = m.group(0) if m else ""
            if any(w in blk for w in EN_HINT):
                out.append(f"  {name}/{l['role']}: 散文が欧文に言及しているのに "
                           f"char_width_px が無い（max_chars が実力の半分に縛られる）")
    return out



def check_cross_role_centery_references():
    """あるroleのコメントが**別のrole**の centerY を引用していて、実際の値と違う。

    【2026-07-27 R9追加】ai_biz_pitch で `base_caption` を動かしたとき、
    それを分母にして導出されていた4roleの注記が旧値のまま残り、
    **実測で −41px / −37px / +2px / 43.2pxずれ**の衝突になった。
    「この値は ○○ から導出した」と書く規約（schema.md R9追記）を機械で見張る。
    履歴語（旧・撤回・R3〜）を含む塊は対象外。
    """
    HIST = ("旧", "撤回", "誤り", "以前", "R3", "R4", "R5", "R6", "R8", "R9",
            "だった", "時代", "→", "経緯", "当初", "削除")
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        cy = {}
        for l in layers:
            v = (l.get("position") or {}).get("centerY")
            if isinstance(v, float):
                cy.setdefault(l["role"], set()).add(v)
        raw = open(f, encoding="utf-8").read()
        for i, ln in enumerate(raw.split("\n"), 1):
            if "#" not in ln or any(w in ln for w in HIST):
                continue
            for role, vals in cy.items():
                # 「<role名> … 0.XX」形式（間に20文字まで）
                # 【R9】当初は「role名の20文字以内に 0.XX」で拾ったが、centerX や
                # pixel_collision の比率まで巻き込んで36件中の大半が偽陽性だった。
                # **centerY / cY という語を間に要求する**
                for m in re.finditer(
                        rf'(?<![A-Za-z0-9_]){re.escape(role)}(?![A-Za-z0-9_])'
                        rf'[^\n]{{0,24}}?(?:centerY|cY)\s*[:：]?\s*(0\.\d+)',
                        ln):
                    q = float(m.group(1))
                    if q not in vals and all(abs(q - v) > 1e-9 for v in vals):
                        out.append(f"  {name}:L{i} が {role} を "
                                   f"centerY {q} と引用しているが実際は "
                                   f"{sorted(vals)}")
    return out



SHADOW_RB, SHADOW_LT = 12, 3   # ドロップシャドウの実測広がり（右下+12 / 左上−3）


def check_four_edges():
    """4辺検算。宣言された `size` と `position` が画面内に16px以上の余裕で収まるか。

    【2026-07-27 R10追加】schema.md はR3から4辺検算を必須と定めているのに、
    **機械照合が無かった**。手で検算していたので毎周どこかで抜け、R9までに
    「上端7px」「上端 −15.5px」「下端ちょうど1080px」を実際に出している。
    `color_shadow: "none"` のroleは影の項を足さない。
    画面端にフチ合わせで置く装置は `edge_bleed: true` で明示的に除外する。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            # 【2026-08-01 R12・重大な穴の修正】旧実装は `size` を宣言した role しか
            # 見ておらず、`max_width_px` だけの role を**1周も検査していなかった**。
            # 11周にわたる「4辺検算NG 0件」はこの見逃しの上に乗っていた。
            # R9 の `_rect()` は既に「推定してでも見る」を実装済みで、すきま検算と
            # 矩形交差検算はそれを使っている。**この検算だけが取り残されていた。**
            if not isinstance(l.get("position"), dict):
                continue
            if l.get("opaque_fullscreen") or l.get("edge_bleed"):
                continue
            r = _rect(l)
            if not r:
                continue
            x0, y0, x1, y1 = r
            est = "" if isinstance(l.get("size"), dict) else "（推定矩形）"
            no_shadow = str(l.get("color_shadow", "")).strip('"') == "none"
            rb, lt = (0, 0) if no_shadow else (SHADOW_RB, SHADOW_LT)
            m = {"左": x0 - lt, "右": 1920 - (x1 + rb),
                 "上": y0 - lt, "下": 1080 - (y1 + rb)}
            bad = {k: round(v, 1) for k, v in m.items() if v < 16}
            if bad:
                out.append(f"  {name}/{l['role']}: 余裕16px未満 {bad}{est}"
                           f"（フチ合わせが設計なら edge_bleed: true を書く）")
    return out



def check_max_chars_total():
    """`max_lines >= 2` のroleに `max_chars_total`（イベント全体の総字数上限）があるか。

    【2026-07-27 R10追加】**`max_chars × max_lines` は総量の上限にならない。**
    investigative の evidence_heading は名目 17×2 = 34字だが実容量は 29字で、
    **yaml自身の例示文言33字が3行になり、3行目と赤下線が黒座布団の外へ93px出た**
    （QCは exit 0 で素通り。目視でしか見つからなかった）。
    理由は2つあり、どちらも `max_chars` の掛け算では表せない:
      - 欧文は単語の切れ目でしか折り返せない（平均送りは容量を保証しない）
      - `max_width_px / char_width_px` の端数を切り捨てると行ごとに損をする
    `overflow_policy` を発火させるのはこの総量である。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            ml = l.get("max_lines")
            if not (isinstance(ml, int) and ml >= 2):
                continue
            mc = l.get("max_chars")
            if not isinstance(mc, int):
                continue
            # 【R10】名目と実容量が割れる条件は2つだけ。和文ベタ（どの文字でも折り返せて
            # 端数も出ない）なら max_chars×max_lines がそのまま総量になるので、
            # 空欄を強制しても情報が増えない。
            cw = l.get("char_width_px")
            font = _font_of(l)
            mw = l.get("max_width_px")
            is_latin = isinstance(cw, int) and cw < font        # 欧文（単語の切れ目でしか折れない）
            has_frac = (isinstance(mw, (int, float)) and isinstance(cw, int)
                        and cw and float(mw) % cw != 0)          # 端数の切り捨て
            if not (is_latin or has_frac):
                continue
            tot = l.get("max_chars_total")
            if not isinstance(tot, int):
                why = "欧文は単語の切れ目でしか折り返せない" if is_latin \
                    else f"max_width_px {mw} ÷ char_width_px {cw} に端数が出る"
                out.append(f"  {name}/{l['role']}: max_lines {ml} なのに "
                           f"max_chars_total が無い（{why}）")
            elif tot > mc * ml:
                out.append(f"  {name}/{l['role']}: max_chars_total {tot} が "
                           f"max_chars {mc}×{ml} = {mc * ml} を超えている")
    return out



def check_clearance_between_roles():
    """同時表示しうるrole間の**すきま**が16px未満（ただし交差はしていない）組。

    【2026-07-27 R10追加・最後まで残っていた穴】
    `check_sized_rects_intersect` は交差しか見ず、`check_four_edges` は画面端しか見ない。
    **「隣り合っているが16px空いていない」は、10周のあいだ機械照合が無かった**。
    R8〜R10で報告された実害はほとんどこの型である:
    宣言16.5px→実測9px、宣言18.0px→実測10px、宣言19.4px→実測1.5px。
    影（右下+12 / 左上−3）を含めた名目矩形どうしで見る。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for i, a in enumerate(layers):
            if a.get("opaque_fullscreen"):
                continue
            for b in layers[i + 1:]:
                if b.get("opaque_fullscreen") or a["role"] == b["role"]:
                    continue
                va, vb = a.get("variant"), b.get("variant")
                if isinstance(va, str) and isinstance(vb, str) and va != vb:
                    continue
                if _linked(a, b, "mutually_exclusive_with") or \
                   _linked(a, b, "intentional_overlap_with"):
                    continue
                ra, rb = _rect(a, vb if isinstance(vb, str) else None), \
                    _rect(b, va if isinstance(va, str) else None)
                if not ra or not rb:
                    continue
                # 影を足した実効矩形
                ra, rb = _shadow(a, ra), _shadow(b, rb)
                gx = max(ra[0], rb[0]) - min(ra[2], rb[2])   # 水平のすきま
                gy = max(ra[1], rb[1]) - min(ra[3], rb[3])   # 垂直のすきま
                gap = max(gx, gy)     # どちらかが空いていれば離れている
                # 【R10】浮動小数の誤差でちょうど16.0pxが弾かれるので許容幅を持たせる
                # 【2026-08-01 R12】下限 `0 <=` を外した。負のすきま＝交差を
                #  「交差検査の担当」として除外していたが、その交差検査は
                #  面積比5%未満を捨てる。**面積比5%未満の交差はどちらにも映らなかった。**
                #  実測3件（ai_biz_pitch -26.6px / japan_vlog -19.3px /
                #  documentary_narrated_jp -24.8px）が全部この隙間に落ちていた。
                if gap < 16 - 1e-6:
                    axis = "横" if gx > gy else "縦"
                    kind = "交差" if gap < 0 else "すきま"
                    out.append(f"  {name}: {a['role']} と {b['role']} の"
                               f"{axis}の{kind}が {gap:.1f}px（16px以上必要）")
    return out



CONSTRAINT_FIELDS = ("max_chars", "max_chars_total", "max_lines", "max_width_px",
                     "font_px", "char_width_px", "overflow_policy", "stroke_px",
                     "padding_px", "size")


def check_sibling_roles_field_parity():
    """同じ接頭辞を持つrole群で、制約フィールドの有無が食い違う。

    【2026-07-27 R11追加】R11 で見つかった「派生値の未追随」4組は、
    **距離が3種類**あった:
      1. 同roleの別フィールド（max_chars の和文注記 → max_chars_total に付け忘れ）
      2. **姉妹role**（bilingual_caption だけが「言語ごとに1行」の注記を持ち、
         bilingual_nameplate は EN2行で size.height を12px超過した）
      3. 姉妹ファイル（narrated.logo_badge を 0.933 に直したのに
         investigative.watermark は 0.94 のまま。**3.2px という数値まで同一の欠陥**）

    このうち 2 は接頭辞で機械的に拾える。`bilingual_` のように
    **同じ装置系の役どうしは、同じ制約フィールドを持つはず**である。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        groups = {}
        for l in layers:
            pre = l["role"].split("_")[0]
            if len(l["role"].split("_")) < 2:
                continue
            groups.setdefault(pre, []).append(l)
        for pre, ls in groups.items():
            if len(ls) < 2:
                continue
            for k in CONSTRAINT_FIELDS:
                has = [l["role"] for l in ls if l.get(k) is not None]
                lacks = [l["role"] for l in ls if l.get(k) is None]
                if has and lacks:
                    out.append(f"  {name}: `{pre}_*` のうち {', '.join(sorted(set(has)))} は "
                               f"{k} を持つが {', '.join(sorted(set(lacks)))} は持たない")
    return out


def check_two_tier_roles_declare_per_language():
    """二段role（font_px_en / font_px_ja を持つ）が、行数と総量を言語ごとに宣言しているか。

    【2026-07-27 R11追加】`max_lines: 2` は二段roleでは
    **「言語ごとに1行」**の意味だが、それが分かるのはコメントを読んだ人だけだった。
    EN を2行にすると narrated の bilingual_nameplate は
    `lh(44)×2 + lh(34) = 180px` で `size.height 168` を12px超過する。
    """
    out = []
    for f in sorted(glob.glob(os.path.join(STYLES, "*.yaml"))):
        name, layers, _ = _layers(f)
        for l in layers:
            if not (isinstance(l.get("font_px_en"), int)
                    and isinstance(l.get("font_px_ja"), int)):
                continue
            for k in ("max_lines_en", "max_lines_ja"):
                if l.get(k) is None:
                    out.append(f"  {name}/{l['role']}: 二段roleなのに {k} が無い"
                               f"（max_lines {l.get('max_lines')} が何を数えているか読めない）")
    return out


WARNINGS = [
    ("max_chars があるのに max_width_px が数値でない（幅の検査が効かない）",
     check_unmeasured_width_with_chars),
    ("散文に「排他／同時表示しない」とあるのにフィールドが無い",
     check_prose_exclusivity_has_field),
    ("コメントの centerY がフィールドと食い違う", check_comment_centery_matches_field),
    ("コメント内の括弧が非対応（引用やリスト記号の誤検知を含む）",
     check_comment_bracket_balance),
    ("コマンド置換で記述が消えた跡（自分の一括編集の事故）",
     check_shell_substitution_damage),
    ("size があるのに中身（font_px / stroke_px）が無い", check_size_without_font),
    ("fade なのに秒数が無い / exit 自体が無い", check_transition_seconds),
    ("max_lines≥2 なのに max_chars_total が無い（掛け算は上限にならない）",
     check_max_chars_total),
    ("max_chars があるのに overflow_policy が無い（行数は止まらない）",
     check_overflow_policy_with_max_chars),
    ("欧文を扱うroleに char_width_px が無い", check_english_needs_char_width),
    ("4辺検算: 画面端まで16pxの余裕が無い", check_four_edges),
    ("同時表示しうるrole間のすきまが16px未満", check_clearance_between_roles),
    ("姉妹role（同じ接頭辞）で制約フィールドの有無が食い違う",
     check_sibling_roles_field_parity),
    ("二段roleが行数を言語ごとに宣言していない", check_two_tier_roles_declare_per_language),
    ("別roleの centerY を旧値で引用している（派生値の未追随）",
     check_cross_role_centery_references),
]


def main():
    total = 0
    for title, fn in CHECKS:
        hits = fn()
        mark = "OK " if not hits else "NG "
        print(f"{mark}{title}: {len(hits)}件")
        for h in hits:
            print(f"     {h}")
        total += len(hits)
    warn = 0
    for title, fn in WARNINGS:
        hits = fn()
        print(f"{'-- ' if hits else 'OK '}{title}: {len(hits)}件")
        for h in hits:
            print(f"     {h}")
        warn += len(hits)
    print()
    # 【2026-08-01 R12】検査の適用範囲を必ず言う。この lint は _rect() が
    #  1920x1080 をハードコードしているため、**縦キャンバスを一度も見ていない**。
    #  「違反0件」は「横で0件」の意味しか持たない。縦は style_apply.load_style で見る。
    print("※ この検査は 1920x1080（横）のみ。縦は pipeline/style_apply.py で検算する")
    print(("クリーン" if total == 0 else f"違反 {total} 件")
          + (f" / 申し送り {warn} 件" if warn else ""))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
