# Disklavier-Auto システムアーキテクチャ設計書 (`ARCHITECTURE.md`)

> **対象システム**: 自律音楽思考エンジン・両ペダル連続物理シミュレーション・音楽性監査・舞台プロセス可視化システム  
> **動作環境**: Windows 11 / NVIDIA GeForce RTX 3070 (8GB VRAM) / Python 3.12 / Ollama (`qwen3.5:4b` 等)

---

## 1. 全体パイプラインと5レイヤー構成

```mermaid
flowchart TD
    Human["人間奏者\nMode A: Piano MIDI IN / Mode B: Cello C519ML + Ref Mic"] --> L1["L1: 低遅延知覚 (src/stage/layer1_perception.py)\nEchoSuppressionLedger / 2ch NLMS / F0・呼吸検知"]
    L1 --> Econ["TokenEconomizer (src/stage/token_economizer.py)\n音響特徴符号化・コンテキスト枝刈り・失敗介入台帳"]
    Corpus["クラシックMIDIコーパス (symusic)\nD:\\Disklavier-Data / data/imslp_midi / human_references / maestro-v3.0.0"] --> Loader["ZeroConfigCorpusLoader\nCUDA / NumPy ボイシング近傍検索"]
    Loader --> Econ
    Econ --> L2P["L2: 自律作曲設計 (src/stage/layer2_intent_llm.py)\nOllama keep_alive=-1 による和声進行・両手配置・両ペダル設計"]
    L2P --> L3["L3: 両手ポリフォニー作曲 (src/stage/layer3_candidate_composer.py)\nCandidate A/B/C/D 生成 ＋ 連続 CC#64 / CC#67 エンベロープ"]
    L3 --> Critic["MusicalityCritic (src/stage/musicality_critic.py)\n6軸監査（反音階・両手和声・声部連結・倍音回避・律動・両ペダル）"]
    Critic -->|"不適格時は自動再ボイシング"| L3
    Critic --> L2M["L2: 実演家メモ言語化\nMusicalLogicValidator (矛盾・AIポエム・〇〇風ラベル 0件保証)"]
    L2M --> Timeline["段階的推敲タイムライン (src/stage/stage_server.py)\nStep 1スケッチ -> Step 2赤線添削・試奏 -> Step 3ロック実演"]
    Timeline --> L5["L5: Disklavier安全出力 (src/stage/layer5_disklavier_output.py)\n30音上限・熱保護・CC#64/CC#67間引き・VDL先読み・<10ms Panic"]
    Timeline --> Synth["LocalAudioSynthesizer (src/stage/local_audio_synth.py)\n44.1kHz 両ペダル物理シミュレーション（デモ専用）"]
    Timeline --> HUD["L4: 60fps 舞台HUD (src/ui/stage_hud.html)\n4ゾーン大譜表・両ペダル連続波形・モチーフ記憶グラフ"]
    Timeline --> Obsidian["ObsidianMusicalBrain (src/obsidian/obsidian_brain.py)\nobsidian_vault/05_Stage_Session_Logs/ 自動記録"]
```

---

## 2. 各サブシステムの詳細責務

### 2.1 Layer 1: 知覚とエコー抑圧 (`src/stage/layer1_perception.py`)
- **Mode A (`EchoSuppressionLedger`)**: AIが送出した `(pitch, sent_ts)` を台帳記録し、物理打鍵帰還ウィンドウ `[0ms, 280ms]` 内に返ってきた自己打鍵ループバックを 100% 遮断し、人間の打鍵のみを通過させます。
- **Mode A (`TurnTakingDetector`)**: 人間の IOI リタルダンド、ダンパー解放、`CC#67` ウナ・コルダ合図からフレーズ終止を検知します。AI 自身が `CC#67` を駆動した直後（`0.35s` 以内）は人間の合図として誤認識しないよう `notify_ai_cc67()` で干渉を遮断します。
- **Mode B (`NLMSEchoCanceller` + `CelloAcousticPerception`)**: 16タップ2chブロック正規化LMS適応フィルタ（`mu=0.98`）でチェロマイク（Ch1）へのピアノ回り込み（Ch2参照）を除去し、F0・ビブラート・重音・アタック傾きによる `staccato` / `legato` 判別を行います。

### 2.2 TokenEconomizer (`src/stage/token_economizer.py`)
`microsoft/TokenEconomizer` の設計思想に基づき、ローカルLLMへのプロンプト情報を高密度化します：
1. **Micro-Skills & Acoustic Fact Injection**: 人間のフレーズ跳躍ベクトル（`IV=+3,+4,-2`）、和声緊張度（`T`）、リズム勢い（`M`）、濁り回避帯域（`AVOID=C3..G4`）を記号列へ圧縮。
2. **Corpus Voicing & Schema Pruning**: コーパス参照ボイシングの冗長なJSONキー・説明文を削除し、トークン削減率（`compression_ratio_pct`）を計測。
3. **Failure-Loop Intervention Ledger**: 直近の `MusicalityCritic` 棄却理由や `MusicalLogicValidator` 違反履歴を保持し、同じ失敗を防ぐ制約トークンを自動注入します。

