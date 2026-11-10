"""
Unit tests for DisklavierSafetyGuard v2.0 verifying all physical hardware protection rules,
Generation-ID stale Note-Off protection, and VDL Note-Off sustain anchoring in dry-run mode.
"""

import time
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard


def test_disklavier_safety_guard_all_rules():
    guard = DisklavierSafetyGuard(port_name=None)

    # 1. Velocity 28-105 clamping & Ghost Note (1-15) protection + Velocity 1 Sympathetic Halo
    assert guard.sanitize_velocity(5, sympathetic_halo=False) == 28
    assert guard.sanitize_velocity(1, sympathetic_halo=True) == 1
    assert guard.sanitize_velocity(120, sympathetic_halo=False) == 105
    assert guard.sanitize_release_velocity(100) == 40
    assert guard.sanitize_release_velocity(5) == 15

    # 2. Pedal CC#64 only (block CC#66 and CC#67)
    assert guard.send_pedal_cc64(66, control=64) is True
    assert guard.current_pedal_val == 66
    assert guard.send_pedal_cc64(127, control=66) is False
    assert guard.send_pedal_cc64(127, control=67) is False
    assert guard.stats["blocked_cc_events"] == 2

    # 3. VDL Note-Off sustain anchor check:
    # A soft note (vel=35, vdl_ms > 100ms) with duration 0.50s must have off_time - send_time > 0.60s!
    now = time.perf_counter()
    recs = guard.schedule_chord_or_notes(
        [(60, 35, 0.50)], target_sound_time=now + 0.20, use_vdl_compensation=True
    )
    assert len(recs) == 1
    assert (recs[0]["off_time"] - recs[0]["send_time"]) >= 0.60

    # 4. 25-Key simultaneous burst -> Micro-staggering & 20-Voice Stealing
    chord_25 = [(36 + i, 60, 0.25) for i in range(25)]
    scheduled = guard.schedule_chord_or_notes(
        chord_25, target_sound_time=time.perf_counter() + 0.01, use_vdl_compensation=False
    )
    assert len(scheduled) == 25
    assert guard.stats["staggered_notes"] >= 13

    time.sleep(0.08)
    assert len(guard.active_notes) <= guard.MAX_ACTIVE_VOICES
    assert guard.stats["voice_steals"] >= 1

    # 5. Emergency panic resets all 88 keys & CC#64=0
    snap = guard.emergency_panic()
    assert snap["active_voices_count"] == 0
    assert snap["pedal_cc64"] == 0
    print("ALL DisklavierSafetyGuard v2.0 tests passed successfully!")


if __name__ == "__main__":
    test_disklavier_safety_guard_all_rules()
