# Disklavier-Auto 全コード監査 ＆ 3周自律改善ログ (`AUDIT_AND_IMPROVEMENT_LOG.md`)

本ドキュメントは、リポジトリ全コード（`src/`, `scripts/`, `tests/`, `docs/`, ルート `.bat` 群, `web-demo/`）に対する網羅的監査で特定・修正した技術的負債・不具合、**100曲高精度作曲＋古典和声リアライザー**による定量的音楽改善結果、および **Playwright 実ブラウザ視覚検査 × 音響和声分析の3周連続改善ループ（Round 1 → Round 2 → Round 3）** の全記録です。

---

## 1. 全コード精査：不具合・技術的負債の完全整理と修正実績

| 分類 | 対象ファイル・箇所 | 検出された根本原因（問題） | 実施した構造修正・恒久対策 |
|---|---|---|---|
| **Windows文字重複バグ** | `setup_environment.bat`, `start_stage.bat`, `run_demo_showcase.bat`, `run_self_check.bat`, `src/stage/cli_banner.py` | `chcp 65001 >nul` + `PYTHONIOENCODING=utf-8` による cp65001 全角CJK文字幅計算ずれ、および 72桁超バナーの折り返しにより `起起動動直直後後...` と文字化け・二重表示が発生。 | 全 `.bat` から `chcp 65001`・`PYTHONIOENCODING` を完全撤廃し純粋ASCII化。`cli_banner.py` は TTY接続時に Win32 API (`ctypes.windll.kernel32.WriteConsoleW`) 直接出力＋パイプ時 UTF-8生出力＋全行表示幅 `<= 72` 桁へ強制統一。 |
| **重複・肥大ファイル整理** | `.agents/`, `skills/`, `docs/demos/*.wav`, `.gitignore`, `pyproject.toml` | `.agent/skills/` と同一内容の重複ディレクトリ `.agents/`, `skills/` が残存。19.3MB 超の非圧縮 `.wav` 2本がリポジトリを圧迫。`pytest` 直実行時に `ModuleNotFoundError: No module named 'src'`。 | 重複スキルディレクトリと 19.3MB WAV 2本を `git rm` 削除し、`.gitignore` へ `.wav`（`!*.mp3`, `!*.mid`, `!*.musicxml`）と一時スクリーンショット領域を追加。`pyproject.toml` に `pythonpath = ["."]` と `sounddevice` 警告フィルタを追加。 |
| **FastAPI / WS 堅牢化** | `src/stage/stage_server.py` | ① `turn` / `preset` / `calibrate` / `retake` / `synthesize_work` が FastAPI イベントループを同期ブロック。② WS 配信が `wait_for(timeout=0.5)` タイムアウト時に正常ソケットまで `clients` から誤破棄し、同期 `TestClient` 等で受信ハングを起こす。③ `turn_ready=True or ready` デッドコード。④ `_lock` 保持中に `nvidia-smi` 呼び出し（Round 2 でデッドロック特定）。 | ① 全重負荷エンドポイントと lifespan を `asyncio.to_thread()` へオフロード。② 33fps 配信で `TimeoutError` 時はフレームスキップのみ行い接続を維持、実切断時のみ掃除＋指数バックオフ再接続。③ GPU VRAM 照会を専用デーモンスレッドへ分離し `get_hud_state()` をロック内 O(1) 化。④ `/studio`, `/api/catalog`, `/api/pieces/{id}`, `/audio/{filename}` にパストラバーサル拒否を追加。 |
| **音響クリック＆メモリ制御** | `src/stage/local_audio_synth.py` | バッファ両端のゼロ交差ズレによる微小クリック、`_HALL_IR_CACHE` 無制限増加、中間 WAV 前提の肥大書き出し。 | `_apply_edge_fades()` による立ち上がり/立ち下がりレイズドコサインフェード導入、`_HALL_IR_CACHE` 最大8エントリ上限管理、および `lameenc` による 128kbps ステレオ MP3 直接エンコード化。 |
| **Preflight キー不整合・ライブ候補協和修復** | `src/stage/harmonic_beauty_realizer.py`, `src/stage/layer3_candidate_composer.py` | ① `LocalPreflightChecker` が `"note"` / `"time_offset_s"` キーを参照し `control==67` を別経路扱いするため、初期テストでノートが空配列になる問題。② ライブ対話ターンの `_repair_to_polyphonic_standard()` が `resolve_vertical_clashes()` を再適用せずスケール量子化シフトしたため協和率が低下する問題。 | ① `pitch`/`note` と `onset_s`/`time_offset_s` 両キーを常に格納し、Preflight にはピアノ発音＋`CC#64` を渡して物理安全監査後に連続 `CC#67` を統合。② `realize_2bar_candidate()` に人間音域クリアランス＋2重衝突解決を組み込み、ライブ修復後にも和声衝突解決＋境界同期ペダルを再適用して全10反復で協和率 `>= 90` を保証。 |

