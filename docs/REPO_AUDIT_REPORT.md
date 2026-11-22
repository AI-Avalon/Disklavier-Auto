# Disklavier-Auto 最新リポジトリ完全監査レポート (`docs/REPO_AUDIT_REPORT.md`)

監査対象: `AI-Avalon/Disklavier-Auto` (`main` / `48a3297`)  
対象環境: Windows 11 Pro 64bit / Core i7-12700F (12コア20スレッド) / 64GB RAM / NVIDIA GeForce RTX 3070 (VRAM 8GB) / Python 3.12 (`PyTorch 2.6.0+cu124`, `symusic 0.6.0`)

---

## 0. 直近 GitHub Actions 実行状況の調査結果

`https://api.github.com/repos/AI-Avalon/Disklavier-Auto/actions/runs?per_page=5` による確認結果:
- Run `44981194970` (`48a3297`, `Deploy Disklavier-Auto Interactive Demo to GitHub Pages`): `completed / success`
- Run `44876556860` (`9f26608`): `completed / failure` (`src/stage/__init__.py` による `symusic` 遅延インポート未対応・軽量CI依存欠如により失敗していた履歴あり)
- Run `44868225738` (`9f26608`): `completed / failure` (同上)

最新 `48a3297` では `.github/workflows/deploy-pages.yml` および `requirements-ci.txt` により CI が緑に復帰しているが、本改修で追加する新規作曲エンジン・UI規約・モバイル検証スクリプトを Ubuntu CI (CPU専用環境) でも確実に無欠陥・緑維持できるよう継続監査対象とした。

---

## 1. 曲が単調・不協和音・断片的になっている根本原因

1. **1〜2小節の固定度数配列・テンプレート依存 (`src/stage/notagen_live_engine.py`, `src/stage/corpus_phrase_model.py`)**:
   - `LivePolyphonyHarmonyGuard.realize_and_guard_bar()` (`src/stage/notagen_live_engine.py:284`) および `NotaGenLiveEngine._fallback_abc_bar_lines()` (`同:775`) が `pats = [0, 3, 2, 5, 4, 2]` のような短い1小節単位パターンに依存しており、クラシック音楽の基本単位である **4〜8小節の楽節構造（前楽節 Antecedent：問いかけ・半終止 `V`/`V7sus4` → 後楽節 Consequent：動機発展・単一頂点・完全終止 `V7→I/i`）** の大局計画がライブ生成エンジン側に欠如している。
   - `CorpusPhraseModelManager.predict_blueprint()` (`src/stage/corpus_phrase_model.py:525-549`) もチェックポイント未ロード時や小節連結時に8音単位の局所骨格しか出力せず、小節間の声部連結（`Voice-Leading Bridge`）や跳躍後の順次回復が明示的楽節計画と直結していない。
2. **作曲専用OSSモデルの実体未統合 (`src/stage/notagen_live_engine.py`)**:
   - `NotaGenLiveEngine` 内に `_notagen_model` フィールド (`notagen_live_engine.py:504`) が用意されているものの、`_run_stream_session()` (`800-825`) は `ollama_abc_stream` と `transformer_abc_fallback` のみを実行し、**実際の `NotaGen-Small` (FP16/INT8 量子化モデル)** のロード・推論パスが未実装。
   - リアルタイム対話用の **`Anticipatory Music Transformer` (`jthickstun/anticipation`, `music-small-800k` 128M, FP16 約0.31GB)** による継続生成・指定小節インフィリングが未導入。
   - Mode B（チェロ×ピアノ）向けの **`MIDI-GPT` (`Metacreation-Lab/MIDI-GPT`, PyTorch CPU 推論 / VRAM 0GB)** によるチェロ旋律条件付き伴奏トラック生成・小節再生成エンジンが未実装。
   - 結果として `src/stage/notagen_engine.py` が存在せず、`notagen_live_engine.py` 単体にフォールバック実装が留まっている。
3. **小節境界での和声接続・ペダル先行制御の断片化**:
   - `LivePolyphonyHarmonyGuard` (`notagen_live_engine.py:404-417`) が各小節を独立処理するため、先行ハーフペダル（`-30ms..-50ms` 前倒し踏み込み）や、楽節全体のダイナミクス・ペダルアークが小節ごとにリセットされやすい。

---

## 2. リアルタイム処理のボトルネック（並列・VRAM・DOM/Canvas）

1. **RTX 3070 (8GB VRAM) デュアルプロファイル管理の欠如 (`src/stage/vram_manager.py`)**:
   - 現状の `RTX3070VRAMManager` (`vram_manager.py:94-241`) には `owner` 排他切替はあるが、**リアルタイム対話プロファイル**（Ollama `qwen3.5:4b` Q4_K_M + FlashAttention + `q8_0` KVキャッシュ + `Anticipatory 128M` 約0.31GB + CUDAテンソルを `< 6.5 GB` で常駐させる構成）と、**NotaGen一から作曲プロファイル**（Ollama を `keep_alive=0` で即時退避＋`empty_cache()` し 8GB 全域を NotaGen FP16/INT8 + SDPA FlashAttention に割当）の明示的プロファイル制御がない。
2. **Worker 6 コーパス検索の直列読込・件数制限・毎クエリVRAM転送 (`src/stage/layer3_candidate_composer.py`)**:
   - `ZeroConfigCorpusLoader` (`layer3_candidate_composer.py:170-345`) は `max_files=36` の少数をシングルスレッドで順次パースし、さらに `retrieve_voicing_references()` (`333`) で毎回 `torch.tensor(self.feature_matrix, device="cuda")` を CPU→GPU へ再転送している。64GB RAM を活かしたマルチスレッド `symusic` 並列インデックス化（256+ 曲の常駐）と事前GPU常駐テンソル化により `< 2ms` 検索へ短縮できる。
