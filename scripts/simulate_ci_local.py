"""
Local GitHub Actions CI Environment Simulator (`scripts/simulate_ci_local.py`).

Simulates the clean Ubuntu runner environment defined in `.github/workflows/deploy-pages.yml`
by blocking optional heavy/hardware-only packages (`symusic`, `torch`, `sounddevice`, `rtmidi`,
`ollama`, `matplotlib`, `lameenc`, `playwright`, `miditok`, `faster_whisper`) via `sys.meta_path`,
then running the exact CI test suite, 100-piece catalog verifier, and static `web-demo/` checks.
"""

from __future__ import annotations

import importlib.abc
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BLOCKED_IN_CI = {
    "sounddevice",
    "rtmidi",
    "torch",
    "symusic",
    "ollama",
    "matplotlib",
    "lameenc",
    "playwright",
    "miditok",
    "faster_whisper",
    "transformers",
}


class _CIImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        top = fullname.split(".")[0]
        if top in BLOCKED_IN_CI:
            raise ModuleNotFoundError(
                f"No module named {fullname!r} (simulated Ubuntu CI without GPU/audio/MIDI)"
            )
        return None


def _verify_web_demo_bundle() -> None:
    root = ROOT / "web-demo"
    for req in ("index.html", "app.js", "style.css", "vexflow-bravura.js", "score_engraver.js", "VEXFLOW_LICENSE.txt", "favicon.ico", "data/catalog_100.json"):
        assert (root / req).exists(), f"Missing {req}"
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "./app.js" in html and "./style.css" in html and "./favicon.ico" in html
    assert "./vexflow-bravura.js" in html and "./score_engraver.js" in html
    assert "/studio-static/" not in html, "Static web-demo must use relative ./ paths"
    (root / "app.js").read_text(encoding="utf-8")
    (root / "style.css").read_text(encoding="utf-8")
    (root / "vexflow-bravura.js").read_text(encoding="utf-8")
    (root / "score_engraver.js").read_text(encoding="utf-8")
    cat = json.loads((root / "data" / "catalog_100.json").read_text(encoding="utf-8"))
    assert len(cat["pieces"]) == 100
    for e in cat["pieces"]:
        assert (root / "data" / "pieces" / f"{e['id']}.json").exists()
        if e.get("showcase_mp3"):
            assert (root / e["showcase_mp3"]).exists()
        if e.get("showcase_mid"):
            assert (root / e["showcase_mid"]).exists()
    oversized = [str(p) for p in root.rglob("*") if p.is_file() and p.stat().st_size > 25 * 1024 * 1024]
    assert not oversized, f"Oversized files in web-demo: {oversized}"


def main() -> int:
    os.environ["CI"] = "1"
    os.environ["PYTHONUTF8"] = "1"

    # Evict any pre-loaded blocked modules and install CI blocker
    for mod_name in list(sys.modules):
        if mod_name.split(".")[0] in BLOCKED_IN_CI:
            del sys.modules[mod_name]
    sys.meta_path.insert(0, _CIImportBlocker())

    import importlib.util

    _orig_find_spec = importlib.util.find_spec

    def _ci_find_spec(name: str, package: Any = None) -> Any:
        if name.split(".")[0] in BLOCKED_IN_CI:
            return None
        return _orig_find_spec(name, package)

    importlib.util.find_spec = _ci_find_spec  # type: ignore[assignment]

    import pytest
    import scripts.verify_catalog_and_demo as verifier

    test_files: Sequence[str] = [
        "tests/test_safety_guard.py",
        "tests/test_phase1_security.py",
        "tests/test_stage_system.py",
        "tests/test_studio_overhaul.py",
        "tests/test_hybrid_and_trace.py",
    ]
    if (ROOT / "tests" / "test_parallel_and_notagen.py").exists():
        test_files = [*test_files, "tests/test_parallel_and_notagen.py"]

    print("[CI-SIM 1/3] Running CI pytest suite with heavy/hardware packages blocked...")
    rc = pytest.main(["-v", *test_files])
    if rc != 0:
        print(f"[CI-SIM FAIL] pytest exited with {rc}")
        return int(rc)

    print("[CI-SIM 2/3] Running catalog & 10-MP3 integrity verification...")
    rc_cat = verifier.main()
    if rc_cat != 0:
        print(f"[CI-SIM FAIL] verify_catalog_and_demo exited with {rc_cat}")
        return int(rc_cat)

    print("[CI-SIM 3/3] Checking static web-demo bundle...")
    _verify_web_demo_bundle()
    print("[CI-SIM PASS] Simulated Ubuntu GitHub Actions environment passed 100%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
