---
name: live-performance-japanese-ui
description: >
  Guidelines for designing and building Disklavier-Auto's intuitive, jargon-free Japanese
  live performance studio UI, multi-song workspace tabs, time-machine scrubber, grand staff
  sheet music view, and mobile touch remote controller. Inspired by anthropics/skills
  frontend-design and modern-web-guidance standards.
---

# Live Performance Japanese UI Skill

## Core Philosophy: Natural, Immediate, Stage-Ready Japanese
- **Ban mechanical jargon and translationese**: Never display terms like `モチーフ即時吸収`, `多面的`, `ポリフォニック・エントロピー`, `アクター・クリティック重み`, or `ゲート長` to performers or audience members.
- **Use vivid, human-centric Japanese**:
  - `曲の盛り上がり` / `歌い方のなめらかさ（レガート度）` / `リズムの活発さ・キレ`
  - `今のひらめきの源泉` / `AIピアニストの今の狙い`
  - `しっとり歌う（レガート） ⇄ 活発に跳ねる（ヴィヴァーチェ）`
  - `🔄 完全リセット（記憶を白紙にして一から新曲を作る）`
  - `✨ 曲の雰囲気だけ変える（進化した頭脳のまま別の曲調へ）`
  - `⚡ ひらめきスパーク（急にドラマチックな展開・転調を起こす）`
  - `⏪ 今の30秒前を救出！（タイムカプセル・ピン留め）`
  - `🛑 すぐ音を止める（緊急ストップ）`

## Modern Web Standards Compliance (`modern-web-guidance`)
1. **Multi-Song Workspace Tabs & Studio Views (`tabs`)**:
   - Keep tablists on a single horizontal row (`display: flex; overflow-x: auto`) with `flex: none` on tab buttons so they never wrap onto multiple lines.
   - Use `focusgroup="tablist nomemory"`, `aria-selected="true|false"`, and `focusgroupstart` on the active tab; show selection with an `::after` `border-block-end: 3px solid` underline (never color alone).
   - Hide inactive panels with `hidden` in initial HTML; keep padding/borders inside panel wrappers.
2. **Range Sliders & Time-Machine Scrubber (`slider`, `brand-consistent-forms`)**:
   - Always use native `<input type="range">` with explicit `<label for="...">` and `aria-valuetext`.
   - Use `accent-color` on `:root` / inputs, and include a full `@supports not (accent-color: ...)` or separate WebKit (`::-webkit-slider-runnable-track`, `::-webkit-slider-thumb`) and Firefox (`::-moz-range-track`, `::-moz-range-progress`, `::-moz-range-thumb`) styling blocks without ever combining vendor selectors in one comma-separated list.
   - Sync `--control-value`, `--attr-min`, and `--attr-max` in JavaScript on `input` events, and provide clear `:focus-visible` outlines.
3. **60fps Performance & Acoustic Feedback**:
   - Separate slow state polling / WebSocket ingestion from `requestAnimationFrame` particle ripples and 88-key / grand-staff canvas animations.
   - Provide clear countdown banners (`🟢 自動で作曲・進化中`, `🟡 あと 3.. 2.. 1.. 秒でピアノが鳴ります`, `🔵 自由に操作できます`).
