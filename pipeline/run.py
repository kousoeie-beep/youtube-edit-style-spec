#!/usr/bin/env python3
"""素材1本 → 公開できる動画一式。

  macOS: uv run --with pillow --with fugashi --with unidic-lite --with mlx-whisper \
     python3 run.py <素材> [--out DIR]
  Linux: uv run --with pillow --with fugashi --with unidic-lite --with openai-whisper \
     python3 run.py <素材> [--out DIR]

2026-08-01 に実素材で確立した工程を1コマンドにまとめたもの。
毎回その場で組み立てると、有効工程10分の作業に50分かかる（実測）。
"""
import argparse, json, os, re, shutil, subprocess, sys, time

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)
def sh(cmd, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)
    return r

def select_video_encoder(encoders, platform_name=None):
    """Return a usable H.264 encoder without assuming the host is macOS."""
    platform_name = platform_name or sys.platform
    names = {line.split()[1] for line in encoders.splitlines() if len(line.split()) > 1}
    if platform_name == "darwin" and "h264_videotoolbox" in names:
        return "h264_videotoolbox"
    if "libx264" in names:
        return "libx264"
    raise RuntimeError("No supported H.264 encoder found (need h264_videotoolbox or libx264)")

def select_asr_backend(platform_name=None):
    """Choose Metal MLX on macOS and portable openai-whisper elsewhere."""
    forced = os.environ.get("YOUTUBE_EDIT_ASR_BACKEND")
    if forced:
        if forced not in {"mlx", "whisper"}:
            raise ValueError("YOUTUBE_EDIT_ASR_BACKEND must be 'mlx' or 'whisper'")
        return forced
    return "mlx" if (platform_name or sys.platform) == "darwin" else "whisper"

def find_japanese_font(candidates=None):
    candidates = candidates or (
        os.path.expanduser("~/Library/Fonts/NotoSansJP-Bold.ttf"),
        "/Library/Fonts/NotoSansJP-Bold.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        os.path.expanduser("~/.local/share/fonts/NotoSansCJK-Bold.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansJP-Bold.ttf",
    )
    return next((path for path in candidates if os.path.exists(path)), None)

_WHISPER_MODEL = None

def local_transcribe(path):
    backend = select_asr_backend()
    if backend == "mlx":
        import mlx_whisper
        return mlx_whisper.transcribe(
            path,
            path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="ja", word_timestamps=True, verbose=False,
        )
    import whisper
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = whisper.load_model(os.environ.get("YOUTUBE_EDIT_WHISPER_MODEL", "turbo"))
    return _WHISPER_MODEL.transcribe(
        path, language="ja", word_timestamps=True, verbose=False
    )

# ── 0. プリフライト（環境・回転・音量）
def preflight(src):
    def probe(a):
        return sh(["ffprobe","-v","error","-select_streams","v:0",
                   "-show_entries",a,"-of","default=nw=1:nk=1",src]).stdout.strip()
    w, h = int(probe("stream=width")), int(probe("stream=height"))
    rot = (probe("stream_side_data=rotation") or "0").splitlines()[0]
    r = abs(int(float(rot))) % 180
    W, H = (h, w) if r == 90 else (w, h)          # ★回転後がキャンバス
    dur = float(sh(["ffprobe","-v","error","-show_entries","format=duration",
                    "-of","csv=p=0",src]).stdout.strip())
    # 【2026-08-01】全編スキャンは72秒かかる。プリフライトは「2パスloudnormが要るか」の
    # 判定にしか使わないので、先頭60秒のサンプルで足りる（本測定はレンダリング時に行う）。
    a = sh(["ffmpeg","-hide_banner","-t","60","-i",src,"-af","ebur128=peak=true",
            "-f","null","-"]).stderr
    I = next((float(l.split()[-2]) for l in a.splitlines() if l.strip().startswith("I:")), -23.0)
    filters = sh(["ffmpeg","-hide_banner","-filters"]).stdout
    has = lambda n: any(len(l.split())>1 and l.split()[1]==n for l in filters.splitlines())
    enc = sh(["ffmpeg","-hide_banner","-encoders"]).stdout
    encoder = select_video_encoder(enc)
    return {"W":W,"H":H,"rot":rot,"dur":dur,"I":I,"created":_creation_time(src),
            "src":src,                     # 画面占有率の測定に使う。
            "portrait":H>W, "drawtext":has("drawtext"),
            "video_encoder":encoder, "hwenc":encoder == "h264_videotoolbox"}

# ── 1. 音声抽出＋強調
def audio(src, wd):
    sh(["ffmpeg","-v","error","-y","-i",src,"-vn","-ac","1","-ar","16000",
        "-c:a","pcm_s16le",f"{wd}/a16k.wav"])
    sh(["ffmpeg","-v","error","-y","-i",f"{wd}/a16k.wav","-af",
        "highpass=f=90,afftdn=nf=-28,loudnorm=I=-14:TP=-1.5,acompressor=threshold=-24dB:ratio=3",
        f"{wd}/a_enh.wav"])

# ── 2. ASR
#  振り分け（captions-quality-v2「ASRの振り分け」）:
#    テキストの正解 = gpt-4o-transcribe（最良。ただし単語タイムスタンプを持たない）
#    語ごとの時刻   = ローカル large-v3-turbo（mlx＝Metal。CPU版は7倍遅い）
#    多数決のパス   = ローカル2 + whisper-1（実測で一致率 +6.75pt）
#  APIキーは **os.environ から取るだけ**。ファイルは読まない。
#  キーが無ければローカル2パスのみで完動する。
# 【2026-08-01 差し替え】gpt-4o-transcribe → **gpt-transcribe**。
#  公式ドキュメントで確認した差:
#    gpt-transcribe    : keywords[] **対応**・languages[]・$0.0045/分 ← 最良かつ最安
#    gpt-4o-transcribe : keywords **非対応**・$0.006/分
#    whisper-1         : timestamp_granularities が使える**唯一**のモデル
#  ⇒ テキストの正解は gpt-transcribe、単語時刻は whisper-1 とローカルから取る。
# 【2026-08-01】素材のスライドから実際に読み取った用語。推測ではない。
# gpt-transcribe の keywords[] に渡すと、訛り・専門用語・騒音下の精度が上がる。
# ★素材ごとに入れ替えること（--keywords で上書きできる）
KEYWORDS = [
    "条件確認トーク", "売らない営業術", "パワーバランスの転換",
    "ワンシート", "NotebookLM", "営業", "成約", "研修",
]

def _api_transcribe(wav, model, want_words=False, keywords=None):
    key = os.environ.get("OPENAI_API_KEY")
    if not key: return None
    mp3 = wav.replace(".wav", ".mp3")
    if not os.path.exists(mp3):
        sh(["ffmpeg","-v","error","-y","-i",wav,"-b:a","64k",mp3])   # 25MB上限対策
    cmd = ["curl","-sS","-X","POST","https://api.openai.com/v1/audio/transcriptions",
           "-H",f"Authorization: Bearer {key}",
           "-F",f"file=@{mp3}","-F",f"model={model}"]
    if model == "gpt-transcribe":
        cmd += ["-F","languages[]=ja"]
        for k in (keywords or []): cmd += ["-F",f"keywords[]={k}"]
    else:
        cmd += ["-F","language=ja"]
    if want_words:
        cmd += ["-F","response_format=verbose_json",
                "-F","timestamp_granularities[]=word"]   # whisper-1 のみ有効
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        log(f"  API[{model}] 失敗: {r.stdout[:160]}")
        return None