---

## 2. 音楽的抜本刷新：改善前（ベースライン）vs 改善後（100曲ライブラリ）定量比較

旧生成デモ MIDI（`HarmonicBeautyRealizer` 導入前）と、Ollama（`qwen3.5:4b`, `keep_alive=-1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`）× `TokenEconomizer` × `HarmonicBeautyRealizer` × `BeautyHarmonyAuditor` で作曲・厳選した全100曲の実測値です：

| 評価指標 | 改善前 Mode A 基準MIDI | 改善前 Mode B 基準MIDI | 刷新後 100曲平均 (`Min..Max`) |
|---|---:|---:|---|
| **休符以外の単音率 (Monophonic Slice %)** | `24.3%` | `25.9%` | **`1.12%`**（全曲最大 `1.70%` / 目標 `< 10%`） |
| **同時発音数 (Avg Polyphony)** | ~2.1 音 | ~2.2 音 | **`5.01 音`**（目標 `>= 2.5 音`） |
| **無根拠不協和ペア率 (Dissonant Pair %)** | `10.9%` | `9.2%` | **`0.65%`**（基準 `<= 2.5%`） |
| **協和率スコア (Consonance Score / 100)** | ~72 点 | ~76 点 | **`97.05 点`**（全曲 `>= 90.0`） |
| **美しさ総合スコア (Beauty Score / 100)** | — | — | **`97.75 点`**（最小 `95.07 点` / 合格線 `88.0`） |
| **LLM 自律作曲設計比率** | 一部ルールフォールバック | 一部ルールフォールバック | **`100 / 100 曲` (`ollama:qwen3.5:4b`)** |
| **代表10曲 MP3 音圧ばらつき (dBFS)** | — | — | **Round 1: `8.0 dB 差` → Round 2/3: `0.23 dB 差`** (`-17.66 .. -17.89 dBFS`, `clipping=0`) |

---

## 3. Playwright 実ブラウザ視覚検査 ＋ 音響和声分析「3周連続改善ループ」

毎ラウンドで実ブラウザ（Chromium Headless `1920x1080` & `1280x720`）のスクリーンショットを視覚検査し、同時に100曲データと10本のデコード済MP3実測値を監査して、以下の5軸（100点満点）で厳格に採点・改善しました：

### 5軸評価スコア推移サマリー

| 評価軸 | Round 1 | Round 2 | Round 3（最終） | 主な改善ポイント |
|---|---:|---:|---:|---|
| **1. 和声美しさ・機能和声表記正確性** | 84 | 96 | **98** | 7th/9th転回形数字付き低音 (`Imaj76`→`Imaj65`) 修復、`spread` 整合性修正 |
| **2. 実MP3 / WebAudio 音響品質** | 78 | 93 | **97** | Mode A/B 間の 8dB 音量差を 0.23dB 以内へ統一；WebAudio に 1.25s ステレオ残響＋3次倍音＋パン分離実装 |
| **3. UI 明瞭性・1280×720 画面設計** | 76 | 92 | **97** | 1280×720 で大譜表全体が縦スクロール不要で収まるよう最適化；大譜表ブレイス・加線・符尾・幕ラベル・クリックシーク実装；`TEXTURE_JA` 日本語化 |
| **4. `/stage` 舞台投影・堅牢性** | 81 | 94 | **98** | `_lock` 内 VRAM照会デッドロック根本解消；Zone D スネークケース撤廃＆桁切れ修正；`VRAM 1.1 GB` 実表示；棄却カード視認性改善 |
| **5. 日本語タイトル・解説文規約準拠** | 74 | 90 | **97** | プロンプトエコー（`事実記述のみ`）21件・仰々しい語句（`叫び`/`嘆き`/`暗黒`/`大調`/欠落`調`）26件を室内楽規範語彙へ完全浄化 |
| **総合平均 (5軸平均 / 100)** | **78.6** | **93.0** | **97.4** | 全6チェック項目 `console=0, pageerror=0, http=0, overflow=0px, overlaps=0` 達成 |

