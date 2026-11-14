# 🎹 Disklavier-Auto 全100実装機能マトリクス (`FEATURES_100.md`)

- **総実装機能数**: **100 / 100 機能**（全10カテゴリ・各10機能）＋ **IMSLP深層DNA・Meta-AI Director・Living Score・イン・アプリ知識グラフ統合**
- **アーキテクチャ方針**: IMSLP 11大名曲（チャイコフスキー／ラフマニノフ／リスト／ショパン／ドビュッシー／バッハ／サン＝サーンス／フォーレ／ブラームス／メンデルスゾーン）の深層音楽DNA解析に基づく1からの本格作曲・Meta-AI Director（詩的日本語＆欧文タイトル＋5楽式構想）・6軸100点満点レーダー自己進化・Disklavier超人間的ヴィルトゥオーゾ表現4技・双方向パッセージ対話3モード・`sakurai-virtuoso-direction` 演出統合・イン・アプリ知識グラフ＆進化ノート・`validate_system_integrity.py` ローカル全数安全監査

> [!NOTE]
> 上位スタジオ統合機能（IMSLP Deep DNA `/api/imslp/dna`、双方向パッセージ返奏 `/api/dialogue/passage_reply`、4手立体交差・88鍵カスケード・Vel 1倍音共鳴・オクターブ連打 `/api/studio/virtuoso_expression`、3幕舞台進行 `/api/studio/stage_act`、イン・アプリ知識グラフ `/api/obsidian/explorer`）は、本100機能カタログの各カテゴリと密結合して動作します。

| カテゴリ | 内容 | 機能番号 |
|---|---|---|
| **A. 人間のお手本＆作曲頭脳** | 巨匠の演奏データからレガートや活発さを学び、ゼロからオリジナル曲を紡ぎ出す機能群 | #1 〜 #10 |
| **B. AI自己進化エンジン＆高速並列探索** | Apple Siliconマルチコアで毎世代複数の音楽候補を並列生成し、人間基準で進化させる機能群 | #11 〜 #20 |
| **C. 演奏表現コントロール＆ライブ演出** | 演奏中リアルタイムに歌い方・激しさ・キャラクター・展開を操る表現機能群 | #21 〜 #30 |
| **D. 複数曲タブ・タイムマシン＆各種リセット** | ブラウザ風の複数曲タブ、世代スクラブ、A/B聴き比べ、30秒救出や多様なリセット機能群 | #31 〜 #40 |
| **E. 五線譜表示＆MIDI / WAV / MusicXML書き出し** | リアルタイム楽譜描画と各種フォーマットでの即座の保存・共有機能群 | #41 〜 #50 |
| **F. チェロ共演＆リアルタイム呼吸合わせ** | チェロの音色・テンポ・調性に寄り添い、美しい掛け合いと開放弦ウォームアップを行う機能群 | #51 〜 #60 |
| **G. 実機Disklavier安全保護（ハードウェア防衛）** | 30音同時押しクラッシュ防止、VDL逆算補正、右ペダル専用、熱保護を守る安全装置群 | #61 〜 #70 |
| **H. バーチャルピアノ物理シミュレーション＆可視化** | 実機なしでもハンマー・ダンパー・弦共鳴・88鍵盤アニメーションを体感できる音響＆視覚機能群 | #71 〜 #80 |
| **I. 記憶ノート（Obsidian）＆ローカル自動チェック** | 成長日記・思考マップの自動記録と0.1秒以内のローカル安全事前監査パイプライン機能群 | #81 〜 #90 |
| **J. スマホリモコン・システム診断＆アクセシビリティ** | QRコードで使えるスマホ/iPadリモコン、日本語ヘルスチェック、緊急停止などの運用支援機能群 | #91 〜 #100 |

## カテゴリ A: 人間のお手本＆作曲頭脳 (10機能)

