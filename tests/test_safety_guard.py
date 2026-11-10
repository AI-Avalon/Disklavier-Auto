"""
Unit tests for DisklavierSafetyGuard verifying all physical hardware protection rules
in dry-run mode (port_name=None).
"""

import time
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard


def test_disklavier_safety_guard_all_rules():
    guard = DisklavierSafetyGuard(port_name=None)

    # 1. Velocity 20-105 clamping & Ghost Note (1-15) protection + Velocity 1 Sympathetic Halo
    assert guard.sanitize_velocity(5, sympathetic_halo=False) == 20
    assert guard.sanitize_velocity(1, sympathetic_halo=True) == 1
    assert guard.sanitize_velocity(120, sympathetic_halo=False) == 105
    assert guard.sanitize_release_velocity(100) == 40
    assert guard.sanitize_release_velocity(5) == 15

    # 2. Pedal CC#64 only (block CC#66 and CC#67)
    assert guard.send_pedal_cc64(55, control=64) is True
    assert guard.current_pedal_val == 55
    assert guard.send_pedal_cc64(127, control=66) is False
    assert guard.send_pedal_cc64(127, control=67) is False
    assert guard.stats["blocked_cc_events"] == 2

    # 3. 30-Key simultaneous burst -> Micro-staggering & 20-Voice Stealing
    chord_25 = [(36 + i, 60, 0.20) for i in range(25)]
    scheduled = guard.schedule_chord_or_notes(
        chord_25, target_sound_time=time.perf_counter() + 0.01, use_vdl_compensation=False
    )
    assert len(scheduled) == 25
    assert guard.stats["staggered_notes"] >= 13

    # Wait briefly for staggered notes to fire and check active voice cap <= 20
    time.sleep(0.08)
    assert len(guard.active_notes) <= guard.MAX_ACTIVE_VOICES
    assert guard.stats["voice_steals"] >= 1

    # 4. Emergency panic resets all 88 keys & CC#64=0
    snap = guard.emergency_panic()
    assert snap["active_voices_count"] == 0
    assert snap["pedal_cc64"] == 0
    print("ALL DisklavierSafetyGuard tests passed successfully!")


if __name__ == "__main__":
    test_disklavier_safety_guard_all_rules()
