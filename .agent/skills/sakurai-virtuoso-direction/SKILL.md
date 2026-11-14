---
name: sakurai-virtuoso-direction
description: Architectural and UX/audio direction guidelines synthesized from kociden/sakurai-skill ("桜井政博のゲーム作るには" 14 chapters) tailored for Disklavier-Auto's Living Score UI, self-evolving composer studio, and stage audio choreography.
---

# Sakurai Virtuoso Direction Skill (桜井政博のゲーム作るには × Disklavier-Auto)

Adapted from `https://github.com/kociden/sakurai-skill` for interactive musical performance, Living Score feedback, and multi-generation evolutionary drama.

## 1. 手応えとヒットストップ (Tactile Response & Visual Hit-Stop)
- **Immediate Visual Mutation Flash**: Whenever an evolution generation advances, a user plays a passage via MIDI In / C519ML, or the Virtuoso Passage Reply fires, highlight the newly rewritten/mutated notes in luminous gold/emerald directly on the **Living Score View (五線譜)** with a crisp caption explaining *why* those notes evolved.
- **Zero-Ambiguity State Transitions**: Every stage button press (`構え` → `攻撃` → `フォロースルー`) must reflect immediately in the pre-sound countdown banner, radar chart delta, and score stream.

## 2. 構え・攻撃・フォロースルー (Windup → Strike → Follow-through)
- **構え (Windup)**: A 3-second glowing stage readiness banner precedes hardware playback so the performer and audience focus on the piano hammers before the first key moves.
- **攻撃 (Climax Strike)**: The 5-section formal arch builds tension through dominant/dim7 harmonies and contrary-motion bass lines before launching into **4-Hand Virtuoso Layering** or an **88-Key Crystal Cascade**.
- **フォロースルー (Resonant Decay)**: Phrases conclude with `Velocity 1` silent key depression (`sympathetic_halo=True`) and carefully damped Note-Off velocities (`15..30`) so overtones bloom naturally in the hall.

## 3. 誇張と省略 (Exaggeration & Audible Contrast)
- **Clear Gen 1 vs. Gen 30+ Dramatic Contrast**: Gen 1 must intentionally read and sound as an unpolished raw thematic sketch (~70 pts) with simple root accompaniment, making the leap to Gen 10 counterpoint (~86 pts) and Gen 30+ 4-hand virtuoso realism (~95–98 pts) unmistakable to any listener.
- **Omit Visual Noise**: Never display engineering jargon (`ポリフォニック・エントロピー`, `ゲート長`, `アクター・クリティック重み`) in the stage UI; show intuitive Japanese music studio copy instead.

## 4. サウンドの優先順位とメリハリ (Sound Hierarchy & Clarity)
- **Melody Lead First**: Keep the principal *Cantabile* melody `+15..+22` velocity above inner voices and `+15..+25 ms` ahead (VDL inverse compensated), ensuring inner obligatos never obscure the theme.
- **Dynamic Range Contrast ($pp \sim ff$)**: Preserve genuine breath pauses (`Rest`), agogic stretch near phrase climaxes, and clear contrast between delicate `pp` silent-key halos and `ff` climaxes within Disklavier safe bounds (`vel 20..105`).

## 5. リスクとリターン (Tension Curves & Hardware Safety)
- **Harmonic Tension → Consonant Triumph**: Reward chromaticism and diminished-7th suspense with soaring 6th/8th melody leaps and sweeping harmonic resolution.
- **Non-Negotiable Physical Bounds**: Exciting virtuoso passages must *never* gamble with Disklavier power supply stability: strictly enforce `<= 16` simultaneous Note-Ons (`<= 20` held voices), `>= 35 ms` note duration, `<= 28 notes/sec`, and `CC#64`-only pedal anticipation (`-30..-50 ms`).
