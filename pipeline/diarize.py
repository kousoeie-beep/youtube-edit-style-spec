#!/usr/bin/env python3
"""話者分離（diarization）。run.py から呼べる単体モジュール。

  uv run --with librosa --with soundfile --with scipy --with numpy \
     python3 diarize.py <wav> [--work DIR] [--caps captions.json] [--force-local]

経路①  OpenAI `gpt-4o-transcribe-diarize`（APIキーがある場合）
経路②  ローカル音響特徴クラスタリング（キーが無い場合・追加モデル不要）

APIキーは **os.environ から取るだけ**。.env は読まない。値はログに出さない。

── 一次情報（2026-08-01 実際に取得して確認）────────────────────────
  https://developers.openai.com/api/docs/guides/speech-to-text
    model            : "gpt-4o-transcribe-diarize"
    endpoint         : POST /v1/audio/transcriptions
    response_format  : "diarized_json"  ← これでないと話者ラベルが返らない
    chunking_strategy: 30秒超の音声では必須（"auto" または VAD 設定）
    segment の中身   : speaker / text / start / end
    ファイル上限     : 25 MB（mp3, mp4, mpeg, mpga, m4a, wav, webm）
    既知話者         : known_speaker_names[] / known_speaker_references[]（最大4人・2〜10秒）
    プロンプト非対応
  https://developers.openai.com/api/docs/pricing
    $0.006 / 分
"""
import argparse, json, os, subprocess, sys

API_MODEL = "gpt-4o-transcribe-diarize"
# 経路によらない妥当性の閾値。どちらも【導出値・要実測】
MAX_SPEAKERS = 4      # 対話動画で5人以上は分割しすぎ
MIN_TURN_SEC = 0.8    # 実際の発話ターンは秒単位。0.3秒は分割しすぎ
# 言語指定が使えないモデルなので、prompt で日本語へ誘導する
DIAR_PROMPT = "以下は日本語の会話です。インタビュアーと回答者が交互に話します。"
API_URL   = "https://api.openai.com/v1/audio/transcriptions"
API_LIMIT = 25 * 1024 * 1024          # 一次情報の 25MB 上限

def log(m): print(m, flush=True)

def _labels(order):
    """出現順に A, B, C … を割り当てる写像を返す。"""
    seen, m = [], {}
    for k in order:
        if k not in seen: seen.append(k)
    for i, k in enumerate(seen):
        m[k] = chr(ord("A") + i) if i < 26 else f"S{i}"
    return m

