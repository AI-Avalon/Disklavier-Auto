# Disklavier-Auto 現状監査レポート (`docs/CURRENT_STATUS_AUDIT.md`)

監査対象コミット: `HEAD` (`9f26608`)  
監査実施日: 2026-11-21

---

## 1. GitHub Actions ＆ GitHub Pages エラーの厳密な原因特定

GitHub REST API (`https://api.github.com/repos/AI-Avalon/Disklavier-Auto/actions/runs?per_page=10`) により直近失敗ランを特定し、Ubuntu CI 模擬環境（`sys.meta_path` で `symusic`, `torch`, `sounddevice`, `rtmidi`, `ollama`, `lameenc`, `matplotlib`, `playwright`, `faster_whisper` を遮断）でローカル再現して原因を 100% 特定した。

- **失敗ワークフロー**: `.github/workflows/deploy-pages.yml` (`Deploy Disklavier-Auto Interactive Demo to GitHub Pages`)
- **失敗ランID**: `44876556860` / `44868225738`（コミット `9f26608`）
- **失敗ジョブ・ステップ**: `validate-and-build` → **Step 5 (`Run unit & safety test suites`: `PYTHONPATH=. pytest -v tests/test_safety_guard.py tests/test_phase1_security.py`)** が終了コード 1 で失敗（Step 6〜8 と `deploy` ジョブが連鎖スキップ）。

### 判明した根本原因（全4件）

1. **`src/stage/__init__.py` の即時インポート副作用（致命的）**:
   - `tests/test_phase1_security.py:8` が `src.server.mission_control_server` をインポート → `mission_control_server.py:52` が `from src.stage.favicon import PIANO_FAVICON_ICO_BYTES` を実行 → Python が親パッケージ `src/stage/__init__.py` を初期化 → `__init__.py:21` が `src/stage/layer3_candidate_composer.py` を無条件インポート → `layer3_candidate_composer.py:24` の `from symusic import Note, Score, Track` で `ModuleNotFoundError: No module named 'symusic'` が発生しテスト収集段階で即死。
2. **Step 6 (`scripts/verify_catalog_and_demo.py:35`) の同一クラッシュ**:
   - `from src.stage.harmonic_beauty_realizer import BeautyHarmonyAuditor` も `src/stage/__init__.py` 経由で `symusic` 未導入環境で即死する。
3. **`symusic` / `torch` / `lameenc` への無ガード依存**:
   - `src/stage/layer3_candidate_composer.py:24` および `src/stage/corpus_phrase_model.py:27` がトップレベルで `symusic` を要求し、`mido` フォールバックがない。
   - `src/stage/harmonic_beauty_realizer.py:517` の `extract_corpus_musical_laws()` は `symusic` 不在時に `files_read = 0` となり `tests/test_stage_system.py:552` が失敗する。
   - `tests/test_stage_system.py:453`（`pm.loaded is True` / PyTorch `.pt` 必須）および `:523, :533`（`lameenc` による `.mp3` 生成必須）が CI 軽量ランナー上で失敗する。
4. **CI 依存管理ファイルの不在**:
   - `.github/workflows/deploy-pages.yml` が `pip install ...` をインライン直書きしており、`requirements-ci.txt` が未分離。

---

## 2. 最新リポジトリ全量棚卸し ＆ 問題リスト

### 2.1 「事前生成JSONの再生」に偏っている箇所
- `src/stage/stage_server.py:1253-1260` および `src/ui/studio.html`: デフォルトルート `/` と `/studio` が「100曲事前生成JSONアーカイブ（`web-demo/data/pieces/piece_*.json`）」のブラウザ画面になっており、リアルタイム対話機能は奥のタブ `#tab-live` または別ページ `/stage` に押し込まれている。
- `run_demo_showcase.bat:37`: `http://localhost:8000/studio?demo=1` を開くが、`/studio` の `app.js` は起動時に事前生成曲 #001/#020 をロードするだけで、即座にリアルタイム対話・一から生成を体験する導線がない。
- `NotaGen` / ローカルLLM による「一から音符・ABC記譜をストリーミング生成して即発音する」ライブデモエンジンが存在しない。

### 2.2 リアルタイムパイプラインが直列・ブロッキングになっている箇所
- `src/stage/stage_server.py:687-940` (`execute_composition_turn`):
  1. `decide_policy()`（L2 Ollama 呼び出し）を同期実行。
  2. `composer.compose_turn()`（`layer3_candidate_composer.py:1008-1126`）内で **4候補 A/B/C/D の生成・修復・`MusicalityCritic`・`BeautyHarmonyAuditor` を直列 `for` ループ** で逐次実行。
  3. `finalize_deliberation_monologue()`（L2 Ollama 呼び出し）を同期実行。
  4. `audio_synth.play_turn_audio()`（44.1kHz ピアノ10倍音＋FFT畳み込み残響）を **ターン実行スレッド上で同期レンダリング** してから `output_layer.schedule_phrase()` を呼んでおり、音声合成が L5 MIDI スケジューラへの送出を遅らせている。
  5. Obsidian ノート書き込み（`record_stage_dialogue_turn`）もターン内で同期ディスクI/Oしている。
