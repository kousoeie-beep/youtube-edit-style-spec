# R12 作業指示: pixel_collision に依存した判断を測り直す

**この会話にいなかった人が単独で実行できるように書いています。** 前提知識は不要です。

## なぜやるか

`overlap_qc.py` に**アルファ切り出しのオフセット漏れ**というバグがあり、
2026-07-31 に外部PRで修正されました（コミット `5c037e7`）。

```python
lx0, ly0 = ix0 - el.x, iy0 - el.y              # 旧（バグ）
lx0, ly0 = bx0 + ix0 - el.x, by0 + iy0 - el.y  # 新（bx0,by0 = 元画像内のbboxオフセット）
```

`Element.x/y` は**透明余白をトリムした後**の画面座標なのに、読み込む配列は**元PNG全体**でした。
オフセットを戻さないと**別の領域を切り出す**ため、透明余白を持つPNG（＝実質すべて）で
`pixel_collision` の値が狂います。

R6〜R11 の11周は、この値を根拠に「実害あり／なし」を判断してきました。
**bbox（矩形の交差）は正しいので結論の多くは変わらないはず**ですが、
**pixel だけを根拠にした判断は保証されません。**

## 測り直す対象は3つ。優先度順

### ① ツールの足切り規則そのもの（最優先）

`overlap_qc.py` のこの分岐が pixel に完全依存しています。

```python
LOW_BAND = 0.01
if ratio <= LOW_BAND:
    continue
pcr = pixel_collision_ratio(a, b, inter)
if ratio <= area_ratio_threshold:   # bbox比 1〜5% の帯域
    if pcr is None or pcr < 0.8:    # ← ここ
        continue
```

**bbox比 1〜5% の組を「拾うか捨てるか」を pcr だけで決めています。**
pcr が狂っていた間、この帯域では

- 本当は衝突していない組を**拾っていた**（過検出）か
- 本当は衝突している組を**捨てていた**（見逃し・こちらが危険）

のどちらか、あるいは両方が起きえます。

さらに、**この規則を作った根拠自体が狂ったコードでの測定**です。
`qc-gates.md` #6 に「`documentary_narrated_jp` の検証で bbox_ratio 6.3% の裏で
pixel_collision 99.5%、目視は完全判読不能」とあり、これを見て閾値を 5% → 1% に下げました。

**やること**: その組を修正後のツールで測り直す。
- pcr が 0.8 以上のままなら、規則も根拠も維持
- 0.8 を割るなら、**閾値 0.8 と LOW_BAND 1% の両方を決め直す必要がある**

### ② 過去のQC結果と新ツールの差分（網羅的に）

過去ラウンドの素材が残っています。**再レンダリング不要で、同じ素材を新ツールに通せます。**

```
~/Documents/編集作業/style_verify_20260725/
  configs/*.json      … 176件
  work/<name>/assets/ … 各10〜18枚のPNG
  work/<name>/overlap_qc_result.json … 旧ツールの結果
```

**やること**:
1. 各 config を修正後の `overlap_qc.py` で流し直す
2. 旧 `overlap_qc_result.json` と新結果を突き合わせる
3. **次の3つに分類して報告する**
   - `pixel が変わったが結論は同じ`（bbox が十分大きい ＝ 影響なし）
   - `pixel が変わって FAIL/PASS が反転した`（★最重要）
   - `新たに検出された／消えたペア`（足切り帯域の出入り）

差分の出し方の例:

```bash
cd ~/Documents/編集作業/style_verify_20260725
for c in configs/*.json; do
  n=$(basename "$c" .json)
  uv run --with pillow --with numpy python3 ../overlap_qc/overlap_qc.py \
    --config "$c" --out-json "work/$n/overlap_qc_r12.json" 2>/dev/null
done
# 旧 overlap_qc_result.json と overlap_qc_r12.json を比較する
```

※ 素材が欠けている config は飛ばして構いません。**飛ばした分は報告に明記**してください
（黙って落とすと「全部見た」と読まれます）。

### ③ `intentional_overlap_with` の3件

「重なっているが実害なし」と判断した組です。**その判断が pixel に依っています。**

| ファイル | 行 | 組 |
|---|---|---|
| `styles/ai_biz_pitch.yaml` | 362 | `highlight_marker` × `base_caption` |
| `styles/documentary_cinematic.yaml` | 279 | `archival_map_graphic` × `speech_caption` |
| `styles/documentary_investigative.yaml` | 259 | `evidence_heading` × `keyword` |

**やること**: 3組とも修正後のツールで測り直し、**フレームを目視**する。
「マーカーが文字を潰していないか」は pixel の値ではなく目で見る。

## 判定と報告

- 報告書は `verify/REPORT_TEMPLATE.md` の型で、**節ごとに追記**する
  （まとめてから書くと途中で落ちたとき全部消えます。実際に2回起きました）
- 数値には**実測の出所を添える**（どのconfig・どのPNG・どの座標計算）。
  「実測」と書くだけでは実測になりません
- **自分の実装ミスと、ツール／仕様の欠陥を分けて書く**

## 終わったら

1. `styles/*.yaml` と `style-learning/schema.md` の中で、
   **測り直しの結果と食い違う pixel 値の記述を訂正する**
   （13ファイル・約60箇所に pixel 値の言及があります）
2. `styles/lint.py` を流して**違反0**を確認してから閉じる
3. 閾値を変えた場合は `qc-gates.md` #6 と `overlap_qc.py` の両方を直す

## 予想される結論（先入観を持たないための注記）

**おそらく大半は「pixel の値は変わったが結論は同じ」になります。** bbox が
50%以上ある組では、pixel が多少ずれても実害の判断は動きません。

危ないのは **bbox比 1〜5% の帯域**だけです。そこに何組いるかを最初に数えると、
作業量の見積もりが立ちます。

**「変わらなかった」も立派な結果です。** 測らずに「たぶん大丈夫」で閉じないでください。
