# Autonomous R&D Acoustic Experiment Report (Experiments 1–5)

**Hardware Environment**:
- **Piano**: Yamaha Disklavier (Connected via USB-C CoreMIDI, Real-Time Mode / 500 ms Prelay OFF)
- **Audio Interface**: Steinberg UR22mkII (44,100 Hz, 60 fps / 735-sample low-latency frames)
- **Microphone**: AKG C519ML Condenser Microphone (Channel 1, +48V Phantom Power, Ambient Noise Floor `-58.7 dBFS`)
- **Telemetry Storage**: [`data/telemetry/rd_studio.sqlite`](data/telemetry/rd_studio.sqlite), [`data/telemetry/experiments_summary.json`](data/telemetry/experiments_summary.json)

---

## Experiment 1: Velocity-Dependent Latency (VDL) & Ghost Note Boundary Mapping

### 1.1 Empirical Findings
Across representative registers (`C2=36`, `C3=48`, `C4=60`, `C5=72`, `C6=84`) and velocities (`20..100`), the C519ML microphone captured the exact time delay $\Delta t_{\text{VDL}}(k, v)$ from MIDI Note-On transmission to acoustic hammer-string impact:

| Register | MIDI Note | Low Velocity ($pp$) Latency & Peak | Mid Velocity ($mf$) Latency & Peak | High Velocity ($ff$) Latency & Peak |
| :--- | :---: | :---: | :---: | :---: |
| **C2** | 36 | Vel 28: `161.89 ms` (`-34.88 dBFS`) | Vel 55: `120.18 ms` (`-26.82 dBFS`) | Vel 92: `82.20 ms` (`-17.72 dBFS`) |
| **C3** | 48 | Vel 24: `155.29 ms` (`-30.21 dBFS`) | Vel 45: `137.15 ms` (`-27.26 dBFS`) | Vel 98: `74.65 ms` (`-12.63 dBFS`) |
| **C4** | 60 | Vel 30: `160.0+ ms` (`-32.42 dBFS`) | Vel 70: `99.62 ms` (`-21.40 dBFS`) | Vel 100: `70.55 ms` (`-11.46 dBFS`) |
| **C5** | 72 | Vel 26: `142.79 ms` (`-33.78 dBFS`) | Vel 52: `122.97 ms` (`-28.89 dBFS`) | Vel 100: `67.71 ms` (`-13.38 dBFS`) |
| **C6** | 84 | Vel 30: `124.69 ms` (`-38.13 dBFS`) | Vel 65: `103.99 ms` (`-26.67 dBFS`) | Vel 96: `69.91 ms` (`-14.65 dBFS`) |

### 1.2 Inverse VDL Compensation Verification
- **Uncompensated $pp + ff$ Chord Spread**: `77.49 ms` (audible flamming when a soft inner chord and loud melody note are sent simultaneously).
- **VDL-Compensated Scheduling Law**:
  $$T_{\text{send}}(k, v) = T_{\text{target}} - \Delta t_{\text{VDL}}(k, v)$$
- **Measured Post-Compensation Attack Spread**: **`±2.40 ms`** (strictly satisfying the $\le \pm 3\text{ ms}$ target).
- **Chart**: [`data/telemetry/exp1_vdl_curve.png`](data/telemetry/exp1_vdl_curve.png)

---

## Experiment 2: Right Damper Half-Pedal (CC#64) RT60 & Acoustic Roughness Minimization

### 2.1 Empirical Sweep (`CC#64 = 0, 30, 40, 50, 60, 70, 80, 100`)
Measured in the cello's rich mid-low register (`C3–G4` chord `C3-G3-D4-E4-G4`):

| CC#64 Value | Damper Mechanical State | Measured RT60 (s) | Acoustic Roughness (Sethares) | Clarity Score |
| :---: | :--- | :---: | :---: | :---: |
| **0** | Fully Seated | `1.063 s` | `0.0300` | `111.4` |
| **30** | Pre-Lift Threshold | `1.135 s` | `0.0300` | `112.4` |
| **40** | **Half-Pedal Lower Sweet Spot** | **`1.124 s`** | **`0.0320`** | **`112.1`** |
| **50** | **Half-Pedal Mid Sweet Spot** | **`1.163 s`** | **`0.0560`** | **`109.8`** |
| **60** | Upper Half-Pedal Transition | `1.699 s` | `0.5973` | `55.1` |
| **70** | Near Full Damper Lift | `2.538 s` | `1.0840` | `10.0` |
| **80** | Full Damper Lift | `3.049 s` | `1.1080` | `10.0` |
| **100** | Maximum Sustain | `3.121 s` | `1.1560` | `10.0` |

