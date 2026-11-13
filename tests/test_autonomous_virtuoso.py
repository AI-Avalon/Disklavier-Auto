"""
Integration & unit tests for:
1. VirtualDisklavierTwin & DisklavierPhysicsSimulator (Ghost notes, VDL, Sag/Crash, Half-Pedal)
2. SeedMemoryPool ingestion from data/imslp_midi and data/telemetry
3. ContinuousVirtuosoEngine (Look-Ahead Ring Buffer, 5-Axis Actor-Critic, Hyper-Speed Evolution)
4. ObsidianMusicalBrain (Vault structure, Hall of Fame .mid export, Bidirectional WikiLinks, Directives)
"""

from __future__ import annotations

import time
from pathlib import Path

from src.evolution.actor_critic import MultiObjectiveCritic, ParallelCandidateGenerator
from src.evolution.continuous_virtuoso import ContinuousVirtuosoEngine
from src.evolution.seed_ingestor import SeedMemoryPool
from src.obsidian import ObsidianMusicalBrain
from src.safety.disklavier_safety_guard import DisklavierSafetyGuard
from src.virtual_twin import DisklavierPhysicsSimulator, VirtualDisklavierTwin


def test_physics_simulator_all_phenomena():
    sim = DisklavierPhysicsSimulator()

    # 1. Ghost Note simulation (velocity 1..15 without sympathetic_halo drops hammer)
    ghost = sim.simulate_note_on(note=60, velocity=8, send_time=1.0, target_sound_time=1.0)
    assert ghost["struck"] is False
    assert ghost["ghost_note_dropped"] is True
    assert sim.physics_stats["ghost_notes_simulated"] == 1

    # Sympathetic Halo vel=1 IS allowed to open silent string
    halo = sim.simulate_note_on(
        note=85, velocity=1, send_time=1.05, target_sound_time=1.05, sympathetic_halo=True
    )
    assert halo["struck"] is True
    assert halo["ghost_note_dropped"] is False

    # 2. VDL curve check: soft note (vel=35) has much larger latency than ff (vel=105)
    vdl_pp = sim.compute_vdl_ms(60, 35)
    vdl_ff = sim.compute_vdl_ms(60, 105)
    assert vdl_pp > 100.0
    assert vdl_ff < 40.0

    # 3. Half-pedal resonance & 45 ms inertia
    sim.update_pedal(64, now=2.0)
    sim.step_watchdog(now=2.05)  # >= 45ms elapsed
    assert 0.5 < sim.physics_stats["half_pedal_resonance_factor"] < 0.8

    # 4. Solenoid Sag (>= 17 in 1ms) & 30-Note Crash detection on unguarded burst
    for idx in range(30):
        sim.simulate_note_on(note=36 + idx, velocity=70, send_time=3.0, target_sound_time=3.0, guarded=False)
    assert sim.physics_stats["solenoid_sags_detected"] >= 1
    assert sim.physics_stats["crashes_detected"] >= 1


def test_virtual_twin_and_safety_guard_prevent_crashes():
    twin = VirtualDisklavierTwin(hardware_connected=False)
    twin.set_config(virtual_sound_enabled=False)
    guard = DisklavierSafetyGuard(port_name=None, virtual_twin=twin)

    now = time.perf_counter()
    # Send 25-note burst + ghost velocity=5 through SafetyGuard -> zero crashes/ghosts in Twin!
    burst = [(36 + i, 5 if i == 0 else 64, 0.25) for i in range(25)]
    guard.schedule_chord_or_notes(
        burst, target_sound_time=now + 0.18, use_vdl_compensation=True, pedal_cc64=64
    )
    time.sleep(0.28)
    snap = twin.get_telemetry_snapshot()
    pstats = snap["physics"]["stats"]
    assert pstats["ghost_notes_simulated"] == 0
    assert pstats["crashes_detected"] == 0
    assert guard.stats["ghost_notes_blocked"] >= 1
    assert guard.stats["staggered_notes"] >= 13

    guard.emergency_panic()
    twin.close()


def test_seed_pool_actor_critic_and_obsidian_evolution():
    pool = SeedMemoryPool(auto_ingest=True)
    motifs = pool.get_all_motifs()
    assert len(motifs) >= 10

    twin = VirtualDisklavierTwin(hardware_connected=False)
    twin.set_config(virtual_sound_enabled=False)
    guard = DisklavierSafetyGuard(port_name=None, virtual_twin=twin)
    engine = ContinuousVirtuosoEngine(safety_guard=guard, seed_pool=pool)
    brain = ObsidianMusicalBrain(
        seed_motifs=motifs,
        on_directives_changed=engine.update_directives,
    )
    engine.attach_obsidian_brain(brain)

    # Start continuous stream briefly to verify non-stop look-ahead ring buffer
    engine.start_stream()
    time.sleep(0.45)
    snap = engine.get_telemetry_snapshot()
    assert snap["streaming_active"] is True
    assert snap["lookahead_horizon_s"] >= 2.0
    assert len(snap["branches"]) == 4
    statuses = [b["status"] for b in snap["branches"]]
    assert statuses.count("ADOPTED") == 1
    assert statuses.count("BACKUP") == 1

    # Absorb live motif without stopping stream
    engine.absorb_live_motif([62, 65, 69, 68, 65, 62], source="pytest_cello")
    time.sleep(0.20)
    engine.stop_stream()

    # Apply human directive & run 12 generations of Hyper-Speed Evolution
    brain.write_human_directives(
        "# Human Directives\n- [x] 次はもっと短調の半音階的バス (chromatic bass) を増やす\n- [x] スタッカートとレガートの対比を強くする\n"
    )
    assert engine.directives["prefer_minor"] is True
    assert engine.directives["prefer_chromatic_bass"] is True

    res = engine.run_hyper_speed_training(num_generations=12, speed_multiplier=50.0)
    assert res["generations_run"] == 12
    assert res["best_score_ever"] >= 88.0

    # Verify Obsidian Vault artifacts
    vault = Path("obsidian_vault")
    assert (vault / "00_Dashboard" / "Evolution_Overview.md").exists()
    assert (vault / "04_Human_Directives.md").exists()
    kg = brain.get_knowledge_graph_snapshot()
    assert kg["stats"]["hof_count"] >= 1
    assert kg["stats"]["motif_count"] >= 10
    assert kg["stats"]["total_links"] >= 15

    # Verify Hall of Fame MIDI files exist on disk
    hof_midis = list((vault / "02_Hall_of_Fame_Performances" / "midi").glob("*.mid"))
    assert len(hof_midis) >= 1

    engine.close()
    brain.close()
    twin.close()
    print("ALL Autonomous Virtuoso, Digital Twin & Obsidian tests passed!")


if __name__ == "__main__":
    test_physics_simulator_all_phenomena()
    test_virtual_twin_and_safety_guard_prevent_crashes()
    test_seed_pool_actor_critic_and_obsidian_evolution()