> 巨匠の演奏データからレガートや活発さを学び、ゼロからオリジナル曲を紡ぎ出す機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 1 | `A1` | **人間のお手本（Teacher MIDI）統計読み込み** | 巨匠ピアノ演奏プロファイルからレガート・強弱・ルバート法則を読み込みます。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 2 | `A2` | **真のレガート（音重ね＋低速離鍵）制御** | メロディ音を+5〜+15ms重ね、Note-Off速度を15〜30に落としてダンパー雑音を消します。 | `human_reference_teacher.py` | `/api/virtuoso/expression_pad` |
| 3 | `A3` | **歌うメロディの先行発音（Melody Lead）** | 主旋律を内声より+15〜+25ms早く、+12〜+20強く弾いて立体的に歌わせます。 | `human_reference_teacher.py` | `/api/virtuoso/expression_pad` |
| 4 | `A4` | **活発なVivace/Scherzando歯切れ表現** | 35〜50msの安全スタッカートと速い離鍵(65〜90)で生命力ある跳躍を生みます。 | `human_reference_teacher.py` | `/api/virtuoso/expression_pad` |
| 5 | `A5` | **12〜15Hz同音連打ガード＆オクターブ分散** | 物理限界を超える連打を自動的にオクターブや分散和音へ華麗に逃がします。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 6 | `A6` | **チャイコフスキー＆ラフマニノフ深層DNAベクトル融合** | 情熱的な6度・8度跳躍、反行バス、重厚な鐘の響きをIMSLP深層DNA解析から再現します。 | `imslp_library.py` / `human_reference_teacher.py` | `/api/imslp/dna` |
| 7 | `A7` | **リスト超絶技巧＆Meta-AI Director 1曲丸ごと構想** | 詩的日本語＆欧文タイトル・調性・Op番号（例：*変ロ短調 Op.14*）と5段楽式を一から生成します。 | `human_reference_teacher.py` | `/api/state` |
| 8 | `A8` | **ドビュッシー／ショパン／バッハ等 11名曲語法ブレンド** | 11作品の旋律輪郭・和声・対位法・アルペジオ法則を確率重みで調和させます。 | `imslp_library.py` | `/api/imslp/dna` |
| 9 | `A9` | **4手立体交差・88鍵カスケード・オクターブ連打表現** | 人間の10度限界を超える4声独立アーティキュレーションと1〜2ms分散88鍵カスケードを生成します。 | `human_reference_teacher.py` / `continuous_virtuoso.py` | `/api/studio/virtuoso_expression` |
| 10 | `A10` | **機械的ベタ打ちMIDIの人間化リペア機能** | 外部の硬いMIDIデータを自動検出し、ルバート・ペダル・レガートを付与します。 | `human_reference_teacher.py` | `/api/features/invoke` |

## カテゴリ B: AI自己進化エンジン＆高速並列探索 (10機能)

