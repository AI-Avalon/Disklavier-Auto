# 02. 導入・外部公開・起動手順 (Windows RTX ＆ M1 Mac)

## 1. 自宅 Windows 11 PC (RTX 3070 8GB) 側の準備・常駐起動

自宅側にはリポジトリの `.venv` 環境・Ollama・Tailscale（および予備用の `cloudflared`）を準備します。

### 事前確認項目

1. **電源プラン・スリープ無効化**: 会場へ向かう前に Windows 設定で「スリープを『なし』」「休止状態を無効」にしてください。
2. **認証トークンの統一**: セキュリティのため任意の長い文字列を環境変数 `DISKLAVIER_API_TOKEN` へ設定して起動すると、会場Mac側と同じトークンで即接続できます（未設定時は自動生成されコンソールと `data\telemetry\remote_gpu_token.txt` へ一時表示されます）。
3. **Windows ファイアウォール**: ポート TCP `8090` をローカル/Tailscale インターフェース上で許可してください（Tailscale `ping` が通っても TCP 8090 がタイムアウトする場合は管理者 PowerShell で以下を実行）。
   ```powershell
   New-NetFirewallRule -DisplayName "Disklavier Remote GPU 8090" -Direction Inbound -Protocol TCP -LocalPort 8090 -Action Allow
   ```

### 起動コマンド（自宅 PC）

```bat
cd /d d:\Users\gakuto\Documents\GitHub\Disklavier-Auto
set DISKLAVIER_API_TOKEN=YourStrongTokenHere
start_remote_gpu_host.bat
```

- `start_remote_gpu_host.bat` の機能:
  - `OLLAMA_FLASH_ATTENTION=1`、`OLLAMA_KV_CACHE_TYPE=q8_0`、`OLLAMA_KEEP_ALIVE=-1` で Ollama の稼働を確認・自動起動。
  - `tailscale ip -4` から自宅 PC の Tailscale IPv4 アドレスを検出し、会場 Mac でそのままコピペ可能な `check_remote_connection.py` と `start_mac_stage.sh` コマンド例を表示。
  - 推論サーバ (`python -m src.stage.remote_gpu_server --host 0.0.0.0 --port 8090`) が異常終了しても **3秒で自動再起動するウォッチドッグループ** で無停止待機。

### 予備外部トンネルの併用（大学 Wi-Fi で VPN が通らない場合）

別ウィンドウで以下を実行しておくと、Tailscale VPN が大学のファイアウォールで遮断された際でも HTTPS (`443`) 経由の **Cloudflare Quick Tunnel** URL（`https://xxxx.trycloudflare.com`）または事前設定済みの名前付きトンネル（`CLOUDFLARE_TUNNEL_TOKEN`）を即座に使えます。

```bat
start_external_tunnel.bat
```

## 2. 会場 M1/M2/M3 MacBook Pro 側のセットアップ・起動

### 初回セットアップ (`setup_mac.sh`)

```bash
git clone https://github.com/AI-Avalon/Disklavier-Auto.git
cd Disklavier-Auto
./setup_mac.sh
```

`setup_mac.sh` は以下を自動実行します:
- Apple Silicon Homebrew 環境の Python 3.11 / 3.12 検出と `.venv` 作成
- `requirements-ci.txt` + macOS 実機向け `python-rtmidi`（CoreMIDI）・`sounddevice`（CoreAudio）・任意で Apple Silicon PyTorch (MPS) を導入
- CoreMIDI ポート（Disklavier 等）および Steinberg UR22 の検出と初期 `config.local.yaml` 生成

> **Audio 警告が出た場合**: `brew install portaudio` を実行した上で再度 `./setup_mac.sh` を実行してください。

### Finder ダブルクリック起動（`.command` ランチャー一式 ＆ `config.remote.env`）

リポジトリ直下の `config.remote.env.example` を `config.remote.env`（`.gitignore` 対象）へコピーして `DISKLAVIER_REMOTE_URL` / `DISKLAVIER_API_TOKEN` を保存しておくと、以下の3ファイルを Finder からダブルクリックするだけで自動読み込み・起動できます。

1. `Mac_1_Check_RTX.command`: Tailscale 疎通 + `check_remote_connection.py` + CoreMIDI/CoreAudio デバイス一覧を一括表示
2. `Mac_2_Start_Demo_with_RTX.command`: 実機 Disklavier 未接続のまま自宅 RTX 連携 + Mac スピーカー発音 + `/studio?demo=1`（論理思考トレース表示）を起動
3. `Mac_3_Start_Stage_Production.command`: 本番ステージ用（`--mode AUTO --production`、実機 Disklavier + UR22 + 黒箱録画 ON、断線時 Mac 自動フェイルオーバー）

### 接続診断 (`scripts/check_remote_connection.py`)

本番起動前に 1 コマンドで ping / HTTPS RTT（min / avg / P95）、Bearer 認証 (401/200)、実推論 E2E 遅延、WS ストリーミング、およびフェイルオーバー退避を一括テストします。

```bash
source .venv/bin/activate
python3 scripts/check_remote_connection.py \
  --url "http://<自宅TAILSCALE_IP>:8090" \
  --token "<DISKLAVIER_API_TOKEN>"
```

オプション:
- `--timeout-ms 1800`: フェイルオーバー閾値（ミリ秒）
- `--quick`: HTTP推論診断のみ高速実行（WSチェック省略）
- `--skip-failover`: 強制タイムアウト模擬を省略

### 本番起動 (`start_mac_stage.sh`)

```bash
./start_mac_stage.sh \
  --remote-url "http://<自宅TAILSCALE_IP>:8090" \
  --token "<DISKLAVIER_API_TOKEN>" \
  --mode AUTO \
  --production
```

主要オプション:
- `--production`: 実機本番設定 (`DISKLAVIER_STAGE_PRODUCTION=1`, PC内部音声Monitor OFF, 黒箱全記録 ON) で `/stage` HUD 用サーバを起動
- `--demo`: 仮想演奏試聴設定 (`--demo`) で `/studio?demo=1` を起動
- `--record`: デモ時でも `recordings/session_YYYYMMDD_HHMMSS/` へ黒箱記録を有効化
- `--mode AUTO|REMOTE_RTX_ONLY|MAC_LOCAL_ONLY`: 推論先モード
- `--lan`: `0.0.0.0` バインドで同一Wi-Fi上のスマートフォン・タブレットからUIアクセス可能化
- `--port 8000`: 指定ポートが使用中でも `+20` 番までの空きポートへ自動回避

> 毎回長い引数を打ちたくない場合は、リポジトリ直下の `.gitignore` 済み設定ファイル `.env.mac` に `DISKLAVIER_REMOTE_URL="..."` と `DISKLAVIER_API_TOKEN="..."` を保存しておくと、`./start_mac_stage.sh --production` だけで読み込まれます。
