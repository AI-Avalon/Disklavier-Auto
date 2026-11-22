"""
Playwright Real-Browser End-to-End & Ergonomic Visual Layout Auditor (`scripts/playwright_audit.py`).

Tests ALL THREE surfaces at BOTH 1920x1080 and 1366x768:
1. Static GitHub Pages bundle (`web-demo/index.html` over clean HTTP)
2. Live FastAPI `/studio` across ALL 4 modes:
   - [1] Cello Dialogue (`#view-cello`)
   - [2] Piano Dialogue (`#view-piano` + interactive `#canvas-piano-keys`)
   - [3] NotaGen / LLM Live Streaming (`#view-notagen` 4-bar streaming check)
   - [4] Curated 100-Piece Library (`#view-library` filters, MP3, WebAudio, mixer, Deliberation cards)
3. Live `/stage` Projection HUD + `F10` Clean Projection Toggle

Enforces:
- 0 `console.error`, 0 uncaught `pageerror`, 0 HTTP >= 400 responses
- Horizontal overflow <= 2px, 0 clipped controls, 0 overlapping controls
- Minimum visible text computed font-size >= 12.0px
- Primary non-compact buttons and mode tabs height >= 40px
- Physical Left/Right pedal spatial ordering: `UNA CORDA` strictly left of `DAMPER`
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

            const isActuallyVisible = (el) => {
                if (!el) return false;
                if (el.closest('[hidden]')) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0.5 && r.height > 0.5;
            };

            const controls = Array.from(document.querySelectorAll('button, a.btn, input, select'))
                .filter((el) => {
                    if (!isActuallyVisible(el)) return false;
                    const r = el.getBoundingClientRect();
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
            const smallPrimaryBtns = [];

            for (const el of controls) {
                const raw = el.getBoundingClientRect();
                if ((el.classList.contains('mode-tab') || (el.classList.contains('btn') && !el.classList.contains('compact'))) && raw.height < 40) {
                    smallPrimaryBtns.push({ id: el.id || el.textContent.trim().slice(0, 20), height: Math.round(raw.height) });
                }

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
                    clipped.push({ id: el.id || el.textContent.trim().slice(0, 20), left: Math.round(raw.left), right: Math.round(raw.right), vw });
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

            // Font size audit on visible text nodes
            let minVisibleFontPx = 999;
            const smallFontEls = [];
            const allTextContainers = Array.from(document.querySelectorAll('body *'));
            for (const el of allTextContainers) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS'].includes(el.tagName)) continue;
                if (!isActuallyVisible(el)) continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (!hasDirectText) continue;
                const fs = parseFloat(window.getComputedStyle(el).fontSize || '15');
                if (fs < minVisibleFontPx) minVisibleFontPx = fs;
                if (fs < 12.0) {
                    smallFontEls.push({ tag: el.tagName, id: el.id || el.className, fs, text: el.textContent.trim().slice(0, 24) });
                }
            }

            // Spatial Left/Right Pedal order audit
            let pedalOrderOk = true;
            const pedalPairs = [];
            for (const container of document.querySelectorAll('.pedal-gauges, .pedal-pair')) {
                if (!isActuallyVisible(container)) continue;
                const textSpans = Array.from(container.querySelectorAll('span'));
                const una = textSpans.find((s) => s.textContent.includes('UNA CORDA'));
                const dmp = textSpans.find((s) => s.textContent.includes('DAMPER'));
                if (una && dmp) {
                    const ur = una.getBoundingClientRect();
                    const dr = dmp.getBoundingClientRect();
                    const ok = ur.left < dr.left;
                    pedalPairs.push({ unaLeft: Math.round(ur.left), damperLeft: Math.round(dr.left), ok });
                    if (!ok) pedalOrderOk = false;
                }
            }

            return {
                label: lbl,
                vw,
                sw,
                overflowPx,
                visibleControlCount: controls.length,
                clipped,
                overlaps,
                minVisibleFontPx: minVisibleFontPx === 999 ? 15 : minVisibleFontPx,
                smallFontEls,
                smallPrimaryBtns,
                pedalOrderOk,
                pedalPairs,
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


def _record_geometry_violations(geometry_reports: List[Dict[str, Any]], violations: List[str]) -> None:
    for g in geometry_reports:
        if g["overflowPx"] > 2:
            violations.append(f"{g['label']}: horizontal overflow {g['overflowPx']}px")
        if g["clipped"]:
            violations.append(f"{g['label']}: clipped controls {g['clipped'][:3]}")
        if g["overlaps"]:
            violations.append(f"{g['label']}: overlapping controls {g['overlaps'][:3]}")
        if g["minVisibleFontPx"] < 12.0 or g["smallFontEls"]:
            violations.append(f"{g['label']}: font < 12px ({g['smallFontEls'][:3]})")
        if g["smallPrimaryBtns"]:
            violations.append(f"{g['label']}: small primary buttons < 40px ({g['smallPrimaryBtns'][:3]})")
        if not g["pedalOrderOk"]:
            violations.append(f"{g['label']}: UNA CORDA not left of DAMPER ({g['pedalPairs']})")


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

            # ===== A. Static GitHub Pages Web-Demo Audit (index.html) =====
            page.goto(f"{static_url}/index.html", wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=12000)
            functional_checks["web_demo_loaded_100"] = True

            # Test Mode 4 filters: mp3 (10), cello (50), piano (50), all (100)
            page.click("button[data-filter='mp3']")
            cnt_mp3 = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='cello']")
            cnt_cello = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='piano']")
            cnt_piano = page.locator("#piece-list .piece-item").count()
            page.click("button[data-filter='all']")
            cnt_all = page.locator("#piece-list .piece-item").count()
            functional_checks["filters"] = {"mp3": cnt_mp3, "cello": cnt_cello, "piano": cnt_piano, "all": cnt_all}
            if (cnt_mp3, cnt_cello, cnt_piano, cnt_all) != (10, 50, 50, 100):
                violations.append(f"Filter counts mismatch: {(cnt_mp3, cnt_cello, cnt_piano, cnt_all)}")

            # Search & Showcase MP3 verification
            page.click("button[data-filter='mp3']")
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

            # WebAudio play + Mixer Solo/Mute + Deliberation tab check
            page.click("#btn-play")
            time.sleep(0.25)
            page.click("button[data-layer='human'][data-action='solo']")
            page.click("button[data-layer='human'][data-action='solo']")
            page.click("#btn-stop")
            page.click("#tab-delib")
            page.wait_for_selector("#delib-grid .cand-card", timeout=5000)
            delib_cnt = page.locator("#delib-grid .cand-card").count()
            functional_checks["delib_cards"] = delib_cnt
            if delib_cnt < 4:
                violations.append(f"Expected 4 deliberation cards, got {delib_cnt}")
            page.click("#tab-score")
            page.click("button[data-filter='all']")

            # Geometry & screenshot of static web-demo at 1920x1080 and 1366x768
            for vw, vh in ((1920, 1080), (1366, 768)):
                page.set_viewport_size({"width": vw, "height": vh})
                time.sleep(0.2)
                geometry_reports.append(_check_layout_geometry(page, f"web_demo_{vw}x{vh}"))
                if vw == 1920:
                    p_wd = out_dir / "web_demo_1920x1080.png"
                    page.screenshot(path=str(p_wd))
                    screenshots["web_demo_1920x1080"] = str(p_wd)

            # Offline Mode 2 Piano test inside static bundle
            page.click("#mode-tab-piano")
            time.sleep(0.25)
            page.click("#btn-piano-demo-phrase")
            time.sleep(0.35)
            page.click("#mode-tab-library")

            # ===== B. Live FastAPI /studio Across All 4 Modes =====
            requests.post(f"{stage_url}/api/stage/reset", timeout=5.0).raise_for_status()
            requests.post(f"{stage_url}/api/stage/turn", timeout=15.0).raise_for_status()

            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(f"{stage_url}/studio", wait_until="domcontentloaded")
            page.wait_for_function("document.querySelectorAll('#cello-cand-cards .cand-pick-card').length === 4", timeout=12000)
            time.sleep(0.25)

            # Mode 1: Cello Dialogue
            geometry_reports.append(_check_layout_geometry(page, "studio_mode1_cello_1920x1080"))
            p_m1 = out_dir / "studio_mode1_cello.png"
            page.screenshot(path=str(p_m1))
            screenshots["studio_mode1_cello"] = str(p_m1)

            # Mode 2: Piano Dialogue + interactive piano key canvas click
            page.click("#mode-tab-piano")
            time.sleep(0.25)
            box = page.locator("#canvas-piano-keys").bounding_box()
            if box:
                page.mouse.click(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.72)
            page.click("#btn-piano-demo-phrase")
            time.sleep(0.35)
            page.wait_for_function("document.querySelectorAll('#piano-cand-cards .cand-pick-card').length === 4", timeout=10000)
            geometry_reports.append(_check_layout_geometry(page, "studio_mode2_piano_1920x1080"))
            p_m2 = out_dir / "studio_mode2_piano.png"
            page.screenshot(path=str(p_m2))
            screenshots["studio_mode2_piano"] = str(p_m2)

            # Mode 3: NotaGen / LLM Live Streaming (4-bar fallback stream)
            page.click("#mode-tab-notagen")
            time.sleep(0.2)
            page.select_option("#sel-ng-backend", "transformer_abc_fallback")
            page.select_option("#sel-ng-bars", "4")
            page.uncheck("#chk-ng-autoplay")
            page.click("#btn-ng-start")
            page.wait_for_function(
                "() => (document.getElementById('ng-metrics-sub')?.textContent || '').includes('4 / 4')",
                timeout=15000,
            )
            time.sleep(0.3)
            functional_checks["notagen_mode3_bars"] = page.locator("#ng-metrics-sub").inner_text()
            geometry_reports.append(_check_layout_geometry(page, "studio_mode3_notagen_1920x1080"))
            p_m3 = out_dir / "studio_mode3_notagen.png"
            page.screenshot(path=str(p_m3))
            screenshots["studio_mode3_notagen"] = str(p_m3)

            # Mode 4: Library Archive
            page.click("#mode-tab-library")
            page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=10000)
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(page, "studio_mode4_library_1920x1080"))
            p_m4 = out_dir / "studio_mode4_library.png"
            page.screenshot(path=str(p_m4))
            screenshots["studio_mode4_library"] = str(p_m4)

            # Check Studio Modes 1, 2, 3, 4 at 1366x768 laptop resolution
            page.set_viewport_size({"width": 1366, "height": 768})
            for mtab, gname in (
                ("#mode-tab-cello", "studio_mode1_cello_1366x768"),
                ("#mode-tab-piano", "studio_mode2_piano_1366x768"),
                ("#mode-tab-notagen", "studio_mode3_notagen_1366x768"),
                ("#mode-tab-library", "studio_mode4_library_1366x768"),
            ):
                page.click(mtab)
                time.sleep(0.22)
                geometry_reports.append(_check_layout_geometry(page, gname))

            # ===== C. Live /stage Projection HUD Audit + F10 Toggle =====
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(f"{stage_url}/stage", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('turn-meta').textContent.includes('Turn #')", timeout=10000)
            time.sleep(0.25)

            geometry_reports.append(_check_layout_geometry(page, "stage_hud_1920x1080"))
            p_hud = out_dir / "stage_hud_mode_b_cello.png"
            page.screenshot(path=str(p_hud))
            screenshots["stage_hud_mode_b_cello"] = str(p_hud)

            # F10 clean projection check using computed display === 'none'
            page.keyboard.press("F10")
            time.sleep(0.2)
            f10_hidden = page.evaluate("window.getComputedStyle(document.getElementById('control-bar')).display === 'none'")
            p_f10 = out_dir / "stage_projection_f10.png"
            page.screenshot(path=str(p_f10))
            screenshots["stage_projection_f10"] = str(p_f10)

            page.keyboard.press("F10")
            time.sleep(0.2)
            f10_restored = page.evaluate("window.getComputedStyle(document.getElementById('control-bar')).display !== 'none'")
            functional_checks["f10_toggle"] = {"hidden": f10_hidden, "restored": f10_restored}
            if not (f10_hidden and f10_restored):
                violations.append(f"F10 toggle check failed: hidden={f10_hidden}, restored={f10_restored}")

            page.set_viewport_size({"width": 1366, "height": 768})
            time.sleep(0.2)
            geometry_reports.append(_check_layout_geometry(page, "stage_hud_1366x768"))

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
        exp_dir = ROOT / "data" / "telemetry" / "exports"
        if exp_dir.exists():
            for pat in ("masterwork_*", "notagen_*"):
                for f in exp_dir.glob(pat):
                    try:
                        f.unlink()
                    except Exception:
                        pass

    _record_geometry_violations(geometry_reports, violations)

    if errors["console"]:
        violations.append(f"console.error count={len(errors['console'])}: {errors['console'][:3]}")
    if errors["pageerror"]:
        violations.append(f"pageerror count={len(errors['pageerror'])}: {errors['pageerror'][:3]}")
    if errors["http"]:
        violations.append(f"HTTP >=400 count={len(errors['http'])}: {errors['http'][:3]}")

    if update_docs_images:
        for key in (
            "studio_mode1_cello",
            "studio_mode2_piano",
            "studio_mode3_notagen",
            "studio_mode4_library",
            "stage_hud_mode_b_cello",
            "stage_projection_f10",
            "web_demo_1920x1080",
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
    parser = argparse.ArgumentParser(description="Playwright Real-Browser Audit for 4-Mode Studio, Stage HUD, & Web Demo")
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--update-docs-images", action="store_true")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    rep = run_audit(round_num=args.round, update_docs_images=args.update_docs_images, quick=args.quick)
    print(f"=== Playwright Audit Round {rep['round']} | passed={rep['passed']} ===")
    print(f"Errors: console={len(rep['errors']['console'])}, pageerror={len(rep['errors']['pageerror'])}, http={len(rep['errors']['http'])}")
    print(f"Functional: {rep['functional_checks']}")
    for g in rep["geometry_reports"]:
        print(
            f" - {g['label']}: overflow={g['overflowPx']}px clipped={len(g['clipped'])} "
            f"overlaps={len(g['overlaps'])} minFont={g['minVisibleFontPx']}px "
            f"smallBtns={len(g['smallPrimaryBtns'])} pedalOrderOk={g['pedalOrderOk']}"
        )
    if rep["violations"]:
        print("VIOLATIONS:")
        for v in rep["violations"]:
            print("  -", v)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
