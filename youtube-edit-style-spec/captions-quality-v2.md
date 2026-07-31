# 字幕品質v2（確定手順）

**このファイルは何を扱うか**: 字幕生成の音声強調・複数パスASR照合・改行規則・QCメトリクスの確定手順。
**いつ読むか**: 編集対象に字幕を付ける工程（毎回の編集で発生する主工程）。SKILL.md本体から常に参照される。

## 字幕品質v2（確定手順・2026-07-10実証）

誤認識の最大レバーは**モデルサイズではなく音声強調**。以下を標準とする：

1. **音声強調版を必ず作る**（不明瞭時だけでなく常時）:
   ```bash
   ffmpeg -i audio_16k_mono.wav -af "highpass=f=90,afftdn=nf=-28,loudnorm=I=-14:TP=-1.5,acompressor=threshold=-24dB:ratio=3" audio_enhanced.wav
   ```
2. **ASRは large-v3-turbo 標準**（キャッシュ済み ~/.cache/whisper/）。`--word_timestamps True --language ja --output_format json`。raw / enhanced の両方を認識
3. **複数パス照合（LLM文脈裁定）**: medium/turbo-raw/turbo-enhanced 等の複数パスを突合し、(a)2パス以上一致→採用 (b)全パス不一致＋低確信(語prob<0.45)→**その語を字幕から省略**し `uncertain_terms.json` に記録 (c)創作・要約・言い回し改善は禁止（発音忠実の原則）。裁定は `caption_corrections.json`（before/after/basis/reason）に必ず記録
4. **改行は語境界のみ**: fugashi（形態素解析）でトークン化し、語の途中で折り返さない（「ギブ経／済圏」分断禁止）。1語が行幅超過する場合のみ語内分割
5. **QCメトリクス**: 低確信語の残存数を qc_selfcheck.md に記録。残存が多い場合は書き出し前に警告
- 実例（IMG_2242）: 「義務→ギブ」6箇所・「義バー→ギバー」等が音声強調だけでLLM修正なしに解消。真に不明瞭な音声はどのモデルでも崩れる→省略が正解