def asr(wd):
    out = {}
    for tag, f in (("enh", "a_enh.wav"), ("raw", "a16k.wav")):
        p = f"{wd}/asr_{tag}.json"
        if not os.path.exists(p):
            r = local_transcribe(f"{wd}/{f}")
            json.dump(r, open(p,"w"), ensure_ascii=False)
        out[tag] = json.load(open(p))
        log(f"  ASR[{tag}] {len(out[tag]['segments'])}セグ（ローカル）")

    # キーが無くても、以前の実行でAPI結果が残っていれば再利用する。
    # キーはこの版では一度もファイルから読まない（環境変数だけ）。
    _cached = any(os.path.exists(f"{wd}/asr_{t}.json") for t in ("w1", "gptx"))
    if os.environ.get("OPENAI_API_KEY") or _cached:
        # ③ whisper-1: 単語タイムスタンプを持つので多数決に入れられる
        p = f"{wd}/asr_w1.json"
        if not os.path.exists(p):
            r = _api_transcribe(f"{wd}/a16k.wav", "whisper-1", True)
            if r: json.dump(r, open(p,"w"), ensure_ascii=False)
        if os.path.exists(p):
            d = json.load(open(p))
            out["w1"] = {"segments":[{"words":d.get("words",[])}]}
            log(f"  ASR[w1] {len(d.get('words',[]))}語（API・多数決用）")
        # ④ gpt-4o-transcribe: テキストの正解。時刻は持たない
        p = f"{wd}/asr_gptx.json"
        if not os.path.exists(p):
            r = _api_transcribe(f"{wd}/a16k.wav", "gpt-transcribe", False, KEYWORDS)
            if r: json.dump(r, open(p,"w"), ensure_ascii=False)
        if os.path.exists(p):
            out["_text"] = json.load(open(p)).get("text","")
            log(f"  ASR[gpt-transcribe] {len(out['_text'])}字（API・テキストの正解・keywords={len(KEYWORDS)}語）")
    else:
        log("  ※ OPENAI_API_KEY 無し → ローカル2パスのみ（一致率が約7pt低い）")
    return out

# ── 3. 多数決照合
#  【2026-08-01 再設計】旧実装は低確信語を1文字単位で落とし、**単語を壊した**
#  （ワ|ザマシン → 「ザマシン」、影|響手法 → 「響手法」、ポ|ンと → 「ンと」）。
#  規約「不確信語は省略」は**語として落とす**意味であり、断片を抜くことではない。
#  → 1〜2文字の断片は落とさない（周囲と結合して意味を成すため）。
#    落とすのは「3文字以上・かつ全パス不一致・かつ低確信」だけにする。
LAUGH = re.compile(r"^[ふフはハへヘえエあアうウ]{4,}[。、！!]*$")
FILLER = {"えー","えーと","あのー","そのー","まあ","ま","んー","えっと"}

def reconcile(P, wd):
    import unicodedata
    nz = lambda t: re.sub(r"[、。，．！？!?\s]", "", unicodedata.normalize("NFKC", t).strip())
    W_ = {k: [w for s in v["segments"] for w in s.get("words",[])]
           for k,v in P.items() if not k.startswith("_")}
    # 【2026-08-01】基準を "enh" に固定していたのが誤り。強調（loudnorm+acompressor）は
    #  小さい発話の後半を壊し、whisperがそこで反復幻覚を起こす（実測: 109-134秒が「セ」）。
    #  **壊れている側を基準にすると、生側が正しく聞き取った45秒がまるごと消える。**
    #  基準は「反復幻覚を除いたあとの発話時間が長い方」を選び、
    #  それでも空いている区間はもう一方から補う。
    for k in W_:
        W_[k] = drop_repetition(W_[k], key="word")
    cov = {k: sum(w["end"] - w["start"] for w in v) for k, v in W_.items()}
    bk = max(cov, key=cov.get)
    base = sorted(W_[bk], key=lambda w: w["start"])
    log(f"  照合の基準 = {bk}（発話時間 " +
        " / ".join(f"{k}:{v:.0f}s" for k, v in sorted(cov.items())) + "）")
    # 基準に1.5秒以上の空白があり、他方に語があれば挿し込む
    other = [w for k, v in W_.items() if k != bk for w in v]
    filled, t = [], 0.0
    for i, w in enumerate(base):
        prev = base[i-1]["end"] if i else 0.0
        if w["start"] - prev >= 1.5:
            ins = [x for x in other if prev + 0.05 <= x["start"] and x["end"] <= w["start"] - 0.05]
            if ins: filled += sorted(ins, key=lambda x: x["start"]); t += len(ins)
        filled.append(w)
    tail = base[-1]["end"] if base else 0.0
    ins = [x for x in other if x["start"] > tail + 0.05]
    if ins: filled += sorted(ins, key=lambda x: x["start"]); t += len(ins)
    if t: log(f"  基準の空白を他パスから補完: {int(t)}語")
    base, adopted, omitted = filled, [], []
    for w in base:
        t = nz(w["word"])
        if not t: continue
        if LAUGH.match(t) or t in FILLER:        # 笑い声・フィラーは字幕にしない
            omitted.append({"w":w["word"],"start":round(w["start"],2),"why":"laugh/filler"})
            continue
        votes = 1 + sum(any(nz(x["word"])==t and abs(x["start"]-w["start"])<0.35
                            for x in W_[k]) for k in W_ if k != bk)
        p = w.get("probability",1)
        drop = (votes < 2 and p < 0.45 and len(t) >= 3)   # ★3文字未満は落とさない
        if drop:
            omitted.append({"w":w["word"],"start":round(w["start"],2),"p":round(p,3),"why":"low"})
        else:
            adopted.append({"w":w["word"],"start":w["start"],"end":w["end"]})
    json.dump(omitted, open(f"{wd}/uncertain_terms.json","w"), ensure_ascii=False, indent=1)
    log(f"  照合 採用{len(adopted)} / 省略{len(omitted)}"
        f"（笑い/フィラー{sum(1 for o in omitted if o.get('why')=='laugh/filler')}）")
    return adopted

# ── 3.5 APIテキストをローカルの時刻に載せる
#  【2026-08-01・実装漏れの修正】gpt-transcribe のテキストを取得していたのに
#  **一度も使っていなかった**。「テキストはAPI・時刻はローカル」という振り分けは、
#  この対応付けを書いて初めて成立する。
#  APIテキストは句読点を持つ（文の切れ目が分かる）が時刻を持たない。
#  ローカルは時刻を持つが句読点が無い。**文字単位で対応付けて両取りする。**
# 【2026-08-01】whisperは非音声区間で同じ字を延々と繰り返す（実測: 109-134秒に
#  「セ」が107語）。これを残すと、APIテキストとの文字対応が末尾で総崩れになり、
#  45秒ぶんの字幕が3.5秒に潰れる。**照合の前に落とす。**
def drop_repetition(words, run=4, key="w"):
    out, i = [], 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and words[j+1][key].strip() == words[i][key].strip():
            j += 1
        n = j - i + 1
        if n >= run and len(words[i][key].strip()) <= 2:
            i = j + 1; continue          # 同じ短い字がrun回以上続く＝幻覚
        out += words[i:j+1]; i = j + 1
    if len(out) != len(words):
        log(f"  反復幻覚を除去: {len(words)-len(out)}語")
    return out

def align_api_text(api_text, words):
    import difflib, unicodedata
    if not api_text or not words: return None
    PUNCT = "、。，．！？!?・…「」『』（）"
    loc = "".join(w["w"].strip() for w in words)
    # ローカル文字 → 時刻 の表
    tl = []
    for w in words:
        t = w["w"].strip()
        if not t: continue
        d = (w["end"] - w["start"]) / len(t)
        tl += [(w["start"] + d*i, w["start"] + d*(i+1)) for i in range(len(t))]
    api = unicodedata.normalize("NFKC", api_text).strip()
    api_np = "".join(c for c in api if c not in PUNCT)       # 句読点を抜いて突合
    keep = [i for i, c in enumerate(api) if c not in PUNCT]
    sm = difflib.SequenceMatcher(None, api_np, loc, autojunk=False)
    out = [None]*len(api_np)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            if b+k < len(tl): out[a+k] = tl[b+k]
    # 未対応の文字は前後から線形に埋める
    cov = sum(1 for x in out if x) / max(1, len(out))   # ★埋める前に測る
    last = next((x for x in out if x), (0.0, 0.1))
    for i, v in enumerate(out):
        if v is None: out[i] = last
        else: last = v
    res = []
    for j, ci in enumerate(keep):
        st, en = out[j]
        res.append({"c": api[ci], "s": st, "e": en})
        # 直後の句読点も同じ時刻に付ける（文末判定に使う）
        k = ci + 1
        while k < len(api) and api[k] in PUNCT:
            res.append({"c": api[k], "s": st, "e": en}); k += 1
    log(f"  APIテキストを時刻に対応付け: {len(res)}字 / 一致被覆 {cov:.1%}")
    return res

