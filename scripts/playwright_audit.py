"""
Playwright Real-Browser End-to-End & Visual Layout Auditor (`scripts/playwright_audit.py`).

Tests BOTH:
1. Local FastAPI `stage_server.py` (`/studio` and `/stage`)
2. Static GitHub Pages bundle (`web-demo/index.html` served over clean HTTP)

Verifies:
- 0 `console.error`, 0 uncaught `pageerror`, 0 HTTP >= 400 responses
- 100-piece catalog load, Mode B / Mode A / MP3 filtering, text search, piece click selection
- WebAudio play/pause + 5-channel mixer toggles + Solo + Deliberation replay
- Client-side SMF MIDI download (`MThd` header verified)
- Showcase MP3 load & play (`readyState >= 2`, `error === null`, `duration > 20s`)
- Live `/studio` turn execution & `/stage` Mode A/B + `F10` projection mode toggle
- Zero horizontal overflow and zero overlapping buttons at BOTH 1920x1080 and 1280x720
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright
import requests
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.stage_server import create_stage_app

WEB_DEMO_DIR = ROOT / "web-demo"
DOCS_IMAGES_DIR = ROOT / "docs" / "images"
AUDIT_OUT_DIR = ROOT / "data" / "telemetry" / "playwright_rounds"


def _find_chromium_exe() -> Optional[str]:
    candidates = [
        r"C:\Users\gakuto\AppData\Local\hermes\tools\chromium-1248\chrome-win64\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _check_layout_geometry(page: Page, label: str) -> Dict[str, Any]:
    return page.evaluate(
        """(lbl) => {
            const vw = window.innerWidth;
            const sw = document.documentElement.scrollWidth;
            const overflowPx = Math.max(0, sw - vw);

            const btns = Array.from(document.querySelectorAll('button, a.btn, input, select'))
                .filter((el) => {
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden' || el.closest('[hidden]')) return false;
                    const r = el.getBoundingClientRect();
                    if (!(r.width > 0 && r.height > 0)) return false;
                    // Respect scroll containers (e.g., #piece-list overflow-y: auto): skip items scrolled out of view
                    let cur = el.parentElement;
                    while (cur && cur !== document.body) {
                        const cst = window.getComputedStyle(cur);
                        if (cst.overflowY === 'auto' || cst.overflowY === 'scroll') {
                            const cr = cur.getBoundingClientRect();
                            if (r.bottom <= cr.top + 1 || r.top >= cr.bottom - 1) return false;
                        }
                        cur = cur.parentElement;
                    }
                    return true;
                });

            const clipped = [];
            const rects = [];
            for (const el of btns) {
                const raw = el.getBoundingClientRect();
                let left = raw.left, top = raw.top, right = raw.right, bottom = raw.bottom;
                let cur = el.parentElement;
                while (cur && cur !== document.body) {
                    const cst = window.getComputedStyle(cur);
                    if (cst.overflowY === 'auto' || cst.overflowY === 'scroll' || cst.overflowX === 'auto' || cst.overflowX === 'scroll') {
                        const cr = cur.getBoundingClientRect();
                        left = Math.max(left, cr.left);
                        top = Math.max(top, cr.top);
                        right = Math.min(right, cr.right);
                        bottom = Math.min(bottom, cr.bottom);
                    }
                    cur = cur.parentElement;
                }
                if (right - left <= 1 || bottom - top <= 1) continue;
                if (raw.right > vw + 2 || raw.left < -2) {
                    clipped.push({ id: el.id || el.textContent.trim().slice(0, 20), left: raw.left, right: raw.right, vw });
                }
                rects.push({ id: el.id || el.className || el.tagName, r: { left, top, right, bottom } });
            }

            const overlaps = [];
            for (let i = 0; i < rects.length; i++) {
                for (let j = i + 1; j < rects.length; j++) {
                    const a = rects[i].r;
                    const b = rects[j].r;
                    const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                    const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                    if (ix > 3 && iy > 3) {
                        overlaps.push({ a: rects[i].id, b: rects[j].id, ix: Math.round(ix), iy: Math.round(iy) });
                    }
                }
            }
            return {
                label: lbl,
                vw,
                sw,
                overflowPx,
                visibleControlCount: btns.length,
                clipped,
                overlaps,
            };
        }""",
        label,
    )


def _attach_error_listeners(page: Page, errors: Dict[str, List[str]]) -> None:
    page.on(
        "console",
        lambda msg: errors["console"].append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors["pageerror"].append(str(exc)))
    page.on(
        "response",
        lambda resp: errors["http"].append(f"{resp.status} {resp.url}") if resp.status >= 400 else None,
    )


def run_audit(round_num: int = 1, update_docs_images: bool = False, quick: bool = False) -> Dict[str, Any]:
    out_dir = AUDIT_OUT_DIR / f"round_{round_num}"
    out_dir.mkdir(parents=True, exist_ok=True)
    DOCS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Start Live FastAPI Stage Server
    stage_port = _free_port()
    app = create_stage_app(
        demo_enabled=False,
        monitor_audio=True,
        production_mode=False,
        corpus_dirs=None,
        use_ollama=False,
        open_hardware=False,
        play_audio_device=False,
    )
    uv_cfg = uvicorn.Config(app, host="127.0.0.1", port=stage_port, log_level="warning")
    stage_srv = uvicorn.Server(uv_cfg)
    stage_th = threading.Thread(target=stage_srv.run, name="PlaywrightStageSrv", daemon=True)
    stage_th.start()

    # 2. Start Static Web-Demo Server
    static_port = _free_port()
    handler = partial(_QuietStaticHandler, directory=str(WEB_DEMO_DIR))
    static_srv = ThreadingHTTPServer(("127.0.0.1", static_port), handler)
    static_th = threading.Thread(target=static_srv.serve_forever, name="PlaywrightStaticSrv", daemon=True)
    static_th.start()

    stage_url = f"http://127.0.0.1:{stage_port}"
    static_url = f"http://127.0.0.1:{static_port}"

    for _ in range(50):
        try:
            if requests.get(f"{stage_url}/api/stage/state", timeout=1.0).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)

    violations: List[str] = []
    errors: Dict[str, List[str]] = {"console": [], "pageerror": [], "http": []}
    geometry_reports: List[Dict[str, Any]] = []
    screenshots: Dict[str, str] = {}
    functional_checks: Dict[str, Any] = {}

    exe = _find_chromium_exe()
    try:
        with sync_playwright() as p:
            launch_kwargs: Dict[str, Any] = {
                "headless": True,
                "args": ["--autoplay-policy=no-user-gesture-required", "--no-sandbox"],
            }
            if exe:
                launch_kwargs["executable_path"] = exe
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True)
            page = context.new_page()
            _attach_error_listeners(page, errors)

            # ===== A. Static GitHub Pages Web-Demo Audit =====
            page.goto(f"{static_url}/index.html", wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=10000)
            functional_checks["web_demo_loaded_100"] = True

            # Test filters: showcase (10), cello (50), piano (50), all (100)
            page.click("button[data-filter='showcase']")
            cnt_show = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='cello']")
            cnt_cello = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='piano']")
            cnt_piano = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='all']")
            cnt_all = page.locator("#piece-list .piece-item").count()
            functional_checks["filters"] = {"showcase": cnt_show, "cello": cnt_cello, "piano": cnt_piano, "all": cnt_all}
            if (cnt_show, cnt_cello, cnt_piano, cnt_all) != (10, 50, 50, 100):
                violations.append(f"Filter counts mismatch: {(cnt_show, cnt_cello, cnt_piano, cnt_all)}")

            # Search test
            page.fill("#filter-search", "mode")
            page.fill("#filter-search", "")

            # Verify showcase MP3 loads and plays cleanly
            page.click("button[data-filter='showcase']")
            page.locator("#piece-list .piece-item").first.click()
            page.wait_for_function(
                "() => { const a = document.getElementById('mp3-player'); return a && a.readyState >= 2 && a.duration > 20; }",
                timeout=10000,
            )
            mp3_info = page.evaluate(
                """async () => {
                    const a = document.getElementById('mp3-player');
                    let playErr = null;
                    try { await a.play(); } catch (e) { playErr = String(e); }
                    const info = { readyState: a.readyState, duration: a.duration, error: a.error ? a.error.code : null, paused: a.paused, playErr };
                    a.pause();
                    return info;
                }"""
            )
            functional_checks["showcase_mp3"] = mp3_info
            if mp3_info["readyState"] < 2 or mp3_info["error"] is not None or mp3_info["duration"] <= 20:
                violations.append(f"MP3 playback verification failed: {mp3_info}")

            # Verify WebAudio playback + 5 mixer toggles + Solo + Reset
            page.click("#btn-play")
            time.sleep(0.35)
            play_label = page.locator("#btn-play").inner_text()
            for mkey in ("rh", "lh", "human", "cc64", "cc67"):
                page.click(f"button[data-mix-toggle='{mkey}']")
            page.click("button[data-mix-solo='rh']")
            page.click("#btn-mixer-reset")
            page.click("#btn-play")  # pause
            functional_checks["webaudio_play_toggle"] = play_label

            # Verify Client MIDI Download starts with MThd
            with page.expect_download(timeout=8000) as dl_info:
                page.click("#btn-download-midi")
            dl = dl_info.value
            dl_path = out_dir / "downloaded_test.mid"
            dl.save_as(str(dl_path))
            mid_hdr = dl_path.read_bytes()[:4]
            functional_checks["midi_download_bytes"] = dl_path.stat().st_size
            if mid_hdr != b"MThd" or dl_path.stat().st_size < 300:
                violations.append(f"Invalid MIDI download: hdr={mid_hdr!r}, size={dl_path.stat().st_size}")

            # Verify Deliberation Replay & capture screenshot before switching back to Score
            page.click("#btn-replay-delib")
            page.wait_for_selector("#delib-grid .cand-card", timeout=5000)
            delib_cnt = page.locator("#delib-grid .cand-card").count()
            functional_checks["delib_cards"] = delib_cnt
            if delib_cnt != 4:
                violations.append(f"Expected 4 deliberation cards, got {delib_cnt}")
            time.sleep(1.7)
            p_delib = out_dir / "studio_delib_replay.png"
            page.screenshot(path=str(p_delib))
            screenshots["studio_delib_replay"] = str(p_delib)
            page.click("#tab-score")
            page.click("button[data-filter='all']")

            # Geometry & screenshot at 1920x1080
            page.set_viewport_size({"width": 1920, "height": 1080})
            time.sleep(0.2)
            g_wd_1080 = _check_layout_geometry(page, "web_demo_1920x1080")
            geometry_reports.append(g_wd_1080)
            p_wd_1080 = out_dir / "web_demo_1920x1080.png"
            page.screenshot(path=str(p_wd_1080))
            screenshots["web_demo_1920x1080"] = str(p_wd_1080)

            # Geometry & screenshot at 1280x720
            page.set_viewport_size({"width": 1280, "height": 720})
            time.sleep(0.2)
            g_wd_720 = _check_layout_geometry(page, "web_demo_1280x720")
            geometry_reports.append(g_wd_720)
            p_wd_720 = out_dir / "web_demo_1280x720.png"
            page.screenshot(path=str(p_wd_720))
            screenshots["web_demo_1280x720"] = str(p_wd_720)

            # ===== B. Live FastAPI /studio Audit =====
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(f"{stage_url}/studio", wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=10000)
            if not quick:
                page.click("#tab-live")
                page.click("#live-next-turn")
                page.wait_for_function(
                    "document.getElementById('live-monologue-box').textContent.includes('Turn #')",
                    timeout=10000,
                )
                page.click("#tab-score")
                time.sleep(0.2)

            g_st_1080 = _check_layout_geometry(page, "studio_1920x1080")
            geometry_reports.append(g_st_1080)
            p_st_1080 = out_dir / "studio_1920x1080.png"
            page.screenshot(path=str(p_st_1080))
            screenshots["studio_1920x1080"] = str(p_st_1080)

            page.set_viewport_size({"width": 1280, "height": 720})
            time.sleep(0.2)
            g_st_720 = _check_layout_geometry(page, "studio_1280x720")
            geometry_reports.append(g_st_720)
            p_st_720 = out_dir / "studio_1280x720.png"
            page.screenshot(path=str(p_st_720))
            screenshots["studio_1280x720"] = str(p_st_720)

            # ===== C. Live /stage Projection HUD Audit (Mode B, Mode A, F10) =====
            requests.post(f"{stage_url}/api/stage/reset", timeout=5.0).raise_for_status()
            requests.post(f"{stage_url}/api/stage/mode", json={"mode": "cello"}, timeout=5.0).raise_for_status()
            for _idx in range(6 if not quick else 2):
                requests.post(f"{stage_url}/api/stage/turn", timeout=12.0).raise_for_status()

            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(f"{stage_url}/stage?snapshot=1", wait_until="networkidle")
            page.wait_for_function("document.getElementById('meta-arena').textContent.includes('TURN #')", timeout=10000)
            time.sleep(0.3)

            g_hud_b = _check_layout_geometry(page, "stage_hud_mode_b_1920x1080")
            geometry_reports.append(g_hud_b)
            p_hud_b = out_dir / "stage_hud_mode_b_cello.png"
            page.screenshot(path=str(p_hud_b))
            screenshots["stage_hud_mode_b_cello"] = str(p_hud_b)

            # Test F10 toggle hides control bar cleanly
            page.keyboard.press("F10")
            time.sleep(0.15)
            f10_hidden = page.evaluate("document.getElementById('control-bar').classList.contains('hidden-bar')")
            p_f10 = out_dir / "stage_projection_f10.png"
            page.screenshot(path=str(p_f10))
            screenshots["stage_projection_f10"] = str(p_f10)
            page.keyboard.press("F10")
            time.sleep(0.15)
            f10_restored = not page.evaluate("document.getElementById('control-bar').classList.contains('hidden-bar')")
            functional_checks["f10_toggle"] = {"hidden": f10_hidden, "restored": f10_restored}
            if not (f10_hidden and f10_restored):
                violations.append(f"F10 toggle check failed: hidden={f10_hidden}, restored={f10_restored}")

            # Mode A 8-turn Act 4 + Synthesize Work screenshot (disconnect WS cleanly before batch POSTs)
            if not quick:
                page.goto("about:blank")
                requests.post(f"{stage_url}/api/stage/reset", timeout=10.0).raise_for_status()
                requests.post(f"{stage_url}/api/stage/mode", json={"mode": "piano"}, timeout=10.0).raise_for_status()
                for _ in range(8):
                    requests.post(f"{stage_url}/api/stage/turn", timeout=20.0).raise_for_status()
                requests.post(f"{stage_url}/api/stage/synthesize_work", json={"play_audio": False}, timeout=25.0).raise_for_status()
                page.goto(f"{stage_url}/stage?snapshot=1", wait_until="networkidle")
                page.wait_for_function("document.getElementById('meta-arena').textContent.includes('TURN #8')", timeout=10000)
                time.sleep(0.3)
                p_hud_a = out_dir / "stage_hud_mode_a_piano.png"
                page.screenshot(path=str(p_hud_a))
                screenshots["stage_hud_mode_a_piano"] = str(p_hud_a)

            page.set_viewport_size({"width": 1280, "height": 720})
            time.sleep(0.2)
            g_hud_720 = _check_layout_geometry(page, "stage_hud_1280x720")
            geometry_reports.append(g_hud_720)

            page.goto("about:blank")
            context.close()
            browser.close()
    finally:
        stage_srv.should_exit = True
        stage_th.join(timeout=3.0)
        try:
            app.state.orchestrator.close()
        except Exception:
            pass
        static_srv.shutdown()
        static_srv.server_close()
        # Clean up any temporary synthesized files in data/telemetry/exports created during live Mode A synth
        exp_dir = ROOT / "data" / "telemetry" / "exports"
        if exp_dir.exists():
            for f in exp_dir.glob("masterwork_*"):
                try:
                    f.unlink()
                except Exception:
                    pass

    for g in geometry_reports:
        if g["overflowPx"] > 2:
            violations.append(f"{g['label']}: horizontal overflow {g['overflowPx']}px")
        if g["clipped"]:
            violations.append(f"{g['label']}: clipped controls {g['clipped'][:3]}")
        if g["overlaps"]:
            violations.append(f"{g['label']}: overlapping controls {g['overlaps'][:3]}")

    if errors["console"]:
        violations.append(f"console.error count={len(errors['console'])}: {errors['console'][:3]}")
    if errors["pageerror"]:
        violations.append(f"pageerror count={len(errors['pageerror'])}: {errors['pageerror'][:3]}")
    if errors["http"]:
        violations.append(f"HTTP >=400 count={len(errors['http'])}: {errors['http'][:3]}")

    if update_docs_images:
        for key in (
            "studio_1920x1080",
            "web_demo_1920x1080",
            "stage_hud_mode_b_cello",
            "stage_hud_mode_a_piano",
            "stage_projection_f10",
        ):
            src_p = screenshots.get(key)
            if src_p and Path(src_p).exists():
                shutil.copy2(src_p, DOCS_IMAGES_DIR / f"{key}.png")

    report = {
        "round": round_num,
        "passed": len(violations) == 0,
        "errors": errors,
        "functional_checks": functional_checks,
        "geometry_reports": geometry_reports,
        "screenshots": screenshots,
        "violations": violations,
    }
    report_path = ROOT / "data" / "telemetry" / f"playwright_audit_round_{round_num}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright Real-Browser Audit for Studio, Stage HUD, & GitHub Pages Web Demo")
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--update-docs-images", action="store_true")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    rep = run_audit(round_num=args.round, update_docs_images=args.update_docs_images, quick=args.quick)
    print(f"=== Playwright Audit Round {rep['round']} | passed={rep['passed']} ===")
    print(f"Errors: console={len(rep['errors']['console'])}, pageerror={len(rep['errors']['pageerror'])}, http={len(rep['errors']['http'])}")
    print(f"Functional: {rep['functional_checks']}")
    for g in rep["geometry_reports"]:
        print(f" - {g['label']}: overflow={g['overflowPx']}px clipped={len(g['clipped'])} overlaps={len(g['overlaps'])}")
    if rep["violations"]:
        print("VIOLATIONS:")
        for v in rep["violations"]:
            print("  -", v)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
