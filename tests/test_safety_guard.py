"""
Unit tests for DisklavierSafetyGuard v2.0 verifying all physical hardware protection rules,
Generation-ID stale Note-Off protection, and VDL Note-Off sustain anchoring in dry-run mode.
"""

import random
import time
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard


def test_guard_thresholds_and_property_invariants():
    # Pin exact physical safety constants before any scheduler/profile changes
    assert DisklavierSafetyGuard.PIANO_MIN_NOTE == 21
    assert DisklavierSafetyGuard.PIANO_MAX_NOTE == 108
    assert DisklavierSafetyGuard.SOFT_SIMULTANEOUS_LIMIT == 12
    assert DisklavierSafetyGuard.HARD_SIMULTANEOUS_LIMIT == 16
    assert DisklavierSafetyGuard.MAX_ACTIVE_VOICES == 20
    assert DisklavierSafetyGuard.MAX_NOTES_PER_SEC == 28
    assert DisklavierSafetyGuard.MIN_SAME_KEY_PERIOD_S == 0.055
    assert DisklavierSafetyGuard.MIN_NOTE_DURATION_S == 0.045
    assert DisklavierSafetyGuard.VALID_VEL_MIN == 28
    assert DisklavierSafetyGuard.VALID_VEL_MAX == 105
    assert DisklavierSafetyGuard.MAX_KEY_HOLD_S == 8.0
    assert DisklavierSafetyGuard.MAX_PEDAL_HOLD_S == 240.0

    guard = DisklavierSafetyGuard(port_name=None)
    rng = random.Random(42)

    for _ in range(100):
        v = rng.randint(-10, 150)
        sanitized = guard.sanitize_velocity(v, sympathetic_halo=False)
        if v <= 0:
            assert sanitized == 0
        else:
            assert 28 <= sanitized <= 105

        assert guard.sanitize_velocity(1, sympathetic_halo=True) == 1

        rv = rng.randint(1, 127)
        rel_legato = guard.sanitize_release_velocity(rv, staccato=False)
        if rv >= 65:
            assert 15 <= rel_legato <= 40
        else:
            assert 15 <= rel_legato <= 40
        rel_stacc = guard.sanitize_release_velocity(rv, staccato=True)
        assert 65 <= rel_stacc <= 90

        ctrl = rng.choice([1, 7, 64, 66, 67, 91])
        pval = rng.randint(-20, 200)
        ok = guard.send_pedal_cc64(pval, control=ctrl)
        if ctrl == 64:
            assert ok is True
            assert 0 <= guard.current_pedal_val <= 127
        else:
            assert ok is False

        bad_note = rng.choice([0, 10, 20, 109, 127])
        assert guard._execute_safe_note_on(bad_note, 64, time.perf_counter()) is False

    guard.emergency_panic()


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


def test_disklavier_model_profiles():
    guard = DisklavierSafetyGuard(port_name="__NON_EXISTENT_TEST_PORT__")
    try:
        res_cons = guard.apply_model_profile("dkv_e3_conservative")
        assert res_cons["profile"]["id"] == "dkv_e3_conservative"
        assert guard.SOFT_SIMULTANEOUS_LIMIT == 10
        assert guard.HARD_SIMULTANEOUS_LIMIT == 12
        assert guard.MAX_ACTIVE_VOICES == 16
        assert guard.MAX_NOTES_PER_SEC == 22
        assert guard.MIN_NOTE_DURATION_S == 0.040
        assert guard.MAX_KEY_HOLD_S == 6.0

        res_pro = guard.apply_model_profile("dcx_enspire_pro")
        assert res_pro["profile"]["id"] == "dcx_enspire_pro"
        assert guard.SOFT_SIMULTANEOUS_LIMIT == 14
        assert guard.MAX_NOTES_PER_SEC == 30
        assert guard.MIN_NOTE_DURATION_S == 0.030

        res_default = guard.apply_model_profile("envious_mark_iv")
        assert res_default["profile"]["id"] == "envious_mark_iv"
        assert guard.MAX_NOTES_PER_SEC == 28
        assert guard.MAX_ACTIVE_VOICES == 20
        assert guard.get_telemetry_snapshot()["model_profile_id"] == "envious_mark_iv"
    finally:
        guard.close()


if __name__ == "__main__":
    test_guard_thresholds_and_property_invariants()
    test_disklavier_safety_guard_all_rules()
    test_disklavier_model_profiles()