# ── 4. 文節チャンク（語境界では割れる。自立語＋付属語で切る）
def chunk(words, maxc=16, gap=0.8, chars=None):
    from fugashi import Tagger
    tg = Tagger()
    if chars is None:                       # APIテキストが無ければ従来どおり
        chars = []
        for w in words:
            t = w["w"].strip()
            if not t: continue
            d = (w["end"]-w["start"])/len(t)
            chars += [{"c":c,"s":w["start"]+d*i,"e":w["start"]+d*(i+1)} for i,c in enumerate(t)]
    full = "".join(c["c"] for c in chars)
    morphs, pos = [], 0
    for m in tg(full):
        sf = m.surface
        if not sf: continue
        seg = chars[pos:pos+len(sf)]
        if not seg: break
        morphs.append({"t":sf,"s":seg[0]["s"],"e":seg[-1]["e"],"pos":m.feature.pos1}); pos += len(sf)
    ATTACH = {"助詞","助動詞","接尾辞","補助記号"}
    B, cur = [], []
    for m in morphs:
        if not cur: cur=[m]; continue
        p = cur[-1]["pos"]
        # 【2026-08-01】接頭辞を「前にくっつける」と書いていたのが誤り。
        #  接頭辞は**後ろ**に付く。「してないです。」に「今」が吸着し、そこから
        #  「動画」まで連鎖して、2つの文が1つの字幕に入っていた。
        #  句点のあとも必ず切る（文末は最強の改行位置）。
        after_end = cur[-1]["t"] and cur[-1]["t"][-1] in "。！？!?"
        if not after_end and (m["pos"] in ATTACH
            or (m["pos"]=="名詞" and p in ("名詞","接頭辞","接尾辞"))
            or (m["pos"]=="動詞" and p in ("動詞","助詞")
                and cur[-1]["t"].endswith(("って","て","で","と"))
                and m["t"].startswith(("いう","いく","くる","みる","おく","しまう",
                                       "ある","いる","もらう","くれる","あげる")))):
            cur.append(m)
        else: B.append(cur); cur=[m]
    if cur: B.append(cur)
    B = [{"t":"".join(x["t"] for x in b),"s":b[0]["s"],"e":b[-1]["e"]} for b in B]
    # 【2026-08-01 再設計】文の途中で切れる問題への対処。
    # 日本語ASRは句点を出さないので、**文末になりうる語形**を広く取り、
    # 「そこで切れるなら多少短くても切る」方針にする（読みやすさ優先）。
    END = ("です","ます","ですね","ますね","でした","ました","ですよ","ますよ",
           "ですか","ますか","ですが","ますが","でしょう","ましょう","ください",
           "だよ","だね","かな","のか","んです","んですね","んですよ","ないです",
           "ありますか","できます","します","なります","思います")
    MINC = 5                       # これ未満なら切らずに繋ぐ
    out, cur = [], []
    for b in B:
        if cur:
            n = sum(len(x["t"]) for x in cur)
            gap_break = b["s"] - cur[-1]["e"] >= gap
            len_break = n + len(b["t"]) > maxc
            punct_break = cur[-1]["t"].endswith(("。","？","！","?","!"))   # ★APIの句読点が最優先
            end_break = punct_break or (cur[-1]["t"].endswith(END) and n >= MINC)
            if gap_break or len_break or end_break:
                out.append(cur); cur=[]
        cur.append(b)
    if cur: out.append(cur)
    caps=[]
    for c in out:
        t="".join(x["t"] for x in c)
        if len(t)<3: continue
        st, en = round(c[0]["s"],2), round(c[-1]["e"],2)
        # 【2026-08-01】表示時間のガード。旧実装は16.4秒出っぱなしや
        # start==end（0秒表示）を素通りさせていた
        dur = en - st
        if dur <= 0.05: en = st + max(0.9, len(t)*0.09)      # 潰れは最低表示に伸ばす
        elif dur > 6.0: en = st + 6.0                         # 出っぱなしは6秒で切る
        elif dur < 0.6: en = st + 0.6
        caps.append({"start":st,"end":round(en,2),"text":t})
    # 重なりを解消（後勝ちで前を切り詰める）
    for i in range(len(caps)-1):
        if caps[i]["end"] > caps[i+1]["start"]:
            caps[i]["end"] = round(max(caps[i]["start"]+0.4, caps[i+1]["start"]-0.05),2)
    return caps

# ── 5. 字幕連番PNG（回転後キャンバス・4辺検算つき）
# 【2026-08-01】話者ごとにテロップの種類を分ける。判定の出どころは2つある。
#  ①話者分離が信用できるとき: 疑問文を多く出した側を「聞き手」とし、
#    **その話者の発話すべて**を question にする（＝本当の話者分離）
#  ②信用できないとき: 発話ごとに疑問文かどうかで振る（＝話法の区別であって話者IDではない）
#  どちらを使ったかは必ずログに出す。混ぜて「話者を分けた」と言わない。
QMARK = ("?", "？")
QTAIL = ("ですか", "ますか", "でしょうか", "ますかね", "ですかね",
         "してほしいです", "教えてほしいです", "ください")

def is_question(t):
    t = t.rstrip("。、 ")
    return t.endswith(QMARK) or t.endswith(QTAIL)

def mark_kinds(caps, spk_ok):
    if spk_ok:
        cnt = {}
        for c in caps:
            k = c.get("speaker")
            if k is None: continue
            cnt.setdefault(k, [0, 0])
            cnt[k][0] += is_question(c["text"]); cnt[k][1] += 1
        if len(cnt) >= 2:
            asker = max(cnt, key=lambda k: cnt[k][0] / max(1, cnt[k][1]))
            for c in caps:
                c["kind"] = "question" if c.get("speaker") == asker else "answer"
            log(f"  テロップ種別: 話者分離ベース（聞き手={asker}, "
                f"疑問文率 {cnt[asker][0]}/{cnt[asker][1]}）")
            return caps
    n = 0
    for c in caps:
        c["kind"] = "question" if is_question(c["text"]) else "answer"
        n += c["kind"] == "question"
    log(f"  テロップ種別: **話法ベース**（疑問文 {n}/{len(caps)}件）"
        f"— 話者分離が信用できないため、話者IDではなく質問/回答で分けている")
    return caps

# 話者ごとのテロップ色。対談・インタビューでは実際に使われる区別方法で、
# 位置を変えるより安全（位置を動かすと4辺検算とすきま検算がやり直しになる）。
SPK_FILL = [(255,255,255,255), (255,224,138,255), (168,230,255,255)]

def apply_headlines(items, path):
    """論点見出しを外から差し替える。

    自動生成は「話題を開いた質問文をそのまま」使う（発明しないため）。
    ただし見出しとしては長い。要約は**規則ではなく人（またはLLM）が書く**もので、
    ここはその受け口。JSON は {"0": "何を作ったのか", ...} の形（キーは話題の順番）。

    差し替えた見出しは元の質問文と時刻が変わらない。**話題の切れ目そのものは
    素材から取ったまま**で、文言だけを差し替える。
    """
    if not path or not os.path.exists(path):
        return items, 0
    over = json.load(open(path, encoding="utf-8"))
    n = 0
    out = []
    for i, it in enumerate(items):
        t = over.get(str(i))
        if t:
            it = dict(it, text=t, headline_source="override")
            n += 1
        out.append(it)
    return out, n


def topic_items(caps, dur):
    """論点見出しの帯に流す項目を作る。

    kirinuki の title_bar は「現在の論点を要約した一言を常時表示、話題転換で差し替え」。
    インタビューでは**質問が話題の切れ目**なので、そこを根拠に区切る。

    2026-08-01: 見出しを機械的に書き換える案は捨てた。
    「今後どういったところに使えそうなイメージありますか?」を規則で縮めると
    日本語として不自然になる。**話題を開いた質問文をそのまま使い、帯の幅に
    文節単位で収める**（発明せず素材に根拠を置く）。要約は人が上書きできる。
    """
    # 【2026-08-01】質問の**字幕**を見出しにしたら断片ばかりになった
    #  （「教えてほしいです」「できるんですか?」）。字幕は16字前後で切っているので、
    #  1つの質問文が複数の字幕に散る。**文に組み直してから**話題の切れ目を取る。
    sents, cur = [], []
    for c in caps:
        cur.append(c)
        if c["text"].rstrip().endswith(("。", "？", "?", "！", "!")):
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    # 見出しの先頭に来るフィラーを落とす（話題名として意味を持たない）
    HEAD_FILLER = ("だから、", "で、", "えー", "あの", "まあ", "ちょっと",
                   "なんか", "それで、", "というか", "あ、", "はい、")
    MIN_TOPIC = 10          # これ未満は相槌的な短い問い返し。話題の切れ目にしない
    qs = []
    for sn in sents:
        t = "".join(x["text"] for x in sn)
        if not any(x.get("kind") == "question" for x in sn):
            continue
        t = t.rstrip("。")
        changed = True
        while changed:
            changed = False
            for h in HEAD_FILLER:
                if t.startswith(h):
                    t = t[len(h):]; changed = True
        if len(t) < MIN_TOPIC:
            continue        # 「できるんですか?」のような問い返しは話題を作らない
        # 【2026-08-01】帯の開始は質問が**終わってから**。
        #  environment-notes「同時刻の通常字幕と同一文言の復唱禁止」。
        #  質問を喋っている間は質問テロップが同じ文言を出しているので、
        #  帯にも同時に出すと画面に同じ文が2つ並ぶ（実際に v11 で並んだ）。
        qs.append({"text": t, "start": sn[-1]["end"] + 0.12,
                   "topic_start": sn[0]["start"]})
    if not qs:
        return []
    out = []
    for i, q in enumerate(qs):
        # 次の話題は「次の質問が始まる時刻」で終わる（帯の重なりを作らない）
        end = qs[i + 1]["topic_start"] if i + 1 < len(qs) else dur
        if end - q["start"] < 1.0:
            continue                      # 出ている時間が1秒未満の帯は出さない
        out.append({"text": q["text"], "start": q["start"], "end": end})
    # 最初の質問より前は見出しを出さない（根拠が無い区間に見出しを立てない）
    return out


