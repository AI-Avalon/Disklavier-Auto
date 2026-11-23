# DESIGN_DIRECTION.md — 題材起点のアートディレクション設計記録

本ドキュメントは、`.agents/skills/anything-frontend-art-direction/` の設計指針（`SKILL.md`、`references/visual-direction.md`、`references/visual-composition.md`、`references/visual-craft.md`、`data/typography/`、`checklists/`）に基づき、**DISKLAVIER STUDIO / STAGE HUD / WebAudio公開デモ** の視覚表現・書体選定・余白・スマホ再配置・検証基準を記録した一次設計書である。

---

## 1. 題材・主役・利用者の判断定義

| 項目 | 設計定義 |
|---|---|
| **題材** | 生演奏（チェロ / 人間ピアノ）と自動演奏ピアノ（Yamaha Disklavier）が舞台上で論理的に対話し共作する室内楽スタジオ |
| **主役（Primary Subject）** | ① **リアルタイム大譜表（五線譜＋物理打鍵・ペダル位置）**、② **演奏家の事実ベース解釈メモ（`monologue_ja`）**、③ **4候補並列スコアと6段階論理思考トレース** |
| **利用者・動作** | **演奏家・音響エンジニア**がリハーサル・本番中に「今どの音が検出され、なぜAIが案Aを選び案B〜Dを棄却したか、左右ペダルと接続先（自宅RTX 3070 / 会場M1 Mac）が安全に動いているか」を瞬時に判断し、必要に応じて指示入力・Retake・緊急PANICを実行する。 |
| **舞台投影時の条件** | 客席・奏者の視界を妨げない低輝度単色地（`#1C1D20` / F10投影 `#141517`）、AI特有のネオン発光・サイバーパンク表現・過剰装飾の排除。 |

---

## 2. 参照見本と採用/棄却理由（`references/` 及び `data/typography/` 実査）

`SKILL.md` の手順に従い、以下の見本（制作説明および実画面・調査台帳記録）を参照し、構造の根拠と字・地・線の根拠を区別して適用した。

1. **Linear — 境界線の抑制と視認順序（`references/visual-direction.md`）**
   - **観察・判断**: 繰り返し操作する作業画面において、読みたい主役（五線譜・数値・本文）より先に目に入る装飾枠・境界線を削減し、位置・余白・下線のみでまとまりを構成する。ただし入力欄や `:focus-visible` の輪郭は明確に残す。
   - **適用**: 既存の5大制約①（四方枠線と影の完全撤廃、下線1本 `1px solid #34373E` と余白のみ）を維持し、アクティブ状態・緊急操作のみ高対比下線（`3px solid #E85E4B` / `#EAE6DF`）で示す。

2. **ZENBI / National Museum of Women in the Arts — 朱の限定配置と明朝の見出し（`references/visual-direction.md`）**
   - **観察・判断**: 作品や主題を引き立てるために周囲の色数を絞り、明朝体で文脈の格調を示しつつ、アクセント色（朱）を「どこに面積を使うか」で意味を分ける。
   - **適用**: 朱色（ヴァーミリオン `#E85E4B` / `#B53524`）は **現在発音・選択タブ・棄却警告・PANIC** のみに限定し、地は単一フラットグレー `#1C1D20` に統一する。

3. **Ishimura+Neichi（`JP-TAKEMURA-ISHIMURA-NEICHI-2022`）＆ 247作品書体調査データ（`data/typography/findings.csv`, `usage-depth.csv`）**
   - **台帳データ確認**:
     - `JP-WEB-YAKAI-SHIBARIKYU-2025`（旧芝離宮夜会 ひかりめぐる庭）: `rendered_font = Zen Old Mincho`
     - `JP-WEB-UNIQLO-THERMOART` / `JP-COMM-LINX-CAMERA-2023`（比較UI）: `rendered_font = Noto Sans JP`
     - `JP-TAKEMURA-ISHIMURA-NEICHI-2022`: 和文と欧文の字高・字間の調律による混植設計
     - `TYPE-SHINMAI-2009` / `UCHICAFE-CHASHI-2022`: 用途（見出しの声 vs 小サイズ数値・品目の照合）による明朝・ゴシックの役割分離

---

## 3. 書体選定とタイトル書体 2案比較根拠

`references/visual-craft.md`（「和文と欧文を合わせる」）に従い、見出し（楽譜・幕構成・解釈メモ）と照合情報（操作ボタン・単位付きミリ秒・MIDI値・論理トレース数値）で役割を明確に分離した。

### 3.1 見出し・タイトル・解釈メモ書体 2案比較

同一テスト文言（和欧混植・単位数値含む実文）:
`「第1幕 問いかけ — D minor / チェロD3→F3レガート検出、案A (94.2%) 採用」`

