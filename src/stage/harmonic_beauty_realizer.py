"""
Harmonic Beauty Realizer & Strict Beauty/Harmony Auditor (`harmonic_beauty_realizer.py`).

Eliminates monophonic dryness and accidental harsh dissonances by enforcing classical voice-leading,
chord-tone strong-beat landing, 4-3/9-8/7-6 suspension resolution, post-leap gap-fill contours,
idiomatic multi-voice LH accompaniment textures, physical vertical clash elimination,
and chord-boundary synchronized Damper (CC#64) + Una Corda (CC#67) curves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from src.safety.preflight_checker import LocalPreflightChecker


NOTE_TO_PC: Dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

PC_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PC_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Comprehensive 12+ tonalities (major, minor, dorian) with classical key signatures & names
TONALITY_SPECS: Dict[str, Dict[str, Any]] = {
    "C_major":  {"tonic_pc": 0,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": 0,  "prefer_flat": False, "ja": "ハ長調"},
    "G_major":  {"tonic_pc": 7,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": 1,  "prefer_flat": False, "ja": "ト長調"},
    "D_major":  {"tonic_pc": 2,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": 2,  "prefer_flat": False, "ja": "ニ長調"},
    "A_major":  {"tonic_pc": 9,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": 3,  "prefer_flat": False, "ja": "イ長調"},
    "E_major":  {"tonic_pc": 4,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": 4,  "prefer_flat": False, "ja": "ホ長調"},
    "F_major":  {"tonic_pc": 5,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": -1, "prefer_flat": True,  "ja": "ヘ長調"},
    "Bb_major": {"tonic_pc": 10, "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": -2, "prefer_flat": True,  "ja": "変ロ長調"},
    "Eb_major": {"tonic_pc": 3,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": -3, "prefer_flat": True,  "ja": "変ホ長調"},
    "Ab_major": {"tonic_pc": 8,  "intervals": [0, 2, 4, 5, 7, 9, 11], "is_minor": False, "fifths": -4, "prefer_flat": True,  "ja": "変イ長調"},
    "A_minor":  {"tonic_pc": 9,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": 0,  "prefer_flat": False, "ja": "イ短調"},
    "E_minor":  {"tonic_pc": 4,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": 1,  "prefer_flat": False, "ja": "ホ短調"},
    "B_minor":  {"tonic_pc": 11, "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": 2,  "prefer_flat": False, "ja": "ロ短調"},
    "F#_minor": {"tonic_pc": 6,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": 3,  "prefer_flat": False, "ja": "嬰ヘ短調"},
    "D_minor":  {"tonic_pc": 2,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": -1, "prefer_flat": True,  "ja": "ニ短調"},
    "D_dorian": {"tonic_pc": 2,  "intervals": [0, 2, 3, 5, 7, 9, 10], "is_minor": True,  "fifths": 0,  "prefer_flat": True,  "ja": "ニ・ドリア旋法"},
    "G_minor":  {"tonic_pc": 7,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": -2, "prefer_flat": True,  "ja": "ト短調"},
    "C_minor":  {"tonic_pc": 0,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": -3, "prefer_flat": True,  "ja": "ハ短調"},
    "F_minor":  {"tonic_pc": 5,  "intervals": [0, 2, 3, 5, 7, 8, 10], "is_minor": True,  "fifths": -4, "prefer_flat": True,  "ja": "ヘ短調"},
}

LH_TEXTURES: Tuple[str, ...] = (
    "open_arpeggio_10th",
    "broken_chord_barcarolle",
    "chorale_voicing",
    "countermelody_tenor",
    "alberti_lyrical",
)


def _pc_name(pc: int, prefer_flat: bool = False) -> str:
    arr = PC_NAMES_FLAT if prefer_flat else PC_NAMES_SHARP
    return arr[int(pc) % 12]


@dataclass
class ChordSpec:
    symbol: str
    root_pc: int
    bass_pc: int
    quality: str
    core_pcs: List[int]
    color_pcs: List[int]
    is_dominant: bool
    is_sus4: bool
    roman: str
    function_ja: str


def _roman_and_function(root_pc: int, quality: str, bass_pc: int, tonality: str) -> Tuple[str, str]:
    spec = TONALITY_SPECS.get(tonality, TONALITY_SPECS["D_minor"])
    tonic_pc = int(spec["tonic_pc"])
    is_minor = bool(spec["is_minor"])
    deg = (root_pc - tonic_pc) % 12

    if is_minor:
        deg_map = {
            0: ("i", "トニック"),
            1: ("bII", "ナポリ/借用"),
            2: ("ii", "サブドミナント"),
            3: ("III", "平行調"),
            4: ("III+", "借用"),
            5: ("iv", "サブドミナント"),
            6: ("#iv", "パッシング"),
            7: ("V" if "maj" in quality or "dom" in quality or "sus" in quality else "v", "ドミナント"),
            8: ("VI", "トニック代理"),
            9: ("vi", "ドリア借用"),
            10: ("VII", "属平行"),
            11: ("vii°", "導音ドミナント"),
        }
    else:
        deg_map = {
            0: ("I", "トニック"),
            1: ("bII", "ナポリ/借用"),
            2: ("ii", "サブドミナント"),
            3: ("bIII", "借用和音"),
            4: ("iii", "トニック代理"),
            5: ("IV", "サブドミナント" if "min" not in quality else "サブドミナントマイナー"),
            6: ("#iv°", "借用ドミナント"),
            7: ("V", "ドミナント"),
            8: ("bVI", "サブドミナントマイナー借用"),
            9: ("vi", "トニック代理"),
            10: ("bVII", "借用和音"),
            11: ("vii°", "導音ドミナント"),
        }
    base_rom, fn_ja = deg_map.get(deg, ("I", "トニック"))
    ext = ""
    if "maj9" in quality or "M9" in quality:
        ext = "maj9"
    elif "maj7" in quality:
        ext = "maj7"
    elif "sus4" in quality:
        ext = "7sus4" if "7" in quality or "9" in quality else "sus4"
    elif "m9" in quality:
        ext = "9"
    elif "m7b5" in quality:
        ext = "ø7"
    elif "m7" in quality or "m11" in quality:
        ext = "7"
    elif "dom9" in quality or "9" in quality:
        ext = "9"
    elif "dom7" in quality or "13" in quality:
        ext = "7"
    elif "add9" in quality:
        ext = "add9"
    elif "6" in quality:
        ext = "6"

    inv = ""
    if bass_pc != root_pc:
        rel_b = (bass_pc - root_pc) % 12
        is_seventh_or_ext = ext in ("7", "9", "maj7", "maj9", "ø7", "7sus4") or "7" in quality or "9" in quality or "13" in quality
        if is_seventh_or_ext:
            # Classical figured-bass inversions for 7th/9th chords (strip redundant 7/9 before 65/43/42)
            base_prefix = ext
            for suffix in ("maj9", "maj7", "7sus4", "ø7", "9", "7"):
                if ext.endswith(suffix):
                    prefix_map = {"maj9": "maj", "maj7": "maj", "ø7": "ø", "7sus4": "sus4", "9": "", "7": ""}
                    base_prefix = prefix_map[suffix]
                    break
            if rel_b in (3, 4):
                ext, inv = base_prefix, "65"
            elif rel_b == 7:
                ext, inv = base_prefix, "43"
            elif rel_b in (10, 11):
                ext, inv = base_prefix, "42"
        else:
            if rel_b in (3, 4):
                inv = "6"
            elif rel_b == 7:
                inv = "64"
            elif rel_b in (10, 11):
                inv = "42"
    return f"{base_rom}{ext}{inv}", fn_ja


_CHAMBER_TITLE_FORMS_CELLO: Tuple[str, ...] = (
    "静謐な問いかけ",
    "室内楽的対話",
    "緩徐な叙唱と変奏",
    "対位法の前奏曲",
    "内声の対話と回想",
    "弦と鍵盤のための小品",
)

_CHAMBER_TITLE_FORMS_PIANO: Tuple[str, ...] = (
    "対話的前奏曲",
    "多声のエレジー",
    "二台ピアノのための夜想曲",
    "開離和音の習作",
    "静かな転調と終止",
    "四幕の幻想小曲",
)


def sanitize_chamber_title_ja(title_ja: str, tonality_ja: str, mode: str, piece_idx: int = 1) -> str:
    """Normalizes LLM-generated titles to idiomatic, dignified chamber-music Japanese without melodrama."""
    t = (title_ja or "").replace("！", "").replace("!", "").strip()
    t = re.sub(r"\s+", " ", t)
    # Fix incomplete Japanese key names emitted by small local LLMs
    replacements = [
        ("へ長の", "ヘ長調の"),
        ("ヘ長の", "ヘ長調の"),
        ("へ短の", "ヘ短調の"),
        ("ヘ短の", "ヘ短調の"),
        ("嬰ヘ短の", "嬰ヘ短調の"),
        ("変ロの", "変ロ長調の"),
        ("変ホの", "変ホ長調の"),
        ("変イの", "変イ長調の"),
        ("ニ短の", "ニ短調の"),
        ("イ短の", "イ短調の"),
        ("ホ短の", "ホ短調の"),
        ("ロ短の", "ロ短調の"),
        ("ト短の", "ト短調の"),
        ("ハ短の", "ハ短調の"),
        ("小調", tonality_ja),
        ("大調", "長調"),
    ]
    for old, new in replacements:
        if old in t:
            t = t.replace(old, new)

    # Remove AI slop / melodramatic tokens ("叫び", "沈黙と叫び", "深淵", "魂", "風")
    banned_title_words = ("叫び", "絶叫", "咆哮", "深淵", "深き夜、弦が", "魂", "〜風", "破片", "暗黒", "嘆き")
    if (
        not t
        or len(t) < 6
        or len(t) > 64
        or any(w in t for w in banned_title_words)
        or re.search(r"^[A-G][b#]?\s*(minor|major|dorian)", t, flags=re.IGNORECASE)
    ):
        forms = _CHAMBER_TITLE_FORMS_CELLO if mode == "cello" else _CHAMBER_TITLE_FORMS_PIANO
        form = forms[(max(1, piece_idx) - 1) % len(forms)]
        return f"{tonality_ja}の{form} 第{piece_idx}番"
    return t


def sanitize_composer_note_ja(note_ja: str, tonality_ja: str, chords: Sequence[str]) -> str:
    """Removes prompt-echo artifacts ('事実記述のみ', typos, exclamation marks) from score notes."""
    s = (note_ja or "").replace("\n", " ").replace("！", "。").replace("!", "。").strip()
    s = re.sub(r"(を?事実記述する1文とする|を?事実記述のみ|事実記述のみ|事実記述)[。.]*", "", s)
    s = s.replace("破片和弦バールコレー", "分散和音バルカローレ")
    s = re.sub(r"破片和[弦声]", "分散和音", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[、,]+。", "。", s)
    s = re.sub(r"。{2,}", "。", s)
    c_start = chords[0] if chords else "Dm9"
    c_mid = chords[min(8, max(0, len(chords) - 1))] if chords else "Bbmaj7"
    c_end = chords[-1] if chords else "Dmadd9"
    if not s or len(s) < 18 or "。" not in s[-2:]:
        if s and len(s) >= 18 and not s.endswith("。"):
            s = s.rstrip("、, ") + "。"
        else:
            s = f"{tonality_ja}の4幕構成。第1幕は{c_start}で提示し、第3幕{c_mid}で多声頂点を築き、第4幕でウナ・コルダを伴い{c_end}へ終止する。"
    return s[:136]


def parse_chord_symbol(symbol: str, tonality: str = "D_minor") -> ChordSpec:
    raw = (symbol or "Dm9").strip()
    main_part, _, slash_part = raw.partition("/")
    m = re.match(r"^([A-G][b#]?)(.*)$", main_part)
    if not m:
        spec = TONALITY_SPECS.get(tonality, TONALITY_SPECS["D_minor"])
        root_pc = int(spec["tonic_pc"])
        qual_raw = "m9" if spec["is_minor"] else "maj9"
        raw = f"{_pc_name(root_pc, bool(spec['prefer_flat']))}{qual_raw}"
    else:
        root_str, qual_raw = m.group(1), m.group(2)
        root_pc = NOTE_TO_PC.get(root_str, 2)

    bass_pc = root_pc
    if slash_part:
        m_b = re.match(r"^([A-G][b#]?)", slash_part.strip())
        if m_b:
            bass_pc = NOTE_TO_PC.get(m_b.group(1), root_pc)

    q = qual_raw.strip()
    ql = q.lower()
    is_sus4 = "sus4" in ql or "sus" in ql
    is_minor_chord = (
        q.startswith("m") and not q.startswith("maj")
    ) or "dim" in ql or "m7b5" in ql
    is_dim = "dim" in ql or "m7b5" in ql

    if is_sus4:
        intervals = [0, 5, 7]
        if "7" in ql or "9" in ql or "13" in ql:
            intervals.append(10)
        if "9" in ql:
            intervals.append(2)
        quality = "7sus4" if ("7" in ql or "9" in ql) else "sus4"
    elif is_dim:
        intervals = [0, 3, 6]
        if "dim7" in ql:
            intervals.append(9)
            quality = "dim7"
        elif "m7b5" in ql or "7" in ql:
            intervals.append(10)
            quality = "m7b5"
        else:
            quality = "dim"
    elif is_minor_chord:
        intervals = [0, 3, 7]
        if "maj7" in ql:
            intervals.append(11)
            quality = "mMaj7"
        elif "11" in ql:
            intervals.extend([10, 2, 5])
            quality = "m11"
        elif "9" in ql:
            intervals.extend([10, 2])
            quality = "m9"
        elif "7" in ql:
            intervals.append(10)
            quality = "m7"
        elif "6" in ql:
            intervals.append(9)
            quality = "m6"
        elif "add9" in ql:
            intervals.append(2)
            quality = "madd9"
        else:
            quality = "min"
    else:
        intervals = [0, 4, 7]
        if "maj9" in ql or "m9" in q:
            intervals.extend([11, 2])
            quality = "maj9"
        elif "maj7" in ql or "M7" in q:
            intervals.append(11)
            if "#11" in ql:
                intervals.append(6)
            quality = "maj7"
        elif "13" in ql:
            intervals.extend([10, 2, 9])
            quality = "dom13"
        elif "b9" in ql:
            intervals.extend([10, 1])
            quality = "dom7b9"
        elif "9" in ql and "add9" not in ql:
            intervals.extend([10, 2])
            quality = "dom9"
        elif "7" in ql:
            intervals.append(10)
            quality = "dom7"
        elif "add9" in ql:
            intervals.append(2)
            quality = "add9"
        elif "6" in ql:
            intervals.append(9)
            quality = "maj6"
        else:
            quality = "maj"

    core_pcs: List[int] = []
    for iv in intervals:
        pc = (root_pc + iv) % 12
        if pc not in core_pcs:
            core_pcs.append(pc)
    if bass_pc not in core_pcs:
        core_pcs.append(bass_pc)

    # Color tones (9th, 6th, 11th) that blend cleanly if consonant
    color_pcs: List[int] = []
    for ext_iv in (2, 9):
        pc = (root_pc + ext_iv) % 12
        if pc not in core_pcs:
            color_pcs.append(pc)

    is_dominant = quality in ("dom7", "dom9", "dom13", "dom7b9")
    roman, fn_ja = _roman_and_function(root_pc, quality, bass_pc, tonality)
    return ChordSpec(
        symbol=raw,
        root_pc=root_pc,
        bass_pc=bass_pc,
        quality=quality,
        core_pcs=core_pcs,
        color_pcs=color_pcs,
        is_dominant=is_dominant,
        is_sus4=is_sus4,
        roman=roman,
        function_ja=fn_ja,
    )


def build_functional_4act_chords(tonality: str, variant: int = 0) -> Dict[int, List[str]]:
    """
    Constructs authentic, lyrical 4-act functional chord progressions (4 chords per act = 16 bars)
    for ANY tonality in TONALITY_SPECS, featuring inversions (/3rd, /5th), subdominant minor,
    6ths, 9ths, and sus4 -> dominant resolutions.
    """
    spec = TONALITY_SPECS.get(tonality, TONALITY_SPECS["D_minor"])
    t = int(spec["tonic_pc"])
    pf = bool(spec["prefer_flat"])
    is_minor = bool(spec["is_minor"])

    def n(semi: int) -> str:
        return _pc_name((t + semi) % 12, pf)

    if is_minor:
        i = n(0)
        ii = n(2)
        III = n(3)
        iv = n(5)
        V = n(7)
        VI = n(8)
        VII = n(10)
        third = n(3)
        fifth = n(7)
        lead = _pc_name((t + 11) % 12, False if pf and (t + 11) % 12 in (1, 4, 6, 9, 11) else pf)
        # Prefer clean enharmonic for leading tone in flat minor keys
        if tonality in ("D_minor", "D_dorian"):
            lead = "C#"
        elif tonality == "G_minor":
            lead = "F#"
        elif tonality == "C_minor":
            lead = "B"
        elif tonality == "F_minor":
            lead = "E"

        if variant % 3 == 0:
            return {
                1: [f"{i}m9", f"{iv}m6/{n(8)}", f"{i}m/{fifth}", f"{V}7sus4"],
                2: [f"{III}maj7", f"{VI}maj7", f"{ii}m7b5", f"{V}7"],
                3: [f"{VI}maj9", f"{iv}m9", f"{i}m/{fifth}", f"{V}9"],
                4: [f"{iv}m6", f"{V}7sus4", f"{V}7", f"{i}madd9"],
            }
        elif variant % 3 == 1:
            return {
                1: [f"{i}m7", f"{V}7/{lead}", f"{i}m/{third}", f"{V}7"],
                2: [f"{VII}9", f"{III}maj9", f"{iv}m7", f"{V}7sus4"],
                3: [f"{iv}m7", f"{VII}7", f"{III}maj7", f"{V}7"],
                4: [f"{VI}maj7", f"{ii}m7b5", f"{V}7", f"{i}m9"],
            }
        else:
            return {
                1: [f"{i}madd9", f"{VI}maj7", f"{iv}m7", f"{V}7sus4"],
                2: [f"{i}m/{third}", f"{iv}m6", f"{V}7", f"{VI}maj9"],
                3: [f"{ii}m7b5", f"{V}7", f"{VI}maj7", f"{V}7"],
                4: [f"{iv}m6", f"{i}m/{fifth}", f"{V}7", f"{i}madd9"],
            }
    else:
        tonic = n(0)
        ii = n(2)
        iii = n(4)
        IV = n(5)
        V = n(7)
        vi = n(9)
        third = n(4)
        fifth = n(7)
        bVI = n(8)
        iv_m = n(5)
        if variant % 3 == 0:
            return {
                1: [f"{tonic}maj9", f"{vi}m7", f"{IV}maj7/{n(9)}", f"{V}9sus4"],
                2: [f"{tonic}maj7/{third}", f"{IV}maj9", f"{ii}m9", f"{V}7"],
                3: [f"{vi}m9", f"{bVI}maj7", f"{tonic}/{fifth}", f"{V}13"],
                4: [f"{IV}maj7", f"{iv_m}m6", f"{V}9sus4", f"{tonic}add9"],
            }
        elif variant % 3 == 1:
            return {
                1: [f"{tonic}add9", f"{iii}m7", f"{ii}m7", f"{V}7"],
                2: [f"{vi}m7", f"{ii}m9", f"{V}7/{n(11)}", f"{tonic}maj7"],
                3: [f"{IV}maj9", f"{V}7/{n(2)}", f"{iii}m7", f"{V}9"],
                4: [f"{ii}m9", f"{V}9sus4", f"{V}7", f"{tonic}maj9"],
            }
        else:
            return {
                1: [f"{tonic}maj7", f"{V}7/{n(11)}", f"{vi}m9", f"{V}7sus4"],
                2: [f"{IV}maj7", f"{tonic}/{third}", f"{ii}m7", f"{V}9"],
                3: [f"{bVI}maj7", f"{IV}m6", f"{tonic}/{fifth}", f"{V}7"],
                4: [f"{vi}m7", f"{ii}m7", f"{V}9", f"{tonic}add9"],
            }


def quantize_to_pitch_set(pitch: int, pcs: Sequence[int], lo: int = 24, hi: int = 96) -> int:
    target = max(lo, min(hi, int(pitch)))
    valid = sorted({int(p) % 12 for p in pcs}) or [target % 12]
    best = target
    best_dist = 999
    for cand in range(max(lo, target - 11), min(hi, target + 11) + 1):
        if (cand % 12) in valid:
            d = abs(cand - target)
            if d < best_dist:
                best_dist = d
                best = cand
    return best


def quantize_to_tonality(pitch: int, tonality: str, lo: int = 24, hi: int = 96) -> int:
    spec = TONALITY_SPECS.get(tonality, TONALITY_SPECS["D_minor"])
    tonic_pc = int(spec["tonic_pc"])
    ivs = list(spec["intervals"])
    pcs = [(tonic_pc + iv) % 12 for iv in ivs]
    return quantize_to_pitch_set(pitch, pcs, lo=lo, hi=hi)


def extract_corpus_musical_laws(corpus_dirs: Optional[List[Path]] = None) -> Dict[str, Any]:
    """
    Extracts universal voice-leading & cantabile laws from classical MIDI files in data/
    without hardcoding composer-imitation presets.
    """
    root = Path(__file__).resolve().parents[2]
    dirs = corpus_dirs or [root / "data" / "imslp_midi", root / "data" / "human_references"]
    midi_files: List[Path] = []
    for d in dirs:
        if d.exists() and d.is_dir():
            midi_files.extend(sorted(list(d.glob("*.mid")) + list(d.glob("*.midi"))))

    leap_recoveries = 0
    big_leaps = 0
    spreads: List[float] = []
    step_ratios: List[float] = []
    files_read = 0

    try:
        from symusic import Score
    except Exception:
        Score = None  # type: ignore[assignment]

    if Score is not None:
        for mf in midi_files[:18]:
            try:
                sc = Score(str(mf))
                notes = sorted(
                    [
                        (int(n.time), int(n.pitch))
                        for tr in sc.tracks
                        if not tr.is_drum
                        for n in tr.notes
                        if 21 <= int(n.pitch) <= 108
                    ],
                    key=lambda x: x[0],
                )
                if len(notes) < 24:
                    continue
                files_read += 1
                sample = notes[:120]
                rh = [p for _, p in sample if p >= 60]
                lh = [p for _, p in sample if p < 60]
                if rh and lh:
                    spreads.append(float(np.median(rh) - np.median(lh)))
                ivs = [rh[i] - rh[i - 1] for i in range(1, len(rh))]
                if ivs:
                    step_ratios.append(sum(1 for iv in ivs if 1 <= abs(iv) <= 2) / len(ivs))
                for i in range(len(ivs) - 1):
                    if abs(ivs[i]) >= 5:
                        big_leaps += 1
                        if ivs[i] * ivs[i + 1] < 0 and 1 <= abs(ivs[i + 1]) <= 4:
                            leap_recoveries += 1
            except Exception:
                continue

    gap_fill_rate = round(leap_recoveries / max(1, big_leaps), 2) if big_leaps else 0.78
    median_spread = int(round(float(np.median(spreads)))) if spreads else 22
    clamped_spread = max(16, min(28, median_spread))
    stepwise_share = round(float(np.mean(step_ratios)), 2) if step_ratios else 0.56
    return {
        "corpus_files_analyzed": files_read,
        "post_leap_gap_fill_rate": max(0.72, gap_fill_rate),
        "median_hand_spread_semitones": clamped_spread,
        "stepwise_to_leap_ratio": stepwise_share,
        "law_summary": f"files={files_read},gap_fill={max(0.72, gap_fill_rate):.2f},spread={clamped_spread}st",
    }


@dataclass
class BeautyAuditReport:
    passed: bool
    beauty_score: float
    consonance_score: float
    polyphony_score: float
    melody_cantabile_score: float
    harmonic_progression_score: float
    pedal_cleanliness_score: float
    avg_polyphony: float
    mono_ratio_pct: float
    dissonant_pair_pct: float
    strong_beat_chord_hit_pct: float
    repedal_sync_pct: float
    preflight_passed: bool
    issues: List[str] = field(default_factory=list)


def _is_justified_tension(
    p_lo: int,
    p_hi: int,
    t_slice: float,
    chord: ChordSpec,
    suspension_windows: List[Tuple[float, float, int]],
) -> bool:
    iv = (p_hi - p_lo) % 12
    # 1. Explicit suspension note that resolves downward within 0.6s
    for s_t0, s_t1, s_pitch in suspension_windows:
        if s_t0 - 0.05 <= t_slice <= s_t1 + 0.05 and (p_lo == s_pitch or p_hi == s_pitch):
            return True
    # 2. Dominant 7th tritone between chord's 3rd and b7th in middle/upper register (>= 53)
    if iv == 6 and chord.is_dominant and p_lo >= 53:
        third_pc = (chord.root_pc + 4) % 12
        sev_pc = (chord.root_pc + 10) % 12
        if {p_lo % 12, p_hi % 12} == {third_pc, sev_pc}:
            return True
    # 3. Maj7 / m7 warm upper-register chord color (>= 57) when separated >= 11 semitones
    if iv == 11 and (p_hi - p_lo) >= 11 and p_lo >= 57:
        if (p_lo % 12) in chord.core_pcs and (p_hi % 12) in chord.core_pcs:
            return True
    # 4. Add9 / 9th interval in upper/mid spread (>= 14 semitones)
    if iv == 2 and (p_hi - p_lo) >= 14 and p_lo >= 52:
        if (p_lo % 12) in chord.core_pcs and (p_hi % 12) in chord.core_pcs:
            return True
    return False


class HarmonicBeautyRealizer:
    """
    Realizes LLM harmonic & melodic blueprints into rich, consonant, polyphonic MIDI structures,
    automatically avoiding minor-2nd/tritone collisions, monophonic gaps, and pedal mud.
    """

    @staticmethod
    def _build_bass_register(bass_pc: int, prev_bass: Optional[int]) -> int:
        cands = [m for m in range(28, 49) if (m % 12) == bass_pc]
        if not cands:
            return 38
        if prev_bass is None:
            return min(cands, key=lambda m: abs(m - 38))
        return min(cands, key=lambda m: abs(m - prev_bass))

    @staticmethod
    def _voice_chord_inner(chord: ChordSpec, lo: int = 44, hi: int = 62, count: int = 3) -> List[int]:
        pool = [m for m in range(lo, hi + 1) if (m % 12) in chord.core_pcs]
        if not pool:
            return [lo + 4, lo + 7]
        picked: List[int] = [pool[0]]
        for p in pool[1:]:
            if len(picked) >= count:
                break
            if p - picked[-1] >= 3:
                picked.append(p)
        return picked

    @classmethod
    def _realize_lh_bar(
        cls,
        texture: str,
        chord: ChordSpec,
        bar_t0: float,
        bar_dur: float,
        prev_bass: Optional[int],
        vel_base: int,
        beats_per_bar: int = 4,
    ) -> Tuple[List[Dict[str, Any]], int]:
        bass = cls._build_bass_register(chord.bass_pc, prev_bass)
        root_mid = quantize_to_pitch_set(bass + 12, [chord.root_pc], lo=40, hi=56)
        fifth = quantize_to_pitch_set(bass + 7, [(chord.root_pc + 7) % 12] + chord.core_pcs, lo=bass + 5, hi=55)
        third_pc = (chord.root_pc + (3 if "m" in chord.quality or "dim" in chord.quality else 4)) % 12
        tenth = quantize_to_pitch_set(bass + 15, [third_pc] + chord.core_pcs, lo=max(fifth + 3, 46), hi=60)
        if tenth <= fifth:
            tenth = quantize_to_pitch_set(fifth + 5, chord.core_pcs, lo=fifth + 3, hi=60)
        ninth = quantize_to_pitch_set(root_mid + 2, chord.core_pcs, lo=max(fifth + 2, 48), hi=61)
        beat = bar_dur / max(1, beats_per_bar)

        evs: List[Dict[str, Any]] = []
        v_bass = max(34, min(88, vel_base))
        v_inner = max(30, min(78, vel_base - 8))

        def _add(p: int, on_s: float, dur_s: float, v: int, role: str) -> None:
            evs.append(
                {
                    "pitch": max(24, min(61, int(p))),
                    "onset_s": round(on_s, 4),
                    "duration_s": round(max(0.18, dur_s), 4),
                    "velocity": max(28, min(92, int(v))),
                    "hand": "lh",
                    "voice": role,
                }
            )

        if texture == "chorale_voicing":
            # Warm overlapping 3-voice LH chorale on beat 1 and beat 3
            inner = cls._voice_chord_inner(chord, lo=max(42, bass + 7), hi=59, count=2)
            _add(bass, bar_t0 + 0.014, bar_dur * 0.94, v_bass, "lh_bass")
            for idx, ip in enumerate(inner):
                _add(ip, bar_t0 + 0.022 + idx * 0.014, bar_dur * 0.52, v_inner, "lh_inner")
            # Gentle inner motion / voicing shift on beat 3
            _add(bass + 12 if bass + 12 <= 54 else fifth, bar_t0 + beat * 2.0 + 0.014, beat * 1.90, v_inner, "lh_fifth")
            _add(tenth, bar_t0 + beat * 2.0 + 0.026, beat * 1.90, v_inner - 2, "lh_inner")

        elif texture == "countermelody_tenor":
            # Sustained bass pedal + singing tenor countermelody
            _add(bass, bar_t0 + 0.014, bar_dur * 0.94, v_bass, "lh_bass")
            _add(fifth, bar_t0 + 0.028, bar_dur * 0.86, v_inner - 4, "lh_fifth")
            tenor_pts = [
                (0.5 * beat, tenth, 1.35 * beat, v_inner + 3),
                (2.0 * beat, ninth, 1.15 * beat, v_inner),
                (3.0 * beat, fifth + 12 if fifth + 12 <= 60 else tenth, 0.96 * beat, v_inner - 2),
            ]
            for off, tp, td, tv in tenor_pts:
                _add(tp, bar_t0 + off, td, tv, "lh_inner")

        elif texture == "broken_chord_barcarolle":
            # Singing low-bass foundation + undulating 6/8 or 4/4 broken-chord wave
            _add(bass, bar_t0 + 0.014, bar_dur * 0.94, v_bass, "lh_bass")
            steps = [
                (0.5 * beat, fifth, 1.45 * beat, v_inner),
                (1.0 * beat, tenth, 1.45 * beat, v_inner + 2),
                (1.75 * beat, fifth, 1.10 * beat, v_inner - 3),
                (2.5 * beat, tenth, 1.35 * beat, v_inner + 1),
                (3.25 * beat, ninth, 0.72 * beat, v_inner - 2),
            ]
            for off, p, d, v in steps:
                _add(p, bar_t0 + off, d, v, "lh_arpeggio")

        elif texture == "alberti_lyrical":
            _add(bass, bar_t0 + 0.014, bar_dur * 0.94, v_bass, "lh_bass")
            steps = [
                (0.5 * beat, fifth, 1.1 * beat, v_inner - 2),
                (1.0 * beat, tenth, 1.1 * beat, v_inner + 1),
                (1.5 * beat, fifth, 0.9 * beat, v_inner - 3),
                (2.0 * beat, root_mid, 1.3 * beat, v_inner),
                (2.5 * beat, tenth, 1.1 * beat, v_inner + 1),
                (3.0 * beat, fifth, 0.95 * beat, v_inner - 2),
                (3.5 * beat, ninth, 0.48 * beat, v_inner - 3),
            ]
            for off, p, d, v in steps:
                _add(p, bar_t0 + off, d, v, "lh_alberti")

        else:  # "open_arpeggio_10th"
            _add(bass, bar_t0 + 0.014, bar_dur * 0.94, v_bass, "lh_bass")
            steps = [
                (0.5 * beat, fifth, 1.75 * beat, v_inner),
                (1.25 * beat, tenth, 1.65 * beat, v_inner + 3),
                (2.25 * beat, root_mid, 1.55 * beat, v_inner - 1),
                (3.0 * beat, ninth, 0.96 * beat, v_inner - 2),
            ]
            for off, p, d, v in steps:
                _add(p, bar_t0 + off, d, v, "lh_arpeggio")

        return evs, bass

    @classmethod
    def _enforce_cantabile_melody(
        cls,
        raw_targets: List[int],
        chord_seq: List[ChordSpec],
        tonality: str,
        act_num: int,
        lo: int = 62,
        hi: int = 88,
        suspension_indices: Optional[Set[int]] = None,
        all_strong_beats: bool = False,
    ) -> Tuple[List[int], List[int]]:
        """
        Shapes a melodic line so that:
        1. Strong positions (even indices, or all indices when all_strong_beats=True) land strictly
           on current chord's `core_pcs`, unless marked as an intentional suspension resolving downward.
        2. Large leaps (>= 5 semitones) recover by opposite stepwise motion (1..3 semitones).
        3. Avoids >=4 unidirectional stepwise runs & sterile zigzags.
        4. Establishes a single expressive Apex pitch near 60-75% of the phrase.
        Returns (pitches, resolved_suspension_indices).
        """
        n = len(raw_targets)
        if n == 0:
            return [], []
        susp_set = set(suspension_indices or set())
        resolved_susp: List[int] = []
        mel = list(raw_targets)

        def _is_strong(idx: int) -> bool:
            return True if all_strong_beats else (idx % 2 == 0)

        # First pass: snap to chord core_pcs on strong beats and tonality on weak beats
        for i in range(n):
            ch = chord_seq[i % len(chord_seq)]
            if i in susp_set and i + 1 < n and not all_strong_beats:
                anchor = quantize_to_pitch_set(mel[i], ch.core_pcs, lo=lo, hi=hi - 2)
                mel[i] = quantize_to_tonality(anchor + 2, tonality, lo=lo, hi=hi)
                mel[i + 1] = anchor
                resolved_susp.append(i)
            elif i > 0 and (i - 1) in resolved_susp:
                continue
            elif _is_strong(i):
                mel[i] = quantize_to_pitch_set(mel[i], ch.core_pcs, lo=lo, hi=hi)
            else:
                mel[i] = quantize_to_tonality(mel[i], tonality, lo=lo, hi=hi)

        # Second pass: Post-leap gap-fill & anti-monotonicity while keeping strong beats chord-locked
        for i in range(1, n - 1):
            leap = mel[i] - mel[i - 1]
            if abs(leap) >= 5 and i not in resolved_susp:
                nxt = mel[i + 1] - mel[i]
                if leap * nxt >= 0 or abs(nxt) > 4 or nxt == 0:
                    ch_nxt = chord_seq[(i + 1) % len(chord_seq)]
                    step_t = mel[i] + (-2 if leap > 0 else 2)
                    if _is_strong(i + 1):
                        cand_nxt = quantize_to_pitch_set(step_t, ch_nxt.core_pcs, lo=lo, hi=hi)
                        # Only accept if opposite direction and <= 4 semitones
                        if (cand_nxt - mel[i]) * leap < 0 and 1 <= abs(cand_nxt - mel[i]) <= 4:
                            mel[i + 1] = cand_nxt
                        else:
                            mel[i + 1] = quantize_to_pitch_set(mel[i] + (-3 if leap > 0 else 3), ch_nxt.core_pcs, lo=lo, hi=hi)
                    else:
                        mel[i + 1] = quantize_to_tonality(step_t, tonality, lo=lo, hi=hi)

        # Eliminate consecutive repeated stagnation except planned suspensions
        for i in range(1, n):
            if mel[i] == mel[i - 1] and i not in resolved_susp:
                ch = chord_seq[i % len(chord_seq)]
                cand = mel[i] + (3 if i % 2 == 1 else -3)
                if _is_strong(i):
                    mel[i] = quantize_to_pitch_set(cand, ch.core_pcs, lo=lo, hi=hi)
                else:
                    mel[i] = quantize_to_tonality(cand, tonality, lo=lo, hi=hi)
                if mel[i] == mel[i - 1]:
                    mel[i] = quantize_to_pitch_set(mel[i] + 5, ch.core_pcs, lo=lo, hi=hi)

        # Break >=4 unidirectional stepwise run
        run_dir = 0
        run_len = 1
        for i in range(1, n):
            d = mel[i] - mel[i - 1]
            cdir = 1 if d > 0 else (-1 if d < 0 else 0)
            if cdir != 0 and cdir == run_dir and 1 <= abs(d) <= 2:
                run_len += 1
                if run_len >= 4:
                    ch = chord_seq[i % len(chord_seq)]
                    nudged = mel[i - 1] - cdir * 4
                    mel[i] = quantize_to_pitch_set(nudged, ch.core_pcs, lo=lo, hi=hi) if _is_strong(i) else quantize_to_tonality(nudged, tonality, lo=lo, hi=hi)
                    run_dir = -cdir
                    run_len = 1
            else:
                run_dir = cdir
                run_len = 1

        # Guarantee single clear Apex pitch near 55-75% of the phrase
        if n >= 6:
            apex_idx = min(n - 2, max(2, int(round(n * 0.65))))
            if not all_strong_beats and apex_idx % 2 != 0 and apex_idx + 1 < n - 1:
                apex_idx += 1
            ch_ap = chord_seq[apex_idx % len(chord_seq)]
            cur_max = max(mel)
            target_ap = max(cur_max + 2, 77 if act_num <= 2 else (83 if act_num == 3 else 75))
            mel[apex_idx] = quantize_to_pitch_set(min(hi, target_ap), ch_ap.core_pcs, lo=lo + 6, hi=hi)
            for j in range(n):
                if j != apex_idx and mel[j] >= mel[apex_idx]:
                    ch_j = chord_seq[j % len(chord_seq)]
                    mel[j] = quantize_to_pitch_set(mel[apex_idx] - 3, ch_j.core_pcs, lo=lo, hi=mel[apex_idx] - 1)

        return mel, resolved_susp

    @classmethod
    def resolve_vertical_clashes(
        cls,
        notes: List[Dict[str, Any]],
        chord_timeline: List[Dict[str, Any]],
        suspension_windows: Optional[List[Tuple[float, float, int]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Sweeps all sounding vertical intervals in 50ms slices and re-voices offending
        inner/accompaniment notes to clean consonant chord tones so accidental minor-2nd,
        major-7th, or muddy low-register clashes never sound.
        """
        if not notes or not chord_timeline:
            return notes
        susp_wins = suspension_windows or []
        out = [dict(n) for n in notes]

        parsed_segs: List[Tuple[float, ChordSpec]] = []
        for seg in chord_timeline:
            sp = seg.get("_spec")
            if not isinstance(sp, ChordSpec):
                sp = parse_chord_symbol(str(seg.get("chord", "Dm9")))
            parsed_segs.append((float(seg.get("start_s", 0.0)), sp))

        def _chord_at(t_s: float) -> ChordSpec:
            chosen = parsed_segs[0][1]
            for st_s, sp in parsed_segs:
                if st_s <= t_s + 1e-4:
                    chosen = sp
                else:
                    break
            return chosen

        # Identify melody & human priority: resolve piano accompaniment harmony before melody/cello
        priority_map = {
            "rh_harmony": 0,
            "lh_inner": 1,
            "lh_alberti": 1,
            "lh_arpeggio": 2,
            "lh_fifth": 3,
            "lh_bass": 5,
            "cello_motif": 6,
            "human_piano": 6,
            "rh_melody": 7,
            "melody": 7,
        }

        end_t = max(float(n["onset_s"]) + float(n["duration_s"]) for n in out)
        t_cur = 0.0
        while t_cur < end_t:
            ch = _chord_at(t_cur)
            active_idxs = [
                idx for idx, n in enumerate(out)
                if float(n["onset_s"]) <= t_cur + 0.015 < (float(n["onset_s"]) + float(n["duration_s"]))
            ]
            if len(active_idxs) >= 2:
                # Sort pairs so highest-priority pitches anchor and lowest-priority adjusts
                for a_pos in range(len(active_idxs)):
                    for b_pos in range(a_pos + 1, len(active_idxs)):
                        ia = active_idxs[a_pos]
                        ib = active_idxs[b_pos]
                        pa = int(out[ia]["pitch"])
                        pb = int(out[ib]["pitch"])
                        if pa == pb:
                            continue
                        p_lo, p_hi = (pa, pb) if pa < pb else (pb, pa)
                        diff = p_hi - p_lo
                        iv = diff % 12
                        bad = False
                        if iv in (1, 11):
                            if not _is_justified_tension(p_lo, p_hi, t_cur, ch, susp_wins):
                                bad = True
                        elif iv == 6:
                            if not _is_justified_tension(p_lo, p_hi, t_cur, ch, susp_wins):
                                bad = True
                        elif p_lo < 52 and 0 < diff < 5:
                            bad = True
                        elif iv == 2 and diff <= 2:
                            # Adjacent major-2nd rubbing in same octave
                            if not _is_justified_tension(p_lo, p_hi, t_cur, ch, susp_wins):
                                bad = True

                        if bad:
                            # Move lower-priority note (never bass root or primary human/melody unless tie)
                            pr_a = priority_map.get(str(out[ia].get("voice", "")).lower(), 2)
                            pr_b = priority_map.get(str(out[ib].get("voice", "")).lower(), 2)
                            victim = ia if pr_a <= pr_b else ib
                            keeper = ib if victim == ia else ia
                            p_keep = int(out[keeper]["pitch"])
                            p_vic = int(out[victim]["pitch"])
                            lo_r = 34 if str(out[victim].get("hand", "")) == "lh" else 58
                            hi_r = 60 if str(out[victim].get("hand", "")) == "lh" else 84
                            others = [int(out[k]["pitch"]) for k in active_idxs if k != victim]
                            best_p = p_vic
                            best_cost = 9999
                            for cand in range(lo_r, hi_r + 1):
                                if (cand % 12) not in ch.core_pcs:
                                    continue
                                clash_cnt = 0
                                for op in others:
                                    d_o = abs(cand - op)
                                    iv_o = d_o % 12
                                    if iv_o in (1, 6, 11) or (d_o <= 2 and d_o > 0) or (min(cand, op) < 52 and 0 < d_o < 5):
                                        clash_cnt += 1
                                cost = clash_cnt * 100 + abs(cand - p_vic)
                                if abs(cand - p_keep) < 3:
                                    cost += 40
                                if cost < best_cost:
                                    best_cost = cost
                                    best_p = cand
                            out[victim]["pitch"] = best_p
            t_cur += 0.05

        return out

    @staticmethod
    def build_chord_synced_pedals(
        chord_timeline: List[Dict[str, Any]],
        acts_meta: List[Dict[str, Any]],
        total_dur_s: float,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Generates clean chord-boundary repedaling (CC#64 drops to 0 before each chord change
        and rises smoothly 55ms after) plus continuous expressive Una Corda (CC#67).
        """
        cc_events: List[Dict[str, Any]] = []
        cc64_curve: List[Dict[str, Any]] = []
        cc67_curve: List[Dict[str, Any]] = []

        def _act_at(t_s: float) -> int:
            for am in acts_meta:
                if float(am["start_s"]) <= t_s + 0.01 < float(am["end_s"]):
                    return int(am["act"])
            return 4 if t_s > total_dur_s * 0.7 else 1

        # CC#64 synchronized with every chord boundary
        for idx, seg in enumerate(chord_timeline):
            t0 = float(seg["start_s"])
            act_n = _act_at(t0)
            depth64 = {1: 72, 2: 84, 3: 98, 4: 68}.get(act_n, 76)
            if idx == 0:
                pts64 = [(0.0, 0), (0.06, depth64)]
            else:
                pts64 = [
                    (max(0.01, round(t0 - 0.032, 3)), 0),
                    (round(t0 + 0.055, 3), depth64),
                ]
            for pt_t, pt_v in pts64:
                cc_events.append({"pitch": 0, "onset_s": pt_t, "control": 64, "value": pt_v, "voice": "pedal"})
                cc64_curve.append({"t": pt_t, "val": pt_v})

        final_release = round(max(0.5, total_dur_s - 0.12), 3)
        cc_events.append({"pitch": 0, "onset_s": final_release, "control": 64, "value": 0, "voice": "pedal"})
        cc64_curve.append({"t": final_release, "val": 0})

        # CC#67 smooth continuous expressiveness across acts (Act 4 highest intimacy)
        for am in acts_meta:
            t_s = float(am["start_s"])
            t_e = float(am["end_s"])
            t_mid = round((t_s + t_e) * 0.5, 3)
            act_n = int(am["act"])
            v_start, v_peak = {
                1: (44, 66),
                2: (32, 48),
                3: (10, 22),
                4: (82, 114),
            }.get(act_n, (36, 60))
            for pt_t, pt_v in ((round(t_s, 3), v_start), (t_mid, v_peak)):
                cc_events.append({"pitch": 0, "onset_s": pt_t, "control": 67, "value": pt_v, "voice": "pedal"})
                cc67_curve.append({"t": pt_t, "val": pt_v})

        cc_events.append({"pitch": 0, "onset_s": final_release, "control": 67, "value": 0, "voice": "pedal"})
        cc67_curve.append({"t": final_release, "val": 0})

        cc64_curve.sort(key=lambda x: x["t"])
        cc67_curve.sort(key=lambda x: x["t"])
        return cc_events, cc64_curve, cc67_curve

    @classmethod
    def realize_4act_piece(
        cls,
        design: Dict[str, Any],
        mode: str = "cello",
        corpus_laws: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Realizes a complete 16-bar 4-Act polyphonic piece (approx. 34-48s) from an LLM or
        seed design specification into clean MIDI notes, chord timeline, and dual-pedal curves.
        """
        tonality = str(design.get("tonal_center") or "D_minor")
        if tonality not in TONALITY_SPECS:
            tonality = "D_minor"
        t_spec = TONALITY_SPECS[tonality]
        tonic_pc = int(t_spec["tonic_pc"])
        bpm = max(58.0, min(104.0, float(design.get("bpm", 76.0))))
        beats_per_bar = 4
        sec_per_beat = 60.0 / bpm
        bar_dur_base = sec_per_beat * beats_per_bar

        act_specs_in = list(design.get("acts") or [])
        default_progs = build_functional_4act_chords(tonality, variant=int(design.get("prog_variant", 0)))
        default_textures = {
            1: str(design.get("lh_texture_act1") or "open_arpeggio_10th"),
            2: str(design.get("lh_texture_act2") or "broken_chord_barcarolle"),
            3: str(design.get("lh_texture_act3") or "countermelody_tenor"),
            4: str(design.get("lh_texture_act4") or "chorale_voicing"),
        }
        act_names_ja = {
            1: "第1幕: 種子提示と問いかけ",
            2: "第2幕: 対話的発展と転調",
            3: "第3幕: 多声的頂点と高揚",
            4: "第4幕: ウナ・コルダ回想と終止",
        }

        all_notes: List[Dict[str, Any]] = []
        chord_timeline: List[Dict[str, Any]] = []
        acts_meta: List[Dict[str, Any]] = []
        suspension_windows: List[Tuple[float, float, int]] = []

        cursor_s = 0.0
        prev_bass: Optional[int] = None
        prev_rh_end = quantize_to_tonality(69 + (tonic_pc - 2), tonality, 65, 76)
        seed_deg = list(design.get("seed_degrees") or [0, 2, 4, 3, 1, 2, 5, 4])

        for act_num in range(1, 5):
            act_in = act_specs_in[act_num - 1] if act_num - 1 < len(act_specs_in) else {}
            chord_syms = list(act_in.get("chords") or default_progs[act_num])
            while len(chord_syms) < 4:
                chord_syms.extend(default_progs[act_num])
            chord_syms = chord_syms[:4]
            parsed_chords = [parse_chord_symbol(s, tonality) for s in chord_syms]

            lh_tex = str(act_in.get("lh_texture") or default_textures[act_num])
            if lh_tex not in LH_TEXTURES:
                lh_tex = default_textures[act_num]

            base_dyn = {1: 62, 2: 70, 3: 82, 4: 54}[act_num]
            vel_act = int(act_in.get("dynamic_vel", base_dyn))
            vel_act = max(46, min(92, vel_act))

            act_start = cursor_s
            # 4 bars per Act -> 8 melodic anchors (2 per bar: beat 1 & beat 3) + lyrical passing notes
            raw_mel_targets: List[int] = []
            contour_offs = list(act_in.get("melody_offsets") or [])
            for b in range(4):
                for half_b in range(2):
                    idx_m = b * 2 + half_b
                    off = int(contour_offs[idx_m]) if idx_m < len(contour_offs) else seed_deg[idx_m % len(seed_deg)]
                    if act_num == 3:
                        off += 3 if idx_m in (4, 5) else 1
                    elif act_num == 4:
                        off = max(-2, off - 1)
                    base_p = prev_rh_end if idx_m == 0 else (67 + (tonic_pc - 2))
                    raw_mel_targets.append(max(62, min(88, base_p + off)))

            # Assign chord for each of the 8 strong-beat/half-bar steps
            chord_per_step = [parsed_chords[i // 2] for i in range(8)]
            mel_pitches, resolved_susp = cls._enforce_cantabile_melody(
                raw_mel_targets,
                chord_per_step,
                tonality,
                act_num,
                lo=62 if act_num != 3 else 65,
                hi=88 if act_num == 3 else 82,
                suspension_indices=set(),
                all_strong_beats=True,
            )

            # Force Act 1 open question ending (5th or 2nd) and Act 4 final tonic ending
            if act_num == 1:
                mel_pitches[-1] = quantize_to_pitch_set(
                    60 + (tonic_pc + 7) % 12 + 12,
                    [(tonic_pc + 7) % 12, (tonic_pc + 2) % 12] + parsed_chords[-1].core_pcs,
                    lo=65,
                    hi=79,
                )
            elif act_num == 4:
                mel_pitches[-1] = quantize_to_pitch_set(
                    60 + tonic_pc + 12,
                    [tonic_pc] + parsed_chords[-1].core_pcs,
                    lo=64,
                    hi=78,
                )

            for b in range(4):
                ch = parsed_chords[b]
                # Gentle agogic ritardando on bar 4 of each Act
                rit_mult = 1.12 if (b == 3 and act_num == 4) else (1.06 if b == 3 else 1.0)
                b_dur = bar_dur_base * rit_mult
                beat = b_dur / beats_per_bar
                b_t0 = cursor_s

                chord_timeline.append(
                    {
                        "bar": (act_num - 1) * 4 + b + 1,
                        "act": act_num,
                        "start_s": round(b_t0, 4),
                        "end_s": round(b_t0 + b_dur, 4),
                        "chord": ch.symbol,
                        "roman": ch.roman,
                        "function_ja": ch.function_ja,
                        "core_pcs": list(ch.core_pcs),
                        "_spec": ch,
                    }
                )

                # 1. Left-Hand multi-voice accompaniment
                lh_evs, prev_bass = cls._realize_lh_bar(
                    texture=lh_tex,
                    chord=ch,
                    bar_t0=b_t0,
                    bar_dur=b_dur,
                    prev_bass=prev_bass,
                    vel_base=max(38, vel_act - 8),
                    beats_per_bar=beats_per_bar,
                )
                all_notes.extend(lh_evs)

                # 2. Right-Hand singing melody (beat 1 & beat 3 chord pillars + expressive neighbor/resolution on beat 2 & 4)
                p1 = mel_pitches[b * 2]
                p3 = mel_pitches[b * 2 + 1]
                # If p1->p3 leaped >= 5, make beat 2 an explicit opposite-direction step recovery, or bridge smoothly
                if abs(p3 - p1) >= 5:
                    p2_raw = p1 + (-2 if p3 < p1 else 2)
                else:
                    p2_raw = int(round((p1 + p3) * 0.5))
                    if p2_raw == p1:
                        p2_raw = p1 + (2 if p3 >= p1 else -2)
                p2 = quantize_to_tonality(p2_raw, tonality, lo=60, hi=88)

                # Closing bar of Act holds final cadence note smoothly
                is_act_end_bar = (b == 3)
                rh_pattern = [
                    (0.0, p1, beat * 1.42, vel_act + (6 if b == 2 else 0), b * 2 in resolved_susp),
                    (beat * 1.5, p2, beat * 0.52, vel_act - 4, False),
                    (beat * 2.0, p3, beat * (1.88 if is_act_end_bar else 1.38), vel_act + (10 if (act_num == 3 and b == 2) else 2), (b * 2 + 1) in resolved_susp),
                ]
                if not is_act_end_bar:
                    nxt_p1 = mel_pitches[(b + 1) * 2]
                    # If p3 jumps >= 5 toward next bar's p1, place opposite gap-fill step; else smooth passing step
                    if abs(nxt_p1 - p3) >= 5:
                        p4_raw = p3 + (-2 if nxt_p1 > p3 else 2)
                    else:
                        p4_raw = p3 + (2 if nxt_p1 > p3 else -2)
                    p4 = quantize_to_tonality(p4_raw, tonality, lo=62, hi=86)
                    if p4 != p3:
                        rh_pattern.append((beat * 3.5, p4, beat * 0.48, vel_act - 6, False))

                for off_s, mp, dur_m, vm, is_susp in rh_pattern:
                    on_m = b_t0 + off_s
                    if is_susp:
                        suspension_windows.append((on_m, on_m + dur_m + 0.35, mp))
                    all_notes.append(
                        {
                            "pitch": max(58, min(92, int(mp))),
                            "onset_s": round(on_m, 4),
                            "duration_s": round(max(0.18, dur_m), 4),
                            "velocity": max(34, min(102, int(vm))),
                            "hand": "rh",
                            "voice": "rh_melody",
                        }
                    )

                    # Vertical RH inner harmony (3rd or 6th below melody on beat 1 & 3)
                    if abs(off_s) < 0.01 or abs(off_s - beat * 2.0) < 0.01:
                        harm_cand = mp - (4 if int(off_s) % 2 == 0 else 8)
                        harm_p = quantize_to_pitch_set(harm_cand, ch.core_pcs, lo=56, hi=mp - 3)
                        if harm_p != mp and 3 <= (mp - harm_p) <= 10:
                            all_notes.append(
                                {
                                    "pitch": harm_p,
                                    "onset_s": round(on_m + 0.015, 4),  # Melody Lead +15ms
                                    "duration_s": round(max(0.16, dur_m * 0.90), 4),
                                    "velocity": max(28, min(86, int(vm - 14))),
                                    "hand": "rh",
                                    "voice": "rh_harmony",
                                }
                            )
                        # Act 3 climax adds lush 3-voice RH chordal fullness
                        if act_num == 3 and abs(off_s) < 0.01:
                            harm2 = quantize_to_pitch_set(mp - 9, ch.core_pcs, lo=53, hi=harm_p - 3)
                            if harm2 < harm_p - 2:
                                all_notes.append(
                                    {
                                        "pitch": harm2,
                                        "onset_s": round(on_m + 0.026, 4),
                                        "duration_s": round(max(0.16, dur_m * 0.88), 4),
                                        "velocity": max(28, min(82, int(vm - 18))),
                                        "hand": "rh",
                                        "voice": "rh_harmony",
                                    }
                                )

                # 3. Expressive Human Counterpart (Cello in Mode B, Piano I in Mode A) locked to same chords
                # Cello sings cantabile barcarolle/chamber lines (MIDI 46..67) on bars 0,1,2,3 (especially rich in Act 1, 3, 4)
                if mode == "cello":
                    c_lo, c_hi = (46, 65) if act_num != 3 else (50, 69)
                    c1 = quantize_to_pitch_set(55 + (tonic_pc - 2), ch.core_pcs, lo=c_lo, hi=c_hi)
                    c2 = quantize_to_pitch_set(c1 + (3 if b % 2 == 0 else -3), ch.core_pcs, lo=c_lo, hi=c_hi)
                    if c2 == c1:
                        c2 = quantize_to_pitch_set(c1 + 4, ch.core_pcs, lo=c_lo, hi=c_hi)
                    c_vel = max(46, min(92, vel_act + (4 if act_num == 3 else -4)))
                    all_notes.append(
                        {
                            "pitch": c1,
                            "onset_s": round(b_t0 + 0.020, 4),
                            "duration_s": round(beat * 1.85, 4),
                            "velocity": c_vel,
                            "hand": "human",
                            "voice": "cello_motif",
                        }
                    )
                    all_notes.append(
                        {
                            "pitch": c2,
                            "onset_s": round(b_t0 + beat * 2.0 + 0.020, 4),
                            "duration_s": round(beat * (1.85 if is_act_end_bar else 1.75), 4),
                            "velocity": max(42, c_vel - 3),
                            "hand": "human",
                            "voice": "cello_motif",
                        }
                    )
                else:
                    # Mode A: Human Piano I upper/mid lyrical counter-dialogue (MIDI 60..79)
                    h_p1 = quantize_to_pitch_set(p1 - 5 if p1 >= 69 else p1 + 4, ch.core_pcs, lo=58, hi=79)
                    h_p2 = quantize_to_pitch_set(p3 - 4 if p3 >= 68 else p3 + 3, ch.core_pcs, lo=58, hi=79)
                    h_vel = max(42, min(88, vel_act - 4))
                    all_notes.append(
                        {
                            "pitch": h_p1,
                            "onset_s": round(b_t0 + beat * 0.5, 4),
                            "duration_s": round(beat * 1.40, 4),
                            "velocity": h_vel,
                            "hand": "human",
                            "voice": "human_piano",
                        }
                    )
                    all_notes.append(
                        {
                            "pitch": h_p2,
                            "onset_s": round(b_t0 + beat * 2.5, 4),
                            "duration_s": round(beat * 1.35, 4),
                            "velocity": max(40, h_vel - 2),
                            "hand": "human",
                            "voice": "human_piano",
                        }
                    )

                cursor_s += b_dur

            prev_rh_end = mel_pitches[-1]
            acts_meta.append(
                {
                    "act": act_num,
                    "label_ja": act_names_ja[act_num],
                    "lh_texture": lh_tex,
                    "start_s": round(act_start, 4),
                    "end_s": round(cursor_s, 4),
                    "chords": chord_syms,
                }
            )
            # Subtle breath pause at act boundary while bass/pedal resonates cleanly
            cursor_s += 0.08 if act_num < 4 else 0.0

        # Ensure smooth voice-leading & gap-fill across the complete realized rh_melody line (including act boundaries)
        mel_seq_notes = [n for n in all_notes if n.get("voice") == "rh_melody"]
        mel_seq_notes.sort(key=lambda x: float(x["onset_s"]))

        def _chord_for_time(t_q: float) -> ChordSpec:
            chosen = chord_timeline[0]["_spec"]
            for seg in chord_timeline:
                if float(seg["start_s"]) <= t_q + 1e-4:
                    chosen = seg["_spec"]
                else:
                    break
            return chosen

        # Pass A: eliminate >=4 unidirectional stepwise runs by redirecting weak passing tones
        for _pass in range(2):
            run_dir = 0
            run_len = 1
            for k in range(1, len(mel_seq_notes)):
                p_prev = int(mel_seq_notes[k - 1]["pitch"])
                p_cur = int(mel_seq_notes[k]["pitch"])
                d = p_cur - p_prev
                cdir = 1 if d > 0 else (-1 if d < 0 else 0)
                if cdir != 0 and cdir == run_dir and 1 <= abs(d) <= 2:
                    run_len += 1
                    if run_len >= 4:
                        ch_k = _chord_for_time(float(mel_seq_notes[k]["onset_s"]))
                        alt = quantize_to_pitch_set(p_prev - cdir * 3, ch_k.core_pcs, lo=62, hi=88)
                        if alt != p_prev:
                            mel_seq_notes[k]["pitch"] = alt
                            run_dir = -cdir
                            run_len = 1
                else:
                    run_dir = cdir
                    run_len = 1

            # Pass B: enforce post-leap gap-fill across bar/act boundaries
            for k in range(1, len(mel_seq_notes) - 1):
                p_prev = int(mel_seq_notes[k - 1]["pitch"])
                p_cur = int(mel_seq_notes[k]["pitch"])
                p_nxt = int(mel_seq_notes[k + 1]["pitch"])
                lv = p_cur - p_prev
                nv = p_nxt - p_cur
                if abs(lv) >= 5 and (lv * nv >= 0 or abs(nv) > 4 or nv == 0):
                    ch_nxt = _chord_for_time(float(mel_seq_notes[k + 1]["onset_s"]))
                    step_dir = -2 if lv > 0 else 2
                    cand = quantize_to_pitch_set(p_cur + step_dir, ch_nxt.core_pcs, lo=62, hi=88)
                    if (cand - p_cur) * lv < 0 and 1 <= abs(cand - p_cur) <= 4:
                        mel_seq_notes[k + 1]["pitch"] = cand
                    else:
                        # Soften excessive leap lv itself if k is not the final note of an act
                        cand_cur = quantize_to_pitch_set(p_prev + (3 if lv > 0 else -3), _chord_for_time(float(mel_seq_notes[k]["onset_s"])).core_pcs, lo=62, hi=88)
                        if cand_cur != p_prev:
                            mel_seq_notes[k]["pitch"] = cand_cur

        # Final sustaining tonic bloom at coda end (smooth voice-leading from Act 4 end)
        final_ch = chord_timeline[-1]["_spec"]
        coda_t = cursor_s - 0.15
        final_bass = cls._build_bass_register(final_ch.root_pc, prev_bass)
        final_fifth = quantize_to_pitch_set(final_bass + 7, [(final_ch.root_pc + 7) % 12] + final_ch.core_pcs, lo=final_bass + 5, hi=55)
        final_tenth = quantize_to_pitch_set(60 + final_ch.root_pc + (3 if t_spec["is_minor"] else 4), final_ch.core_pcs, lo=60, hi=72)
        last_mel_p = int(mel_seq_notes[-1]["pitch"]) if mel_seq_notes else (72 + final_ch.root_pc)
        final_soprano = quantize_to_pitch_set(last_mel_p, [final_ch.root_pc] + final_ch.core_pcs, lo=64, hi=81)
        if abs(final_soprano - last_mel_p) >= 5:
            final_soprano = quantize_to_pitch_set(last_mel_p - 2, final_ch.core_pcs, lo=64, hi=81)
        for idx, (fp, fh, fv) in enumerate(
            [
                (final_bass, "lh", "lh_bass"),
                (final_fifth, "lh", "lh_fifth"),
                (final_tenth, "rh", "rh_harmony"),
                (final_soprano, "rh", "rh_melody"),
            ]
        ):
            all_notes.append(
                {
                    "pitch": fp,
                    "onset_s": round(coda_t + idx * 0.032, 4),
                    "duration_s": 2.40,
                    "velocity": 50 if idx < 3 else 56,
                    "hand": fh,
                    "voice": fv,
                }
            )
        total_dur_s = round(coda_t + 2.60, 2)

        # Resolve any accidental vertical clashes across Human + LH + RH
        clean_notes = cls.resolve_vertical_clashes(all_notes, chord_timeline, suspension_windows)

        # Build chord-boundary synced CC#64 & continuous CC#67
        cc_events, cc64_curve, cc67_curve = cls.build_chord_synced_pedals(chord_timeline, acts_meta, total_dur_s)

        # Separate & run Disklavier Preflight check on piano notes + CC#64 (CC#67 is soft pedal for studio/synth)
        human_notes = [dict(n) for n in clean_notes if n.get("hand") == "human"]
        piano_notes_for_pf = []
        for n in clean_notes:
            if n.get("hand") != "human":
                ev = dict(n)
                ev["note"] = int(ev["pitch"])
                ev["time_offset_s"] = float(ev["onset_s"])
                piano_notes_for_pf.append(ev)
        cc64_for_pf = []
        for c in cc_events:
            if c.get("control") == 64:
                ce = dict(c)
                ce["note"] = 0
                ce["time_offset_s"] = float(ce["onset_s"])
                ce["pedal_cc64"] = int(ce["value"])
                cc64_for_pf.append(ce)

        repaired_pf, preflight_report = LocalPreflightChecker.audit_and_repair(
            piano_notes_for_pf + cc64_for_pf
        )
        repaired_piano_notes = [dict(e) for e in repaired_pf if int(e.get("note", e.get("pitch", 0)) or 0) > 0]
        for ev in repaired_piano_notes:
            ev["pitch"] = int(ev.get("note", ev.get("pitch", 60)))
            ev["onset_s"] = float(ev.get("time_offset_s", ev.get("onset_s", 0.0)))

        combined_notes = sorted(
            human_notes + repaired_piano_notes,
            key=lambda x: (float(x.get("onset_s", 0.0)), int(x.get("pitch", 0))),
        )
        # Strip private _spec before JSON serialization
        public_timeline = [
            {k: v for k, v in seg.items() if k != "_spec"}
            for seg in chord_timeline
        ]

        return {
            "tonality": tonality,
            "tonality_ja": str(t_spec["ja"]),
            "bpm": round(bpm, 1),
            "total_duration_s": total_dur_s,
            "notes": combined_notes,
            "human_events": human_notes,
            "piano_events": sorted(
                repaired_piano_notes + cc_events,
                key=lambda x: float(x.get("onset_s", x.get("time_offset_s", 0.0))),
            ),
            "piano_preflight_events": repaired_pf,
            "chord_timeline": public_timeline,
            "acts": acts_meta,
            "cc64_curve": cc64_curve,
            "cc67_curve": cc67_curve,
            "suspension_windows": [[round(a, 3), round(b, 3), int(p)] for a, b, p in suspension_windows],
            "preflight_report": preflight_report,
        }

    @classmethod
    def realize_2bar_candidate(
        cls,
        human_notes: List[Dict[str, Any]],
        tonality: str,
        chord_symbols: List[str],
        act_number: int,
        phrase_function: str,
        strategy: str,
        bridge_pitch: int,
        target_apex: int,
        lh_idiom: str,
        blueprint_offsets: List[int],
        agogic_ratios: List[float],
        velocity_curve: List[float],
        pedal_strategy: str = "half_clear",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Realizes a 2-bar (approx. 3.2-3.8s) live dialogue candidate (A/B/C) with strict
        chord-tone landing, multi-voice LH bed, clash clearance, and chord-synced dual pedals.
        """
        if tonality not in TONALITY_SPECS:
            tonality = "D_minor"
        t_spec = TONALITY_SPECS[tonality]
        tonic_pc = int(t_spec["tonic_pc"])
        syms = (list(chord_symbols or []) + ["Dm9", "A7sus4"])[:2]
        ch1 = parse_chord_symbol(syms[0], tonality)
        ch2 = parse_chord_symbol(syms[1], tonality)

        bar_dur = 1.65 if act_number != 4 else 1.85
        chord_timeline = [
            {"bar": 1, "act": act_number, "start_s": 0.0, "end_s": bar_dur, "chord": ch1.symbol, "roman": ch1.roman, "_spec": ch1},
            {"bar": 2, "act": act_number, "start_s": bar_dur, "end_s": bar_dur * 2.0, "chord": ch2.symbol, "roman": ch2.roman, "_spec": ch2},
        ]

        tex_map = {
            "arpeggio_root_5th_10th": "open_arpeggio_10th",
            "pedal_point_inner_voice": "countermelody_tenor",
            "syncopated_offbeat_harmony": "broken_chord_barcarolle",
            "una_corda_choral_recall": "chorale_voicing",
        }
        lh_tex = tex_map.get(lh_idiom, "open_arpeggio_10th")
        if strategy == "suspension_inner_voice":
            lh_tex = "countermelody_tenor"
        elif strategy == "overtone_clear_high_spread":
            lh_tex = "chorale_voicing" if act_number == 4 else "broken_chord_barcarolle"

        vel_base = 76 if act_number == 3 else (58 if act_number == 4 else 68)
        lh1, prev_b = cls._realize_lh_bar(lh_tex, ch1, 0.0, bar_dur, None, vel_base - 8)
        lh2, _ = cls._realize_lh_bar(lh_tex, ch2, bar_dur, bar_dur, prev_b, vel_base - 8)

        raw_mel = [
            quantize_to_tonality(bridge_pitch + int(blueprint_offsets[i % len(blueprint_offsets)]), tonality, 62, 89)
            for i in range(8)
        ]
        if strategy == "overtone_clear_high_spread":
            raw_mel = [min(90, p + 4) for p in raw_mel]
        chord_steps = [ch1, ch1, ch1, ch1, ch2, ch2, ch2, ch2]
        susp_idxs = {2, 5} if strategy == "suspension_inner_voice" else {4}
        mel, resolved_susp = cls._enforce_cantabile_melody(
            raw_mel,
            chord_steps,
            tonality,
            act_number,
            lo=62,
            hi=89 if act_number == 3 else 84,
            suspension_indices=susp_idxs,
        )

        if phrase_function == "question_half_cadence":
            mel[-1] = quantize_to_pitch_set(
                72 + (tonic_pc + 7) % 12,
                [(tonic_pc + 7) % 12, (tonic_pc + 2) % 12] + ch2.core_pcs,
                lo=65,
                hi=82,
            )
        elif phrase_function == "recapitulation_resolution":
            mel[-1] = quantize_to_pitch_set(72 + tonic_pc, [tonic_pc] + ch2.core_pcs, lo=64, hi=80)

        rh_evs: List[Dict[str, Any]] = []
        susp_wins: List[Tuple[float, float, int]] = []
        step_beats = [0.44, 0.30, 0.38, 0.48, 0.38, 0.50, 0.34, 0.68]
        t = 0.0
        for i in range(8):
            dt = step_beats[i] * float(agogic_ratios[i % len(agogic_ratios)])
            dt = max(0.22, min(0.86, dt))
            if i == 4:
                t = max(t, bar_dur)
            ch = ch1 if i < 4 else ch2
            p = mel[i]
            v = max(44, min(98, int(round(vel_base - 6 + float(velocity_curve[i % len(velocity_curve)]) * 24 + (8 if i == 5 else 0)))))
            dur = dt * (0.82 if pedal_strategy == "dry" else 1.08)
            if i == 7:
                dur = max(0.60, dur * 1.25)
            if i in resolved_susp:
                susp_wins.append((t, t + dur + 0.3, p))
            rh_evs.append(
                {
                    "pitch": p,
                    "onset_s": round(t, 4),
                    "duration_s": round(dur, 4),
                    "velocity": v,
                    "hand": "rh",
                    "voice": "rh_melody",
                }
            )
            if i in (0, 2, 4, 5, 7):
                hp = quantize_to_pitch_set(p - (4 if i in (0, 5) else 5), ch.core_pcs, lo=59, hi=p - 3)
                if p - hp >= 3:
                    rh_evs.append(
                        {
                            "pitch": hp,
                            "onset_s": round(t + 0.015, 4),
                            "duration_s": round(dur * 0.90, 4),
                            "velocity": max(34, v - 14),
                            "hand": "rh",
                            "voice": "rh_harmony",
                        }
                    )
            t += dt

        total_dur = max(bar_dur * 2.0, t + 0.25)
        acts_meta = [{"act": act_number, "start_s": 0.0, "end_s": total_dur}]
        all_notes = lh1 + lh2 + rh_evs

        # Clear close spectral proximity (<=2st anywhere, <=4st in 40..66) against human pitches using chord tones
        h_pitches = [
            int(n.get("pitch", n.get("note", 0)) or 0)
            for n in (human_notes or [])
            if int(n.get("pitch", n.get("note", 0)) or 0) > 0
        ]
        if h_pitches:
            def _seg_at(on_t: float) -> ChordSpec:
                return ch1 if on_t < bar_dur - 0.01 else ch2

            for ev in all_notes:
                v_role = str(ev.get("voice", "")).lower()
                if v_role == "lh_bass":
                    continue
                p_cur = int(ev["pitch"])
                if any(abs(p_cur - hp) <= 2 or (40 <= p_cur <= 66 and abs(p_cur - hp) <= 4) for hp in h_pitches):
                    sp_c = _seg_at(float(ev["onset_s"]))
                    is_lh = str(ev.get("hand", "")).lower() == "lh"
                    lo_r, hi_r = (32, 57) if is_lh else (64, 88)
                    pool = [m for m in range(lo_r, hi_r + 1) if (m % 12) in sp_c.core_pcs]
                    clear_pool = [
                        m for m in pool
                        if all(abs(m - hp) > 2 and not (40 <= m <= 66 and abs(m - hp) <= 4) for hp in h_pitches)
                    ]
                    if clear_pool:
                        ev["pitch"] = min(clear_pool, key=lambda m: abs(m - p_cur))

        clean_notes = cls.resolve_vertical_clashes(all_notes, chord_timeline, susp_wins)
        clean_notes = cls.resolve_vertical_clashes(clean_notes, chord_timeline, susp_wins)
        cc_evs, c64, c67 = cls.build_chord_synced_pedals(chord_timeline, acts_meta, total_dur)
        return clean_notes + cc_evs, c64, c67


class BeautyHarmonyAuditor:
    """
    Rigorous polyphonic & harmonic quality gate:
    1. Consonance Score >= 90.0 (unjustified dissonant pair ratio <= 2.5%)
    2. Polyphony Score (avg_polyphony >= 2.5 and mono_ratio_pct < 10.0%)
    3. Melodic Cantabile Score >= 82.0 (post-leap gap-fill, anti-scale, single apex, dynamic arch)
    4. Harmonic Progression Score >= 85.0 (strong-beat chord hit >= 85%)
    5. Pedal Cleanliness Score >= 85.0 (chord-boundary CC#64 clear/re-press >= 85% & continuous CC#67)
    """

    @classmethod
    def audit_piece(
        cls,
        notes: List[Dict[str, Any]],
        chord_timeline: List[Dict[str, Any]],
        cc64_curve: List[Dict[str, Any]],
        cc67_curve: List[Dict[str, Any]],
        tonality: str = "D_minor",
        suspension_windows: Optional[List[Tuple[float, float, int]]] = None,
        piano_events_for_preflight: Optional[List[Dict[str, Any]]] = None,
    ) -> BeautyAuditReport:
        note_evs = [n for n in notes if int(n.get("pitch", n.get("note", 0)) or 0) > 0]
        if not note_evs:
            return BeautyAuditReport(
                passed=False, beauty_score=0.0, consonance_score=0.0, polyphony_score=0.0,
                melody_cantabile_score=0.0, harmonic_progression_score=0.0, pedal_cleanliness_score=0.0,
                avg_polyphony=0.0, mono_ratio_pct=100.0, dissonant_pair_pct=100.0,
                strong_beat_chord_hit_pct=0.0, repedal_sync_pct=0.0, preflight_passed=False,
                issues=["empty_notes"],
            )

        susp_wins: List[Tuple[float, float, int]] = []
        for sw in (suspension_windows or []):
            if len(sw) >= 3:
                susp_wins.append((float(sw[0]), float(sw[1]), int(sw[2])))

        parsed_tl: List[Tuple[float, float, ChordSpec]] = []
        for seg in chord_timeline:
            sp = seg.get("_spec")
            if not isinstance(sp, ChordSpec):
                sp = parse_chord_symbol(str(seg.get("chord", "Dm9")), tonality)
            parsed_tl.append((float(seg.get("start_s", 0.0)), float(seg.get("end_s", 2.0)), sp))
        if not parsed_tl:
            sp0 = parse_chord_symbol("Dm9", tonality)
            parsed_tl = [(0.0, 999.0, sp0)]

        def _chord_at(t_s: float) -> ChordSpec:
            chosen = parsed_tl[0][2]
            for st, _en, sp in parsed_tl:
                if st <= t_s + 1e-4:
                    chosen = sp
                else:
                    break
            return chosen

        end_t = max(float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.3)) for n in note_evs)
        poly_counts: List[int] = []
        total_pairs = 0
        unjustified_dissonant_pairs = 0
        low_mud_events = 0

        t_grid = np.arange(0.0, max(0.2, end_t - 0.1), 0.05)
        for tg in t_grid:
            t_val = float(tg)
            active = [
                int(n.get("pitch", n.get("note", 0)))
                for n in note_evs
                if float(n.get("onset_s", 0.0)) <= t_val < (float(n.get("onset_s", 0.0)) + float(n.get("duration_s", 0.3)))
            ]
            if not active:
                continue
            poly_counts.append(len(active))
            ch = _chord_at(t_val)
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    total_pairs += 1
                    p1, p2 = active[i], active[j]
                    p_lo, p_hi = (p1, p2) if p1 < p2 else (p2, p1)
                    diff = p_hi - p_lo
                    iv = diff % 12
                    if iv in (1, 6, 11) or (iv == 2 and diff <= 2):
                        if not _is_justified_tension(p_lo, p_hi, t_val, ch, susp_wins):
                            unjustified_dissonant_pairs += 1
                    if p_lo < 52 and 0 < diff < 5:
                        low_mud_events += 1

        avg_poly = float(np.mean(poly_counts)) if poly_counts else 0.0
        mono_ratio_pct = 100.0 * sum(1 for c in poly_counts if c <= 1) / max(1, len(poly_counts))
        diss_pct = 100.0 * unjustified_dissonant_pairs / max(1, total_pairs)
        mud_pct = 100.0 * low_mud_events / max(1, total_pairs)

        consonance_score = max(0.0, min(100.0, 100.0 - diss_pct * 3.8 - mud_pct * 3.0))
        polyphony_score = max(
            0.0,
            min(
                100.0,
                55.0 + min(30.0, max(0.0, avg_poly - 1.8) * 20.0) + max(0.0, 15.0 - mono_ratio_pct * 1.5),
            ),
        )

        # 3. Melody cantabile audit
        rh_mel = [
            n for n in note_evs
            if str(n.get("voice", "")).lower() in ("rh_melody", "melody")
            or (str(n.get("hand", "")).lower() == "rh" and str(n.get("voice", "")).lower() != "rh_harmony")
        ]
        rh_mel.sort(key=lambda x: float(x.get("onset_s", 0.0)))
        mel_pitches = [int(n["pitch"]) for n in rh_mel]
        mel_ivs = [mel_pitches[i] - mel_pitches[i - 1] for i in range(1, len(mel_pitches))]
        big_leaps = 0
        recovered = 0
        for i in range(len(mel_ivs) - 1):
            if abs(mel_ivs[i]) >= 5:
                big_leaps += 1
                if mel_ivs[i] * mel_ivs[i + 1] < 0 and 1 <= abs(mel_ivs[i + 1]) <= 4:
                    recovered += 1
        gap_rate = (recovered / big_leaps) if big_leaps else 1.0

        # Check unidirectional stepwise run >= 5
        run_dir = 0
        run_len = 1
        bad_scale_runs = 0
        for d in mel_ivs:
            cdir = 1 if d > 0 else (-1 if d < 0 else 0)
            if cdir != 0 and cdir == run_dir and 1 <= abs(d) <= 2:
                run_len += 1
                if run_len >= 5:
                    bad_scale_runs += 1
            else:
                run_dir = cdir
                run_len = 1

        mel_span = (max(mel_pitches) - min(mel_pitches)) if mel_pitches else 0
        mel_vels = [int(n.get("velocity", 68)) for n in rh_mel] or [68]
        vel_span = max(mel_vels) - min(mel_vels)
        melody_cantabile_score = max(
            0.0,
            min(
                100.0,
                52.0
                + gap_rate * 22.0
                + (14.0 if mel_span >= 8 else 6.0)
                + (12.0 if vel_span >= 12 else 5.0)
                - bad_scale_runs * 12.0,
            ),
        )

        # 4. Harmonic Progression: strong-beat chord tone hit rate
        strong_hits = 0
        strong_total = 0
        for st, en, sp in parsed_tl:
            mid_bar = (st + en) * 0.5
            for anchor_t in (st, mid_bar):
                near = [
                    n for n in rh_mel
                    if abs(float(n.get("onset_s", 0.0)) - anchor_t) <= 0.14
                ]
                if near:
                    strong_total += 1
                    p0 = int(near[0]["pitch"])
                    is_susp = any(s0 - 0.05 <= anchor_t <= s1 for s0, s1, sp_n in susp_wins if sp_n == p0)
                    if (p0 % 12) in sp.core_pcs or (p0 % 12) in sp.color_pcs or is_susp:
                        strong_hits += 1
        strong_hit_pct = 100.0 * strong_hits / max(1, strong_total)
        harmonic_progression_score = max(0.0, min(100.0, strong_hit_pct * 0.85 + 15.0))

        # 5. Pedal cleanliness: CC#64 dips near chord boundaries + CC#67 continuous dynamic range
        boundaries = [st for st, _, _ in parsed_tl[1:]]
        synced = 0
        for b_t in boundaries:
            if any(abs(float(pt.get("t", 0.0)) - b_t) <= 0.09 and int(pt.get("val", 127)) <= 12 for pt in cc64_curve):
                synced += 1
        repedal_sync_pct = 100.0 * synced / max(1, len(boundaries)) if boundaries else 100.0
        c67_vals = [int(pt.get("val", 0)) for pt in cc67_curve] or [0]
        has_cc67 = len(c67_vals) >= 4 and (max(c67_vals) - min(c67_vals)) >= 25
        pedal_cleanliness_score = max(0.0, min(100.0, repedal_sync_pct * 0.78 + (22.0 if has_cc67 else 4.0)))

        preflight_ok = True
        if piano_events_for_preflight is not None:
            pf_norm = []
            for ev in piano_events_for_preflight:
                p_val = int(ev.get("note", ev.get("pitch", 0)) or 0)
                if p_val <= 0 and int(ev.get("control", 0) or 0) == 67:
                    continue
                ce = dict(ev)
                ce["note"] = p_val
                ce["time_offset_s"] = float(ce.get("time_offset_s", ce.get("onset_s", 0.0)))
                pf_norm.append(ce)
            pf = LocalPreflightChecker.audit_events(pf_norm)
            preflight_ok = bool(pf.get("passed", False))

        beauty_score = round(
            0.30 * consonance_score
            + 0.22 * polyphony_score
            + 0.18 * melody_cantabile_score
            + 0.16 * harmonic_progression_score
            + 0.14 * pedal_cleanliness_score,
            2,
        )

        issues: List[str] = []
        if consonance_score < 90.0 or diss_pct > 2.5:
            issues.append(f"dissonance_too_high({diss_pct:.1f}%)")
        if avg_poly < 2.5:
            issues.append(f"avg_polyphony_low({avg_poly:.2f})")
        if mono_ratio_pct >= 10.0:
            issues.append(f"mono_ratio_high({mono_ratio_pct:.1f}%)")
        if melody_cantabile_score < 82.0:
            issues.append(f"melody_cantabile_low({melody_cantabile_score:.1f})")
        if strong_hit_pct < 85.0:
            issues.append(f"chord_landing_low({strong_hit_pct:.1f}%)")
        if repedal_sync_pct < 85.0 or not has_cc67:
            issues.append(f"pedal_sync_low({repedal_sync_pct:.1f}%)")
        if not preflight_ok:
            issues.append("disklavier_preflight_failed")

        passed = (len(issues) == 0) and (beauty_score >= 88.0)
        return BeautyAuditReport(
            passed=passed,
            beauty_score=beauty_score,
            consonance_score=round(consonance_score, 1),
            polyphony_score=round(polyphony_score, 1),
            melody_cantabile_score=round(melody_cantabile_score, 1),
            harmonic_progression_score=round(harmonic_progression_score, 1),
            pedal_cleanliness_score=round(pedal_cleanliness_score, 1),
            avg_polyphony=round(avg_poly, 2),
            mono_ratio_pct=round(mono_ratio_pct, 1),
            dissonant_pair_pct=round(diss_pct, 2),
            strong_beat_chord_hit_pct=round(strong_hit_pct, 1),
            repedal_sync_pct=round(repedal_sync_pct, 1),
            preflight_passed=preflight_ok,
            issues=issues,
        )
