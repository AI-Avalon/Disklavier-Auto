# Disklavier-Auto 移行引き継ぎ仕様書 (`HANDOVER_SPEC.md`)

> **対象リポジトリ**: `AI-Avalon/Disklavier-Auto` (`main` ブランチ / 基準コミット `7fb9aea`)  
> **作成日**: 2026-11-16  
> **移行先環境**: Windows 11 / NVIDIA GeForce RTX 3070 (8GB VRAM) / ローカルAI自律セッション・自動作曲システム再構築向け  
> **引き継ぎ目的**: 現在の macOS (Apple Silicon) 版コードベース、開発履歴、実装済み機能、音響/MIDIパラメータ、macOS固有依存箇所、および既存テスト検証手法を網羅し、新環境側の Antigravity が調査なしで即座に移植・拡張作業へ着手できるようにする。

---

## 目次
1. [現在のディレクトリ構成と全モジュールの役割一覧](#1-現在のディレクトリ構成と全モジュールの役割一覧)
2. [実装済みの全機能リスト](#2-実装済みの全機能リスト)
3. [音響処理（AKG C519ML + Steinberg UR22）および MIDI通信（Yamaha Disklavier）パラメータ詳細](#3-音響処理akg-c519ml--steinberg-ur22および-midi通信yamaha-disklavierパラメータ詳細)
4. [macOS固有の依存箇所と Windows 11（NVIDIA RTX 3070環境）移植時の要修正ポイント一覧](#4-macos固有の依存箇所と-windows-11nvidia-rtx-3070環境移植時の要修正ポイント一覧)
5. [既存のテストスイート・仮想環境テストの実行方法と検証項目](#5-既存のテストスイート仮想環境テストの実行方法と検証項目)

---

## 1. 現在のディレクトリ構成と全モジュールの役割一覧

### 1.1 リポジトリ全体ツリー

```text
Disklavier-Auto/
├── .agent/skills/                          # Antigravity プロジェクト内蔵 Agent Skills (4種)
│   ├── apple-silicon-parallel-evolution/SKILL.md
│   ├── human-virtuoso-expressive-midi/SKILL.md
│   ├── live-performance-japanese-ui/SKILL.md
│   └── sakurai-virtuoso-direction/SKILL.md
├── data/
│   ├── human_references/                   # 巨匠リファレンスMIDI (6曲) + 統計プロファイルJSON
│   │   ├── bach_vivace_stretto.mid
│   │   ├── chopin_rubato_nocturne.mid
│   │   ├── debussy_luminous_planing.mid
│   │   ├── master_performance_profiles.json
│   │   ├── rachmaninoff_bell_counterpoint.mid
│   │   ├── scherzando_virtuoso_etude.mid
│   │   └── tchaikovsky_passionate_cantabile.mid
│   ├── imslp_midi/                         # IMSLP 11大クラシック名曲 MIDI (自動生成SMF)
│   │   ├── bach_suite1_bwv1007.mid
│   │   ├── brahms_sonata1_op38.mid
│   │   ├── chopin_nocturne_sonata.mid
│   │   ├── debussy_clair_sonata.mid
│   │   ├── faure_elegie_op24.mid
│   │   ├── faure_sicilienne_op78.mid
│   │   ├── liszt_1850_liebestraum_campanella_cascade.mid
│   │   ├── mendelssohn_op109.mid
│   │   ├── rachmaninoff_1901_prelude_g_minor_bells.mid
│   │   ├── saint_saens_swan.mid
│   │   └── tchaikovsky_1893_pathetique_cantabile.mid
│   └── telemetry/                          # SQLite WAL, 実験結果JSON, DNA/Seed DB, 書き出し先
│       ├── composed_masterwork.mid
│       ├── experiments_summary.json
│       ├── exports/                        # (.gitignore対象) MIDI/MusicXML/WAV ダウンロード出力
│       ├── imslp_knowledge_base.json       # IMSLP 11曲カタログ + Deep DNA 数値特徴量
│       ├── rd_studio.sqlite                # (.gitignore対象) WAL SQLite DB (5テーブル)
│       └── seed_genome_pool.json           # SeedMemoryPool 抽出モチーフ群
├── docs/
│   ├── HARDWARE_TEST_PROCEDURE.md          # 実機 Disklavier 手動安全検証プロトコル
│   └── PERFORMANCE.md                      # Phase 1-3 実測ベンチマーク比較レポート
├── obsidian_vault/                         # Obsidian 連携ナレッジグラフ＆殿堂入りアーカイブ
│   ├── 00_Dashboard/Evolution_Overview.md
│   ├── 01_Motif_Library/*.md               # 14ファイルのモチーフ解析ノート
│   ├── 02_Hall_of_Fame_Performances/       # スコア85+の殿堂入りノート(.md) + midi/*.mid
│   ├── 03_Harmonic_Discoveries/*.md        # 転調・倍音・対位法の発見ノート (3ファイル)
│   └── 04_Human_Directives.md              # リアルタイム編集対応の人間指示書
├── scripts/
│   └── run_ci_local.sh                     # ローカル完結CIスクリプト (ruff + pytest + integrity)
├── src/
│   ├── audio/
│   │   └── c519ml_monitor.py               # UR22mkII + AKG C519ML 音響解析・かぶり除去
│   ├── composer/
│   │   └── ai_classical_composer.py        # 対話型展開作曲 & 3モード パッセージ返奏
│   ├── evolution/
│   │   ├── __init__.py
│   │   ├── actor_critic.py                 # 並列候補生成 & 5軸/6軸自己採点エンジン
│   │   ├── continuous_virtuoso.py          # ノンストップ二重リングバッファ進化エンジン
│   │   ├── human_reference_teacher.py      # 巨匠法則・Emergent着想・4大技巧・Meta-AI Director
│   │   ├── seed_ingestor.py                # MIDI資産スキャン & SeedMemoryPool構築
│   │   └── song_workspace.py               # マルチソングタブ & タイムマシン永続化
│   ├── experiments/
│   │   ├── run_exp1_exp2_calibration.py    # Exp1 (VDL実測フィット) & Exp2 (RT60/Roughness)
│   │   └── run_exp3_exp4_exp5.py           # Exp3 (代奏&かぶり除去), Exp4 (ABテスト), Exp5 (作曲ループ)
│   ├── features/
│   │   └── feature_matrix_100.py           # 全100機能カタログ・譜面座標・MIDI/MusicXML/WAV・QR・Doctor
│   ├── midi/
│   │   └── imslp_library.py                # IMSLP 11大名曲生成・Deep DNA解析・5段階最適化
│   ├── obsidian/
│   │   ├── __init__.py
│   │   └── obsidian_brain.py               # Vault自動生成・WikiLinks・mtimeキャッシュ知識グラフ
│   ├── safety/
│   │   ├── disklavier_safety_guard.py      # 実機保護ミドルウェア・VDL逆算・Conditionスケジューラ
│   │   └── preflight_checker.py            # <0.1s オフライン事前監査 & 自動修復エンジン
│   ├── server/
│   │   └── mission_control_server.py       # FastAPI REST & 30Hz 差分 WebSocket サーバー
│   ├── session/
│   │   └── accompaniment_engine.py         # チェロ共演リアルタイム伴奏 & リハーサルTime-Machine
│   ├── telemetry/
│   │   └── telemetry_store.py              # SQLite WAL 永続化・検索/書出・バナー/例外監視
│   ├── ui/
│   │   ├── index.html                      # 9タブ統合ライブスタジオUI (WAI-ARIA準拠)
│   │   └── remote.html                     # スマホ・タブレット用タッチリモコン (/remote)
│   └── virtual_twin/
│       ├── __init__.py
│       └── virtual_disklavier.py           # 物理シミュレータ + 44.1kHz内蔵ピアノ/チェロ音源
├── tests/
│   ├── test_autonomous_virtuoso.py         # Virtual Twin / SeedPool / Evolution / Obsidian 結合テスト
│   ├── test_phase1_security.py             # トークン認証 / Origin / パストラバーサル / Pydantic境界テスト
│   ├── test_safety_guard.py                # SafetyGuard 物理保護・VDL錨定・プロファイル切替テスト
│   ├── test_scheduler_jitter.py            # Hybrid Condition スケジューラ ジッター計測テスト
│   └── test_studio_overhaul.py             # 100機能 / エクスポート / タイムマシン / ASGI統合テスト
├── .gitignore
├── EXPERIMENT_REPORT.md                    # Exp1〜5 実機実験レポート
├── FEATURES_100.md                         # 全100機能カタログ定義書 (実装済90 / 準備中10)
├── LICENSE
├── pyproject.toml                          # パッケージ定義 (Python >=3.11, audio/dev extras, ruff/pytest)
├── README.md                               # システム概要・5大Mermaid図・ステージ台本
├── run_studio.sh                           # Mac用スタジオ起動スクリプト (既定 127.0.0.1 / --lan)
└── validate_system_integrity.py            # 全7スイート統合整合性＆実機安全性検証
```

### 1.2 全モジュール・スクリプトの役割と主要クラス/関数一覧

| ファイルパス | 主なクラス・関数・定数 | 役割と責務 |
|---|---|---|
| `pyproject.toml` | `requires-python = ">=3.11"` | コア依存 (`fastapi>=0.115.0`, `uvicorn>=0.30.0`, `pydantic>=2.7.0`, `mido>=1.3.0`, `numpy>=1.26.0`, `scipy>=1.13.0`, `matplotlib>=3.9.0`, `requests>=2.31.0`)、任意依存 `[audio]` (`sounddevice>=0.4.6`, `soundfile>=0.12.1`, `python-rtmidi>=1.5.0`)、`[dev]` (`pytest>=8.0.0`, `ruff>=0.4.0`)、`ruff`/`pytest` 設定 |
| `src/audio/c519ml_monitor.py` | `C519MLAudioMonitor`, `midi_to_name`, `freq_to_midi`, `midi_to_freq` | Steinberg UR22mkII (AKG C519ML) からの 44.1kHz / 735サンプル (60fps) 収音、4096pt RFFT → 60ビンCQT変換 (`MIDI 36..95`)、Harmonic Peelingによる重音/フラジオレット検出、Sethares粗さ算出、MIDI連動クロストーク減算、リアルタイム音響減衰監視 (`_verify_acoustic_sustain`) |
| `src/safety/disklavier_safety_guard.py` | `DisklavierSafetyGuard`, `VDLModel`, `ScheduledMidiEvent`, `DISKLAVIER_MODEL_PROFILES` | 実機Yamaha Disklavierと全アプリ出力の関所。双方向MIDI IN/OUT収集、VDL打弦遅延逆算スケジューリング、鍵盤別 `key_generation` による古いNote-Off遮断、30音電源クラッシュ防止 (12/16同時音・20保持上限・28音/秒)、`CC#64`限定保護、熱保護ウォッチドッグ (8s/240s)、`threading.Condition` 低ジッターディスパッチ、閉ループ自動ヒール |
| `src/safety/preflight_checker.py` | `LocalPreflightChecker` | `<0.1s` ローカル事前監査 (`audit_events`) ＆自動修復 (`audit_and_repair`)。全候補チャンク、パッセージ応答、MIDI/MusicXML/WAV書き出しの直前に物理制約違反をゼロ化 |
| `src/virtual_twin/virtual_disklavier.py` | `VirtualDisklavierTwin`, `DisklavierPhysicsSimulator`, `SynthVoice` | 実機非接続時・開発時のデジタルツイン。物理現象（空振りGhost Note、VDL誤差、1ms>=17音電源サグ、>=30音クラッシュ、45msダンパー慣性、熱解放）シミュレータ＋44.1kHzステレオ倍音加算シンセ（ピアノ非調和性部分音＋チェロ5.4Hzビブラート） |
| `src/midi/imslp_library.py` | `IMSLPMasterworkLibrary`, `IMSLPDeepAnalyzer`, `_build_masterwork_definitions`, `optimize_midi_5_stages`, `save_piece_to_smf` | IMSLP 11大クラシック名曲定義、ディープ音楽DNA解析 (`imslp_knowledge_base.json` 出力)、5段階Disklavier最適化、シンコペーションCC#64付SMF Type-1保存・再生 |
| `src/composer/ai_classical_composer.py` | `compose_expanded_dialogue_piece`, `reply_to_passage_virtuoso`, `listen_and_compose_live`, `start_human_turn_listening`, `inject_interactive_motif_notes` | 双方向対話モード (`HUMAN_TURN` 収録 → 8〜90秒4部楽式展開作曲)、および人間演奏パッセージへの3モードヴィルトゥオーゾ即時返奏 (`contrapuntal_reply` / `liszt_rachmaninoff_virtuoso` / `evolve_symphonic_masterwork`) |
| `src/session/accompaniment_engine.py` | `generate_accompaniment_for_cello_event`, `get_proxy_cello_test_passage`, `run_live_proxy_session_demo`, `record_rehearsal_take_live`, `replay_rehearsal_take_with_style` | チェロ単音/重音/フラジオに対する6スタイル伴奏生成（純正律3度クッション、+20ms Melody Lead、Velocity 1 倍音共鳴）、Piano-as-Cello代奏テストベンチ、リハーサルTime-Machine比較 |
| `src/evolution/human_reference_teacher.py` | `HumanReferenceTeacher`, `MASTER_LINEAGES`, `compute_hardware_acceleration_info` | 6巨匠リファレンスMIDI生成 (`data/human_references/`)、創発的着想算出 (`compute_emergent_inspiration`)、True Legato/Vivace法則適用 (`apply_human_master_expression`)、機械的MIDI人間化、4指標/6軸100点レーダー採点、4手/88鍵カスケード/オクターブ連打生成器、Meta-AI Director (`generate_meta_director_work_plan`) |
| `src/evolution/actor_critic.py` | `MusicalGenome`, `MultiObjectiveCritic`, `ParallelCandidateGenerator`, `ARC_STAGES`, `CHORD_MODULATION_ROUTES`, `CANDIDATE_STRATEGIES` | 進化ゲノム、マルチコア `ProcessPoolExecutor` (`spawn`) 並列候補生成（8〜16候補同時評価・スレッドフォールバック付）、5軸+6軸重み付き評価、日本語実況字幕・推敲思考テキスト生成 |
| `src/evolution/continuous_virtuoso.py` | `ContinuousVirtuosoEngine`, `CHARACTER_PRESETS` | 二重リングバッファ (`_ring_buffer`) ノンストップ自動演奏・作曲エンジン、50世代超高速トレーニング (`run_hyper_speed_training`)、タブ/タイムマシン制御、ブラインドA/Bテスト、30秒救出カプセル、第1〜3幕ステージ脚本実行 (`run_stage_act`) |
| `src/evolution/seed_ingestor.py` | `SeedMemoryPool`, `_extract_midi_file_stats`, `_infer_scale_from_pitches` | `data/imslp_midi/*.mid` と `data/telemetry/*.mid` 等をスキャンし、`seed_genome_pool.json` (14+モチーフ) を構築 |
| `src/evolution/song_workspace.py` | `MultiSongWorkspaceManager`, `SongTabState` | ブラウザ風複数曲タブ管理、世代スナップショット (`MAX_GEN_SNAPSHOTS=60`)、Undo/Redo (`MAX_UNDO=40`)、A/B状態保持、タブ復元、JSON保存/読込 |
| `src/obsidian/obsidian_brain.py` | `ObsidianMusicalBrain`, `_parse_directives_text` | `obsidian_vault/` 自動構築、85点以上の殿堂入り(`MAX_HOF_FILES=36`) `.md`+`.mid` 保存、双方向 `[[WikiLinks]]` 更新、`04_Human_Directives.md` 0.5秒間隔監視、mtimeキャッシュ付イン・アプリ知識グラフ＆系統樹エクスプローラ |
| `src/telemetry/telemetry_store.py` | `TelemetryStore`, `resolve_safe_path`, `record_caught_exception`, `get_exception_metrics` | SQLite WAL永続化 (`rd_studio.sqlite`: 5テーブル)、全文/スコア検索、CSV/JSON書出、パストラバーサル防衛、サイレント例外ゼロの集約メトリクス、バナー/Gitログ管理 |
| `src/features/feature_matrix_100.py` | `FEATURE_MATRIX_100`, `get_feature_catalog`, `build_sheet_music_payload`, `export_events_to_midi_file`, `export_events_to_musicxml`, `export_events_to_wav`, `build_cello_open_string_warmup_wave`, `generate_qr_svg`, `get_lan_remote_info`, `run_japanese_system_doctor`, `invoke_feature` | 100機能カタログ（実装済90 / 準備中10）、Living Score大譜表差分座標、SMF/MusicXML 3.1/44.1kHz WAV書出、チェロ開放弦C-G-D-A起動儀式、純Python製QR Version 1〜6 SVG生成、LAN IP検出、日本語ヘルスチェック |
| `src/server/mission_control_server.py` | `app`, `WebSocketBroadcaster`, `lifespan`, `studio_auth_middleware`, 45+ FastAPI endpoints | FastAPI REST & 30Hz多レート差分WebSocketサーバー、起動時ライフサイクル管理、トークン認証 (`STUDIO_AUTH_TOKEN`)、Origin許可制御 |
| `src/ui/index.html` & `src/ui/remote.html` | 9タブ WAI-ARIA HTML/JS UI & スマホリモコン | ジャゴン排除の日本語ライブスタジオUI（大譜表Canvas、88鍵、波紋、6軸レーダー、ピアノロール、SQLiteログ検索、知識グラフモーダル）＋モバイルタッチUI |
| `src/experiments/run_exp1_exp2_calibration.py` & `run_exp3_exp4_exp5.py` | `run_experiment_1_vdl` 〜 `run_experiment_5_ai_composer_loop` | 実機接続時の自動キャリブレーション＆R&D実験（Exp 1〜5）スクリプト（PNGチャートと `experiments_summary.json` を生成） |
| `run_studio.sh` / `scripts/run_ci_local.sh` / `validate_system_integrity.py` | — | Macローカル起動 (`--lan`対応)、ローカルCI一括検証、7スイート実機安全・統合テスト |

---

## 2. 実装済みの全機能リスト

システム全体の機能カタログ（`src/features/feature_matrix_100.py` & `FEATURES_100.md`）には **10カテゴリ × 10機能 = 全100機能（`implemented`: 90機能、`preparing`: 10機能）** が登録されており、さらに Phase 4/5 拡張機能群が統合稼働しています。

### 2.1 GUI・ライブ演出・スマホリモコン
- **9タブ WAI-ARIA 単一行スタジオUI (`src/ui/index.html`)**:
  1. `🎹 ライブ演奏＆生きた五線譜` (`#vtab-stage`): Meta-AI 詩的曲名＆5部楽式バナー、第1〜3幕ステージ脚本ボタン、2D表情パッド (`expression_x` / `expression_y`)、5種のキャラクタープリセット、4大超人間技巧ボタン、3モード双方向パッセージ対話ボタン、「今のひらめきの源泉」バー、Living Score五線譜Canvas、88鍵盤リアルタイム可視化＆ポリフォニック波紋Canvas、CC#64/同時発音数/打鍵速度/VDLステータスバッジ。
  2. `🧬 AIの自己進化＆6軸レーダー` (`#vtab-evolve`): 4指標カード、6軸100点満点レーダーチャート、世代別成長曲線、50世代ブースター、同時生成候補ツリー比較、AI推敲日記 (`critic-logs`)、6軸スモールマルチプルズ、ピアノロール＆CC#64連動キャンバス、6軸重みチューナー（5プリセット＋スライダー）、ブラインドA/Bテスト (`X vs Y`)。
  3. `🎻 チェロ共演＆IMSLP深層解析` (`#vtab-cello`): マイクON/OFF、開放弦C-G-D-A起動儀式、代理チェロ共演テスト、検出音高表示、IMSLP 11名曲深層DNA解析表示、11曲安全再生リスト、人間演奏 vs AIピアニスト応答比較グラフ。
  4. `📓 イン・アプリ進化ノート＆知識グラフ` (`#vtab-notes`): 日本語リクエストメモ編集・送信、殿堂入り系統樹リスト、知識グラフCanvas、マークダウンプレビュー、`<dialog>` 拡大モーダル。
  5. `✨ 全100機能＆健康診断` (`#vtab-features`): 日本語システムヘルスチェック (`run_japanese_system_doctor`)、100機能検索＆カテゴリフィルタ付きワンクリック試用エクスプローラー（準備中10機能は `preparing` バッジを誠実表示）。
  6. `🛡️ 安全監視＆実機プロファイル` (`#vtab-safety`): 機種別プロファイル3段階切替 (`envious_mark_iv` / `dkv_e3_conservative` / `dcx_enspire_pro`)、同時発音数・音/秒時系列グラフ、同音連打間隔(ms)＆ベロシティ分布ヒストグラム、ガード累積介入統計。
  7. `🧪 VDL校正＆ペダル実験` (`#vtab-experiments`): Exp1 VDL実測 vs 理論フィット曲線＋パラメータ手動調整フォーム、Exp2 ハーフペダルRT60＆濁り曲線、Exp3〜5 KPIサマリー。
  8. `⚡ 高速化・ジッター＆通信診断` (`#vtab-perf`): 30Hz WebSocket差分圧縮帯域 (KB/s) グラフ、Condition起床ジッター＆Preflight監査レイテンシ、並列エンジン＆Obsidian mtimeキャッシュ状態、捕捉済み例外モニター。
  9. `🗄️ SQLiteログ検索＆書き出し` (`#vtab-logs`): 5テーブル横断検索（キーワード＋最低スコア）、CSV (`utf-8-sig`) / JSON エクスポート。
- **ブラウザ風マルチソングタブ ＆ 進化タイムマシン**:
  - 曲タブの作成 (`+ 新しい曲を作る`)・切替・クローズ・閉じたタブの復元 (`MultiSongWorkspaceManager`)。
  - Undo/Redo (`MAX_UNDO=40`)、世代スライダースクラブ (`MAX_GEN_SNAPSHOTS=60`)、第1世代(A) vs 最新(B) 聴き比べ、**30秒前救出カプセル (`rescue_last_30s_to_tab`)**、4種のリセット/変化ボタン（完全リセット / 頭脳維持ムード変更 / ひらめきスパーク / いいね！投票）。
- **モバイルタッチリモコン (`src/ui/remote.html`, `/remote`)**:
  - 純Python製 SVG QRコード (Version 1〜6, Reed-Solomon ECC Level L) モーダル。LAN IP自動検出＋`--lan` モード時のセッション `?token=` 付与。
- **3秒強制発音カウントダウン ＆ ジャゴン排除**:
  - 実機/仮想音源の重要発音前に3秒カウントダウンバナー (`SOUND_COUNTDOWN`) を表示。
  - 機械的翻訳語（`モチーフ即時吸収`, `多面的`, `ポリフォニック・エントロピー`, `アクター・クリティック重み`, `ゲート長`）を UI・ドキュメント上で禁止・排除し、`validate_system_integrity.py` と `test_studio_overhaul.py` で自動検証。

### 2.2 リアルタイム作曲・自己進化・タイトル/メロディ生成
- **二重リングバッファ・ノンストップ生成 (`ContinuousVirtuosoEngine`)**:
  - バックグラウンド作曲スレッド (`_composer_loop`, 120ms周期) と再生スレッド (`_playback_loop`, 40ms周期) が `LOOKAHEAD_MIN_S = 5.5s` / `LOOKAHEAD_TARGET_S = 8.5s` を維持して無音ギャップを防止。
- **並列候補生成 (`ParallelCandidateGenerator`)**:
  - 毎世代 `8〜16` 候補を12の戦略 (`CANDIDATE_STRATEGIES`) と8つの転調ルート (`CHORD_MODULATION_ROUTES`) を組み合わせて生成。`prefer_multiprocess=True` 時（50世代ブースター初回等）は `ProcessPoolExecutor(mp_context="spawn")`、リアルタイム時は高速な `ThreadPoolExecutor` で並列評価。
- **多軸評価システム (`MultiObjectiveCritic` & `HumanReferenceTeacher`)**:
  - **5軸合計 (100点)**: 声部連結・対位法 `25点` + 動機連続性 `20点` + 和声独創性 `20点` + 表現アーティキュレーション `15点` + Disklavier物理適性 `20点`（平行5度/8度減点、反行ボーナス、解決跳躍ボーナス含む）。
  - **6軸100点満点レーダー**: `melody_beauty` (20%), `harmonic_drama` (17%), `counterpoint` (17%), `human_realism` (18%), `virtuoso_brilliance` (14%), `disklavier_safety` (14%)。外部重みプリセット (`balanced`, `cantabile`, `virtuoso`, `safe_concert`, `contrapuntal`) やスライダー (0.2〜2.5) でリアルタイム再重み付け可能。
  - **成長カーブ演出**: 第1世代は意図的に `<= 74.8` 点（第2世代 `<= 80.5` 点）の素朴な原石として生成し、世代を重ねて `93〜99.6` 点へ進化する過程を日本語の推敲ログ (`commentary_ja`, `pianist_thought_bubble_ja`, `live_story_subtitle_ja`) と共に可視化。
- **Meta-AI Director による曲名・楽式・オリジナル旋律生成**:
  - `generate_meta_director_work_plan()`: 詩的な日本語曲名・欧文曲名・調性・Op番号（例：*『幻想曲 変ロ短調「蒼き星の奔流」Op.14』*）と5段階楽式（Ⅰ. 静謐な序奏 → Ⅱ. 主題提示 → Ⅲ. 対位法的展開 → Ⅳ. 超絶技巧クライマックス → Ⅴ. 余韻のコーダ）を自動構築。
  - `_compose_original_phrase_intervals()` + `_transform_intervals()`: 世代・局面・表情X/Yからオリジナル音程列を生成し、動機変容 (`original`, `inversion`, `retrograde_inversion`, `augmentation`, `diminution`, `stretto`) を施して旋法へ量子化。
  - **創発的着想算出 (`compute_emergent_inspiration`)**: 固定の「作曲家模倣ボタン」を使わず、ゲノムの反行重み・半音階率・対位法密度・ペダル深度・ルバート深度から5巨匠 (`tchaikovsky`, `rachmaninoff`, `debussy`, `chopin`, `bach`) の影響率をソフトマックス算出（合計100%）。
- **Disklavier ならではの超人間的4大技巧**:
  1. `generate_4hand_virtuoso_layering`: 重低音バス + チェロ対旋律2声 + 主旋律 (+18ms先行/+12..20vel強化) + 高音パール装飾の5層ポリフォニー。
  2. `generate_88key_crystal_cascade`: Velocity 1 サイレント開放弦ハロー + 88鍵域 (MIDI 24..105) 1.8msマイクロスタッガー分散掃引。
  3. `sympathetic_halo`: `Velocity = 1` でハンマーを打弦させずダンパーだけを持ち上げ、倍音共鳴のみを響かせる技巧。
  4. `generate_octave_alternating_tremolo`: `[p, p+7, p+12, p+7]` を `52ms` 間隔で分散打鍵し、単鍵連打を `<= 9.6 Hz` に抑えた高速トレモロ。

### 2.3 IMSLP MIDI解析・双方向パッセージ対話・チェロ共演
- **IMSLP 11大名曲ライブラリ＆Deep Musical DNA (`src/midi/imslp_library.py`)**:
  - 収録11曲: Debussy (*Clair de Lune*), Saint-Saëns (*Le Cygne*), Fauré (*Élégie Op.24*, *Sicilienne Op.78*), J.S. Bach (*BWV 1007 Prelude*), Chopin (*Op.9 No.2*), Brahms (*Op.38*), Mendelssohn (*Op.109*), Tchaikovsky (*Pathétique/Cantabile*), Rachmaninoff (*Op.23 Bells*), Liszt (*Liebestraum/Campanella*)。
  - `IMSLPDeepAnalyzer`: 順次進行率、6度/8度跳躍率、黄金分割クライマックス位置、6分割テンションカーブ、反行率、半音階率、アルペジオ音域幅、内声オブリガート密度、カデンツァ有無を解析し `imslp_knowledge_base.json` へ永続化。
  - `optimize_midi_5_stages`: 禁止CC遮断 → Ghost Velocity補正 (`<28 -> 36`, `>105 -> 105`) → 12音超和音の1.8ms分散 → シンコペーションCC#64先行 → VDL遅延予測＆最小60ms音価保証。
- **双方向パッセージ対話 (`reply_to_passage_virtuoso`)**:
  - Disklavier MIDI IN、C519ML Audio、GUI入力のいずれから受け取ったパッセージも3モードで即時返奏:
    1. `contrapuntal_reply`: 反行・転回を用いたソプラノ＆チェロ対旋律＋バス対位法。
    2. `liszt_rachmaninoff_virtuoso`: 4手立体ポリフォニー＋オクターブトレモロ＋88鍵クリスタルカスケード。
    3. `evolve_symphonic_masterwork`: 入力パッセージを第1主題に据えた4部展開＋88鍵クライマックス。
- **チェロ共演エンジン (`src/session/accompaniment_engine.py`)**:
  - `generate_accompaniment_for_cello_event`: 6スタイル (`bach`, `saint_saens`, `brahms`, `debussy`, `faure_romantic`, `sympathetic_halo`)。純正律3度衝突回避（中音域の長3度を省き Root+5th+9th に置換）、Goebl +20ms Melody Lead、音域別ハーフペダル、プロキシテストパス (`Single`, `5th/6th Double-Stop`, `D5 Flageolet`) 搭載。
- **対話展開自動作曲 (`compose_expanded_dialogue_piece`)**:
  - 人間の収録フレーズを `8〜90秒` の4楽章構成（提示部→対話展開部→クライマックス→余韻コーダ）、5発達モード (`debussy_impressionist`, `call_and_response`, `faure_chopin_lyric`, `bach_counterpoint`, `dramatic_crescendo`) に展開してSMF保存・実演。

### 2.4 コード・アーティキュレーション認識、ベロシティ/ペダル制御、安全装置
- **音響入力側の認識 (`C519MLAudioMonitor`)**:
  - **コード/重音・フラジオレット認識**: 60ビンCQT上で Harmonic Peeling（最大3音）を実施し、オクターブ・12度・長17度の倍音成分 (`[0, +12, +19, +24, +28]`) を減算することで `is_double_stop`（2音以上の重音）および `is_flageolet`（`MIDI >= 69` 単音倍音）を識別。
  - **ボウイング・アーティキュレーション認識**: 線形RMS増分 `rms_delta` により `Spiccato / Marcato`（`> 0.015`）と `Legato / Sostenuto` を即時判別。
- **MIDI/フレーズ側の認識＆表現制御 (`HumanReferenceTeacher` & `ai_classical_composer.py`)**:
  - 入力パッセージの平均音価 `< 0.18s` でスタッカート系、`>= 0.18s` でレガート・カンタービレ系を判別。根音からの短3度 (`+3 semitones`) の有無で長調/短調を判定。
  - **True Cantabile Legato (`expression_x <= 0.15`)**: 隣接音オーバーラップ `+5〜+15 ms`、Note-Off離鍵ベロシティ `15〜30`、主旋律リード `+15〜+25 ms` ＆ `+12〜+20 vel`、和音ロール `10〜22 ms`。
  - **True Vivace / Scherzando (`expression_x > 0.15`)**: 最小スタッカート長 `35〜50 ms`（`<30 ms` 厳禁）、最小音間ギャップ `>= 20〜24 ms`、速い離鍵ベロシティ `65〜90`、強弱対比（通常 `30〜85` vs アクセント `86〜99`）。
- **ベロシティ・右ペダル（CC#64）制御**:
  - 有効打鍵ベロシティ: `DisklavierSafetyGuard` は `[28, 105]` クランプ（`LocalPreflightChecker` は `[20, 105]`）。`1〜15` の空振り（Ghost Note）を禁止し、`sympathetic_halo=True` の `Velocity=1` のみ例外許可。
  - ペダル限定＆先読み制御: `CC#64`（右ダンパーペダル）のみ許可し、`CC#66`（ソステヌート）/ `CC#67`（ウナコルダ）を完全ブロック。和音・小節境界の `-25〜-50 ms` 先行でペダルを一旦 `18〜22` に浅くリセットし、`+100 ms` 前後でハーフペダルスイートスポット（`35〜80`）へ踏み直すシンコペーション・ハーフペダルを実行。
- **30音同時発音クラッシュ防止・2層物理安全装置**:
  - **第1層 (`LocalPreflightChecker`)**: `<0.1s` で全イベント列を監査・自動修復（20保持音上限、12/16同時音1.8msスタッガー、28音/秒上限、>=35ms音価、>=66ms同音連打オクターブ分散）。
  - **第2層 (`DisklavierSafetyGuard`)**: 実時間ディスパッチ時に 2ms窓 `12/16` 音超過時の 1.8ms 底上げスタッガー、`MAX_ACTIVE_VOICES=20` 超過時の最古内声スティール（30音電源クラッシュ閾値未満を厳守）、`key_generation[note]` による古いNote-Off破棄（反復打鍵の途切れ根絶）、熱保護ウォッチドッグ（単鍵8秒・ペダル240秒自動解放）、即時全88鍵解放 `emergency_panic()`（`CC#64=0`, `note 21..108 note_off`, `CC#123=0`）。

### 2.5 楽譜表示・書き出し・Obsidian連携・100機能のうち「準備中 (10機能)」の内訳
- **Living Score 五線譜 ＆ 書き出し (`feature_matrix_100.py`)**:
  - `build_sheet_music_payload`: MIDI 60以上でト音、未満でヘ音大譜表へ割り当て、`pp〜ff`/`halo` 強弱記号、スラー/スタッカート表記、前世代との差分 (`mutated: true` + 日本語変更理由 `mutation_reason_ja`) を生成。
  - `/api/export/midi` (SMF Type-1), `/api/export/musicxml` (MusicXML 3.1 Grand Staff), `/api/export/wav` (44.1kHz 16-bit PCMステレオ音響合成)。
- **Obsidian 連携 (`obsidian_brain.py`)**:
  - `obsidian_vault/` への殿堂入り自動保存（`>= 85.0`点、最大36件）、双方向WikiLink、`00_Dashboard/Evolution_Overview.md` のMermaid自動更新、`04_Human_Directives.md` の0.5秒監視、およびイン・アプリ知識グラフ＆系統樹ビューア。
- **全100機能カタログの誠実分類 (`_PREPARING_FEATURE_IDS`)**:
  - **90機能 (`implemented`)**: 上記のGUI・作曲・進化・安全・Obsidian・実験・エクスポート・リモコン群。
  - **10機能 (`preparing`)**: `B9` (`/api/virtuoso/absorb_motif` 互換経路), `F3` (`/api/session/proxy_cello`), `F4` (`/api/dialogue/generate` 旧ルート; 現行は `/api/dialogue/passage_reply` や `/api/composer/listen_and_compose`), `F5`, `F7`, `F8`, `F10`, `H1` (`/api/virtual_twin/config` 互換名; 現行は `/api/twin/config`), `H3` (`/api/sound/countdown`), `H9`。※ `invoke_feature()` はこれらに対し誠実に `status: "preparing"` を返す設計。

---

## 3. 音響処理（AKG C519ML + Steinberg UR22）および MIDI通信（Yamaha Disklavier）パラメータ詳細

### 3.1 音響処理パラメータ一覧 (`C519MLAudioMonitor` / `VirtualDisklavierTwin` / `mission_control_server.py`)

| パラメータ名 | 現行設定値 / 式 | 定義箇所・補足 |
|---|---|---|
| デバイス検索キーワード | `"Steinberg UR22"` | `C519MLAudioMonitor.__init__` (`c519ml_monitor.py:59`)。未検出時はデフォルト入力、失敗時は `_fallback_loop`（`N(0, 0.0012)` 模擬ノイズ）へ縮退 |
| サンプリングレート (`SAMPLE_RATE`) | `44100` Hz | `c519ml_monitor.py:53`, `virtual_disklavier.py:397` (+48Vファンタム電源駆動の C519ML を想定) |
| オーディオバッファ (`BLOCK_SIZE`) | `735` samples (`16.67 ms` / 正確に `60.0 fps`) | `44100 / 60 = 735`。入力: 1ch `float32`、出力 (Virtual Twin): 2ch `float32` |
| FFTサイズ (`FFT_SIZE`) | `4096` samples (Hanning窓) | `c519ml_monitor.py:55, 81`。周波数分解能 `≈ 10.77 Hz` |
| 音響リングバッファ容量 | `882,000` samples (`20.0 秒`) | `SAMPLE_RATE * 20` (`c519ml_monitor.py:72`) |
| 対数CQTピッチビン | `60` ビン (`MIDI 36 / C2 / 65.4Hz` 〜 `MIDI 95 / B6 / 1975.5Hz`) | 半音幅 `2**(-0.5/12) .. 2**(+0.5/12)` の最大スペクトル値を抽出 (`c519ml_monitor.py:79, 238-246`) |
| 環境ノイズフロア (`noise_floor_dbfs`) | 既定 `-58.5 dBFS`（起動時実測を `[-72.0, -48.0]` にクランプ） | `mission_control_server.py:181`（実測値 `-58.7 dBFS`） |
| 有効音高検出閾値 (`threshold_db`) | `noise_floor_dbfs + 9.5 dB`（約 `-49.0 dBFS`） | `c519ml_monitor.py:364` |
| オンセット検出閾値 | `rms_delta > 0.0045` かつ `rms_dbfs > threshold_db` | 最短オンセット間隔 `0.14 s` (`140 ms`)、`c519ml_monitor.py:370-371` |
| ボウイング判別閾値 | `rms_delta > 0.015` → `"Spiccato / Marcato"`、それ以外 `"Legato / Sostenuto"` | `c519ml_monitor.py:382-384` |
| BPM推定フィルタ | 有効IOI `[0.18s, 2.2s]` の中央値から `0.75 * prev + 0.25 * clamp(inst_bpm, 40, 168)` | `c519ml_monitor.py:375-380` |
| 倍音剥離 (Harmonic Peeling) | 最大 `3` 反復。`[0, +12, +19, +24, +28]` ビンを `-18 dB`、隣接 `±1` を `-10 dB` 減算 | 重音 (`>=2`)・フラジオ (`MIDI >= 69` 単音) を判別。Proxyモード保持時間 `0.45 s` (`c519ml_monitor.py:402-436`) |
| MIDI連動クロストーク除去 | 基音＋第2〜4倍音ゲイン `[1.0, 0.45, 0.22, 0.12]` を推定し線形パワー領域で `0.82` 倍減算 | `est_db = -52.0 + (vel / 105.0) * 32.0 + 20 * log10(h_gain)`、`rem_lin = max(1e-5, raw_lin - 0.82 * bleed_lin)`。実測平均 `+10.74 dB` SNR改善 (`c519ml_monitor.py:252-271`) |
| 音響サスティン連続性監視 | `rms_dbfs > noise_floor_dbfs + 4.5 dB` で持続判定 | `90%` 未満で「自動Gate/Pedal補正中」表示 (`c519ml_monitor.py:316-326`) |
| VDL単音打鍵アタック検出 | `1 ms` (`44` samples) エネルギー包絡。閾値 `max(baseline_peak*1.45, baseline_rms*2.6, 0.0018)` | 実測生遅延から USB オーディオ転送遅延 `8.5 ms` を減算 (`clamp(raw - 8.5, 16.0, 210.0)`) (`mission_control_server.py:473-490`) |
| Sethares 音響的濁り計算 | `60..3500 Hz` の上位10ピーク間で `b1=3.5, b2=5.75` の臨界帯域モデル計算 | `c519ml_monitor.py:330-359` |

### 3.2 MIDI通信・VDL逆算・物理安全パラメータ一覧 (`DisklavierSafetyGuard` / `LocalPreflightChecker`)

| パラメータ名 | 現行設定値 / モデル式 | 定義箇所・補足 |
|---|---|---|
| MIDIポート検出 | `mido.get_output_names()` / `mido.get_input_names()` から `"Disklavier"` 部分一致（大小無視） | `disklavier_safety_guard.py:286-318`。Real-Time Mode (Disklavier側500msプレレイOFF) 前提 |
| MIDI IN エコー除外窓 | `len(_event_heap) > 0` または `(now - last_system_send_time) < 0.30 s` (`300 ms`) | システム送信中のエコーを `"disklavier_echo"`、人間打鍵を `"human_player"` として分離 (`disklavier_safety_guard.py:334-338`) |
| **VDL 逆算遅延モデル (`VDLModel`)** | $\Delta t_{\text{VDL}}(k, v) = \text{clamp}(\text{base\_min} + A \cdot e^{-\lambda(\text{clamp}(v, 24, 105) - 24)} + (60 - k)\cdot|\text{slope}|,\; 20.0,\; 165.0)$ | 既定係数: `base_min_ms=28.0`, `amplitude_ms=115.0`, `decay_rate=0.032`, `bass_mass_slope=-0.14` (`R²=0.965`)。`vel<=1` は `150.0 ms`。送信時刻 `send_t = target_sound_time - vdl_ms/1000`、消音時刻 `off_t = max(target_sound_time, send_t + vdl_ms/1000) + dur_s + overlap_s` (`disklavier_safety_guard.py:65-90, 519-538`) |
| 鍵域制限 (`PIANO_MIN/MAX_NOTE`) | `21` (`A0`) 〜 `108` (`C8`) | 範囲外イベントは破棄またはクランプ (`disklavier_safety_guard.py:154-155`) |
| 同時打鍵上限 (`2.0 ms` 窓) | Soft `12` 音 / Hard `16` 音 (`1.8 ms` 下声部からマイクロスタッガー) | `SIMULTANEOUS_WINDOW_S = 0.002`, `MICRO_STAGGER_STEP_S = 0.0018` (`disklavier_safety_guard.py:156-159`) |
| 同時保持ボイス上限 | 既定 `20` 音 (30音PSUクラッシュ閾値に対し10音の余裕) | 超過時は最低音・最高音を除いた最古内声を自動スティール (`_steal_oldest_inner_voice`, `disklavier_safety_guard.py:160, 742-754`) |
| 秒間打鍵密度上限 | `28` notes/sec (`MAX_NOTES_PER_SEC`) | `1.0 s` スライディング窓 (`disklavier_safety_guard.py:161, 781-783`) |
| 同一鍵リトリガ保護 | SafetyGuard: 最小周期 `55 ms`、再打鍵ギャップ `24 ms` (`send_t - 26 ms` で先行消音) / Preflight: `>= 66 ms` (`<= 15.1 Hz`) | `< 45 ms` の同音重複はカットせずサスティン延長マージ (`repetition_merged`) (`disklavier_safety_guard.py:162-163, 577-591, 810-818`; `preflight_checker.py:32`) |
| 最小音価（ゲート長） | SafetyGuard: `45 ms` (`0.045 s`) / Preflight: `35 ms` (`0.035 s`) | ソレノイド空振り防止 (`disklavier_safety_guard.py:164`; `preflight_checker.py:31`) |
| 有効ベロシティ域 | SafetyGuard: `28..105` / Preflight: `20..105` (`sympathetic_halo=True` 時のみ `1` 許可) | `1..15` の空振りを阻止 (`disklavier_safety_guard.py:165-166`; `preflight_checker.py:29-30`) |
| 離鍵 (Release) ベロシティ | レガート: `15..40` (既定 `22`) / スタッカート: `65..90` | ダンパー衝撃音抑制 (`disklavier_safety_guard.py:167-171`) |
| 許可ペダル・先読み幅 | `CC#64` のみ許可 (`CC#66`, `CC#67` は遮断)。先読み `20..45 ms` (Preflight `30..50 ms`) | ハーフペダル実用スイートスポット `CC#64 = 35..80` (Exp2推奨: 低音`38-46`, 中音`46-58`, 高音`58-72`) |
| 熱保護ウォッチドッグ | 単鍵保持上限 `8.0 s` (`MAX_KEY_HOLD_S`) / ペダル連続保持上限 `240.0 s` (`MAX_PEDAL_HOLD_S`) | `100 ms` 周期で監視。ペダル240秒超過時は `CC#64=0` で `80 ms` 解放後に復帰 (`disklavier_safety_guard.py:172-173, 1067-1100`) |
| 閉ループ自動ヒール (`closed_loop_params`) | `legato_overlap_ms = 38.0`, `min_soft_velocity = 28`, `sustain_pedal_boost = 4` | 直近8音以上の実持続達成率 `< 92.0%` 時、自動で overlap `+6 ms` (上限85ms)・pedal `+2` (上限14) 補正 (`disklavier_safety_guard.py:216-221, 938-951`) |
| **機種別安全プロファイル** | `envious_mark_iv` (12/16音, 20保持, 28/s, 35ms, 8s)<br>`dkv_e3_conservative` (10/12音, 16保持, 22/s, 40ms, 6s)<br>`dcx_enspire_pro` (14/16音, 22保持, 30/s, 30ms, 10s) | `DISKLAVIER_MODEL_PROFILES` (`disklavier_safety_guard.py:115-146`)。UI / `/api/safety/profile` で動的切替 |
| **Hybrid Condition スケジューラ** | 残余 `>14ms`: `cv.wait(min(0.050, rem-0.012))`<br>`2.5..14ms`: `cv.wait(0.0004)`<br>`<=2.5ms`: `time.sleep(0.0001)` 微細待機ループ | Mac実測ジッター 平均 `0.156–0.190 ms` / p95 `0.383–0.745 ms` (`disklavier_safety_guard.py:1050-1065`) |
| **WebSocket 配信レート** | 単一 `WebSocketBroadcaster` @ `30 Hz` (`fast` 30Hz / `virtuoso` 5Hz全キーMD5差分 / `slow` 1Hz) | Mac実測 `41.67 KB/s per client` (`1,422 B` 平均フレーム, `33.18 KB` 初期フレーム) (`mission_control_server.py:1429-1651`) |

---

## 4. macOS固有の依存箇所と Windows 11（NVIDIA RTX 3070環境）移植時の要修正ポイント一覧

Windows 11 + RTX 3070 環境でローカルAI自律セッション・自動作曲システムへ再構築する際、以下の **7領域** を優先順に修正・置換してください。

### 4.1 【最重要・物理動作に直結】Windows タイマー分解能とスケジューラ・ジッター対策
- **現象・原因**: macOS (Machカーネル) では `threading.Condition.wait(0.0004)` や `time.sleep(0.0001)` がサブミリ秒（実測 0.15〜0.19ms）で復帰しますが、**Windows 11 の標準システムタイマー分解能は `15.625 ms` (64 Hz)** です。このまま実行すると `DisklavierSafetyGuard._scheduler_and_watchdog_loop()` 内の待機で毎回 10〜15ms オーバーシュートし、VDL `±3ms` 同時打鍵精度が崩壊して `tests/test_scheduler_jitter.py` (`mean < 2.5ms`, `p95 < 5.0ms`) が確実に失敗します。
- **対象ファイル・箇所**: `src/safety/disklavier_safety_guard.py` (`__init__`, `close`, `_scheduler_and_watchdog_loop` L1050-1065)
- **要修正ポイント**:
  1. `sys.platform == "win32"` 時に `ctypes.windll.winmm.timeBeginPeriod(1)` を `__init__` で呼び出し、システムタイマー分解能を `1 ms` に引き上げる（`close()` で `timeEndPeriod(1)` を呼ぶ）。
  2. `_scheduler_and_watchdog_loop` (L1057-1065) において、Windows環境では `rem <= 0.0045` (4.5ms前) からロックを外し、`time.sleep(0.0001)` の代わりに純粋な `time.perf_counter()` スピンウェイト（または `time.sleep(0)` イールド）で `micro_target` まで待機するように切り替える。

### 4.2 【最重要・ハードウェアI/O】CoreAudio → Windows WASAPI / Steinberg UR22 ASIO への移行
- **現象・原因**: Windows版 `sounddevice` (PortAudio) では、Steinberg UR22mkII が **MME / Windows DirectSound / Windows WASAPI / ASIO** の複数ホストAPIで重複列挙されます。現在の `_find_device()` (`c519ml_monitor.py:127-146`) は最初に名前一致したデバイス（通常は高遅延な MME）を掴んでしまいます。また、ASIOは単一アプリ排他（または全二重 `sd.Stream` 必須）である点、Windowsの既定サンプリングレートが `48000 Hz` に設定されている場合の不一致にも注意が必要です。
- **対象ファイル・箇所**:
  - `src/audio/c519ml_monitor.py` (`_find_device` L127-146, `start` L148-170)
  - `src/virtual_twin/virtual_disklavier.py` (`_start_audio_engine` L427-464, 特に L444 `"Mac CoreAudio Default Output"`)
  - `src/server/mission_control_server.py` (L490 の USB転送遅延オフセット `8.5 ms`)
- **要修正ポイント**:
  1. Windows用の **Yamaha Steinberg USB Driver** を導入した上で、`_find_device()` で `sd.query_hostapis()` を参照し、**`"Windows WASAPI"`**（排他問題がなく入出力分離が容易）または **`"ASIO"`** を優先選択するフィルタを実装する。
  2. `SAMPLE_RATE = 44100` で開けないオーディオドライバ（48kHz固定機器）に対する自動フォールバック、または Windows サウンド設定での `44100 Hz` 統一を案内・検証する。
  3. L444 の `"Mac CoreAudio Default Output"` 表記や UI/JSON 内の `"Mac内蔵"` 文言を OS に応じた動的表現へ更新する。
  4. `mission_control_server.py:490` の `raw_latency_ms - 8.5` は macOS CoreAudio + UR22mkII の実測オフセットであるため、Windows WASAPI/ASIO 環境で `run_exp1_exp2_calibration.py` を再計測し再校正する。

### 4.3 【重要・MIDI】CoreMIDI → Windows WinMM (マルチプロセス排他制約) への移行
- **現象・原因**: Windows の `mido` + `python-rtmidi` は **WinMM** バックエンドを使用します。CoreMIDI と異なり、Windows の WinMM は **ポート名末尾にインデックス番号が付与される**（例: `"Disklavier 0"`, `"Yamaha USB-MIDI-1 1"`）ほか、**1つのMIDIポートを複数プロセスから同時に開けない（排他占有）** 制約があります。
- **対象ファイル・箇所**:
  - `src/safety/disklavier_safety_guard.py` (`_connect_ports` L286-320)
- **要修正ポイント**:
  1. Windows 11 用 **Yamaha USB-MIDI Driver** をインストールする。
  2. `preferred_name` を環境変数 `DISKLAVIER_MIDI_PORT` で上書き可能にし、フォールバック候補 (`"disklavier"`, `"yamaha"`, `"usb-midi"`) を柔軟にマッチさせる。
  3. FastAPI / Uvicorn 起動時は必ず **`--workers 1`**（シングルプロセス）で起動し、プロセス終了漏れによる MIDI ポート占有ロック（`RtMidiError: MidiInWinMM::openPort`）を防ぐ。

### 4.4 【重要・GPU/AI再構築】Apple Silicon (MPS / Accelerate) → NVIDIA RTX 3070 (CUDA) への移行
- **現状のMac依存箇所**:
  - `src/evolution/human_reference_teacher.py:114-137` (`compute_hardware_acceleration_info`): `torch.backends.mps.is_available()` のみをチェックし、未検出時は `"Apple Accelerate SIMD (NumPy Vectorized)"` を返している。
  - `src/evolution/actor_critic.py:939-951` (`ParallelCandidateGenerator._get_process_pool`): `mp_context=multiprocessing.get_context("spawn")` を使用（Windows の標準コンテキストも `spawn` であるため互換性は高い）。
  - `src/features/feature_matrix_100.py:34, 86, 87, 1114`, `src/ui/index.html:698, 1182, 1396`, `pyproject.toml:8`, `.agent/skills/apple-silicon-parallel-evolution/SKILL.md`: UIやテキストが `"Apple Silicon"` 固定。
- **要修正・拡張ポイント (RTX 3070 8GB VRAM 活用)**:
  1. `compute_hardware_acceleration_info()` で `torch.cuda.is_available()` を最優先チェックし、`torch.cuda.get_device_name(0)` / VRAM容量 (`torch.cuda.get_device_properties(0).total_memory`) をテレメトリへ露出する（PyTorch CUDA版 `torch` を `pyproject.toml` の `[cuda]` 等に追加）。
  2. Windows の `spawn` マルチプロセスではワーカー起動時にモジュールが再インポートされるため、`actor_critic.py` のワーカー関数 (`_evaluate_candidate_worker`) が `mission_control_server.py`（インポート時に L166-202 でハードウェアI/Oとスレッドを即時起動する構造）を絶対にインポートしない設計を維持し、スクリプト起動時は `if __name__ == "__main__":` ガードを徹底する。
  3. **ローカルAI自律システム再構築の指針**: RTX 3070 の CUDA コアを活用し、現行のルールベース＋進化的探索 (`actor_critic.py`) を基盤としつつ、ローカル LLM / Music Transformer / リアルタイム音響特徴抽出（PyTorch GPU バッチ評価・オーディオオンセット推論）を CUDA 上で高速推論する構成へ拡張可能（ただし **最終出力は必ず `<0.1s` `LocalPreflightChecker` と `DisklavierSafetyGuard` を通す安全二層構造を維持** すること）。

### 4.5 【必須】パス表記（POSIX `/` vs Windows `\`）の統一
- **現象・原因**: Windows上で `Path.relative_to(...)` をそのまま `str(...)` に変換するとバックスラッシュ `\`（例: `00_Dashboard\Evolution_Overview.md`）になり、Markdownリンク、API JSON、および `resolve_safe_path()` の先頭スラッシュ/パストラバーサル判定で不整合が生じる箇所があります。
- **要修正ファイル・行番号**（すべて `.as_posix()` へ変更推奨）:
  - `src/obsidian/obsidian_brain.py:508` (`rel = str((folder / f"{safe_id}.md").relative_to(self.vault_dir))` → Windows環境では `\` が含まれ、L509の `resolve_safe_path` 自体は通るがクロスプラットフォーム一貫性のため `.as_posix()` 推奨)
  - `src/obsidian/obsidian_brain.py:515, 569`
  - `src/midi/imslp_library.py:1148` (`str(smf_path.relative_to(BASE_DIR))` → `.as_posix()`)
  - `src/evolution/human_reference_teacher.py:321` (`str(mid_path.relative_to(BASE_DIR))` → `.as_posix()`)
  - `src/evolution/seed_ingestor.py:199, 224` (`str(...relative_to(BASE_DIR))` → `.as_posix()`)
- **補足**: `src/telemetry/telemetry_store.py:58-85` の `resolve_safe_path()` は既に `pathlib.Path` と `is_relative_to()` で安全に実装されており、Windows ドライブレター付き絶対パスも `raw_p.is_absolute()` で適切に拒否できます。

### 4.6 【必須】起動スクリプト・ローカルCIスクリプト・仮想環境パスの Windows 対応
- **対象ファイル**:
  - `run_studio.sh`: Bash、`PYTHONPATH=. .venv/bin/uvicorn ...`、および macOS 専用の `open -na "Google Chrome" --args --app=...` を使用。
  - `scripts/run_ci_local.sh`: `.venv/bin/python` と `.venv/bin/ruff` を参照。
- **要修正ポイント**:
  - Windows PowerShell 用の `run_studio.ps1`（または `.bat`）を新規作成し、`.venv\Scripts\uvicorn.exe`、`Start-Process "chrome.exe" "--app=http://127.0.0.1:8765"`（フォールバック `Start-Process "http://127.0.0.1:8765"`）で起動できるようにする。
  - `scripts/run_ci_local.ps1` を作成し、`.venv\Scripts\ruff.exe` / `.venv\Scripts\python.exe` を実行するようにする。

### 4.7 【推奨】既存コードの軽微な不整合・環境依存メモ（引き継ぎ時の注意点）
解析時に確認された既知の小さな実装メモ（移植・リファクタリング時に合わせて整理推奨）:
1. `src/features/feature_matrix_100.py:17`: `import soundfile as sf` がモジュールトップレベルで無条件インポートされている一方、`pyproject.toml` では `[project.optional-dependencies] audio` 側に定義されている。新規仮想環境構築時は必ず `pip install -e ".[audio,dev]"` でインストールすること。
2. `src/features/feature_matrix_100.py:1252`: `invoke_feature("G10")` が `safety_guard.emergency_panic(reason="feature_explorer")` を呼んでいるが、`DisklavierSafetyGuard.emergency_panic(self)` (`disklavier_safety_guard.py:1102`) には `reason` 引数が無い。
3. `src/ui/index.html` / `remote.html`: `font-family` に `-apple-system, BlinkMacSystemFont, "Hiragino Sans"` が先頭記載されている（Windows用に `"Yu Gothic UI"`, `"Meiryo"`, `"Noto Sans JP"` 等をフォールバックに含めると視認性が向上）。
4. `tests/test_studio_overhaul.py:168, 241`: `engine.workspace.state_path = ...` を設定しているが、`song_workspace.py` が実際に使用している定数は `WORKSPACE_SNAPSHOT_PATH` である点に留意。

---

## 5. 既存のテストスイート・仮想環境テストの実行方法と検証項目

本リポジトリはクラウドCI（`.github/workflows/`）を禁止し、**ローカル完結検証**を原則としています。

### 5.1 仮想環境セットアップ & テスト実行コマンド

#### macOS / Git Bash 環境（現行）
```bash
# 仮想環境作成・依存インストール (初回)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[audio,dev]"

# ローカルCI一括実行 (ruff lint + tests/ + validate_system_integrity.py)
./scripts/run_ci_local.sh
```

#### Windows 11 PowerShell 環境（移行先推奨手順）
```powershell
# 1. 仮想環境作成・依存インストール (Python 3.11+ 推奨)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[audio,dev]"
# ※ RTX 3070 CUDA 版 PyTorch を使う場合は別途 CUDA 対応 wheel を導入:
# pip install torch --index-url https://download.pytorch.org/whl/cu124

# 2. 全検証スイート手動実行 (run_ci_local.sh と同等)
$env:PYTHONPATH = "."
.\.venv\Scripts\ruff.exe check src tests validate_system_integrity.py
.\.venv\Scripts\pytest.exe tests/
.\.venv\Scripts\python.exe validate_system_integrity.py
```

#### 個別スイート実行コマンド（Windows / Mac 共通ロジック）
```bash
# Macの例 (.venv\Scripts\python.exe に読み替えて実行)
PYTHONPATH=. .venv/bin/python validate_system_integrity.py
PYTHONPATH=. .venv/bin/python tests/test_phase1_security.py
PYTHONPATH=. .venv/bin/python tests/test_scheduler_jitter.py
PYTHONPATH=. .venv/bin/python tests/test_safety_guard.py
PYTHONPATH=. .venv/bin/python tests/test_autonomous_virtuoso.py
PYTHONPATH=. .venv/bin/python tests/test_studio_overhaul.py
```

### 5.2 各テストスイートの検証項目一覧

| テストファイル / スクリプト | 検証対象モジュール・機能 | 主なアサーション・合格基準 |
|---|---|---|
| **`validate_system_integrity.py`**<br>(全7統合スイート) | `feature_matrix_100`, `imslp_library`, `human_reference_teacher`, `ai_classical_composer`, `continuous_virtuoso`, `obsidian_brain`, `mission_control_server`, `index.html` | 1. 100機能カタログの登録整合性 (`total_count == 100`)<br>2. IMSLP 11曲以上 (Tchaikovsky/Rachmaninoff/Liszt/Chopin/Bach含む) の Deep DNA 抽出<br>3. Meta-AI Director 詩的曲名/欧文タイトル/Op番号/5部楽式生成 & 6軸レーダー `[0..100]`<br>4. 4手立体・88鍵カスケード・オクターブトレモロの物理安全検証 (`max_simul <= 16`, `max_held <= 20`, `vel 20..105` / halo `1`, `dur >= 0.035s`)<br>5. 3モード双方向パッセージ対話 (`contrapuntal_reply`, `liszt_rachmaninoff_virtuoso`, `evolve_symphonic_masterwork`) の安全性<br>6. エンジン連携・第1〜3幕実行・Living Score差分(`mutated_count > 0`, `mutation_reason_ja` 付与)・Obsidian知識エクスプローラ<br>7. FastAPI必須ルート存在確認・UI要素ID存在・禁止ジャゴン (`BANNED_UI_JARGON`) ゼロ |
| **`tests/test_safety_guard.py`** | `DisklavierSafetyGuard`, `DISKLAVIER_MODEL_PROFILES` | - 安全定数の固定検証 (`21..108`, `12/16`, `20`, `28`, `0.055`, `0.045`, `28..105`, `8.0s`, `240.0s`)<br>- 100回ランダムファズによる `sanitize_velocity` (`[28, 105]` & `vel=1` halo)・`sanitize_release_velocity` (レガート `15..40` / スタッカート `65..90`)・`CC#64`許可/`CC#66,67`ブロック確認<br>- VDL Note-Off 持続錨定 (`vel=35`, `dur=0.50s` で `off_time - send_time >= 0.60s`)<br>- 25音同時バーストでのマイクロスタッガー (`>=13`)・ボイススティール (`active_notes <= 20`)<br>- `emergency_panic()` による全鍵・ペダル解放と3機種プロファイル切替 |
| **`tests/test_scheduler_jitter.py`** | `DisklavierSafetyGuard` (`threading.Condition` スケジューラ) | - 実機なし環境で 30音を `42 ms` 間隔でスケジュール<br>- 全30音送信完了、`scheduler_jitter["mean_ms"] < 2.5 ms`、`p95_ms < 5.0 ms`（※Windows移植時に `timeBeginPeriod(1)` の動作確認指標として最重要） |
| **`tests/test_autonomous_virtuoso.py`** | `DisklavierPhysicsSimulator`, `VirtualDisklavierTwin`, `SeedMemoryPool`, `ContinuousVirtuosoEngine`, `ObsidianMusicalBrain` | - Ghost Note (`vel=8`) の空振り検知 vs `vel=1` halo許可<br>- VDL曲線 (`pp > 100ms`, `ff < 40ms`) と 45ms ペダル慣性<br>- 非ガード25/30音バーストでのサグ (`>=17`) & クラッシュ (`>=30`) 検出、および `SafetyGuard` 経由でのゼロクラッシュ防御<br>- `SeedMemoryPool` 10モチーフ以上読込、リングバッファ先読み (`>= 2.0s`)、生モチーフ吸収、人間指示書書換→12世代高速進化で `best_score_ever >= 88.0`、Obsidian Vault/HoF MIDI生成検証 |
| **`tests/test_studio_overhaul.py`** | `.agent/skills/`, `LocalPreflightChecker`, `HumanReferenceTeacher`, `MultiSongWorkspaceManager`, `feature_matrix_100`, ASGI FastAPI | - `.github/workflows` 非存在と Agent Skills 存在<br>- 不正イベントの `<100 ms` 監査・自動修復<br>- 創発的着想合計100%・機械的MIDI人間化・Vivace規則 (`>= 35ms`, `rel 65..90`)<br>- タブ作成/切替/閉鎖/復元・Undo/Redo・スクラブ・A/B・30秒救出・各種リセット<br>- MusicXML 3.1 / WAV (RIFFヘッダ) / SMF MIDI / QR SVG 生成、UI禁止用語チェック、README Mermaid (`>=4`図) 検証<br>- ASGI直接呼出による主要 `/api/*` エンドポイント 200応答検証 |
| **`tests/test_phase1_security.py`** | `mission_control_server.py` 認証・入力境界・パス防衛 | - トークン無し / 誤トークンの `POST /api/*` が `401` で拒否され、正規 `X-Studio-Token` が `200` になること<br>- `/remote/info` がループバック時はトークンを含み、非ループバック未認証時はトークンを隠蔽すること<br>- WebSocket `/ws` がトークン無で `1008` クローズ、正当 `?token=` で `accept` すること<br>- `../../etc/passwd` 等のパストラバーサルが `400/404` で遮断されること<br>- `>512` イベント / `>16` 和音 / 範囲外Note・Velocity・負時間・長時間への Pydantic `422` バリデーション |

> [!IMPORTANT]
> **実機接続時の手動確認手順**: 自動テスト（`run_ci_local.sh`）では物理ソレノイドを連続駆動しません。Windows機へ Yamaha Disklavier と UR22mkII を接続した後の初回音出し・VDLキャリブレーション・緊急停止確認は、必ず [`docs/HARDWARE_TEST_PROCEDURE.md`](docs/HARDWARE_TEST_PROCEDURE.md) に従ってピアノの前で手動実施してください。
