# Disklavier-Auto 利用ガイド (`USER_GUIDE.md`)

本ガイドでは、Windows 11 環境における **初回セットアップ**、**デモ・自己実演モード（PC単体試聴）**、**本番実機モード（Yamaha Disklavier ＋ Steinberg UR22）**、**舞台プロジェクション操作**、および **自動検証（Self-Check）** の手順を説明します。

---

## 1. 初回セットアップ (`setup_environment.bat`)

1. ローカルLLMとして [Ollama](https://ollama.com/) をインストールし、`qwen3.5:4b` 等の8GB VRAM対応モデルを取得しておきます（例：`ollama pull qwen3.5:4b`）。
2. リポジトリ直下の **`setup_environment.bat`** をダブルクリックします。
   - Python 仮想環境 `.venv` を自動構築し、必須ライブラリ（`fastapi`, `uvicorn`, `symusic`, `ollama`, `mido`, `python-rtmidi`, `sounddevice`, `numpy`, `pytest` 等）を導入します。
   - `src.stage.config_scanner` が MIDI ポート、Steinberg UR22（WASAPI/ASIO優先）、Ollama モデル、および `data/` 内クラシックMIDIコーパス（`imslp_midi`, `human_references`, `maestro-v3.0.0`, `D:\Disklavier-Data`）をスキャンし、`config.local.yaml` を生成します。

---

## 2. 起動モードの使い分け

| 用途 | 実行ファイル | 特徴 |
|---|---|---|
| **PC単体での作曲デモ・試聴** | **`run_demo_showcase.bat`** | 仮想チェロ／ピアノ入力、Python 44.1kHz 直接発音シンセ（両ペダル物理模擬付き）、WebAudio、および `DEMO DECK` がすべて有効になります。 |
| **コンサート本番・実機リハーサル** | **`start_stage.bat`** | `--production` で起動し、仮想奏者・PCモニターシンセ・試奏音・DEMO DECK をすべて強制OFFにします。実機DisklavierとUR22のみに接続します。 |
| **自動テスト・10回反復品質検証** | **`run_self_check.bat`** | 全ユニットテスト、`validate_system_integrity.py`、および実Ollama・音響・安全ゲートの10回連続ループを自動検証します。 |

---

## 3. 舞台HUD（Web UI）の見方と操作

起動後、ブラウザで `http://localhost:8000`（デモ時は `http://localhost:8000/?demo=1`）が開きます。

1. **上部コントロールバー（`F10` で完全格納）**:
   - `Mode A: Piano ↔ Piano` / `Mode B: Cello ↔ Disklavier` の切替。
   - 口頭指示欄へ自然言語でリハーサル指示（例：`中音域の濁りを避け、両手開離和音とウナ・コルダ踏み替えで返す`）を入力して送信。
   - **`30秒物理遅延＆NLMS校正`**: 開演前に VDL 打鍵遅延と 2ch NLMS エコー減衰量（ERLE dB）を確認します。
   - **`PANIC: ALL NOTES OFF`**: 緊急時に 10ms 未満で全鍵盤・ダンパー（`CC#64=0`）・ウナ・コルダ（`CC#67=0`）を解放します。
2. **Zone A（左上：知覚・呼吸・両ペダル）**:
   - 人間の F0 ピッチ軌跡と、AI が駆動している **Una Corda (Left / CC#67)** および **Damper (Right / CC#64)** のリアルタイム開度バー（ハーフペダル帯マーカー付き）を表示します。
3. **Zone B（右上：実演家の思考トレース）**:
   - 実測音・ボツ案棄却理由・採用ボイシングとペダル操作を45〜74文字の実用メモで表示し、TokenEconomizer によるトークン削減率（`ECONOMIZER: xx% saved`）も確認できます。
4. **Zone C（中央：公開推敲・大譜表アリーナ）**:
   - 4候補（A/B/C/D）の **ト音＋ヘ音の大譜表**、**`MusicalityCritic` 6軸スコアバー**、および各譜面下部の **CC#64/CC#67 連続ペダルカーブ** をリアルタイム描画します。
   - ボツ案の欠点音符はヴァーミリオン（`#C83E2B`）で囲まれ、具体的な棄却理由とともに赤線で却下されます。
5. **Zone D（下部：公演モチーフ記憶グラフ）**:
   - 開演時（`記憶初期化`後）はノード `0` から始まり、人間のモチーフと AI 変奏が対話順に結線されていきます。
   - 各ターンの詳細ログとスコア内訳は自動的に `obsidian_vault/05_Stage_Session_Logs/` へ Markdown ノートとして保存されます。

---

## 4. トラブルシューティング

- **本番モードで自分のピアノ音をAIが二重に拾う**:
  - HUD 左上で `Mode A: Piano ↔ Piano` が選択されているか確認してください。`EchoSuppressionLedger` が AI 送信直後の `0〜280ms` の返り音を遮断します。
- **Disklavier の打鍵タイミングが遅れる**:
  - Disklavier 本体の設定メニューで **`Delay In = OFF`** に設定してください（本システムの L5 `offset_ms(pitch, velocity)` が打弦ラグを逆算先読みします）。
- **文字化け・コンソール出力の文字重複が起きる**:
  - すべての `.bat` ファイルは純粋 ASCII 記述＋`src/stage/cli_banner.py` 経由の UTF-8 出力に統一されており、Windows `cmd.exe` でも重複は発生しません。