> Apple Siliconマルチコアで毎世代複数の音楽候補を並列生成し、人間基準で進化させる機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 11 | `B1` | **ゼロからオリジナルフレーズ生成ゲノム** | 固定モチーフに頼らず音程輪郭とリズム骨格から毎回オリジナル物語を作曲します。 | `actor_critic.py` | `/api/virtuoso/full_reset` |
| 12 | `B2` | **8〜16候補の並列世代探索エンジン** | 1回の作曲サイクルで複数候補を同時評価し最良の音楽だけを採用します。 | `actor_critic.py` | `/api/virtuoso/status` |
| 13 | `B3` | **Apple Silicon マルチコアProcessPoolExecutor** | Macの複数CPUコアを使って候補評価を高速並列化します。 | `actor_critic.py` | `/api/virtuoso/hyper_evolve` |
| 14 | `B4` | **Apple Accelerate SIMD / MPS 数値計算** | NumPyベクトル演算（および利用可能時MPS）で評価スコアを高速算出します。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 15 | `B5` | **6軸100点満点レーダー＆4指標人間採点 + 日本語推敲ログ** | 旋律・和声・対位法・自然さ・超絶技巧・実機優しさの6軸レーダーと理由解説を表示します。 | `human_reference_teacher.py` / `actor_critic.py` | `/api/state` |
| 16 | `B6` | **裏での超高速まとめて進化（50世代ブースター）** | 数秒の裏計算で50世代分の進化を一気に進めて頭脳を鍛え上げます。 | `continuous_virtuoso.py` | `/api/virtuoso/hyper_evolve` |
| 17 | `B7` | **途切れない先読みノンストップストリーミング** | 次の4〜8小節をバックグラウンドで先読み生成し無音空白をゼロにします。 | `continuous_virtuoso.py` | `/api/virtuoso/toggle_stream` |
| 18 | `B8` | **「👍 今のフレーズいいね！」即時報酬フィードバック** | 人間の好感を即座にAIの嗜好ベクトルと進化スコアへ反映します。 | `continuous_virtuoso.py` | `/api/virtuoso/like` |
| 19 | `B9` | **対話型モチーフ傾聴＆自然消化互換API** | 生演奏から聴き取った音高・リズムを既存語法へ自然に溶け込ませます。 | `continuous_virtuoso.py` | `/api/virtuoso/absorb_motif` |
| 20 | `B10` | **世代系統樹リアルタイム成長記録** | 第1世代から現在までどの枝が勝者として残ったかを可視化します。 | `actor_critic.py` | `/api/virtuoso/status` |

## カテゴリ C: 演奏表現コントロール＆ライブ演出 (10機能)

> 演奏中リアルタイムに歌い方・激しさ・キャラクター・展開を操る表現機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 21 | `C1` | **リアルタイム2D表情パッド（表現×エネルギー）** | 「しっとり歌う⇄活発に跳ねる」「穏やか⇄情熱的」をドラッグ操作で自在に操ります。 | `continuous_virtuoso.py` | `/api/virtuoso/expression_pad` |
| 22 | `C2` | **キャラクターワンタッチ変身（5つの物語テーマ）** | 深夜のノクターン・情熱ロマン・活発スケルツォ・印象派・対話フーガへ即時変身。 | `continuous_virtuoso.py` | `/api/virtuoso/character_preset` |
| 23 | `C3` | **自然日本語「今の音楽の実況字幕」** | 今AIがどんな情景を思い描いて弾いているかを一言の日本語で表示します。 | `continuous_virtuoso.py` | `/api/virtuoso/status` |
| 24 | `C4` | **「💬 AIピアニストの今の狙い」吹き出し解説** | 次の展開や転調の意図を分かりやすい日本語コメントで伝えます。 | `continuous_virtuoso.py` | `/api/virtuoso/status` |
| 25 | `C5` | **動的ルバート＆フレーズ頂点呼吸カーブ** | 4〜8小節単位で自然にテンポが揺らぎフレーズ末尾で息継ぎします。 | `human_reference_teacher.py` | `/api/virtuoso/expression_pad` |
| 26 | `C6` | **ソフトペダル感とハーフペダル(35〜80)制御** | CC#64の踏み加減を繊細に変え、濁りのない透明な共鳴を作ります。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 27 | `C7` | **和音打弦-30〜-50ms前ペダル先踏み（レガートペダル）** | 和音の直前にダンパーを浮かせて響きの連結を極限まで滑らかにします。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 28 | `C8` | **ダイナミック・コントラスト（強弱レンジ拡大）** | ささやくVelocity 30から劇的な99まで立体的な抑揚を自動で描きます。 | `human_reference_teacher.py` | `/api/virtuoso/expression_pad` |
| 29 | `C9` | **バロック・ロマン派アルペジオ／鐘のロール奏法** | 和音を12〜24msの優雅な分散打鍵にほぐし立体感を出します。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 30 | `C10` | **ジャゴンゼロ・直感的日本語UIラベルシステム** | 専門用語・機械的翻訳語を徹底排除したライブ向けユーザー体験を提供します。 | `ui/index.html` | `/` |

