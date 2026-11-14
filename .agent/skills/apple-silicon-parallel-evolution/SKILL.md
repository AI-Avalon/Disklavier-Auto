---
name: apple-silicon-parallel-evolution
description: >
  Architecture guide for Apple Silicon multi-core ProcessPoolExecutor evolutionary search,
  optional Metal GPU (MPS) / NumPy SIMD vectorized scoring, and sub-0.1s local preflight
  MIDI physical safety auditing without external cloud CI workflows.
---

# Apple Silicon Parallel Evolution & Local Preflight Skill

## 1. Local-Only Fast Pipeline (No Cloud CI / GitHub Actions)
- Do not create `.github/workflows/` CI files. Run all verification locally on Mac with `<0.1s` turnaround via `src/safety/preflight_checker.py` (`LocalPreflightChecker`) and fast unit/integration tests.

## 2. Multi-Core Parallel Evolutionary Generation
- Use `concurrent.futures.ProcessPoolExecutor` (or a warm reusable worker pool bounded to `min(8, os.cpu_count() or 4)`) to generate and evaluate `8..16` candidate continuations per generation simultaneously.
- Keep worker functions pure, top-level module functions with JSON/picklable arguments (`genome_dict`, `motif_dict`, `prev_bar_state`, `story_arc_stage`, `expression_x`, `expression_y`, `seed`).
- Gracefully fall back to sequential or `ThreadPoolExecutor` execution when invoked inside daemon threads or restricted test sandboxes where process spawning is disallowed or slower for tiny batches.

## 3. Metal GPU (MPS) & SIMD Vectorization
- Detect hardware acceleration capabilities at startup:
  - Check `torch.backends.mps.is_available()` if PyTorch is installed; otherwise use Apple Accelerate-backed `numpy` vectorized matrix scoring (`np.ndarray` operations for harmonic consonance, roughness, and human-reference profile cosine similarity).
- Expose `hardware_accelerator_info()` in telemetry so the Studio UI shows real local acceleration status (e.g., `Apple Silicon 8-Core CPU + Vectorized Matrix Engine`).

## 4. Sub-0.1s Preflight Audit Before Every Dispatch
- Every composed candidate chunk or exported MIDI passes through `LocalPreflightChecker.audit_and_repair(events)` before playback or saving:
  1. Clamp velocity to `[20, 105]` (preserving `1` for `sympathetic_halo=True`).
  2. Enforce gate duration `>= 35 ms`.
  3. Micro-stagger chords exceeding 12 simultaneous onsets within 2 ms by `1.8 ms` bottom-up (hard cap 16).
  4. Enforce same-key period `>= 66 ms` (`<= 15 Hz`) by octave redistribution or merging.
  5. Verify `CC#64` only (strip `CC#66`/`CC#67`) and pre-roll `-30..-50 ms`.