| 比較項目 | 案A（採用）: **`Shippori Mincho` + `Cormorant Garamond`** | 案B（比較・不採用）: **`Zen Old Mincho` + `Georgia`** |
|---|---|---|
| **設計意図（原典）** | `Shippori Mincho` は東京築地活版製造所の五号系活字をベースにした明朝体で、仮名の抑揚と筆致の余韻がある。`Cormorant Garamond` は古典楽譜原典版（Urtext）のタイトル組版に近い鋭いセリフと縦横のコントラストを持つ。 | `Zen Old Mincho` は現代的な均整を持ち、漢字と仮名の大きさ・余白を均一化する設計。`Georgia` は標準的なWebセリフで幅がやや広い。 |
| **実内容の視認差** | 仮名（「問いかけ」「を」）の運筆に表情が残り、楽譜タイトルや演奏家の独白（20〜30px）で室内楽プログラムのような静かな緊張が生まれる。`Cormorant Garamond` の大文字 `DISKLAVIER STUDIO` と並んだ際に縦の線が引き締まる。 | 整然として読みやすいが、均等な骨格のため舞台上の見出しとして少し平易・汎用サイト寄りに見え、クラシック楽譜タイトルの緊張感が弱まる。 |
| **小サイズ・画数・フォールバック** | 最小見出しでも `15.5px` 以上で使用し、画数の多い漢字（「響」「鍵」「響板」）の潰れを回避。未読込・オフライン時は `"Yu Mincho", "Hiragino Mincho ProN", "Noto Serif JP", Georgia, serif` へ安全縮退。 | 同様に高可読だが、舞台エディトリアルの主役性を優先し案Aを採用。 |

### 3.2 役割別フォントスタック（`tabular-nums` 統一）

- **見出し・幕構成・解釈メモ（`--font-serif`）**:
  `"Cormorant Garamond", "Shippori Mincho", "Yu Mincho", "Hiragino Mincho ProN", "Noto Serif JP", Georgia, serif`
- **操作ボタン・フォーム・案内本文（`--font-sans`）**:
  `"Noto Sans JP", "Hiragino Kaku Gothic ProN", "Yu Gothic UI", "Meiryo", sans-serif`
  - 根拠: `JP-COMM-LINX-CAMERA-2023` 等の比較UI観測でも実描画確認済みの `Noto Sans JP` を採用。13px〜15pxのタブ・ラベル・フォーム入力で高い判別性を確保。
- **リアルタイム計測値・トレースJSONキー・F0/ms/VRAM数値（`--font-mono`）**:
  `"JetBrains Mono", "Cascadia Mono", "Consolas", monospace`
  - 全数値コンテナに `font-variant-numeric: tabular-nums;` を適用し、60fps更新時の桁揺れ・横幅変動（CLS）をゼロ化。

---

## 4. 余白設計・PC→スマホ再配置（`references/visual-composition.md`）

1. **近接と余白のルール**:
   - 関連情報（ラベルと値・ペダルの左右対）は `gap: 8px–12px` で密着させ、機能群（知覚 vs 大譜表＋候補 vs 操作バー）の間には `18px–24px` の余白＋1px下線区切りを確保。
   - **物理ペダル空間順序**: 左 `UNA CORDA (CC#67)` ／ 右 `DAMPER (CC#64)` をPC・スマホ問わず左右1列2分割（`grid-template-columns: 1fr 1fr`）で物理ピアノと一致維持。
2. **幅 `1920px` → `1366px` → `390px` → `320px` の再配置**:
   - `> 1180px`: 2カラム分割（左：入力・知覚・ペダル / 右：ヒーロー大譜表 or 6段階論理思考トレースタブ＋4候補）。
   - `<= 768px`（スマホ `390x844` / 最小 `320px`）:
     - 1カラム縦積み、詳細メトリクスは `<details class="mobile-accordion">` で初期格納して大譜表・主要操作がファーストビューに残る構成。
     - モード切替は固定 `56px` ボトムナビ（4ボタン、各 `>= 48x48px` タップ領域）へ移動。
     - 全要素 `min-width: 0; overflow-wrap: anywhere;` により横スクロール **0px** を厳守。

---

## 5. チェックリスト達成状況（`checklists/` ＋ 5大デザイン規約）

| チェックリスト / 規約 | 検証内容 | 実装箇所 |
|---|---|---|
| **`checklists/accessibility.md`** | ① スキップリンク（`.skip-link` → `#main-content`）、② 全入力/セレクトへの明示的 `<label>`（placeholder依存ゼロ）、③ キーボードTab順序と明瞭な `:focus-visible` 輪郭、④ モーダル開閉時のフォーカス移動＆`Esc`復帰、⑤ `320px` 幅で横溢れ 0px | `studio.html`, `stage_hud.html`, `web-demo/index.html`, `style.css`, `app.js` |
| **`checklists/forms.md`** | 全入力欄・モード切替・Hybrid接続セレクト・NotaGen設定欄に `for`/`id` 対応 `<label>` を付与し、ボタン・セレクトの意味を明示 | `studio.html`, `stage_hud.html`, `web-demo/index.html` |
| **`checklists/motion.md`** | `@media (prefers-reduced-motion: reduce)` で全トランジション・アニメーションを無効化し、静止状態でも状態（採用・棄却・接続先・ペダル値）が文字と下線で判別可能 | `style.css`, `stage_hud.html` |
| **`checklists/performance.md`** | `fonts.googleapis.com`（`display=swap`）利用、`AbortController` による非同期曲切替の古い応答破棄、オフスクリーンCanvasキャッシュ、`CLS < 0.01`（全カード/トレース行インプレース更新） | `app.js`, `style.css`, `stage_hud.html` |
| **5大デザイン規約（既存厳守）** | ①四方枠線と影ゼロ（下線1本のみ）、②背景グラデーション無しの単色フラットグレー（`#1C1D20` / `#141517`）、③DOM兄弟順 `:nth-child` 固定の薄い幾何学SVG（`opacity: 0.05`）、④カード・トレース行の自動入替禁止、⑤全可視テキスト `>= 13.0px`（`.sr-only` 含む） | `style.css`, `stage_hud.html`, `scripts/playwright_audit.py` |