3. **60fps WebSocket 過剰シリアライズ (`src/stage/stage_server.py`)**:
   - `StageWSBroadcaster._broadcast_loop()` (`stage_server.py:1554-1570`) は状態変化がない無音待機中も 1/60 秒ごとに `orch.get_hud_state()` を呼び出して全 JSON を再構築・送信している。状態ハッシュ／リビジョン比較による差分・間引き最適化の余地がある。
4. **フロントエンド DOM 再構築と五線譜毎回全描画 (`web-demo/app.js`, `src/ui/stage_hud.html`)**:
   - `renderCandidateCards()` (`web-demo/app.js:833`) は更新のたびに `container.innerHTML = ""` で 4 枚の候補 DOM を毎回破棄・再生成している。
   - `renderGrandStaff()` (`web-demo/app.js:508-563`) および `drawHeroStaff()` (`src/ui/stage_hud.html:568-620`) はフレームごとに五線・小節線・ト音/ヘ音記号を一から描き直しており、オフスクリーン Canvas キャッシュがない。

---

## 3. Web ページが見づらい原因 ＆ 5大デザイン規約違反 ＆ スマホ非対応

1. **規約1違反（カードの四方枠線と影の残存）**:
   - `web-demo/style.css` の `.card` (`327`), `.metric-tile` (`460`), `.pedal-box` (`506`), `.cand-pick-card` (`584`), `.studio-body` (`729`), `.piece-item` (`787`), `.vu-box` (`916`), `.mixer-row` (`986`), `.chord-chip` (`1065`), `.cand-card` (`1177`), `.help-box` (`1276`) に四方 `border: 1px solid ...` が残存。
   - `src/ui/stage_hud.html` の `.control-bar`, `.zone-a`, `.panel`, `.tile`, `.pedal-card`, `.cand-card`, `.demo-deck`, `.meta-arena` に四方枠線が残存。
   - `src/ui/remote.html:64,157,165` に `box-shadow` が残存。
   - `src/ui/index.html:389` に `box-shadow`、全体に四方 `border` と `border-radius` カード囲みが残存。
2. **規約2違反（背景グレー1色統一違反・グラデーション残存）**:
   - `web-demo/style.css` と `src/ui/stage_hud.html` は `#060608`, `#0B0B0F`, `#121217`, `#08080C`, `#0F0F14`, `#161513`, `#141310`, `#1D100E` 等、多数の異なる背景色・パネル色が混在しており、フラットグレー1色統一になっていない。
   - `src/ui/index.html:35,106-118,330,358-362,387,417` および `src/ui/remote.html:28,59,123-126,144` に `linear-gradient` / `radial-gradient` が残存。
3. **規約3違反（DOM 並び順で固定された極薄背景幾何学図形の欠如）**:
   - 各カード・セクションの背面に、内容や状態ではなく `:nth-child` 等の DOM 並び順だけで種類が固定された極薄幾何学図形（円・正三角形・正方形・半円・対角線）が実装されていない。
4. **規約4違反（DOM 再生成・自動切替の残存）**:
   - `web-demo/app.js:833` の `container.innerHTML = ""` による候補カード全破棄・再生成、およびターン更新時の選択状態強制リセット (`930-937`) が静かな固定表示を妨げている。
5. **規約5違反（12px〜12.5px など 13px 未満の文字の残存）**:
   - `web-demo/style.css`: `.brand-sub` (`100`: `12.5px`), `.mp3-tag` (`826`: `12.5px`), `.mixer-desc` (`1006`: `12.5px`), `.chord-chip` (`1063`: `12.5px`) が 13px 未満。
   - `web-demo/app.js`: Canvas 描画内の `12px` フォント指定（`484`, `664`, `754`）。
   - `src/ui/index.html` / `src/ui/remote.html`: `0.72rem` (~10-11.5px), `0.75rem`, `0.78rem`, `0.82rem` 等の極小フォントが多数存在。
6. **スマートフォン専用 UI (`@media (max-width: 768px)`) の完全欠如**:
   - `web-demo/style.css` に `768px` 以下のメディアクエリがなく、スマホ幅（`390×844`）で開くと `.studio-body` (`grid-template-columns: 300px minmax(0, 1fr)`) や `.xray-metrics` / `.chord-strip` / `.audit-bars` が横方向にはみ出す・押し潰れる。
   - 下部固定ボトムナビゲーション（高さ 56px・タップ領域 `>= 48×48px`）、コンパクト常時ヘッダー、アコーディオン収納による情報過多回避が未実装。

---

## 4. GitHub Actions / GitHub Pages・ファイル構成の課題

1. **デッドコード・重複とモジュール分離**:
   - 作曲エンジンの中核を `src/stage/notagen_engine.py` として正式実装し、`src/stage/notagen_live_engine.py` は後方互換ラッパー／エイリアスとして統合整理する必要がある。
   - `docs/demos/` および `web-demo/audio/` のデモ音源に、新エンジン（`NotaGen` / `Anticipatory` / `MIDI-GPT`）による最新生成作品を組み込み、`verify_catalog_and_demo.py` で継続検証する必要がある。
2. **Playwright 監査のモバイル＆5大規約カバレッジ不足 (`scripts/playwright_audit.py`)**:
   - 現行 `playwright_audit.py` は `1920×1080` と `1366×768` のみ検証し、フォント最小値も `12.0px` チェックに留まっている。
   - 本改修にて **スマホ実画面（`390×844`）の自動検証**、**13px 未満ゼロ検証**、**カード枠線/影ゼロ検証**、**グラデーションゼロ検証**、**幾何学図形並び順検証**、**5秒静置カード入れ替えゼロ検証** を強制実装する。
