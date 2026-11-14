---
name: human-virtuoso-expressive-midi
description: >
  Domain rules and numerical performance laws extracted from human concert pianist MIDI
  datasets (IMSLP, GiantMIDI-Piano, Maestro, ATEPP) for True Cantabile Legato, Vivace/Scherzando
  articulation, emergent composer inspiration blending, and Yamaha Disklavier physical safety.
  Informed by open-source MIDI humanization and music-theory skills (ldrolez/free-midi-chords,
  aikkkkko/midi-orchestra).
---

# Human Virtuoso Expressive MIDI Skill

## 1. Emergent Composer Inspiration (No Hardcoded Buttons)
- Never force the player to click a fixed "absorb motif" or "style preset" to imitate a composer.
- Generate original thematic arcs from scratch (`Exposition -> Development -> Climax -> Recapitulation -> Coda`) and compute emergent inspiration affinities (`tchaikovsky`, `rachmaninoff`, `debussy`, `chopin`, `bach`) directly from melodic leap distributions, harmonic planing, counterpoint density, and rubato curves.
- Report the emergent result transparently in Japanese (e.g., `チャイコフスキー的な情熱的跳躍 65%・ドビュッシー的な透明な色彩 25%・バッハ的な対話 10%`).

## 2. True Cantabile Legato Laws (When `expression_x <= 0.1`)
- **Adjacent Note Overlap**: `+5 ms` to `+15 ms` between consecutive melody notes to eliminate 0 ms mechanical gaps.
- **Gradual Damping (Zero Damper Thud)**: Keep Note-Off release velocity in `15..30`.
- **Melody Lead & Projection**: Lead the singing melody onset by `+15 ms` to `+25 ms` relative to inner/accompaniment voices (or push accompaniment `+15..+25 ms` behind beat center) and boost melody velocity by `+12..+20`.
- **Anticipatory Half-Pedal (CC#64 only)**: Pre-roll pedal transitions `-30 ms` to `-50 ms` before harmonic arrivals, staying in the sweet spot `CC#64 = 35..80`.
- **Soft Chord Roll**: Gently arpeggiate lush chords by `12..22 ms` from bass to soprano.

## 3. True Vivace / Allegro / Scherzando Laws (When `expression_x > 0.1`)
- **Physical Solenoid Staccato Guarantee**: Minimum note gate `35 ms..50 ms` (NEVER `< 30 ms`), minimum inter-note gap `>= 20 ms`, and crisp Note-Off release velocity `65..90`.
- **Dynamic Contrast**: Contrast the stable linear range (`Velocity 30..85`) with high-energy climax accents (`Velocity 86..99`).
- **Repetition & Trill Protection**: Cap same-key repetition rate at `12..15 Hz` (period `>= 66..83 ms`, physical ceiling `20 Hz`); redistribute denser passages into sparkling octaves or chordal broken patterns.

## 4. Non-Negotiable Disklavier Hardware Safety Rules
- **No 30-Note Crash**: Max `12..16` simultaneous Note-Ons within `1..2 ms`; micro-stagger excess notes bottom-up by `1..3 ms`; max `20` held voices; max `28 notes/sec`.
- **Effective Velocity Window**: Clamp normal strikes to `20..105` (only allow `Velocity = 1` for deliberate `sympathetic_halo=True` silent open-string resonance).
- **VDL Inverse Compensation**: Advance hammer send timestamps by `Delta_t_VDL(note, velocity)` (`~25 ms` ff to `~160 ms` pp) and anchor Note-Off to acoustic strike time.
- **CC#64 Only & Thermal Watchdog**: Block `CC#66` / `CC#67`; keep keys `< 8–10s` and continuous pedal `< 4–10 min`; always expose immediate Panic.
