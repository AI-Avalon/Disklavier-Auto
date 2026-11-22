# Disklavier-Auto 作曲専用OSSエンジン調査・比較選定・統合設計 (`docs/COMPOSITION_ENGINES.md`)

対象ハードウェア: Windows 11 Pro 64bit / Core i7-12700F (12コア20スレッド) / 64GB RAM / NVIDIA GeForce RTX 3070 (VRAM 8GB)  
PyTorch 環境: `2.6.0+cu124` (`torch.ao.quantization.quantize_dynamic` + `torch.nn.functional.scaled_dot_product_attention` 対応)

---

## 1. 調査・比較した作曲専用OSSモデル群

| モデル / プロジェクト | ライセンス / 発表元 | アーキテクチャ / 表現形式 | パラメータ規模 | 推定VRAM (FP16 / 量子化) | 主な強み | 本システムでの役割・配置 |
|---|---|---|---|---|---|---|
| **NotaGen (`ElectricAlexis/NotaGen`)** (`NotaGen-Small`) | MIT / Academic | Patch-level 12層 + Char-level 3層階層Decoder (`hidden=768`, `patch_len=2048`), Interleaved ABC記譜法, CLaMP-DPO強化学習 | 110M (`Small`) / 244M (`Medium`) / 516M (`Large`) | FP16: ~0.45–1.8 GB（KVキャッシュ込 ~2.2–4.5 GB） / INT8動的量子化: ~0.25–1.1 GB | クラシック楽譜美学・多声部`[V:1]`/`[V:2]`対位法・強弱/速度表現の再現性が極めて高い | **主軸エンジン (`notagen_full` プロファイル)**: Ollamaを`keep_alive=0`で退避し、8GB VRAM専有＋SDPA FlashAttentionで一から4〜8小節楽節生成 |
| **Anticipatory Music Transformer (`jthickstun/anticipation`, `stanford-crfm/music-small-800k`)** | Apache 2.0 | Causal Decoder Transformer (GPT-2 Small相当), Arrival-Time符号化 + 5秒先読み制御間隔 ($\delta=5\text{s}$) | 128M (`Small`) / 780M (`Large`) | **FP16: ~0.31 GB** | 人間が弾いたフレーズの「続き（継続生成）」と「指定小節の穴埋め（インフィリング）」が超低遅延で可能 | **GPU常駐枠 (`realtime_dialogue` プロファイル)**: Ollama (`qwen3.5:4b` Q4_K_M ~3.6GB) と RTX 3070 8GB に同時常駐し、Mode A（ピアノ対話）の即時継続・補完を実行 |
| **MIDI-GPT (`Metacreation-Lab/MIDI-GPT`)** | Academic / Open | GPT-2ベースマルチトラックTransformer, GigaMIDI学習, 小節/トラック単位インフィリング & 属性制御 | Compact GPT-2 | **0.0 GB (CPU推論)** | 音密度・ポリフォニー数・音域属性を指定し、既存主旋律トラックに対する伴奏トラックを安定生成 | **CPU伴奏枠 (VRAM 0GB)**: Core i7-12700F 並列スレッド上で稼働し、Mode B（チェロ×ピアノ）のチェロ旋律条件付き伴奏・小節再生成を担当 |
| **MusicLang (`musiclang/musiclang-v2`)** | Open | ローマ数字機能和声・コード進行・声部進行制約トークン | Compact LLM / Grammar | 0.0–0.8 GB | コード進行指定下の厳密な和声機能・強拍和声音着地・掛留音解決 | **機能和声・声部連結制約層として統合**: `PeriodStructurePlanner` および `HarmonicBeautyRealizer` へローマ数字カデンツ・機能和声規則を統合 |
| **Orpheus Music Transformer** | Open | RoPE + FlashAttention (8kコンテキスト), 多楽器トークン | 748M | FP16: ~1.8–4.8 GB (INT4/8必須) | 長尺構造・多楽器コンテキスト維持 | 748M重みはリアルタイム対話枠でOllamaと競合するため不採用。8kコンテキスト・RoPE設計知見を長尺4幕アーキテクトへ反映 |
| **Aria-Duet (`EleutherAI / Aria-MIDI`)** | Apache 2.0 / Open | LLaMA系連続KVキャッシュ先読み・リアル演奏マイクロタイミング | Medium–Large | ~2.5–6.0 GB | デュエット対話時の連続KVキャッシュ先読みによるレイテンシ削減 | **設計思想を Worker 5 先読みに統合**: 人間フレーズ終止前の先読み投機的ドラフトバッファ (`SpeculativePreGenWorker`) として実装 |

