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
    a = sh(["ffmpeg","-hide_banner","-i",src,"-af","ebur128=peak=true","-f","null","-"]).stderr
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

# ── 2. ASR（mlx＝Metal。CPU版は7倍遅い）
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
        log(f"  ASR[{tag}] {len(out[tag]['segments'])}セグ")
    return out

# ── 3. 多数決照合（不一致＋低確信は省略）
def reconcile(P, wd):
    import unicodedata
    nz = lambda t: re.sub(r"[、。，．！？!?\s]", "", unicodedata.normalize("NFKC", t).strip())
    W_ = {k: [w for s in v["segments"] for w in s.get("words",[])] for k,v in P.items()}
    base, adopted, omitted = W_["enh"], [], []
    for w in base:
        t = nz(w["word"])
        if not t: continue
        votes = 1 + sum(any(nz(x["word"])==t and abs(x["start"]-w["start"])<0.35
                            for x in W_[k]) for k in W_ if k!="enh")
        if votes>=2 or w.get("probability",1)>=0.45:
            adopted.append({"w":w["word"],"start":w["start"],"end":w["end"]})
        else:
            omitted.append({"w":w["word"],"start":round(w["start"],2),
                            "p":round(w.get("probability",1),3)})
    json.dump(omitted, open(f"{wd}/uncertain_terms.json","w"), ensure_ascii=False, indent=1)
    log(f"  照合 採用{len(adopted)} / 省略{len(omitted)} / 一致率{sum(1 for _ in adopted)/max(1,len(base)):.1%}")
    return adopted

# ── 4. 文節チャンク（語境界では割れる。自立語＋付属語で切る）
def chunk(words, maxc=16, gap=0.8):
    from fugashi import Tagger
    tg = Tagger()
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
        if (m["pos"] in ATTACH or m["pos"]=="接頭辞"
            or (m["pos"]=="名詞" and p in ("名詞","接頭辞","接尾辞"))
            or (m["pos"] in ("動詞","形容詞") and p in ("動詞","助詞"))):
            cur.append(m)
        else: B.append(cur); cur=[m]
    if cur: B.append(cur)
    B = [{"t":"".join(x["t"] for x in b),"s":b[0]["s"],"e":b[-1]["e"]} for b in B]
    END = ("です","ます","ですね","ますね","でした","ました","ですよ","ますよ")
    out, cur = [], []
    for b in B:
        if cur:
            n = sum(len(x["t"]) for x in cur)
            if (b["s"]-cur[-1]["e"]>=gap or n+len(b["t"])>maxc
                or (cur[-1]["t"].endswith(END) and n>=5)):
                out.append(cur); cur=[]
        cur.append(b)
    if cur: out.append(cur)
    caps=[]
    for c in out:
        t="".join(x["t"] for x in c)
        if len(t)<3: continue
        caps.append({"start":round(c[0]["s"],2),"end":round(c[-1]["e"],2),"text":t})
    return caps

# ── 5. 字幕連番PNG（回転後キャンバス・4辺検算つき）
def capseq(caps, env, wd):
    from PIL import Image, ImageDraw, ImageFont
    W,H,FPS = env["W"], env["H"], 30
    FS  = 72 if env["portrait"] else 92
    ST  = 10 if env["portrait"] else 12
    MG, CY, LH = 48, int(H*0.82), int((72 if env["portrait"] else 92)*1.46)
    MAXW = W-MG*2
    fp = next((q for q in (
        os.path.expanduser("~/Library/Fonts/NotoSansJP-Bold.ttf"),
        "/Library/Fonts/NotoSansJP-Bold.ttf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    ) if os.path.exists(q)), None)
    if not fp:
        sys.exit("NotoSansJP-Bold.ttf が見つからない。~/Library/Fonts/ に入れてください")
    f = ImageFont.truetype(fp, FS)
    d0 = ImageDraw.Draw(Image.new("RGBA",(10,10)))
    tw = lambda t: (lambda b: b[2]-b[0])(d0.textbbox((0,0),t,font=f,stroke_width=ST))
    def wrap(t):
        L,ln=[],""
        for ch in t:
            if tw(ln+ch)>MAXW and ln: L.append(ln); ln=ch
            else: ln+=ch
        if ln: L.append(ln)
        return L[:2]
    out=f"{wd}/capseq"; shutil.rmtree(out,ignore_errors=True); os.makedirs(out)
    blank=f"{out}/_b.png"; Image.new("RGBA",(W,H),(0,0,0,0)).save(blank)
    ng=[]; M={}
    for i,c in enumerate(caps):
        L=wrap(c["text"]); im=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(im)
        y0=CY-LH*len(L)//2
        for k,ln in enumerate(L):
            b=d.textbbox((0,0),ln,font=f,stroke_width=ST)
            x=(W-(b[2]-b[0]))//2-b[0]; y=y0+LH*k-b[1]
            d.text((x+3,y+3),ln,font=f,fill=(0,0,0,115),stroke_width=ST,stroke_fill=(0,0,0,115))
            d.text((x,y),ln,font=f,fill=(255,255,255,255),stroke_width=ST,stroke_fill=(0,0,0,255))
            if x<16 or x+(b[2]-b[0])>W-16: ng.append(i)
        if y0-3<16 or y0+LH*len(L)+12>H-16: ng.append(i)
        M[i]=f"{out}/_m{i:03d}.png"; im.save(M[i])
    n=int(env["dur"]*FPS)+1; act=[-1]*n
    for i,c in enumerate(caps):
        for fr in range(int(c["start"]*FPS), min(n,int(c["end"]*FPS)+1)): act[fr]=i
    for fr in range(n): os.link(M[act[fr]] if act[fr]>=0 else blank, f"{out}/{fr:05d}.png")
    log(f"  字幕PNG {len(M)}枚 / 連番{n} / 4辺検算NG {len(set(ng))}件")
    return out, len(set(ng))

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
    a=ap.parse_args()
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
    log("⑤ 文節チャンク"); caps=chunk(words)
    json.dump(caps,open(f"{wd}/captions.json","w"),ensure_ascii=False,indent=1)
    log(f"  {len(caps)}件 / 平均{sum(len(c['text']) for c in caps)/len(caps):.1f}字")
    log("⑥ 字幕PNG"); seq,ng=capseq(caps,env,wd)
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
