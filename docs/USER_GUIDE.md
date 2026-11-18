# Disklavier-Auto 利用ガイド (`USER_GUIDE.md`)

本ガイドでは、Windows 11 環境における **初回セットアップ（PyTorch CUDA・コーパス学習含む）**、**デモ・試聴モード**、**本番実機モード（Yamaha Disklavier ＋ Steinberg UR22）**、**公開リハーサル（書き直し）＆1曲完成出力**、および **自動検証** の手順を説明します。

---

## 1. 初回セットアップ (`setup_environment.bat`)

1. ローカルLLMとして [Ollama](https://ollama.com/) をインストールし、`qwen3.5:4b` 等の8GB VRAM対応モデルを取得しておきます（例：`ollama pull qwen3.5:4b`）。
2. リポジトリ直下の **`setup_environment.bat`** をダブルクリックします。
   - Python 仮想環境 `.venv` を自動構築し、必須ライブラリ（`fastapi`, `uvicorn`, `symusic`, `ollama`, `torch` CUDA 12.4, `lameenc`, `sounddevice`, `soundfile`, `scipy`, `pytest`, `ruff` 等）を導入します。
   - `src.stage.config_scanner` が MIDI ポート、Steinberg UR22（WASAPI/ASIO優先）、Ollama モデル、および `data/` 内クラシックMIDIコーパスをスキャンし、`config.local.yaml` を生成します。
   - `data/models/classical_phrase_model.pt` が未生成の場合、自動的に RTX 3070 CUDA で `train/train_phrase_transformer.py` を実行して学習済み重みを構築します。

---

## 2. 起動モードの使い分け

| 用途 | 実行ファイル | 特徴 |
|---|---|---|
| **PC単体での作曲デモ・完成作品試聴** | **`run_demo_showcase.bat`** | 仮想チェロ／ピアノ入力、コンサートホール物理音響シンセ、WebAudio、公開推敲・完成作品出力、およびデモ試聴プレイヤーが有効になります。 |
| **コンサート本番・実機リハーサル** | **`start_stage.bat`** | `--production` で起動し、仮想奏者・PCモニターシンセ・試奏音・DEMO DECK を強制OFFにします。実機DisklavierとUR22のみに接続します。 |
| **自動テスト・10回反復品質検証** | **`run_self_check.bat`** | 全ユニットテスト、`validate_system_integrity.py`、および実Ollama・4幕構成・ホール音響・安全ゲートの10回連続ループを自動検証します。 |

---

## 3. 舞台HUD（Web UI）の見方と操作

起動後、ブラウザで `http://localhost:8000`（デモ時は `http://localhost:8000/?demo=1`）が開きます。

1. **上部コントロールバー（`F10` で完全格納）**:
   - **`Mode A: Piano ↔ Piano` / `Mode B: Cello ↔ Disklavier`**: 演奏編成の切替。
   - **指示・推敲入力欄 ＋ `送信` / `推敲書き直し (Retake)`**: 自然言語で演奏指示やダメ出し（例：`今のフレーズはまだ解決せず問いかけのまま、左手の10度を強調して書き直して`）を送り、その場で直前のAIフレーズを書き直せます。
   - **`作品を完成させ通し演奏`**: これまでの全ターンの対話を1つの完結した楽曲（第1幕→第4幕＋コーダ）に統合し、作品タイトルを自動命名した上で **MP3 / WAV / MIDI / MusicXML / Obsidianノート** を生成し、HUD上部のオーディオプレイヤーで即座に再生できます。
   - **`VDL/NLMS校正`**: 開演前に打弦遅延と 2ch NLMS エコー減衰量（ERLE dB）を検証します。
   - **`PANIC: 全音停止`**: 緊急時に 10ms 未満で全鍵盤・ダンパー（`CC#64=0`）・ウナ・コルダ（`CC#67=0`）を解放します。
2. **完成作品試聴プレイヤー＆4幕構成アークバー**:
   - リポジトリ収録済みの完成デモ作品（`Mode B 完成曲再生` / `Mode A 完成曲再生`）や合成したばかりの楽曲をワンクリック試聴できます。
   - **`第1幕: 問いかけ (半終止) → 第2幕: 発展・模進・転調 → 第3幕: 多声クライマックス → 第4幕: ウナ・コルダ回想・終止`** の現在位置、記憶中の **主題核（Seed Motif）**、**声部接続（Bridge）** を常時表示します。
3. **Zone A〜D の表示**:
   - **Zone A（左上）**: F0 ピッチ軌跡、Una Corda (`CC#67`) / Damper (`CC#64`) 連続開度ゲージ、呼吸終止ゲージ。
   - **Zone B（右上）**: 実演家メモ（45〜74文字）、現在の幕、楽曲機能（`question_half_cadence` 等）、和声進行、TokenEconomizer 削減率。
   - **Zone C（中央）**: 4候補の大譜表、`MusicalityCritic` 6軸スコアバー、赤ペン添削、連続両ペダル曲線。
   - **Zone D（下部）**: 開演時ノード `0` から成長する公演モチーフ記憶グラフ。完成作品は `obsidian_vault/02_Hall_of_Fame_Performances/` へ自動保存されます。

---

## 4. トラブルシューティング

- **本番モードで自分のピアノ音をAIが二重に拾う**:
  - HUD 左上で `Mode A: Piano ↔ Piano` が選択されているか確認してください。`EchoSuppressionLedger` が AI 送信直後の `0〜280ms` の返り音を遮断します。
- **Disklavier の打鍵タイミングが遅れる**:
  - Disklavier 本体の設定メニューで **`Delay In = OFF`** に設定してください（本システムの L5 `offset_ms(pitch, velocity)` が打弦ラグを逆算先読みします）。
- **文字化け・コンソール出力の文字重複が起きる**:
  - すべての `.bat` ファイルは純粋 ASCII 記述＋`src/stage/cli_banner.py` 経由の UTF-8 出力に統一されており、Windows `cmd.exe` でも重複は発生しません。