def keyword_items(caps, topics, dur):
    """強調テロップに出す語を選ぶ。

    規則は environment-notes L17 に既にある（実装が無かっただけ）:
      ・重要語・固有名詞・意外な主張のみ
      ・1トピック1回
      ・**同時刻の通常字幕と同一文言の復唱禁止**（字幕の消滅実時刻 +0.12s 以降に配置）

    2026-08-01: 候補を素直に「名詞」で取ると デザイン/モデル/ページ のような
    一般語が並ぶ。**それは「重要語」ではない。**
    製品名・固有名詞（英数字トークン）と、素材ごとに与えた KEYWORDS に絞る。
    """
    from fugashi import Tagger
    tg = Tagger()
    kw = {k for k in KEYWORDS}
    cand = []
    for c in caps:
        for m in tg(c["text"]):
            t = m.surface
            if (m.feature.pos1 or "") != "名詞":
                continue
            ok = (t.isascii() and t.isalnum() and len(t) >= 3) or t in kw
            if ok:
                cand.append({"text": t, "cap": c})
    # 【2026-08-01】最初に出てきた語を採ると、その話題で一番大事な語が落ちる
    #  （「営業」が先に出て NotebookLM が消えた）。**話題ごとに最も固有性の高い語**を採る。
    #  優先度: 製品名・英数字の固有名詞 > 与えられたキーワード > 長い語
    by_topic = {}
    for x in cand:
        tp = next((i for i, it in enumerate(topics)
                   if it["start"] <= x["cap"]["start"] < it["end"]), -1)
        by_topic.setdefault(tp, []).append(x)

    def rank(x):
        t = x["text"]
        return (0 if (t.isascii() and t.isalnum()) else 1, -len(t))

    used, out = set(), []
    for tp in sorted(by_topic):
        for x in sorted(by_topic[tp], key=rank):
            if x["text"] in used:
                continue                  # 1本につき1回（同語の繰り返しを出さない）
            st = x["cap"]["end"] + 0.12   # 字幕が消えてから出す（復唱にしない）
            en = min(dur, st + 2.0)
            if en - st < 0.6:
                continue
            used.add(x["text"])
            out.append({"text": x["text"], "start": round(st, 2),
                        "end": round(en, 2), "_topic": tp})
            break                          # 1トピック1回
    return sorted(out, key=lambda o: o["start"])


def timestamp_items(env, topics, dur):
    """撮影時刻のピルに出す項目。

    japan_vlog の timestamp_pill は「半透明グレーの小型ピルで時刻を提示（AM:9:30）。
    朝パートで断続的に出現」。**時刻は素材のメタデータから導ける**（発明しない）。
    出すのは話題の頭だけにする（常時出すと実物と違う＝「断続的」の再現にならない）。
    """
    base = env.get("created")
    if not base:
        return []
    import datetime
    out, last = [], None
    for it in topics:
        t = base + datetime.timedelta(seconds=it["start"])
        ap = "AM" if t.hour < 12 else "PM"
        label = f"{ap}:{t.hour % 12 or 12}:{t.minute:02d}"
        if label == last:
            continue          # 【2026-08-02】分が変わっていないのに同じ時刻を出さない
        last = label
        out.append({"text": label,
                    "start": it["start"], "end": min(dur, it["start"] + 3.0)})
    return out


def shape_items(style, role_name, dur):
    """図形だけの役（レターボックスの黒帯など）を全編に敷く指示を返す。

    japan_vlog の letterbox は「2.35:1シネマ枠の上下黒帯」。
    文字を持たないので**矩形をそのまま塗る**。導出でも発明でもなく幾何そのもの。
    """
    rs = [r for r in style["roles"] if r["role"] == role_name and r["resolved"]]
    return [{"box": tuple(round(v) for v in r["rect"]), "start": 0.0, "end": dur}
            for r in rs]


def _creation_time(src):
    """素材の撮影時刻。Apple の quicktime タグを優先（ローカル時刻で入っている）。"""
    import datetime
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        r = sh(["ffprobe","-v","error","-show_entries",f"format_tags={key}",
                "-of","default=nw=1:nk=1", src])
        v = (r.stdout or "").strip()
        if v:
            try:
                return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
    return None


# ── 5.7 自動カット（無音除去）
#  一次指示書 §3「0.45秒以上の無音。ただし感情、考える間、オチ前の間は残す」。
#
#  2026-08-02: 最初は音量（silencedetect / フレームRMS）で無音を探したが、
#  **どのしきい値でも語と58〜75%重なった**。日本語は無声子音や語中の間で
#  エネルギーが落ちるので、低エネルギー区間は「発話の内側」に多い。
#  信号の選び方が誤っていた。**語と語の間隙**で測るのが正しい。
FILL_KEEP = 0.25      # 間を全部詰めると詰まって聞こえる。これだけ残す
CUT_MIN   = 0.45      # 一次指示書の閾値

def find_cuts(words, env, wd, caps=None):
    import soundfile as sf, numpy as np
    y, sr = sf.read(f"{wd}/a16k.wav")
    y = y if y.ndim == 1 else y.mean(1)
    dur = env["dur"]
    lvl = 20*np.log10(np.sqrt((y**2).mean())+1e-9)     # 素材全体の平均音量

    def rms(a, b):
        seg = y[int(a*sr):int(b*sr)]
        return 20*np.log10(np.sqrt((seg**2).mean())+1e-9) if len(seg) else -99

    ws = sorted(words, key=lambda w: w["start"])
    gaps, prev = [], 0.0
    for w in ws:
        if w["start"] - prev >= CUT_MIN:
            gaps.append((prev, w["start"]))
        prev = max(prev, w["end"])
    if dur - prev >= CUT_MIN:
        gaps.append((prev, dur))

    qstarts = [c["start"] for c in (caps or []) if c.get("kind") == "question"]
    cuts = []
    for a, b in gaps:
        r = rms(a, b)
        if r > lvl + 6:
            continue          # 平均より大きい＝笑い声・反応・再生音。**残す**
        if any(0 <= q - b <= 0.5 for q in qstarts):
            continue          # 質問の直前は「考える間」。残す
        room = (b - a) - FILL_KEEP
        if room < 0.2:
            continue
        st = a + FILL_KEEP / 2
        cuts.append({"start": round(st, 3), "end": round(st + room, 3),
                     "reason": "long_silence", "risk": "low",
                     "rms_db": round(float(r), 1)})
    # 【2026-08-02】境界をフレームに合わせる。environment-notes が
    #  「多分割concat＋fps量子化のタイムラインドリフトで90秒地点で最大+0.8sずれる」と
    #  警告している。**丸め方を揃えれば、そもそもずれない。**
    FPS = 30
    for c in cuts:
        c["start"] = round(round(c["start"] * FPS) / FPS, 4)
        c["end"] = round(round(c["end"] * FPS) / FPS, 4)
    cuts = [c for c in cuts if c["end"] - c["start"] >= 1.0 / FPS]
    json.dump(cuts, open(f"{wd}/cuts.json", "w"), ensure_ascii=False, indent=1)
    rm = sum(c["end"] - c["start"] for c in cuts)
    log(f"  カット候補 {len(cuts)}件 / 除去 {rm:.1f}秒（{100*rm/dur:.1f}%）→ 尺 {dur-rm:.1f}秒")
    return cuts


