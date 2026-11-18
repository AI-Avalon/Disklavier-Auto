"""
Generates Authentic Multi-Turn MIDI Demo Compositions (`docs/demos/`) and
Captures Live Headless Browser Screenshots (`docs/images/`) of the Stage HUD.

Outputs:
1. `docs/demos/demo_mode_b_cello_dialogue.mid` (8-turn Cello <-> Two-Hand Piano + CC#64/CC#67)
2. `docs/demos/demo_mode_a_piano_dialogue.mid` (8-turn Piano <-> Two-Hand Piano + CC#64/CC#67)
3. `docs/demos/COMPOSITION_SHOWCASE.md` (Turn-by-turn MusicalityCritic & Voicing Dossier)
4. `docs/images/stage_hud_mode_b_cello.png` (Live 1920x1080 HUD capture in Mode B)
5. `docs/images/stage_hud_mode_a_piano.png` (Live 1920x1080 HUD capture in Mode A)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Tuple

import mido
import requests
from symusic import Score
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stage.config_scanner import scan_ollama_environment
from src.stage.layer1_perception import midi_to_note_name
from src.stage.stage_server import create_stage_app

DOCS_DEMOS_DIR = ROOT / "docs" / "demos"
DOCS_IMAGES_DIR = ROOT / "docs" / "images"


def _find_browser_exe() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
        shutil.which("google-chrome") or "",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    raise RuntimeError("No Chromium/Edge headless browser executable found for HUD screenshot capture.")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _events_to_mido_track(
    name: str,
    program: int,
    channel: int,
    timed_items: List[Tuple[float, str, int, int]],
    tpq: int = 480,
    tempo_bpm: int = 75,
) -> mido.MidiTrack:
    """Converts absolute-time MIDI messages (sec, kind, p1, p2) into a mido MidiTrack."""
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=name, time=0))
    tr.append(mido.Message("program_change", program=program, channel=channel, time=0))

    sec_per_beat = 60.0 / float(tempo_bpm)
    ticks_per_sec = float(tpq) / sec_per_beat

    # Sort chronologically: note_off before note_on at identical timestamps
    order_rank = {"note_off": 0, "control_change": 1, "note_on": 2}
    sorted_items = sorted(timed_items, key=lambda x: (round(x[0], 4), order_rank.get(x[1], 1), x[2]))

    last_tick = 0
    for t_sec, kind, p1, p2 in sorted_items:
        cur_tick = max(last_tick, int(round(max(0.0, t_sec) * ticks_per_sec)))
        delta = cur_tick - last_tick
        last_tick = cur_tick
        if kind == "note_on":
            tr.append(mido.Message("note_on", note=max(21, min(108, p1)), velocity=max(18, min(112, p2)), channel=channel, time=delta))
        elif kind == "note_off":
            tr.append(mido.Message("note_off", note=max(21, min(108, p1)), velocity=24, channel=channel, time=delta))
        elif kind == "control_change":
            tr.append(mido.Message("control_change", control=p1, value=max(0, min(127, p2)), channel=channel, time=delta))
    tr.append(mido.MetaMessage("end_of_track", time=240))
    return tr


def _compose_8turn_midi(
    mode: str,
    out_mid_path: Path,
    use_ollama: bool,
) -> List[Dict[str, Any]]:
    """
    Runs 8 full L1->L2->L3->Critic turns via StageOrchestrator and compiles all human inquiries,
    AI Right-Hand, AI Left-Hand, and continuous CC#64/CC#67 pedal curves into an SMF Type-1 MIDI file.
    """
    app = create_stage_app(
        demo_enabled=False,
        monitor_audio=True,
        production_mode=False,
        corpus_dirs=None,
        use_ollama=use_ollama,
        open_hardware=False,
        play_audio_device=False,
    )
    orch = app.state.orchestrator
    orch.set_mode(mode)
    orch.reset_performance()

    directives_by_mode = {
        "cello": [
            "低音レガートに対し開離ボイシングとウナ・コルダ踏み替えで応答",
            "鋭い跳躍スピッカートに反行対位法と浅い踏み替えで応対",
            "重音の倍音域を空け高域透明和音とハーフペダルで支える",
            "半音階下降に対し内声掛留解決と踏み替えで返す",
            "休符の呼吸を生かし長調側へ開く両手ボイシングで応える",
            "低音バスP5維持のまま上声で跳躍対旋律を置く",
            "ウナ・コルダを深く踏み弱奏の対話で繋ぐ",
            "主音へ落ち着いた終止和音と緩やかなペダル解放で締める",
        ],
        "piano": [
            "提示モチーフに対し反行対位法と踏み替えで応対",
            "高域フレーズを受け低音バスと内声半音進行で支える",
            "スタッカート対話に明晰な跳躍とドライ気味の踏み替え",
            "掛留音と9thテンションを交えた開離両手ボイシング",
            "ウナ・コルダで音色を柔らかく変え反行変奏を置く",
            "密集回避のため10度バスと高域三度・六度で展開",
            "付点と呼吸休符を伴う室内楽的掛け合いで応じる",
            "静かな終止和音と両ペダルの段階的解放で結ぶ",
        ],
    }

    human_track_msgs: List[Tuple[float, str, int, int]] = []
    rh_track_msgs: List[Tuple[float, str, int, int]] = []
    lh_track_msgs: List[Tuple[float, str, int, int]] = []
    turn_logs: List[Dict[str, Any]] = []

    cursor_s = 0.0
    for idx in range(8):
        orch.apply_directive(directives_by_mode[mode][idx])
        st = orch.execute_composition_turn(use_virtual_human=True, staggered_playback=False)

        human_notes = list(st["perception"].get("recent_human_notes") or [])
        adopted = next((c for c in st["candidates"] if c["adopted"]), st["candidates"][0])
        ai_events = list(adopted.get("events") or [])

        # Append Human inquiry notes
        human_end = 0.4
        for hn in human_notes:
            p = int(hn.get("pitch", 50))
            if p <= 0:
                continue
            on = cursor_s + float(hn.get("onset_s", 0.0))
            dur = max(0.08, float(hn.get("duration_s", 0.25)))
            vel = int(hn.get("velocity", 72))
            human_track_msgs.append((on, "note_on", p, vel))
            human_track_msgs.append((on + dur, "note_off", p, 24))
            human_end = max(human_end, float(hn.get("onset_s", 0.0)) + dur)

        # Append AI Two-Hand Response & Continuous CC#64 / CC#67 Curves
        ai_start = cursor_s + human_end + 0.14
        ai_end = 0.5
        for ev in ai_events:
            p = int(ev.get("pitch", 0) or 0)
            on = ai_start + float(ev.get("onset_s", 0.0))
            if p <= 0:
                ctrl = int(ev.get("control", 0) or 0)
                val = int(ev.get("value", 0) or 0)
                if ctrl in (64, 67):
                    lh_track_msgs.append((on, "control_change", ctrl, val))
                continue

            dur = max(0.06, float(ev.get("duration_s", 0.35)))
            vel = int(ev.get("velocity", 68))
            hand = str(ev.get("hand", "")).lower()
            target = lh_track_msgs if (hand == "lh" or p < 60) else rh_track_msgs
            target.append((on, "note_on", p, vel))
            target.append((on + dur, "note_off", p, 24))
            ai_end = max(ai_end, float(ev.get("onset_s", 0.0)) + dur)

        # Reset pedals cleanly between turns
        lh_track_msgs.append((ai_start + ai_end + 0.08, "control_change", 64, 0))
        lh_track_msgs.append((ai_start + ai_end + 0.08, "control_change", 67, 0))

        turn_logs.append(
            {
                "turn": idx + 1,
                "mode": mode,
                "human_pitches": "-".join(midi_to_note_name(int(n["pitch"])) for n in human_notes if int(n.get("pitch", 0)) > 0),
                "articulation": st["perception"].get("articulation"),
                "intent": st["intent"],
                "candidates": st["candidates"],
                "adopted": adopted,
                "audio_status": st["audio_status"],
            }
        )
        cursor_s = ai_start + ai_end + 0.45

    orch.close()

    mid = mido.MidiFile(type=1, ticks_per_beat=480)
    tempo_tr = mido.MidiTrack()
    tempo_tr.append(mido.MetaMessage("track_name", name=f"Disklavier-Auto {mode.upper()} 8-Turn Dialogue", time=0))
    tempo_tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(75), time=0))
    tempo_tr.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    tempo_tr.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(tempo_tr)

    human_prog = 42 if mode == "cello" else 0
    human_name = "Cello_Human_Part" if mode == "cello" else "Piano_Human_Part"
    mid.tracks.append(_events_to_mido_track(human_name, human_prog, 0, human_track_msgs))
    mid.tracks.append(_events_to_mido_track("AI_Piano_RightHand", 0, 1, rh_track_msgs))
    mid.tracks.append(_events_to_mido_track("AI_Piano_LeftHand_DualPedal", 0, 2, lh_track_msgs))

    out_mid_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_mid_path))

    # Verify loadability via symusic & dual-pedal presence
    sc = Score(str(out_mid_path))
    total_notes = sum(len(tr.notes) for tr in sc.tracks)
    cc_nums = {int(c.number) for tr in sc.tracks for c in tr.controls}
    assert total_notes >= 40, f"{out_mid_path} has too few notes: {total_notes}"
    assert 64 in cc_nums and 67 in cc_nums, f"{out_mid_path} missing CC#64 or CC#67: {cc_nums}"
    return turn_logs


def _write_showcase_markdown(
    mode_b_logs: List[Dict[str, Any]],
    mode_a_logs: List[Dict[str, Any]],
    out_md: Path,
) -> None:
    lines: List[str] = [
        "# Disklavier-Auto 8ターン自律作曲デモ・ショーケース (`COMPOSITION_SHOWCASE.md`)",
        "",
        "本ドキュメントは、再構築された **L1知覚 → TokenEconomizer → L2自律作曲設計 (Ollama) → L3コーパス構造参照・両手ポリフォニー生成 → `MusicalityCritic` 6軸監査 → 連続両ペダル (CC#64/CC#67) 物理シミュレーション** の各ターン推敲記録と実演出力を自動収録したものです。",
        "",
        "## 収録標準MIDIファイル",
        "- **Mode B（チェロ × 両手ピアノ 8ターン通し対話）**: [`demo_mode_b_cello_dialogue.mid`](demo_mode_b_cello_dialogue.mid)",
        "- **Mode A（ピアノ × 両手ピアノ 8ターン通し対話）**: [`demo_mode_a_piano_dialogue.mid`](demo_mode_a_piano_dialogue.mid)",
        "",
    ]

    for title, logs in (
        ("## 1. Mode B: チェロ × 両手ピアノ対話（8ターン詳細）", mode_b_logs),
        ("## 2. Mode A: ピアノ × 両手ピアノ対話（8ターン詳細）", mode_a_logs),
    ):
        lines.append(title)
        lines.append("")
        for rec in logs:
            intent = rec["intent"]
            ad = rec["adopted"]
            econ = intent.get("token_economizer", {})
            c64 = ad.get("pedal_cc64_curve") or []
            c67 = ad.get("pedal_cc67_curve") or []
            c64_range = f"{min(x['val'] for x in c64)}..{max(x['val'] for x in c64)}" if c64 else "0"
            c67_range = f"{min(x['val'] for x in c67)}..{max(x['val'] for x in c67)}" if c67 else "0"

            lines.append(f"### Turn {rec['turn']} — 入力 `{rec['human_pitches']}` ({rec['articulation']})")
            lines.append(f"- **実演家メモ (`monologue_ja`)**: {intent.get('monologue_ja')}")
            lines.append(f"- **自律和声・ボイシング設計**: 調性 `{intent.get('tonal_center')}` / 進行 `{' → '.join(intent.get('chord_progression', []))}` / 立体配置 `{intent.get('voicing_structure')}`")
            lines.append(f"- **TokenEconomizer 圧縮**: 削減率 `{econ.get('compression_ratio_pct', 0)}%` (`{econ.get('raw_tokens_est', 0)} -> {econ.get('compressed_tokens_est', 0)}` tokens) / LLM `{intent.get('llm_source')}`")
            lines.append(f"- **連続両ペダル実演**: Damper CC#64 `{c64_range}` (踏み替え `{rec['audio_status'].get('repedal_count', 0)}` 回) / Una Corda CC#67 `{c67_range}` (倍音ベール `{rec['audio_status'].get('una_corda_veil_db', 0)} dB`)")
            lines.append("")
            lines.append("| 案 | 状態 | 総合点 | 反音階 | 両手和声 | 声部進行 | 倍音回避 | 律動呼吸 | 両ペダル | 音域 | 棄却根拠 |")
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
            for c in rec["candidates"]:
                pct = c.get("subscores_pct", {})
                st_lbl = "**採用**" if c["adopted"] else "棄却"
                rej_str = "-" if c["adopted"] else (c.get("rejection_tag_ja") or "-")
                lines.append(
                    f"| {c['candidate_id']} | {st_lbl} | {c['total_score']} | "
                    f"{pct.get('anti_scale', 0)}% | {pct.get('polyphony_harmony', 0)}% | "
                    f"{pct.get('voice_leading', 0)}% | {pct.get('spectral_clearance', 0)}% | "
                    f"{pct.get('rhythmic_diversity', 0)}% | {pct.get('dual_pedal', 0)}% | "
                    f"`{c.get('pitch_range_str', '--')}` | {rej_str} |"
                )
            lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


def _capture_live_screenshots(use_ollama: bool) -> Tuple[Path, Path]:
    browser = _find_browser_exe()
    port = _free_port()
    app = create_stage_app(
        demo_enabled=False,
        monitor_audio=True,
        production_mode=False,
        corpus_dirs=None,
        use_ollama=use_ollama,
        open_hardware=False,
        play_audio_device=False,
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, name="ScreenshotStageServer", daemon=True)
    th.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            r = requests.get(f"{base_url}/api/stage/state", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)

    DOCS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    img_b = DOCS_IMAGES_DIR / "stage_hud_mode_b_cello.png"
    img_a = DOCS_IMAGES_DIR / "stage_hud_mode_a_piano.png"

    try:
        # Mode B: Execute 4 rich turns so Motif Graph has 8 connected nodes and Zone C shows 4 staves
        requests.post(f"{base_url}/api/stage/mode", json={"mode": "cello"}, timeout=5.0).raise_for_status()
        for sc_key in ("cello_low_legato", "cello_leap_spiccato", "overtone_mud_clearance"):
            requests.post(f"{base_url}/api/stage/preset", json={"preset": sc_key}, timeout=15.0).raise_for_status()

        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--window-size=1920,1080",
                "--virtual-time-budget=3500",
                f"--screenshot={img_b}",
                f"{base_url}/?snapshot=1",
            ],
            check=True,
            timeout=30,
        )

        # Mode A: Reset and execute 3 piano<->piano turns
        requests.post(f"{base_url}/api/stage/reset", timeout=5.0).raise_for_status()
        requests.post(f"{base_url}/api/stage/mode", json={"mode": "piano"}, timeout=5.0).raise_for_status()
        for _ in range(3):
            requests.post(f"{base_url}/api/stage/turn", timeout=15.0).raise_for_status()

        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--window-size=1920,1080",
                "--virtual-time-budget=3500",
                f"--screenshot={img_a}",
                f"{base_url}/?snapshot=1",
            ],
            check=True,
            timeout=30,
        )
    finally:
        server.should_exit = True
        th.join(timeout=3.0)

    for p in (img_b, img_a):
        assert p.exists() and p.stat().st_size > 25000, f"Screenshot too small or missing: {p}"
    return img_b, img_a


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate demo MIDIs, showcase doc, and live HUD screenshots")
    parser.add_argument("--no-ollama", action="store_true")
    args = parser.parse_args()

    ollama_running = bool(scan_ollama_environment().get("running", False)) and not args.no_ollama
    print(f"Generating 8-turn Mode B & Mode A demo MIDIs (Ollama={ollama_running})...")
    mode_b_logs = _compose_8turn_midi("cello", DOCS_DEMOS_DIR / "demo_mode_b_cello_dialogue.mid", ollama_running)
    mode_a_logs = _compose_8turn_midi("piano", DOCS_DEMOS_DIR / "demo_mode_a_piano_dialogue.mid", ollama_running)
    _write_showcase_markdown(mode_b_logs, mode_a_logs, DOCS_DEMOS_DIR / "COMPOSITION_SHOWCASE.md")
    print("Capturing live Stage HUD screenshots (1920x1080)...")
    img_b, img_a = _capture_live_screenshots(ollama_running)
    print(f"Generated assets:\n - {DOCS_DEMOS_DIR / 'demo_mode_b_cello_dialogue.mid'}\n - {DOCS_DEMOS_DIR / 'demo_mode_a_piano_dialogue.mid'}\n - {DOCS_DEMOS_DIR / 'COMPOSITION_SHOWCASE.md'}\n - {img_b}\n - {img_a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
