"""
Playwright Real-Browser End-to-End, Design Rule, & Responsive Layout Auditor (`scripts/playwright_audit.py`).

Tests ALL THREE surfaces across Desktop (1920x1080, 1366x768) AND Smartphone (390x844):
1. Static GitHub Pages bundle (`web-demo/index.html` over clean HTTP)
2. Live FastAPI `/studio` across ALL 4 modes:
   - [1] Cello Dialogue (`#view-cello`)
   - [2] Piano Dialogue (`#view-piano` + interactive `#canvas-piano-keys`)
   - [3] NotaGen / LLM Live Streaming (`#view-notagen` 4-bar streaming check)
   - [4] Curated 100-Piece Library (`#view-library` filters, MP3, WebAudio, mixer, Deliberation cards)
3. Live `/stage` Projection HUD + `F10` Clean Projection Toggle

Enforces the 5 Mandatory Design Rules + Functional Integrity:
- Rule 1: Zero 4-sided card borders & zero `box-shadow` (bottom hairline underline + spacing only)
- Rule 2: Single flat gray background (`#1C1D20` / `#141517`), zero CSS gradients / pastels
- Rule 3: Subtle SVG geometric background shapes fixed by `:nth-child` (`opacity <= 0.08`)
- Rule 4: Zero auto card swapping or DOM destruction (5-second stability & `CLS < 0.01`)
- Rule 5: Minimum visible text computed font-size `>= 13.0px`
- Desktop overflow `<= 2px`, Mobile (`390x844`) overflow `=== 0px`, 0 clipped/overlapping controls
- Mobile fixed bottom bar `>= 56px` height & all 4 nav buttons `>= 48x48px`
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
        r"""(lbl) => {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const sw = document.documentElement.scrollWidth;
            const sh = document.documentElement.scrollHeight;
            const overflowPx = Math.max(0, sw - vw);

            const openModalDialog = document.querySelector('.modal-backdrop:not([hidden]) .modal-dialog');
            const scanRoot = openModalDialog || document.body;

            const isActuallyVisible = (el) => {
                if (!el || !el.isConnected) return false;
                if (el.closest('[hidden], .sr-only, .hero-watermark')) return false;
                if (el.classList.contains('skip-link') && document.activeElement !== el) return false;
                const closedDet = el.closest('details:not([open])');
                if (closedDet && !el.closest('summary')) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) <= 0.01) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0.5 && r.height > 0.5;
            };

            const isScrolledOut = (el, r) => {
                let cur = el.parentElement;
                while (cur && cur !== document.body && cur !== document.documentElement) {
                    if (cur.classList && cur.classList.contains('studio-shell')) break;
                    const cst = window.getComputedStyle(cur);
                    if (cst.overflowY === 'auto' || cst.overflowY === 'scroll' || cst.overflowY === 'hidden') {
                        const cr = cur.getBoundingClientRect();
                        if (r.bottom <= cr.top + 1 || r.top >= cr.bottom - 1) return true;
                    }
                    if (cst.overflowX === 'auto' || cst.overflowX === 'scroll' || cst.overflowX === 'hidden') {
                        const cr = cur.getBoundingClientRect();
                        if (r.right <= cr.left + 1 || r.left >= cr.right - 1) return true;
                    }
                    cur = cur.parentElement;
                }
                return false;
            };

            const clipToScrollAncestors = (el, raw) => {
                let left = raw.left, top = raw.top, right = raw.right, bottom = raw.bottom;
                let cur = el.parentElement;
                while (cur && cur !== document.body && cur !== document.documentElement) {
                    if (cur.classList && cur.classList.contains('studio-shell')) break;
                    const cst = window.getComputedStyle(cur);
                    if (['auto', 'scroll', 'hidden', 'clip'].includes(cst.overflowY)) {
                        const cr = cur.getBoundingClientRect();
                        top = Math.max(top, cr.top);
                        bottom = Math.min(bottom, cr.bottom);
                    }
                    if (['auto', 'scroll', 'hidden', 'clip'].includes(cst.overflowX)) {
                        const cr = cur.getBoundingClientRect();
                        left = Math.max(left, cr.left);
                        right = Math.min(right, cr.right);
                    }
                    cur = cur.parentElement;
                }
                return { left, top, right, bottom };
            };

            const controls = Array.from(scanRoot.querySelectorAll('button, a.btn, input, select, textarea'))
                .filter((el) => {
                    if (el.classList.contains('skip-link')) return false;
                    if (!isActuallyVisible(el)) return false;
                    const r = el.getBoundingClientRect();
                    return !isScrolledOut(el, r);
                });

            const clipped = [];
            const rects = [];
            const smallPrimaryBtns = [];

            const inFixedNav = (el) => Boolean(el.closest('.mobile-bottom-nav'));
            const shellEl = document.querySelector('.studio-shell');
            const shellClipRect = (shellEl && ['auto', 'scroll', 'hidden', 'clip'].includes(window.getComputedStyle(shellEl).overflowY))
                ? shellEl.getBoundingClientRect()
                : null;

            for (const el of controls) {
                const raw = el.getBoundingClientRect();
                if ((el.classList.contains('mode-tab') || (el.classList.contains('btn') && !el.classList.contains('compact'))) && raw.height < 40) {
                    smallPrimaryBtns.push({ id: el.id || el.textContent.trim().slice(0, 20), height: Math.round(raw.height) });
                }

                const cbox = clipToScrollAncestors(el, raw);
                if (cbox.right - cbox.left <= 1 || cbox.bottom - cbox.top <= 1) continue;
                if (raw.right > vw + 2 || raw.left < -2) {
                    clipped.push({ id: el.id || el.textContent.trim().slice(0, 20), left: Math.round(raw.left), right: Math.round(raw.right), vw });
                }
                rects.push({ el, id: el.id || el.className || el.tagName, fixedNav: inFixedNav(el), r: cbox });
            }

            const overlaps = [];
            for (let i = 0; i < rects.length; i++) {
                for (let j = i + 1; j < rects.length; j++) {
                    if (rects[i].el.contains(rects[j].el) || rects[j].el.contains(rects[i].el)) continue;
                    // Elements inside overflowing .studio-shell are clipped above fixed bottom nav; check against shellClipRect instead
                    if (rects[i].fixedNav !== rects[j].fixedNav && shellClipRect) {
                        const navR = rects[i].fixedNav ? rects[i].r : rects[j].r;
                        const nix = Math.max(0, Math.min(navR.right, shellClipRect.right) - Math.max(navR.left, shellClipRect.left));
                        const niy = Math.max(0, Math.min(navR.bottom, shellClipRect.bottom) - Math.max(navR.top, shellClipRect.top));
                        if (nix <= 2 || niy <= 2) continue;
                    }
                    const a = rects[i].r;
                    const b = rects[j].r;
                    const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                    const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                    if (ix > 3 && iy > 3) {
                        overlaps.push({ a: rects[i].id, b: rects[j].id, ix: Math.round(ix), iy: Math.round(iy) });
                    }
                }
            }

            // Comprehensive DOM pairwise non-ancestor overlap audit (controls + text nodes + scores + keyboards)
            const inToast = (el) => Boolean(el.closest('.export-toast'));
            const domItems = [];
            const seenDom = new Set();
            const addDomBox = (el, kind) => {
                if (!el || seenDom.has(el) || el.classList.contains('skip-link')) return;
                if (!isActuallyVisible(el)) return;
                const raw = el.getBoundingClientRect();
                if (isScrolledOut(el, raw)) return;
                const cbox = clipToScrollAncestors(el, raw);
                if (cbox.right - cbox.left <= 2 || cbox.bottom - cbox.top <= 2) return;
                seenDom.add(el);
                domItems.push({
                    el,
                    kind,
                    id: (el.id ? '#' + el.id : el.className ? String(el.className).split(/\s+/)[0] : el.tagName) + ':' + el.textContent.trim().slice(0, 18),
                    display: window.getComputedStyle(el).display,
                    parent: el.parentElement,
                    parentDisplay: el.parentElement ? window.getComputedStyle(el.parentElement).display : 'block',
                    fixedNav: inFixedNav(el),
                    toast: inToast(el),
                    r: cbox,
                });
            };
            for (const c of controls) addDomBox(c, 'ctrl');
            for (const media of scanRoot.querySelectorAll('svg.grand-staff-svg, canvas')) addDomBox(media, 'media');
            for (const el of scanRoot.querySelectorAll('*')) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS', 'SVG'].includes(el.tagName) || el.closest('svg')) continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (hasDirectText) addDomBox(el, 'text');
            }

            const scrollHost = document.querySelector('.studio-shell');
            const shEffective = scrollHost ? Math.max(sh, scrollHost.scrollHeight) : sh;
            const vhEffective = scrollHost ? scrollHost.clientHeight : vh;
            const atDocBottom = (scrollHost && scrollHost.scrollHeight > scrollHost.clientHeight + 4)
                ? (scrollHost.scrollTop + scrollHost.clientHeight >= scrollHost.scrollHeight - 2)
                : ((window.scrollY + vh) >= (sh - 2));
            const domOverlaps = [];
            for (let i = 0; i < domItems.length; i++) {
                const A = domItems[i];
                for (let j = i + 1; j < domItems.length; j++) {
                    const B = domItems[j];
                    if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
                    // On a vertically scrolling page, fixed bottom bar intentionally floats over mid-page scroll region until bottom
                    if (A.fixedNav !== B.fixedNav && shEffective > vhEffective + 4 && !atDocBottom) continue;
                    if (A.toast !== B.toast && (A.fixedNav || B.fixedNav)) continue;
                    if (
                        A.parent === B.parent &&
                        ['block', 'list-item'].includes(A.parentDisplay) &&
                        A.display === 'inline' &&
                        B.display === 'inline'
                    ) {
                        continue;
                    }
                    const ix = Math.max(0, Math.min(A.r.right, B.r.right) - Math.max(A.r.left, B.r.left));
                    const iy = Math.max(0, Math.min(A.r.bottom, B.r.bottom) - Math.max(A.r.top, B.r.top));
                    if (ix > 2.0 && iy > 2.0) {
                        domOverlaps.push({ a: A.id, b: B.id, ix: Number(ix.toFixed(1)), iy: Number(iy.toFixed(1)) });
                    }
                }
            }

            // Font size audit on visible text nodes (Rule 5: strictly >= 13.0px)
            let minVisibleFontPx = 999;
            const smallFontEls = [];
            const allTextContainers = Array.from(scanRoot.querySelectorAll('*'));
            for (const el of allTextContainers) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS'].includes(el.tagName) || el.closest('svg')) continue;
                if (!isActuallyVisible(el)) continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (!hasDirectText) continue;
                const fs = parseFloat(window.getComputedStyle(el).fontSize || '15');
                if (fs < minVisibleFontPx) minVisibleFontPx = fs;
                if (fs < 13.0) {
                    smallFontEls.push({ tag: el.tagName, id: el.id || el.className, fs, text: el.textContent.trim().slice(0, 24) });
                }
            }

            // SVG Grand Staff internal geometry & collision audit
            const svgAudit = {
                svgCount: 0,
                brokenSvgs: [],
                svgSmallFonts: [],
                svgTextClipped: [],
                svgTextOverlaps: [],
                svgTextNoteOverlaps: [],
                svgClefNoteOverlaps: [],
            };
            const rectOverlap = (a, b, pad = 0) => {
                const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                return { ix, iy, hit: ix > pad && iy > pad };
            };
            for (const svg of scanRoot.querySelectorAll('svg.grand-staff-svg')) {
                if (!isActuallyVisible(svg)) continue;
                const sr = svg.getBoundingClientRect();
                if (isScrolledOut(svg, sr)) continue;
                svgAudit.svgCount += 1;
                const hostId = svg.parentElement?.id || svg.parentElement?.className || 'grand-staff';
                const hasEmpty = Boolean(svg.querySelector('.score-empty-text'));
                const staves = Array.from(svg.querySelectorAll('.vf-stave'));
                const clefs = Array.from(svg.querySelectorAll('.vf-clef'));
                const notes = Array.from(svg.querySelectorAll('.vf-notehead, .vf-stavenote'));
                if (sr.width < 50 || sr.height < 80 || staves.length < 2 || clefs.length < 2 || (!hasEmpty && notes.length < 1)) {
                    svgAudit.brokenSvgs.push({
                        hostId,
                        w: Math.round(sr.width),
                        h: Math.round(sr.height),
                        staves: staves.length,
                        clefs: clefs.length,
                        notes: notes.length,
                    });
                }

                // Collect literal horizontal staff lines + notation primitives
                const staffLineBoxes = [];
                for (const st of staves) {
                    for (const shape of st.querySelectorAll('path, rect, line')) {
                        const br = shape.getBoundingClientRect();
                        if (br.height <= 4.5 && br.width > 18) staffLineBoxes.push(br);
                    }
                }
                const notationBoxes = [];
                for (const nEl of svg.querySelectorAll('.vf-notehead, .vf-accidental, .vf-stem, .vf-beam, .vf-clef, .vf-keysig, .vf-timesig, rect.ledger-line, line.ledger-line')) {
                    const br = nEl.getBoundingClientRect();
                    if (br.width > 0.5 && br.height > 0.5) notationBoxes.push({ tag: nEl.getAttribute('class') || nEl.tagName, r: br });
                }

                const svgTexts = Array.from(svg.querySelectorAll('text')).filter((t) => {
                    const st = window.getComputedStyle(t);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    const tr = t.getBoundingClientRect();
                    return t.textContent.trim().length > 0 && tr.width > 0.5 && tr.height > 0.5;
                });

                const tBoxes = svgTexts.map((t) => ({
                    el: t,
                    cls: t.getAttribute('class') || '',
                    text: t.textContent.trim().slice(0, 28),
                    fs: parseFloat(window.getComputedStyle(t).fontSize || '0'),
                    r: t.getBoundingClientRect(),
                }));

                for (const tb of tBoxes) {
                    if (tb.fs < 13.0) {
                        svgAudit.svgSmallFonts.push({ hostId, fs: tb.fs, text: tb.text, cls: tb.cls });
                    }
                    if (tb.r.left < sr.left - 1.5 || tb.r.right > sr.right + 1.5 || tb.r.top < sr.top - 1.5 || tb.r.bottom > sr.bottom + 1.5) {
                        svgAudit.svgTextClipped.push({
                            hostId,
                            text: tb.text,
                            tr: { l: Math.round(tb.r.left), r: Math.round(tb.r.right), t: Math.round(tb.r.top), b: Math.round(tb.r.bottom) },
                            sr: { l: Math.round(sr.left), r: Math.round(sr.right), t: Math.round(sr.top), b: Math.round(sr.bottom) },
                        });
                    }
                    if (!tb.el.classList.contains('score-empty-text')) {
                        for (const sline of staffLineBoxes) {
                            const ov = rectOverlap(tb.r, sline, 0.8);
                            if (ov.hit) {
                                svgAudit.svgTextNoteOverlaps.push({ hostId, text: tb.text, target: 'staff-line', ix: Math.round(ov.ix), iy: Math.round(ov.iy) });
                                break;
                            }
                        }
                        for (const nb of notationBoxes) {
                            const ov = rectOverlap(tb.r, nb.r, 1.0);
                            if (ov.hit) {
                                svgAudit.svgTextNoteOverlaps.push({ hostId, text: tb.text, target: nb.tag, ix: Math.round(ov.ix), iy: Math.round(ov.iy) });
                                break;
                            }
                        }
                    }
                }

                for (let i = 0; i < tBoxes.length; i++) {
                    for (let j = i + 1; j < tBoxes.length; j++) {
                        const ov = rectOverlap(tBoxes[i].r, tBoxes[j].r, 1.0);
                        if (ov.hit) {
                            svgAudit.svgTextOverlaps.push({ hostId, a: tBoxes[i].text, b: tBoxes[j].text, ix: Math.round(ov.ix), iy: Math.round(ov.iy) });
                        }
                    }
                }

                // Clef / KeySig / TimeSig separation from notes & accidentals
                const headerMods = Array.from(svg.querySelectorAll('.vf-clef, .vf-keysig, .vf-timesig')).map((m) => m.getBoundingClientRect());
                const firstNotesAndAcc = Array.from(svg.querySelectorAll('.vf-notehead, .vf-accidental')).map((n) => n.getBoundingClientRect());
                for (const hm of headerMods) {
                    if (hm.width < 1 || hm.height < 1) continue;
                    for (const fn of firstNotesAndAcc) {
                        const ov = rectOverlap(hm, fn, 0.5);
                        if (ov.hit) {
                            svgAudit.svgClefNoteOverlaps.push({ hostId, ix: Math.round(ov.ix), iy: Math.round(ov.iy) });
                        }
                    }
                }
            }

            // Text truncation & container clipping detection
            const truncationIssues = [];
            for (const el of scanRoot.querySelectorAll('*')) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS', 'PRE', 'INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) || el.closest('svg')) continue;
                if (!isActuallyVisible(el)) continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (!hasDirectText) continue;
                const st = window.getComputedStyle(el);
                if ((st.overflowX === 'hidden' || st.overflowX === 'clip' || st.textOverflow === 'ellipsis') && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 3) {
                    truncationIssues.push({
                        id: el.id || el.className || el.tagName,
                        scrollW: el.scrollWidth,
                        clientW: el.clientWidth,
                        text: el.textContent.trim().slice(0, 28),
                    });
                } else if ((st.overflowY === 'hidden' || st.overflowY === 'clip') && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 4) {
                    truncationIssues.push({
                        id: el.id || el.className || el.tagName,
                        scrollH: el.scrollHeight,
                        clientH: el.clientHeight,
                        text: el.textContent.trim().slice(0, 28),
                    });
                }
            }

            // WCAG AA Contrast audit for visible HTML text
            const parseRgba = (str) => {
                const m = String(str || '').match(/rgba?\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)(?:,\s*(\d+(?:\.\d+)?))?\)/);
                if (!m) return null;
                return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: m[4] != null ? Number(m[4]) : 1.0 };
            };
            const blend = (bg, fg) => ({
                r: fg.r * fg.a + bg.r * (1 - fg.a),
                g: fg.g * fg.a + bg.g * (1 - fg.a),
                b: fg.b * fg.a + bg.b * (1 - fg.a),
                a: 1.0,
            });
            const effectiveBg = (el) => {
                const chain = [];
                let cur = el;
                while (cur && cur.nodeType === 1) {
                    const c = parseRgba(window.getComputedStyle(cur).backgroundColor);
                    if (c && c.a > 0.01) chain.push(c);
                    cur = cur.parentElement;
                }
                let col = { r: 28, g: 29, b: 32, a: 1.0 };
                for (let k = chain.length - 1; k >= 0; k--) col = blend(col, chain[k]);
                return col;
            };
            const relLum = (c) => {
                const f = (v) => {
                    const s = v / 255.0;
                    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
                };
                return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
            };
            const contrastIssues = [];
            for (const el of scanRoot.querySelectorAll('*')) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS'].includes(el.tagName) || el.closest('svg') || el.disabled) continue;
                if (!isActuallyVisible(el) || el.getAttribute('aria-hidden') === 'true') continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (!hasDirectText) continue;
                const st = window.getComputedStyle(el);
                if (Number(st.opacity) < 0.2) continue;
                const fg = parseRgba(st.color);
                if (!fg) continue;
                const bg = effectiveBg(el);
                const fgComp = blend(bg, fg);
                const l1 = relLum(fgComp);
                const l2 = relLum(bg);
                const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
                const fs = parseFloat(st.fontSize || '15');
                const fw = Number(st.fontWeight || 400);
                const minRatio = (fs >= 24 || (fs >= 18.66 && fw >= 700)) ? 3.0 : 4.5;
                if (ratio < minRatio) {
                    contrastIssues.push({
                        id: el.id || el.className || el.tagName,
                        ratio: Number(ratio.toFixed(2)),
                        minRatio,
                        color: st.color,
                        bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`,
                        text: el.textContent.trim().slice(0, 22),
                    });
                }
            }

            // Raw variable / enum / garbled text leak audit (excluding raw ABC pre console)
            const rawLeakIssues = [];
            const leakPatterns = [
                '\\bundefined\\b',
                '\\bNaN\\b',
                '\\[object Object\\]',
                'monologue_ja',
                'step2_audition_reject',
                'question_half_cadence',
                'answer_authentic_cadence',
                'tension_deceptive',
                'climax_dominant_pedal',
                'coda_plagal_fade',
                'D_dorian',
                'D_minor',
                'F_major',
                'G_minor',
                'A_minor',
                'notagen_small',
                'L1_PERCEPTION',
                'TOKEN_ECON',
                'L2_LOGIC_CHAIN',
                'L3_CANDIDATES',
                'REALIZER_DIFF',
                'TIMING_BUDGET',
                'broken_chord_barcarolle',
                'cantabile_polyphony',
                'impressionist_pedal',
                'dramatic_octave_chorale',
                'minimalist_pulse',
                'countermelody_tenor',
                'open_arpeggio_10th',
                'chorale_voicing',
                'alberti_lyrical',
                'webaudio_demo',
                'mac_local',
                'browser_abc_demo',
                'LH_open5th',
                'RH_cantabile',
                'half_clear',
                '\\(#A8C8E8\\)',
                '\\(#EAE6DF\\)',
                '\\biv66\\b',
                '\\bii66\\b',
                '大調',
                '小調',
                '嘆き',
                '叫び',
                '黒箱',
                '事実記述',
                '事実のみ記す',
                '-inf dB',
                'Mode B:',
                'Mode A:',
                'Turn #',
                '\\uFFFD',
                '□',
            ];
            const leakRegex = new RegExp('(' + leakPatterns.join('|') + ')', 'g');
            const asciiAccRegex = /(?:^|[\s(/\-\–\→\[,])([A-G](?:#|b)(?:m7|maj7|dim7|sus4|m|7|9|6|[0-8])?)(?=$|[\s)/,\-\–\→\]・])/g;
            const textHolders = Array.from(scanRoot.querySelectorAll('*'));
            for (const el of textHolders) {
                if (['SCRIPT', 'STYLE', 'OPTION', 'AUDIO', 'CANVAS'].includes(el.tagName)) continue;
                if (el.id === 'ng-abc-console' || el.closest('#ng-abc-console')) continue;
                if (!isActuallyVisible(el)) continue;
                const hasDirectText = Array.from(el.childNodes).some(
                    (n) => n.nodeType === Node.TEXT_NODE && n.textContent && n.textContent.trim().length > 0
                );
                if (!hasDirectText) continue;
                const txt = el.textContent.trim();
                const m1 = txt.match(leakRegex);
                if (m1) {
                    rawLeakIssues.push({ id: el.id || el.className || el.tagName, token: m1[0], snippet: txt.slice(0, 40) });
                }
                const m2 = txt.match(asciiAccRegex);
                if (m2) {
                    rawLeakIssues.push({ id: el.id || el.className || el.tagName, token: m2[0].trim(), snippet: txt.slice(0, 40) });
                }
            }

            // Rule 1 & Rule 2 & Rule 3 audits
            const cardSelectors = '.card, .panel, .zone-a, .metric-tile, .tile, .pedal-box, .pedal-card, .cand-pick-card, .cand-card, .piece-item, .mixer-row, .topbar, .workspace-strip, .score-card, .feat-card, .trace-stage, .trace-cand-row';
            const cardBorderShadowViolations = [];
            const gradientViolations = [];
            let svgShapeCardsCount = 0;

            for (const el of document.querySelectorAll(cardSelectors)) {
                if (!isActuallyVisible(el)) continue;
                const st = window.getComputedStyle(el);
                if (st.boxShadow && st.boxShadow !== 'none') {
                    cardBorderShadowViolations.push({ el: el.className || el.id, issue: 'boxShadow=' + st.boxShadow });
                }
                const bt = parseFloat(st.borderTopWidth || '0');
                const bl = parseFloat(st.borderLeftWidth || '0');
                const br = parseFloat(st.borderRightWidth || '0');
                if (bt > 0.1 || bl > 0.1 || br > 0.1) {
                    cardBorderShadowViolations.push({ el: el.className || el.id, issue: `4-sided border T=${bt} L=${bl} R=${br}` });
                }
                if (st.backgroundImage && /gradient\(/i.test(st.backgroundImage)) {
                    gradientViolations.push({ el: el.className || el.id, bg: st.backgroundImage.slice(0, 60) });
                }
                const bef = window.getComputedStyle(el, '::before');
                if (bef && bef.backgroundImage && bef.backgroundImage.includes('data:image/svg+xml')) {
                    const op = parseFloat(bef.opacity || '0');
                    if (op > 0 && op <= 0.08) {
                        svgShapeCardsCount += 1;
                    }
                }
            }

            // Rule 2: Scan all elements & stylesheets for any CSS gradients and check flat gray body
            const bodyBg = window.getComputedStyle(document.body).backgroundColor;
            const validGrayBgs = ['rgb(28, 29, 32)', 'rgb(20, 21, 23)'];
            const bodyBgOk = validGrayBgs.includes(bodyBg);
            for (const ss of Array.from(document.styleSheets)) {
                try {
                    for (const rule of Array.from(ss.cssRules || [])) {
                        if (rule.cssText && /gradient\(/i.test(rule.cssText)) {
                            gradientViolations.push({ stylesheet: ss.href || 'inline', snippet: rule.cssText.slice(0, 60) });
                        }
                    }
                } catch (_) {}
            }

            // Mobile bottom navigation geometry check (when vw <= 768)
            const mobileNav = document.querySelector('.mobile-bottom-nav');
            let mobileNavReport = null;
            if (vw <= 768 && mobileNav) {
                const mRect = mobileNav.getBoundingClientRect();
                const mBtns = Array.from(mobileNav.querySelectorAll('.mobile-nav-btn')).map((b) => {
                    const r = b.getBoundingClientRect();
                    return { mode: b.dataset.modeView || b.dataset.mode || '', width: Math.round(r.width), height: Math.round(r.height) };
                });
                mobileNavReport = {
                    visible: isActuallyVisible(mobileNav),
                    height: Math.round(mRect.height),
                    buttons: mBtns,
                };
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
                domOverlaps,
                minVisibleFontPx: minVisibleFontPx === 999 ? 15 : minVisibleFontPx,
                smallFontEls,
                smallPrimaryBtns,
                svgAudit,
                truncationIssues,
                contrastIssues,
                rawLeakIssues,
                cardBorderShadowViolations,
                gradientViolations,
                bodyBg,
                bodyBgOk,
                svgShapeCardsCount,
                mobileNavReport,
                pedalOrderOk,
                pedalPairs,
            };
        }""",
        label,
    )


def _check_card_stability(page: Page, label: str, container_selector: str, wait_s: float = 5.0) -> Dict[str, Any]:
    """Rule 4: Verify 0 automatic card reordering / DOM recreation and CLS < 0.01 over 5s."""
    page.evaluate(
        """(sel) => {
            window.__clsTotal = 0;
            const wrap = document.querySelector(sel);
            window.__cardInitRefs = wrap ? Array.from(wrap.children) : [];
            window.__cardInitKeys = window.__cardInitRefs.map((c, idx) => c.dataset.cid || c.dataset.slot || String(idx));
            try {
                window.__clsObs = new PerformanceObserver((list) => {
                    for (const entry of list.getEntries()) {
                        if (!entry.hadRecentInput) {
                            window.__clsTotal += entry.value || 0;
                        }
                    }
                });
                window.__clsObs.observe({ type: 'layout-shift', buffered: false });
            } catch (_) {}
        }""",
        container_selector,
    )
    time.sleep(wait_s)
    res = page.evaluate(
        """(sel) => {
            if (window.__clsObs) {
                try { window.__clsObs.disconnect(); } catch (_) {}
            }
            const wrap = document.querySelector(sel);
            const curRefs = wrap ? Array.from(wrap.children) : [];
            const curKeys = curRefs.map((c, idx) => c.dataset.cid || c.dataset.slot || String(idx));
            let swaps = 0;
            let recreated = 0;
            const n = Math.min(window.__cardInitRefs.length, curRefs.length);
            for (let i = 0; i < n; i++) {
                if (window.__cardInitKeys[i] !== curKeys[i]) swaps += 1;
                if (window.__cardInitRefs[i] !== curRefs[i]) recreated += 1;
            }
            return {
                label: sel,
                count: curRefs.length,
                swaps,
                recreated,
                cls: Number((window.__clsTotal || 0).toFixed(5)),
            };
        }""",
        container_selector,
    )
    res["surface"] = label
    return res


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
        is_mobile = int(g.get("vw", 1920)) <= 768
        max_ov = 0 if is_mobile else 2
        if g["overflowPx"] > max_ov:
            violations.append(f"{g['label']}: horizontal overflow {g['overflowPx']}px (limit {max_ov}px)")
        if g["clipped"]:
            violations.append(f"{g['label']}: clipped controls {g['clipped'][:3]}")
        if g["overlaps"]:
            violations.append(f"{g['label']}: overlapping controls {g['overlaps'][:3]}")
        if g.get("domOverlaps"):
            violations.append(f"{g['label']}: DOM element overlaps ({len(g['domOverlaps'])}) {g['domOverlaps'][:3]}")
        if g["minVisibleFontPx"] < 13.0 or g["smallFontEls"]:
            violations.append(f"{g['label']}: font < 13px ({g['smallFontEls'][:3]})")
        if g["smallPrimaryBtns"]:
            violations.append(f"{g['label']}: small primary buttons < 40px ({g['smallPrimaryBtns'][:3]})")
        svg_aud = g.get("svgAudit") or {}
        for svg_key in ("brokenSvgs", "svgSmallFonts", "svgTextClipped", "svgTextOverlaps", "svgTextNoteOverlaps", "svgClefNoteOverlaps"):
            if svg_aud.get(svg_key):
                violations.append(f"{g['label']}: SVG {svg_key} ({svg_aud[svg_key][:3]})")
        if g.get("truncationIssues"):
            violations.append(f"{g['label']}: text truncation/clipping {g['truncationIssues'][:3]}")
        if g.get("contrastIssues"):
            violations.append(f"{g['label']}: contrast < AA ({g['contrastIssues'][:3]})")
        if g.get("rawLeakIssues"):
            violations.append(f"{g['label']}: raw token/notation leak {g['rawLeakIssues'][:3]}")
        if g.get("cardBorderShadowViolations"):
            violations.append(f"{g['label']}: Rule 1 card border/shadow violation ({g['cardBorderShadowViolations'][:3]})")
        if not g.get("bodyBgOk", True):
            violations.append(f"{g['label']}: Rule 2 non-gray body background {g.get('bodyBg')}")
        if g.get("gradientViolations"):
            violations.append(f"{g['label']}: Rule 2 gradient violation ({g['gradientViolations'][:3]})")
        if g.get("svgShapeCardsCount", 0) < 1:
            violations.append(f"{g['label']}: Rule 3 expected >= 1 SVG geometric shape card")
        mnav = g.get("mobileNavReport")
        if is_mobile and mnav is not None:
            if not mnav.get("visible") or int(mnav.get("height", 0)) < 56:
                violations.append(f"{g['label']}: mobile bottom nav invalid ({mnav})")
            bad_mbtns = [b for b in mnav.get("buttons", []) if b["width"] < 48 or b["height"] < 48]
            if len(mnav.get("buttons", [])) < 4 or bad_mbtns:
                violations.append(f"{g['label']}: mobile nav buttons < 48x48 ({mnav})")
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

            # Offline Mode 1 Cello Logic Trace & Mode 2 Piano test inside static bundle
            page.click("#mode-tab-cello")
            time.sleep(0.2)
            page.click("#tab-cello-trace")
            time.sleep(0.2)
            wd_trace_ok = page.evaluate(
                """() => {
                    const p = document.getElementById('panel-cello-trace');
                    if (!p || p.hidden) return false;
                    return ['cello-tr-l1-body', 'cello-tr-tok-body', 'cello-tr-l2-body', 'cello-tr-c-A', 'cello-tr-rz-body', 'cello-tr-tm-body']
                        .every((id) => (document.getElementById(id)?.textContent || '').trim().length > 0);
                }"""
            )
            functional_checks["web_demo_logic_trace"] = bool(wd_trace_ok)
            if not wd_trace_ok:
                violations.append("web-demo Logic Trace 6-stage panel failed to render cleanly")
            page.click("#tab-cello-score")
            time.sleep(0.15)
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

            # Art Direction checklist checks: Web fonts, Skip-link, Modal focus/Escape, prefers-reduced-motion
            fonts_info = page.evaluate(
                """async () => {
                    await document.fonts.ready;
                    const loaded = Array.from(document.fonts).filter((f) => f.status === 'loaded').map((f) => f.family);
                    return { total: document.fonts.size, loadedCount: loaded.length, families: Array.from(new Set(loaded)).slice(0, 8) };
                }"""
            )
            functional_checks["fonts"] = fonts_info

            skip_link_ok = page.evaluate(
                """() => {
                    const sk = document.querySelector('.skip-link');
                    const mainEl = document.getElementById('main-content');
                    return Boolean(sk && sk.getAttribute('href') === '#main-content' && mainEl);
                }"""
            )
            functional_checks["skip_link_ok"] = bool(skip_link_ok)
            if not skip_link_ok:
                violations.append("Skip-link or #main-content missing on /studio")

            # Help Modal a11y + geometry + Escape check
            page.click("#btn-help")
            time.sleep(0.18)
            modal_opened_focus = page.evaluate(
                "() => ({ open: !document.getElementById('help-modal')?.hidden, activeId: document.activeElement?.id || '' })"
            )
            geometry_reports.append(_check_layout_geometry(page, "studio_help_modal_1920x1080"))
            page.keyboard.press("Escape")
            time.sleep(0.15)
            modal_closed_focus = page.evaluate(
                "() => ({ closed: Boolean(document.getElementById('help-modal')?.hidden), activeId: document.activeElement?.id || '' })"
            )
            modal_a11y = {
                "opened": modal_opened_focus["open"],
                "focus_moved_to_close": modal_opened_focus["activeId"] == "btn-help-close",
                "closed_on_escape": modal_closed_focus["closed"],
                "focus_restored_to_trigger": modal_closed_focus["activeId"] == "btn-help",
            }
            functional_checks["modal_a11y"] = modal_a11y
            if not all(modal_a11y.values()):
                violations.append(f"Modal keyboard a11y failed: {modal_a11y}")

            page.emulate_media(reduced_motion="reduce")
            time.sleep(0.1)
            rm_dur = page.evaluate(
                """() => {
                    const el = document.querySelector('.breath-fill') || document.body;
                    const raw = String(window.getComputedStyle(el).transitionDuration || '0s').split(',')[0].trim();
                    if (raw.endsWith('ms')) return parseFloat(raw) / 1000.0;
                    return parseFloat(raw) || 0.0;
                }"""
            )
            page.emulate_media(reduced_motion="no-preference")
            rm_ok = float(rm_dur) <= 0.002
            functional_checks["reduced_motion"] = {"transition_duration_s": rm_dur, "ok": rm_ok}
            if not rm_ok:
                violations.append(f"prefers-reduced-motion check failed: transitionDuration={rm_dur}s")

            # Mode 1: Cello Dialogue + 5s Card Stability Check (Rule 4) + Rejected Candidate + Export Toast + Logic Trace
            init_cand_lbl = page.locator("#cello-hero-cand-label").inner_text()
            functional_checks["initial_adopted_selected"] = "採用候補" in init_cand_lbl
            if not functional_checks["initial_adopted_selected"]:
                violations.append(f"/studio initial load did not select adopted candidate: {init_cand_lbl}")
            geometry_reports.append(_check_layout_geometry(page, "studio_mode1_cello_1920x1080"))
            stability_report = _check_card_stability(page, "studio_mode1_cello", "#cello-cand-cards", wait_s=1.2 if quick else 5.0)
            functional_checks["card_stability"] = stability_report
            if stability_report["swaps"] > 0 or stability_report["recreated"] > 0 or stability_report["cls"] >= 0.01:
                violations.append(f"Rule 4 card stability violation: {stability_report}")
            p_m1 = out_dir / "studio_mode1_cello.png"
            page.screenshot(path=str(p_m1))
            screenshots["studio_mode1_cello"] = str(p_m1)
            screenshots["desktop_mode1_cello"] = str(p_m1)

            if page.locator("#cello-cand-cards .cand-pick-card.rejected").count() > 0:
                page.locator("#cello-cand-cards .cand-pick-card.rejected").first.click()
                time.sleep(0.22)
                geometry_reports.append(_check_layout_geometry(page, "studio_mode1_rejected_1920x1080"))
                if page.locator("#cello-cand-cards .cand-pick-card.adopted").count() > 0:
                    page.locator("#cello-cand-cards .cand-pick-card.adopted").first.click()
                    time.sleep(0.18)

            page.click("#btn-cello-save")
            page.wait_for_function("!document.getElementById('cello-export-toast')?.hidden", timeout=15000)
            time.sleep(0.15)
            geometry_reports.append(_check_layout_geometry(page, "studio_toast_state_1920x1080"))
            if page.locator("#cello-export-toast-close").count() > 0:
                page.click("#cello-export-toast-close")
                time.sleep(0.1)

            page.click("#tab-cello-trace")
            time.sleep(0.2)
            studio_trace_ok = page.evaluate(
                """() => {
                    const p = document.getElementById('panel-cello-trace');
                    if (!p || p.hidden) return false;
                    return ['cello-tr-l1-body', 'cello-tr-tok-body', 'cello-tr-l2-body', 'cello-tr-c-A', 'cello-tr-rz-body', 'cello-tr-tm-body']
                        .every((id) => (document.getElementById(id)?.textContent || '').trim().length > 0);
                }"""
            )
            functional_checks["studio_logic_trace_ok"] = bool(studio_trace_ok)
            if not studio_trace_ok:
                violations.append("/studio Logic Trace 6-stage panel failed to populate")
            geometry_reports.append(_check_layout_geometry(page, "studio_logic_trace_1920x1080"))
            p_lt = out_dir / "logic_trace_inspector.png"
            page.screenshot(path=str(p_lt))
            screenshots["logic_trace_inspector"] = str(p_lt)
            page.click("#tab-cello-score")
            time.sleep(0.15)

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
            screenshots["desktop_mode2_piano"] = str(p_m2)

            # Mode 3: NotaGen / LLM Live Streaming (4-bar notagen_small quantized test)
            page.click("#mode-tab-notagen")
            time.sleep(0.2)
            page.select_option("#sel-ng-backend", "notagen_small")
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
            screenshots["desktop_mode3_notagen"] = str(p_m3)

            # Mode 4: Library Archive (initial + active playback + deliberation tab)
            page.click("#mode-tab-library")
            page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=10000)
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(page, "studio_mode4_library_1920x1080"))
            p_m4 = out_dir / "studio_mode4_library.png"
            page.screenshot(path=str(p_m4))
            screenshots["studio_mode4_library"] = str(p_m4)
            screenshots["desktop_mode4_library"] = str(p_m4)

            page.click("#btn-play")
            time.sleep(0.35)
            geometry_reports.append(_check_layout_geometry(page, "studio_mode4_playing_1920x1080"))
            page.click("#btn-stop")
            page.click("#tab-delib")
            page.wait_for_selector("#delib-grid .cand-card svg.grand-staff-svg", timeout=8000)
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(page, "studio_mode4_delib_1920x1080"))
            page.click("#tab-score")
            time.sleep(0.2)

            # Check Studio Modes 1, 2, 3, 4 at 1366x768 laptop resolution
            page.set_viewport_size({"width": 1366, "height": 768})
            for mtab, gname in (
                ("#mode-tab-cello", "studio_mode1_cello_1366x768"),
                ("#mode-tab-piano", "studio_mode2_piano_1366x768"),
                ("#mode-tab-notagen", "studio_mode3_notagen_1366x768"),
                ("#mode-tab-library", "studio_mode4_library_1366x768"),
            ):
                page.click(mtab)
                time.sleep(0.25)
                geometry_reports.append(_check_layout_geometry(page, gname))
                if mtab == "#mode-tab-cello":
                    p_1366 = out_dir / "studio_mode1_cello_1366x768.png"
                    page.screenshot(path=str(p_1366))
                    screenshots["studio_mode1_cello_1366x768"] = str(p_1366)

            # ===== C. Live /stage Projection HUD Audit + F10 Toggle =====
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.goto(f"{stage_url}/stage", wait_until="domcontentloaded")
            page.wait_for_function("document.getElementById('turn-meta').textContent.includes('ターン')", timeout=10000)
            time.sleep(0.25)

            geometry_reports.append(_check_layout_geometry(page, "stage_hud_1920x1080"))
            p_hud = out_dir / "stage_hud_mode_b_cello.png"
            page.screenshot(path=str(p_hud))
            screenshots["stage_hud_mode_b_cello"] = str(p_hud)

            page.click("#tab-hud-trace")
            time.sleep(0.2)
            hud_trace_ok = page.evaluate(
                """() => {
                    const p = document.getElementById('panel-hud-trace');
                    if (!p || p.hidden) return false;
                    return ['hud-tr-l1-body', 'hud-tr-tok-body', 'hud-tr-l2-body', 'hud-tr-c-A', 'hud-tr-rz-body', 'hud-tr-tm-body']
                        .every((id) => (document.getElementById(id)?.textContent || '').trim().length > 0);
                }"""
            )
            functional_checks["stage_hud_logic_trace_ok"] = bool(hud_trace_ok)
            if not hud_trace_ok:
                violations.append("/stage HUD Logic Trace 6-stage panel failed to populate")
            geometry_reports.append(_check_layout_geometry(page, "stage_hud_logic_trace_1920x1080"))
            page.click("#tab-hud-arena")
            time.sleep(0.15)

            # F10 clean projection check using computed display === 'none'
            page.keyboard.press("F10")
            time.sleep(0.2)
            f10_hidden = page.evaluate("window.getComputedStyle(document.getElementById('control-bar')).display === 'none'")
            geometry_reports.append(_check_layout_geometry(page, "stage_hud_f10_projection_1920x1080"))
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

            # ===== C2. HiDPI 2x Close-Up Grand Staff Inspection (Adopted & Rejected) =====
            hi_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            hi_page = hi_ctx.new_page()
            _attach_error_listeners(hi_page, errors)
            hi_page.goto(f"{stage_url}/studio", wait_until="domcontentloaded")
            hi_page.wait_for_function("document.querySelectorAll('#cello-cand-cards .cand-pick-card').length === 4", timeout=12000)
            hi_page.wait_for_selector("#canvas-cello-hero-staff svg.grand-staff-svg", timeout=8000)
            time.sleep(0.3)
            p_gs_ok = out_dir / "audit_grand_staff_closeup.png"
            hi_page.locator("#canvas-cello-hero-staff").screenshot(path=str(p_gs_ok))
            screenshots["audit_grand_staff_closeup"] = str(p_gs_ok)

            if hi_page.locator("#cello-cand-cards .cand-pick-card.rejected").count() > 0:
                hi_page.locator("#cello-cand-cards .cand-pick-card.rejected").first.click()
                time.sleep(0.25)
                p_gs_rej = out_dir / "audit_grand_staff_rejected_closeup.png"
                hi_page.locator("#canvas-cello-hero-staff").screenshot(path=str(p_gs_rej))
                screenshots["audit_grand_staff_rejected_closeup"] = str(p_gs_rej)
            hi_ctx.close()

            # ===== D. Smartphone Dedicated UI Audit (390x844, Touch, 0px Overflow) =====
            mob_ctx = browser.new_context(
                viewport={"width": 390, "height": 844},
                is_mobile=True,
                has_touch=True,
                accept_downloads=True,
            )
            mob_page = mob_ctx.new_page()
            _attach_error_listeners(mob_page, errors)

            # D1. Static Web Demo on Mobile (390x844)
            mob_page.goto(f"{static_url}/index.html", wait_until="networkidle")
            mob_page.wait_for_function("document.querySelectorAll('#piece-list .piece-item').length === 100", timeout=12000)
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(mob_page, "mobile_web_demo_390x844"))
            p_mwd = out_dir / "mobile_web_demo_390x844.png"
            mob_page.screenshot(path=str(p_mwd))
            screenshots["mobile_web_demo_390x844"] = str(p_mwd)

            # D2. Live /studio All 4 Modes on Mobile (390x844)
            mob_page.goto(f"{stage_url}/studio", wait_until="domcontentloaded")
            mob_page.wait_for_function("document.querySelectorAll('#cello-cand-cards .cand-pick-card').length === 4", timeout=12000)
            time.sleep(0.3)

            for m_key, sc_key in (
                ("cello", "mobile_mode1_cello"),
                ("piano", "mobile_mode2_piano"),
                ("notagen", "mobile_mode3_notagen"),
                ("library", "mobile_mode4_library"),
            ):
                mob_page.click(f".mobile-nav-btn[data-mode-view='{m_key}']")
                time.sleep(0.28)
                geometry_reports.append(_check_layout_geometry(mob_page, f"{sc_key}_390x844"))
                p_mob = out_dir / f"{sc_key}.png"
                mob_page.screenshot(path=str(p_mob))
                screenshots[sc_key] = str(p_mob)

            # Mobile Logic Trace Inspector + Help Modal + Rejected Candidate + Grand Staff closeup (390x844)
            mob_page.click(".mobile-nav-btn[data-mode-view='cello']")
            time.sleep(0.22)
            if mob_page.locator("#canvas-cello-hero-staff").count() > 0:
                mob_page.locator("#canvas-cello-hero-staff").scroll_into_view_if_needed()
                time.sleep(0.2)
                p_mgs = out_dir / "mobile_grand_staff_390x844.png"
                mob_page.locator("#canvas-cello-hero-staff").screenshot(path=str(p_mgs))
                screenshots["mobile_grand_staff_390x844"] = str(p_mgs)
            if mob_page.locator("#cello-cand-cards .cand-pick-card.rejected").count() > 0:
                mob_page.locator("#cello-cand-cards .cand-pick-card.rejected").first.click()
                time.sleep(0.22)
                geometry_reports.append(_check_layout_geometry(mob_page, "mobile_mode1_rejected_390x844"))
                if mob_page.locator("#cello-cand-cards .cand-pick-card.adopted").count() > 0:
                    mob_page.locator("#cello-cand-cards .cand-pick-card.adopted").first.click()
                    time.sleep(0.18)

            mob_page.click("#btn-help")
            time.sleep(0.18)
            geometry_reports.append(_check_layout_geometry(mob_page, "mobile_help_modal_390x844"))
            mob_page.keyboard.press("Escape")
            time.sleep(0.15)

            mob_page.click("#tab-cello-trace")
            time.sleep(0.22)
            geometry_reports.append(_check_layout_geometry(mob_page, "mobile_logic_trace_390x844"))
            p_mlt = out_dir / "mobile_logic_trace_390x844.png"
            mob_page.screenshot(path=str(p_mlt))
            screenshots["mobile_logic_trace_390x844"] = str(p_mlt)
            mob_page.click("#tab-cello-score")
            time.sleep(0.15)

            # Ultra-narrow 320px Smartphone Reflow Check (anything-frontend-art-direction 320px floor)
            mob_page.set_viewport_size({"width": 320, "height": 640})
            time.sleep(0.22)
            mob_page.click(".mobile-nav-btn[data-mode-view='cello']")
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(mob_page, "mobile_narrow_cello_320x640"))
            mob_page.click(".mobile-nav-btn[data-mode-view='library']")
            time.sleep(0.25)
            geometry_reports.append(_check_layout_geometry(mob_page, "mobile_narrow_library_320x640"))

            mob_page.goto("about:blank")
            mob_ctx.close()
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
            "audit_grand_staff_closeup",
            "audit_grand_staff_rejected_closeup",
            "studio_mode1_cello",
            "studio_mode2_piano",
            "studio_mode3_notagen",
            "studio_mode4_library",
            "logic_trace_inspector",
            "desktop_mode1_cello",
            "desktop_mode2_piano",
            "desktop_mode3_notagen",
            "desktop_mode4_library",
            "stage_hud_mode_b_cello",
            "stage_projection_f10",
            "web_demo_1920x1080",
            "mobile_mode1_cello",
            "mobile_mode2_piano",
            "mobile_mode3_notagen",
            "mobile_mode4_library",
            "mobile_logic_trace_390x844",
            "mobile_web_demo_390x844",
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
        sa = g.get("svgAudit") or {}
        svg_errs = sum(
            len(sa.get(k, []))
            for k in ("brokenSvgs", "svgSmallFonts", "svgTextClipped", "svgTextOverlaps", "svgTextNoteOverlaps", "svgClefNoteOverlaps")
        )
        print(
            f" - {g['label']}: overflow={g['overflowPx']}px clipped={len(g['clipped'])} "
            f"overlaps={len(g['overlaps'])} domOv={len(g.get('domOverlaps', []))} "
            f"svg={sa.get('svgCount', 0)}/err={svg_errs} trunc={len(g.get('truncationIssues', []))} "
            f"contrast={len(g.get('contrastIssues', []))} rawLeak={len(g.get('rawLeakIssues', []))} "
            f"minFont={g['minVisibleFontPx']}px smallBtns={len(g['smallPrimaryBtns'])} pedalOrderOk={g['pedalOrderOk']}"
        )
    if rep["violations"]:
        print("VIOLATIONS:")
        for v in rep["violations"]:
            print("  -", v)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
