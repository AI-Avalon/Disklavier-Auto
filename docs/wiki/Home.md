# Disklavier-Auto 運用・技術ドキュメント Wiki

YAMAHA Disklavier 自動演奏グランドピアノと人間の生演奏者（チェロ / ピアノ）が舞台上でリアルタイムに聴き合い、和声的・対位法的に共作するための舞台制御・AI推論システム **Disklavier-Auto** の公式ドキュメントです。

会場に重いデスクトップPCを持ち込めないライブ環境（名古屋工業大学での接続実証テスト等）に対応するため、**「会場 M1 MacBook Pro（超低遅延MIDI/音響/安全装置/HUD）」** と **「自宅 RTX 3070 Windows PC（LLM / NotaGen / 4候補並列作曲推論サーバ）」** を外部ネットワーク経由で分離結合し、遅延や回線断線時にも演奏を止めない無停止自動フェイルオーバーを備えています。

## Wiki 目次

1. [01. システム構成と会場Mac⇔自宅RTXハイブリッド設計](01-Architecture-and-Hybrid-RTX-Mac)
2. [02. 導入・外部公開・起動手順 (Windows RTX ＆ M1 Mac)](02-Installation-Windows-RTX-and-M1-Mac)
3. [03. 舞台運用・実機Disklavier/UR22設定・黒箱全記録](03-Live-Stage-Operations-and-Hardware)
4. [04. 6段階「論理思考トレース」・音楽エンジン仕様](04-Logic-Trace-and-Musical-Engine)
5. [05. UIアートディレクション・書体選定・アクセシビリティ規約](05-UI-Design-Direction-and-Accessibility)
6. [06. 実測性能ベンチマーク・負荷試験・トラブルシューティング](06-Performance-Benchmarks-and-QA)

## 前夜（自宅RTX PC）＆ 当日会場（M1 Mac）クイックリファレンス

### 自宅 RTX 3070 Windows 11 PC（常駐待機）

```bat
set DISKLAVIER_API_TOKEN=<共有する強力な任意のトークン>
start_remote_gpu_host.bat
```

- 大学Wi-Fi等で Tailscale VPN 通信が制限される場合の予備トンネル起動:

```bat
start_external_tunnel.bat
```

### 会場 M1 MacBook Pro（名工大接続テスト）

```bash
./setup_mac.sh
python3 scripts/check_remote_connection.py --url "<TailscaleまたはCloudflare URL>" --token "<TOKEN>"
./start_mac_stage.sh --remote-url "<URL>" --token "<TOKEN>" --mode AUTO --production
```

## リンク

- リポジトリルート: [AI-Avalon/Disklavier-Auto](https://github.com/AI-Avalon/Disklavier-Auto)
- 公開 WebAudio デモ: [GitHub Pages Studio Demo](https://ai-avalon.github.io/Disklavier-Auto/)
- デザイン設計書: [docs/DESIGN_DIRECTION.md](https://github.com/AI-Avalon/Disklavier-Auto/blob/main/docs/DESIGN_DIRECTION.md)
- 実測性能レポート: [docs/PERFORMANCE_AND_READINESS_REPORT.md](https://github.com/AI-Avalon/Disklavier-Auto/blob/main/docs/PERFORMANCE_AND_READINESS_REPORT.md)
