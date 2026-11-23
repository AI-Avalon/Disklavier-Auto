# PERFORMANCE AND PRODUCTION READINESS REPORT

- **Generated (UTC)**: `2026-11-23T14:52:28+00:00`
- **Overall Result**: **PASS**
- **GPU Hardware**: `NVIDIA GeForce RTX 3070 (8.0 GB)`

## 1. Suite 1 — Realtime Latency & Dispatch Jitter

| Metric | Measured Result | Target | Status |
| --- | --- | --- | --- |
| L1 Perception + NLMS (200 frames) | mean `0.422 ms`, P95 `0.652 ms` | P95 < `23.22 ms` | PASS |
| L3 4-Candidate Parallel Compose (10 turns) | mean `16.15 ms`, P95 `17.51 ms`, speedup `2.48x` | mean < 100.0 ms | PASS |
| Local Ollama LLM Inference | qwen3.5:4b | warm TTFT `53.3 ms` (total `514.6 ms`) | TTFT < 3000 ms | PASS |
| WebSocket `/ws/stage` 60fps Push | mean `20.81 ms`, P95 `29.195 ms`, jitter std `8.93 ms` | P95 < 60.0 ms | PASS |
| L5 Scheduled MIDI Dispatch Jitter | mean `0.076 ms`, P95 `0.323 ms` | P95 < 5.0 ms | PASS |
| L5 Emergency Panic Shutdown | `0.082 ms` (`active=0`) | < 10.0 ms | PASS |

## 2. Suite 2 — External Network Fault-Injection, Failover & Field Recorder

| Phase | Backend Used | Failover / Recovery | Measured RTT (ms) |
| --- | --- | --- | --- |
| `wan_50ms` | `remote_rtx` | OK | `60.32` |
| `wan_200ms` | `remote_rtx` | OK | `212.27` |
| `timeout_2500ms` | `mac_local_failover` | FAILOVER | `1812.34` |
| `hard_disconnect` | `mac_local_failover` | FAILOVER | `19.19` |
| `wan_recovery_50ms` | `remote_rtx` | RECOVERED | `56.83` |

- **Hybrid Transitions**: failover_count=`2`, recovery_count=`1`, crashes=`0` -> **PASS**
- **FieldSessionRecorder / Replayer**: WAV exists=`True`, trace_count=`2`, timeline_items=`9`, failover_events=`2`

## 3. Suite 3 — 55-Turn Endurance, VRAM/Memory & Physical Safety

| Verification Item | Measured Result | Safety Limit | Status |
| --- | --- | --- | --- |
| Continuous Mixed Turns (Mode B / A / Retake) | `55` turns | >= 50 turns | PASS |
| Peak GPU VRAM | `4.55 / 8.0 GB` | <= 8.0 GB | PASS |
| Steady-State Heap Delta (Turn 15 -> 55) | `1.33 MB` | < 25.0 MB | PASS |
| Peak Active Polyphony | `18 / 30` voices | <= 30 voices | PASS |
| Mode A Self-Echo Loop Leaks (50 returns) | `0` | 0 | PASS |
| Mode B Bleed False Triggers (20 blocks) | `0` (steady ERLE `+95.07 dB`) | 0 | PASS |
| Un-repaired Severe Clashes / Monophonic Gaps | `0` clashes / `0` mono | 0 / 0 | PASS |

## 4. Suite 4 — Playwright Visual, Art Direction Checklist & Mobile UI Audit

- **Passed**: **PASS** | Viewports Audited: `23` | Console/Page/HTTP Errors: `0/0/0`
- **Loaded Typography**: `Cormorant Garamond, JetBrains Mono, Noto Sans JP, Shippori Mincho`
- **Rule 4 Card Stability**: `swaps=0`, `recreated=0`, `CLS=0`
- **Checklist Verification**: `skip_link=True`, `modal_a11y=True`, `reduced_motion=True`, `stage_hud_trace=True`, `studio_trace=True`