def remap(t, cuts):
    """元の時刻を、カット後の時刻へ写す。カット中の時刻はカット開始点へ寄せる。"""
    off = 0.0
    for c in cuts:
        if t >= c["end"]:
            off += c["end"] - c["start"]
        elif t > c["start"]:
            return round(c["start"] - off, 3)
        else:
            break
    return round(t - off, 3)


# 【2026-08-02】スタイルは entrance/exit を108箇所で宣言しているのに、
#  パイプラインは全部ハードカットで出していた。論点見出しと同じ「宣言はあるが実装が無い」型。
def anim_factor(kind, p, sec, elapsed):
    """アニメーションの (拡大率, 不透明度) を返す。p=0→1 で進行。"""
    if kind == "pop":
        # 0.6 から 1.06 へ行き過ぎて 1.0 に落ち着く（一般的なポップイン）
        if p < 0.7:
            q = p / 0.7
            return 0.6 + 0.46 * (1 - (1 - q) ** 2), min(1.0, p / 0.35)
        q = (p - 0.7) / 0.3
        return 1.06 - 0.06 * q, 1.0
    if kind == "fade":
        return 1.0, p
    return 1.0, 1.0


def phase_of(q, t):
    """その描画行が今どの段階にいるか。(種類, 進行度) を返す。steady は (None, 1.0)。"""
    ein, eout = q.get("entrance"), q.get("exit")
    si, so = q.get("entrance_sec") or 0.0, q.get("exit_sec") or 0.0
    if ein and ein != "hard_cut" and si > 0 and t < q["start"] + si:
        return ein, max(0.0, (t - q["start"]) / si)
    if eout and eout != "hard_cut" and so > 0 and t > q["end"] - so:
        return eout, max(0.0, (q["end"] - t) / so)
    return None, 1.0


# ── 画像挿入（B-roll）
#  スタイル側には画像を置く役が既にある（motion_graphic / app_logo_card /
#  news_citation_card 等）。**役があるならそこへ置く**。無ければ画面内に収める。
#  2026-08-02: 「これ風に作って」で画像まで入るようにするための最小実装。
#  画像そのものは外から与える（動画から導けない情報なので発明しない）。
SCREEN_BUSY = 0.28   # 画面がこの割合を超えて写っていたら重ねない【導出値・要実測】


def screen_box(src, t, wd, W, H):
    """その時刻に「明るい画面」が画角のどこにあるか。(x0,y0,x1,y1) を返す。
    無ければ None。

    【2026-08-02】最初は「画面が画角のどれだけを占めるか」を測って、
    大きければ挿入を見送る実装にした。**測る対象が違った。**
    問題は画面の大きさではなく「挿入枠が画面に重なるか」で、
    占有率10%でもテレビの真上に置けば結果は同じだった。
    """
    try:
        import numpy as np
        from scipy import ndimage
        from PIL import Image
        f = os.path.join(wd, f"_probe_{int(t*10)}.png")
        sh(["ffmpeg", "-v", "error", "-ss", str(t), "-i", src,
            "-frames:v", "1", "-vf", "scale=320:-1", "-y", f])
        g = np.array(Image.open(f).convert("L")); os.remove(f)
        m = g > 195
        lab, n = ndimage.label(m)
        if n == 0:
            return None
        sz = ndimage.sum(m, lab, range(1, n + 1))
        ys, xs = np.nonzero(lab == int(np.argmax(sz)) + 1)
        k = W / g.shape[1]
        # int() で包む。numpy の float が枠に混ざると PIL の paste が落ちる
        # （numpy 真偽値が json.dump を落としたのと同じ型のミス。2度目）
        return (int(xs.min()*k), int(ys.min()*k), int(xs.max()*k), int(ys.max()*k))
    except Exception as e:
        log(f"  ⚠ 画面位置を測れなかった（{type(e).__name__}: {e}）→ そのまま置く")
        return None


def _overlap(a, b):
    """2つの矩形の重なり面積 ÷ a の面積。"""
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1-x0)*(y1-y0) / max(1.0, (a[2]-a[0])*(a[3]-a[1]))


def avoid_screen(box, scr, H, margin=40):
    """挿入枠が画面に重なるなら、重ならない位置へ縦にずらす。
    ずらせなければ元の位置のまま返す（消さない。画像自体には価値がある）。"""
    if not scr or _overlap(box, scr) < OVERLAP_MAX:
        return box, 0.0
    x0, y0, x1, y1 = box
    h = y1 - y0
    for cand in (int(scr[3]) + margin, int(scr[1]) - margin - h):  # 画面の下 → 上
        if margin <= cand and cand + h <= H - margin:
            cand = int(cand); nb = (x0, cand, x1, cand + h)
            if _overlap(nb, scr) < OVERLAP_MAX:
                return nb, _overlap(box, scr)
    return box, _overlap(box, scr)


OVERLAP_MAX = 0.22   # 挿入枠のこの割合を超えて画面に重なったらずらす【導出値・要実測】
COLLIDE_MAX = 0.02   # 要素どうしがこの割合を超えて重なったら報告【導出値・要実測】


def unmap(t, cuts):
    """カット後の時刻 → 元素材の時刻。

    【2026-08-02】capseq に渡る caps は**カット後**の時刻に変換済みなのに、
    それをそのまま**元素材**に当てて画面位置を測っていた。別の瞬間を見ていた
    ことになり、重なり率（38〜51%）も間違ったフレームに対する数字だった。
    """
    if not cuts:
        return t
    s = t
    for c in sorted(cuts, key=lambda c: c["start"]):
        if c["start"] <= s:
            s += c["end"] - c["start"]
        else:
            break
    return s


def broll_items(images, topics, dur, hold=2.5, src=None, wd=None,
                box=None, H=1920, cuts=None, W_CANVAS=1080):
    """画像を話題の頭へ順に割り当てる。画像が足りなければ足りるぶんだけ。
    **置く前にその時刻の画面位置を見て、重なるならずらす。**"""
    if not images or not topics:
        return []
    out, moved = [], []
    for i, it in enumerate(topics):
        if i >= len(images):
            break
        st = it["start"]
        b = box
        if src and wd and box:
            # 元素材の時刻に戻してから測る。W は画布の幅（枠の座標ではない）
            scr = screen_box(src, unmap(st + 0.5, cuts), wd, W_CANVAS, H)
            b, ov = avoid_screen(box, scr, H)
            if b != box:
                moved.append((round(st, 1), round(ov * 100)))
        out.append({"path": images[i], "start": round(st, 2),
                    "end": round(min(dur, st + hold), 2), "box": b})
    if moved:
        log("  画像インサートをずらした: "
            + " / ".join(f"{t}秒（画面と{p}%重なっていた）" for t, p in moved))
    return out


def broll_box(style, canvas_w, canvas_h):
    """画像を置く矩形。スタイルが画像の役を持つならその矩形、無ければ安全枠。"""
    if style:
        for rn in ("motion_graphic", "news_citation_card", "app_logo_card",
                   "glossary_card", "sponsor_embed"):
            r = next((x for x in style["roles"]
                      if x["role"] == rn and x["resolved"]), None)
            if r:
                return tuple(round(v) for v in r["rect"]), rn
    # 役が無いスタイル: 画面幅の 72% を上寄せで置く（字幕と見出しを避ける位置）
    w = int(canvas_w * 0.72); h = int(w * 9 / 16)
    x0 = (canvas_w - w) // 2; y0 = int(canvas_h * 0.28)
    return (x0, y0, x0 + w, y0 + h), None


