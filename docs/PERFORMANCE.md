# Disklavier-Auto Performance & Architecture Benchmark Report

All post-change measurements below were executed locally on Apple Silicon (macOS, Python 3.13.7, Virtual Twin mode — no physical Disklavier connected).

## Summary Comparison (Before vs. After Phase 1–3 Optimizations)

| Subsystem | Before (Baseline `e195bf9`) | After Optimization | Notes |
|---|---|---|---|
| **Event Dispatch Scheduler (`DisklavierSafetyGuard`)** | `time.sleep(0.0005)` busy 2kHz polling (~5% idle CPU, timer wake jitter) | Hybrid `threading.Condition(self._lock)` wake-on-event + sub-2.5ms precision tail | **0.156ms – 0.190ms mean jitter**, **0.383ms – 0.745ms p95** (`30 notes` tested without hitting hardware) |
| **WebSocket Telemetry Stream (`/ws`)** | Per-client `asyncio.sleep(1/60)` full JSON re-serialize (~198KB Obsidian graph + full `virtuoso` + `agent` + `studio_state` repeated -> >2,500 KB/s per client) | Single shared `WebSocketBroadcaster` at 30Hz (`fast` 30Hz / `virtuoso` 5Hz full-key delta / `slow` 1Hz `obsidian_meta`) | **41.67 KB/s per client** (`1,422 B` avg tick, `33.18 KB` initial frame, **< 200 KB/s target met by ~4.8x margin**) |
| **Obsidian Knowledge Graph (`224 nodes`, `1,046 edges`)** | `12.23 ms` mean under `self._lock` on every call | `15.54 ms` cold cache miss (file I/O outside `self._lock`), **`1.70 ms` warm hit mean** | Lock held only for microsecond version check & pointer swap (~7.2x speedup on cache hit) |
| **FastAPI Endpoint Imports** | 8 runtime `importlib.reload()` calls on request path | Top-level imports + `@asynccontextmanager async def lifespan` | Zero per-request module recompilation |
| **SQLite Telemetry Persistence** | Synchronous default rollback journal | `PRAGMA journal_mode=WAL;` + `generation_history` / `event_logs` indexed writes | Concurrent dashboard/search reads without blocking playback |
| **Sub-0.1s Local Safety Preflight (`LocalPreflightChecker`)** | `0.294 ms` mean on 83 events (all rules in Python loop) | `0.0021 ms` (empty tick) / `< 1 ms` per generated bar (< 100 ms target) | Runs deterministically before every bar & API export |

## Reproducible Verification Commands

```bash
# Condition scheduler dispatch jitter (mean < 2.5ms, p95 < 5.0ms)
PYTHONPATH=. .venv/bin/python tests/test_scheduler_jitter.py

# Full local CI suite (ruff + unit/security/integrity tests)
./scripts/run_ci_local.sh
```