# ────────────────────────────────────────────────────────────
# 経路① OpenAI gpt-4o-transcribe-diarize
# ────────────────────────────────────────────────────────────
def _api_diarize(wav_path, work_dir):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None

    # 25MB 上限。a_enh.wav は 192kHz PCM で 51MB あるので必ず落とす必要がある。
    send = wav_path
    if os.path.getsize(wav_path) > API_LIMIT:
        send = os.path.join(work_dir, "a_diar.mp3")
        if not os.path.exists(send):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav_path,
                            "-ac", "1", "-ar", "16000", "-b:a", "64k", send],
                           capture_output=True, text=True)
        if not os.path.exists(send) or os.path.getsize(send) > API_LIMIT:
            log("  [API] 25MB以下に圧縮できなかった → ローカルへ")
            return None
        log(f"  [API] 25MB超のため mp3 64k に変換 "
            f"({os.path.getsize(wav_path)/1e6:.1f}MB → {os.path.getsize(send)/1e6:.1f}MB)")

    cmd = ["curl", "-sS", "-X", "POST", API_URL,
           "-H", f"Authorization: Bearer {key}",       # 値は表示しない
           "-F", f"file=@{send}",
           "-F", f"model={API_MODEL}",
           "-F", "response_format=diarized_json",
           # 【2026-08-02 実測】このモデルは言語を指定できない。
           #   languages[]=ja → 「このモデルでは非対応」と明示的に拒否
           #   language=ja    → エラーは出ないが効かない（英語のまま出る）
           #  自動判定に任せるしかないが、日本語音声を英語として書き起こす
           #  （実測: "Could I just know what they did to create this?"）。
           #  そこで prompt で日本語へ誘導する。
           "-F", f"prompt={DIAR_PROMPT}",
           "-F", "chunking_strategy=auto"]             # 30秒超で必須
    # 【2026-08-02】language を渡しておらず、日本語音声を**英語として**書き起こして
    #  いた（実測: " Could I just know what they did to create this?"）。
    #  中身が別物なので話者の割り当ても壊れ、2人の対話に9〜11人を返していた。
    #  文字起こし側は言語を指定していたのに、話者分離側だけ抜けていた。
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except Exception:
        log(f"  [API] 応答がJSONでない: {r.stdout[:200]}")
        return None
    if "error" in d:
        log(f"  [API] エラー: {str(d['error'])[:200]}")
        return None

    json.dump(d, open(os.path.join(work_dir, "diarization_api.json"), "w"),
              ensure_ascii=False, indent=1)

    segs = d.get("segments") or []
    if not segs:
        log(f"  [API] segments が空: {list(d.keys())}")
        return None

    raw = [(float(s["start"]), float(s["end"]), str(s.get("speaker", "?")),
            s.get("text", "")) for s in segs]
    m = _labels([x[2] for x in raw])
    turns = [{"start": round(s, 3), "end": round(e, 3), "speaker": m[sp], "text": tx}
             for s, e, sp, tx in raw]
    log(f"  [API] {API_MODEL} → {len(turns)}ターン / 話者{len(set(t['speaker'] for t in turns))}人")
    return turns

# ────────────────────────────────────────────────────────────
# 経路② ローカル音響特徴クラスタリング
#   MFCC 20次の平均＋標準偏差（＝声質）だけを使い、Ward法で2〜3に分ける。
#   pyannote のような学習済み話者埋め込みは使わない（導入コストが高い）。
#
# 【2026-08-01 実測。この経路は当てにならない、というのが実測の結論】
#   IMG_2514（2人・2分14秒）で「質問とその答えは必ず別人」（4組）
#   「1文が分割されただけの字幕は必ず同一人」（10組）という、
#   テキストから客観的に取れる制約14件で採点した。
#
#   窓の置き方（位相）を20通りずらして測った平均:
#     音源      窓    k | 質問→答えの境界 4点満点（平均）
#     a_enh    2.5s  2 | 0.85
#     a_enh    3.0s  3 | 1.55
#     a16k(生) 2.5s  3 | 1.80  ← 全構成中の最良
#   一方、**ラベルを完全ランダムに振った場合の期待値が約2.0**。
#   つまり最良構成でも偶然と同等かそれ以下。この素材では機能していない。
#
#   総当たりでは 13/14 を出す構成もあったが、窓を6ミリ秒ずらすだけで
#   10/14 に落ちた。あれは探索で拾った当たりくじであって実力ではない。
#
#   理由（この素材固有）: 男性2名でF0が100〜125Hzに重なる／手持ちの
#   単一マイクが動く／残響が強い／88秒以降はTVで生成動画の音声が鳴る。
#
#   → 出力には必ずシルエット係数を confidence として付け、低ければ
#     low_confidence フラグを立てて警告する。字幕に焼き込む用途では
#     このフラグが立っている限り使ってはいけない。
# ────────────────────────────────────────────────────────────
SR        = 16000
TOP_DB    = 30     # 無音判定のしきい値（librosa.effects.split）
MIN_UNIT  = 0.30   # これ未満の発話片は捨てる（秒）
SPLIT_LEN = 2.50   # これを超える塊はこの長さで分割（1区間に2人入るのを防ぐ）
MIN_TURN  = 0.40   # 合併後、これ未満のターンは前後に吸収（秒）
SMOOTH    = True   # 単発の飛びを均すか
K_MARGIN  = 0.02   # k=3 を採るのに必要なシルエットの上積み。実測差は0.003＝雑音
CONF_MIN  = 0.15   # これ未満なら「信用するな」と警告する（実測値は0.07）

