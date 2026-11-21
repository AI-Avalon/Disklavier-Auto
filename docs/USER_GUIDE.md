# Disklavier-Auto 利用ガイド (`USER_GUIDE.md`)

本ガイドでは、**GitHub Pages 公開デモでの即試聴**、**Windows 11 ローカル環境構築**、**`/studio`（制作・試聴スタジオ）と `/stage`（本番舞台投影HUD）の使い分け**、および **自動検証 (`run_self_check.bat`)** の手順を説明します。

---

## 1. ブラウザで今すぐ試聴する（GitHub Pages デモ）

ローカル環境がなくても、ブラウザから以下 URL にアクセスするだけで **100曲ライブラリ＋代表10曲ホール録音 MP3** をすぐ試聴できます：
- **GitHub Pages デモ**: https://ai-avalon.github.io/Disklavier-Auto/

### GitHub Pages の有効化手順（リポジトリ管理者向け）
1. GitHub リポジトリページで **Settings** → **Pages** を開く。
2. **Build and deployment** の **Source** で **`GitHub Actions`** を選択して保存。
3. `main` ブランチへプッシュすると `.github/workflows/deploy-pages.yml` が自動検証＋デプロイを実行し、`https://ai-avalon.github.io/Disklavier-Auto/` が公開されます。

---

## 2. 初回セットアップ（Windows 11 / RTX 3070）

1. ローカル LLM [Ollama](https://ollama.com/) をインストールし、`qwen3.5:4b` を取得します（`ollama pull qwen3.5:4b`）。
2. リポジトリ直下の **`setup_environment.bat`** をダブルクリックします。
   - `.venv` を構築し、PyTorch CUDA 12.4、`fastapi`、`symusic`、`lameenc`、`playwright` 等を導入します。
   - `src.stage.config_scanner` が MIDI ポート、Steinberg UR22、Ollama モデル、MIDI コーパスをスキャンし `config.local.yaml` を作成します。

---

## 3. 起動モードの使い分け

| 用途 | 実行ファイル | 開くURL / 特徴 |
|---|---|---|
| **PC単体デモ・100曲試聴・ミキサー検証** | **`run_demo_showcase.bat`** | `http://localhost:8000/studio?demo=1` を開き、100曲試聴・パート別ミキサー・和声X線・仮想舞台デモ・ホール音響シンセが稼働します。 |
| **コンサート本番・実機リハーサル** | **`start_stage.bat`** | `--production` で `http://localhost:8000/stage` を開き、仮想奏者・PCモニターを完全OFFにして実機 Disklavier と UR22 に直結します。 |
| **全自動検証・Playwright・品質チェック** | **`run_self_check.bat`** | 全 pytest、整合性検証、100曲＋10 MP3 品質検証、Playwright 実ブラウザテスト、10回反復 LLM 検証を一括実行します。 |

---

## 4. `/studio` 制作・試聴スタジオの操作方法

1. **左ペイン（100曲レパートリー・ブラウザ）**:
   - `全100曲` / `代表MP3 (10)` / `Mode B: チェロ (50)` / `Mode A: ピアノ (50)` のフィルタ、調性・美しさスコア・検索ボックスで絞り込み、曲をクリックして即ロードします。
2. **上部再生デッキ**:
   - **`再生 (WebAudio)`**: 選択中の曲を 5チャンネル・ミキサー連動シンセで即時再生します。シークバーや大譜表クリックで任意の位置へ移動できます。
   - **代表曲ホール物理録音 MP3 プレイヤー**: 代表10曲の10倍音ピアノ＋チェロ＋1.6s RT60ホール残響レンダリング MP3 を直接再生できます。
   - **`MIDI ダウンロード`**: 全100曲すべてをブラウザ上で SMF Type-1 `.mid` ファイルとして即保存できます。
   - **`推敲リプレイ`**: 採用案 A と棄却されたボツ案 B/C/D の比較・赤ペン根拠・実演家メモをステップ再生します。
3. **パート別 聴き比べミキサー**:
   - `右手 (RH)` / `左手 (LH)` / `チェロ・対話` / `ダンパー共鳴 (CC#64)` / `ウナ・コルダ音色 (CC#67)` を個別に ON/OFF やソロ（`S`）試聴し、各声部やペダルの効果を耳で即確認できます。
4. **和声 X 線モニター (Harmony X-Ray)**:
   - 再生中のコード記号・ローマ数字機能和声（例 `Dm9 (i9)`）・協和率・同時発音数・両ペダルのリアルタイムゲージを表示します。
5. **ライブ舞台対話コントロールタブ**（`/studio` のみ）:
   - 実サーバーの Mode A/B 切替、`次ターン実行`、`推敲 Retake`、`4幕作品統合出力`、および右上の **`舞台投影 HUD (/stage)`** 遷移を行えます。

---

## 5. `/stage` 本番舞台HUDの操作方法

- **開演前**: `VDL校正` と `初期化` を確認し、**`F10`** キー（または `F10 投影` ボタン）を押して操作バーを格納した本番投影ビューへ切り替えます。
- **緊急時**: 右上の **`PANIC`** で 10ms 未満で全鍵盤・`CC#64=0`・`CC#67=0` を解放します。
- **Disklavier 本体設定**: Yamaha Disklavier 側のメニューで必ず **`Delay In = OFF`** に設定してください。

---

## 6. トラブルシューティング

- **Windows コンソールで日本語が二重表示される (`起起動動...`)**:
  - 最新版の `.bat` ファイルと `src/stage/cli_banner.py` では `chcp 65001` を廃止し Win32 `WriteConsoleW` 出力＋72桁折り返し抑止へ統一済みです。最新 `main` へ更新して実行してください。
- **WebAudio 音が鳴らない**:
  - ブラウザの自動再生ポリシーにより、最初の操作後に WebAudio が有効になります。`再生 (WebAudio)` ボタンまたは MP3 プレイヤーの再生ボタンを直接クリックしてください。