### 2.3 L2 自律音楽思考 & 言語検閲 (`src/stage/layer2_intent_llm.py`)
- **「〇〇風」テンプレートの全面禁止**: `BANNED_PHRASES` と正規表現により、`バロック風`・`印象派風`・`ドビュッシー風` 等の模倣ラベル、音楽理論的矛盾（`長三度の空虚五度` 等）、AIポエム表現（`緊張を高める`・`余白を残して`・`共鳴` 等）をプログラムで100%拒否します。
- **RTX 3070 最適化**: Ollama 呼び出し時に `keep_alive=-1`（VRAM常駐ロック）と `think=False` を指定し、`.bat` 側で `OLLAMA_FLASH_ATTENTION=1` と `OLLAMA_KV_CACHE_TYPE=q8_0` を有効化しています。
- **自律設計スキーマ (`LLMPolicyDesignSchema`)**: 調性・和声進行・3〜5声の立体ボイシング配置（`voicing_structure`）・右手コンター（`rh_contour`）・左手運動（`lh_motion`）・連続 `CC#64`/`CC#67` 計画値をLLM自身が設計します。

### 2.4 L3 両手ポリフォニー作曲 & MusicalityCritic (`layer3_candidate_composer.py` / `musicality_critic.py`)

```mermaid
sequenceDiagram
    participant L1 as "L1 知覚スナップショット"
    participant Corpus as "ZeroConfigCorpusLoader\n(symusic + CUDA/NumPy検索)"
    participant L2 as "L2 Ollama (TokenEconomizer)"
    participant L3 as "L3 4候補作曲 (A/B/C/D)"
    participant Critic as "MusicalityCritic (6軸監査)"
    participant L45 as "L4 HUD & L5 Safety / Synth"

    L1->>Corpus: "緊張度・勢いから参照ボイシング3件を検索"
    L1->>L2: "圧縮コンテキスト送信"
    L2->>L3: "和声進行・両手配置・連続両ペダル方針を出力"
    L3->>Critic: "4候補 (A/B/C/D) を提出"
    Critic-->>L3: "単調音階・単声・短2度衝突があれば自動再ボイシング"
    Critic->>L2: "採用1案・ボツ3案・減点座標・理由を返却"
    L2->>L45: "45〜74文字の実用メモを添えて推敲タイムライン送出"
```

`MusicalityCritic` は全候補を以下の6軸（100点満点）で検査します：

| 評価軸 | 配点 | 主な検査・減点対象 |
|---|---:|---|
| 1. 反・音階化 (`anti_scale`) | 20点 | 4音以上の単調一方向順次音階進行、機械的アルペジオ反復を即棄却 |
| 2. 両手ポリフォニー＆垂直和声 (`polyphony_harmony`) | 20点 | 単音（モノフォニー）出力を禁止。LHバス/内声とRH対旋律/和音の同時和声構成を検証 |
| 3. 対位法・声部連結 (`voice_leading`) | 18点 | 並達5度・8度、同音反復停滞を減点し、反行・斜行・掛留解決を評価 |
| 4. 倍音クリアランス (`spectral_clearance`) | 18点 | CUDA/NumPy行列演算でチェロ/人間基音（100〜600Hz帯）との短2度・密集混濁を検出 |
| 5. 律動・休符・強弱多様性 (`rhythmic_diversity`) | 12点 | 均一ベタ打ちを禁止。音価対比、呼吸休符、ダイナミクス曲線を検証 |
| 6. 両ペダル連続連動 (`dual_pedal`) | 12点 | `CC#64`（遅れ踏み込み・ハーフペダル・踏み替え・解放）と `CC#67` の連続カーブを検証 |

### 2.5 両ペダル連続物理シミュレーション & L5 安全出力

```mermaid
flowchart LR
    Curve["L3 連続ペダルカーブ\nCC#64 (Damper) & CC#67 (Una Corda)"] --> L5["L5 安全ゲート (DisklavierStageOutputLayer)"]
    Curve --> Synth["LocalAudioSynthesizer (44.1kHz)"]
    L5 --> Thin["40ms最小間隔 & +/-3 デッドバンド間引き\n(0 / 127 境界は即時通過)"]
    L5 --> Poly["ハード上限 30音ボイススティール\nVel [18..112] / 60ms再打鍵 / 10s TTL"]
    L5 --> Panic["緊急停止 (<10ms)\nAll Note-Off + CC#64/67/120/123 = 0"]
    Synth --> Damper["CC#64 物理模擬:\n開度比例Decay延長・共鳴倍音・0復帰で即時ダンパー消音"]
    Synth --> Una["CC#67 物理模擬:\nハンマー横移動・第3〜5倍音連続減衰・アタック軟化"]
```
