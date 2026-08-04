# style_apply.py — 編集スタイル定義（yaml 15本）をパイプラインに接続した記録

2026-08-01 / 対象素材 `IMG_2514_v2`（1080x1920・iPhone縦撮り `rotation=-90`）
成果物 `pipeline/style_apply.py`（698行・新規1本）。`run.py` は未変更。

再現コマンド:

```bash
uv run --with pyyaml --with pillow python3 style_apply.py --audit 1080 1920
uv run --with pyyaml --with pillow python3 style_apply.py --compare 1080 1920
uv run --with pyyaml --with pillow python3 style_apply.py --plan kirinuki \
    --caps ../IMG_2514_v2/work/captions.json --canvas 1080 1920 --png 2
```

---

## 1. 縦横問題への方針と、その根拠

### 採用: (a) 座標変換 —「正規化座標はそのまま／px は一様スケール」

| 対象 | 変換 | 理由 |
|---|---|---|
| `position.centerX/centerY` | **変換しない**（そのまま新キャンバスに掛ける） | 既に正規化されている。「centerY 0.85 = 下から15%」は設計意図であって 1080 の産物ではない |
| `font_px*` / `stroke_px` / `padding_px` / `max_width_px` / `size` / `char_width_px` | **一様スケール `s = canvas_w / 1920`**（縦では 0.5625） | `max_width_px` は折返し幅（schema.md L579）なので W に追随しないと必ず溢れる |
| `opaque_fullscreen: true` の `size` | **スケールせず実キャンバスに置換** | 一様スケールすると 1920x1080 → 1080x607 = **被覆31.6%** になり、schema.md L401「全画面を示せ」と `overlap_qc` の98%却下を自分で破る（実装中に実際に踏み、PNG目視で発見） |
| `max_chars` | **宣言値を使わず再導出**（`max_width_px ÷ 字送り`） | schema.md L908「派生値は分母を動かした周で全部やり直す」 |

**なぜ一様スケールなのか（W比とH比を別々に掛けない理由）**

非一様にすると `max_chars ≒ max_width_px / font_px` が変わる。すると 11周かけて詰めた
`max_chars` / `max_lines` / 4辺検算・すきま検算の整合が**全部無効になる**。
一様なら比が保存されるので、yaml側の検算前提をそのまま持ち越せる。

**なぜ (b)(c) を採らなかったか**

- (b) 縦用の既定値を別に持つ / (c) 縦は基準プリセットへフォールバック
  → どちらも**スタイル定義を1バイトも読まない**ことになる。11本の position・
  z_order・排他宣言・変種・overflow_policy が全部死ぬ。接続する意味がなくなる。
- schema.md L1111 は「縦対応を yaml に入れるなら `aspect` ごとの `variant` を切るのが筋」
  と書いているが、それは**yaml を書き換える**案である。今回は yaml 不可侵なので、
  読み取り側で同じ効果を出す (a) を採った。

### 一様スケールが壊すもの: **16px は比で保存されない**

これが縦での破綻の唯一の主因だった。

比は保存されるが、`MIN_MARGIN = 16px` は**絶対要求**なので保存されない。
影（右下+12 / 左上−3、schema.md L1011 の `alpha > 0` 実測値）もスケールしない。
したがって右辺の実余裕は:

```
m_縦 = (m_横 + 12) × 0.5625 − 12
```

実データで検証: 全54roleのうち **46roleで式が完全一致**、残り8roleも乖離 ≤3.4px
（8件とも可読性下限で font を持ち上げ、`stroke_px` をスケールでなく再導出した role）。

この式を 16px について解くと **しきい値**が出る:

| 辺 | 横で必要な余裕 | 意味 |
|---|--:|---|
| 右 | **37.8px** | 横でこれ未満の余裕しか無い role は、縦にすると必ず16pxを割る |
| 左 | **30.8px** | 同上（左は影が3pxなので少し緩い） |

**縦にすると横方向だけが苦しくなり、縦方向はむしろ楽になる。** 実測:

| キャンバス | 4辺NGのrole数 | 内訳 |
|---|--:|---|
| 横 1920x1080 | 7 | 下5 / 左1 / 上1 ← **縦方向が主** |
| 縦 1080x1920 | 19 | **左12 / 右8 / 上0 / 下0** ← **100%が横方向** |

横で出ていた「下」5件は縦では全部消えた（高さが 1080→1920 になったため）。
これは机上の予測ではなく、`--compare 1080 1920` の出力そのもの。

