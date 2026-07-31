"""合成後の実座標を算出する。

【なぜ正本にするか】R8〜R11で、検証者は毎周この計算を書き直していた。
書き直すたびに基準がぶれ、**「yamlの欠陥か、計測のミスか」の切り分けに
報告の3〜8件が費やされた**。計算はいつも同じなので固定する。

やること: ffmpeg の filter_complex から各PNGの overlay 座標を取り、
PNG の実描画bbox（alpha > 10）を足して、画面上の実座標を出す。

  実左端 = overlay_x + (alpha>10 の最小x)

【重要・R10で判明】この「実描画bbox」は**インクの座標**であって、
yaml の `size` が定義する**箱の座標ではない**。箱は centerY に対して対称だが
インクは非対称（影が右下+12 / 左上−3、グリフのascent/descentも非対称）なので、
インク基準で16pxのクリアランスを取ると**宣言矩形では4pxになる**。
実装者が読むのは箱なので、**導出は箱基準（`--boxes` 出力）で行い、
インク実測（`--ink` 出力）は「箱の宣言が実物と合っているか」の照合に使う。**
"""
import argparse
import json
import os
import re
import sys

USAGE = """
  uv run --with pillow --with numpy python3 measure.py --work <work_dir> --name <name>
    work_dir/ffmpeg_inputs_<name>.txt と filter_complex_<name>.txt を読む
  uv run --with pyyaml python3 measure.py --boxes <style.yaml>
    yaml の size/position から**箱**の名目矩形（影込み）を出す
"""

SHADOW = (-3, -3, 12, 12)   # 左, 上, 右, 下（schema.md の実測値）


def ink_rects(work, name):
    from PIL import Image
    import numpy as np
    inp = open(os.path.join(work, f"ffmpeg_inputs_{name}.txt")).read()
    files = re.findall(r'-i (\S+)', inp)
    fc = open(os.path.join(work, f"filter_complex_{name}.txt")).read()
    pos = {}
    for m in re.finditer(r'\[(\d+):v\]', fc):
        i = int(m.group(1))
        o = re.search(r'overlay=(-?\d+):(-?\d+)', fc[m.end():m.end() + 500])
        if o and i not in pos:
            pos[i] = (int(o.group(1)), int(o.group(2)))
    out = {}
    for i, f in enumerate(files):
        base = os.path.basename(f)
        if not base.endswith(".png") or i not in pos:
            continue
        a = np.array(Image.open(f).convert("RGBA"))
        mask = a[:, :, 3] > 10
        if not mask.any():
            continue
        ys, xs = np.where(mask)
        x, y = pos[i]
        out[base[:-4]] = [int(x + xs.min()), int(y + ys.min()),
                          int(x + xs.max() + 1), int(y + ys.max() + 1)]
    return out


def box_rects(path):
    import yaml
    d = yaml.safe_load(open(path, encoding="utf-8"))
    out = {}
    for l in d.get("telop_layers") or []:
        sz, po = l.get("size"), l.get("position")
        if not isinstance(sz, dict) or not isinstance(po, dict):
            continue
        try:
            cx, cy = float(po["centerX"]) * 1920, float(po["centerY"]) * 1080
            w, h = float(sz["width"]), float(sz["height"])
        except (KeyError, TypeError, ValueError):
            continue
        sl, st, sr, sb = (0, 0, 0, 0) if \
            str(l.get("color_shadow", "")).strip('"') == "none" else SHADOW
        key = l["role"]
        out.setdefault(key, []).append(
            [round(cx - w / 2 + sl, 1), round(cy - h / 2 + st, 1),
             round(cx + w / 2 + sr, 1), round(cy + h / 2 + sb, 1)])
    return out


def main():
    p = argparse.ArgumentParser(usage=USAGE)
    p.add_argument("--work")
    p.add_argument("--name")
    p.add_argument("--boxes")
    a = p.parse_args()
    if a.boxes:
        r = box_rects(a.boxes)
        print("# 箱の名目矩形（影込み）L T R B —— **導出はこちらを使う**")
    elif a.work and a.name:
        r = ink_rects(a.work, a.name)
        print("# インクの実座標 L T R B —— 箱の宣言が実物と合っているかの照合用")
    else:
        p.print_usage(); sys.exit(2)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
