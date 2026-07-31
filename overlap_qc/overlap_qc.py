# -*- coding: utf-8 -*-
"""overlap_qc.py — 汎用オーバーレイ被り検出ツール（基準プリセット式編集パイプライン共通QC資産）。

ffmpeg filter_complex スクリプト（overlay=X:Y[:enable='between(t,S,E)'] チェーン）を
解析し、動画キャンバス上で「同時刻に表示される要素ペアの矩形交差」を機械検出する。

対応パターン:
  1. 個別 overlay（テロップ・図解・サイドバー・ネームプレート等）: overlay=X:Y:enable=between(t,S,E)
  2. 素材が fade フィルタ等の単入力チェーンを経由してから overlay されるケース（ラベルを再帰解決）
  3. concat=n=N:v=1:a=0 で画像シーケンスを1トラックに結合してから overlay=0:0 するケース
     （基準プリセット切り抜き版のcaption/titleトラック等）。この場合は各コマの実フレーム量子化済み
     開始時刻を -t 尺から再構成し（fps量子化ドリフトを吸収）、個別要素として展開する。
  4. crop+pad によるレターボックス帯（vlog等）: 上下の黒帯を合成要素として追加する。

要素の実座標は各PNGのアルファチャンネル bbox から求める（フルキャンバスPNGでも
実際に描画されているピクセル範囲のみを矩形として扱う）。

被り判定:
  - 時間窓が重なる（重なり時間 > MIN_TIME_OVERLAP）
  - かつ 矩形交差面積 / min(bbox面積A, bbox面積B) > AREA_RATIO_THRESHOLD (既定5%)
  - 加えて実ピクセル衝突率（両画像の交差矩形内で共にalpha>閾値の画素の割合）も参考値として算出
    （フルキャンバス背景に対する誤検出を減らすための二次指標）

使い方:
    uv run python3 overlap_qc.py --config configs/kirinuki.json --out-json out.json

既知の誤検出パターン（DJI_0029 5スタイルの実地QCで確認・要目視トリアージ）:
  a) 「完全遮蔽」: 交差領域内で後段（z順で後）の要素が完全不透明（alpha=255一色、
     または背景色ベタ）な場合、下の要素は単に隠れるだけで同時に視認されるわけではない。
     bbox交差率100%でも実害なし（例: biztalkの企画趣旨/状況説明カードは全画面不透明
     黒背景で最前面合成のため、下の字幕・サイドバーは完全に隠れるだけ）。
  b) 「安全コンテナへの完全内包」: 小さい要素（字幕等）がレターボックス帯や背景カード等の
     大きい要素の矩形に完全に内包される場合、bbox比は自明に100%近くなるが、これは
     「意図した安全地帯に収まっている」ことの証明であり被りではない
     （例: vlogの字幕は下部レターボックス帯[y:948-1080]に全カードが完全収容されている）。
  c) 上記いずれにも該当しない、かつ pixel_collision_ratio が高い候補は実フレームを
     ffmpegで抽出し目視確認すること（本ツールはスクリーニングのみを行う）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

ALPHA_THRESHOLD = 10  # alpha値がこれ以上を「不透明ピクセル」とみなす
MIN_TIME_OVERLAP = 0.05  # 秒。これ未満の時間重複は境界誤差として無視
AREA_RATIO_THRESHOLD = 0.05  # 5%。ブリーフ指定の閾値


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Element:
    video: str
    element_id: str
    category: str
    file: Optional[str]  # None の場合は合成要素（レターボックス帯など、PNGを持たない）
    t0: float
    t1: float
    x: int
    y: int
    w: int
    h: int
    note: str = ""

    @property
    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def area(self):
        return self.w * self.h


@dataclass
class OverlapEvent:
    video: str
    a: Element
    b: Element
    t_start: float
    t_end: float
    inter_area: int
    ratio: float  # inter_area / min(area_a, area_b)
    pixel_collision_ratio: Optional[float] = None


# ---------------------------------------------------------------------------
# filter_complex パーサ
# ---------------------------------------------------------------------------

SINGLE_CHAIN_RE = re.compile(r"^\[([\w:]+)\](?!\[)(.+)\[(\w+)\];?\s*$")
OVERLAY_RE = re.compile(
    r"^\[([\w:]+)\]\[([\w:]+)\]overlay=(?:x=)?(-?[\d.]+):(?:y=)?(-?[\d.]+)"
    r"(?:[^\[]*?enable='between\(t,([\d.]+),([\d.]+)\)')?[^\[]*\[(\w+)\];?\s*$"
)
CONCAT_INPUTS_RE = re.compile(r"^((?:\[\w+\])+)concat=n=(\d+):v=1:a=\d+\[(\w+)\];?\s*$")
FPS_RGBA_RE = re.compile(r"^\[(\d+):v\]fps=\d+,format=rgba\[(\w+)\];?\s*$")
CROP_RE = re.compile(r"crop=w=(\d+):h=(\d+):x=0?:y=(\d+)")
PAD_RE = re.compile(r"pad=w=(\d+):h=(\d+):x=0?:y=(\d+):color=black")


def parse_inputs_file(path: str, project_root: str) -> list[tuple[str, Optional[float]]]:
    """render script / ffmpeg_inputs txt から -i 順のファイルリストを復元する。
    戻り値: [(絶対パス, -t値 or None), ...] インデックス0が最初の -i (ベース動画)。
    パス中の相対パス（例: "work/caption_pngs/cap_000.png"）は project_root
    （work/ の親、= 案件ディレクトリ）基準で解決する。
    """
    text = open(path, encoding="utf-8").read()
    # eval ffmpeg -y $(cat work/xxx.txt) ... の形式なら中身のオプション文字列を対象にする
    m = re.search(r"cat\s+([^\s)]+)\)", text)
    if m:
        inc_rel = m.group(1)
        inc_path = inc_rel if os.path.isabs(inc_rel) else os.path.join(project_root, inc_rel)
        if os.path.exists(inc_path):
            text = open(inc_path, encoding="utf-8").read()
    pattern = re.compile(r"(?:-loop 1 )?(?:-t ([\d.]+) )?-i \"?([^\"\s]+)\"?")
    out = []
    for dur, filepath in pattern.findall(text):
        abspath = filepath if os.path.isabs(filepath) else os.path.join(project_root, filepath)
        out.append((abspath, float(dur) if dur else None))
    return out


def resolve_chains(lines: list[str]) -> dict[str, str]:
    """単入力チェーン（fade等）のラベルを辿って元の [N:v] 入力番号に解決する辞書を作る。"""
    label_to_index: dict[str, str] = {}
    changed = True
    # 複数パスで transitively 解決する（fade->fade->overlay input のような多段も許容）
    for _ in range(5):
        changed = False
        for line in lines:
            m = SINGLE_CHAIN_RE.match(line.strip())
            if not m:
                continue
            src, _ops, dst = m.groups()
            if re.match(r"^\d+:[va]$", src):
                resolved = src
            elif src in label_to_index:
                resolved = label_to_index[src]
            else:
                continue
            if label_to_index.get(dst) != resolved:
                label_to_index[dst] = resolved
                changed = True
        if not changed:
            break
    return label_to_index


def infer_category(filepath: str) -> str:
    name = filepath.lower()
    rules = [
        ("caption", "caption(base telop)"),
        ("cap_", "caption(base telop)"),
        ("emphasis", "emphasis telop"),
        ("shock", "shock telop"),
        ("colortelop", "color telop"),
        ("nameplate", "nameplate/speaker card"),
        ("sidebar", "sidebar/chapter"),
        ("diagram", "diagram card"),
        ("context", "context/situation card"),
        ("marker", "marker annotation"),
        ("quizcard", "quiz card"),
        ("quiz_card", "quiz card"),
        ("scene", "scene label"),
        ("title", "title/nameplate card"),
    ]
    for key, cat in rules:
        if key in name:
            return cat
    return "unknown"


def get_alpha_rect(path: str) -> Optional[tuple[int, int, int, int]]:
    """PNGを開き、アルファ>閾値の実描画領域 (x0,y0,w,h) を返す（透明のみなら None）。"""
    if is_video_asset(path):
        # 動画は静的にアルファを取れない。呼び出し側で素材サイズを矩形として使う。
        return None
    arr = _load_alpha_array(path) > ALPHA_THRESHOLD
    rows = np.any(arr, axis=1)
    cols = np.any(arr, axis=0)
    if not rows.any():
        return None
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)


def build_elements_for_video(cfg: dict) -> list[Element]:
    """1本の動画の filter_complex + 入力リストから Element 一覧を構築する。"""
    video = cfg["name"]
    work_dir = cfg["work_dir"]
    fps = cfg.get("fps", 30)
    duration = cfg["duration"]
    project_root = os.path.dirname(work_dir.rstrip("/"))
    filter_path = os.path.join(work_dir, cfg["filter_complex"])
    lines = [l for l in open(filter_path, encoding="utf-8").read().splitlines() if l.strip()]

    inputs = parse_inputs_file(os.path.join(work_dir, cfg["inputs_file"]), project_root)
    # index 0 = base video (skip as asset), index i>=1 corresponds to inputs[i]
    index_to_file = {i: inputs[i][0] for i in range(len(inputs))}
    index_to_dur = {i: inputs[i][1] for i in range(len(inputs))}

    label_to_index = resolve_chains(lines)

    # concat tracks: label -> ordered list of input indices
    fps_rgba_map: dict[str, str] = {}  # csK label -> "N:v"
    for line in lines:
        m = FPS_RGBA_RE.match(line.strip())
        if m:
            idx, label = m.groups()
            fps_rgba_map[label] = f"{idx}:v"

    concat_tracks: dict[str, list[str]] = {}
    for line in lines:
        m = CONCAT_INPUTS_RE.match(line.strip())
        if m:
            labels_str, _n, dst = m.groups()
            sub_labels = re.findall(r"\[(\w+)\]", labels_str)
            resolved = []
            for lbl in sub_labels:
                if lbl in fps_rgba_map:
                    resolved.append(fps_rgba_map[lbl])
                elif lbl in label_to_index:
                    resolved.append(label_to_index[lbl])
                else:
                    resolved.append(lbl)
            concat_tracks[dst] = resolved

    elements: list[Element] = []

    def resolve_to_index(label: str) -> Optional[str]:
        if re.match(r"^\d+:[va]$", label):
            return label
        return label_to_index.get(label)

    concat_counter = 0
    for line in lines:
        m = OVERLAY_RE.match(line.strip())
        if not m:
            continue
        _base, asset_label, x, y, t0s, t1s, _out = m.groups()
        x, y = float(x), float(y)

        if asset_label in concat_tracks:
            # フルキャンバス画像シーケンスを個別要素へ展開（フレーム量子化で実開始時刻を再構成）
            concat_counter += 1
            track_name = f"concat_{concat_counter}_{asset_label}"
            cum_frames = 0
            for sub_label in concat_tracks[asset_label]:
                idx_match = re.match(r"^(\d+):v$", sub_label)
                if not idx_match:
                    continue
                idx = int(idx_match.group(1))
                fpath = index_to_file.get(idx)
                dur = index_to_dur.get(idx)
                if fpath is None or dur is None:
                    continue
                seg_frames = round(dur * fps)
                seg_t0 = cum_frames / fps
                seg_t1 = (cum_frames + seg_frames) / fps
                cum_frames += seg_frames
                rect = get_alpha_rect(fpath)
                if rect is None:
                    continue
                bx, by, bw, bh = rect
                elements.append(Element(
                    video=video,
                    element_id=f"{track_name}/{os.path.basename(fpath)}",
                    category=infer_category(fpath),
                    file=fpath,
                    t0=seg_t0, t1=seg_t1,
                    x=int(x + bx), y=int(y + by), w=bw, h=bh,
                    note="concat-track element; start time frame-quantized reconstruction",
                ))
            continue

        idx_label = resolve_to_index(asset_label)
        if idx_label is None:
            continue
        idx_match = re.match(r"^(\d+):v$", idx_label)
        if not idx_match:
            continue
        idx = int(idx_match.group(1))
        fpath = index_to_file.get(idx)
        if fpath is None or not os.path.exists(fpath):
            continue
        rect = get_alpha_rect(fpath)
        if rect is None:
            continue
        bx, by, bw, bh = rect
        t0 = float(t0s) if t0s else 0.0
        t1 = float(t1s) if t1s else duration
        elements.append(Element(
            video=video,
            element_id=os.path.basename(fpath),
            category=infer_category(fpath),
            file=fpath,
            t0=t0, t1=t1,
            x=int(x + bx), y=int(y + by), w=bw, h=bh,
        ))

    # レターボックス帯（crop+padパターン）を合成要素として追加
    full_text = "\n".join(lines)
    crop_m = CROP_RE.search(full_text)
    pad_m = PAD_RE.search(full_text)
    if crop_m and pad_m:
        crop_h = int(crop_m.group(2))
        pad_h = int(pad_m.group(2))
        pad_y = int(pad_m.group(3))
        top_h = pad_y
        bottom_h = pad_h - (pad_y + crop_h)
        if top_h > 0:
            elements.append(Element(
                video=video, element_id="letterbox_top", category="letterbox band",
                file=None, t0=0.0, t1=duration, x=0, y=0, w=1920, h=top_h,
                note="synthetic: crop+pad black bar",
            ))
        if bottom_h > 0:
            elements.append(Element(
                video=video, element_id="letterbox_bottom", category="letterbox band",
                file=None, t0=0.0, t1=duration, x=0, y=pad_y + crop_h, w=1920, h=bottom_h,
                note="synthetic: crop+pad black bar",
            ))

    return elements


# ---------------------------------------------------------------------------
# 交差検出
# ---------------------------------------------------------------------------

def rects_intersect(r1, r2):
    ax0, ay0, ax1, ay1 = r1
    bx0, by0, bx1, by1 = r2
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def pixel_collision_ratio(a: Element, b: Element, inter_rect) -> Optional[float]:
    """交差矩形内で両画像とも不透明な画素の割合（min(不透明画素数)に対する比）。
    合成要素（file=None、レターボックス帯など）は矩形全体を不透明とみなす。
    """
    ix0, iy0, ix1, iy1 = inter_rect
    w, h = ix1 - ix0, iy1 - iy0
    if w <= 0 or h <= 0:
        return None

    def mask_of(el: Element):
        if el.file is None:
            return None  # 全域不透明とみなす（Noneはその意味で扱う）
        if is_video_asset(el.file):
            # 動画はフレームごとにアルファが変わりうるため静的解析できない。
            # None＝「全域不透明」として扱う（見逃しより過検出を選ぶ保守的な近似）。
            return None
        im = _load_alpha_array(el.file)
        # el の矩形内でのローカル座標に変換
        lx0, ly0 = ix0 - el.x, iy0 - el.y
        lx1, ly1 = lx0 + w, ly0 + h
        return im[ly0:ly1, lx0:lx1]

    mask_a = mask_of(a)
    mask_b = mask_of(b)
    if mask_a is None and mask_b is None:
        return 1.0
    if mask_a is None:
        nonzero_b = int(np.count_nonzero(mask_b > ALPHA_THRESHOLD))
        return nonzero_b / (w * h) if w * h else None
    if mask_b is None:
        nonzero_a = int(np.count_nonzero(mask_a > ALPHA_THRESHOLD))
        return nonzero_a / (w * h) if w * h else None
    bin_a = mask_a > ALPHA_THRESHOLD
    bin_b = mask_b > ALPHA_THRESHOLD
    both = int(np.count_nonzero(bin_a & bin_b))
    min_nonzero = min(int(np.count_nonzero(bin_a)), int(np.count_nonzero(bin_b)))
    if min_nonzero == 0:
        return 0.0
    return both / min_nonzero


_ALPHA_ARRAY_CACHE: dict[str, "np.ndarray"] = {}


# 【2026-07-26 追加】3つ目の盲点への対処。
# 単入力チェーン解決器がPNGアセットと「動画のcrop/scaleチェーン」を区別しないため、
# webcam PiP等で .mp4 を overlay 元にすると PIL が動画を画像として開こうとして
# UnidentifiedImageError でツール全体がクラッシュしていた（screen_tutorial検証で発覚）。
# → 動画拡張子は「アルファ情報を取れない要素」として明示的に扱い、
#   クラッシュではなく「全面不透明の矩形」として近似する（bboxはoverlay座標＋素材サイズ）。
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def is_video_asset(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def _load_alpha_array(path: str) -> "np.ndarray":
    cached = _ALPHA_ARRAY_CACHE.get(path)
    if cached is not None:
        return cached
    if is_video_asset(path):
        # 動画は1フレームごとにアルファが変わりうるため静的解析できない。
        # ここでは「矩形全域が不透明」とみなす保守的な近似を返す（見逃しより過検出を選ぶ）。
        raise ValueError(
            f"動画ソースはアルファ解析できません: {path}\n"
            f"  → 呼び出し側で is_video_asset() を先に判定し、矩形全域を不透明として扱うこと"
        )
    try:
        im = Image.open(path)
    except Exception as e:
        raise ValueError(
            f"画像として開けませんでした: {path} ({type(e).__name__}: {e})\n"
            f"  → 動画やその他の非画像ファイルがoverlay元に混ざっていないか確認すること"
        ) from e
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    arr = np.array(im.getchannel("A"))
    _ALPHA_ARRAY_CACHE[path] = arr
    return arr


def detect_overlaps(elements: list[Element], area_ratio_threshold=AREA_RATIO_THRESHOLD,
                     min_time_overlap=MIN_TIME_OVERLAP, compute_pixel=True) -> list[OverlapEvent]:
    events = []
    n = len(elements)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = elements[i], elements[j]
            t_start = max(a.t0, b.t0)
            t_end = min(a.t1, b.t1)
            if t_end - t_start < min_time_overlap:
                continue
            inter = rects_intersect(a.rect, b.rect)
            if inter is None:
                continue
            ix0, iy0, ix1, iy1 = inter
            inter_area = (ix1 - ix0) * (iy1 - iy0)
            min_area = min(a.area, b.area)
            if min_area == 0:
                continue
            ratio = inter_area / min_area
            # 【2026-07-27 修正】bbox比が小さくても実ピクセルは潰れている型を取り逃していた。
            # documentary_narrated_jp の検証で **bbox_ratio 6.3% の裏で
            # pixel_collision 99.5%、目視は完全判読不能** という組が見つかった。
            # bbox比だけで足切りすると、細い装置が文字の芯を貫くケースを丸ごと落とす。
            # 足切りを1%まで下げ、その帯域は「実ピクセルが潰れている場合のみ」採用する。
            LOW_BAND = 0.01
            if ratio <= LOW_BAND:
                continue
            pcr = None
            if compute_pixel:
                try:
                    pcr = pixel_collision_ratio(a, b, inter)
                except Exception:
                    pcr = None
            if ratio <= area_ratio_threshold:
                # bbox比は閾値以下。実ピクセルが高い場合だけ拾う（低ければ従来どおり無視）
                if pcr is None or pcr < 0.8:
                    continue
            events.append(OverlapEvent(
                video=a.video, a=a, b=b, t_start=t_start, t_end=t_end,
                inter_area=inter_area, ratio=ratio, pixel_collision_ratio=pcr,
            ))
    events.sort(key=lambda e: -e.ratio)
    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_skipped(cfg: dict, elements: list) -> list[dict]:
    """検査できなかった軸を構造化して返す（akari edit-lint の設計を移植）。

    「PASS」が何を保証していないかを呼び出し側に明示するのが目的。
    単一の NOT_INSPECTED フラグでは elements==0 の1パターンしか表現できなかった。
    """
    skipped = []
    if len(elements) == 0:
        skipped.append({
            "axis": "all",
            "reason": "検査対象の要素が0件。ベースPNGへの事前合成、または多段filter_complexで"
                      "要素がマニフェストに現れていない可能性が高い",
            "action": "フレーム目視で確認すること。このQC結果を根拠に被りなしと判断しない",
        })
    # 動画ソースはアルファを静的に解析できないため、要素として追跡していない
    video_inputs = [f for f, _ in _iter_declared_inputs(cfg) if is_video_asset(f)]
    if len(video_inputs) > 1:   # index0 はベース動画なので2本以上あれば overlay 用途
        skipped.append({
            "axis": "video_overlay",
            "reason": f"動画ソース {len(video_inputs) - 1} 件はアルファを静的解析できないため検査対象外"
                      "（webcam PiP・円形マスク等）",
            "action": "PiPと他要素の重なりはフレーム目視で確認すること",
        })
    skipped.append({
        "axis": "intra_png_draw_order",
        "reason": "同一PNG内部の描画順ミス（文字にバッジが重なる等）は原理的に検出できない",
        "action": "初回レンダリング後のフレーム目視が必須",
    })
    return skipped


def _iter_declared_inputs(cfg: dict):
    """configから宣言された入力ファイルを列挙する（失敗しても空で返す）。"""
    try:
        work_dir = cfg["work_dir"]
        project_root = os.path.dirname(work_dir.rstrip("/"))
        return parse_inputs_file(os.path.join(work_dir, cfg["inputs_file"]), project_root)
    except Exception:
        return []


def run(cfg_path: str, out_json: Optional[str] = None):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    elements = build_elements_for_video(cfg)
    events = detect_overlaps(elements)

    # 【2026-07-27 追加】設計上わざと重ねる装置（ハイライトマーカー等）を除外する。
    # coconaのhighlight_markerはbase_captionの背後に敷く装置で、重なるのが正しい。
    # しかしQCが毎回FAILを出すため、**本物の衝突がノイズに埋もれていた**
    # （実際この検証では6件中2件が意図的な重なりで、残り4件の実害が見えにくかった）。
    # configに intentional_overlaps を書くと、そのペアはFAIL判定から外し、
    # 別枠 declared_overlaps に**必ず記録して可視化する**（黙って消さない）。
    #   "intentional_overlaps": [["highlight_marker", "base_caption"]]
    # 名前はPNGのbasename（連番サフィックスは無視して前方一致）で照合する。
    declared_pairs = [tuple(sorted(p)) for p in cfg.get("intentional_overlaps", [])]

    def _stem(name: str) -> str:
        base = os.path.splitext(os.path.basename(name))[0]
        return re.sub(r"_\d+$", "", base)

    def _is_declared(ev) -> bool:
        pair = tuple(sorted((_stem(ev.a.element_id), _stem(ev.b.element_id))))
        return pair in declared_pairs

    declared = [e for e in events if _is_declared(e)]
    events = [e for e in events if not _is_declared(e)]

    # 【2026-07-27 追加】全画面の不透明カードが下の要素を隠すのは「衝突」ではなく
    # **遮蔽(occlusion)** であり、設計どおりの挙動である。
    # これをFAILに数えると、全画面カードを持つスタイルは永久にFAILのままになり、
    # 本物の衝突が埋もれる（coconaで実際に実害4件が見えにくくなった）。
    # configに書く:
    #   "opaque_fullscreen": ["statement_card", "endcredit", "motion_graphic"]
    # yaml側の `opaque_fullscreen: true` と対応させること。
    # **上に乗っている場合のみ**遮蔽と判定する（下にあるなら隠せないので衝突のまま）。
    opaque_names = set(cfg.get("opaque_fullscreen", []))
    order = {el.element_id: i for i, el in enumerate(elements)}  # 後ろほど前面

    # 【2026-07-27 追加】**宣言を信じない。実寸で検証する。**
    # screen_tutorialの検証で「opaque_fullscreenと宣言しても実装が1121×581のままだと、
    # 宣言だけが独り歩きしてQC除外という誤った安全信号になる」と指摘された。
    # 実際その要素は画面の31.4%しか覆っていなかった。
    # キャンバスの98%以上を覆っていない要素は宣言を**却下**し、通常の衝突として扱う。
    CANVAS_W, CANVAS_H = 1920, 1080
    COVER_MIN = 0.98
    rejected_opaque = []
    for el in elements:
        st = re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(el.element_id))[0])
        if st not in opaque_names:
            continue
        cover = (el.w * el.h) / float(CANVAS_W * CANVAS_H)
        if cover < COVER_MIN:
            rejected_opaque.append({"element": el.element_id, "coverage": round(cover, 4),
                                    "rect": [el.x, el.y, el.x + el.w, el.y + el.h]})
    if rejected_opaque:
        opaque_names -= {re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(r["element"]))[0])
                         for r in rejected_opaque}
        print("")
        print("!!! 警告: opaque_fullscreen と宣言されているのに全画面を覆っていない要素があります !!!")
        for r in rejected_opaque:
            print(f"    {r['element']}: 被覆率 {r['coverage']*100:.1f}% "
                  f"rect={r['rect']} -> 宣言を却下し、通常の衝突として検査します")
        print("    宣言だけが独り歩きすると『QC除外してよい』という誤った安全信号になります。")
        print("    yaml側の opaque_fullscreen を外すか、実装をキャンバス全面に直してください。")

    def _is_occlusion(ev) -> bool:
        a, b = _stem(ev.a.element_id), _stem(ev.b.element_id)
        ia, ib = order.get(ev.a.element_id, -1), order.get(ev.b.element_id, -1)
        if a in opaque_names and ia > ib:
            return True
        if b in opaque_names and ib > ia:
            return True
        return False

    occluded = [e for e in events if _is_occlusion(e)]
    events = [e for e in events if not _is_occlusion(e)]
    for e in occluded:
        print(f"  [遮蔽] {e.a.element_id} x {e.b.element_id} "
              f"-> 全画面不透明カードによる遮蔽。設計どおりなので衝突には数えない")

    print(f"=== {cfg['name']} === elements={len(elements)} overlaps={len(events)}"
          + (f" (宣言済みの意図的な重なり {len(declared)}件を除外)" if declared else ""))
    for e in declared:
        print(f"  [宣言済] {e.a.element_id} x {e.b.element_id} "
              f"pixel_collision={e.pixel_collision_ratio:.3f} "
              f"-> 意図的な重なりとしてconfigで宣言済み。**目視での確認は依然必須**")

    # 【2026-07-26 追加】検査対象ゼロを「衝突ゼロ」と取り違えないようにする。
    # build_liveの検証で elements=0 / overlaps=[] という**偽の安全信号**が出た。
    # 原因は ①ベースPNGへの事前合成 ②ビデオソースの多段filter_complex(alphamerge等)
    # がマニフェストに現れず、QCから完全に不可視だったこと。
    # 「合格」と「検査していない」は全く違うので、必ず警告して非ゼロ終了する。
    if len(elements) == 0:
        print("")
        print("!!! 警告: 検査対象の要素が0件です。これは『衝突ゼロ』ではなく『検査していない』状態です !!!")
        print("    よくある原因:")
        print("      - オーバーレイをベースPNGへ事前合成しており、独立要素としてマニフェストに現れない")
        print("      - ビデオソースを多段filter_complex(alphamerge/円形マスク等)で合成しており追跡できない")
        print("      - configのパスやキー名が実際の生成物と一致していない")
        print("    → **このQC結果を根拠に「被りなし」と判断してはいけない。必ずフレーム目視で確認すること**")
    for e in events:
        print(f"  [{e.t_start:6.2f}-{e.t_end:6.2f}] {e.a.category:24s} x {e.b.category:24s} "
              f"bbox_ratio={e.ratio:5.1%} pixel_collision={e.pixel_collision_ratio}"
              f"  ({e.a.element_id} vs {e.b.element_id})")
    if out_json:
        data = {
            "video": cfg["name"],
            "element_count": len(elements),
            # 【2026-07-26 追加】検査が成立したかを機械可読にする。
            # element_count=0 は「合格」ではなく「検査不能」。status で明示的に区別する。
            # 【2026-07-27 追加】動画レイヤーを飛ばした場合は PASS ではなく PARTIAL。
            # 「衝突ゼロ」と「被りなし」は違う（交差率83%のPiP衝突がPASSで通った実例あり）
            "status": "NOT_INSPECTED" if len(elements) == 0 else (
                "FAIL" if len(events) else (
                    "PARTIAL" if any(x.get("axis") == "video_overlay"
                                     for x in _build_skipped(cfg, elements)) else "PASS"
                )
            ),
            # 【2026-07-27 追加・AkariLabs/akari-video の edit-lint 設計から移植】
            # 単一の status フラグでは「何を検査できなかったか」が表現できない。
            # 検査できなかった軸を構造化リストで明示し、PASSの意味を限定する。
            "skipped": _build_skipped(cfg, elements),
            # 【2026-07-27 追加】FAIL判定から外した「宣言済みの意図的な重なり」。
            # 黙って消すと本物との区別がつかなくなるため必ず記録する。
            # 【2026-07-27 追加】宣言されたが実寸が全画面でないため却下したもの。
            "rejected_opaque_fullscreen": rejected_opaque,
            # 【2026-07-27 追加】全画面不透明カードによる遮蔽。衝突とは別物として記録する。
            "occlusions": [
                {
                    "t_start": ev.t_start, "t_end": ev.t_end,
                    "covered": ev.a.element_id if _stem(ev.b.element_id) in opaque_names else ev.b.element_id,
                    "cover": ev.b.element_id if _stem(ev.b.element_id) in opaque_names else ev.a.element_id,
                }
                for ev in occluded
            ],
            "declared_overlaps": [
                {
                    "t_start": ev.t_start, "t_end": ev.t_end,
                    "element_a": ev.a.element_id, "element_b": ev.b.element_id,
                    "pixel_collision_ratio": ev.pixel_collision_ratio,
                    "note": "configのintentional_overlapsで宣言済み。目視確認は依然必須",
                }
                for ev in declared
            ],
            "overlaps": [
                {
                    "t_start": ev.t_start, "t_end": ev.t_end,
                    "category_a": ev.a.category, "category_b": ev.b.category,
                    "element_a": ev.a.element_id, "element_b": ev.b.element_id,
                    "rect_a": ev.a.rect, "rect_b": ev.b.rect,
                    "bbox_ratio": ev.ratio, "pixel_collision_ratio": ev.pixel_collision_ratio,
                }
                for ev in events
            ],
        }
        json.dump(data, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"-> {out_json}")
    # 【2026-07-27 追加】動画レイヤーが実在して検査から外れた場合、
    # 「衝突ゼロ」は**部分的にしか検査していない**という意味でしかない。
    # ai_daigaku_news の検証で、keyword赤枠とアバターPiPが**交差率83.0%**で
    # 重なっているのに element_count=17(生成20)・overlaps=0・**終了コード0** が返り、
    # `qc && render` のチェーンが止まらなかった。skipped に正直に書いていても
    # 終了コードが0なら誰も止まらない。
    _video_skipped = any(x.get("axis") == "video_overlay" for x in _build_skipped(cfg, elements))
    return elements, events, _video_skipped


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args()
    elements, events, video_skipped = run(args.config, args.out_json)
    # 【2026-07-27 修正】コメントで「非ゼロ終了する」と書きながら未実装だった。
    # CIやシェルの && で使えるよう、状態を終了コードに反映する。
    #   0 = PASS（検査して衝突なし）
    #   1 = FAIL（衝突あり）
    #   2 = NOT_INSPECTED（検査対象0件＝合格ではない）
    if len(elements) == 0:
        sys.exit(2)
    if events:
        sys.exit(1)
    if video_skipped:
        print("")
        print("!!! 部分検査です。『衝突ゼロ』を『被りなし』と読まないでください !!!")
        print("    動画レイヤー（webcam PiP等）はアルファを静的解析できないため検査対象外です。")
        print("    実測例: keyword赤枠とアバターPiPが**交差率83.0%**で重なっているのに")
        print("            overlaps=0 / 終了コード0 が返り、qc && render が止まらなかった。")
        print("    → **必ずフレーム目視で確認すること**。終了コードは2(部分検査)を返します。")
        sys.exit(2)
    sys.exit(0)