---

## 2. RTX 3070 (VRAM 8GB) における動作検証とデュアルプロファイル設計

システムRAMへの退避（3〜5 tok/s へ急落するメモリウォール）を完全に防ぐため、GPUメモリを厳格な2プロファイルで制御する（`src/stage/vram_manager.py`）：

1. **`realtime_dialogue` プロファイル（合計 < 6.5 GB 厳守）**:
   - Ollama (`qwen3.5:4b` Q4_K_M + `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` + `keep_alive=-1`): 約 `3.6 GB`
   - `AnticipatoryContinuationEngine` (`music-small-800k` 128M FP16相当): 約 `0.31 GB`
   - CUDA コーパス類似度・倍音監査テンソル: `< 0.15 GB`
   - Mode B 伴奏 (`MIDIGPTAccompanimentEngine`): **CPU推論 (`0.0 GB` VRAM)**
   - **合計予約目安: 約 `4.1〜5.8 GB` (`< 6.5 GB` 安全マージン確保)**
2. **`notagen_full` プロファイル（NotaGen 一から生成時）**:
   - 切替トリガー時に Ollama へ `POST /api/generate` (`keep_alive: 0`) を送信して即座にアンロードし、`gc.collect()` + `torch.cuda.empty_cache()` + `torch.cuda.ipc_collect()` を実行。
   - 空いた 8.0 GB VRAM 領域へ `NotaGenSmallQuantizedModel`（PyTorch SDPA FlashAttention + FP16 / CPU・省メモリ環境向け INT8 `quantize_dynamic`）を展開して最速トークン生成を行う。

---

## 3. 「ちゃんと曲になる」ためのハイブリッド統合パイプライン

どの生成モデルを用いた場合でも、単独出力による「1〜2小節で切れる断片」「不協和音の放置」「ペダル欠落」を根絶するため、以下の5段構成を通過させる：

1. **`PeriodStructurePlanner`（4〜8小節 楽節構造計画）**:
   - 前楽節（Antecedent, 1〜4小節）：種モチーフ提示・転回/反行展開 → 第4小節 **半終止（V / V7sus4）** による開放的な問いかけ。
   - 後楽節（Consequent, 5〜8小節）：動機ゼクエンツ展開・第6小節付近の単一旋律頂点・跳躍後の順次回復 → 第8小節 **完全終止（V7 → I/i）** とウナ・コルダ収束。
2. **専用生成コア（用途別3エンジン）**:
   - **一から作曲**: `NotaGenSmallQuantizedModel`（パッチ＋文字階層Transformer / Interleaved ABC `[V:1]`/`[V:2]` 生成）または Ollama ABC ストリーム。
   - **ピアノ対話継続・小節補完**: `AnticipatoryContinuationEngine`（GPU常駐 FP16 継続・インフィリング）。
   - **チェロ対話伴奏**: `MIDIGPTAccompanimentEngine`（CPU並列・チェロ帯域 `100–600 Hz` 自動回避・3〜4声部伴奏）。
3. **声部連結 ＆ 掛留解決 (`LivePolyphonyHarmonyGuard` / `HarmonicBeautyRealizer`)**:
   - 強拍でのコードトーン着地保証、4-3 / 9-8 / 7-6 掛留音の順次解決、跳躍後の逆行順次回復（Post-Leap Gap-Fill Law）。
   - 左右手の立体ボイシング（左手：バス根音＋5度＋10度展開／テノール内声対旋律、右手：歌唱主旋律＋内声ハーモニー）。
4. **両ペダル連続カーブ同期 (`CC#64` Damper + `CC#67` Una Corda)**:
   - 和声交代 `-30ms`〜`-50ms` 前の先行ハーフペダル踏み替えと、終止/弱奏時の左ペダル `CC#67` 連続補間。
5. **物理安全ゲート (`LocalPreflightChecker` + `DisklavierStageOutputLayer`)**:
   - 同時発音30音ハードリミット、2ms同時打鍵16音上限、持続音20音上限、ベロシティ `[20, 105]`、最短ノート長 `>= 35ms`、緊急全消音 `< 10ms`。