def _units(y, sr, segments=None):
    """解析単位（開始, 終了）秒のリストを作る。

    注意: run.py が作る a_enh.wav は loudnorm と acompressor を通っており、
    無音が持ち上がって **VADが機能しない**（実測: top_db=30 で「発話」1区間・
    カバー率100%）。その場合ここは実質「全体を SPLIT_LEN 秒で刻んだ等間隔窓」
    になる。生の a16k.wav なら top_db=30 で136区間・91.4%。
    """
    import librosa
    if segments:
        regions = [(float(s["start"]), float(s["end"])) for s in segments]
    else:
        iv = librosa.effects.split(y, top_db=TOP_DB, frame_length=1024, hop_length=256)
        regions = [(a / sr, b / sr) for a, b in iv]
        cov = sum(b - a for a, b in regions) / max(len(y), 1)
        if cov > 0.95:
            log(f"  [local] VADが無効（カバー率{cov*100:.1f}%）→ {SPLIT_LEN}秒の等間隔窓として扱う")
    out = []
    for a, b in regions:
        if b - a < MIN_UNIT: continue
        n = max(1, int(round((b - a) / SPLIT_LEN)))
        step = (b - a) / n
        for i in range(n):
            s, e = a + step * i, a + step * (i + 1)
            if e - s >= MIN_UNIT: out.append((s, e))
    return out

def _features(y, sr, units):
    import numpy as np, librosa
    F = []
    for s, e in units:
        seg = y[int(s * sr):int(e * sr)]
        if len(seg) < int(0.2 * sr):
            seg = np.pad(seg, (0, int(0.2 * sr) - len(seg)))
        mf  = librosa.feature.mfcc(y=seg, sr=sr, n_mfcc=20, n_fft=512, hop_length=160)
        rms = librosa.feature.rms(y=seg, frame_length=512, hop_length=160)[0]
        n = min(mf.shape[1], rms.shape[0]); mf, rms = mf[:, :n], rms[:n]
        keep = rms > max(rms.max() * 0.2, 1e-7)     # 無音フレームは声質を汚す
        if keep.sum() >= 3: mf = mf[:, keep]
        F.append(np.concatenate([mf.mean(1), mf.std(1)]))
    X = np.asarray(F, dtype=float)
    return (X - X.mean(0)) / (X.std(0) + 1e-9)

def _silhouette(X, lab):
    import numpy as np
    from scipy.spatial.distance import squareform, pdist
    D = squareform(pdist(X))
    ks = np.unique(lab)
    if len(ks) < 2: return -1.0
    sc = []
    for i in range(len(X)):
        same = (lab == lab[i]); same[i] = False
        if same.sum() == 0: continue
        a = D[i][same].mean()
        b = min(D[i][lab == k].mean() for k in ks if k != lab[i])
        sc.append((b - a) / max(a, b))
    return float(np.mean(sc)) if sc else -1.0

_LAST = {}   # 直近のローカル実行の内訳（confidence 等）を diarize() へ渡す

