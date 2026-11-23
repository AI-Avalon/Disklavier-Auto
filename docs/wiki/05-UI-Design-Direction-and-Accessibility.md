# 05. UIアートディレクション・書体選定・アクセシビリティ規約

## 1. `RNA4219/anything-frontend-art-direction` の題材起点適用

本プロジェクトの全Web画面（`src/ui/studio.html`, `src/ui/stage_hud.html`, `web-demo/`）は `.agents/skills/anything-frontend-art-direction/`（[設計詳細: `docs/DESIGN_DIRECTION.md`](https://github.com/AI-Avalon/Disklavier-Auto/blob/main/docs/DESIGN_DIRECTION.md)）の手順および 247 作品の書体・レイアウト調査データに基づいて設計されています。

### 題材定義
> **「アコースティックの生演奏者（チェロ / ピアノ）と自動演奏グランドピアノ Disklavier が、舞台上で互いの呼吸と和声を聴き合い論理的に共作する」**

### 書体選定とタイトル書体2案比較

247作品の調査データ（`usage-depth.csv` / `findings.csv`）における和文UI・舞台美術・精密計器の知見から選定しました。

| 役割 | 採用書体 | 選定根拠 |
| --- | --- | --- |
| **見出し・舞台タイトル・楽曲名** | `Shippori Mincho` + `Cormorant Garamond` | 明朝体の上品なセリフ骨格により「クラシック音楽の楽譜彫刻・室内楽プログラム」の格調を付与。数字と欧文楽語には `Cormorant Garamond` を優先割り当て |
| *(比較対照案・不採用)* | *`Zen Old Mincho` + `Georgia`* | *夜間公演向け和風イベント例では重厚で有効だが、西洋室内楽＋現代音響計測データの並列画面では字面が重く `Shippori Mincho` + `Cormorant Garamond` を採用* |
| **操作UI・ボタン・説明本文** | `Noto Sans JP` | 和文 UI 標準としての高い可読性と 13px〜15px 域での破綻のなさ |
| **F0 / RTT / MIDI / Score数値** | `JetBrains Mono` (`tabular-nums`) | 等幅・等幅数字 (`tabular-nums`) により 60fps 更新中も数値の横ブレを防止 |

## 2. 舞台UI 5大設計規約（Playwright 自動監査対象）

1. **枠線・影ゼロ構成**: カード・タイルの4辺囲み枠線および `box-shadow` を禁止し、下線 1px (`border-bottom`) と余白のみで情報階層を構成。
2. **グラデーション無し単色グレー背景**: 舞台照明下で過度な発光を防ぐ `#1C1D20`（および F10 投影クリーン時は `#141517`）のフラット単色。
3. **固定順序・薄背景幾何図形**: `:nth-child` で固定された SVG 幾何要素を `opacity <= 0.08` で配置。
4. **自動並び替え・DOM破壊ゼロ**: リアルタイム更新中に案A〜Dカードや6段階論理思考トレースの要素順を自動入替・再生成せず、5秒間 `CLS < 0.01` を維持。
5. **最小可視フォント 13.0px 以上 ＆ 44px 以上操作対象**: スクリーンリーダー専用ラベル `.sr-only` を含め計算済みフォントサイズ `>= 13.0px` を強制し、スマホ (`390px` ＆ `320px`) でも横スクロール `0px`・ナビ `>= 48x48px`・`UNA CORDA` (左) / `DAMPER` (右) 配列を維持。

## 3. アクセシビリティ（`checklists/` 全項目準拠）

- **スキップリンク ＆ ランドマーク**: `.skip-link`（キーボード Tab で最上部に出現 → `<main id="main-content">` へ移動）。
- **フォーカストラップ ＆ 復帰**: 操作ガイドモーダル (`#help-modal`) 開放時は閉じるボタン (`#btn-help-close`) へ自動フォーカスし、`Tab` 循環および `Esc` クローズ時に元の起動ボタン (`#btn-help`) へフォーカスを復帰。
- **非同期競合防止**: 100曲ライブラリ選択時に高速連打されても `AbortController` により古い通信を即キャンセル。
- **モーション軽減対応**: `@media (prefers-reduced-motion: reduce)` により OS 設定が縮小モーションの場合はすべての CSS トランジション時間を実質ゼロ (`<= 0.002s`) へ抑制。