---

### Round 1（初回全面監査・スクリーンショット検査 & 課題抽出）
- **実行**: `.venv\Scripts\python.exe scripts/playwright_audit.py --round 1` & `scripts/verify_catalog_and_demo.py`
- **検査画像**: `round_1/{web_demo_1920x1080, web_demo_1280x720, studio_1920x1080, studio_1280x720, stage_hud_mode_b_cello, stage_hud_mode_a_piano, stage_projection_f10}.png`
- **特定された具体的欠点**:
  1. **MP3 音量の極端な不均衡 (`8.0 dB` 差)**: Mode B (`showcase_01..05`) が `-15.41 .. -18.41 dBFS` なのに対し、Mode A (`showcase_06..10`) が `-19.98 .. -23.31 dBFS`（Peak `0.4278`）と顕著に小さく聴き比べ時に音圧差が生じた。
  2. **転回形ローマ数字バグ (68曲)**: `parse_chord_symbol` 内の `_roman_and_function()` が `ext + inv` を単純連結しており、`Imaj76` / `V76` / `ii76` と誤記されていた（正しくは `Imaj65` / `V65` / `ii65`）。
  3. **日本語タイトル＆メモの違和感**: カタログ内に `叫び`・不完全調名 (`へ長の`, `変ロの`) を含む題名が 23件、楽曲解説末尾にプロンプト文言 `事実記述のみ` がエコーされたメモが 21件存在。さらに `corpus_laws.law_summary` が丸め前の `spread=12st` を出力していた。
  4. **1280×720 での見切れ＆表現不足**: `/studio` と `web-demo` の 1280×720 スクリーンショットで下部「同期大譜表＆ピアノロール」の大部分が画面下端へはみ出していた。また設計メモがデッキ部と譜面下部に重複表示され、譜面に大譜表ブレイス・加線・符尾・幕見出し・クリックシークが無かった。
  5. **`/stage` HUD 細部**: Zone A が `VRAM 0.0 GB` と誤表示、Zone D ノードラベルに `AI [B] Act2 F_major:` とスネークケースと末尾コロンが露出、棄却カード (`opacity: 0.72`) が暗過ぎた。

### Round 2（第1次修正 → 実ブラウザ視覚検査 → デッドロック検出と追加研磨）
- **実施した修正（Round 1 → 2）**:
  - `local_audio_synth.py`: `render_masterwork_stereo_audio()` に RMS ターゲットマスタリング（`target_rms=0.145`）とトランジェントソフトニー制限を導入し、全10曲 MP3 を `-17.66 .. -17.89 dBFS`（差 `0.23 dB`）へ均一化。
  - `harmonic_beauty_realizer.py` / `layer2_intent_llm.py`: `_roman_and_function()` を正しい数字付き低音へ修正し、`sanitize_chamber_title_ja()`・`sanitize_composer_note_ja()`・`compose_100_pieces.py --polish-existing` により100曲 JSON と `catalog_100.json` を再研磨。
  - `web-demo/style.css`, `app.js`, `index.html`, `src/ui/studio.html`: `@media (max-height: 820px)` 圧縮・重複メモ除去・キーボード操作 (`Space`, `←/→`, `Esc`)・譜面クリックシーク・大譜表左ブレイス＆第1〜4幕見出し・和声X線次和音矢印を追加。
  - `stage_server.py`, `stage_hud.html`: 棄却カード視認性向上、Zone D ラベル整形、GPU VRAM 表示実装。