def capseq(caps, env, wd, style_id=None, spk_ok=False, logo=None, broll=None,
           headlines=None, cuts_=None, topic=True):
    from PIL import Image, ImageDraw, ImageFont
    W,H,FPS = env["W"], env["H"], 30
    fp = find_japanese_font()
    if not fp:
        sys.exit("Noto Sans JP/CJK Bold が見つからない。ユーザーまたはシステムのfontディレクトリに入れてください")

    # ── スタイル定義を使う経路。失敗したら既定レイアウトに落ちる（無音で落ちない）
    plan = None; bar = []; badge = None; shapes = []; pics = []
    if style_id:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import style_apply as SA
            st = SA.load_style(style_id, W, H)
            plan = SA.render_plan(st, caps, W, H, speaker_kinds=True)
            ev = len({p["event_index"] for p in plan})
            log(f"  スタイル {style_id} を適用: {ev}イベント / {len(plan)}行")
            # 常駐の論点見出し帯。**字幕しか描かないのは「スタイルを再現した」とは言えない**
            # 【2026-08-02】Shorts は区間を組み替えるので「現在の話題」が
            #  成立しない。自動検出した見出しを残すと内容とずれる（実測: 30秒
            #  地点で新人研修の話をしているのに見出しは「デザインできるんですか?」）。
            #  **成立しない装置は出さない。**
            items = topic_items(caps, env["dur"]) if topic else []
            items, nov = apply_headlines(items, headlines)
            if nov:
                log(f"  論点見出しを {nov}件 差し替え（--headlines）")
            # 見出し系の役は「現在の話題」を出すという同じ機能なので、
            # どれか1つでも解決していればそこへ流す（別実装を作らない）。
            TOPIC_ROLES = ("title_bar", "chapter_tag", "chapter_tab",
                           "question_tab", "running_outline")
            bar, bar_role = [], None
            for rn in TOPIC_ROLES:
                if not items: break
                r = next((x for x in st["roles"]
                          if x["role"] == rn and x["resolved"] and x.get("font_px")), None)
                if r:
                    bar = SA.render_role(st, rn, items, W, H)
                    if bar: bar_role = rn; break
            if bar:
                log(f"  論点見出し（{bar_role}）{len(items)}件（話題の切れ目＝質問文）")
            elif items:
                log("  ⚠ このスタイルに見出し系の役が無いので論点見出しは出さない")
            # 撮影時刻ピル（メタデータから導出）
            if any(x["role"] == "timestamp_pill" and x["resolved"] and x.get("font_px")
                   for x in st["roles"]):
                ts = timestamp_items(env, items, env["dur"])
                if ts:
                    bar += SA.render_role(st, "timestamp_pill", ts, W, H)
                    log(f"  撮影時刻ピル {len(ts)}件（{env['created']:%Y-%m-%d %H:%M} 起点）")
                else:
                    log("  ⚠ timestamp_pill はあるが素材に撮影時刻が無いので出さない")
            # 質問カード（質問検出から）
            if any(x["role"] == "question_card" and x["resolved"] and x.get("font_px")
                   for x in st["roles"]):
                qc = [{"text": i["text"], "start": i["start"],
                       "end": min(env["dur"], i["start"] + 4.0)} for i in items]
                if qc:
                    bar += SA.render_role(st, "question_card", qc, W, H)
                    log(f"  質問カード {len(qc)}件")
            # 画像挿入（B-roll）。**アニメーションは画像にも掛ける**（fadeで入れる）
            if broll and os.path.isdir(broll):
                imgs = sorted(os.path.join(broll, x) for x in os.listdir(broll)
                              if x.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
                bx, brole = broll_box(st, W, H)
                bi_ = broll_items(imgs, items, env["dur"],
                                  src=env.get("src"), wd=wd, box=bx, H=H,
                                  cuts=cuts_, W_CANVAS=W)
                # 【2026-08-02】PNG1枚ごとに開き直していたので、1000枚超×画像枚数ぶん
                #  ファイルを開き、v17 は UnidentifiedImageError で落ちた。
                #  毎回リサイズとカード生成をやり直してもいた。**1回だけ作る。**
                PAD, RAD = 10, 16
                for b_ in bi_:
                    src_ = Image.open(b_["path"]).convert("RGBA")
                    # ずらした枠は項目ごとに違う。共通の bx を使うと寸法が食い違う
                    _b = b_.get("box") or bx
                    bw_, bh_ = _b[2]-_b[0], _b[3]-_b[1]
                    k_ = min(bw_/src_.width, bh_/src_.height)
                    src_ = src_.resize((max(1,int(src_.width*k_)), max(1,int(src_.height*k_))),
                                       Image.LANCZOS)
                    cw_, ch_ = src_.width + PAD*2, src_.height + PAD*2
                    card = Image.new("RGBA", (cw_+18, ch_+18), (0,0,0,0))
                    cd = ImageDraw.Draw(card)
                    cd.rounded_rectangle([12,12,cw_+12,ch_+12], RAD+2, fill=(0,0,0,90))
                    cd.rounded_rectangle([0,0,cw_,ch_], RAD, fill=(255,255,255,255))
                    m_ = Image.new("L", src_.size, 0)
                    ImageDraw.Draw(m_).rounded_rectangle(
                        [0,0,src_.width,src_.height], RAD-4, fill=255)
                    card.paste(src_, (PAD, PAD), m_)
                    b_["_card"] = card
                    src_.close()
                pics.extend(bi_)
                if pics:
                    log(f"  画像挿入 {len(pics)}枚 / 置き場所 "
                        f"{brole or '安全枠'} {bx}")
                elif imgs:
                    log("  ⚠ 画像はあるが話題が無いので挿入しない")
            # レターボックス（図形。導出でも発明でもなく幾何そのもの）
            for rn in ("letterbox", "letterbox_top", "letterbox_bottom"):
                shapes += shape_items(st, rn, env["dur"])
            if shapes:
                log(f"  レターボックス {len(shapes)}本（全編）")
            # 強調テロップ。選定規則は environment-notes L17 に既にあった（実装が無かった）
            KW_ROLES = ("keyword", "positive_keyword", "shock_keyword", "highlight_marker")
            kws = keyword_items(caps, items, env["dur"]) if items else []
            for rn in KW_ROLES:
                r = next((x for x in st["roles"]
                          if x["role"] == rn and x["resolved"] and x.get("font_px")), None)
                if r and kws:
                    k = SA.render_role(st, rn, kws, W, H)
                    if k:
                        bar += k
                        log(f"  強調テロップ（{rn}）{len(kws)}件"
                            f"：{'・'.join(x['text'] for x in kws)}")
                        break
            # ロゴバッジ。**描けない役を黙って飛ばすと「再現した」が静かに嘘になる**
            npr = next((r for r in st["roles"]
                        if r["role"] == "nameplate" and r["resolved"]), None)
            if npr:
                badge = SA.image_role(st, "nameplate", logo, W, H)
                if not badge:
                    log("  ⚠ nameplate（ロゴバッジ）はこのスタイルの常駐要素だが、"
                        "画像が渡されていないので**描いていない**（--logo で渡せる）")
                elif not os.path.exists(badge["path"]):
                    log(f"  ⚠ ロゴ {badge['path']} が見つからない → nameplate は描かない")
                    badge = None
                else:
                    log(f"  ロゴバッジ {badge['box']} に {os.path.basename(badge['path'])}")
        except Exception as e:
            log(f"  ⚠ スタイル {style_id} は適用できない（{e}）→ 既定レイアウト")
            plan = None

    if plan is None:                       # 既定（スタイル定義なし）
        FS = 72 if env["portrait"] else 92
        ST = 10 if env["portrait"] else 12
        MG, CY = 48, int(H*0.82); LH = int(FS*1.46); MAXW = W-MG*2
        f = ImageFont.truetype(fp, FS)
        d0 = ImageDraw.Draw(Image.new("RGBA",(10,10)))
        tw = lambda t:(lambda b:b[2]-b[0])(d0.textbbox((0,0),t,font=f,stroke_width=ST))
        plan=[]
        for ei,c in enumerate(caps):
            L,ln=[],""
            for ch in c["text"]:
                if tw(ln+ch)>MAXW and ln: L.append(ln); ln=ch
                else: ln+=ch
            if ln: L.append(ln)
            L=L[:2]; y0=CY-LH*len(L)//2
            for k,t in enumerate(L):
                b=d0.textbbox((0,0),t,font=f,stroke_width=ST)
                plan.append({"text":t,"x":(W-(b[2]-b[0]))//2-b[0],"y":y0+LH*k-b[1],
                             "font_px":FS,"stroke_px":ST,"start":c["start"],"end":c["end"],
                             "event_index":ei,
                             "bbox":(( W-(b[2]-b[0]))//2, y0+LH*k,
                                     (W+(b[2]-b[0]))//2, y0+LH*k+LH)})

    # 描画行を「同時に出る1枚」へ束ねる。
    # 常駐帯は字幕と独立に切り替わるので、**(字幕イベント, 帯イベント) の組**でPNGを作る。
    ev = {}
    for q in plan: ev.setdefault(q["event_index"], []).append(q)
    bev = {}
    for q in bar: bev.setdefault(q["event_index"], []).append(q)

    def at(d, t):
        for i, rows in d.items():
            if rows[0]["start"] <= t < max(r["end"] for r in rows): return i
        return -1

    FPS_ = FPS
    n = int(env["dur"] * FPS_) + 1

    def bucket(rows, t):
        """アニメーション中のフレームは1枚ずつ別のPNGにする。steady は 0。"""
        if not rows: return 0
        kind, _ = phase_of(rows[0], t)
        if kind is None: return 0
        if t - rows[0]["start"] < (rows[0].get("entrance_sec") or 0):
            return 1 + int(round((t - rows[0]["start"]) * FPS_))
        return -1 - int(round((rows[0]["end"] - t) * FPS_))

    keys = []
    for fr in range(n):
        t = fr / FPS_
        ci, bi = at(ev, t), at(bev, t)
        pk = tuple(int(round((t - p_["start"]) * FPS_)) if p_["start"] <= t < p_["end"]
                   else -1 for p_ in pics)
        keys.append((ci, bucket(ev.get(ci), t), bi, bucket(bev.get(bi), t), pk))
    uniq = sorted(set(keys))

    fonts = {}
    out=f"{wd}/capseq"; shutil.rmtree(out,ignore_errors=True); os.makedirs(out)
    ng=set(); not_inspected=set(); collide=[]; M={}

    def layer(rows, t):
        """1グループを描いて、宣言どおりのアニメーションを掛けた層を返す。"""
        lay = Image.new("RGBA",(W,H),(0,0,0,0)); dd = ImageDraw.Draw(lay)
        for q in sorted(rows, key=lambda r: r.get("z_order") or 0):
            fk=(q["font_px"],)
            f = fonts.get(fk) or fonts.setdefault(fk, ImageFont.truetype(fp,q["font_px"]))
            st=q["stroke_px"]; x,y=q["x"],q["y"]
            fill = tuple(q.get("fill") or SPK_FILL[0])
            dd.text((x+3,y+3),q["text"],font=f,fill=(0,0,0,115),stroke_width=st,stroke_fill=(0,0,0,115))
            dd.text((x,y),q["text"],font=f,fill=fill,stroke_width=st,stroke_fill=(0,0,0,255))
        kind, prog = phase_of(rows[0], t) if rows else (None, 1.0)
        if kind is None:
            return lay
        sc, al = anim_factor(kind, prog, 0, 0)
        bb = lay.getbbox()
        if bb and abs(sc - 1.0) > 1e-3:
            crop = lay.crop(bb)
            nw, nh = max(1,int(crop.width*sc)), max(1,int(crop.height*sc))
            crop = crop.resize((nw,nh), Image.LANCZOS)
            cx, cy = (bb[0]+bb[2])//2, (bb[1]+bb[3])//2
            lay = Image.new("RGBA",(W,H),(0,0,0,0))
            lay.alpha_composite(crop, (cx-nw//2, cy-nh//2))
        if al < 0.999:
            lay.putalpha(lay.getchannel("A").point(lambda v: int(v*al)))
        return lay

    for key in uniq:
        ci, cph, bi, bph, _pk = key
        t = next(fr for fr, k in enumerate(keys) if k == key) / FPS_
        im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
        parts = {}                      # 要素ごとの層。重なり検査に使う
        for shp in shapes:              # レターボックスは全編・全PNG
            d.rectangle(list(shp["box"]), fill=(0, 0, 0, 255))
        for pc in pics:                 # 挿入画像。字幕より下の層に置く
            if not (pc["start"] <= t < pc["end"]): continue
            card = pc["_card"]          # ★1回だけ作って使い回す
            fadev = 0.35
            al = min(1.0, (t-pc["start"])/fadev, (pc["end"]-t)/fadev, 1.0)
            c2 = card
            if al < 0.999:
                c2 = card.copy()
                c2.putalpha(c2.getchannel("A").point(lambda v: int(v*max(0.0,al))))
            x0,y0,x1,y1 = pc["box"]; bw,bh = x1-x0, y1-y0
            pos = (x0+(bw-c2.width)//2, y0+(bh-c2.height)//2)
            ly = Image.new("RGBA",(W,H),(0,0,0,0)); ly.alpha_composite(c2, pos)
            parts["画像"] = ly
            im.alpha_composite(c2, pos)
        if badge:                       # 常駐なので全PNGに乗せる
            b = Image.open(badge["path"]).convert("RGBA")
            x0, y0, x1, y1 = badge["box"]
            bw, bh = x1 - x0, y1 - y0
            k = min(bw / b.width, bh / b.height)
            b = b.resize((max(1, int(b.width * k)), max(1, int(b.height * k))))
            im.alpha_composite(b, (x0 + (bw - b.width) // 2, y0 + (bh - b.height) // 2))
        for nm, rows in (("字幕", ev.get(ci)), ("見出し", bev.get(bi))):
            if not rows: continue
            ly = layer(rows, t)
            parts[nm] = ly
            im.alpha_composite(ly)
        # 4辺検算。**アニメーション中の行き過ぎ（pop は1.06倍）も含めて見る**
        #  【2026-08-02】帯やロゴがあると検査を丸ごと飛ばすのに「NG 0件」と
        #  報告していた。qc-gates.md が「1つも検査していないのに合格に見える」
        #  偽の安全信号として名指ししている型。**検査不能は合格ではない。**
        if shapes or badge:
            not_inspected.add(key)
        else:
            bb = im.getbbox()
            if bb and (bb[0]-3 < 16 or bb[2]+12 > W-16
                       or bb[1]-3 < 16 or bb[3]+12 > H-16):
                ng.add(key)

        # 【2026-08-02】要素どうしの重なりを一度も見ていなかった。
        #  qc-gates.md の盲点①「同一PNG内部の描画順ミスは原理的に検出できない」
        #  ——レイヤー間QCの話だが、**全部を1枚に描く実装では自分で見るしかない**。
        #  実描画インクの矩形どうしで測る（宣言矩形ではなく実際に塗った範囲）。
        boxes = {k: v.getbbox() for k, v in parts.items() if v.getbbox()}
        names = sorted(boxes)
        for ii in range(len(names)):
            for jj in range(ii + 1, len(names)):
                a, b = boxes[names[ii]], boxes[names[jj]]
                ov = _overlap(a, b)
                if ov > COLLIDE_MAX:
                    collide.append((names[ii], names[jj], round(ov * 100)))
        M[key]=f"{out}/_m{uniq.index(key):04d}.png"; im.save(M[key])
    edge = (f"4辺検算NG {len(ng)}件" if not not_inspected
            else f"4辺検算 **検査不能 {len(not_inspected)}枚**（帯/ロゴあり）")
    log(f"  字幕PNG {len(M)}枚（アニメーション込み）/ 連番{n} / {edge}")
    if collide:
        agg = {}
        for a, b, v in collide:
            k = f"{a}×{b}"; agg[k] = max(agg.get(k, 0), v)
        log("  ⚠ 要素が重なっている: "
            + " / ".join(f"{k} 最大{v}%" for k, v in sorted(agg.items())))
    else:
        log("  要素どうしの重なり: 0件")
    for fr in range(n): os.link(M[keys[fr]], f"{out}/{fr:05d}.png")
    return out, len(ng)

# ── 6. レンダリング（loudnorm 2パス・HWエンコード）
def render(src, seq, env, outp, wd, cuts=None):
    m = sh(["ffmpeg","-hide_banner","-i",src,"-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json","-f","null","-"]).stderr
    j = json.loads(m[m.rfind("{"):m.rfind("}")+1])
    log(f"  loudnorm 1パス目: I={j['input_i']} TP={j['input_tp']}")
    af = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={j['input_i']}:"
          f"measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true,"
          "aresample=192000,alimiter=level=false:limit=-2.5dB,aresample=48000")
    vcodec = ["-c:v","h264_videotoolbox","-b:v","10M"] if env["hwenc"] else \
             ["-c:v","libx264","-preset","medium","-crf","20"]
    # カットは trim/atrim + concat で行う。
    # environment-notes「ffmpeg 8.0.1 の select/aselect は不発」に従い select を使わない。
    if cuts:
        keep, prev = [], 0.0
        for c in cuts:
            if c["start"] > prev: keep.append((prev, c["start"]))
            prev = c["end"]
        if env["dur"] > prev: keep.append((prev, env["dur"]))
        pre = "".join(
            f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}];"
            for i, (a, b) in enumerate(keep))
        cc = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
        base = (pre + f"{cc}concat=n={len(keep)}:v=1:a=1[cv][ca];"
                "[cv]eq=brightness=0.06:contrast=1.12:saturation=1.05,unsharp=5:5:0.4[b];"
                "[b][1:v]overlay=0:0:format=auto[v]")
        # 【2026-08-02】filter_complex の出力ラベルに -af は当てられない
        #  （同じストリームに -af と -filter_complex は併用できない）。
        #  カットするときは音声処理もグラフの中に入れる。
        base += f";[ca]{af}[aout]"
        amap = ["-map","[aout]"]; afopt = []
        log(f"  カット適用: {len(keep)}区間を連結（{len(cuts)}箇所を除去）")
    else:
        base = ("[0:v]eq=brightness=0.06:contrast=1.12:saturation=1.05,"
                "unsharp=5:5:0.4[b];[b][1:v]overlay=0:0:format=auto[v]")
        amap = ["-map","0:a"]; afopt = ["-af", af]
    r = sh(["ffmpeg","-hide_banner","-v","warning","-y","-i",src,
            "-framerate","30","-i",f"{seq}/%05d.png",
            "-filter_complex", base,
            "-map","[v]",*amap,*afopt,
            *vcodec,"-pix_fmt","yuv420p","-r","30",
            "-c:a","aac","-b:a","192k","-movflags","+faststart",outp])
    # 【2026-08-02】戻り値を一度も見ておらず、**失敗しても「完成」と表示していた**。
    #  v14 は final.mp4 が生成されないまま「完成: ...」「4辺検算NG 0件」と出た。
    #  出力が無い／壊れているのに成功と報告するのが、この工程で一番重い欠陥。
    if r.returncode != 0 or not os.path.exists(outp) or os.path.getsize(outp) < 1024:
        log("  ✗ レンダリング失敗:")
        for ln in (r.stderr or "").strip().splitlines()[-12:]:
            log("    " + ln)
        sys.exit("レンダリングに失敗した。上のエラーを見ること")
    return r.returncode

# ── 7. 納品パック
def pack(caps, outp, wd, dist):
    def ts(t,sep=","):
        h=int(t//3600); m=int(t%3600//60); s=t%60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".",sep)
    srt="\n".join(f"{i+1}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n"
                  for i,c in enumerate(caps))
    open(f"{dist}/captions.srt","w").write(srt)
    open(f"{dist}/captions.vtt","w").write("WEBVTT\n\n"+"\n".join(
        f"{ts(c['start'],'.')} --> {ts(c['end'],'.')}\n{c['text']}\n" for c in caps))
    json.dump(caps, open(f"{dist}/transcript.json","w"), ensure_ascii=False, indent=1)
    # QC
    a = sh(["ffmpeg","-hide_banner","-i",outp,"-af","ebur128=peak=true","-f","null","-"]).stderr
    I = next((l.split()[-2] for l in a.splitlines() if l.strip().startswith("I:")), "?")
    TP= next((l.split()[-2] for l in a.splitlines() if l.strip().startswith("Peak:")), "?")
    blk = sh(["ffmpeg","-hide_banner","-i",outp,"-vf",
              "blackdetect=d=0.2:pic_th=0.98","-f","null","-"]).stderr.count("black_start")
    return {"I":I,"TP":TP,"black":blk}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("--out",default=None)
    ap.add_argument("--keywords",default=None,help="カンマ区切り。素材固有の用語")
    ap.add_argument("--style",default=None,help="styles/*.yaml の style_id（例: kirinuki）")
    ap.add_argument("--no-diarize",action="store_true")
    ap.add_argument("--no-cut",action="store_true",help="無音カットをしない")
    ap.add_argument("--logo",default=None,help="チャンネルロゴ画像。スタイルが nameplate を持つとき使う")
    ap.add_argument("--broll",default=None,help="挿入画像のフォルダ。話題の頭へ順に入れる")
    ap.add_argument("--headlines",default=None,
                    help="論点見出しの差し替えJSON。{\"0\":\"何を作ったのか\"} の形")
    a=ap.parse_args()
    global KEYWORDS
    if a.keywords: KEYWORDS = [x.strip() for x in a.keywords.split(",") if x.strip()]
    src=os.path.abspath(a.src)
    base=a.out or os.path.join(os.path.dirname(src),
          os.path.splitext(os.path.basename(src))[0]+"_edit")
    wd,dist=f"{base}/work",f"{base}/dist"
    os.makedirs(wd,exist_ok=True); os.makedirs(dist,exist_ok=True)

    log("① プリフライト"); env=preflight(src)
    log(f"  {env['W']}x{env['H']} ({'縦' if env['portrait'] else '横'}) rot={env['rot']} "
        f"{env['dur']:.1f}秒 {env['I']:.1f}LUFS hw={env['hwenc']}")
    log("② 音声抽出＋強調"); audio(src,wd)
    log("③ ASR（2パス）"); P=asr(wd)
    log("④ 多数決照合"); words=reconcile(P,wd)
    log("⑤ 文節チャンク")
    words = drop_repetition(words)
    ac = align_api_text(P.get("_text"), words) if P.get("_text") else None
    caps = chunk(words, chars=ac)
    log(f"  {len(caps)}件 / 平均{sum(len(c['text']) for c in caps)/len(caps):.1f}字")

    # ── 5.5 話者分離。**生の a16k.wav を使う**。
    #  a_enh.wav は loudnorm+acompressor でダイナミクスが潰れており、
    #  VADが1区間しか返さない（実測: 生136区間 → 強調後1区間）。
    #  話者の手掛かりである音量差そのものを消しているため、強調音声では原理的に無理。
    spk_ok = False
    if not a.no_diarize:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import diarize as DZ
            turns = DZ.diarize(f"{wd}/a16k.wav", wd, segments=None)
            if turns:
                # confidence / low_confidence は **各ターンではなく親側**にある。
                # 2026-08-01: t.get("confidence", 1.0) と書いてしまい、既定値1.0が返って
                # ゲートが一度も作動せず、信用できないラベルで色を塗った。
                meta = json.load(open(f"{wd}/diarization.json"))
                conf = meta.get("confidence")
                low  = bool(meta.get("low_confidence"))
                spk_ok = not low
                # 【2026-08-02】信用できない話者ラベルを caps に焼くと、
                #  色分けを止めても mark_kinds が話者ベースに切り替わり、
                #  論点見出しが6件→1件に崩れた。**信用できないなら付けない。**
                if spk_ok:
                    caps = DZ.assign_speakers(caps, turns)
                n = len({t.get("speaker") for t in turns})
                cs = "—" if conf is None else f"{conf:.3f}"
                mt = meta.get("median_turn_sec")
                log(f"  話者 {n}人 / 経路 {meta.get('route')} / 確信度 {cs}"
                    f" / ターン長中央値 {mt}秒 → "
                    + ("テロップを色分け" if spk_ok
                       else "**信用できないので話者を使わない**"))
        except Exception as e:
            log(f"  話者分離をスキップ（{e}）")
    caps = mark_kinds(caps, spk_ok)

    # ── 5.7 自動カット。**字幕の時刻もカット後の時間軸へ写す**
    cuts = []
    if not a.no_cut:
        log("⑤.7 自動カット")
        cuts = find_cuts(words, env, wd, caps)
        if cuts:
            rm = sum(c["end"] - c["start"] for c in cuts)
            for c in caps:
                c["start"], c["end"] = remap(c["start"], cuts), remap(c["end"], cuts)
            caps = [c for c in caps if c["end"] - c["start"] >= 0.25]
            env = dict(env, dur=round(env["dur"] - rm, 3))
    json.dump(caps,open(f"{wd}/captions.json","w"),ensure_ascii=False,indent=1)
    json.dump(cuts,open(f"{wd}/cuts.json","w"),ensure_ascii=False,indent=1)

    log("⑥ 字幕PNG")
    seq,ng=capseq(caps,env,wd,style_id=a.style,spk_ok=spk_ok,logo=a.logo,broll=a.broll,
                  headlines=a.headlines,cuts_=cuts)
    log("⑦ レンダリング"); outp=f"{dist}/final.mp4"
    rc=render(src,seq,env,outp,wd,cuts)
    log("⑧ 納品パック"); qc=pack(caps,outp,wd,dist)
    log("─"*46)
    log(f"完成: {outp}")
    log(f"  ラウドネス {qc['I']} LUFS / TruePeak {qc['TP']} dBFS / 黒フレーム {qc['black']}")
    log(f"  4辺検算NG {ng}件 / 字幕 {len(caps)}件")
    log(f"  総所要 {time.time()-T0:.1f}秒")
    return rc

if __name__=="__main__": sys.exit(main())