### 可読性下限（caption 系 role のみ）

一様スケールは `font_px 92 → 51px` にする。角度的には横 92px/1920 と等価だが、
run.py capseq の実測ベースライン（`FS = 72 if portrait else 92` / `ST = 10 if portrait else 12`、
run.py L225-226）を下回る。よって **caption 系 role に限り** 72px を下限とし、
下限まで持ち上げたら schema.md L662 のチェックリストどおり
`stroke_px` / `char_width_px` / `max_chars` / `size.height` を**全部再導出**してから
検算をやり直す。補助role（バッジ・チャプタータグ）は持ち上げない（元々補助情報のため）。

> 実装中の事故: 最初 `font_px` を下限に代入した**後**に従属フォントへ倍率を掛けたため、
> `font_px` に倍率が二重にかかり `72 → 123px` になった。順序を逆にして解消。
> schema.md L650「フィールドを直したら派生値を全部やり直す」の、まさに逆方向の失敗。

---

## 2. 15本の適用可否（縦 1080x1920）

判定の定義:
- **可** = 同時表示しうる全roleが 4辺16px・すきま16px を満たす
- **条件付き可** = caption 系は成立。補助roleにNGがある（`load_style(..., strict=False)` で取れる）
- **不可** = caption 系 role が無い / 解決できない / caption 自身が検算NG

| # | style_id | 変種 | 横1920x1080 | **縦1080x1920** | caption役 | font | 字/行×行 | 縦で新たに割れたrole |
|--:|---|---|:--:|:--:|---|--:|---|---|
| 1 | `documentary_immersive` | variant_B3 | 可 | **可** | `caption` | 72 | 7×1 | — |
| 2 | `screen_tutorial` | variant_st1 | 可 | **可** | `caption` | 72 | 7×2 | — |
| 3 | `wipe_explainer` | pip_pattern_A | 不可 | **可** | `caption` | 72 | 6×2 | — |
| 4 | `kirinuki` | — | 不可 | **条件付き可** | `caption` | 72 | 11×2 | `nameplate`（右15.0px・**1.0px不足**） |
| 5 | `ai_news_weekly` | — | 不可 | **条件付き可** | `caption` | 72 | 11×2 | `title_bar`（右4.7px） |
| 6 | `japan_vlog` | — | 不可 | **条件付き可** | `caption` | 72 | 11×2 | `timestamp_pill`（左15.0）、`nameplate`（右4.6） |
| 7 | `documentary_startup_journey` | variant_field_interview | 可 | **条件付き可** | `caption` | 72 | 11×2 | `nameplate`（右10.6）、`chapter_tag`（左12.6）、`person_status`（左10.6） |
| 8 | `documentary_narrated_jp` | variant_tv_incident | 不可 | **条件付き可** | `quote_caption` | 72 | 10×2 | `logo_badge`（右4.4）、`fact_caption`（左10.5） |
| 9 | `ai_tool_avatar` | mode_A | 不可 | **条件付き可** | `caption` | 72 | 8×1 | — （横から継続の `nameplate` 右7.9） |
| 10 | `ai_biz_pitch` | — | 条件付き可 | **条件付き可** | `base_caption` | 72 | 14×1 | `title_bar`(右12.9)、`positive_keyword`(左13.0/右4.0)、`app_logo_card`(左14.1)、`reaction_character`(左8.0) |
| 11 | `documentary_cinematic` | variant_narration_only | 不可 | **不可** | `caption` | 72 | 4×2 | caption 自身が左 **−3.9px**（画面外） |
| 12 | `documentary_investigative` | variant_host_investigator | 不可 | **不可** | `caption` | 72 | 10×2 | caption 自身が左 9.0px |
| 13 | `business_talk` | — | 不可 | **不可** | — | — | — | `dialogue_caption` が解決不能（後述） |
| 14 | `educational_explainer` | — | 不可 | **不可** | — | — | — | caption 系 role が存在しない |
| 15 | `entertainment_variety` | — | 不可 | **不可** | — | — | — | `caption` は `layer_kind: video`（ワイプ映像）で字幕ではない |

### 不可の内訳は「縦のせい」ばかりではない

**縦が原因で落ちたのは 11 と 12 の2本だけ**（caption 自身の左端が横方向で潰れた）。
13〜15 は**横でも同じ理由で不可**であり、縦横問題とは無関係な yaml 側の欠落である。