- **Round 2 監査で発見した問題**:
  1. 初回 Round 2 実行時、`stage_server.py` の `get_hud_state()` が `self._lock` 保持中に GPU 照会を行い `POST /api/stage/turn` が `ReadTimeout(12.0)` でタイムアウトするデッドロックを実計測で発見 → 即座に `_vram_poll_loop` 専用デーモンスレッドへ完全非同期化し解消。
  2. 再実行時の Playwright 視覚＆幾何検証で、左サイドバー自動スクロール後にスクロール領域外へ隠れた `.piece-item` 要素が兄弟ヘッダと幾何重複と誤検知される問題、さらに `studio_delib_replay.png` で英語スネークケース `broken_chord_barcarolle` がそのまま露出していた点、WebAudio ブラウザ再生にホール残響がなく乾いて聞こえる点、および残存タイトル3件 (`変ホ大調`, `嘆き`, `暗黒的昇華`) を発見。

### Round 3（第2次研磨 → 最終完全検証・スクリーンショット確定）
- **実施した修正（Round 2 → 3）**:
  - `web-demo/app.js`: ブラウザ WebAudio エンジンにプロシージャル 1.25s ステレオ室内楽ホール `ConvolverNode` 畳み込み残響（wet `0.26`）・ハンマー打鍵 3次倍音＋速度連動ローパスフィルタ・右手/左手/チェロのステレオパンニング分離を実装。
  - `web-demo/app.js`, `style.css`: `TEXTURE_JA` マップでミキサーと推敲リプレイのテクスチャ表記を完全日本語化（例: `分散和音 (バルカローレ)`）、譜面第4幕と凡例の間隔調整、棄却ミニ譜面音符視認性改善。
  - `harmonic_beauty_realizer.py`: `大調`→`長調` および `暗黒`/`嘆き` フィルタを追加し残存3曲を含む100曲全タイトルの不自然表現ゼロを達成。
  - `stage_hud.html`, `scripts/playwright_audit.py`: Zone D ラベル上限を26文字へ拡大（オクターブ番号切れ解消）、幾何チェッカーをスクロールコンテナ境界クリップ対応へ厳密化、WebSocket 切断後のクリーン終了を追加。
- **Round 3 最終実測結果**:
  - `scripts/verify_catalog_and_demo.py`: `passed=True`、`100 / 100 曲` 合格（平均美しさ `97.75`、最小 `95.07`、協和率 `97.05`、不協和ペア `0.65%`、平均同時発音 `5.01音`、単音率 `1.12%`）、`10 MP3` 全デコード正常・クリップ 0・音圧 `-17.66 .. -17.89 dBFS`。
  - `scripts/playwright_audit.py --round 3 --update-docs-images`: **`passed=True`**、`console=0`, `pageerror=0`, `http=0`、全6画面条件（`web_demo` 1920×1080 & 1280×720、`studio` 1920×1080 & 1280×720、`stage` 1920×1080 & 1280×720）で `overflow=0px`, `clipped=0`, `overlaps=0`。
  - **1280×720 完全ワンビュー収納**: 上部再生デッキ、5ch ミキサー、和声 X 線、および同期大譜表＋ピアノロール＋両ペダル曲線が垂直スクロールなしで一画面に収まることを確認。

---

