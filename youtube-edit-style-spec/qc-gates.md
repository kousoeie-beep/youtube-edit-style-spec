# QCゲートの盲点とcommon.pyの既知バグ

**このファイルは何を扱うか**: `overlap_qc.py`（被りQCゲート）が原理的に検出できない4パターン、共通ライブラリ `common.py` の既知バグと修正状況。
**いつ読むか**: レンダリング前のQC実行時、および「QCが通ったのにおかしい」という症状の切り分け時。QCゲート自体の実行要件は [environment-notes.md](environment-notes.md) の「レンダリング前の被りQCゲート必須」を参照。

## 被りQCゲートの盲点（重要・2026-07-26に2つ目が判明）

1. `overlap_qc.py` は**レイヤー間**の交差を検出するツールであり、**同一PNG内部の描画順ミス**（例: タイトル文言の末尾にバッジが重なって読めない）は原理的に検出できない
2. **オーバーレイをベースPNGへ事前合成した場合／ビデオソースを多段filter_complex（alphamerge・円形マスク等）で合成した場合、要素がマニフェストに現れずQCから完全に不可視になる**。build_liveの検証で `element_count=0 / overlaps=[]` という**偽の安全信号**が出た（＝1つも検査していないのに合格に見える）
   → **ツール側を修正済み**: 要素0件なら警告を出し、JSONに `status: "NOT_INSPECTED"` を記録し、**終了コードでも区別する**（`0`=PASS / `1`=FAIL / `2`=NOT_INSPECTED）。
   シェルで `overlap_qc.py --config X && レンダリング` と書けば、検査不能時にレンダリングが止まる。
   **`element_count` が期待より少なければ、その時点でQCは信用できない**
3. **動画（.mp4等）をoverlay元にするとツール自体がクラッシュしていた**（2026-07-26 screen_tutorial検証で発覚）。単入力チェーン解決器がPNGアセットと動画のcrop/scaleチェーンを区別せず、PILが動画を画像として開こうとして `UnidentifiedImageError` で全体が落ちる。webcam PiPを使うスタイル（build_live / wipe_explainer / screen_tutorialの常駐PiP型）で必ず踏む
   → **ツール側を修正済み**: `is_video_asset()` を追加しクラッシュは解消。ただし**動画要素は現状「検査対象から除外」される**（アルファを静的に取れないため）。
   **＝webcam PiP等の動画レイヤーはQCの検査範囲外**であり、`element_count` にも含まれない。**必ずフレーム目視で確認すること**

4. **Pillowの `ImageDraw` は低アルファ描画をアルファ合成しない**（2026-07-26 documentary_cinematic検証で発覚）。
   RGBA値をそのまま**上書き**するため、紙の粒状ノイズ等を「低アルファの点を敷き詰める」方法で表現すると、
   **全画面カードのはずが近透明の「穴」だらけになりベース映像が透ける**。
   overlap_qcはレイヤー矩形しか見ないので検出できない（4つ目の盲点）。
   → **対策**: テクスチャ表現でも**アルファは255に統一**し、色の明度差で質感を出す。
     どうしても半透明が要るなら別レイヤーを作って `Image.alpha_composite()` で合成する。
     **初回レンダリング後のフレーム目視で必ず確認すること**（透けは静止画で一目でわかる）

5. **グロー／ぼかしを持つ要素はQCの網をすり抜ける**（2026-07-27 ai_biz_pitch再実装検証で発覚）。
   `overlap_qc.py` は `ALPHA_THRESHOLD=10` で実描画bboxをトリムするが、
   **ネオングローやぼかしの外周はalphaが10未満まで落ちるため切り捨てられる**。
   実測: `positive_keyword.png`（緑ネオングロー付き）は 1099×187 のPNGに対し
   alpha>10 の範囲が y=8..178 で、**上下8pxがトリムされた**。
   その結果、PNG生bbox基準では base_caption と縦11px・bbox_ratio 7.3% 重なっているのに
   **QCは重なりy方向2pxと判定し検出しなかった**。
   → **対策**: グロー/ぼかし/ソフトシャドウを持つ装置は、QCが緑でも重なりうると考え、
     必ずフレーム目視すること。視覚的な「にじみ」は人間には見えてもQCには見えない。

## 共通ライブラリ `common.py` の既知バグと修正（2026-07-26）

**英語テキストで単語間スペースが全消失していた**（japan_vlogのバイリンガル字幕検証で発覚）。
`wrap_text_by_pixel` が fugashi（日本語形態素解析器）でトークン化するため、英語の空白がトークンとして返らず消える:
- `"Good morning"` → `"Goodmorning"`
- `"I turn on the TV to check the news"` → `"IturnontheTVtocheckthenews"`

日本語は空白を持たないため今まで顕在化しなかった。**バイリンガル字幕・英語圏スタイル（documentary_immersiveの英語圏側、build_liveの英語UI等）を作る際は必ず踏む**。
→ **修正済み**: 空白で先に分割し、空白なし断片のみfugashiにかけ、空白を明示復元する実装に変更。日本語の禁則処理（行頭に「、」が来ない）も維持されることを確認済み。

