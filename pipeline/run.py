#!/usr/bin/env python3
"""素材1本 → 公開できる動画一式。

  uv run --with pillow --with fugashi --with unidic-lite --with mlx-whisper \
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
    return {"W":W,"H":H,"rot":rot,"dur":dur,"I":I,
            "portrait":H>W, "drawtext":has("drawtext"),
            "hwenc":"h264_videotoolbox" in enc}

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
    import mlx_whisper
    out = {}
    for tag, f in (("enh", "a_enh.wav"), ("raw", "a16k.wav")):
        p = f"{wd}/asr_{tag}.json"
        if not os.path.exists(p):
            r = mlx_whisper.transcribe(f"{wd}/{f}",
                    path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                    language="ja", word_timestamps=True, verbose=False)
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


def capseq(caps, env, wd, style_id=None, spk_ok=False):
    from PIL import Image, ImageDraw, ImageFont
    W,H,FPS = env["W"], env["H"], 30
    fp = next((q for q in (
        os.path.expanduser("~/Library/Fonts/NotoSansJP-Bold.ttf"),
        "/Library/Fonts/NotoSansJP-Bold.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    ) if os.path.exists(q)), None)
    if not fp:
        sys.exit("NotoSansJP-Bold.ttf が見つからない。~/Library/Fonts/ に入れてください")

    # ── スタイル定義を使う経路。失敗したら既定レイアウトに落ちる（無音で落ちない）
    plan = None
    if style_id:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import style_apply as SA
            st = SA.load_style(style_id, W, H)
            plan = SA.render_plan(st, caps, W, H, speaker_kinds=True)
            ev = len({p["event_index"] for p in plan})
            log(f"  スタイル {style_id} を適用: {ev}イベント / {len(plan)}行")
            # 常駐の論点見出し帯。**字幕しか描かないのは「スタイルを再現した」とは言えない**
            items = topic_items(caps, env["dur"])
            bar = SA.render_role(st, "title_bar", items, W, H) if items else []
            if bar:
                log(f"  論点見出し帯 {len(items)}件（話題の切れ目＝質問文）")
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
    keys = []
    for fr in range(n):
        t = fr / FPS_
        keys.append((at(ev, t), at(bev, t)))
    uniq = sorted(set(keys))

    fonts = {}
    out=f"{wd}/capseq"; shutil.rmtree(out,ignore_errors=True); os.makedirs(out)
    ng=set(); M={}
    for key in uniq:
        ci, bi = key
        im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
        rows = (ev.get(ci) or []) + (bev.get(bi) or [])
        for q in sorted(rows, key=lambda r: r.get("z_order") or 0):
            fk=(q["font_px"],); f=fonts.get(fk) or fonts.setdefault(fk, ImageFont.truetype(fp,q["font_px"]))
            st=q["stroke_px"]; x,y=q["x"],q["y"]
            fill = tuple(q.get("fill") or SPK_FILL[0])
            d.text((x+3,y+3),q["text"],font=f,fill=(0,0,0,115),stroke_width=st,stroke_fill=(0,0,0,115))
            d.text((x,y),q["text"],font=f,fill=fill,stroke_width=st,stroke_fill=(0,0,0,255))
            x0,y0,x1,y1=q["bbox"]
            if x0-3<16 or x1+12>W-16 or y0-3<16 or y1+12>H-16: ng.add(key)
        if not rows:
            M[key]=f"{out}/_blank.png"
            if not os.path.exists(M[key]): im.save(M[key])
            continue
        M[key]=f"{out}/_m{uniq.index(key):03d}.png"; im.save(M[key])
    for fr in range(n): os.link(M[keys[fr]], f"{out}/{fr:05d}.png")
    log(f"  字幕PNG {len(set(M.values()))}枚 / 連番{n} / 4辺検算NG {len(ng)}件")
    return out, len(ng)

# ── 6. レンダリング（loudnorm 2パス・HWエンコード）
def render(src, seq, env, outp, wd):
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
    r = sh(["ffmpeg","-hide_banner","-v","warning","-y","-i",src,
            "-framerate","30","-i",f"{seq}/%05d.png",
            "-filter_complex","[0:v]eq=brightness=0.06:contrast=1.12:saturation=1.05,"
                              "unsharp=5:5:0.4[b];[b][1:v]overlay=0:0:format=auto[v]",
            "-map","[v]","-map","0:a","-af",af,
            *vcodec,"-pix_fmt","yuv420p","-r","30",
            "-c:a","aac","-b:a","192k","-movflags","+faststart",outp])
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
                caps = DZ.assign_speakers(caps, turns)
                spk_ok = not low
                n = len({t.get("speaker") for t in turns})
                cs = "—" if conf is None else f"{conf:.3f}"
                log(f"  話者 {n}人 / 経路 {meta.get('route')} / 確信度 {cs} → "
                    + ("テロップを色分け" if spk_ok else "**確信度が低いので色分けしない**"))
        except Exception as e:
            log(f"  話者分離をスキップ（{e}）")
    caps = mark_kinds(caps, spk_ok)
    json.dump(caps,open(f"{wd}/captions.json","w"),ensure_ascii=False,indent=1)

    log("⑥ 字幕PNG"); seq,ng=capseq(caps,env,wd,style_id=a.style,spk_ok=spk_ok)
    log("⑦ レンダリング"); outp=f"{dist}/final.mp4"
    rc=render(src,seq,env,outp,wd)
    log("⑧ 納品パック"); qc=pack(caps,outp,wd,dist)
    log("─"*46)
    log(f"完成: {outp}")
    log(f"  ラウドネス {qc['I']} LUFS / TruePeak {qc['TP']} dBFS / 黒フレーム {qc['black']}")
    log(f"  4辺検算NG {ng}件 / 字幕 {len(caps)}件")
    log(f"  総所要 {time.time()-T0:.1f}秒")
    return rc

if __name__=="__main__": sys.exit(main())
