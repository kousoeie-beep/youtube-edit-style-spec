# 基準プリセット式 編集スタイル定義 — チューニング引き継ぎ一式

2026-07-31 時点。**受け取った側がやるのは「チューニングの周回を回すこと」**です。

## これは何か

15本の編集スタイルを **機械可読な仕様（yaml）** として書いたもの。
「yaml だけを読んだ人が、同じ絵を作れるか」を11周検証して、外れた箇所を潰し続けています。

**まだ完成していません。** 現在の可否は `styles/_index.md` の「現行ステータス」を正本とします。
修正後に再検証されていないスタイルは「未検証」と扱い、残件を「可」にするのがこの引き継ぎの目的です。

## 中身

```
youtube-edit-style-spec/
  SKILL.md                    ← 入口。ここから読む
  styles/*.yaml               ← 仕様（15本）。これが本体
  styles/lint.py              ← 静的検査26個。周回を閉じる前のゲート
  styles/_index.md            ← 15本の一覧・可否表・充足率
  style-learning/schema.md    ← 規約の正本。11周分の失敗が全部ここに書いてある
  verify/PROTOCOL.md          ← 検証の手順・判定基準・報告の書き方
  verify/measure.py           ← 合成後の実座標を測る
  verify/yamledit.py          ← yaml を安全に編集する（重複キーを作らない）
  verify/REPORT_TEMPLATE.md   ← 報告書の型（節ごと追記式）
  qc-gates.md                 ← QC が原理的に見られないもの
  environment-notes.md        ← レンダリングの実行要件
  audio-pipeline.md / captions-quality-v2.md / delivery-ops.md ほか
  fonts/meta.json             ← 使えるフォントの一覧（実ファイルは含まない）
overlap_qc/overlap_qc.py      ← 被りQCゲート本体（スキル外にあるので同梱した）
```

## 入っていないもの（意図的）

| 何 | なぜ |
|---|---|
| **レンダラ（実装）** | **毎周ゼロから書き捨てるのがこの検証の本体**。共通レンダラを渡すと再実装が消えて、検証が何も測らなくなる（`verify/PROTOCOL.md` §0） |
| 認証情報の類 | 秘密情報は一切入れていない。該当ドキュメントは「扱いの規律」だけで値を持たない |
| フォントの実ファイル | 各自インストール。`fonts/meta.json` に必要な family が載っている |
| 実素材（動画・音声） | 別途 |
| 過去の検証成果物 | 153本の使い捨てスクリプト・中間動画。再現に不要 |

## 動かすのに要るもの

- Python 3 + `uv`
- `ffmpeg` / `ffprobe`
- フォント（最低 `NotoSansJP-Bold` / `NotoSansJP-Regular`。`fonts/meta.json` 参照）
- Pillow / numpy / PyYAML / fugashi / unidic-lite（`uv run --with` で都度入る）

動作確認:

```bash
cd youtube-edit-style-spec
uv run --with pyyaml python3 styles/lint.py
```

「クリーン」と表示されれば違反ゲートは通過しています。`申し送り N 件` は終了コードには影響しませんが、次周の未解決事項です。

## 1周の回し方

`verify/PROTOCOL.md` が正本です。要約すると:

1. **yaml のフィールドだけを読んで、対象スタイルをゼロから実装する**（散文は根拠に使わない）
2. 25秒のクリップをレンダリング（対照窓を作るなら本編と時間で分ける）
3. `overlap_qc.py` を通す（本編と対照窓で config を分ける）
4. `verify/measure.py --boxes` で宣言矩形、`--work` で実インクを測る
5. フレームを最低6点、目視する
6. **可 / 不可** を判定して報告書に書く（判定基準は PROTOCOL §4。「ほぼ可」は書かない）
7. 修正は `verify/yamledit.py` の `set_field()` を通す
8. **`styles/lint.py` で違反0を確認してから次の周へ**

## 最初に読むべき3つ

1. `verify/PROTOCOL.md` — やることの全部
2. `style-learning/schema.md` の**末尾から**（新しい追記ほど重い）
3. `styles/_index.md` の可否表 — どのスタイルが何周目で何を残しているか

## いま分かっている残件

- **「size だけあって中身（font_px / stroke_px）が無い」文字role が10件。** これが最大の塊。
  実素材で測定して埋めると派生値の連鎖が起きるため、隣接roleまで再導出して再検証する
- 現在の可否は `styles/_index.md` を正本とし、修正直後の状態は必ず「未検証」と扱う

## 引き継ぐ相手への一番の忠告

**規約を足したら、その周のうちに `lint.py` に検査も足すこと。**
検査の無い規約は、書いた本人が翌周に破ります（11周で何度も実演しました）。
