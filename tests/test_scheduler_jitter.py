"""
Scheduler dispatch jitter verification for DisklavierSafetyGuard (threading.Condition scheduler).
Schedules 30 timed MIDI notes over ~0.9s without physical hardware and measures actual dispatch jitter.
"""

from __future__ import annotations

import time

from src.safety.disklavier_safety_guard import DisklavierSafetyGuard


def test_condition_scheduler_jitter() -> None:
    guard = DisklavierSafetyGuard(port_name="__NON_EXISTENT_TEST_PORT__")
    scheduled_times = []
    try:
        t0 = time.perf_counter() + 0.06
        for i in range(30):
            target = t0 + i * 0.042
            note = 60 + (i % 12)
            # Clear VDL compensation and short duration to isolate Condition dispatch timing
            guard.schedule_chord_or_notes(
                notes=[(note, 72, 0.045)],
                target_sound_time=target,
                use_vdl_compensation=False,
                source_tag=f"jitter_{i}",
            )
            scheduled_times.append(target)

        # Wait until all scheduled notes finish dispatching
        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline:
            snap = guard.get_telemetry_snapshot()
            if snap["stats"]["total_notes_sent"] >= 30 and snap["vdl_buffer_count"] == 0:
                break
            time.sleep(0.01)

        snap = guard.get_telemetry_snapshot()
        jitter = snap["scheduler_jitter"]
        assert snap["stats"]["total_notes_sent"] == 30, snap["stats"]
        assert jitter["samples"] >= 30, jitter
        print(
            f"Condition scheduler jitter (30 notes): "
            f"mean={jitter['mean_ms']}ms, p95={jitter['p95_ms']}ms, max={jitter['max_ms']}ms"
        )
        assert jitter["mean_ms"] < 2.5, f"Mean jitter too high: {jitter}"
        assert jitter["p95_ms"] < 5.0, f"P95 jitter too high: {jitter}"
    finally:
        guard.close()


if __name__ == "__main__":
    test_condition_scheduler_jitter()
    print("ALL Condition scheduler jitter tests passed!")
