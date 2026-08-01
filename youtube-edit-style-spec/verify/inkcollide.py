"""役どうしの被りを**実ピクセル**で測る（R12 再測定）。

R6–R11 の pixel_collision は overlap_qc.py の alpha スライスのオフセット誤りで
全部信用できない（外部PRで修正済み）。矩形での交差検査は通っていても、
実際に描いたインクがどれだけ重なるかは別。

全roleを、そのroleが宣言した font / 幅 / 行数で描き、alpha>0 の実インクで
ピクセル交差を数える。**同時に出ない組（排他・変種違い）は除く。**
"""
import sys
sys.path.insert(0, "/Users/kousuke/Documents/編集作業/pipeline")
import style_apply as SA
from PIL import Image, ImageDraw

# 文字は日本語の実文。人工文字列だと形態素も字幅も現実とずれる
FILLER = "この動画で解説する内容をここに入れて実際の字幅で測ります"

def ink_mask(role, cw, ch):
    """その役の実インクを 1bit マスクで返す。文字を持たない役は矩形で埋める。"""
    im = Image.new("L", (cw, ch), 0)
    d = ImageDraw.Draw(im)
    r = role["rect"]
    if role.get("text_content") == "none" or not role.get("font_px"):
        d.rectangle([r[0], r[1], r[2], r[3]], fill=255)   # 図形・供給素材は箱で見る
        return im
    fp, st = role["font_px"], role["stroke_px"] or 0
    f = SA._pil_font(fp)
    wof, _, _ = SA._measurer(fp, st)
    mw = role.get("max_width_px") or (r[2] - r[0])
    ml = role["max_lines"] or 1
    lines = SA.wrap_text(FILLER, wof, mw, ml)[:ml]
    lh = SA.line_height(fp)
    top = (r[1] + r[3]) / 2 - lh * len(lines) / 2
    cx = (r[0] + r[2]) / 2
    for k, ln in enumerate(lines):
        b = d.textbbox((0, 0), ln, font=f, stroke_width=st)
        d.text((cx - (b[2]-b[0])/2 - b[0], top + lh*k - b[1]), ln,
               font=f, fill=255, stroke_width=st, stroke_fill=255)
    return im

def area(m):
    return sum(1 for v in m.getdata() if v)


def selftest():
    """**この検査が交差を検出できることを、毎回まず確かめる。**

    2026-08-01: 「一致被覆100%」も「4辺NG 0件」も、失敗しようがない書き方に
    なっていたせいで長く嘘を出し続けた。交差0という結果は、
    交差を検出できることを示してからでないと意味がない。
    """
    import copy
    st = SA.load_style("kirinuki", 1080, 1920)
    live = [r for r in st["roles"] if r["resolved"] and not r["opaque_fullscreen"]]
    a = copy.deepcopy(next(r for r in live if r["role"] == "caption"))
    b = next(r for r in live if r["role"] == "title_bar")
    a["rect"] = b["rect"]
    ma, mb = ink_mask(a, 1080, 1920), ink_mask(b, 1080, 1920)
    n = area(Image.composite(ma, Image.new("L", (1080, 1920), 0), mb))
    if n <= 0:
        raise SystemExit("自己確認に失敗: わざと重ねても交差を検出できない。検査が壊れている")
    print(f"自己確認: わざと重ねた組で {n}px を検出 → 検査は生きている\n")


selftest()

tot = 0
for sid in SA.all_style_ids():
    for cw, ch, lb in ((1920, 1080, "横"), (1080, 1920, "縦")):
        try:
            st = SA.load_style(sid, cw, ch)
        except Exception:
            continue
        live = [r for r in st["roles"] if r["resolved"] and not r["opaque_fullscreen"]]
        masks = {r["role"]: ink_mask(r, cw, ch) for r in live}
        for i, a in enumerate(live):
            for b in live[i+1:]:
                if a["role"] == b["role"] or SA._linked(a, b):
                    continue          # 排他・意図的重なりは対象外
                ma, mb = masks[a["role"]], masks[b["role"]]
                inter = Image.composite(ma, Image.new("L", (cw, ch), 0), mb)
                n = area(inter)
                if n:
                    aa, bb = area(ma), area(mb)
                    ratio = n / max(1, min(aa, bb))
                    print(f"{sid} {lb}: {a['role']} x {b['role']} 実インク交差 "
                          f"{n}px（小さい側の {ratio:.1%}）")
                    tot += 1
print(f"\n実インクで交差した組: {tot}")