**非対称な入退場（entrance と exit で秒数が違う）を表現できなかった**（documentary_narrated_jpの検証で発覚）。
`FilterBuilder.overlay_png()` の `fade` が単一引数で、in/outが必ず同値になっていた。
yamlは `entrance: hard_cut` / `exit: fade`（例: 局ロゴが開始時は即出現、末尾のみ2〜3秒かけて消える）という
**非対称仕様を定義できるのに、ライブラリ側が実装できない**状態だった。
→ **修正済み**: `fade_in` / `fade_out` を個別指定できるようにした（未指定なら従来の `fade` を使うので後方互換あり）。
```python
overlay_png(..., fade_in=0, fade_out=2.5)   # 出現は即時、退場は2.5秒フェード
```

6. **bbox比が低くても実ピクセルは潰れている**（2026-07-27 documentary_narrated_jp検証で発覚）。
   **bbox_ratio 6.3% の裏で pixel_collision 99.5%、目視は完全判読不能**という組があった。
   細い装置が文字の芯を貫くとこうなる。bbox比だけの足切り（旧5%）はこの型を丸ごと落としていた。
   → **ツール側を修正済み**: 足切りを1%へ下げ、1〜5%の帯域は実ピクセル0.8以上のときだけ採用する。

7. **画面の外へのはみ出しは一切見ない**（2026-07-27 複数スタイルで発覚）。
   `overlap_qc.py` は**要素どうしの交差**しか見ないため、要素が画面外へ出ても検出しない。
   実例: entertainment_variety の ticker が左に9-50px、documentary_investigative の
   evidence_heading が左に286px、screen_tutorial の chapter_tab が左に173px
   （手順番号「2.ア」が消えた）、documentary_narrated_jp の bilingual_nameplate が
   左に54px（"MUSEUM"→"USEUM"）。
   → **目視でしか見つからない**。[style-learning/schema.md](style-learning/schema.md) の
     centerX検算を実装前に必ず通すこと。

## 遮蔽（occlusion）は衝突ではない。ただし宣言は実寸で検証される（2026-07-27 追加）

全画面の不透明カードが下の要素を隠すのは設計どおりの**遮蔽**であり、衝突ではない。
これをFAILに数えると全画面カードを持つスタイルは永久にFAILのままになり、本物が埋もれる。

```json
"opaque_fullscreen": ["statement_card", "endcredit", "motion_graphic"]
```

yaml側の `opaque_fullscreen: true` と対応させる。**上に乗っている場合のみ**遮蔽と判定する
（下にあるなら隠せないので衝突のまま）。結果は `occlusions` と標準出力の `[遮蔽]` に残る。

**宣言は信じられない。ツールが実寸で検証する。** キャンバスの98%未満しか覆っていない
要素は宣言を**却下**し、警告を出して通常の衝突として検査する
（`rejected_opaque_fullscreen` に記録）。
screen_tutorial の検証で「宣言しても実装が1121×581のままなら、宣言だけが独り歩きして
『QC除外してよい』という誤った安全信号になる」と指摘されたため。実際その要素は
画面の31.4%しか覆っていなかった。

## 意図的な重なりは config で宣言する（2026-07-27 追加）

ハイライトマーカーのように**設計上わざと重ねる**装置があると、QCが毎回FAILを出し
**本物の衝突がノイズに埋もれる**。実際 ai_biz_pitch では衝突6件のうち2件が意図的な重なりで、
残り4件の実害が見えにくくなっていた。

QCのconfigに書く:
```json
"intentional_overlaps": [["highlight_marker", "base_caption"]]
```
名前はPNGのbasename（連番サフィックスは無視して前方一致）。
宣言したペアはFAIL判定から外れるが、**結果JSONの `declared_overlaps` に必ず記録され、
標準出力にも `[宣言済]` として表示される**。黙って消さないので見落とせない。

yaml側には `intentional_overlap_with: <role名>` を書いて対応させること
（[style-learning/schema.md](style-learning/schema.md) 参照）。

**宣言は「目視しなくてよい」という意味ではない。** 意図的な重なりこそ、
重なり方が正しいか（マーカーが文字を潰していないか等）を目で見る必要がある。

## 実行時のつまずき（2026-07-27に実際に踏んだもの）

**`overlap_qc.py` は numpy と Pillow の両方を要求する。** 片方だけだと
`ModuleNotFoundError` で落ち、**終了コード1（＝FAILと同じ）を返す**。
QCが「衝突あり」で落ちたのか「起動すらできなかった」のかを取り違えないこと。

```bash
uv run --with pillow --with numpy python3 ../overlap_qc/overlap_qc.py \
  --config configs/<name>.json --out-json work/<name>/overlap_qc_result.json
```

**`render_and_mux.sh <name>` は `work/<name>/assets/` に
`base_<name>.mp4` と `audio_<name>.m4a` が揃っている前提**で動く。
既存の検証を雛形にして新しい `<name>` で回すとき、filter_complex と inputs は
スクリプトが生成するが**ベース映像と音声はコピーされない**。
無いと ffmpeg が `No such file or directory` で落ちるが、
**シェルの `set -e` で途中終了するため成功時の `RENDER_DONE_<name>` が出ない**
だけになり、原因がログを見るまで分からない。先に配置しておくこと。

**ビルドスクリプトは Pillow と fugashi/unidic-lite を要求する。**
```bash
uv run --with pillow --with fugashi --with unidic-lite python3 scripts/build_<name>.py
```
毎回uvが依存解決するため1回あたり30-40秒かかる。**パラメータ掃引を何度も回すなら
まとめて1回の実行で全候補を出力する設計にすること**（5回回して2分のタイムアウトに
かかった実例あり）。

**結論: QCゲート通過後も必ずフレームを目視すること。ツールの緑信号は目視の代替にならない。**