- `business_talk.dialogue_caption`: `max_width_px` が数値でなく**散文**
  （「動的628px（サイドバーのみ同時表示時）/ 484px（…）」＋導出式の解説）。
  `max_chars` も `"10（628px時）/ 8（484px時）"` という文字列。
  **ジャンル名（対談・投資プレゼン）がこの素材に最も近いのに、機械では1本も読めない。**
- `educational_explainer`: caption 系 role が1つも無い（`running_outline` / `definition_pill` /
  `region_card` など、字幕以外の装置だけで構成されている）。
- `entertainment_variety`: `caption` という名前の role はあるが `layer_kind: video`＝ワイプ映像。
  role 名だけで字幕と判断すると誤る（`business_talk.caption` も `opaque_fullscreen` の
  全画面カードで字幕ではない）。**この2件があるので caption 役の選定から
  `opaque_fullscreen` と `layer_kind: video` を除外している。**

### 副産物: lint.py の 4辺検算が構造的に見ていない領域がある

`lint.check_four_edges` は **`size` を宣言している role しか見ない**
（`if not isinstance(sz, dict): continue`）。`max_width_px` しか持たない role は
`lint._rect` で矩形を推定できるのに、4辺検算からは構造的に外れている。

本実装は `lint._rect` と同じ式で推定してから4辺を測るので、
**横 1920x1080 でも lint が 0件と報告する箇所に NG を出す**（例: `kirinuki.caption` の
下端余裕 4px、`documentary_cinematic.caption` の左端）。
どちらが正しいかではなく**見ている範囲が違う**ので、報告では
`4辺[宣言矩形]` / `4辺[推定矩形]` を出し分けている。
schema.md L542「max_chars も max_width_px も持たない role は一括補完から構造的に外れる」と
同型の穴が、4辺検算側にも残っていた。

---

## 3. 実測（実素材の字幕を通した結果）

入力 `IMG_2514_v2/work/captions.json`（47件）。
`render_plan()` の出力（＝実際に描く1行ごとのエントリ）を `verify_plan()` で再検算した。

| style | caption役 | 折返し幅px | 字/行×行 | イベント数 | 分割 | 平均字/行 | 孤立行(≤2字) | **4辺NG** | **すきまNG** | **最小余裕px** |
|---|---|--:|---|--:|--:|--:|--:|--:|--:|--:|
| `kirinuki` | caption | 828 | 11×2 | **47**（分割なし） | 0 | 7.4 | 8 | **0** | **0** | **121.0** |
| `ai_news_weekly` | caption | 844 | 11×2 | 47 | 0 | 7.5 | 7 | 0 | 0 | 106.0 |
| `japan_vlog` | caption | 844 | 11×2 | 47 | 0 | 7.5 | 7 | 0 | 0 | 106.0 |
| `documentary_startup_journey` | caption | 844 | 11×2 | 47 | 0 | 7.5 | 7 | 0 | 0 | 106.0 |
| `documentary_narrated_jp` | quote_caption | 788 | 10×2 | 47 | 0 | 7.1 | 9 | 0 | 0 | 134.0 |
| `screen_tutorial` | caption | 506 | 7×2 | 73 | 52 | 5.1 | 2 | 0 | 0 | 74.0 |
| `wipe_explainer` | caption | 444 | 6×2 | 82 | 70 | 4.5 | 9 | 0 | 0 | 205.2 |
| `documentary_immersive` | caption | 506 | 7×1 | **114** | 108 | 5.1 | 0 | 0 | 0 | 128.0 |
| `ai_tool_avatar` | caption | 621 | 8×1 | 86 | 78 | 6.8 | 0 | 0 | 0 | 180.4 |
| `ai_biz_pitch` | base_caption | 1035 | 14×1 | 68 | 42 | 8.6 | 0 | **23** | 0 | **14.0** |

要求（4辺検算NG 0件・すきま16px以上）は **`ai_biz_pitch` 以外の9本で達成**。

`ai_biz_pitch` だけ NG 23件なのは、`base_caption` が `edge_bleed: true`（＝画面端まで
流す設計）を宣言しているため role 単位の4辺検算からは除外されるが、
**実際に描いた行は除外されない**から。折返し幅1035pxに対しキャンバス1080pxなので
片側 22.5px、影12pxを引いて 10.5px しか残らない。
`edge_bleed` は「はみ出してよい」という宣言であって、縦キャンバスでもそれを
意図しているかは yaml から読めない。**宣言矩形は緑・描画実体は赤**という、
schema.md L451「検査は緑、出力は違反」と同型の状態である。

