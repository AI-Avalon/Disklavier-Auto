# Disklavier-Auto 100曲高精度作曲ライブラリ ＆ 代表10曲ショーケース (`COMPOSITION_SHOWCASE.md`)

Ollama (`qwen3.5:4b`) × `HarmonicBeautyRealizer` × `BeautyHarmonyAuditor` × コンサートホール物理モデリング音響シンセ (`LocalAudioSynthesizer`) によって作曲・厳選された **100曲（Mode B 50曲 + Mode A 50曲）** の統計および代表10曲の解説です。

## 1. 全100曲ライブラリ品質サマリ

- **総曲数**: `100 曲` (Mode B チェロ×ピアノ: `50` 曲 / Mode A ピアノ×ピアノ: `50` 曲)
- **品質ゲート合格率**: `100 / 100 曲 (100.0%)`
- **平均 美しさ総合スコア**: `97.75 / 100`
- **平均 協和率 (Consonance)**: `97.05 / 100` (平均無根拠不協和ペア率 `0.65%`)
- **平均 同時発音数 (Polyphony)**: `5.01 音` (休符以外の単音率平均 `1.12%`)
- **LLM 作曲設計比率**: Ollama 実推論 `100 / 100 曲`

## 2. 代表10曲 ホール実録音 MP3 ＆ MIDI ライブラリ

| # | 編成 | 作品タイトル | 調性 | BPM | 時間 | 美しさ | 協和率 | 同時音 | 単音率 | 試聴MP3 / MIDI |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 01 | Mode B (Vc+Pf) | ニ長調の問いかけ | ニ長調 | 64 | 63.82s | 99.78 | 99.9 | 5.24 | 0.6% | [MP3](showcase_01.mp3) / [MIDI](showcase_01.mid) |
| 02 | Mode B (Vc+Pf) | ヘ長調の問いかけ 第20番 | ヘ長調 | 76 | 54.16s | 99.73 | 99.9 | 5.05 | 0.8% | [MP3](showcase_02.mp3) / [MIDI](showcase_02.mid) |
| 03 | Mode B (Vc+Pf) | 変ホ長調の問いかけ | 変ホ長調 | 72 | 57.02s | 99.42 | 99.4 | 5.2 | 1.2% | [MP3](showcase_03.mp3) / [MIDI](showcase_03.mid) |
| 04 | Mode B (Vc+Pf) | ニ短調の静謐な問いかけ 第37番 | ニ短調 | 67 | 61.08s | 99.42 | 99.1 | 5.05 | 0.9% | [MP3](showcase_04.mp3) / [MIDI](showcase_04.mid) |
| 05 | Mode B (Vc+Pf) | 変イ長調の問いかけ | 変イ長調 | 82 | 50.4s | 99.14 | 98.7 | 5.19 | 1.4% | [MP3](showcase_05.mp3) / [MIDI](showcase_05.mid) |
| 06 | Mode A (Pf+Pf) | 変ホ長調の四重奏 | 変ホ長調 | 66 | 61.96s | 99.57 | 99.9 | 4.92 | 1.2% | [MP3](showcase_06.mp3) / [MIDI](showcase_06.mid) |
| 07 | Mode A (Pf+Pf) | 変イ長調の問いかけ 第84番 | 変イ長調 | 76 | 54.16s | 99.42 | 99.0 | 5.01 | 0.8% | [MP3](showcase_07.mp3) / [MIDI](showcase_07.mid) |
| 08 | Mode A (Pf+Pf) | ドリアの夜想曲 | ニ・ドリア旋法 | 75 | 54.85s | 99.36 | 99.7 | 4.99 | 1.7% | [MP3](showcase_08.mp3) / [MIDI](showcase_08.mid) |
| 09 | Mode A (Pf+Pf) | ハ長調の問いかけ | ハ長調 | 84 | 49.26s | 99.23 | 98.7 | 4.82 | 1.1% | [MP3](showcase_09.mp3) / [MIDI](showcase_09.mid) |
| 10 | Mode A (Pf+Pf) | ニ短調の問いかけ | ニ短調 | 95 | 43.87s | 98.96 | 98.1 | 4.81 | 1.4% | [MP3](showcase_10.mp3) / [MIDI](showcase_10.mid) |

## 3. 代表2曲（Mode B / Mode A 最優秀作品）スタンダード配信ファイル

- **Mode B 最優秀作品**: [MP3](demo_mode_b_cello_dialogue.mp3) | [MIDI](demo_mode_b_cello_dialogue.mid) | [MusicXML](demo_mode_b_cello_dialogue.musicxml)
- **Mode A 最優秀作品**: [MP3](demo_mode_a_piano_dialogue.mp3) | [MIDI](demo_mode_a_piano_dialogue.mid) | [MusicXML](demo_mode_a_piano_dialogue.musicxml)
