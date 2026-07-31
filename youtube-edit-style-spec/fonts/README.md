# フォント索引（`~/Library/Fonts/`）

`fonts/meta.json` は `~/Library/Fonts/` 実在191ファイルを fontTools で実測し、
family / ウェイト / 日本語対応可否 / 可変フォント可否 / ライセンスを索引化したもの。
OSS [AkariLabs/akari-video](https://github.com/AkariLabs/akari-video) の `catalog/font/meta.json` 方式（SPDX・商用可否・AI学習可否を明記する索引）を移植した。

**この索引はカタログ（データ）であり、`common.py` は書き換えていない。** 実装反映は「4. common.py への反映提案」を参照。

## 1. 判定方法（必ず記録）

1. `uv run --with fonttools` で `fontTools.ttLib.TTFont` を開き、`name` テーブルの
   nameID 13（License Description）/ 14（License URL）を読む。
2. 文字列パターン一致でSPDX識別子を判定する（`SIL Open Font License` → `OFL-1.1`、
   `Apache License, Version 2.0` → `Apache-2.0`）。**一致しない場合は `null`（不明）のまま記録し、推測で埋めない。**
3. 日本語対応は cmap のひらがな・カタカナ・CJK統合漢字グリフ数で機械判定（ひらがな/カタカナ各20字以上、または漢字500字以上）。
4. 可変フォントは `fvar` テーブルの有無、またはファイル名の `[wght]` 等サフィックスで判定。

`meta.json` の各エントリの `license.source` に判定方法を必ず記録している
（`"font name table (nameID 13/14, fontTools実測)"` または `"不明（...）"`）。

## 2. 全体サマリ

- 総ファイル数: **191**（全て `.ttf`）
- 総ファミリー数: **111**（ウェイト違いをまとめた単位）
- 日本語対応: **64ファミリー**（138ファイル）／非日本語: **47ファミリー**（53ファイル）
- ライセンス判明: **110ファミリー**（OFL-1.1が105、Apache-2.0が5）
- ライセンス不明: **1ファミリー**（`Monoton` / 非日本語・装飾フォント）
- **日本語対応フォントは64ファミリー全件、ライセンス判明・商用利用可**（詳細は `wiki` ではなく本ファイルと監査報告を参照）

## 3. 日本語字幕に使えるフォント一覧（64ファミリー、全てライセンス判明・commercial_ok）

現行の `NotoSansJP`（`common.py` の `FONT_BOLD`/`FONT_REGULAR`）以外の選択肢。カテゴリ別に分類。

### ゴシック体（汎用・可読性重視）
| id | family | ウェイト | ライセンス |
|---|---|---|---|
| `noto-sans-jp` | Noto Sans JP（現行デフォルト） | bold, regular | OFL-1.1 |
| `bizudp-gothic` | BIZ UDPGothic | bold, regular | OFL-1.1 |
| `ibm-plex-sans-jp` | IBM Plex Sans JP | thin〜bold 7段階 | OFL-1.1 |
| `murecho` | Murecho | light, regular, bold | OFL-1.1 |
| `line-seed-jp` | LINE Seed JP | thin, regular, bold, extrabold | OFL-1.1 |
| `sawarabi-gothic` | Sawarabi Gothic | regular | OFL-1.1 |
| `zen-kaku-gothic-new` | Zen Kaku Gothic New | light〜black 5段階 | OFL-1.1 |
| `zen-kaku-gothic-antique` | Zen Kaku Gothic Antique | light〜black 5段階 | OFL-1.1 |
| `kosugi` | Kosugi | regular | Apache-2.0 |

### 丸ゴシック体（柔らかい・親しみやすい）
| id | family | ウェイト | ライセンス |
|---|---|---|---|
| `kiwi-maru` | Kiwi Maru | light, medium, regular | OFL-1.1 |
| `zen-maru-gothic` | Zen Maru Gothic | light〜black 5段階 | OFL-1.1 |
| `tsukimi-rounded` | Tsukimi Rounded | light〜semibold 5段階 | OFL-1.1 |
| `mplus-rounded-1c` | Rounded Mplus 1c（M PLUS Rounded 1c） | thin〜black 7段階 | OFL-1.1 |
| `kosugi-maru` | Kosugi Maru | regular | Apache-2.0 |

### 明朝体（クラシック・シネマティック・落ち着いた印象）
| id | family | ウェイト | ライセンス |
|---|---|---|---|
| `noto-serif-jp` | Noto Serif JP | bold, regular | OFL-1.1 |
| `bizudp-mincho` | BIZ UDPMincho | bold, regular | OFL-1.1 |
| `hina-mincho` | Hina Mincho | regular | OFL-1.1 |
| `sawarabi-mincho` | Sawarabi Mincho | regular | OFL-1.1 |
| `kaisei-decol` | Kaisei Decol | medium, regular, bold | OFL-1.1 |
| `kaisei-haruno-umi` | Kaisei HarunoUmi | medium, regular, bold | OFL-1.1 |
| `kaisei-opti` | Kaisei Opti | medium, regular, bold | OFL-1.1 |
| `kaisei-tokumin` | Kaisei Tokumin | medium〜extrabold | OFL-1.1 |
| `shippori-mincho` | Shippori Mincho | medium〜extrabold 5段階 | OFL-1.1 |
| `shippori-mincho-b-1` | Shippori Mincho B1 | medium〜extrabold 5段階 | OFL-1.1 |
| `zen-old-mincho` | Zen Old Mincho | medium〜black 5段階 | OFL-1.1 |
| `zen-antique` | Zen Antique | regular | OFL-1.1 |
| `zen-antique-soft` | Zen Antique Soft | regular | OFL-1.1 |
| `shippori-antique` | Shippori Antique | regular | OFL-1.1 |
| `shippori-antique-b-1` | Shippori Antique B1 | regular | OFL-1.1 |
| `new-tegomin` | New Tegomin | regular | OFL-1.1 |

### 手書き・筆文字風（感情的・パーソナルな演出）
| id | family | ウェイト | ライセンス |
|---|---|---|---|
| `yomogi` | Yomogi | regular | OFL-1.1 |
| `klee-one` | Klee One | regular, semibold | OFL-1.1 |
| `zen-kurenaido` | Zen Kurenaido | regular | OFL-1.1 |
| `yuji-boku` | Yuji Boku（筆・墨） | regular | OFL-1.1 |
| `yuji-mai` | Yuji Mai（筆） | regular | OFL-1.1 |
| `yuji-syuku` | Yuji Syuku（筆） | regular | OFL-1.1 |
| `shizuru` | Shizuru | regular | OFL-1.1 |

### ポップ・装飾（見出し・キャラクター演出向け、字幕本文には不向き）
| id | family | ライセンス |
|---|---|---|
| `mochiy-pop-one` / `mochiy-pop-p-one` | Mochiy Pop One / P One | OFL-1.1 |
| `hachi-maru-pop` | Hachi Maru Pop | OFL-1.1 |
| `dela-gothic-one` | Dela Gothic One | OFL-1.1 |
| `rampart-one` | Rampart One | OFL-1.1 |
| `reggae-one` | Reggae One | OFL-1.1 |
| `darumadrop-one` | Darumadrop One | OFL-1.1 |
| `cherry-bomb-one` | Cherry Bomb One | OFL-1.1 |
| `rockn-roll-one` | RocknRoll One | OFL-1.1 |
| `yusei-magic` | Yusei Magic | OFL-1.1 |
| `otomanopee-one` | Otomanopee One | OFL-1.1 |
| `potta-one` | Potta One | OFL-1.1 |
| `monomaniac-one` | Monomaniac One | OFL-1.1 |
| `slackside-one` | Slackside One | OFL-1.1 |
| `chokokutai` | Chokokutai | OFL-1.1 |
| `stick` | Stick | OFL-1.1 |
| `train-one` | Train One | OFL-1.1 |
| `aoboshi-one` | Aoboshi One | OFL-1.1 |
| `palette-mosaic` | Palette Mosaic | OFL-1.1 |
| `rock-3-d` | Rock 3D | OFL-1.1 |
| `dot-gothic-16` | DotGothic16（ドット/レトロ端末風） | OFL-1.1 |
| `wdxl-lubrifont-jpn` | WDXL Lubrifont JP N | OFL-1.1 |

### 等幅・テック系
| id | family | ライセンス |
|---|---|---|
| `mplus-1-code` | M PLUS 1 Code（等幅） | OFL-1.1 |
| `mplus-1` / `mplus-2` / `mplus-1p` / `mplusu` | M PLUS 1 / 2 / 1p / U 系 | OFL-1.1 |

## 4. スタイル別の推奨（既存15スタイルyaml読了の上での提案）

**前提**: 現行15スタイルyaml全件を確認したところ、`caption_font` フィールドは全て
「基準プリセット既定値（Noto Sans JP相当・フォント92px/黒フチ12px）」を基準とし、
実測動画のフォント名自体は解像度制約（多くが360p）で特定不可能だった、と記録されている。
つまり**現行yamlはどれも別書体を指定していない**。以下は「今後こうした演出を追加したい場合の選択肢」の**提案**であり、
yaml書き換えは行っていない（指示通り）。

| スタイル | 意匠の要点（yamlより） | 提案フォント | 用途 |
|---|---|---|---|
| `documentary_cinematic` | シネマティック回顧型。重厚な引きの絵とタイトルカード演出 | `zen-old-mincho`（黒に近いウェイトで見出し）／`kaisei-haruno-umi`（本文・字幕の上品さ） | タイトルカード・章題テロップ |
| `documentary_immersive` | 体験密着型。B1/B2/B3で音響・文法が分かれる一枚岩でないジャンル | `zen-antique-soft`（B2系のナレーション的な柔らかさ）／B3系は現行デフォルト継続を推奨 | 内省的なナレーションテロップ |
| `documentary_investigative` | 調査報道型。モードA/Bの証拠提示構造、証拠見出しキャプションは通常より大きい | `noto-serif-jp` または `bizudp-mincho`（公文書的な硬さ）／見出しスタンプは `zen-kaku-gothic-new`(black) | 証拠見出しキャプションの権威付け |
| `documentary_narrated_jp` | 日本語ナレーション系。D1(事実要約・人物名)/D2(無装飾)で分岐 | D2系（装飾性を持たない）に `murecho` や `zen-kaku-gothic-antique`（控えめな人文フォント） | D2系の落ち着いたテロップ |
| `documentary_startup_journey` | build in public型。スタートアップ密着、6変種 | `mplus-2` または `mplus-rounded-1c`（SaaS/プロダクト系の現代的な質感） | プロダクト紹介・進捗テロップ |
| `ai_news_weekly` | AI活用系ニュース速報 | `dot-gothic-16`（速報・端末感のあるバッジ）／本文は現行デフォルト継続 | 速報バッジ・タグ |
| `business_talk` | BT1系ビジネス対談 | `bizudp-gothic`／`bizudp-mincho`（名称通りビジネス用途に設計されたUDフォント） | 名前プレート・肩書テロップ |
| `ai_biz_pitch` | AIスクール系。解説カード主体で視覚リズムが高い | `zen-maru-gothic` または `mplus-rounded-1c`（親しみやすさと可読性の両立） | 解説カード見出し |
| `educational_explainer` | 教育解説の標準形 | `zen-kaku-gothic-new`（日本語解説系YouTubeで定番の視認性） | 見出し・強調語 |
| `entertainment_variety` | バラエティ。手書きマーカー注釈（矢印+下線+緑手書き文字）が辞書外要素 | **`yomogi`**（手書きマーカー注釈に最適、または`klee-one`） | 手書きマーカー注釈の実装（v3手法候補） |
| `japan_vlog` | 日本紹介系vlog。控えめな意匠、広いダイナミックレンジ | `yomogi` または `kiwi-maru`（パーソナルな温かみ） | 個人的な一言コメント演出 |
| `kirinuki` | 切り抜き量産型。フル字幕・カット最小限・青太字 | 本文は現行デフォルト継続（速度優先）。強調語のみ `rockn-roll-one` 等の勢いのある書体を検討可 | 強調ワードのみ |
| `ai_tool_avatar` | AIチャンネル、解説カード＋二層構造（猫キャラ想定） | `hachi-maru-pop` または `mochiy-pop-one`（キャラクター吹き出し向け） | キャラクター吹き出しテキスト |
| `screen_tutorial` | 画面録画チュートリアル。ズーム不使用、赤系オーバーレイで操作箇所強調 | `ibm-plex-sans-jp` または `mplus-1-code`（等幅・技術文書的な精度感） | UI操作説明・コールアウト |
| `wipe_explainer` | ワイプ解説、2パターンの実装が必要 | `zen-kaku-gothic-new`（educational_explainerと同系統） | ワイプ枠内キャプション |

**運用ルール**: 上記はあくまで提案であり、実際に採用する場合は該当 `styles/*.yaml` に
`caption_font` の追記としてこうすけの承認を得た上で反映すること（本タスクでは yaml 未変更）。

## 5. ライセンス不明フォント（業務利用しない）

| ファイル | 状況 |
|---|---|
| `Monoton-Regular.ttf` | `name` テーブルに License Description(nameID13)/License URL(nameID14) レコードが存在しない。`fc-query` でも同様に取得不可。Copyright表記のみ（`Copyright (c) 2011 by vernon adams. All rights reserved.`）で、商用可否・再配布可否を裏付ける記述が本体に一切ない。 |

**運用ルール（必須）**: `license.spdx` が `null`（不明）のフォントは、確認が取れるまで
**納品物・商用動画に使用しない**。使いたい場合は Google Fonts 等の公式ページで
ライセンス文（OFL.txt 等）を人力で確認し、確認できた根拠（URL・確認日）を
`meta.json` の該当エントリの `license.source` に追記してから解禁すること。
推測でライセンスを埋めて解禁することは禁止。

なお、日本語対応64ファミリーは全件ライセンス判明・商用利用可のため、
**日本語字幕用途に限れば現時点で不明フォントを踏むリスクはない**（Monotonは非日本語の装飾フォント）。

## 6. `ai_training_ok` を全件 `unknown` にしている理由

OFL-1.1・Apache-2.0 いずれのライセンス文にも「AI学習への使用可否」を明記した条項がない
（両ライセンスとも2000年代〜2010年代前半に書かれたテキストで、AI学習利用を想定していない）。
「ライセンス文に書いていないから可」と拡大解釈するのは推測にあたるため、
本索引では機械的に `unknown` を維持する。AI学習用途（フォント自体を学習データにする等）が
発生した場合は都度、法務的な確認を挟むこと。動画の字幕描画（グリフのレンダリング表示）は
「AI学習」ではなく通常のフォント使用にあたるため、この制約の対象外。

## 7. common.py への反映提案（実装はしない・提案のみ）

現状 `~/Documents/編集作業/style_verify_20260725/scripts/common.py` は以下のようにパス直指定している：

```python
FONT_DIR = os.path.expanduser("~/Library/Fonts")
FONT_BOLD = os.path.join(FONT_DIR, "NotoSansJP-Bold.ttf")
FONT_REGULAR = os.path.join(FONT_DIR, "NotoSansJP-Regular.ttf")
```

### 提案A（最小変更・後方互換優先）
`meta.json` から `noto-sans-jp` エントリを引いて同じ2定数を組み立てるヘルパーを追加する。
既存呼び出し側（`FONT_BOLD`/`FONT_REGULAR` を直接参照している箇所）は無改修で済む。

```python
# 提案イメージ（未実装）
import json

def _load_font_meta():
    meta_path = os.path.expanduser(
        "~/kousuke-vault/.claude/skills/youtube-edit-style-spec/fonts/meta.json"
    )
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)

def resolve_font(font_id: str, weight: str) -> str:
    """meta.jsonからfont_idを引き、ライセンス不明フォントはassert Falseで拒否する"""
    meta = _load_font_meta()
    entry = next(f for f in meta["fonts"] if f["id"] == font_id)
    if entry["license"]["spdx"] is None:
        raise ValueError(f"{font_id} はライセンス不明のため使用不可（fonts/README.md参照）")
    path = entry["files"][weight]
    return os.path.expanduser(path)

FONT_BOLD = resolve_font("noto-sans-jp", "bold")
FONT_REGULAR = resolve_font("noto-sans-jp", "regular")
```

### 提案B（スタイル別フォント切替に対応・将来拡張向け）
`styles/*.yaml` に将来 `caption_font_id: zen-kaku-gothic-new` のような指定が追加された場合、
`resolve_font(style.caption_font_id or "noto-sans-jp", weight)` の形で呼び出し側から
スタイルごとに切り替えられるようにする。現時点ではどのyamlも書体を指定していないため、
今回は着手不要（YAGNI）。将来 `entertainment_variety` の手書きマーカー注釈実装など、
特定書体が必要になった時点で個別に追加するのが最小影響。

### 採用しない案
- フォントファイルを `assets/font/` 配下にコピーして同梱する案（AkariLabsの2026-07-23追記と同様の方式）は、
  今回は見送り。理由: `~/Library/Fonts/` は全メンバー共通のmacOS標準パスであり、
  同梱によるリポジトリ肥大化（TTF数十MB級）のメリットが薄い。ローカル環境前提が崩れる
  （他OS・CI環境での再現性が必要になった時点で再検討）。

**この提案はREADMEへの記載のみ。`common.py` 自体は今回のタスクでは書き換えていない。**