### 目視所見（1080x1920 に実描画・`style_preview/` に8枚）

セーフエリア16pxの枠線と、caption 以外の role の宣言矩形を重ねて描いている。

1. **`kirinuki`（`kirinuki_1080x1920_ev023.png`）— 最も素直。**
   2行がキャンバス中央下（centerY 0.85 = 1632px）に収まり、左右に126px、
   下に約190pxの余白。`title_bar` の帯と `nameplate` バッジが上端に離れて乗る。
   `nameplate` の右端がセーフエリア線にわずかに触れて見える（実測15.0px＝1.0px不足）。
   **目視と数値が一致した。**
2. **`screen_tutorial`（`..._ev036.png`）— 幾何は通るが構図が崩れる。**
   caption が `centerX 0.3` にあるため、縦では画面の**左3割**に字幕が寄る。
   横画面では「PiPの左隣」という合理的な配置だが、縦にすると
   **隣に何も無い場所に浮く**。検算は 0件・最小余裕74pxで通るのに、絵として不自然。
   *正規化座標の意味は保存されるが、その座標が持っていた「隣接関係の意図」は保存されない。*
   `title_bar`（`opaque_fullscreen`）がキャンバス全域を覆っていることも目視確認
   （修正前は1080x607＝31.6%しか覆っておらず、この目視で発覚した）。
3. **`wipe_explainer`（`..._ev041.png`）— 検算は最良、可読性は最悪。**
   最小余裕205pxで全styleトップなのに、折返し幅444px＝6字/行のため
   「このページを」が **「このページ」＋「を」** に割れ、1文字だけの行が出る。
   孤立行9件は kirinuki の yaml 自身が警告している型
   （「18字の発話で16字＋2字の**2文字だけの孤立行**が出る…見栄えが悪い」kirinuki.yaml L114）。
   **検算NG 0件は「読める」を意味しない。**
4. `documentary_immersive` は `max_lines: 1` なので47発話が**114イベント**に膨れる。
   幾何は通るが、字幕が高速に入れ替わり続ける別物の動画になる。

---

## 4. この素材に合うスタイルの推薦

素材の性質: 暗い会議室・**カメラ固定**・2人の対談（質問者＋回答者）・
**画面共有が主役**（NotebookLM で1枚ペラPDFから動画生成するデモ）・
run.py の実出力はカット編集ゼロ・全発話字幕。

### 第1推薦: **`kirinuki`**（`load_style('kirinuki', 1080, 1920, strict=False)`）

| 理由 | 根拠 |
|---|---|
| 構造が素材と一致 | `camera: static`（実測）／`cuts_per_min 0.36`＝カメラ転換ほぼ無し／`visual_rhythm 36.9`＝**テロップ切替だけで視覚リズムを作る**。run.py はカット編集を1回もしないので、この文法だけが破綻しない |
| 字幕方針が一致 | 「全発話フル字幕・カバー率ほぼ100%の無間隙連続」。captions.json は全発話をカバーしており、そのまま流し込める |
| 縦での実測が最良 | 47発話 → **47イベント（分割0件）**。`max_chars 11×2行` が元の16字チャンクを吸収する。4辺NG 0件・すきまNG 0件・最小余裕121px |
| 論点見出しの器がある | `title_bar`（論点見出し常駐・話題転換で差し替え）＝この対談の「Q.何を作ったか→デザインは→用途は」という質問単位の章立てにそのまま使える |
| 欠点が1つだけ・かつ1pxで既知 | `nameplate`（右上ロゴバッジ）の右余裕15.0px（要求16px、**1.0px不足**）。`centerX 0.95 → 0.94` で 25.8px になり解消する（yaml不可侵のため未適用・算術のみ提示） |

解決後の caption 実値: `font 72 / stroke 10 / 折返し828px / 11字×2行 /
center (540, 1632) / size 848x232 / z_order 2 / overflow_policy split_event`。
run.py capseq の実測ベースライン（font 72・ST 10・centerY 0.82）とほぼ同座標に着地する
＝**11周のスタイル資産と、実素材で確立した工程が同じ場所を指した**。

### 第2推薦（構造の参照先）: **`screen_tutorial` / `variant_st1`**