- **Mode B リアルタイム音声入力・NLMS 並列スレッド不在**: `_orchestration_loop()` (`stage_server.py:1037-1060`) は `demo_enabled=True` 時に 4.2 秒おきに仮想ターンを回すか、Mode A の `turn_detector` をポーリングするだけで、UR22mkII からのライブ音声入力を並列処理する専用知覚ワーカーおよび先読み投機的生成バッファが存在しない。
- **WebSocket 配信周期**: `StageWSBroadcaster._broadcast_loop` (`stage_server.py:1243`) が `1/33s`（約33fps）であり、60fps 未満。
- **VRAM 動的解放の欠如**: RTX 3070（8GB）上で Ollama (`qwen3.5:4b`, 3.1GB) と PyTorch モデルが競合した際に `keep_alive: 0` で自動退避する `VRAMManager` がない。

### 2.3 GUI / UX・視認性・導線の問題点（スクリーンショット ＆ CSS 実測）
- **極小フォントの乱立**: `web-demo/style.css` と `src/ui/stage_hud.html` 全体に `8.5px`, `9px`, `9.5px`, `10px`, `10.5px`, `11px` が多用されており、遠くからの視認性・人間工学的読みやすさが著しく低い。
- **小さな操作ボタン**: `.btn.sm` が `padding: 3px 7px; font-size: 10px`（高さ約 22〜26px）で舞台脇・タッチ操作に適さない。
- **グランドピアノと逆転したペダル配置**: `studio.html:182-186` では左に `DAMPER (R / CC#64)`、右に `UNA CORDA (L / CC#67)` が並んでおり、ピアノ足元の空間配置（左＝ウナ・コルダ、右＝ダンパー）と逆。
- **2×2 窮屈な候補グリッド**: `/stage` の Zone C は狭い枠内に4つの五線譜と6軸バーを詰め込んでおり、各五線譜の譜面と棄却理由バッジが小さく潰れている（`cand-badge` に `text-overflow: ellipsis`）。
- **モード別導線の不足**: ユーザーは「チェロ対話」「ピアノ対話」「LLM/NotaGen一から作曲ライブ」「100曲ライブラリ」を最初から明確に選べない。また「実機 ⇄ デモ」ワンクリック切替、3ステップ導入ガイド、`? 使い方ガイド` モーダル、リアルタイム性能/VRAM計測HUD、ワンクリック保存（WAV/MIDI/Obsidian）導線が欠如。

### 2.4 作業ツリー状態 ＆ 残存・整理対象ファイル
- 変更済み未コミットファイル: `obsidian_vault/05_Stage_Session_Logs/StageTurn_demo_0001..0010_cello.md`（過去のデモ実行時に書き換えられたログ）。
- レガシー UI / サーバー (`src/server/mission_control_server.py`, `src/ui/index.html`, `src/ui/remote.html`) は `validate_system_integrity.py` と `tests/test_studio_overhaul.py` / `tests/test_phase1_security.py` が参照しているため互換維持が必須。
- `.github/workflows/` 配下は `tests/test_studio_overhaul.py:96` の制約により `deploy-pages.yml` のみを維持する。

---

## 3. 改修方針サマリ

1. **Phase 1**: `requirements-ci.txt` 新設、`src/stage/__init__.py` 遅延エクスポート化、`symusic`/`torch`/`lameenc` の安全な `mido`/ルールフォールバック実装、`deploy-pages.yml` 強化、`scripts/simulate_ci_local.py` 追加。
2. **Phase 2**: `src/stage/parallel_pipeline.py` に **6ワーカー完全並列パイプライン**（L1/L5専用I/O・NLMS知覚ワーカー・非同期LLMワーカー・4候補並列作曲監査プール・先読み投機的生成バッファ・非同期ホール音響＆60fps WS）を構築。
3. **Phase 3**: `src/stage/vram_manager.py`（RTX 3070 8GB 動的メモリ解放）＋ `src/stage/notagen_live_engine.py`（NotaGen / Ollama ABC記譜リアルタイムストリーミング生成・リアルタイムポリフォニー和声ガード・即時発音・WAV/MIDI/Obsidian保存）を実装。
4. **Phase 4**: 人間工学基準（UI `>= 15px`、ボタン `>= 44px`、思考メモ `24–30px`、左＝Una Corda / 右＝Damper、主役大譜表＋4比較カード）に準拠した **4モード明確分離 GUI** を `/`, `/studio`, `web-demo/`, `/stage` へ実装。
5. **Phase 5 & 6**: 純ASCII `.bat` 更新（ポート自動回避）、`README.md` ＆ `docs/USER_GUIDE.md` 刷新、2周以上の Playwright 実ブラウザ監査＋スクリーンショット目視確認、CI模擬・全自動テスト・10回LLM検証通過後に `git push` し、GitHub Actions 全緑（`success`）を確認する。