## カテゴリ D: 複数曲タブ・タイムマシン＆各種リセット (10機能)

> ブラウザ風の複数曲タブ、世代スクラブ、A/B聴き比べ、30秒救出や多様なリセット機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 31 | `D1` | **ブラウザ風マルチソングタブバー** | 曲ごとに独立した進化状態と記憶を持つ複数タブを行き来できます。 | `song_workspace.py` | `/api/tabs` |
| 32 | `D2` | **新規曲タブ作成（+ 新しい曲を作る）** | 今の曲を消さずに新しい空きタブを追加して別コンセプトの曲を育てられます。 | `song_workspace.py` | `/api/tabs/new` |
| 33 | `D3` | **閉じたタブの復元（間違えて閉じた曲を戻す）** | 誤って閉じたタブの履歴スタックからワンクリックで復元できます。 | `song_workspace.py` | `/api/tabs/restore` |
| 34 | `D4` | **進化タイムマシン Undo / Redo（戻す・進む）** | 「さっきの展開の方が良かった」ときに1手単位で世代を戻せます。 | `song_workspace.py` | `/api/timemachine/undo` |
| 35 | `D5` | **世代タイムライン・スライダー（第1世代⇄現在）** | スライダーを動かすだけで過去の任意の世代の状態へ一瞬でワープ試聴できます。 | `song_workspace.py` | `/api/timemachine/scrub` |
| 36 | `D6` | **進化前(A) vs 進化後(B) ワンボタン聴き比べ** | 第1世代の原石と最新世代の名演を連続再生して成長を耳で確かめられます。 | `continuous_virtuoso.py` | `/api/timemachine/ab_compare` |
| 37 | `D7` | **⏪ 今の30秒前を救出！（タイムカプセル・ピン留め）** | 直近35秒の名演奏を専用ピン留めタブとして永久保存し再生できます。 | `continuous_virtuoso.py` | `/api/timemachine/rescue_30s` |
| 38 | `D8` | **🔄 完全リセット（記憶を白紙にして一から新曲を作る）** | ゲノムと記憶を初期化し、まったく新しい調・物語の新曲ゼロスタートを切ります。 | `continuous_virtuoso.py` | `/api/virtuoso/full_reset` |
| 39 | `D9` | **✨ 曲の雰囲気だけ変える（頭脳を維持して転調）** | 進化で得た演奏スキルはそのままに、別の調性とムードへ即座に衣装替えします。 | `continuous_virtuoso.py` | `/api/virtuoso/mood_change` |
| 40 | `D10` | **⚡ ひらめきスパーク＆JSON永続自動保存** | 劇的な転調スパークを注入しつつ、タブと履歴をローカルJSONに自動保存します。 | `song_workspace.py` | `/api/virtuoso/spark` |

## カテゴリ E: 五線譜表示＆MIDI / WAV / MusicXML書き出し (10機能)

