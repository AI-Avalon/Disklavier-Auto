# Antigravity 2.0 — Live-Visualized Autonomous R&D Studio for Cello & Disklavier

**Yamaha Disklavier（自動演奏グランドピアノ）× Steinberg UR22mkII × AKG C519ML（コンデンサーマイク）** を統合し、チェロとDisklavierによるリアルタイム・クラシックセッション、聴取型AI自動作曲、5段階最適化MIDI再生、および自律音響実験（Experiment 1〜5）を行う60fpsライブGUIスタジオです。

---

## 🚀 クイックスタート（起動方法）

```bash
./run_studio.sh
```
または手動でサーバーを起動してブラウザ／アプリウィンドウを開きます：
```bash
PYTHONPATH=. .venv/bin/uvicorn src.server.mission_control_server:app --host 127.0.0.1 --port 8765
open -na "Google Chrome" --args --app="http://127.0.0.1:8765" --window-size=1480,940
```

---

## 🛡️ 1. Disklavier 物理安全制約ミドルウェア ([`DisklavierSafetyGuard`](src/safety/disklavier_safety_guard.py))

すべてのMIDI出力は例外なく [`DisklavierSafetyGuard`](src/safety/disklavier_safety_guard.py) を通過し、ハードウェアのクラッシュ・ソレノイド過負荷・熱保護解放を100%防止します：

1. **30鍵同時押しクラッシュ回避＆16音制限＋20音Voice Stealing**:
   - 2 ms窓内の同時Note-Onをソフト上限 **12音（ハード上限16音）** に制限し、超過分は下声部から上声部へ **1.8 msずつ自動分散（Micro-staggering）** します。
   - アクティブ保持ボイス数が上限 **20音** を超えないよう、バス音（最低音）とソプラノ音（最高音）を保護しつつ古い内声から安全にNote-Off（Voice Stealing）します。
2. **音符密度＆同一鍵盤連打リミッター**:
   - 全88鍵合計で **最大28〜30 Notes/sec** に制限し、DSP・電源飽和を防止します。
   - 同一鍵盤の連打は **周期68 ms以上（約14.7 Hz、最小離鍵ギャップ25 ms以上）** を強制し、高速連打時は自動で間引くかオクターブ上へ倍音分散します。
3. **ベロシティ有効領域（Velocity 20〜105）＆最小ノート長（38 ms以上）＆ソフト離鍵**:
   - 空振（Ghost Note: `Velocity 1〜15`）を排除し、すべての通常発音ノートを **Velocity 20〜105** にクランプします。
   - **例外（Sympathetic Halo）**: `sympathetic_halo=True` の倍音共鳴モード時のみ `Velocity 1`（サイレント打鍵によるダンパー開放）を許可します。
   - **最小ノート長**: ソレノイド不完全動作を防ぐため **最低38 ms**（30 ms未満厳禁）を強制保証します。
   - **Note-Off Velocity**: ダンパー激突ノイズ（Damper Thud Noise）を防ぐため離鍵ベロシティを **15〜40（デフォルト25）** に制御します。
4. **一番右のダンパーペダル（CC#64）専用制御＆-40 ms先行入力（Pre-roll）**:
   - 使用ペダルは **右ダンパーペダル（CC#64）のみ** とし、CC#66 / CC#67 は完全遮断します。
   - ハーフペダル領域（**CC#64 = 35〜80**）を連続制御し、ダンパー機構の物理慣性ラグを補償するため和音Note-Onに対して **-40 ms 先行送信** します。
5. **熱保護ウォッチドッグ（8,000 ms / 240 s）＆緊急パニック**:
   - 単一鍵盤ホールド最大 **8,000 ms**、ペダル連続ON最大 **240秒** で自動リフレッシュします。
   - 画面右上の `🔴 EMERGENCY PANIC` ボタンおよびプロセス終了時に全88鍵Note-Off＋CC#64=0を即時送信します。

---

## 🖥️ 2. 常時稼働 6タブ GUI & 60fps Visual Heartbeat ([`src/ui/index.html`](src/ui/index.html))

- **Global Top Bar（常時固定）**:
  - `👤 User Action & Status Banner`: 🟢 `HANDS OFF` / 🟡 `⚠️ PIANO SOUND IN 3.. 2.. 1.. (3秒カウントダウン予告)` / 🔵 `READY TO INTERACT` / 🔴 `ACTION NEEDED`
  - `Input Mode Selector`: `[🎹 Test: Piano-as-Cello Proxy]` ⇄ `[🎻 Live: Real Cello on C519ML]` ⇄ `[🌊 Synth Cello Injector]`
  - `Solenoid Load Meter (0-16 notes)` & `🔴 EMERGENCY PANIC Button`
- **4つの60fpsリアルタイム視覚フィードバック**:
  1. **C519ML Live Oscilloscope & 60-Bin CQT Spectrogram**
  2. **88-Key Disklavier & Right Damper Pedal (CC#64) Visualizer**（Velocity色分け＋🟣 `Velocity 1` Sympathetic Halo 表示）
  3. **DisklavierSafetyGuard Telemetry Gauges**（同時打鍵・保持ボイス・秒間密度・VDL先読みキュー）
  4. **Agent Live Brain, Code Diff, Terminal Stream & Git Commit Feed**
- **6つの機能タブ**:
  - **Tab 1 (`📡 Live Mission Control & Agent Monitor`)**: 全体進捗・波形・鍵盤・ログ・実験ステータスの統合コックピット
  - **Tab 2 (`🎻 Live Cello & Disklavier Session`)**: 単音・重音（Double/Triple Stops）・フラジオレット・弓使い・ルバートBPM検知と6種類のクラシック伴奏スタイル、およびワンクリック **「Auto Proxy Session Demo」**
  - **Tab 3 (`🎼 AI Classical Composer Studio`)**: **「C519MLでモチーフを聴いて作曲」** による4声部対位法＆アルペジオ自動作曲、ピアノロール表示、SMF（`.mid`）出力
  - **Tab 4 (`🎹 Precision MIDI Player & IMSLP`)**: IMSLPクラシック名曲8曲（バッハ、フォーレ『エレジー』『シシリエンヌ』、サン＝サーンス『白鳥』、ブラームス、メンデルスゾーン、ショパン、ドビュッシー）と **Disklavier 5段階オプティマイザ**
  - **Tab 5 (`📼 Rehearsal Time-Machine`)**: 録音したチェロ（または代奏）テイクに対する作曲家別伴奏スタイルの実機聴き比べ＆協和度比較
  - **Tab 6 (`🔬 Autonomous R&D Encyclopedia`)**: Experiment 1〜5 の全実測グラフ・データ閲覧・再実験トリガー

詳細な実験結果と物理モデル式については [`EXPERIMENT_REPORT.md`](EXPERIMENT_REPORT.md) を参照してください。
