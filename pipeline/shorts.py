#!/usr/bin/env python3
"""長尺から Shorts を切り出す。

一次指示書の構成要件（auto_youtube_edit_agent.md「4. 構成 Shorts」）:
  0-2秒   結果・違和感・強い問い・完成物を先に見せる
  2-8秒   状況説明
  8-20秒  実演、証拠、変化
  20秒〜  回収、学び、軽いCTA
また「Shortsでは原則イントロを付けず、冒頭フックをそのまま使う」。

区間の選定は**人（またはLLM）が決めて外から渡す**。規則で「盛り上がり」を
推定する実装は素材ごとに外すので作らない。ここがやるのは、
選ばれた区間を繋ぎ、字幕を新しい時間軸へ載せ替えることだけ。

区間は**元素材の時刻**で与える。長尺のカット後の時刻ではない。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as R  # noqa: E402


def cut_segments(src, segs, out, wd):
    """区間を切り出して繋ぐ。再エンコードする（コピーだとGOP境界でずれる）。"""
    parts = []
    for i, (a, b) in enumerate(segs):
        p = os.path.join(wd, f"_seg{i:02d}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a),
                        "-to", str(b), "-i", src,
                        "-c:v", "h264_videotoolbox", "-b:v", "12M",
                        "-c:a", "aac", "-b:a", "192k", p], check=True)
        parts.append(p)
    # concat はリストファイルの**あるディレクトリからの相対**でパスを解決する。
    # 相対パスを書くと wd が二重になる（実測: work/work/_seg00.mp4）。絶対で書く。
    lst = os.path.join(wd, "_concat.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True)
    for p in parts:
        os.remove(p)
    os.remove(lst)
    return out


def remap_caps(caps_src, segs):
    """元素材の時刻で並ぶ字幕を、繋いだ後の時間軸へ載せ替える。

    区間をまたぐ字幕は**切り捨てずに区間内へ収める**（頭やお尻が欠けた
    字幕を出すより、短く出す方がまだ読める）。
    """
    out, base = [], 0.0
    for a, b in segs:
        for c in caps_src:
            s, e = c["start"], c["end"]
            if e <= a or s >= b:
                continue
            ns, ne = max(s, a) - a + base, min(e, b) - a + base
            if ne - ns < 0.25:
                continue
            out.append(dict(c, start=round(ns, 3), end=round(ne, 3)))
        base += b - a
    return out, base