> リアルタイム楽譜描画と各種フォーマットでの即座の保存・共有機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 41 | `E1` | **Living Score 五線譜（変更音符ハイライト＆強弱記号）** | ト音＋ヘ音大譜表で世代間の書き換わり音符をハイライトし推敲理由と強弱(pp〜ff)を表示します。 | `feature_matrix_100.py` | `/api/score/current` & `/api/features/sheet_music` |
| 42 | `E2` | **チェロ声部パート譜同時マッピング** | チェロ音域の歌うラインや伴奏対象を低音部譜表に対応付けます。 | `feature_matrix_100.py` | `/api/score/current` |
| 43 | `E3` | **スラー（レガート）＆スタッカート奏法記号表示** | 楽譜上にレガート弧線とスタッカート記号を自動計算して表示します。 | `feature_matrix_100.py` | `/api/score/current` |
| 44 | `E4` | **拍子・小節線・臨時記号（♯/♭）インテリジェント表示** | MIDIノートを調性コンテキストに合わせて見やすい譜面座標へ変換します。 | `feature_matrix_100.py` | `/api/score/current` |
| 45 | `E5` | **標準MIDIファイル (.mid) ワンクリック保存** | 今のオリジナル演奏をDAWや実機で使えるSMF Type-1 MIDIでダウンロードできます。 | `feature_matrix_100.py` | `/api/export/midi` |
| 46 | `E6` | **MusicXML 3.1 (.musicxml) 楽譜ソフト書き出し** | MuseScoreやFinale等でそのまま開ける五線譜ファイルを生成します。 | `feature_matrix_100.py` | `/api/export/musicxml` |
| 47 | `E7` | **高音質WAV (.wav) 44.1kHz オフライン音響書き出し** | 倍音合成＋ペダル共鳴付きの高品位オーディオファイルとして保存できます。 | `feature_matrix_100.py` | `/api/export/wav` |
| 48 | `E8` | **救出タブからのフレーズ即座書き出し対応** | 「30秒前を救出」した演奏もそのままMIDI/MusicXML/WAV化できます。 | `feature_matrix_100.py` | `/api/export/midi` |
| 49 | `E9` | **自動調号・テンポメタ情報埋め込み** | 現在のBPMと調性をMIDIおよびMusicXMLヘッダへ正確に書き込みます。 | `feature_matrix_100.py` | `/api/export/musicxml` |
| 50 | `E10` | **ライブ演奏中の楽譜自動追従スクロール互換** | 世代更新に合わせて最新の小節フレーズへ自動で譜面が切り替わります。 | `feature_matrix_100.py` | `/api/score/current` |

## カテゴリ F: チェロ共演＆リアルタイム呼吸合わせ (10機能)

> チェロの音色・テンポ・調性に寄り添い、美しい掛け合いと開放弦ウォームアップを行う機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 51 | `F1` | **🎻 チェロ開放弦(C-G-D-A)共鳴＆88鍵ウェーブ起動儀式** | チェロのC2/G2/D3/A3共鳴和音と全88鍵盤をなぞる美しい起動テスト演奏を行います。 | `feature_matrix_100.py` | `/api/cello/warmup_wave` |
| 52 | `F2` | **リアルタイム・マイク音声ピッチ検出 (YIN)** | 生のチェロ演奏音をマイクで聴き取り瞬時に音程を推定します。 | `audio_monitor.py` | `/api/audio/toggle` |
| 53 | `F3` | **チェロ音域対応 動的伴奏ジェネレーター** | チェロの現在音高に合わせて衝突しないピアノボイシングを生成します。 | `accompaniment_engine.py` | `/api/session/proxy_cello` |
| 54 | `F4` | **双方向ヴィルトゥオーゾ・パッセージ対話（3モード返奏）** | ピアノ/チェロ演奏に対し「対位法応答 / リスト変奏 / 第1主題から大曲進化」で返奏します。 | `ai_classical_composer.py` / `continuous_virtuoso.py` | `/api/dialogue/passage_reply` |
| 55 | `F5` | **代理チェロテスト演奏（マイク不要リハ）** | 実チェロが手元になくても仮想チェロ音源で共演テストができます。 | `accompaniment_engine.py` | `/api/session/proxy_cello` |
| 56 | `F6` | **IMSLP巨匠レパートリー5段階最適化プレイリスト** | バッハ・ショパン・ドビュッシーの名曲分析データを実機安全版で試聴できます。 | `imslp_library.py` | `/api/imslp/list` |
| 57 | `F7` | **チェロ音域との声部干渉回避フィルター** | チェロのメロディと同じ音域でピアノ内声が濁らないよう自動調整します。 | `accompaniment_engine.py` | `/api/session/toggle` |
| 58 | `F8` | **A=442Hz / 440Hz オーケストラ調律チューニング情報** | チェロとの共演を想定した基準ピッチ表示と共鳴設計を提供します。 | `audio_monitor.py` | `/api/status` |
| 59 | `F9` | **倍音共鳴サイレント打鍵（Silent Harmonic Halo）** | 発音しない極弱ベロシティ1打鍵でダンパーを浮かせチェロの倍音を響かせます。 | `disklavier_safety_guard.py` | `/api/safety/harmonic_halo` |
| 60 | `F10` | **対話セクションMIDI直接保存機能** | チェロとの掛け合いで生まれたフレーズを個別のMIDIファイルとして保存します。 | `ai_classical_composer.py` | `/api/dialogue/export` |

