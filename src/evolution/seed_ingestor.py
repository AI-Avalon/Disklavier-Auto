"""
Seed Memory Ingestor: Scans all existing MIDI assets (`data/imslp_midi/*.mid`,
`data/telemetry/composed_masterwork.mid`, `imslp_knowledge_base.json`, and
`_build_masterwork_definitions()`) to extract core motifs, intervallic contours,
rhythm ratios, voice-leading contrary motion statistics, and half-pedal profiles
into `data/telemetry/seed_genome_pool.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mido

from src.midi.imslp_library import IMSLP_DIR, KB_JSON_PATH, _build_masterwork_definitions
from src.telemetry.telemetry_store import BASE_DIR, TELEMETRY_DIR, record_caught_exception


SEED_POOL_PATH = TELEMETRY_DIR / "seed_genome_pool.json"


def _extract_midi_file_stats(midi_path: Path) -> Dict[str, Any]:
    """Parses a .mid file with mido to extract melody line, bass line, contrary motion, and pedal."""
    mid = mido.MidiFile(str(midi_path))
    tempo_us = 500000
    notes_with_time: List[Dict[str, Any]] = []
    cc64_values: List[int] = []
    abs_t = 0.0

    merged = mido.merge_tracks(mid.tracks)
    for msg in merged:
        abs_t += mido.tick2second(msg.time, mid.ticks_per_beat, tempo_us)
        if msg.type == "set_tempo":
            tempo_us = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0:
            notes_with_time.append(
                {"t": round(abs_t, 4), "note": int(msg.note), "vel": int(msg.velocity)}
            )
        elif msg.type == "control_change" and msg.control == 64:
            cc64_values.append(int(msg.value))

    if not notes_with_time:
        return {
            "note_count": 0,
            "melody_pitches": [60, 64, 67, 72],
            "bass_pitches": [48, 43, 45, 48],
            "interval_vector": [0, 4, 7, 12],
            "rhythm_ratios": [1.0, 1.0, 1.0, 1.0],
            "contrary_motion_ratio": 0.65,
            "half_pedal_profile": {"mean_cc64": 60, "min_cc64": 18, "max_cc64": 68},
            "voicing_spread": 24,
        }

    # Separate upper melody (>= 60) and bass (<= 53)
    upper = [n for n in notes_with_time if n["note"] >= 60] or notes_with_time
    bass = [n for n in notes_with_time if n["note"] <= 53] or notes_with_time[:8]

    mel_pitches = [n["note"] for n in upper[:12]]
    bass_pitches = [n["note"] for n in bass[:12]]

    root = mel_pitches[0]
    interval_vec = [p - root for p in mel_pitches[:8]]

    # Rhythm ratios between upper melody onsets
    dts = []
    for i in range(1, min(9, len(upper))):
        dt = max(0.08, upper[i]["t"] - upper[i - 1]["t"])
        dts.append(dt)
    base_dt = min(dts) if dts else 0.4
    rhythm_ratios = [round(dt / max(0.08, base_dt), 2) for dt in dts] or [1.0, 1.0, 1.0]

    # Contrary motion estimate between consecutive melody & bass steps
    pairs = min(len(mel_pitches), len(bass_pitches)) - 1
    contrary = 0
    total_steps = 0
    for i in range(max(0, pairs)):
        dm = mel_pitches[i + 1] - mel_pitches[i]
        db = bass_pitches[i + 1] - bass_pitches[i]
        if dm != 0 or db != 0:
            total_steps += 1
            if (dm > 0 and db < 0) or (dm < 0 and db > 0):
                contrary += 1
    contrary_ratio = round(contrary / max(1, total_steps), 2) if total_steps > 0 else 0.68

    p_vals = [v for v in cc64_values if v > 25] or [62]
    all_pitches = [n["note"] for n in notes_with_time]
    spread = int(max(all_pitches) - min(all_pitches))

    return {
        "note_count": len(notes_with_time),
        "melody_pitches": mel_pitches[:8],
        "bass_pitches": bass_pitches[:8],
        "interval_vector": interval_vec,
        "rhythm_ratios": rhythm_ratios,
        "contrary_motion_ratio": max(0.45, contrary_ratio),
        "half_pedal_profile": {
            "mean_cc64": int(round(sum(p_vals) / len(p_vals))),
            "min_cc64": int(min(cc64_values)) if cc64_values else 18,
            "max_cc64": int(max(cc64_values)) if cc64_values else 68,
        },
        "voicing_spread": spread,
    }


def _infer_scale_from_pitches(pitches: List[int]) -> Tuple[int, List[int]]:
    if not pitches:
        return 61, [0, 2, 4, 5, 7, 9, 11]
    tonic = pitches[0]
    while tonic > 65:
        tonic -= 12
    while tonic < 48:
        tonic += 12
    pcs = sorted(set((p - tonic) % 12 for p in pitches))
    # Determine major vs minor vs Dorian/Lydian hint
    if 3 in pcs and 4 not in pcs:
        return tonic, [0, 2, 3, 5, 7, 8, 10]
    if 6 in pcs:
        return tonic, [0, 2, 4, 6, 7, 9, 11]
    return tonic, [0, 2, 4, 5, 7, 9, 11]


class SeedMemoryPool:
    """
    Indexes all existing repository MIDI assets, IMSLP works, and composed masterworks
    into an initial evolutionary seed memory pool (`seed_genome_pool.json`).
    """

    ALL_COMPOSERS = (
        "chopin",
        "debussy",
        "rachmaninoff",
        "tchaikovsky",
        "liszt",
        "bach",
        "faure",
        "saint_saens",
        "brahms",
        "mendelssohn",
    )

    def __init__(self, auto_ingest: bool = True) -> None:
        self.motifs: List[Dict[str, Any]] = []
        self.kb_metadata: Dict[str, Any] = {}
        if auto_ingest:
            self.ingest_all()

    def ingest_all(self) -> List[Dict[str, Any]]:
        motifs: List[Dict[str, Any]] = []

        # 1. Load imslp_knowledge_base.json if present
        if KB_JSON_PATH.exists():
            try:
                self.kb_metadata = json.loads(KB_JSON_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                record_caught_exception("seed_ingestor.load_kb_metadata", exc)
                self.kb_metadata = {}

        masterworks = {p["id"]: p for p in _build_masterwork_definitions()}

        # 2. Scan all .mid files in data/imslp_midi/
        if IMSLP_DIR.exists():
            for midi_file in sorted(IMSLP_DIR.glob("*.mid")):
                piece_id = midi_file.stem
                meta = masterworks.get(piece_id, {})
                stats = _extract_midi_file_stats(midi_file)
                mel = stats["melody_pitches"]
                tonic, scale = _infer_scale_from_pitches(mel)

                # Prefer curated melody voice events from masterwork definition when available
                if meta:
                    mel_evs = [
                        e
                        for e in meta.get("events", [])
                        if e.get("voice") in ("melody", "cello_motif") and int(e.get("note", 0)) > 0
                    ]
                    if mel_evs:
                        mel = [int(e["note"]) for e in mel_evs[:8]]
                        tonic, scale = _infer_scale_from_pitches(mel)

                motifs.append(
                    {
                        "motif_id": f"motif_{piece_id}",
                        "title": meta.get("title", piece_id.replace("_", " ").title()),
                        "composer": meta.get("composer", "Classical Master"),
                        "key": meta.get("key", "Concert Key"),
                        "tonic_midi": tonic,
                        "scale_intervals": scale,
                        "melody_pitches": mel,
                        "interval_vector": [p - mel[0] for p in mel] if mel else [0, 4, 7],
                        "rhythm_ratios": stats["rhythm_ratios"],
                        "contrary_motion_ratio": stats["contrary_motion_ratio"],
                        "half_pedal_profile": stats["half_pedal_profile"],
                        "voicing_spread": stats["voicing_spread"],
                        "voice_leading_pattern": meta.get(
                            "voice_leading_pattern", "Classical Polyphonic Legato"
                        ),
                        "source_file": midi_file.relative_to(BASE_DIR).as_posix(),
                        "tags": ["imslp", "seed_memory", piece_id.split("_")[0]],
                    }
                )

        # 3. Scan composed_masterwork.mid and other telemetry MIDI files
        for extra_mid in sorted(TELEMETRY_DIR.glob("*.mid")):
            stats = _extract_midi_file_stats(extra_mid)
            mel = stats["melody_pitches"]
            tonic, scale = _infer_scale_from_pitches(mel)
            motifs.append(
                {
                    "motif_id": f"motif_{extra_mid.stem}",
                    "title": f"Previous Session Masterwork ({extra_mid.stem})",
                    "composer": "Human x Antigravity Archive",
                    "key": "Evolved Concert Key",
                    "tonic_midi": tonic,
                    "scale_intervals": scale,
                    "melody_pitches": mel,
                    "interval_vector": stats["interval_vector"],
                    "rhythm_ratios": stats["rhythm_ratios"],
                    "contrary_motion_ratio": stats["contrary_motion_ratio"],
                    "half_pedal_profile": stats["half_pedal_profile"],
                    "voicing_spread": stats["voicing_spread"],
                    "voice_leading_pattern": "Captured Dialogue & Syncopated Legato",
                    "source_file": extra_mid.relative_to(BASE_DIR).as_posix(),
                    "tags": ["archive", "telemetry_midi"],
                }
            )

        # 4. Add curated Cello-Piano Counterpoint & Chromatic Mediant Seed Motifs
        extra_curated = [
            {
                "motif_id": "motif_cello_elegy_d_minor",
                "title": "D Minor Contrapuntal Cello Lament",
                "composer": "Seed Polyphony Pool",
                "key": "D Minor",
                "tonic_midi": 50,
                "scale_intervals": [0, 2, 3, 5, 7, 8, 10],
                "melody_pitches": [62, 65, 69, 68, 65, 64, 62],
                "interval_vector": [0, 3, 7, 6, 3, 2, 0],
                "rhythm_ratios": [1.5, 1.0, 2.0, 1.0, 1.0, 1.0],
                "contrary_motion_ratio": 0.78,
                "half_pedal_profile": {"mean_cc64": 60, "min_cc64": 18, "max_cc64": 66},
                "voicing_spread": 31,
                "voice_leading_pattern": "Chromatic Descending Bass + Expressive Cello Suspension",
                "source_file": "curated_seed_pool",
                "tags": ["cello", "minor", "chromatic_bass", "counterpoint"],
            },
            {
                "motif_id": "motif_impressionist_lydian_ab",
                "title": "A-flat Lydian Impressionist Nocturne",
                "composer": "Seed Impressionist Pool",
                "key": "A-flat Lydian",
                "tonic_midi": 56,
                "scale_intervals": [0, 2, 4, 6, 7, 9, 11],
                "melody_pitches": [68, 72, 74, 79, 75, 74, 72],
                "interval_vector": [0, 4, 6, 11, 7, 6, 4],
                "rhythm_ratios": [1.0, 1.5, 2.0, 1.0, 1.0, 1.5],
                "contrary_motion_ratio": 0.72,
                "half_pedal_profile": {"mean_cc64": 70, "min_cc64": 22, "max_cc64": 76},
                "voicing_spread": 36,
                "voice_leading_pattern": "Lydian #11 Planing + Half-Pedal Shimmer",
                "source_file": "curated_seed_pool",
                "tags": ["lydian", "impressionist", "half_pedal"],
            },
        ]
        motifs.extend(extra_curated)

        self.motifs = motifs
        self._persist()
        return self.motifs

    def _persist(self) -> None:
        SEED_POOL_PATH.write_text(
            json.dumps(
                {
                    "total_motifs": len(self.motifs),
                    "motifs": self.motifs,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def get_all_motifs(self) -> List[Dict[str, Any]]:
        return list(self.motifs)

    def get_motif(self, motif_id: str) -> Optional[Dict[str, Any]]:
        for m in self.motifs:
            if m["motif_id"] == motif_id:
                return m
        return self.motifs[0] if self.motifs else None

    def pick_next_motif(self, current_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.motifs:
            return {
                "motif_id": "motif_default",
                "title": "Default Concert Motif",
                "composer": "Autonomous Virtuoso",
                "key": "D-flat Major",
                "tonic_midi": 61,
                "scale_intervals": [0, 2, 4, 5, 7, 9, 11],
                "melody_pitches": [61, 65, 68, 73, 70, 68, 65],
                "interval_vector": [0, 4, 7, 12, 9, 7, 4],
                "rhythm_ratios": [1.0, 1.0, 1.5, 1.0, 1.0, 1.0],
                "contrary_motion_ratio": 0.72,
                "half_pedal_profile": {"mean_cc64": 66, "min_cc64": 18, "max_cc64": 72},
                "voicing_spread": 30,
                "tags": ["default"],
            }
        if current_id is None:
            return self.motifs[0]
        ids = [m["motif_id"] for m in self.motifs]
        if current_id in ids:
            idx = (ids.index(current_id) + 1) % len(self.motifs)
            return self.motifs[idx]
        return self.motifs[0]
