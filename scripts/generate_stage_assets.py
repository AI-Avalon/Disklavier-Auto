"""
Generates Complete 4-Act Co-Composed Masterwork Audio/MIDI/MusicXML Demos (`docs/demos/`)
and Captures Live 1920x1080 Headless Browser Screenshots (`docs/images/`) of the Stage HUD.

Outputs:
1. `docs/demos/demo_mode_b_cello_dialogue.{mp3,wav,mid,musicxml}` (8-turn 4-Act Cello <-> Disklavier + Retake)
2. `docs/demos/demo_mode_a_piano_dialogue.{mp3,wav,mid,musicxml}` (8-turn 4-Act Piano <-> Disklavier + Retake)
3. `docs/demos/COMPOSITION_SHOWCASE.md` (Full 4-Act Musicological Analysis, Retake Diff & Critique Dossier)
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


def _compose_4act_masterwork(
    mode: str,
    basename: str,
    use_ollama: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Runs an 8-turn 4-Act co-composition session (plus 1 live Public Rehearsal Retake)
    via StageOrchestrator with real Ollama + RTX 3070 CorpusPhraseModelManager +
    CoCompositionArchitect + Concert Hall Synth, and exports .mp3 / .wav / .mid / .musicxml.
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
            "第1幕提示: 低音レガートに対し、半終止のまま音楽的に問いかけて",
            "第1幕対話: 跳躍スピッカートを反転模倣し、左手に10度アルペジオの呼吸を",
            "第2幕発展: 相対調へ転調しつつ上行ゼクエンツと内声対旋律を展開",
            "第2幕模進: 重音の倍音域を避け、4-3掛留解決とハーフペダルで接続",
            "第2幕架橋: 休符の呼吸を生かし属調方向へ緊張を繋ぐ",
            "第3幕頂点: 低音バス保続音の上で3声ポリフォニーを高域頂点へ統合",
            "第3幕高揚: バスP5支えと両手対位法の掛け合いでクライマックスを維持",
            "第4幕回想: ウナ・コルダを深く踏み、冒頭種子動機を静かに回想して完全終止",
        ],
        "piano": [
            "第1幕提示: 冒頭モチーフに対し、半終止の問いかけと開離10度で応対",
            "第1幕対話: 跳躍と逆行回復を伴い、解決せず次へ繋ぐ問いかけを置く",
            "第2幕発展: 近親調へ転調しながらゼクエンツ模進とテノール内声を展開",
            "第2幕模進: 掛留解決とシンコペーション和音で立体的に前進する",
            "第2幕架橋: 密な中域を避けて10度バスと高域対旋律で架橋する",
            "第3幕頂点: 低音保続音と4声ポリフォニーを作品全体の最高音頂点へ導く",
            "第3幕高揚: ソプラノ頂点の跳躍回復と充実した両手和声で響かせる",
            "第4幕回想: ウナ・コルダで音色を柔らかく変え冒頭動機を回想し完全終止",
        ],
    }

    turn_logs: List[Dict[str, Any]] = []
    for idx in range(8):
        orch.apply_directive(directives_by_mode[mode][idx])
        st = orch.execute_composition_turn(use_virtual_human=True, staggered_playback=False)

        # Demonstrate live Public Rehearsal Retake right after Turn 2 (rewriting Act 1 ending before Act 2)
        if idx == 1:
            st = orch.retake_last_turn(
                "今のフレーズはまだ解決しないで問いかけのままにして、左手の内声を増強して書き直して"
            )

        human_notes = list(st["perception"].get("recent_human_notes") or [])
        adopted = next((c for c in st["candidates"] if c["adopted"]), st["candidates"][0])
        turn_logs.append(
            {
                "turn": idx + 1,
                "mode": mode,
                "act_label_ja": st["intent"].get("act_label_ja", f"第{((idx)//2)+1}幕"),
                "phrase_function": st["intent"].get("phrase_function", ""),
                "bridge": st["intent"].get("voice_leading_bridge", "--"),
                "human_pitches": "-".join(
                    midi_to_note_name(int(n["pitch"])) for n in human_notes if int(n.get("pitch", 0)) > 0
                ),
                "articulation": st["perception"].get("articulation"),
                "intent": st["intent"],
                "candidates": st["candidates"],
                "adopted": adopted,
                "audio_status": st["audio_status"],
            }
        )

    summary = orch.synthesize_complete_work(
        out_dir=DOCS_DEMOS_DIR,
        basename=basename,
        play_audio=False,
    )
    retake_logs = list(orch.architect.retake_logs)
    summary["retake_logs"] = retake_logs
    orch.close()

    # Rigorous output verification
    mid_path = DOCS_DEMOS_DIR / f"{basename}.mid"
    wav_path = DOCS_DEMOS_DIR / f"{basename}.wav"
    mp3_path = DOCS_DEMOS_DIR / f"{basename}.mp3"
    xml_path = DOCS_DEMOS_DIR / f"{basename}.musicxml"
    for p in (mid_path, wav_path, mp3_path, xml_path):
        assert p.exists() and p.stat().st_size > 1000, f"Expected output artifact missing or tiny: {p}"

    sc = Score(str(mid_path))
    total_notes = sum(len(tr.notes) for tr in sc.tracks)
    cc_nums = {int(c.number) for tr in sc.tracks for c in tr.controls}
    assert total_notes >= 80, f"{mid_path} has too few notes for a complete work: {total_notes}"
    assert 64 in cc_nums and 67 in cc_nums, f"{mid_path} missing CC#64 or CC#67: {cc_nums}"

    am = summary["audio_metrics"]
    assert 45.0 <= float(am["duration_s"]) <= 135.0, f"Unexpected masterwork audio duration: {am['duration_s']}"
    assert float(am["rms"]) > 0.015, f"Masterwork audio RMS too low: {am['rms']}"
    assert int(am["clipping_count"]) == 0 and not am["has_nan_or_inf"]
    return summary, turn_logs


def _write_showcase_markdown(
    mode_b_sum: Dict[str, Any],
    mode_b_logs: List[Dict[str, Any]],
    mode_a_sum: Dict[str, Any],
    mode_a_logs: List[Dict[str, Any]],
    out_md: Path,
) -> None:
    lines: List[str] = [
        "# Disklavier-Auto 4幕共同作曲マスターワーク・ショーケース (`COMPOSITION_SHOWCASE.md`)",
        "",
        "本ドキュメントは、**4幕大局構成エンジン (`CoCompositionArchitect`) × RTX 3070 クラシックMIDI学習モデル (`CorpusPhraseModelManager`) × Ollama + TokenEconomizer × `MusicalityCritic` + `MasterworkEvaluator` × コンサートホール物理モデリング音響 (`LocalAudioSynthesizer`)** によって共同作曲・公開推敲され、1つの完結した楽曲として出力された2作品の全記録です。",
        "",
        "## 完成デモ音源・楽譜ダウンロード（MIDIソフト不要で即試聴可）",
        "",
        "### 作品1（Mode B：チェロ × Disklavier）",
        f"- **作品名**: {mode_b_sum['title_ja']} (*{mode_b_sum['title_en']}*)",
        f"- **主調 / 主題核**: `{mode_b_sum['home_tonality']}` / `{mode_b_sum['seed_motif']}`",
        f"- **総演奏時間 / 音響統計**: `{mode_b_sum['total_duration_s']} 秒` (`RMS={mode_b_sum['audio_metrics']['rms']}`, `{mode_b_sum['audio_metrics']['dbfs']} dBFS`, `Clipping=0`)",
        "- **試聴・楽譜ファイル**: [MP3 完成音源 (`demo_mode_b_cello_dialogue.mp3`)](demo_mode_b_cello_dialogue.mp3) | [WAV マスター (`demo_mode_b_cello_dialogue.wav`)](demo_mode_b_cello_dialogue.wav) | [標準MIDI (`demo_mode_b_cello_dialogue.mid`)](demo_mode_b_cello_dialogue.mid) | [MusicXML 大譜表 (`demo_mode_b_cello_dialogue.musicxml`)](demo_mode_b_cello_dialogue.musicxml)",
        "",
        "### 作品2（Mode A：人間ピアノ × Disklavier 連弾対話）",
        f"- **作品名**: {mode_a_sum['title_ja']} (*{mode_a_sum['title_en']}*)",
        f"- **主調 / 主題核**: `{mode_a_sum['home_tonality']}` / `{mode_a_sum['seed_motif']}`",
        f"- **総演奏時間 / 音響統計**: `{mode_a_sum['total_duration_s']} 秒` (`RMS={mode_a_sum['audio_metrics']['rms']}`, `{mode_a_sum['audio_metrics']['dbfs']} dBFS`, `Clipping=0`)",
        "- **試聴・楽譜ファイル**: [MP3 完成音源 (`demo_mode_a_piano_dialogue.mp3`)](demo_mode_a_piano_dialogue.mp3) | [WAV マスター (`demo_mode_a_piano_dialogue.wav`)](demo_mode_a_piano_dialogue.wav) | [標準MIDI (`demo_mode_a_piano_dialogue.mid`)](demo_mode_a_piano_dialogue.mid) | [MusicXML 大譜表 (`demo_mode_a_piano_dialogue.musicxml`)](demo_mode_a_piano_dialogue.musicxml)",
        "",
    ]

    for heading, summary, logs in (
        (f"## 1. Mode B 完成作品アナリーゼ: {mode_b_sum['title_ja']}", mode_b_sum, mode_b_logs),
        (f"## 2. Mode A 完成作品アナリーゼ: {mode_a_sum['title_ja']}", mode_a_sum, mode_a_logs),
    ):
        lines.append(heading)
        lines.append("")
        lines.append("### 公開リハーサル（ダメ出し・小節書き直し）の記録")
        retakes = summary.get("retake_logs") or []
        if retakes:
            for rt in retakes:
                lines.append(
                    f"- **Turn {rt.get('turn_number')} 書き直し#{rt.get('retake_index')}**: 指示「{rt.get('directive_text')}」 → "
                    f"`{rt.get('diff_ja')}` (スコア {rt.get('before_score')} → {rt.get('after_score')})"
                )
        else:
            lines.append("- 書き直し履歴なし")
        lines.append("")

        for rec in logs:
            intent = rec["intent"]
            ad = rec["adopted"]
            econ = intent.get("token_economizer", {})
            c64 = ad.get("pedal_cc64_curve") or []
            c67 = ad.get("pedal_cc67_curve") or []
            c64_range = f"{min(x['val'] for x in c64)}..{max(x['val'] for x in c64)}" if c64 else "0"
            c67_range = f"{min(x['val'] for x in c67)}..{max(x['val'] for x in c67)}" if c67 else "0"
            mw = ad.get("masterwork_pct") or {}

            lines.append(f"### Turn {rec['turn']} [{rec['act_label_ja']}] — 入力 `{rec['human_pitches']}` ({rec['articulation']})")
            lines.append(f"- **楽曲機能・声部接続**: `{rec['phrase_function']}` / Bridge `{rec['bridge']}` / LH書法 `{ad.get('lh_idiom', '--')}` (`{ad.get('blueprint_source', '--')}`)")
            lines.append(f"- **実演家メモ (`monologue_ja`)**: {intent.get('monologue_ja')}")
            lines.append(f"- **自律和声・ボイシング設計**: 調性 `{intent.get('tonal_center')}` / 進行 `{' → '.join(intent.get('chord_progression', []))}`")
            lines.append(f"- **Masterwork 4基準**: 主題統一 `{mw.get('thematic_unity', 0)}%` / 旋律頂点 `{mw.get('melodic_arch', 0)}%` / 和声引力 `{mw.get('harmonic_trajectory', 0)}%` / 声部接続 `{mw.get('inter_turn_continuity', 0)}%`")
            lines.append(f"- **TokenEconomizer 圧縮**: 削減率 `{econ.get('compression_ratio_pct', 0)}%` (`{econ.get('raw_tokens_est', 0)} -> {econ.get('compressed_tokens_est', 0)}` tokens) / LLM `{intent.get('llm_source')}`")
            lines.append(f"- **連続両ペダル実演**: Damper CC#64 `{c64_range}` (踏み替え `{rec['audio_status'].get('repedal_count', 0)}` 回) / Una Corda CC#67 `{c67_range}`")
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
        # Mode B: Execute 6 turns through Act 3 Climax + 1 Retake so HUD shows Act 3 Climax & 12 connected nodes
        requests.post(f"{base_url}/api/stage/mode", json={"mode": "cello"}, timeout=5.0).raise_for_status()
        for idx in range(6):
            requests.post(f"{base_url}/api/stage/turn", timeout=18.0).raise_for_status()
            if idx == 1:
                requests.post(
                    f"{base_url}/api/stage/retake",
                    json={"text": "今のフレーズはまだ解決しない問いかけのまま、左手の内声を増強して書き直して"},
                    timeout=18.0,
                ).raise_for_status()

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

        # Mode A: Reset and execute 8 turns through Act 4 + Retake + synthesize title
        requests.post(f"{base_url}/api/stage/reset", timeout=5.0).raise_for_status()
        requests.post(f"{base_url}/api/stage/mode", json={"mode": "piano"}, timeout=5.0).raise_for_status()
        for idx in range(8):
            requests.post(f"{base_url}/api/stage/turn", timeout=18.0).raise_for_status()
            if idx == 1:
                requests.post(
                    f"{base_url}/api/stage/retake",
                    json={"text": "問いかけのまま左手10度を強調して書き直して"},
                    timeout=18.0,
                ).raise_for_status()
        requests.post(f"{base_url}/api/stage/synthesize_work", json={"play_audio": False}, timeout=20.0).raise_for_status()

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
    parser = argparse.ArgumentParser(description="Generate 4-Act demo audio/MIDI/MusicXML, showcase doc, and HUD screenshots")
    parser.add_argument("--no-ollama", action="store_true")
    args = parser.parse_args()

    ollama_running = bool(scan_ollama_environment().get("running", False)) and not args.no_ollama
    print(f"Generating 4-Act Masterwork Demos in Mode B & Mode A (Ollama={ollama_running})...")
    mode_b_sum, mode_b_logs = _compose_4act_masterwork("cello", "demo_mode_b_cello_dialogue", ollama_running)
    mode_a_sum, mode_a_logs = _compose_4act_masterwork("piano", "demo_mode_a_piano_dialogue", ollama_running)
    _write_showcase_markdown(
        mode_b_sum,
        mode_b_logs,
        mode_a_sum,
        mode_a_logs,
        DOCS_DEMOS_DIR / "COMPOSITION_SHOWCASE.md",
    )
    print("Capturing live Stage HUD screenshots (1920x1080)...")
    img_b, img_a = _capture_live_screenshots(ollama_running)
    print(
        f"Generated assets:\n"
        f" - Mode B: {mode_b_sum['title_ja']} ({mode_b_sum['total_duration_s']}s) -> demo_mode_b_cello_dialogue.{{mp3,wav,mid,musicxml}}\n"
        f" - Mode A: {mode_a_sum['title_ja']} ({mode_a_sum['total_duration_s']}s) -> demo_mode_a_piano_dialogue.{{mp3,wav,mid,musicxml}}\n"
        f" - {DOCS_DEMOS_DIR / 'COMPOSITION_SHOWCASE.md'}\n"
        f" - {img_b}\n"
        f" - {img_a}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