## カテゴリ G: 実機Disklavier安全保護（ハードウェア防衛） (10機能)

> 30音同時押しクラッシュ防止、VDL逆算補正、右ペダル専用、熱保護を守る安全装置群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 61 | `G1` | **30音同時押しクラッシュ絶対防止（最大12/16音制限）** | 1ms〜2msの同時Note-Onを厳しく監視し、実機CPUハングを根絶します。 | `disklavier_safety_guard.py` | `/api/status` |
| 62 | `G2` | **下声部からの1.8msマイクロスタッガー分散** | 分厚い和音は低音から1.8msずつずらして電源負荷と打鍵衝撃を平滑化します。 | `disklavier_safety_guard.py` | `/api/status` |
| 63 | `G3` | **秒間イベント密度上限（24〜28 notes/sec）** | 高密度パッセージのソレノイド過負荷を防ぐスマートスロットリングを実行します。 | `disklavier_safety_guard.py` | `/api/status` |
| 64 | `G4` | **ハンマーVDL打弦遅延の逆算先読み補正** | 弱音(約160ms)と強音(約25ms)の発音遅延差を自動補正し和音を揃えます。 | `disklavier_safety_guard.py` | `/api/status` |
| 65 | `G5` | **通常ベロシティ実効安全域 (28〜105) クランプ** | 空振り(1〜15)やハンマー過衝撃(>105)を排し自然なダイナミクスを守ります。 | `disklavier_safety_guard.py` | `/api/status` |
| 66 | `G6` | **一番右ペダル（CC#64）専用＆CC#66/67自動遮断** | 誤動作を防ぐためサスティンペダルCC#64のみを許可し他ペダルをブロックします。 | `disklavier_safety_guard.py` | `/api/status` |
| 67 | `G7` | **ソレノイド熱保護ウォッチドッグ（鍵盤8s／ペダル4m）** | 長時間の連続通電によるコイル発熱・電源保護作動を自動解放で防ぎます。 | `disklavier_safety_guard.py` | `/api/status` |
| 68 | `G8` | **最小スタッカート長 (35ms以上) 物理ガード** | ハンマーが弦に届かない短すぎるパルスを禁止し発音不良を防ぎます。 | `disklavier_safety_guard.py` | `/api/status` |
| 69 | `G9` | **レガート15〜30 / スタッカート65〜90 Note-Off制御** | 機械的キーオフノイズを抑えつつ歯切れと歌心を両立します。 | `disklavier_safety_guard.py` | `/api/status` |
| 70 | `G10` | **全鍵即時解放Panic（Emergency All-Notes-Off）** | 異常時に即座に全88鍵Note-OffとCC#64=0を送信して音を完全に止めます。 | `disklavier_safety_guard.py` | `/api/safety/panic` |

## カテゴリ H: バーチャルピアノ物理シミュレーション＆可視化 (10機能)

