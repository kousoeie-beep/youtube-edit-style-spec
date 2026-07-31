---
name: youtube-edit-style-spec
description: 素材（動画/音声/台本/画像）を渡すだけで基準プリセット式YouTube編集（本編/Shorts）を実行する。auto-youtube-edit-agent仕様書＋このMacの環境知見＋字幕品質v2（音声強調→複数パスASR照合→不確信語省略→語境界改行）を統合した実行スキル。「この動画を編集して」「基準プリセット式で」等の依頼で使用。Palmier編集の依頼は palmier-video-auto-edit を使う。
---

# 基準プリセット式 YouTube 自動編集（実行スキル）

## 正本

一次指示書は **auto-youtube-edit-agent 仕様書**。実行前に必ずクローンして全文読むこと：

```bash
git clone https://github.com/zettai-code/auto-youtube-edit-agent.git <scratchpad>/auto-youtube-edit-agent
# 読む: auto_youtube_edit_agent.md（カット基準/字幕規則/テロップenum/音声処理/サムネ5案/品質チェック/edit_log義務）
```

本スキルは仕様書に、①このMacの環境知見 ②字幕品質v2手順 ③納品運用ルール を上書き追加するもの。矛盾したら本スキルが優先（ローカル環境の実測に基づくため）。

## 実行体制

Fable(PM)=計画・検収・コミット／Sonnet(Executor)=実行（`Agent`ツール `model:'sonnet'` にBriefを渡す）。パイロット3本（2026-07-09〜10）で実証済み。

## 索引（本体はルーター。詳細は各leafへ）

このSKILL.mdは実行順序と参照先だけを持つ薄いルーター。**現在の工程に必要なleafだけを読み、後工程を先回りして読まない**。

### 毎回の編集で通る工程（パイプライン）

| 工程 | leaf | 扱う内容 |
|---|---|---|
| 字幕生成 | [captions-quality-v2.md](captions-quality-v2.md) | 音声強調→複数パスASR照合→不確信語省略→語境界改行 |
| 音響設計 | [audio-pipeline.md](audio-pipeline.md) | LUFS/LRAの交換関係、無音率が構造的に潰れる根本原因と正しい手順 |
| レンダリング前QC | [qc-gates.md](qc-gates.md) | `overlap_qc.py` が検出できない4パターン、`common.py` の既知バグ |

### 横断的インフラ（工程ではなく参照される知見）

| 領域 | leaf | 扱う内容 |
|---|---|---|
| 実行環境 | [environment-notes.md](environment-notes.md) | このMac固有のffmpeg/Pillow制約、強調テロップ視認性の確定数値、被りQC・被り予防の設計規則 |
| 納品運用 | [delivery-ops.md](delivery-ops.md) | nice値降格・scratchpad揮発性・作業ディレクトリ・納品先、品質実績（参考基準）、**このスキル自体を並列編集する際の注意** |

### 検証（スタイル定義が実装者に伝わるかを測る）

| 何 | どこ | 何のため |
|---|---|---|
| 手順の正本 | [verify/PROTOCOL.md](verify/PROTOCOL.md) | 「yamlだけを読んで作り直す」検証の手順・判定基準・報告の書き方 |
| 計測 | [verify/measure.py](verify/measure.py) | 合成後の実座標。**箱基準とインク基準を分けて出す**（混同が実害を生んだ） |
| 報告の型 | [verify/REPORT_TEMPLATE.md](verify/REPORT_TEMPLATE.md) | 節ごと追記式。途中で落ちても実測が残る |
| 静的検査 | [styles/lint.py](styles/lint.py) | 24検査。**周回を閉じる前に違反0を確認する** |

**実装（レンダラ）は正本化しない。** 毎周ゼロから書き捨てるのがこの検証の本体で、
共通レンダラを渡すと再実装が消えて何も測らなくなる。

### スタイル学習（「〇〇風で」対応。全体の最大の塊のため独立ディレクトリ）

こうすけがURL/ファイルを持ち込んで新規編集スタイルを学習・登録する依頼、または既存プリセットを適用する依頼は [style-learning/index.md](style-learning/index.md) を読む（薄いルーター。取得・分析・スキーマ・検証・整合性チェック・登録済み一覧へさらに分岐）。

## 実行順の目安

1. [正本](#正本)の仕様書を読む
2. 実行環境の制約を把握: [environment-notes.md](environment-notes.md)
3. APIキーの有無を確認: [api-keys.md](api-keys.md)
4. 「〇〇風で」の指定があれば [style-learning/registry.md](style-learning/registry.md) で登録済みスタイルを確認し、無ければ [style-learning/index.md](style-learning/index.md) から学習フローに入る。**どのスタイルを使うか迷う場合は [styles/_index.md](styles/_index.md) の横断比較表**（cuts/LUFS/LRA/無音方針/テロップ密度の一覧。**無音率が選定の第一軸**）
5. 字幕生成: [captions-quality-v2.md](captions-quality-v2.md)
6. 音響設計: [audio-pipeline.md](audio-pipeline.md)
7. レンダリング前QC: [qc-gates.md](qc-gates.md)（詳細な環境固有の被りQC手順は [environment-notes.md](environment-notes.md) にも記載）
8. 書き出し・納品: [delivery-ops.md](delivery-ops.md)

## 最重要ハードルール（詳細は各leaf）

- **APIキーの値をログ・レポート・チャットに出力しない**（運用規律は公開版に含めない）
- **QCゲート通過後も必ずフレームを目視すること。ツールの緑信号は目視の代替にならない**（[qc-gates.md](qc-gates.md)）
- **推測で字幕を作らない**。全パス不一致＋低確信語は省略し記録する（[captions-quality-v2.md](captions-quality-v2.md)）
- **scratchpadは数時間で消える**。作業ディレクトリは `~/Documents/編集作業/<案件名>/` に置く（[delivery-ops.md](delivery-ops.md)）
- **`styles/*.yaml` を更新したら「同じ主張をしている他のセクション」を必ず全部直す**（[style-learning/consistency-check.md](style-learning/consistency-check.md)）
