# Auto YouTube Edit Agent

素材を入れるだけで、YouTubeの**本編（長尺）とShortsの両方**を自動編集するためのマスター指示書。
このリポジトリは、Claude Code / Codex / 動画生成パイプライン実装エージェントに渡して実行する指示書 [`auto_youtube_edit_agent.md`](./auto_youtube_edit_agent.md) を管理します。

## これは何か

入力素材（動画・音声・台本・画像）から、そのまま公開できる完成品に近い動画を自動生成するエージェントの仕様書です。Shortsの短尺編集に限定せず、10分以上の解説・チュートリアル・対談・実演・レビューなどの**長尺編集を第一級のユースケース**として扱います。

## 自動生成するもの

- 無音・噛み・言い直し・不要シーンをカットした完成動画（本編/Shorts両対応）
- 発音・発声に忠実に同期した日本語字幕
- タイトル、章タイトル、ツールカード、強調テロップ
- イントロ（フック演出）とアウトロ（CTA、次動画誘導）
- グラフ・表・図解・数値ビジュアルの生成と挿入
- 必要箇所のB-roll / 背景映像の生成と挿入
- ナレーション生成、BGM/SFX、音量統一
- サムネイル5案、タイトル案、説明文、チャプター、固定コメント
- 公開後の改善用分析メモ

## 想定コマンド

```bash
/chaen-youtube-edit <素材動画/音声/画像...> --script <台本.md optional> --mode auto --out ./dist
```

例:

```bash
/chaen-youtube-edit 冒頭.MOV 本編.mp4 台本.md --mode youtube-short --out ./dist/episode_001
```

## 使い方

1. [`auto_youtube_edit_agent.md`](./auto_youtube_edit_agent.md) を実装エージェント（Claude Code など）に渡す
2. 素材と（任意で）台本を入力として指定する
3. `dist/` に完成動画・字幕・公開パックが出力される

詳細な方針・入出力仕様・パイプラインは [`auto_youtube_edit_agent.md`](./auto_youtube_edit_agent.md) を参照してください。