> 実機なしでもハンマー・ダンパー・弦共鳴・88鍵盤アニメーションを体感できる音響＆視覚機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 71 | `H1` | **バーチャル・ディスクラビア物理シミュレーションエンジン** | ハンマー運動、VDL遅延、弦振動、ダンパー位置をリアルタイム模擬します。 | `virtual_disklavier.py` | `/api/virtual_twin/config` |
| 72 | `H2` | **Mac CoreAudio内蔵ピアノシンセサイザー** | 実機ピアノが手元になくてもMacから豊かなピアノ音を直接再生します。 | `virtual_disklavier.py` | `/api/sound/enable` |
| 73 | `H3` | **発音前5秒カウントダウン安全確認フロー** | 起動時に突然大音が鳴るのを防ぎ、優しいカウントダウン後に鳴らし始めます。 | `mission_control_server.py` | `/api/sound/countdown` |
| 74 | `H4` | **88鍵盤リアルタイム打鍵＆キーカラー可視化** | 黒鍵・白鍵の押下状態と強弱・声部の違いを美しい鍵盤UIで表示します。 | `ui/index.html` | `/` |
| 75 | `H5` | **和音の波紋（ポリフォニック・リップル）演出** | 弾かれた音の響きを空間的な光の輪としてステージ上に可視化します。 | `ui/index.html` | `/` |
| 76 | `H6` | **右ペダル（CC#64）踏み込み量ゲージ** | 現在のダンパー開度とハーフペダル状態を滑らかなバーでリアルタイム監視します。 | `ui/index.html` | `/` |
| 77 | `H7` | **同時保持ボイス数・電流負荷リアルタイム監視** | 安全上限20音に対する現在の保持数とソレノイド負荷率を常時表示します。 | `disklavier_safety_guard.py` | `/api/status` |
| 78 | `H8` | **仮想VDL着弾タイムラグ可視化メーター** | 入力から実際の打弦までのミリ秒遅延と先読み補正効果を監視します。 | `virtual_disklavier.py` | `/api/status` |
| 79 | `H9` | **サウンドON/OFF・音量トリム瞬間切替** | ライブや深夜の練習に合わせてボタン1つでVirtual Soundを消音できます。 | `virtual_disklavier.py` | `/api/virtual_twin/config` |
| 80 | `H10` | **Mac環境自動フォールバック（オーディオデバイス保護）** | オーディオ出力先が未接続でもUIが停止せず安全にシミュレーション継続します。 | `virtual_disklavier.py` | `/api/status` |

## カテゴリ I: 記憶ノート（Obsidian）＆ローカル自動チェック (10機能)

> 成長日記・思考マップの自動記録と0.1秒以内のローカル安全事前監査パイプライン機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 81 | `I1` | **Obsidian Vault 自動成長日記マークダウン出力** | AIが各世代で学んだ発見やスコア推移をObsidianノートへ自動追記します。 | `obsidian_brain.py` | `/api/obsidian/snapshot` |
| 82 | `I2` | **イン・アプリ音楽知識グラフ＆進化系統樹・思考ノートビューア** | 外部Obsidian不要で系統樹・知識ネットワーク・採用理由・思考文字ノートをアプリ内閲覧できます。 | `obsidian_brain.py` | `/api/obsidian/explorer` |
| 83 | `I3` | **人間からの演奏リクエストノート双方向編集** | UIまたはObsidianのテキスト指令をAIピアニストの創作指針へ即反映します。 | `obsidian_brain.py` | `/api/obsidian/directives` |
| 84 | `I4` | **<0.1秒 ローカルプリフライト監査 ＆ 統合整合性テスト** | `LocalPreflightChecker` による0.1s未満保護に加え `validate_system_integrity.py` で全7スイートを検証します。 | `preflight_checker.py` / `validate_system_integrity.py` | `/api/state` |
| 85 | `I5` | **自動違反修復リペア（Auto-Repair Gate & Velocity）** | 万が一30ms未満の短音や禁止CCが含まれても発音前に自動修復します。 | `preflight_checker.py` | `/api/features/invoke` |
| 86 | `I6` | **パブリックドメイン名演特徴量コーパス (6名演プロファイル)** | Tchaikovsky/Rachmaninoff/Debussy/Chopin/Bach/Scherzandoの統計モデルを内包します。 | `human_reference_teacher.py` | `/api/virtuoso/status` |
| 87 | `I7` | **ローカルSVG/JSONによる軽量・高速統計ログ** | 外部ネットワーク不要で履歴・学習成果をすべてローカルディスクに保持します。 | `song_workspace.py` | `/api/tabs` |
| 88 | `I8` | **プロジェクト内臓Agent Skills (.agent/skills/) 運用** | UI/MIDI/並列計算の専門スキル定義をプロジェクト内に構造化して保持します。 | `.agent/skills/` | `/api/features/catalog` |
| 89 | `I9` | **完全ローカル完結ポリシー（GitHub Actions非依存）** | 通信遅延や外部キュー待ちのない高速ローカル完結サイクルを徹底します。 | `preflight_checker.py` | `/api/features/catalog` |
| 90 | `I10` | **履歴・タイムライン自動復旧キャッシュ** | 誤操作や再起動後も `data/workspace_state.json` から作業タブ群を自動復旧します。 | `song_workspace.py` | `/api/tabs` |