def _local_diarize(wav_path, work_dir, segments=None, kmax=3):
    import numpy as np, librosa
    from scipy.cluster.hierarchy import linkage, fcluster

    y, sr = librosa.load(wav_path, sr=SR, mono=True)
    units = _units(y, sr, segments)
    if not units:
        log("  [local] 発話区間が取れなかった")
        return []
    X = _features(y, sr, units)
    Z = linkage(X, method="ward")

    cand, scores = {}, {}
    for k in range(2, kmax + 1):
        cand[k] = fcluster(Z, k, criterion="maxclust")
        scores[k] = round(_silhouette(X, cand[k]), 4)
    # k=2 を既定とし、上積みが K_MARGIN を超えたときだけ k を増やす。
    # 実測では k=2:0.066 / k=3:0.069 と差が0.003しかなく、素点で選ぶと
    # 2人の対談に3話者を出してしまった。
    k = 2
    for kk in range(3, kmax + 1):
        if scores[kk] - scores[k] > K_MARGIN: k = kk
    lab, conf = cand[k], scores[k]
    log(f"  [local] {len(units)}区間 / シルエット {scores} → k={k} 採用（confidence={conf}）")
    if conf < CONF_MIN:
        log(f"  [local] ★警告 confidence={conf} < {CONF_MIN}。"
            f"クラスタが分離していない＝この話者ラベルは信用できない。")

    _LAST["confidence"], _LAST["k"] = conf, k
    _LAST["silhouette"], _LAST["units"] = scores, len(units)

    # 単発の飛びを均す（前後が同じ話者の**短い**区間はそちらに寄せる）
    # ※ 対象を1.0秒以下に絞る。SPLIT_LEN（2.5秒）にすると全区間が対象になり、
    #   ターンが潰れて情報が減るだけだった。
    if SMOOTH:
        lab = lab.copy()
        for i in range(1, len(lab) - 1):
            if lab[i - 1] == lab[i + 1] != lab[i] and (units[i][1] - units[i][0]) <= 1.0:
                lab[i] = lab[i - 1]

    m = _labels([int(x) for x in lab])
    turns = []
    for (a, b), c in zip(units, lab):
        sp = m[int(c)]
        if turns and turns[-1]["speaker"] == sp and a - turns[-1]["end"] < 0.8:
            turns[-1]["end"] = b
        else:
            turns.append({"start": a, "end": b, "speaker": sp})
    # 極端に短いターンは隣に吸収
    out = []
    for t in turns:
        if out and t["end"] - t["start"] < MIN_TURN and t["start"] - out[-1]["end"] < 0.5:
            out[-1]["end"] = t["end"]
        else:
            out.append(t)
    for t in out:
        t["start"], t["end"] = round(t["start"], 3), round(t["end"], 3)
    return out

