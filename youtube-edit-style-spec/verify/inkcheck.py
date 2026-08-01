"""13スタイル×縦横を実際にPNGへ描き、**実インク**で4辺を測る。

推定矩形（size / max_width_px からの箱）で通っていても、実際に描いた文字が
どこに乗るかは別。過去に「全画面カードが31.6%しか覆っていない」を
数値検査が全部通した実績がある。**描いて測る。**
"""
import sys, json
sys.path.insert(0, "/Users/kousuke/Documents/編集作業/pipeline")
import style_apply as SA
from PIL import Image, ImageDraw

# 実素材の字幕を使う（人工文字列だと形態素解析を一度も通さないまま通ってしまう）
CAPS = json.load(open("/Users/kousuke/Documents/編集作業/IMG_2514_v9/work/captions.json"))
SHADOW_RB, SHADOW_LT, MIN = 12, 3, 16

rows = []
for sid in SA.all_style_ids():
    for cw, ch, lb in ((1920, 1080, "横"), (1080, 1920, "縦")):
        try:
            st = SA.load_style(sid, cw, ch)
        except Exception:
            continue
        plan = SA.render_plan(st, CAPS, cw, ch, speaker_kinds=True)
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