## カテゴリ J: スマホリモコン・システム診断＆アクセシビリティ (10機能)

> QRコードで使えるスマホ/iPadリモコン、日本語ヘルスチェック、緊急停止などの運用支援機能群

| # | ID | 機能名 | 内容 | 主な実装モジュール | API / 操作入口 |
|---|---|---|---|---|---|
| 91 | `J1` | **📱 スマホ・iPad専用ライブWebリモコン (/remote)** | 同じWi-Fi内のスマホから大文字・高コントラストUIで演奏を遠隔操作できます。 | `feature_matrix_100.py` | `/remote` |
| 92 | `J2` | **自動生成SVG QRコード接続モーダル** | 外部ライブラリ不要の純Python製QRコードでスマホから一瞬でカメラ読み取り接続できます。 | `feature_matrix_100.py` | `/api/remote/info` |
| 93 | `J3` | **LAN IP自動検出＆ネットワークURL案内** | MacのローカルIPアドレスを自動検出し接続用URLを画面に表示します。 | `feature_matrix_100.py` | `/api/remote/info` |
| 94 | `J4` | **WebSocket リアルタイムテレメトリ同期 (200ms間隔)** | 鍵盤状態・スコア・字幕・ペダルを遅延なくブラウザへストリーミングします。 | `mission_control_server.py` | `/ws` |
| 95 | `J5` | **WebSocket自動再接続＆フェイルセーフ** | 一時的な接続切れでも数秒後に自動で再接続しライブを止めません。 | `ui/index.html` | `/` |
| 96 | `J6` | **ワンクリック起動スクリプト (run_studio.sh)** | 環境準備・プロセス整理・サーバー起動・ブラウザ自動表示を一発で行います。 | `run_studio.sh` | `/` |
| 97 | `J7` | **🎹 音出し確認用ショートテスト奏（Test Tone / Phrase）** | ハードウェアおよびCoreAudioの配線確認を即座に行えます。 | `mission_control_server.py` | `/api/safety/test_note` |
| 98 | `J8` | **全100機能インタラクティブ機能エクスプローラー** | この100機能の全一覧を検索・フィルタ・ワンクリックお試し実行できます。 | `feature_matrix_100.py` | `/api/features/catalog` |
| 99 | `J9` | **🩺 システム健康診断（日本語ヘルスチェック）** | CPU・SIMD・MIDIポート・音量・安全ガード・ローカル監査を日本語で診断します。 | `feature_matrix_100.py` | `/api/features/invoke` |
| 100 | `J10` | **キーボードショートカット＆アクセシビリティ対応UI** | 単一行WAI-ARIAタブ、フォーカス管理、色覚コントラスト配慮で快適に操作できます。 | `ui/index.html` | `/` |