# ────────────────────────────────────────────────────────────
# 公開インターフェース
# ────────────────────────────────────────────────────────────
def diarize(wav_path: str, work_dir: str, segments: list = None) -> list:
    """[{"start": float, "end": float, "speaker": "A"|"B"|...}, ...] を返す。
    APIキーがあればAPI、無ければローカル特徴でフォールバック。
    結果は work_dir/diarization.json にも保存する。

    保存するJSONには route と confidence も入れる。ローカル経路で
    low_confidence が true のときは、話者ラベルを成果物に焼き込まないこと。
    """
    os.makedirs(work_dir, exist_ok=True)
    _LAST.clear()
    turns, route = None, "openai:" + API_MODEL
    if os.environ.get("OPENAI_API_KEY") and not os.environ.get("DIARIZE_FORCE_LOCAL"):
        turns = _api_diarize(wav_path, work_dir)
    if not turns:
        route = "local:mfcc20(mean+std)/ward"
        log("  ※ APIを使わない（キー無し or 失敗）→ ローカル特徴クラスタリング")
        turns = _local_diarize(wav_path, work_dir, segments)

    meta = {"route": route, "source": os.path.basename(wav_path),
            "speakers": sorted({t["speaker"] for t in turns}),
            "turns": turns}
    # 【2026-08-02】確信度をローカル経路でしか計算しておらず、API経路は
    #  判定を素通りしていた。実測: API が2人の対話に**9人**・ターン長中央値
    #  **0.30秒**を返し、pipeline はそれを信用して論点見出しを6件→1件に壊した。
    #  真値が無くても効く手掛かりは「話者数」と「ターン長」の2つ。経路によらず測る。
    #  なお API が返すのは**セグメント**であって話者ターンではない。繋いでから測る。
    #  （繋がずに測ると「0.3秒のターンが200件」に見え、原因を読み違える）
    merged, durs = [], []
    for t in turns:
        if merged and merged[-1]["speaker"] == t["speaker"]:
            merged[-1]["end"] = t["end"]
        else:
            merged.append(dict(t))
    durs = sorted(x["end"] - x["start"] for x in merged
                  if "end" in x and "start" in x)
    n_spk = len(meta["speakers"])
    med = durs[len(durs) // 2] if durs else 0.0
    meta["turn_count"] = len(merged)
    # 対話は2〜3人。ターンは秒単位。どちらも【導出値・要実測】
    # bool() で包む。numpy 由来の真偽値だと json.dump が落ちる（実測でローカル経路が
    # クラッシュした。API経路では float が素の Python 型なので表に出なかった）
    implausible = bool(n_spk > MAX_SPEAKERS or med < MIN_TURN_SEC)
    meta["speaker_count"] = n_spk
    meta["median_turn_sec"] = round(float(med), 3)
    meta["implausible"] = implausible
    if route.startswith("local"):
        meta["confidence"] = _LAST.get("confidence")
        meta["silhouette_by_k"] = _LAST.get("silhouette")
        meta["low_confidence"] = bool((_LAST.get("confidence") or 0) < CONF_MIN or implausible)
    else:
        # API は確信度を返さない。**「不明」は「高い」ではない**ので、
        # 妥当性検査だけで判定する。
        meta["confidence"] = None
        meta["low_confidence"] = implausible
    json.dump(meta, open(os.path.join(work_dir, "diarization.json"), "w"),
              ensure_ascii=False, indent=1)
    return turns

def assign_speakers(caps: list, turns: list) -> list:
    """各字幕に "speaker" キーを付けて返す。
    重なりが最大の話者を採用し、どれとも重ならなければ直前の話者を引き継ぐ。"""
    out, prev = [], None
    default = turns[0]["speaker"] if turns else "A"
    for c in caps:
        acc = {}
        for t in turns:
            ov = min(c["end"], t["end"]) - max(c["start"], t["start"])
            if ov > 0: acc[t["speaker"]] = acc.get(t["speaker"], 0.0) + ov
        sp = max(acc, key=acc.get) if acc else (prev or default)
        prev = sp
        out.append({**c, "speaker": sp})
    return out

# ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--work", default=None)
    ap.add_argument("--caps", default=None, help="字幕JSON（[{start,end,text}]）")
    ap.add_argument("--asr", default=None, help="ASR JSON（segments を解析単位に使う）")
    ap.add_argument("--force-local", action="store_true")
    a = ap.parse_args()
    if a.force_local: os.environ["DIARIZE_FORCE_LOCAL"] = "1"
    wd = a.work or os.path.dirname(os.path.abspath(a.wav))

    segs = None
    if a.asr and os.path.exists(a.asr):
        segs = json.load(open(a.asr)).get("segments")

    turns = diarize(a.wav, wd, segs)
    tot = {}
    for t in turns: tot[t["speaker"]] = tot.get(t["speaker"], 0.0) + t["end"] - t["start"]
    log(f"話者 {len(tot)}人 / ターン {len(turns)}")
    for sp in sorted(tot): log(f"  {sp}: {tot[sp]:6.2f}秒 ({sum(1 for t in turns if t['speaker']==sp)}ターン)")

    if a.caps and os.path.exists(a.caps):
        caps = assign_speakers(json.load(open(a.caps)), turns)
        json.dump(caps, open(os.path.join(wd, "captions_spk.json"), "w"),
                  ensure_ascii=False, indent=1)
        for c in caps:
            log(f"  {c['start']:6.2f}-{c['end']:6.2f}  {c['speaker']}  {c['text']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
