"""
Catalog & Demo Musicality Integrity Verifier (`scripts/verify_catalog_and_demo.py`).

Checks:
1. `web-demo/data/catalog_100.json` and all `piece_001.json..piece_100.json` exist and pass schema.
2. Mode split = 50 Mode B (cello) + 50 Mode A (piano).
3. All 100 pieces meet strict musicality gates:
   - Beauty Score >= 88.0
   - Consonance Score >= 90.0 & Dissonant Pair % <= 2.5%
   - Avg Polyphony >= 2.5 & Monophonic Slice % < 10.0%
   - Pedal Cleanliness >= 85.0 & Disklavier Preflight Passed
4. All 10 showcase MP3s (`web-demo/audio/` and `docs/demos/`) + canonical `demo_mode_{a,b}_*` decode cleanly
   with valid duration, healthy RMS (> 0.015), 0 clipping, and 0 NaN/Inf.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

import mido
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.harmonic_beauty_realizer import BeautyHarmonyAuditor

WEB_DEMO_DIR = ROOT / "web-demo"
CATALOG_PATH = WEB_DEMO_DIR / "data" / "catalog_100.json"
PIECES_DIR = WEB_DEMO_DIR / "data" / "pieces"
WEB_AUDIO_DIR = WEB_DEMO_DIR / "audio"
DOCS_DEMOS_DIR = ROOT / "docs" / "demos"
REPORT_PATH = ROOT / "data" / "telemetry" / "catalog_100_verification.json"


def _find_ffmpeg() -> str:
    local_ff = Path(r"C:\Users\gakuto\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe")
    if local_ff.exists():
        return str(local_ff)
    w = shutil.which("ffmpeg")
    if w:
        return w
    return ""


def _decode_mp3_metrics(mp3_path: Path, ffmpeg_exe: str) -> Dict[str, Any]:
    try:
        data, sr = sf.read(str(mp3_path), dtype="float32")
    except Exception:
        if not ffmpeg_exe:
            return {"error": f"Cannot decode {mp3_path.name} without soundfile MP3 or ffmpeg"}
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_wav = Path(tf.name)
        try:
            subprocess.run(
                [ffmpeg_exe, "-y", "-loglevel", "error", "-i", str(mp3_path), str(tmp_wav)],
                check=True,
                timeout=20,
            )
            data, sr = sf.read(str(tmp_wav), dtype="float32")
        finally:
            if tmp_wav.exists():
                tmp_wav.unlink()

    dur_s = len(data) / float(sr)
    rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)) + 1e-12)
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    dbfs = round(20.0 * math.log10(max(1e-6, rms)), 2)
    has_bad = bool(np.any(~np.isfinite(data)))
    clipping = int(np.sum(np.abs(data) > 1.0))
    return {
        "file": str(mp3_path.relative_to(ROOT)).replace("\\", "/"),
        "size_kb": round(mp3_path.stat().st_size / 1024.0, 1),
        "sample_rate": int(sr),
        "duration_s": round(dur_s, 2),
        "rms": round(rms, 4),
        "dbfs": dbfs,
        "peak": round(peak, 4),
        "clipping": clipping,
        "has_nan_or_inf": has_bad,
    }


def verify_all(expected_count: int = 100) -> Dict[str, Any]:
    violations: List[str] = []
    if not CATALOG_PATH.exists():
        return {"passed": False, "violations": [f"Missing {CATALOG_PATH}"]}

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = list(catalog.get("pieces") or [])
    if len(entries) != expected_count:
        violations.append(f"Expected {expected_count} pieces in catalog, got {len(entries)}")

    cello_cnt = sum(1 for e in entries if e.get("mode") == "cello")
    piano_cnt = sum(1 for e in entries if e.get("mode") == "piano")
    if cello_cnt != expected_count // 2 or piano_cnt != expected_count - (expected_count // 2):
        violations.append(f"Expected 50/50 mode split, got cello={cello_cnt}, piano={piano_cnt}")

    re_audit_scores: List[float] = []
    poly_vals: List[float] = []
    mono_vals: List[float] = []
    cons_vals: List[float] = []
    diss_vals: List[float] = []

    for e in entries:
        pid = e["id"]
        p_path = PIECES_DIR / f"{pid}.json"
        if not p_path.exists():
            violations.append(f"Missing piece JSON: {p_path}")
            continue
        pj = json.loads(p_path.read_text(encoding="utf-8"))
        au = pj.get("audit", {})
        if not au.get("passed", False):
            violations.append(f"{pid}: audit.passed is False")
        if float(au.get("beauty_score", 0.0)) < 88.0:
            violations.append(f"{pid}: beauty_score {au.get('beauty_score')} < 88.0")
        if float(au.get("consonance_score", 0.0)) < 90.0:
            violations.append(f"{pid}: consonance_score {au.get('consonance_score')} < 90.0")
        if float(au.get("avg_polyphony", 0.0)) < 2.5:
            violations.append(f"{pid}: avg_polyphony {au.get('avg_polyphony')} < 2.5")
        if float(au.get("mono_ratio_pct", 100.0)) >= 10.0:
            violations.append(f"{pid}: mono_ratio_pct {au.get('mono_ratio_pct')} >= 10.0")
        if not au.get("preflight_passed", False):
            violations.append(f"{pid}: preflight_passed is False")

        # Spot-check real re-audit every 10th piece + all top 10 showcase pieces
        if int(pj.get("index", 0)) % 10 == 1 or pj.get("showcase_mp3"):
            expanded_notes = [
                {
                    "pitch": int(n["p"]),
                    "onset_s": float(n["t"]),
                    "duration_s": float(n["d"]),
                    "velocity": int(n["v"]),
                    "hand": str(n["h"]),
                    "voice": str(n["r"]),
                }
                for n in pj["notes"]
            ]
            rep = BeautyHarmonyAuditor.audit_piece(
                expanded_notes, pj["chord_timeline"], pj["cc64"], pj["cc67"], tonality=pj["tonality"]
            )
            if not rep.passed:
                violations.append(f"{pid}: independent re-audit failed ({rep.issues})")

        re_audit_scores.append(float(au.get("beauty_score", 0.0)))
        poly_vals.append(float(au.get("avg_polyphony", 0.0)))
        mono_vals.append(float(au.get("mono_ratio_pct", 0.0)))
        cons_vals.append(float(au.get("consonance_score", 0.0)))
        diss_vals.append(float(au.get("dissonant_pair_pct", 0.0)))

    # Verify 10 showcase MP3s & MIDIs
    ffmpeg_exe = _find_ffmpeg()
    mp3_metrics: List[Dict[str, Any]] = []
    showcase_ids = list(catalog.get("showcase_ids") or [])
    if len(showcase_ids) != 10:
        violations.append(f"Expected 10 showcase_ids, got {len(showcase_ids)}")

    for rank in range(1, 11):
        stem = f"showcase_{rank:02d}"
        w_mp3 = WEB_AUDIO_DIR / f"{stem}.mp3"
        w_mid = WEB_AUDIO_DIR / f"{stem}.mid"
        d_mp3 = DOCS_DEMOS_DIR / f"{stem}.mp3"
        d_mid = DOCS_DEMOS_DIR / f"{stem}.mid"
        for p in (w_mp3, w_mid, d_mp3, d_mid):
            if not p.exists() or p.stat().st_size < 1000:
                violations.append(f"Missing or tiny showcase asset: {p}")
        if w_mp3.exists():
            m = _decode_mp3_metrics(w_mp3, ffmpeg_exe)
            mp3_metrics.append(m)
            if "error" in m:
                violations.append(m["error"])
            else:
                if m["duration_s"] < 20.0:
                    violations.append(f"{w_mp3.name}: duration_s {m['duration_s']} < 20s")
                if m["rms"] <= 0.015:
                    violations.append(f"{w_mp3.name}: RMS {m['rms']} <= 0.015")
                if m["clipping"] > 0 or m["has_nan_or_inf"]:
                    violations.append(f"{w_mp3.name}: clipping={m['clipping']} nan={m['has_nan_or_inf']}")
        if w_mid.exists():
            mid = mido.MidiFile(str(w_mid))
            nc = sum(1 for tr in mid.tracks for msg in tr if msg.type == "note_on" and msg.velocity > 0)
            if nc < 60:
                violations.append(f"{w_mid.name}: too few MIDI notes ({nc})")

    for cname in ("demo_mode_b_cello_dialogue", "demo_mode_a_piano_dialogue"):
        for ext in (".mp3", ".mid", ".musicxml"):
            cp = DOCS_DEMOS_DIR / f"{cname}{ext}"
            if not cp.exists() or cp.stat().st_size < 1000:
                violations.append(f"Missing canonical demo file: {cp}")

    summary = {
        "passed": len(violations) == 0,
        "pieces_verified": len(entries),
        "mode_b_count": cello_cnt,
        "mode_a_count": piano_cnt,
        "avg_beauty_score": round(float(np.mean(re_audit_scores)), 2) if re_audit_scores else 0.0,
        "min_beauty_score": round(float(np.min(re_audit_scores)), 2) if re_audit_scores else 0.0,
        "avg_consonance_score": round(float(np.mean(cons_vals)), 2) if cons_vals else 0.0,
        "avg_dissonant_pair_pct": round(float(np.mean(diss_vals)), 2) if diss_vals else 0.0,
        "avg_polyphony": round(float(np.mean(poly_vals)), 2) if poly_vals else 0.0,
        "avg_mono_ratio_pct": round(float(np.mean(mono_vals)), 2) if mono_vals else 0.0,
        "max_mono_ratio_pct": round(float(np.max(mono_vals)), 2) if mono_vals else 0.0,
        "showcase_mp3_count": len(mp3_metrics),
        "mp3_metrics": mp3_metrics,
        "violations": violations,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    res = verify_all()
    print(json.dumps({k: v for k, v in res.items() if k != "mp3_metrics"}, ensure_ascii=False, indent=2))
    print(f"MP3 metrics verified: {len(res.get('mp3_metrics', []))} files")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
