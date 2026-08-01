"""レンダリング前の環境・素材プリフライト。

【なぜ要るか】2026-08-01、実素材を初めて通したとき、
`environment-notes.md` に**すでに書いてあった制約を3つとも踏んだ**:
  L8  ffmpegにlibass/drawtextなし        → drawtext指定で落ちるまで気づかず
  L9  回転メタデータを必ず確認            → 1920x1080前提で字幕を作り右端が切れた
  L14 フルキャンバス多段overlayは激遅     → 20分かけて中断
**散文の知見は読まれない。機械で止める。**

  uv run --with pillow python3 verify/preflight.py <素材>
"""
import json, subprocess, sys, shutil

def probe(src, args):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", args, "-of", "default=nw=1:nk=1", src],
                       capture_output=True, text=True)
    return r.stdout.strip()

def ffmpeg_has(name):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         capture_output=True, text=True).stdout
    return any(l.split()[1] == name for l in out.splitlines()
               if len(l.split()) > 1 and not l.startswith(" ---"))

def main(src):
    ng, warn = [], []
    print("=" * 62)

    # ① ffmpeg の機能
    for f in ("drawtext", "subtitles", "ass"):
        if ffmpeg_has(f):
            print(f"  [ok]   {f} が使える")
        else:
            warn.append(f)
    if warn:
        print(f"  [注意] ffmpegに {', '.join(warn)} が無い")
        print("         → **字幕はPillowでPNG生成し overlay で合成する**")
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    if "h264_videotoolbox" in enc:
        print("  [ok]   h264_videotoolbox が使える → HWエンコードを使うこと")

    # ② 素材の回転（これを外すと座標が全部ずれる）
    w, h = int(probe(src, "stream=width")), int(probe(src, "stream=height"))
    rot = probe(src, "stream_side_data=rotation") or "0"
    r = abs(int(float(rot.splitlines()[0]))) % 180
    cw, ch = (h, w) if r == 90 else (w, h)
    print(f"\n  素材の宣言寸法 : {w}x{h}   回転: {rot.splitlines()[0]}")
    print(f"  **実キャンバス : {cw}x{ch}**  ({'縦' if ch > cw else '横'})")
    if (cw, ch) != (w, h):
        print("  [!!]   ffprobeのwidth/heightは**回転前**。この値で座標を組むと必ずずれる")
    if ch > cw:
        ng.append("縦素材。styles/*.yaml は全て 1920x1080 前提なので"
                  "そのままでは4辺検算・すきま検算が成立しない")

    # ③ 音（loudnorm 1パスで届くか）
    a = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-af",
                        "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    I = next((l.split()[-2] for l in a.splitlines() if l.strip().startswith("I:")), None)
    if I:
        print(f"\n  ラウドネス     : {I} LUFS")
        if float(I) < -25:
            ng.append(f"素材が {I} LUFS と極端に小さい。"
                      "**loudnorm 1パスでは -14 に届かない → 2パス必須**")

    print("=" * 62)
    for m in ng:
        print(f"  ★ {m}")
    print(f"\n  要対処 {len(ng)}件")
    return 1 if ng else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
