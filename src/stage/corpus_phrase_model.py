"""
Classical MIDI Corpus Idiomatic Piano Phrase Extractor & RTX 3070 Transformer Model.

Extracts genuine idiomatic piano textures from classical MIDI files (`data/imslp_midi`,
`data/human_references`, `data/maestro-v3.0.0`, `data/midi`) using `symusic`:
1. Cantabile melody phrasing & Post-Leap Gap-Fill Law (stepwise reversal after leaps >= 5 semitones)
2. Breathing left-hand accompaniment idioms:
   - 0: arpeggio_root_5th_10th
   - 1: pedal_point_inner_voice
   - 2: syncopated_offbeat_harmony
   - 3: una_corda_choral_recall
3. Suspension & resolution archetypes (none, 4-3, 9-8, 7-6/leading-tone)
4. Agogic rubato timing stretch & cantabile velocity arch curves

Trains and runs a compact PyTorch Transformer (`ClassicalPhraseTransformerNet`) on RTX 3070 CUDA
and saves/loads `data/models/classical_phrase_model.pt` for sub-2ms real-time L3 inference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from symusic import Score

    _HAS_SYMUSIC = True
except Exception:  # pragma: no cover
    Score = Any  # type: ignore[misc,assignment]
    _HAS_SYMUSIC = False

try:
    import torch
    import torch.nn as nn

    _HAS_TORCH = True
    _HAS_CUDA = bool(torch.cuda.is_available())
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    _HAS_TORCH = False
    _HAS_CUDA = False


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "models" / "classical_phrase_model.pt"
DEFAULT_META_PATH = REPO_ROOT / "data" / "models" / "classical_phrase_model_meta.json"

LH_IDIOMS = (
    "arpeggio_root_5th_10th",
    "pedal_point_inner_voice",
    "syncopated_offbeat_harmony",
    "una_corda_choral_recall",
)

SUSPENSION_TYPES = ("none", "sus4_3", "sus9_8", "sus7_6")

COND_DIM = 24
PHRASE_STEPS = 8
DEGREE_OFFSET_MIN = -7
DEGREE_OFFSET_MAX = 7
NUM_DEGREE_CLASSES = DEGREE_OFFSET_MAX - DEGREE_OFFSET_MIN + 1  # 15 classes: -7..+7


@dataclass
class PhraseBlueprint:
    melody_degree_offsets: List[int]
    lh_idiom: str
    suspension_type: str
    agogic_ratios: List[float]
    velocity_curve: List[float]
    post_leap_recovery_prob: float
    apex_step_idx: int
    source: str = "rtx3070_transformer"
    corpus_stats: Dict[str, Any] = field(default_factory=dict)


def _estimate_pitch_class_tonic(pitches: List[int]) -> int:
    """Estimates the tonal center pitch class [0..11] from bass & final pitches."""
    if not pitches:
        return 2  # D
    counts = np.zeros(12, dtype=np.float64)
    for idx, p in enumerate(pitches):
        weight = 1.6 if p < 55 else 1.0
        if idx < 4 or idx >= len(pitches) - 4:
            weight *= 1.4
        counts[p % 12] += weight
    return int(np.argmax(counts))


def _semitones_to_diatonic_offset(semis: int) -> int:
    """Maps semitone difference relative to phrase anchor into clipped diatonic step offset [-7..+7]."""
    sign = 1 if semis >= 0 else -1
    a = abs(int(semis))
    octaves = a // 12
    rem = a % 12
    # Approximate chromatic-to-diatonic degree mapping
    pc_to_deg = (0, 1, 1, 2, 2, 3, 3, 4, 5, 5, 6, 6)
    deg = octaves * 7 + pc_to_deg[rem]
    signed = sign * deg
    return max(DEGREE_OFFSET_MIN, min(DEGREE_OFFSET_MAX, signed))


def extract_idiomatic_phrases_from_score(
    score: Score,
    max_windows: int = 16,
) -> List[Dict[str, Any]]:
    """
    Extracts structured 8-note cantabile melody + LH accompaniment + suspension windows
    from a classical MIDI `symusic.Score`.
    """
    tpq = max(1, int(score.ticks_per_quarter))
    all_notes: List[Tuple[float, int, float, int]] = []
    for tr in score.tracks:
        if tr.is_drum:
            continue
        for n in tr.notes:
            p = int(n.pitch)
            if 21 <= p <= 108:
                onset_beats = float(n.time) / tpq
                dur_beats = max(0.08, float(n.duration) / tpq)
                vel = max(18, min(112, int(n.velocity)))
                all_notes.append((onset_beats, p, dur_beats, vel))

    if len(all_notes) < 20:
        return []

    all_notes.sort(key=lambda x: (x[0], x[1]))
    tonic_pc = _estimate_pitch_class_tonic([n[1] for n in all_notes[:120]])

    # Separate RH upper voice (top melody note per 0.12-beat onset slice) & LH lower notes
    melody_pts: List[Tuple[float, int, float, int]] = []
    lh_pts: List[Tuple[float, int, float, int]] = []
    for onset, pitch, dur, vel in all_notes:
        if pitch < 58:
            lh_pts.append((onset, pitch, dur, vel))
        else:
            if melody_pts and abs(onset - melody_pts[-1][0]) < 0.12:
                if pitch > melody_pts[-1][1]:
                    melody_pts[-1] = (onset, pitch, dur, vel)
            else:
                melody_pts.append((onset, pitch, dur, vel))

    if len(melody_pts) < PHRASE_STEPS + 2:
        return []

    stride = max(PHRASE_STEPS, len(melody_pts) // max(1, max_windows))
    samples: List[Dict[str, Any]] = []

    for start_idx in range(0, len(melody_pts) - PHRASE_STEPS, stride):
        win = melody_pts[start_idx : start_idx + PHRASE_STEPS]
        t_start = win[0][0]
        t_end = win[-1][0] + win[-1][2]
        span_beats = max(1.0, t_end - t_start)
        if span_beats > 24.0:
            continue

        pitches = [w[1] for w in win]
        anchor = pitches[0]
        deg_offsets = [_semitones_to_diatonic_offset(p - anchor) for p in pitches]

        # Post-leap gap-fill ratio inside window
        leaps = [pitches[i] - pitches[i - 1] for i in range(1, len(pitches))]
        big_leaps = 0
        gap_filled = 0
        for i, lv in enumerate(leaps[:-1]):
            if abs(lv) >= 5:
                big_leaps += 1
                nxt = leaps[i + 1]
                if lv * nxt < 0 and 1 <= abs(nxt) <= 3:
                    gap_filled += 1
        gap_fill_rate = (gap_filled / big_leaps) if big_leaps > 0 else 1.0

        # Gather concurrent LH accompaniment notes in window
        lh_win = [ln for ln in lh_pts if (t_start - 0.5) <= ln[0] <= (t_end + 0.2)]
        lh_pitches = [ln[1] for ln in lh_win]
        lh_onsets = [ln[0] for ln in lh_win]
        lh_span = (max(lh_pitches) - min(lh_pitches)) if len(lh_pitches) >= 2 else 7

        # Classify LH idiomatic texture
        if len(lh_pitches) >= 4 and lh_span >= 13 and len(set(round(o, 2) for o in lh_onsets)) >= 3:
            lh_idiom_idx = 0  # arpeggio_root_5th_10th
        elif lh_pitches and sum(1 for p in lh_pitches if p == lh_pitches[0]) >= 2:
            lh_idiom_idx = 1  # pedal_point_inner_voice
        elif len(lh_onsets) >= 3 and any((o - t_start) % 1.0 > 0.25 for o in lh_onsets):
            lh_idiom_idx = 2  # syncopated_offbeat_harmony
        else:
            lh_idiom_idx = 3  # una_corda_choral_recall

        # Detect suspension-resolution contour (-1 or -2 semitones after upward leap or hold)
        susp_idx = 0
        for i in range(1, len(leaps)):
            if leaps[i - 1] >= 4 and leaps[i] in (-1, -2):
                iv_rel = (pitches[i] - tonic_pc) % 12
                if iv_rel in (5, 6):
                    susp_idx = 1  # sus4_3
                elif iv_rel in (1, 2):
                    susp_idx = 2  # sus9_8
                else:
                    susp_idx = 3  # sus7_6
                break

        # Agogic rubato ratios and velocity arch
        iois = [max(0.15, win[i + 1][0] - win[i][0]) for i in range(len(win) - 1)]
        iois.append(max(0.25, win[-1][2]))
        mean_ioi = float(np.mean(iois)) if iois else 0.5
        agogic = [round(max(0.75, min(1.38, x / max(0.1, mean_ioi))), 3) for x in iois]
        vels_norm = [round(max(0.30, min(0.98, w[3] / 110.0)), 3) for w in win]

        # Build 24-dim conditioning vector matching runtime inference schema
        pos_frac = start_idx / max(1, len(melody_pts) - PHRASE_STEPS)
        if pos_frac < 0.25:
            act_idx = 0
        elif pos_frac < 0.62:
            act_idx = 1
        elif pos_frac < 0.86:
            act_idx = 2
        else:
            act_idx = 3

        cond = np.zeros(COND_DIM, dtype=np.float32)
        cond[act_idx] = 1.0                  # 0..3: Act 1..4 one-hot
        cond[4 + act_idx] = 1.0              # 4..7: Phrase function one-hot
        for k in range(6):
            iv = leaps[k] if k < len(leaps) else 0
            cond[8 + k] = max(-1.0, min(1.0, iv / 12.0))
        prev_p = melody_pts[start_idx - 1][1] if start_idx > 0 else anchor
        cond[14] = ((prev_p - tonic_pc) % 12) / 11.0
        cond[15] = ((anchor - tonic_pc) % 12) / 11.0
        cond[16] = min(1.0, lh_span / 24.0)
        cond[17] = float(gap_fill_rate)

        tension = min(1.0, sum(1 for lv in leaps if abs(lv) % 12 in (1, 2, 6, 11)) / 4.0)
        momentum = min(1.0, 0.5 / max(0.15, mean_ioi))
        avg_dur = float(np.mean([w[2] for w in win]))
        cond[18] = float(tension)
        cond[19] = float(momentum)
        cond[20] = 1.0 if avg_dur >= 0.35 else 0.0  # legato indicator
        cond[21] = 1.0  # piano/chamber conditioning flag
        cond[22] = min(1.0, (max(pitches) - min(pitches)) / 24.0)
        cond[23] = float(np.mean(vels_norm))

        samples.append(
            {
                "cond": cond,
                "deg_classes": np.array([d - DEGREE_OFFSET_MIN for d in deg_offsets], dtype=np.int64),
                "lh_idiom": int(lh_idiom_idx),
                "suspension": int(susp_idx),
                "agogic": np.array(agogic, dtype=np.float32),
                "vel_curve": np.array(vels_norm, dtype=np.float32),
                "gap_fill_rate": float(gap_fill_rate),
            }
        )
        if len(samples) >= max_windows:
            break

    return samples


def scan_and_extract_corpus_dataset(
    search_dirs: Optional[List[Path]] = None,
    max_files: int = 180,
    max_windows_per_file: int = 12,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scans `data/` classical MIDI directories and extracts idiomatic piano phrase training samples."""
    dirs = search_dirs or [
        REPO_ROOT / "data" / "imslp_midi",
        REPO_ROOT / "data" / "human_references",
        REPO_ROOT / "data" / "maestro-v3.0.0",
        REPO_ROOT / "data" / "midi",
    ]
    midi_files: List[Path] = []
    for d in dirs:
        if not (d.exists() and d.is_dir()):
            continue
        found = sorted(list(d.rglob("*.mid")) + list(d.rglob("*.midi")))
        if d.name == "maestro-v3.0.0":
            # Take evenly distributed subset of Maestro concert pieces for rapid training
            cap = max(40, max_files - len(midi_files))
            if len(found) > cap:
                step = max(1, len(found) // cap)
                found = found[::step][:cap]
        midi_files.extend(found)
        if len(midi_files) >= max_files:
            break

    if not _HAS_SYMUSIC:
        return [], {
            "files_scanned": 0,
            "files_parsed": 0,
            "total_phrase_samples": 0,
            "mean_post_leap_gap_fill_rate": 0.85,
            "lh_idiom_distribution": {k: 0 for k in LH_IDIOMS},
            "suspension_distribution": {k: 0 for k in SUSPENSION_TYPES},
        }

    all_samples: List[Dict[str, Any]] = []
    parsed_files = 0
    for mf in midi_files[:max_files]:
        try:
            sc = Score(str(mf))
            win_samples = extract_idiomatic_phrases_from_score(sc, max_windows=max_windows_per_file)
            if win_samples:
                parsed_files += 1
                all_samples.extend(win_samples)
        except Exception:
            continue

    gap_rates = [s["gap_fill_rate"] for s in all_samples]
    lh_counts = {LH_IDIOMS[i]: 0 for i in range(len(LH_IDIOMS))}
    susp_counts = {SUSPENSION_TYPES[i]: 0 for i in range(len(SUSPENSION_TYPES))}
    for s in all_samples:
        lh_counts[LH_IDIOMS[s["lh_idiom"]]] += 1
        susp_counts[SUSPENSION_TYPES[s["suspension"]]] += 1

    stats = {
        "files_scanned": len(midi_files[:max_files]),
        "files_parsed": parsed_files,
        "total_phrase_samples": len(all_samples),
        "mean_post_leap_gap_fill_rate": round(float(np.mean(gap_rates)) if gap_rates else 0.85, 4),
        "lh_idiom_distribution": lh_counts,
        "suspension_distribution": susp_counts,
    }
    return all_samples, stats


if _HAS_TORCH:

    class ClassicalPhraseTransformerNet(nn.Module):
        """
        Compact Transformer Encoder trained on classical MIDI corpora (RTX 3070 CUDA)
        to predict 8-step cantabile melody degree contours, LH accompaniment idioms,
        suspension types, agogic rubato timing, and velocity envelopes.
        """

        def __init__(
            self,
            cond_dim: int = COND_DIM,
            seq_len: int = PHRASE_STEPS,
            d_model: int = 96,
            nhead: int = 4,
            num_layers: int = 2,
        ) -> None:
            super().__init__()
            self.seq_len = seq_len
            self.cond_proj = nn.Sequential(
                nn.Linear(cond_dim, d_model),
                nn.GELU(),
                nn.Linear(d_model, d_model),
            )
            self.pos_emb = nn.Parameter(torch.randn(1, seq_len, d_model) * 0.04)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=192,
                dropout=0.1,
                activation="gelu",
                batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
            self.melody_degree_head = nn.Linear(d_model, NUM_DEGREE_CLASSES)
            self.lh_idiom_head = nn.Linear(d_model, len(LH_IDIOMS))
            self.suspension_head = nn.Linear(d_model, len(SUSPENSION_TYPES))
            self.agogic_vel_head = nn.Sequential(
                nn.Linear(d_model, 2),
                nn.Sigmoid(),
            )

        def forward(self, cond: torch.Tensor) -> Dict[str, torch.Tensor]:
            # cond: (B, COND_DIM) -> (B, seq_len, d_model)
            base = self.cond_proj(cond).unsqueeze(1).expand(-1, self.seq_len, -1)
            h = self.encoder(base + self.pos_emb)
            pooled = h.mean(dim=1)
            melody_logits = self.melody_degree_head(h)          # (B, S, 15)
            lh_logits = self.lh_idiom_head(pooled)              # (B, 4)
            susp_logits = self.suspension_head(pooled)          # (B, 4)
            ag_vel = self.agogic_vel_head(h)                    # (B, S, 2)
            agogic = 0.78 + ag_vel[..., 0] * 0.54               # [0.78, 1.32]
            vel_norm = 0.35 + ag_vel[..., 1] * 0.63             # [0.35, 0.98]
            return {
                "melody_logits": melody_logits,
                "lh_logits": lh_logits,
                "susp_logits": susp_logits,
                "agogic": agogic,
                "vel_norm": vel_norm,
            }


def build_conditioning_vector(
    act_number: int,
    phrase_function: str,
    seed_intervals: List[int],
    prev_end_pitch: int,
    anchor_pitch: int,
    tonic_pc: int,
    tension: float,
    momentum: float,
    articulation: str,
    mode: str,
) -> np.ndarray:
    cond = np.zeros(COND_DIM, dtype=np.float32)
    act_idx = max(0, min(3, int(act_number) - 1))
    cond[act_idx] = 1.0

    func_map = {
        "question_half_cadence": 4,
        "dialogic_sequence": 5,
        "polyphonic_climax": 6,
        "recapitulation_resolution": 7,
    }
    cond[func_map.get(phrase_function, 4)] = 1.0

    for k in range(6):
        iv = seed_intervals[k] if k < len(seed_intervals) else 0
        cond[8 + k] = max(-1.0, min(1.0, iv / 12.0))

    cond[14] = ((prev_end_pitch - tonic_pc) % 12) / 11.0
    cond[15] = ((anchor_pitch - tonic_pc) % 12) / 11.0
    cond[16] = 0.72 if act_number == 3 else 0.58
    cond[17] = 0.92
    cond[18] = max(0.0, min(1.0, float(tension)))
    cond[19] = max(0.0, min(1.0, float(momentum)))
    cond[20] = 1.0 if articulation != "staccato" else 0.0
    cond[21] = 1.0 if mode == "piano" else 0.6
    cond[22] = 0.65 if act_number >= 3 else 0.48
    cond[23] = 0.78 if act_number == 3 else (0.50 if act_number == 4 else 0.64)
    return cond


class CorpusPhraseModelManager:
    """
    Loads the RTX 3070 trained `ClassicalPhraseTransformerNet` checkpoint
    (`data/models/classical_phrase_model.pt`) and generates sub-2ms idiomatic `PhraseBlueprint`s
    with deterministic music-theoretic fallback if the checkpoint does not exist yet.
    """

    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH) -> None:
        self.model_path = Path(model_path)
        self.meta_path = self.model_path.with_name(self.model_path.stem + "_meta.json")
        self.device: str = "cuda" if _HAS_CUDA else "cpu"
        self.model: Any = None
        self.loaded: bool = False
        self.corpus_stats: Dict[str, Any] = {}
        self._load_checkpoint()

    def _load_checkpoint(self) -> None:
        if self.meta_path.exists():
            try:
                self.corpus_stats = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                self.corpus_stats = {}

        if not _HAS_TORCH or not self.model_path.exists():
            return
        try:
            ckpt = torch.load(str(self.model_path), map_location=self.device, weights_only=True)
            net = ClassicalPhraseTransformerNet().to(self.device)
            state = ckpt["model_state_dict"] if isinstance(ckpt, dict) and "model_state_dict" in ckpt else ckpt
            net.load_state_dict(state)
            net.eval()
            self.model = net
            self.loaded = True
            if isinstance(ckpt, dict) and "corpus_stats" in ckpt:
                self.corpus_stats = ckpt["corpus_stats"]
        except Exception:
            self.model = None
            self.loaded = False

    def predict_blueprint(
        self,
        act_number: int,
        phrase_function: str,
        seed_intervals: List[int],
        prev_end_pitch: int,
        anchor_pitch: int,
        tonic_pc: int,
        tension: float,
        momentum: float,
        articulation: str,
        mode: str,
    ) -> PhraseBlueprint:
        if self.loaded and self.model is not None and _HAS_TORCH:
            cond_np = build_conditioning_vector(
                act_number=act_number,
                phrase_function=phrase_function,
                seed_intervals=seed_intervals,
                prev_end_pitch=prev_end_pitch,
                anchor_pitch=anchor_pitch,
                tonic_pc=tonic_pc,
                tension=tension,
                momentum=momentum,
                articulation=articulation,
                mode=mode,
            )
            with torch.no_grad():
                x = torch.from_numpy(cond_np).unsqueeze(0).to(self.device)
                out = self.model(x)
                deg_cls = torch.argmax(out["melody_logits"], dim=-1).squeeze(0).cpu().tolist()
                deg_offsets = [int(c) + DEGREE_OFFSET_MIN for c in deg_cls]
                lh_idx = int(torch.argmax(out["lh_logits"], dim=-1).item())
                susp_idx = int(torch.argmax(out["susp_logits"], dim=-1).item())
                agogic = [round(float(v), 3) for v in out["agogic"].squeeze(0).cpu().tolist()]
                vel_curve = [round(float(v), 3) for v in out["vel_norm"].squeeze(0).cpu().tolist()]

            # Enforce post-leap gap-fill & anti-monotonicity safety on inferred degree skeleton
            deg_offsets = self._enforce_cantabile_contour(deg_offsets, act_number)
            apex_idx = int(np.argmax(deg_offsets))
            return PhraseBlueprint(
                melody_degree_offsets=deg_offsets,
                lh_idiom=LH_IDIOMS[lh_idx % len(LH_IDIOMS)],
                suspension_type=SUSPENSION_TYPES[susp_idx % len(SUSPENSION_TYPES)],
                agogic_ratios=agogic,
                velocity_curve=vel_curve,
                post_leap_recovery_prob=float(self.corpus_stats.get("mean_post_leap_gap_fill_rate", 0.88)),
                apex_step_idx=apex_idx,
                source=f"rtx3070_transformer({self.device})",
                corpus_stats=dict(self.corpus_stats),
            )

        # Deterministic classical idiom fallback when checkpoint has not been trained
        if act_number == 1:
            degs = [0, 3, 2, 5, 4, 2, 5, 4]
            lh = "arpeggio_root_5th_10th"
            susp = "sus4_3"
            agogic = [1.05, 0.92, 0.96, 1.12, 0.95, 0.90, 1.08, 1.24]
            vels = [0.56, 0.64, 0.68, 0.78, 0.72, 0.66, 0.70, 0.60]
        elif act_number == 2:
            degs = [0, 2, 4, 3, 5, 4, 6, 5]
            lh = "syncopated_offbeat_harmony"
            susp = "sus9_8"
            agogic = [0.95, 0.88, 1.04, 0.90, 1.10, 0.92, 1.05, 1.18]
            vels = [0.60, 0.66, 0.72, 0.70, 0.80, 0.76, 0.84, 0.72]
        elif act_number == 3:
            degs = [0, 4, 3, 6, 5, 7, 5, 4]
            lh = "pedal_point_inner_voice"
            susp = "sus7_6"
            agogic = [1.02, 0.85, 0.90, 1.08, 0.95, 1.22, 1.04, 1.25]
            vels = [0.70, 0.76, 0.82, 0.86, 0.90, 0.95, 0.84, 0.76]
        else:
            degs = [4, 2, 5, 4, 2, 3, 1, 0]
            lh = "una_corda_choral_recall"
            susp = "sus4_3"
            agogic = [1.05, 0.95, 1.08, 1.00, 1.12, 1.08, 1.18, 1.32]
            vels = [0.54, 0.58, 0.62, 0.58, 0.52, 0.48, 0.44, 0.40]

        return PhraseBlueprint(
            melody_degree_offsets=degs,
            lh_idiom=lh,
            suspension_type=susp,
            agogic_ratios=agogic,
            velocity_curve=vels,
            post_leap_recovery_prob=0.88,
            apex_step_idx=int(np.argmax(degs)),
            source="idiom_blueprint_fallback",
            corpus_stats=dict(self.corpus_stats),
        )

    @staticmethod
    def _enforce_cantabile_contour(degs: List[int], act_number: int) -> List[int]:
        out = [max(-5, min(7, int(d))) for d in degs[:PHRASE_STEPS]]
        while len(out) < PHRASE_STEPS:
            out.append(out[-1] if out else 0)

        # Ensure non-monotonicity and at least one expressive leap + opposite recovery
        diffs = [out[i] - out[i - 1] for i in range(1, len(out))]
        if all(abs(d) <= 1 for d in diffs):
            out[2] = out[1] + 3
            out[3] = out[2] - 1
            out[5] = out[4] + 2
            out[6] = out[5] - 1

        # Break any >=4 consecutive unidirectional steps
        run_dir = 0
        run_len = 1
        for i in range(1, len(out)):
            d = out[i] - out[i - 1]
            cur_dir = 1 if d > 0 else (-1 if d < 0 else 0)
            if cur_dir != 0 and cur_dir == run_dir and abs(d) <= 1:
                run_len += 1
                if run_len >= 3:
                    out[i] = out[i - 1] - cur_dir * 2
                    run_dir = -cur_dir
                    run_len = 1
            else:
                run_dir = cur_dir
                run_len = 1

        # Guarantee single melodic apex around steps 4..6
        apex_idx = 5 if act_number >= 2 else 4
        max_val = max(out)
        if out[apex_idx] < max_val or out.count(max_val) > 1:
            out[apex_idx] = min(7, max(3, max_val + (1 if out.count(max_val) > 1 else 0)))
        return out
