#!/usr/bin/env python3
"""サムネイル5案を作る。

一次指示書 auto_youtube_edit_agent.md「サムネイル5案」に対応する。
5方向（結果約束/問題解決/権威新規性/証拠数字/人物感情）を同時に作り、
CTR仮説が最も違う3案をA/Bテスト候補に選ぶ、という運用を前提にしている。

仕様のうち実装で効いている規則：
  - 文字は3〜6語。スマホの小さい表示で読める太さ
  - 顔・結果物・強いコントラストのどれかを必ず入れる
  - **数字は編集側で載せ、生成画像に描かせない** → 文字は全部 Pillow で描く
  - タイトルとサムネで同じことを重複させない（サムネ＝感情/結果）

背景は「完成動画の最重要フレーム」から採る。ただし **字幕が焼かれていない
元素材** から採ること（final.mp4 から採ると字幕が二重に写る）。
"""
import os
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720          # YouTube 推奨
FONT_CANDIDATES = (
    os.path.expanduser("~/Library/Fonts/NotoSansJP-Bold.ttf"),
    "/Library/Fonts/NotoSansJP-Bold.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
)


def _font(px):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    raise SystemExit("日本語の太字フォントが見つからない")


def grab(src, t, out):
    """元素材から1フレーム抜く。字幕が焼かれていないものを使うこと。"""
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", src,
                    "-frames:v", "1", "-y", out], check=True)
    return out


def _cover(im, box_w, box_h, focus_y=0.42):
    """縦素材を横サムネに収める。切り取りの中心を focus_y で寄せる
    （被写体が上寄りなので、真ん中で切ると天井ばかりになる）。"""
    k = max(box_w / im.width, box_h / im.height)
    im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                   Image.LANCZOS)
    x = (im.width - box_w) // 2
    y = int((im.height - box_h) * focus_y)
    return im.crop((x, y, x + box_w, y + box_h))


def compose(bg_path, lines, accent, out_path, focus_y=0.42, side="left"):
    """背景1枚＋文字で1案を作る。

    lines : [(文字列, 大きさ倍率)] 上から順に描く
    accent: 強調色。案ごとに変えて「別の仮説」だと見て分かるようにする
    side  : 文字を置く側。背景の主題と重ならない方に置く
    """
    src = Image.open(bg_path).convert("RGB")
    im = _cover(src, W, H, focus_y)

    # 【2026-08-02】固定の濃さで暗幕を掛けていたら、白い紙が背景の案で
    #  白文字がまったく読めなかった（B案・E案）。目視では「なんとなく薄い」
    #  としか分からないので、**文字を置く帯の明るさを実測**して濃さを決める。
    import numpy as np
    band = np.array(im.convert("L"))
    bx0, bx1 = (0, int(W * 0.56)) if side == "left" else (int(W * 0.44), W)
    lum = float(band[:, bx0:bx1].mean())
    # 帯の明るさを SCRIM_TARGET まで落とすのに必要な不透明度
    need = max(0.0, 1.0 - SCRIM_TARGET / max(lum, 1.0))
    peak = int(min(248, 255 * need * 1.25 + 60))

    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    half = int(W * 0.56)
    for i in range(half):
        a = int(peak * (1 - i / half) ** 1.35)
        x = i if side == "left" else W - 1 - i
        d.line([(x, 0), (x, H)], fill=(6, 8, 16, a))
    im = Image.alpha_composite(im.convert("RGBA"), panel)

    d = ImageDraw.Draw(im)
    pad = 52
    total = sum(int(96 * s) + 20 for s, in [(s,) for _, s in lines])
    y = (H - total) // 2
    for text, scale in lines:
        px = int(96 * scale)
        f = _font(px)
        tw = d.textbbox((0, 0), text, font=f)[2]
        x = pad if side == "left" else W - pad - tw
        # 縁取りは**本体と反対の明るさ**にする。白文字に白縁を付けると
        # 白背景で完全に消える（実測でB案・E案がそうなった）
        fill_lum = 0.299 * accent[0] + 0.587 * accent[1] + 0.114 * accent[2]
        stroke = (0, 0, 0) if fill_lum > 140 else (255, 255, 255)
        d.text((x + 5, y + 5), text, font=f, fill=(0, 0, 0, 150))
        d.text((x, y), text, font=f, fill=accent,
               stroke_width=max(4, px // 16), stroke_fill=stroke)
        y += px + 20

    out = im.convert("RGB")
    out.save(out_path, quality=95)
    _assert_readable(out, side, out_path)
    return out_path


SCRIM_TARGET = 88     # 文字帯をこの明るさまで落とす【導出値・要実測】
CONTRAST_MIN = 55     # 文字と背景の明度差の下限【導出値・要実測】


def _assert_readable(im, side, path):
    """文字が背景に埋もれていないか**測って**確かめる。
    目視だと「読めるつもり」で通ってしまうので、数値で落とす。"""
    import numpy as np
    a = np.array(im.convert("L"))
    x0, x1 = (0, int(W * 0.56)) if side == "left" else (int(W * 0.44), W)
    band = a[:, x0:x1]
    # 文字は縁取りで極端な明暗を作る。その差が小さい＝埋もれている
    hi, lo = np.percentile(band, 97), np.percentile(band, 20)
    if hi - lo < CONTRAST_MIN:
        raise SystemExit(
            f"サムネの文字が読めない: {os.path.basename(path)} "
            f"明度差 {hi - lo:.0f} < {CONTRAST_MIN}")


def build(src_video, out_dir, plan):
    """plan = [{"t":秒, "lines":[...], "accent":(r,g,b), "name":"A_結果約束",
                "focus_y":0.42, "side":"left"}, ...]"""
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for p in plan:
        raw = os.path.join(out_dir, f"_bg_{p['name']}.png")
        grab(src_video, p["t"], raw)
        out = os.path.join(out_dir, f"{p['name']}.jpg")
        compose(raw, p["lines"], p["accent"], out,
                p.get("focus_y", 0.42), p.get("side", "left"))
        os.remove(raw)
        made.append(out)
    return made