ジャンル定義としては本命。「純粋PC画面共有クラスタ（0.17-1.7 cuts/min）」＋
`nameplate` が常駐PiP（`layer_kind: video`）＋
`click_highlight_circle` / `click_highlight_box`＝**操作対象を赤円・赤矩形で囲む**
（`screen_tutorial` は「ズーム不使用が確定」＝クロップズームでなくオーバーレイ図解）。
この素材は「この画面に貼り付けた」「ここが画像生成」と画面を指す発話が多いので、
**強調装置としてはこれが唯一の適合**。

ただし縦では採らない。理由は2つ、どちらも実測:
- caption が `centerX 0.3` → 縦では画面左3割に浮く（目視所見2）
- 折返し506px＝7字/行 → 73イベント中**52件が分割**。発話の区切りが壊れる

→ **caption は kirinuki、`click_highlight_*` だけ screen_tutorial から借りる**のが実務解。
なお `click_highlight_*` は `position: {centerX: variable, centerY: variable}` で
座標が yaml に無い（schema.md L470「`anchored_to_target` は対象の入力経路が無いと機能しない」）。
使うなら**対象座標を人が指定する経路**が別途要る。

### 採らないもの

| style | 理由 |
|---|---|
| `business_talk` | **ジャンル名（対談・投資プレゼン）は最も近いが、機械では読めない。** `dialogue_caption` の `max_width_px` / `max_chars` が散文。横1920x1080でも不可 |
| `wipe_explainer` | 画面共有＋ワイプという構造は合うが、縦で6字/行・孤立行9件。可読性が成立しない |
| `documentary_*` 5本 | いずれもナレーション主導・テロップ最小・無音率0.13-1.22%（`_index.md` L5）。この素材は**2人の生の対話・間もフィラーも残る**ので文法が正反対 |
| `ai_biz_pitch` | 「稼ぐ」訴求型のCTA/煽り文法。用途が違ううえ、縦で描画4辺NG 23件・最小余裕14px |
| `educational_explainer` / `entertainment_variety` | caption 系 role が実質存在しない |

---

## 5. 実装中に踏んだ欠陥（記録）

すべて schema.md に既出の型だった。**散文で読んでいても踏む**という再演。

| # | 事故 | 対応する規約 | 発見手段 |
|--:|---|---|---|
| 1 | `font_px` を下限に代入した後に倍率を掛け、**二重適用で 72→123px** | L650「フィールドを直したら派生値を全部やり直す」の逆方向 | 判定表の font 列が 100/123/130/185 と散った |
| 2 | `opaque_fullscreen` を一様スケールし、**被覆31.6%** の「全画面カード」を作った | L401「宣言だけでは1ピクセルも覆えない」 | **PNG目視のみ**。検算は素通り（4辺検算の除外対象のため） |
| 3 | role 名 `caption` だけで字幕役を選び、全画面カード（business_talk）とワイプ映像（entertainment_variety）を字幕に割り当てた | L352「`layer_kind: video` は QC の盲点」 | 判定表で business_talk の caption が全画面サイズになっていた |
| 4 | `font_px` 未宣言の role を「解決不能」にして 7本を落とした | L684「`caption_font` は `font_px` を持たない role の既定値」 | 初回 audit で kirinuki まで不可になり、明らかにおかしいと分かった |

**#2 は検算では絶対に見つからない。** PNGを描いて見るまで、
判定表は「可」を出し続けていた。qc-gates.md の「QCの緑は目視の代替にならない」が
そのまま再現した。

---

## 6. 接続の残り（未着手・提案のみ）

`run.py` は未変更なので、現時点では `style_apply.py` は**独立して動く**が
パイプラインからは呼ばれていない。接続するなら `run.py` の `capseq()` を

```python
st   = style_apply.load_style(STYLE_ID, env["W"], env["H"], strict=False)
plan = style_apply.render_plan(st, caps, env["W"], env["H"])
```

に置き換え、`FS/ST/MG/CY/LH` の直書き5定数を `plan` の値で駆動すればよい。
`render_plan` は1エントリ＝1描画行で `x,y` をそのまま `ImageDraw.text` に渡せる形にしてある
（run.py の既存の描画呼び出しと同じ座標系）。時間区間は
schema.md L453 に従い**半開区間 `[start, end)`** で返す。
run.py 側は `int(c["end"]*FPS)+1` で**終端を含めている**ので、接続時はここを
`int(c["end"]*FPS)` に直す必要がある（境界1フレームの二重表示。
schema.md L447 で ai_tool_avatar が実際に踏んだ型）。