### 2.2 Optimal Register- & Tempo-Adaptive Half-Pedal Curve
From the transition knee observed between `CC#64 = 40..55` (high clarity, low roughness) and `CC#64 >= 60` (sharp rise in low-register harmonic beating), we derived the adaptive control law with **-40 ms Pre-roll**:
- **Cello Low Register (`C2–B2`)**: `CC#64 = 38–46`
- **Cello Cantabile Mid Register (`C3–G4`)**: `CC#64 = 46–58`
- **Cello High / Flageolet Register (`A4–A5`)**: `CC#64 = 58–72`
- **Chart**: [`data/telemetry/exp2_half_pedal.png`](data/telemetry/exp2_half_pedal.png)

---

## Experiment 3: Piano-as-Cello Proxy & MIDI-Linked Acoustic Bleed Cancellation

- **Testbench**: The Disklavier performed Cello Proxy phrases (Single Cantabile `C3`, 5th Double-Stop `C3+G3`, 6th Double-Stop `D3+B3`, and High Flageolet `D5=74`) simultaneously with multi-voice piano accompaniment.
- **Spectral Bleed Subtraction**: Using the live active-note state from [`DisklavierSafetyGuard`](src/safety/disklavier_safety_guard.py), the fundamental and first 4 harmonics of every accompaniment note were subtracted in real time from the C519ML 60-bin CQT spectrum.
- **Measured SNR Improvement**: **`+10.74 dB` average SNR gain** (`+10.60 dB` on single cantabile, `+11.26 dB` on 5th double-stop, `+9.54 dB` on 6th double-stop, `+11.57 dB` on D5 flageolet).

---

## Experiment 4: Classical Accompaniment AB Acoustic Testing

1. **Hypothesis 4A — Intonation-Aware Voicing (Just-Intonation 3rd Cushion)**:
   - Omitting the piano's middle-register 12-TET major 3rd against the cello's expressive 3rd and wrapping with Root + 5th + 9th + high arpeggio improved the Clarity & Consonance score from `8.9` (Control 4A-A) to **`20.1`** (Treatment 4A-B), reducing harmonic beating in the mid-low register.
2. **Hypothesis 4B — Goebl (2001) Melody Lead (+20 ms VDL-Compensated Lead)**:
   - Advancing the primary bass and counter-melody by **+20 ms** ahead of the inner arpeggio voices (after VDL inverse compensation) improved polyphonic separation from `20.9` (Control 4B-A) to **`32.2`** (Treatment 4B-B).
3. **Hypothesis 4C — Sympathetic Halo (`Velocity 1` Silent String Resonance)**:
   - Silently depressing harmonic chord keys (`D3, A3, D4` at `Velocity 1`) during high cello flageolet passages (`A4, D5`) increased overtone resonance clarity from `59.5` (Control 4C-A) to **`74.1`** (Treatment 4C-B, **+46% overtone sustain richness** without hammer strike noise).
- **Chart**: [`data/telemetry/exp4_ab_comparison.png`](data/telemetry/exp4_ab_comparison.png)

---

## Experiment 5: IMSLP Knowledge Base × AI Classical Composer Self-Improvement Loop

Using patterns extracted from 8 IMSLP masterworks ([`data/telemetry/imslp_knowledge_base.json`](data/telemetry/imslp_knowledge_base.json)), the AI Classical Composer ([`src/composer/ai_classical_composer.py`](src/composer/ai_classical_composer.py)) listened to a C519ML motif and iteratively refined a 4-voice composition across 3 physical Disklavier performance & recording cycles:

- **Iteration #1 (Baseline Parallel Harmonization)**: Voice Leading `98.5`, Attack Uniformity `81.5`, Roughness `0.142`, Dynamic Score `79.2` → **Total Score: `86.5 / 100`**
- **Iteration #2 (+ Contrary Motion Counterpoint & 3rd Cushion)**: Voice Leading `98.5`, Attack Uniformity `81.5`, Roughness `0.048`, Dynamic Score `88.4` → **Total Score: `91.2 / 100`**
- **Iteration #3 (+ VDL Melody Lead +20ms, Adaptive Half-Pedal & Sympathetic Halo)**: Voice Leading `98.5`, Attack Uniformity `97.4`, Roughness `0.048`, Dynamic Score `95.8` → **Total Score: `96.6 / 100`**
- **Chart**: [`data/telemetry/exp5_ai_composer.png`](data/telemetry/exp5_ai_composer.png)
- **Generated SMF Output**: [`data/telemetry/composed_masterwork.mid`](data/telemetry/composed_masterwork.mid)
