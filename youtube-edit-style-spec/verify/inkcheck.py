"""13スタイル×縦横を実際にPNGへ描き、**実インク**で4辺を測る。

推定矩形（size / max_width_px からの箱）で通っていても、実際に描いた文字が
どこに乗るかは別。過去に「全画面カードが31.6%しか覆っていない」を
数値検査が全部通した実績がある。**描いて測る。**
"""
import sys, json
sys.path.insert(0, "/Users/kousuke/Documents/編集作業/pipeline")
import style_apply as SA
import run as R
from PIL import Image, ImageDraw

# 実素材の字幕を使う（人工文字列だと形態素解析を一度も通さないまま通ってしまう）
CAPS = json.load(open("/Users/kousuke/Documents/編集作業/IMG_2514_v9/work/captions.json"))
SHADOW_RB, SHADOW_LT, MIN = 12, 3, 16

def selftest():
    """**この検査が画面外を検出できることを、毎回まず確かめる。**

    2026-08-01: 「一致被覆100%」は埋め合わせの後に数えていて構造的に100%だった。
    「4辺検算NG 0件」は size 宣言ありのroleしか見ていなかった。
    0という結果は、0でない場合を検出できることを示してからでないと意味がない。
    """
    cw, ch = 1080, 1920
    f = SA._pil_font(72)
    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((-40, 500), "画面外にはみ出す行", font=f, fill=(255,255,255,255))
    bb = im.getbbox()
    if not bb or bb[0] - SHADOW_LT >= MIN:
        raise SystemExit("自己確認に失敗: わざとはみ出させても検出できない。検査が壊れている")
    print(f"自己確認: わざとはみ出させた行で左 {bb[0]-SHADOW_LT}px を検出 → 検査は生きている\n")


selftest()

rows = []
for sid in SA.all_style_ids():
    for cw, ch, lb in ((1920, 1080, "横"), (1080, 1920, "縦")):
        try:
            st = SA.load_style(sid, cw, ch)
        except Exception:
            continue
        plan = SA.render_plan(st, CAPS, cw, ch, speaker_kinds=True)
        # 【2026-08-01】検証は**実際に描くもの全部**を覆っていないと意味がない。
        #  論点見出し帯と強調テロップを足したのに、検証は字幕しか見ていなかった。
        items = R.topic_items(CAPS, 134.7)
        for rn in ("title_bar", "chapter_tag", "chapter_tab", "question_tab", "running_outline"):
            if any(x["role"] == rn and x["resolved"] and x.get("font_px") for x in st["roles"]):
                plan += SA.render_role(st, rn, items, cw, ch); break
        kws = R.keyword_items(CAPS, items, 134.7)
        for rn in ("keyword", "positive_keyword", "shock_keyword", "highlight_marker"):
            if any(x["role"] == rn and x["resolved"] and x.get("font_px") for x in st["roles"]):
                plan += SA.render_role(st, rn, kws, cw, ch); break
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        worst = {"左": 1e9, "右": 1e9, "上": 1e9, "下": 1e9}
        over = 0
        for p in plan:
            f = SA._pil_font(p["font_px"])
            d2 = ImageDraw.Draw(Image.new("RGBA", (cw, ch), (0, 0, 0, 0)))
            d2.text((p["x"], p["y"]), p["text"], font=f, fill=(255,255,255,255),
                    stroke_width=p["stroke_px"], stroke_fill=(0,0,0,255))
            bb = d2._image.getbbox()      # 実インクの外形（alpha>0）
            if not bb:
                continue
            # 【2026-08-02】実インクだけ見ると capseq より甘い判定になる。
            #  capseq は計画の bbox（送り幅ベース＝保守的）で弾いており、
            #  ai_biz_pitch の base_caption 4行は「実インク22px / 計画14px」で
            #  inkcheck だけ通っていた。**実際に弾く側に合わせる。**
            pb = p["bbox"]
            bb = (min(bb[0], pb[0]), min(bb[1], pb[1]),
                  max(bb[2], pb[2]), max(bb[3], pb[3]))
            m = {"左": bb[0] - SHADOW_LT, "右": cw - (bb[2] + SHADOW_RB),
                 "上": bb[1] - SHADOW_LT, "下": ch - (bb[3] + SHADOW_RB)}
            for k in m:
                worst[k] = min(worst[k], m[k])
            if any(v < MIN for v in m.values()):
                over += 1
        rows.append((sid, lb, worst, over, len(plan)))

print(f"{'style':28s} {'向き':4s} {'左':>7s} {'右':>7s} {'上':>7s} {'下':>7s}  NG/行数")
ng_total = 0
for sid, lb, w, over, n in rows:
    flag = "  ← NG" if over else ""
    ng_total += over
    print(f"{sid:28s} {lb:4s} {w['左']:7.1f} {w['右']:7.1f} {w['上']:7.1f} {w['下']:7.1f}  {over:3d}/{n}{flag}")
print(f"\n実インクで4辺16px未満だった描画行: 合計 {ng_total}")
