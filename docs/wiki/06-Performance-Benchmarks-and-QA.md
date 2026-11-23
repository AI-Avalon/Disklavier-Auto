# 06. 実測性能ベンチマーク・負荷試験・トラブルシューティング

本ページの実測値は、Windows 11 + NVIDIA GeForce RTX 3070 (8.0 GB) 環境で `scripts/run_production_readiness_tests.py` を完走して取得した計測結果（詳細: [PERFORMANCE_AND_READINESS_REPORT.md](https://github.com/AI-Avalon/Disklavier-Auto/blob/main/docs/PERFORMANCE_AND_READINESS_REPORT.md)）に基づきます。

## 1. 実測レイテンシ ＆ ディスパッチ・ジッター (Suite 1)

| 計測項目 | 実測結果 | 目標基準 | 判定 |
| --- | --- | --- | --- |
| **L1 音響知覚 + 2ch NLMS** (`200` ブロック) | 平均 `0.422 ms` / P95 `0.652 ms` / 最大 `0.941 ms` | P95 < `23.22 ms` | PASS |
| **L3 4候補並列作曲** (`10` ターン) | 平均 `16.15 ms` / P95 `17.51 ms` / 並列高速化 `2.48x` | 平均 < `100.0 ms` | PASS |
| **Ollama LLM 推論** (`qwen3.5:4b`, `think=False`) | ウォーム TTFT `53.3 ms` (全体 `514.6 ms`) / コールド読込 TTFT `2860.3 ms` | TTFT < `3000 ms` | PASS |
| **WebSocket `/ws/stage` 60fps 同期配信** | 平均 `20.81 ms` / P95 `29.195 ms` / ジッター標準偏差 `8.93 ms` | P95 < `60.0 ms` | PASS |
| **L5 Disklavier MIDI スケジューラ精度** (`16` 音) | 平均 `0.076 ms` / P95 `0.323 ms` / 最大 `0.323 ms` | P95 < `5.0 ms` | PASS |
| **緊急 PANIC 全消音応答速度** | `0.082 ms` (終了後残存発音 `0` 音) | < `10.0 ms` | PASS |

## 2. 外部接続回線劣化 ＆ 無停止フェイルオーバー (Suite 2)

`remote_gpu_server.py` との間に異常注入プロキシを挟み、リアルタイム対話中の回線劣化・遮断を試験した結果です（設定タイムアウト閾値 `timeout_ms=1800`）。

| 試験フェーズ | 実際の推論先 | フェイルオーバー / 復帰 | 計測 RTT (ms) |
| --- | --- | --- | --- |
| 1. `wan_50ms` (低遅延WAN模擬) | `remote_rtx` | 正常継続 (`OK`) | `60.32` |
| 2. `wan_200ms` (大学Wi-Fi混雑模擬) | `remote_rtx` | 正常継続 (`OK`) | `212.27` |
| 3. `timeout_2500ms` (`> 1800ms` タイムアウト) | `mac_local_failover` | 自動退避 (`FAILOVER`) | `1812.34` |
| 4. `hard_disconnect` (TCPソケット強制遮断) | `mac_local_failover` | 無停止退避 (`FAILOVER`) | `19.19` |
| 5. `wan_recovery_50ms` (回線復旧 + クールダウン) | `remote_rtx` | 自動復帰 (`RECOVERED`) | `56.83` |

- 切替サマリー: `failover_count=2`、`recovery_count=1`、クラッシュ・例外送出 **`0` 件**。
- `FieldSessionRecorder` / `FieldSessionReplayer`: 同期収録 (`midi_events.jsonl`, `disklavier_io.mid`, `ur22_audio.wav`, `logic_trace.jsonl`, `network_latency.jsonl`) および実機なしタイムライン再構成 (`timeline_items=9`) を確認済み。

## 3. 55ターン連続高負荷・メモリ・安全性監査 (Suite 3)

| 安全・耐久監査項目 | 実測結果 | 上限 / 基準 | 判定 |
| --- | --- | --- | --- |
| **連続混在ターン完走** (Mode B / Mode A / Retake) | `55` ターン完走 | >= `50` ターン | PASS |
| **ピーク GPU VRAM (RTX 3070)** | `4.55 / 8.0 GB` | <= `8.0 GB` (OOM 0件) | PASS |
| **定常メモリ増分 (`tracemalloc` Turn 15 → 55)** | `1.33 MB` | < `25.0 MB` | PASS |
| **最大同時発音数** | `18 / 30` 音 | <= `30` 音 | PASS |
| **Mode A 自己打鍵ループ漏れ** (`50` 音帰還) | `0` 件 | `0` 件 | PASS |
| **Mode B ピアノ回り込み誤検出** (`20` ブロック) | `0` 件 (定常 NLMS ERLE `+95.07 dB`) | `0` 件 | PASS |
| **未解消の重度垂直衝突 / 単音抜け違反** | `0` 件 / `0` 件 | `0` / `0` | PASS |

## 4. Playwright 実ブラウザ視覚・UI規約監査 (Suite 4)

- 監査パターン数: `23` 画面状態（Desktop `1920x1080`・`1366x768` / スマホ `390x844`・超狭幅 `320x640`）。
- コンソールエラー / 未捕捉例外 / HTTP 404+: **`0 / 0 / 0`**。
- 読込確認済み Webフォント: `Cormorant Garamond`, `Shippori Mincho`, `Noto Sans JP`, `JetBrains Mono`。
- Rule 4 カード安定性: 入替 `0` / DOM再生成 `0` / `CLS = 0`。
- 機能・アクセシビリティ検証: `skip_link_ok=True`、`modal_a11y` 全通過 (`btn-help-close` フォーカス移動 / `Esc` 復帰)、`prefers-reduced-motion` (`1e-06 s`)、`UNA CORDA` (左) `<` `DAMPER` (右) 全幅維持。

## 5. ローカル検証・QA コマンド一覧

```bat
:: 1. 完全自己点検 (pytest + 整合性 + 100曲MP3 + CI模擬 + Playwright + Ollama 10回反復)
run_self_check.bat

:: 2. 本番実用化・負荷・回線断線試験（本ページの全ベンチマーク再計測）
.venv\Scripts\python.exe scripts/run_production_readiness_tests.py --update-docs-images

:: 3. GitHub Actions CI 同等テスト (軽量CPU環境模擬)
.venv\Scripts\python.exe scripts/simulate_ci_local.py
```

## 6. 現地トラブルシューティング

| 症状 | 原因の可能性 | 即座の対処法 |
| --- | --- | --- |
| Tailscale 経由で `check_remote_connection.py` がタイムアウト | 大学等の Wi-Fi で UDP / VPN 通信が制限されている | 自宅 PC 側で `start_external_tunnel.bat` を起動し、生成された `https://*.trycloudflare.com` URL へ接続先を切り替える |
| HTTP `401 Unauthorized` が返る | `DISKLAVIER_API_TOKEN` が自宅側と Mac 側で不一致 | 自宅コンソール（または `data\telemetry\remote_gpu_token.txt`）のトークン文字列を `--token` へ再指定 |
| Disklavier の発音が約 0.5 秒遅れる | 本体設定 `Delay In` が ON になっている | Disklavier 本体のメニューから **`Delay In = OFF`** へ変更（本エンジンは L5 VDL 先読みを使用） |
| macOS で `sounddevice` 初期化エラー | PortAudio ネイティブライブラリ未導入 | `brew install portaudio` を実行してから `./setup_mac.sh` を再実行 |
| 自宅 Ollama の初回応答だけ遅い (`> 1800ms`) | モデルが未ロード（コールド起動 `2.86s`） | 本番演奏開始前に `check_remote_connection.py` を1回実行すると `OLLAMA_KEEP_ALIVE=-1` で常駐し、以降は TTFT ~`53ms` になる |
| 音が鳴り止まない・暴走しそうになった | 予測不能な外部 MIDI/残響入力 | 画面右上の **`PANIC 全消音`** またはキーボード **`Esc`** キーで 1ms 以内に全遮断 |