### Round 4（正規 VexFlow 4.2.5 大譜表完全移行 ＆ 全画面表記・衝突ゼロ徹底監査）
- **実行コマンド**: `.venv\Scripts\python.exe scripts/playwright_audit.py --round 4 --update-docs-images` & `scripts/verify_catalog_and_demo.py`
- **特定された具体的欠点と根本原因**:
  1. **五線譜レンダリングの破綻**: 旧自作 Canvas リボン描画が、音部記号・調号との接触回避不足、2度音程・臨時記号衝突未対応、小節線／拍子の未量子化、および調号無視の `#` 固定異名同音テーブルにより正規楽譜の体裁を損ねていた。
  2. **ローマ数字 `iv66` ＆ 生識別子露出**: `harmonic_beauty_realizer.py` の `_roman_and_function()` が `ext=="6"` を第1転回形 `inv="6"` と単純連結していた（19曲）。またフロントエンドおよび `logic_trace.py` で `D_dorian`, `notagen_small`, `question_half_cadence`, `broken_chord_barcarolle`, `LH_open5th + RH_cantabile`, `half_clear`, `(#A8C8E8)`, `黒箱記録`, `-inf dB`, `Turn #` が一部未翻訳・生文字列で露出していた。
  3. **初期候補選択のズレ＆一部表記切れ・書体沈み込み**: `/studio` 読み込み直後にオフライン既定と本番第1ターンの `turn_id` が一致していたことで棄却案Aが初期表示される問題、Cormorant Garamond のオールドスタイル数字の沈み込み、作品リスト題名・設定欄の見切れ。
- **実施した修正**:
  - **MIT `VexFlow 4.2.5` (`web-demo/vexflow-bravura.js`) ローカルバンドル化 ＆ `web-demo/score_engraver.js` 新設**:
    - `stage_hud.html`, `studio.html`, `web-demo/index.html`, `web-demo/app.js`, `src/stage/stage_server.py` の全大譜表および Mode 4 推敲ミニスコアを正規 VexFlow SVG 彫刻へ完全移行。
    - 8つの正規記譜ルール（大括弧＋左右小節線接続、音部・調号・拍子記号右側の余白確保、C4基準MIDI座標・加線、Bravura楕円符頭・符尾方向・拍単位連桁・2度音程オフセット、`KEY_SPELLING_TABLE` による調性別異名同音と上下声部独立の `Accidental.applyAccidentals`、4/4 小節厳密量子化、幅別動的表示小節数、コード名・強弱記号・左右ペダル・赤ペン楕円＋棄却理由テキストの非重複専用Y軸レーン、HiDPI SVG ＋ コントラスト 4.5:1 以上）を厳密実装。
  - **テキスト・和声表記修正**: `harmonic_beauty_realizer.py` の転回判定修正＆ `compose_100_pieces.py --polish-existing` による全100曲JSON再検証、`logic_trace.py` と `score_engraver.js` フォーマッタによる `♭`/`♯` 統一と日本語用語統一、`app.js` `userPinnedCand`/`hasLiveSync` による初期採用候補自動選択修正、`style.css` `font-variant-numeric: lining-nums tabular-nums` ＆ 2行テキストエリア・折り返し許可。
  - **Playwright 幾何衝突ゼロ・SVG内部・コントラスト・露出自動監査の組込み (`scripts/playwright_audit.py`)**:
    - 全30状態の解像度（`1920×1080`, `1366×768`, `390×844`, `320×640`）× 状態（初期表示・再生中・棄却候補表示・論理トレース・保存トースト・ヘルプモーダル・F10投影）で DOM 非祖先重なり・SVG `<text>` 間＆音符／五線間重なり・音部記号干渉・見切れ・WCAG AA コントラスト・生識別子・表記ミス・最小 13px を自動検査。
- **Round 4 最終実測結果 ＆ 目視確認**:
  - `scripts/playwright_audit.py --round 4 --update-docs-images`: **`passed=True`**（30状態すべて `overflow=0px`, `clipped=0`, `overlaps=0`, `domOv=0`, `svg>=1/err=0`, `trunc=0`, `contrast=0`, `rawLeak=0`, `minFont>=13.0px`, `pedalOrderOk=True`, `console=0`, `pageerror=0`, `http=0`）。
  - 2x HiDPI 拡大画像（[audit_grand_staff_closeup.png](images/audit_grand_staff_closeup.png)・[audit_grand_staff_rejected_closeup.png](images/audit_grand_staff_rejected_closeup.png)）および PC・モバイル全スクリーンショットを目視検査し、記号・文字の重なりや不自然な表記がゼロであることを確認。
