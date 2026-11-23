# 03. 舞台運用・実機Disklavier/UR22設定・黒箱全記録

## 1. 実機機材結線と絶対遵守設定

| 機材 / 項目 | 設定・接続内容 | 理由 |
| --- | --- | --- |
| **YAMAHA Disklavier 本体** | **`Delay In = OFF`**（実機モード） | Disklavier 内部の500msバッファリングを無効化し、L5の先読みソレノイド着火遅延テーブル `offset_ms(pitch, vel)` で正確な拍を合わせるため |
| **USB MIDI / CoreMIDI** | M1 MacBook Pro ⇔ Disklavier USB MIDI (または MIDI I/F) | CoreMIDI 直結で MIDI OUT ジッター `< 5 ms` を維持 |
| **AKG C519ML → UR22** | UR22 **Ch1（MIC Input 1 / 48V ファンタム ON）** | チェロ生収音の超低遅延取り込み |
| **参照マイク / ライン → UR22** | UR22 **Ch2**（ピアノ回り込み参照信号） | `NLMSEchoCanceller` によるアダプティブ・ピアノ音除去の基準信号 |
| **UR22 モニター設定** | PC音声監視 = **本番時は OFF**（`--production`） | 舞台上でのハウリング・アコースティック混濁防止 |

## 2. 4つの動作モードの正確な使い分け

### [1] チェロ対話モード (Mode B: Acoustic Dialogue)
- UR22 Ch1/Ch2 から 44.1kHz で取り込んだチェロ音に対し、NLMS エコー除去後のクリーンF0（YIN）、ビブラート幅（cent）、アーティキュレーション（staccato/legato）、呼吸終止ゲージ（silence + フレーズ変化）を検出して応答します。
- 物理テスト時: 画面左側の **「NLMS 較正」** ボタンを最初に無音〜数秒実行すると現場残響に合わせた安定抑圧が得られます。

### [2] ピアノ対話モード (Mode A: Dual Piano Interactive)
- Disklavier 本体の生打鍵 MIDI IN（または画面下部鍵盤・PCキーボード `A W S E D F T G Y H U J K`）を取り込みます。
- **自己打鍵完全分離 (`EchoSuppressionLedger`)**: AIが Disklavier へ送信したピッチと送信時刻を台帳に記録し、ソレノイド物理動作から MIDI IN へ戻ってくる同一音（`[0ms, 280ms]` 窓）を100%自己ループ除外します。

### [3] NotaGen / LLM 一から作曲ライブ (`[3] NotaGen/LLM 一から作曲`)
- 前楽節（半終止）から後楽節（完全終止）までの構成とABC記譜を1小節単位でリアルタイム・ストリーミング生成し、和声ガードを通過した音型を即座に発音します。
- **エンジン選択**: `auto` / `notagen_small` / `llm_json` / `heuristic` から選択可能。

### [4] 100作品デモ試聴・アーカイブ (`[4] デモ試聴・アーカイブ`)
- 事前生成・品質監査済みの100曲カタログ（Mode B 50曲 / Mode A 50曲、高音質スタジオMP3 10曲含む）を検索・試聴し、82秒前後の時間軸をスクラバーや4声レイヤー独立 MUTE / SOLO ミキサーで確認できます。「**この余韻から AI作曲を再開**」ボタンで選択曲の調・終止状態を引き継いで `[3]` モードへシームレス遷移できます。

## 3. 舞台操作ショートカット ＆ 緊急停止 (PANIC)

- **`F10` キー**: 操作ボタン群（ヘッダー／デッキ／指示バー）を隠し、背景をさらに落ち着いた `#141517` へ落とす **舞台投影クリーンモード** のON/OFF切替。
- **右上 `PANIC 全消音` / `Esc` キー**: モーダルが開いている時はモーダルを閉じ、通常時は `< 10 ms` 以内で全キュー消去・全発音 Note-Off・`CC#123=0` / `CC#120=0` / `CC#64=0` を送出します。
- **左右ペダル物理整合規約**: 本システムはすべての画面（Studio / Stage HUD / Web Demo）で実機の足元と同一の **左 = `UNA CORDA (CC#67)`・右 = `DAMPER (CC#64)`** 配置に固定されています。

## 4. 現場ブラックボックス全自動レコーダー (`FieldSessionRecorder`)

名工大実機テスト等で「AIがその瞬間に何を聴き、どの論理で何を打ち返し、ネットワーク遅延がどう推移したか」を事後完全検証するための同期記録機能です。

### 記録方法
- `./start_mac_stage.sh --production`（自動ON）または `--record` フラグ、あるいは Studio トップバーの **「黒箱記録: OFF → ON」** ボタンで開始/停止します。
- API直接制御: `POST /api/stage/recorder/start` ・ `POST /api/stage/recorder/stop`

### 保存ファイル (`recordings/session_YYYYMMDD_HHMMSS/`)

| ファイル名 | 内容 |
| --- | --- |
| `midi_events.jsonl` | 時系列相対時刻 (`rel_s`) 付き MIDI IN / OUT 全メッセージ |
| `disklavier_io.mid` | Track 1 (`Disklavier_MIDI_IN`) + Track 2 (`Disklavier_MIDI_OUT`) の Type-1 SMF |
| `ur22_audio.wav` | 44.1kHz ステレオ16bit WAV（L: マイク入力 `mic_ch`、R: ピアノ参照 `ref_ch`） |
| `logic_trace.jsonl` | 毎ターンの6段階論理思考トレース完全レコード |
| `network_latency.jsonl` | 自宅 RTX RTT 推移・タイムアウト・フェイルオーバー・自動復帰イベント |
| `session_manifest.json` | セッション総時間、総ノート数、フェイルオーバー回数、収録ファイル一覧 |

### 実機なし事後リプレイ解析

```python
from pathlib import Path
from src.stage.field_recorder import FieldSessionReplayer

sdir = Path("recordings/session_20261124_140000")
session = FieldSessionReplayer.load_session(sdir)
timeline = FieldSessionReplayer.iter_timeline(sdir)
for ev in timeline[:10]:
    print(ev["rel_s"], ev["stream"], ev["data"])
```
