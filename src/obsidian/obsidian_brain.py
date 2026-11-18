"""
Obsidian Musical Second Brain (`obsidian_vault/`) Auto-Builder, Bidirectional WikiLink
Graph Engine, Disklavier-Optimized SMF Exporter, and Real-Time `04_Human_Directives.md` Watcher.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.midi.imslp_library import save_piece_to_smf
from src.telemetry.telemetry_store import BASE_DIR, record_caught_exception, resolve_safe_path


VAULT_DIR = BASE_DIR / "obsidian_vault"


def _slugify(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_\-]+", "_", text.strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "Note"


def _parse_directives_text(text: str) -> Dict[str, Any]:
    lower = text.lower()
    prefer_minor = any(k in lower for k in ("短調", "minor", "悲哀", "elegy", "dark", "c minor", "d minor"))
    prefer_chroma = any(k in lower for k in ("半音階", "chromatic", "バス", "bass", "descending"))
    prefer_contrast = any(
        k in lower for k in ("スタッカート", "staccato", "対比", "contrast", "articulation", "レガート")
    )
    prefer_cp = any(k in lower for k in ("対位法", "counterpoint", "カノン", "fugue", "polyphon"))
    prefer_pedal = any(k in lower for k in ("ハーフペダル", "half pedal", "half-pedal", "残響", "shimmer"))

    active_tags: List[str] = []
    if prefer_minor:
        active_tags.append("Minor Mode")
    if prefer_chroma:
        active_tags.append("Chromatic Bass")
    if prefer_contrast:
        active_tags.append("Staccato-Legato Contrast")
    if prefer_cp:
        active_tags.append("Dense Counterpoint")
    if prefer_pedal:
        active_tags.append("Deep Half-Pedal")

    return {
        "raw_text": text,
        "prefer_minor": prefer_minor,
        "prefer_chromatic_bass": prefer_chroma,
        "prefer_articulation_contrast": prefer_contrast,
        "bass_chromaticism_target": 0.86 if prefer_chroma else 0.62,
        "staccato_contrast_target": 0.42 if prefer_contrast else 0.28,
        "counterpoint_density_target": 0.90 if prefer_cp else 0.78,
        "half_pedal_target": 68 if prefer_pedal else 62,
        "active_tags": active_tags,
        "summary": ", ".join(active_tags) if active_tags else "Balanced Classical Virtuoso",
    }


class ObsidianMusicalBrain:
    """
    Manages the `obsidian_vault/` Markdown knowledge graph, Hall of Fame MIDI/Note archival,
    harmonic discovery notes, dashboard Mermaid visualizations, and live directives watcher.
    """

    HOF_SCORE_THRESHOLD = 85.0
    MAX_HOF_FILES = 36

    def __init__(
        self,
        seed_motifs: Optional[List[Dict[str, Any]]] = None,
        on_directives_changed: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.vault_dir = VAULT_DIR
        self.dashboard_dir = self.vault_dir / "00_Dashboard"
        self.motif_dir = self.vault_dir / "01_Motif_Library"
        self.hof_dir = self.vault_dir / "02_Hall_of_Fame_Performances"
        self.hof_midi_dir = self.hof_dir / "midi"
        self.discovery_dir = self.vault_dir / "03_Harmonic_Discoveries"
        self.stage_logs_dir = self.vault_dir / "05_Stage_Session_Logs"
        self.directives_path = self.vault_dir / "04_Human_Directives.md"

        self.on_directives_changed = on_directives_changed
        self._lock = threading.RLock()
        self._last_directives_hash: str = ""
        self._hof_records: List[Dict[str, Any]] = []
        self._discovery_backlinks: Dict[str, List[str]] = {}
        self._motif_backlinks: Dict[str, List[str]] = {}
        self._last_dashboard_gen: int = -1
        self._graph_cache: Optional[Dict[str, Any]] = None
        self._graph_cache_sig: Optional[Tuple[Any, ...]] = None
        self._graph_version: int = 0

        self._ensure_structure(seed_motifs or [])
        self._load_existing_hof_index()

        self._running = True
        self._watcher_thread = threading.Thread(
            target=self._watch_directives_loop,
            name="ObsidianDirectivesWatcher",
            daemon=True,
        )
        self._watcher_thread.start()

    def _ensure_structure(self, seed_motifs: List[Dict[str, Any]]) -> None:
        for d in (
            self.dashboard_dir,
            self.motif_dir,
            self.hof_dir,
            self.hof_midi_dir,
            self.discovery_dir,
            self.stage_logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

        if not self.directives_path.exists():
            default_dir = (
                "---\n"
                "type: human_directives\n"
                "updated_at: auto\n"
                "tags: [directives, fitness-function, live-control]\n"
                "---\n\n"
                "# 04 Human Directives (人間からの進化指示書)\n\n"
                "このファイルを Obsidian または GUI から編集して保存すると、0.5秒以内に AI ピアニストの\n"
                "Fitness Function（採点重み）と変異方向へリアルタイム反映されます。\n\n"
                "## 現在の指示 (Active Directives)\n"
                "- [x] 声部連結（対位法）と途切れないレガート・オーバーラップ (+5〜+15ms) を維持する\n"
                "- [ ] 次はもっと短調の半音階的バス (Chromatic Bass) を増やす\n"
                "- [ ] スタッカートとレガートの対比 (Staccato-Legato Contrast) を強くする\n"
                "- [ ] ハーフペダル (CC#64: 35-80) による倍音残響を深める\n\n"
                "## 関連ノート\n"
                "- [[Evolution_Overview]]\n"
            )
            self.directives_path.write_text(default_dir, encoding="utf-8")

        self._last_directives_hash = hashlib.md5(
            self.directives_path.read_bytes()
        ).hexdigest()

        # Populate 01_Motif_Library/
        for m in seed_motifs:
            self._write_motif_note(m)

        # Populate initial 03_Harmonic_Discoveries/
        initial_discoveries = [
            (
                "Discovery_Chromatic_Mediant_bVI_maj7_Sharp11",
                "Chromatic Mediant Modulation (I -> bVI maj7#11)",
                "遠隔調である下六度長七度（#11th含み）へのピボット転調。ドビュッシー的色彩感とバス反行を両立。",
                ["chromatic-mediant", "lydian", "modulation"],
            ),
            (
                "Discovery_Half_Pedal_Overtone_Halo",
                "Syncopated Half-Pedal (CC#64: 35-80) & Velocity-1 Sympathetic Halo",
                "小節境界の -35ms〜-45ms 先行ペダル踏み替えと Velocity 1 開放弦共鳴の併用による無音断の倍音持続。",
                ["half-pedal", "sympathetic-halo", "acoustics"],
            ),
            (
                "Discovery_Neapolitan_Pivot_Counterpoint",
                "Neapolitan Pivot & Contrary Bass Counterpoint",
                "ナポリ和音 (bII maj7) を経由し、上声旋律の上昇に対してバスを半音階的に下降させる対位法的解決。",
                ["neapolitan", "counterpoint", "contrary-motion"],
            ),
        ]
        for fname, title, desc, tags in initial_discoveries:
            p = self.discovery_dir / f"{fname}.md"
            if not p.exists():
                content = (
                    f"---\n"
                    f"type: harmonic_discovery\n"
                    f"title: \"{title}\"\n"
                    f"tags: {tags}\n"
                    f"---\n\n"
                    f"# {title}\n\n"
                    f"{desc}\n\n"
                    f"## 知識グラフ接続\n"
                    f"- [[Evolution_Overview]]\n"
                    f"- [[04_Human_Directives]]\n"
                )
                p.write_text(content, encoding="utf-8")

        # Write initial Dashboard
        self.update_dashboard(evolution_history=[], elite_genome={}, directives={})

    def _write_motif_note(self, motif: Dict[str, Any], backlinks: Optional[List[str]] = None) -> Path:
        mid = _slugify(motif.get("motif_id", "motif"))
        note_path = self.motif_dir / f"{mid}.md"
        title = motif.get("title", mid)
        composer = motif.get("composer", "Unknown")
        key = motif.get("key", "Concert Key")
        intervals = motif.get("interval_vector", [])
        rhythm = motif.get("rhythm_ratios", [])
        tags = motif.get("tags", ["motif"])
        contrary = motif.get("contrary_motion_ratio", 0.65)
        pedal = motif.get("half_pedal_profile", {})
        blinks = backlinks if backlinks is not None else self._motif_backlinks.get(mid, [])
        links_md = "\n".join(f"- [[{b}]]" for b in blinks[:10]) or "- [[Evolution_Overview]]"

        content = (
            f"---\n"
            f"type: motif\n"
            f"motif_id: \"{mid}\"\n"
            f"composer: \"{composer}\"\n"
            f"tonality: \"{key}\"\n"
            f"interval_structure: {intervals}\n"
            f"rhythm_ratios: {rhythm}\n"
            f"contrary_motion_ratio: {contrary}\n"
            f"tags: {tags}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"- **作曲家 / 出典**: {composer} (`{motif.get('source_file', 'seed')}`)\n"
            f"- **調性**: {key}\n"
            f"- **音程ベクトル**: `{intervals}`\n"
            f"- **リズム比率**: `{rhythm}`\n"
            f"- **反行率 / ハーフペダルプロファイル**: {int(float(contrary)*100)}% / CC#64 mean={pedal.get('mean_cc64', 64)}\n\n"
            f"## 派生した名演・関連ノート (Bidirectional Links)\n"
            f"- [[Evolution_Overview]]\n"
            f"{links_md}\n"
        )
        note_path.write_text(content, encoding="utf-8")
        return note_path

    def _load_existing_hof_index(self) -> None:
        records: List[Dict[str, Any]] = []
        for md_file in sorted(self.hof_dir.glob("*.md")):
            txt = md_file.read_text(encoding="utf-8", errors="ignore")
            m_score = re.search(r"total_score:\s*([0-9.]+)", txt)
            m_gen = re.search(r"generation:\s*(\d+)", txt)
            m_title = re.search(r"title:\s*\"([^\"]+)\"", txt)
            m_midi = re.search(r"midi_file:\s*\"([^\"]+)\"", txt)
            records.append(
                {
                    "note_id": md_file.stem,
                    "title": m_title.group(1) if m_title else md_file.stem,
                    "generation": int(m_gen.group(1)) if m_gen else 1,
                    "total_score": float(m_score.group(1)) if m_score else 85.0,
                    "midi_rel": m_midi.group(1) if m_midi else "",
                }
            )
        records.sort(key=lambda r: r["total_score"], reverse=True)
        self._hof_records = records[: self.MAX_HOF_FILES]

    def _discovery_for_chord(self, chord_label: str) -> str:
        low = chord_label.lower()
        if "neapolitan" in low or "tritone" in low:
            return "Discovery_Neapolitan_Pivot_Counterpoint"
        if "mediant" in low or "bvi" in low or "dorian" in low:
            return "Discovery_Chromatic_Mediant_bVI_maj7_Sharp11"
        return "Discovery_Half_Pedal_Overtone_Halo"

    def record_generation_result(
        self,
        generation_result: Dict[str, Any],
        motif: Dict[str, Any],
        arc_stage: Tuple[str, str, float],
        evolution_history: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Archives high-scoring (>= 85.0) performances as Hall of Fame notes + optimized .mid,
        updates bidirectional links on Motif & Discovery notes, and refreshes the Dashboard.
        """
        with self._lock:
            gen = int(generation_result["generation"])
            winner = generation_result["winner"]
            score = float(generation_result["best_score"])
            axes = winner["evaluation"]["axes"]
            commentary = winner["evaluation"]["commentary_ja"]
            chord_label = winner.get("chord_label", "Tonic -> Chromatic Mediant")
            transform_type = winner.get("transform_type", "inversion")

            motif_id = _slugify(motif.get("motif_id", "motif"))
            disc_id = self._discovery_for_chord(chord_label)

            perf_id: Optional[str] = None
            if score >= self.HOF_SCORE_THRESHOLD:
                # Cap number of stored HOF files to keep vault crisp and fast
                existing_gens = {r["generation"] for r in self._hof_records}
                should_save = (gen not in existing_gens) and (
                    len(self._hof_records) < self.MAX_HOF_FILES
                    or score > min(r["total_score"] for r in self._hof_records)
                )
                if should_save:
                    perf_id = f"HoF_Gen{gen:03d}_Score{int(round(score))}_{_slugify(arc_stage[0])}"
                    midi_filename = f"{perf_id}.mid"
                    midi_path = self.hof_midi_dir / midi_filename
                    piece_obj = {
                        "id": perf_id,
                        "title": f"Gen {gen} {arc_stage[0]} ({score:.1f} pts)",
                        "tempo_bpm": 62,
                        "events": winner["events"],
                    }
                    save_piece_to_smf(piece_obj, midi_path)

                    md_path = self.hof_dir / f"{perf_id}.md"
                    md_content = (
                        f"---\n"
                        f"type: hall_of_fame_performance\n"
                        f"generation: {gen}\n"
                        f"total_score: {score}\n"
                        f"arc_stage: \"{arc_stage[0]}\"\n"
                        f"chord_progression: \"{chord_label}\"\n"
                        f"motivic_transform: \"{transform_type}\"\n"
                        f"midi_file: \"02_Hall_of_Fame_Performances/midi/{midi_filename}\"\n"
                        f"title: \"Gen {gen} — {arc_stage[1]} ({score:.1f} pts)\"\n"
                        f"tags: [hall-of-fame, generation-{gen}, disklavier-optimized]\n"
                        f"---\n\n"
                        f"# 🏆 Hall of Fame: Generation {gen} ({score:.1f} / 100 pts)\n\n"
                        f"- **音楽的局面 (Story Arc)**: {arc_stage[1]}\n"
                        f"- **和声転調ルート**: `{chord_label}`\n"
                        f"- **動機発展技法**: `{transform_type}`\n"
                        f"- **最適化MIDIファイル**: [{midi_filename}](midi/{midi_filename})\n\n"
                        f"## 5軸自己採点スコア内訳\n"
                        f"| 評価軸 | スコア | 満点 |\n"
                        f"|---|---|---|\n"
                        f"| ① 声部連結・対位法美 | {axes.get('voice_leading', 0):.2f} | 25.0 |\n"
                        f"| ② 動機発展・非分断フロー | {axes.get('motivic_continuity', 0):.2f} | 20.0 |\n"
                        f"| ③ 和声独創性・ドラマ性 | {axes.get('harmonic_originality', 0):.2f} | 20.0 |\n"
                        f"| ④ タッチ・表現力 (CC#64/Articulation) | {axes.get('expressive_articulation', 0):.2f} | 15.0 |\n"
                        f"| ⑤ Disklavier実機物理適性 | {axes.get('physical_playability', 0):.2f} | 20.0 |\n\n"
                        f"## AI Critic 講評\n"
                        f"> {commentary}\n\n"
                        f"## 双方向リンク (Bidirectional Knowledge Graph)\n"
                        f"- Source Motif: [[{motif_id}]]\n"
                        f"- Harmonic Discovery: [[{disc_id}]]\n"
                        f"- Dashboard: [[Evolution_Overview]]\n"
                        f"- Directives: [[04_Human_Directives]]\n"
                    )
                    md_path.write_text(md_content, encoding="utf-8")

                    # Update bidirectional links on Motif & Discovery notes
                    self._motif_backlinks.setdefault(motif_id, [])
                    if perf_id not in self._motif_backlinks[motif_id]:
                        self._motif_backlinks[motif_id].append(perf_id)
                        self._write_motif_note(motif, self._motif_backlinks[motif_id])

                    self._discovery_backlinks.setdefault(disc_id, [])
                    if perf_id not in self._discovery_backlinks[disc_id]:
                        self._discovery_backlinks[disc_id].append(perf_id)
                        self._append_discovery_backlink(disc_id, perf_id, gen, score)

                    self._hof_records.append(
                        {
                            "note_id": perf_id,
                            "title": f"Gen {gen} — {arc_stage[0]} ({score:.1f} pts)",
                            "generation": gen,
                            "total_score": score,
                            "midi_rel": f"02_Hall_of_Fame_Performances/midi/{midi_filename}",
                        }
                    )
                    self._hof_records.sort(key=lambda r: r["total_score"], reverse=True)
                    self._hof_records = self._hof_records[: self.MAX_HOF_FILES]

            # Update Evolution_Overview.md every generation or milestone
            if gen != self._last_dashboard_gen:
                self._last_dashboard_gen = gen
                self.update_dashboard(
                    evolution_history=evolution_history,
                    elite_genome=generation_result.get("elite_genome", {}),
                    directives={},
                )
            self._graph_version += 1
            return perf_id

    def _append_discovery_backlink(
        self, disc_id: str, perf_id: str, gen: int, score: float
    ) -> None:
        p = self.discovery_dir / f"{disc_id}.md"
        if not p.exists():
            return
        txt = p.read_text(encoding="utf-8")
        link_token = f"[[{perf_id}]]"
        if link_token not in txt:
            txt += f"- Applied in {link_token} (Gen {gen}, {score:.1f} pts)\n"
            p.write_text(txt, encoding="utf-8")

    def update_dashboard(
        self,
        evolution_history: List[Dict[str, Any]],
        elite_genome: Dict[str, Any],
        directives: Dict[str, Any],
    ) -> None:
        with self._lock:
            cur_gen = evolution_history[-1]["generation"] if evolution_history else 1
            best_score = (
                max(h["best_score"] for h in evolution_history)
                if evolution_history
                else 0.0
            )
            latest_score = evolution_history[-1]["best_score"] if evolution_history else 0.0

            # Build Mermaid xychart-beta data from last 16 history points
            pts = evolution_history[-16:]
            if pts:
                x_labels = ", ".join(f"G{p['generation']}" for p in pts)
                y_best = ", ".join(f"{p['best_score']:.1f}" for p in pts)
                y_mean = ", ".join(f"{p['mean_score']:.1f}" for p in pts)
            else:
                x_labels = "G1"
                y_best = "75.0"
                y_mean = "72.0"

            top_hof_rows = "\n".join(
                f"| #{idx+1} | [[{r['note_id']}]] | Gen {r['generation']} | **{r['total_score']:.1f} pts** |"
                for idx, r in enumerate(self._hof_records[:10])
            ) or "| - | (Waiting for >= 85 pts) | - | - |"

            top_motifs = sorted(self.motif_dir.glob("*.md"))[:5]
            top_hofs = self._hof_records[:4]
            mermaid_edges: List[str] = [
                '    D["04_Human_Directives"] -->|"Fitness Weights"| O["Evolution_Overview"]'
            ]
            for m_p in top_motifs:
                m_id = m_p.stem
                mermaid_edges.append(f'    {m_id}["{m_id}"] --> O')
            for h in top_hofs:
                h_id = h["note_id"]
                mermaid_edges.append(f'    O --> {h_id}["{h_id}"]')

            overview_md = (
                "---\n"
                "type: dashboard\n"
                f"current_generation: {cur_gen}\n"
                f"best_score: {best_score:.1f}\n"
                f"hall_of_fame_count: {len(self._hof_records)}\n"
                "tags: [dashboard, evolution, autonomous-virtuoso]\n"
                "---\n\n"
                "# 🎹 Autonomous Virtuoso — Evolution Overview\n\n"
                "## システム統計 (Live System Stats)\n"
                f"- **現在の世代 (Current Generation)**: `Generation {cur_gen}`\n"
                f"- **最新採用スコア (Latest Score)**: `{latest_score:.1f} / 100.0 pts`\n"
                f"- **歴代最高スコア (All-Time Peak)**: `{best_score:.1f} / 100.0 pts`\n"
                f"- **Hall of Fame 殿堂入り名演数**: `{len(self._hof_records)} performances`\n"
                f"- **Motif Library 登録数**: `{len(list(self.motif_dir.glob('*.md')))} motifs`\n\n"
                "## 📈 進化スコア推移 (Evolution Trajectory)\n"
                "```mermaid\n"
                "xychart-beta\n"
                "    title \"Self-Evolving Virtuoso Score Progression\"\n"
                f"    x-axis [{x_labels}]\n"
                "    y-axis \"Score (0-100)\" 60 --> 100\n"
                f"    line [{y_best}]\n"
                f"    line [{y_mean}]\n"
                "```\n\n"
                "## 🏆 歴代最高スコアランキング (Hall of Fame)\n"
                "| Rank | Performance Note | Generation | Total Score |\n"
                "|---|---|---|---|\n"
                f"{top_hof_rows}\n\n"
                "## 🕸️ 音楽知識グラフ (Knowledge Graph)\n"
                "```mermaid\n"
                "flowchart LR\n"
                + "\n".join(mermaid_edges)
                + "\n```\n\n"
                "## コントロール\n"
                "- 人間の音楽的要望は [[04_Human_Directives]] に記述してください。\n"
            )
            (self.dashboard_dir / "Evolution_Overview.md").write_text(
                overview_md, encoding="utf-8"
            )
            self._graph_version += 1

    def read_human_directives(self) -> Dict[str, Any]:
        if not self.directives_path.exists():
            return _parse_directives_text("")
        txt = self.directives_path.read_text(encoding="utf-8", errors="ignore")
        return _parse_directives_text(txt)

    def write_human_directives(self, new_text: str) -> Dict[str, Any]:
        with self._lock:
            self.directives_path.write_text(new_text, encoding="utf-8")
            self._last_directives_hash = hashlib.md5(
                self.directives_path.read_bytes()
            ).hexdigest()
            parsed = _parse_directives_text(new_text)
            self._graph_version += 1
        if self.on_directives_changed:
            self.on_directives_changed(parsed)
        return parsed

    def _watch_directives_loop(self) -> None:
        while self._running:
            try:
                if self.directives_path.exists():
                    raw_bytes = self.directives_path.read_bytes()
                    digest = hashlib.md5(raw_bytes).hexdigest()
                    if digest != self._last_directives_hash:
                        self._last_directives_hash = digest
                        parsed = _parse_directives_text(
                            raw_bytes.decode("utf-8", errors="ignore")
                        )
                        if self.on_directives_changed:
                            self.on_directives_changed(parsed)
            except Exception as exc:
                record_caught_exception("obsidian_brain.watch_directives", exc)
            time.sleep(0.5)

    def get_note_content(self, note_id: str) -> Optional[Dict[str, Any]]:
        if not note_id or ".." in note_id or "/" in note_id or "\\" in note_id:
            return None
        safe_id = _slugify(note_id)
        for folder in (
            self.dashboard_dir,
            self.motif_dir,
            self.hof_dir,
            self.discovery_dir,
            self.vault_dir,
        ):
            rel = (folder / f"{safe_id}.md").relative_to(self.vault_dir).as_posix()
            cand = resolve_safe_path(self.vault_dir, rel, (".md",))
            if cand is not None and cand.is_file():
                txt = cand.read_text(encoding="utf-8", errors="ignore")
                m_midi = re.search(r"midi_file:\s*\"([^\"]+)\"", txt)
                return {
                    "id": safe_id,
                    "path": cand.relative_to(BASE_DIR).as_posix(),
                    "markdown": txt,
                    "midi_rel": m_midi.group(1) if m_midi else None,
                }
        return None

    def get_knowledge_graph_snapshot(self) -> Dict[str, Any]:
        cat_dirs = [
            ("dashboard", self.dashboard_dir),
            ("motif", self.motif_dir),
            ("hall_of_fame", self.hof_dir),
            ("discovery", self.discovery_dir),
        ]
        all_md: List[Tuple[str, Path, int, int]] = []
        for cat, d in cat_dirs:
            for p in sorted(d.glob("*.md")):
                try:
                    st = p.stat()
                    all_md.append((cat, p, int(st.st_mtime_ns), int(st.st_size)))
                except OSError:
                    continue
        if self.directives_path.exists():
            try:
                st = self.directives_path.stat()
                all_md.append(("directives", self.directives_path, int(st.st_mtime_ns), int(st.st_size)))
            except OSError:
                pass

        with self._lock:
            ver_start = self._graph_version
            sig = (ver_start, tuple((str(p), mtime, sz) for _, p, mtime, sz in all_md))
            if self._graph_cache is not None and self._graph_cache_sig == sig:
                cached = dict(self._graph_cache)
                cached["hof_leaderboard"] = list(self._hof_records[:10])
                return cached

        # Build nodes, edges, and markdown parses outside self._lock
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []
        known_ids = set()

        for cat, md_path, _, _ in all_md:
            nid = md_path.stem
            known_ids.add(nid)
            txt = md_path.read_text(encoding="utf-8", errors="ignore")
            links = re.findall(r"\[\[([^\]]+)\]\]", txt)
            m_score = re.search(r"total_score:\s*([0-9.]+)", txt)
            m_title = re.search(r"^#\s+(.+)$", txt, flags=re.MULTILINE)
            m_midi = re.search(r"midi_file:\s*\"([^\"]+)\"", txt)
            nodes.append(
                {
                    "id": nid,
                    "title": m_title.group(1).strip() if m_title else nid,
                    "category": cat,
                    "rel_path": md_path.relative_to(BASE_DIR).as_posix(),
                    "score": float(m_score.group(1)) if m_score else None,
                    "midi_rel": m_midi.group(1) if m_midi else None,
                    "links": sorted(set(links)),
                }
            )

        edge_seen = set()
        for n in nodes:
            for tgt in n["links"]:
                tgt_clean = tgt.split("|")[0].strip()
                if tgt_clean in known_ids and tgt_clean != n["id"]:
                    key = (n["id"], tgt_clean)
                    if key not in edge_seen:
                        edge_seen.add(key)
                        edges.append({"source": n["id"], "target": tgt_clean})

        directives_raw = (
            self.directives_path.read_text(encoding="utf-8", errors="ignore")
            if self.directives_path.exists()
            else ""
        )

        with self._lock:
            hof_list = list(self._hof_records[:10])
            snapshot = {
                "vault_path": str(self.vault_dir),
                "nodes": nodes,
                "edges": edges,
                "stats": {
                    "total_notes": len(nodes),
                    "total_links": len(edges),
                    "motif_count": sum(1 for n in nodes if n["category"] == "motif"),
                    "hof_count": sum(1 for n in nodes if n["category"] == "hall_of_fame"),
                    "discovery_count": sum(1 for n in nodes if n["category"] == "discovery"),
                },
                "hof_leaderboard": hof_list,
                "directives_raw": directives_raw,
            }
            if self._graph_version == ver_start:
                self._graph_cache = snapshot
                self._graph_cache_sig = sig
            return snapshot

    def get_in_app_knowledge_explorer(self) -> Dict[str, Any]:
        """
        In-App Musical Knowledge Graph & Evolution Note Explorer:
        Returns the knowledge graph, phylogenetic evolution lineage, and full rendered
        Markdown notes so the user never needs to leave the app to open Obsidian.
        """
        graph = self.get_knowledge_graph_snapshot()
        recent_notes_full: List[Dict[str, Any]] = []
        # Prioritize Hall of Fame, Dashboard, Discovery, then Motifs
        ordered_nodes = sorted(
            graph["nodes"],
            key=lambda n: (
                0 if n["category"] == "hall_of_fame" else (1 if n["category"] == "dashboard" else (2 if n["category"] == "discovery" else 3)),
                -(n.get("score") or 0.0),
            ),
        )
        for n in ordered_nodes[:16]:
            note_obj = self.get_note_content(n["id"])
            if note_obj:
                recent_notes_full.append(
                    {
                        "id": n["id"],
                        "title": n["title"],
                        "category": n["category"],
                        "score": n.get("score"),
                        "midi_rel": note_obj.get("midi_rel"),
                        "links": n.get("links", []),
                        "markdown": note_obj["markdown"],
                    }
                )

        # Build phylogenetic evolution tree summarizing progression across Hall of Fame & Motifs
        phylo_tree: List[Dict[str, Any]] = []
        for idx, rec in enumerate(list(self._hof_records[:12])):
            phylo_tree.append(
                {
                    "rank": idx + 1,
                    "generation": rec.get("generation", idx + 1),
                    "score": rec.get("total_score", rec.get("score", 85.0)),
                    "note_id": rec.get("note_id", ""),
                    "title": rec.get("title", f"Generation {rec.get('generation', idx + 1)}"),
                    "midi_rel": rec.get("midi_rel"),
                }
            )

        return {
            "graph": graph,
            "phylogenetic_tree": phylo_tree,
            "recent_notes_full": recent_notes_full,
        }

    def record_stage_dialogue_turn(
        self,
        turn_id: int,
        mode: str,
        human_summary: Dict[str, Any],
        token_stats: Dict[str, Any],
        intent_data: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        pedal_summary: Dict[str, Any],
        session_tag: str = "stage",
    ) -> Path:
        """
        Records a live stage / demo turn (human phrase analysis, TokenEconomizer metrics,
        MusicalityCritic 6-axis scores & rejection reasons, adopted two-hand voicing,
        dual pedal curves, and performer memo) to `obsidian_vault/05_Stage_Session_Logs/`.
        """
        with self._lock:
            self.stage_logs_dir.mkdir(parents=True, exist_ok=True)
            safe_tag = _slugify(session_tag)
            note_id = f"StageTurn_{safe_tag}_{int(turn_id):04d}_{mode}"
            md_path = self.stage_logs_dir / f"{note_id}.md"

            adopted = next((c for c in candidates if c.get("adopted")), None)
            adopted_id = adopted.get("candidate_id", "A") if adopted else "A"
            adopted_score = float(adopted.get("total_score", 0.0)) if adopted else 0.0

            cand_rows = []
            for c in candidates:
                cid = c.get("candidate_id", "?")
                status = "ADOPTED" if c.get("adopted") else "REJECTED"
                reason = (c.get("rejection_detail") or {}).get("reason_core_ja") or c.get("rejection_tag_ja") or "-"
                cand_rows.append(
                    f"| {cid} | {status} | {float(c.get('total_score', 0.0)):.1f} | "
                    f"{c.get('pitch_range_str', '--')} | {reason} |"
                )

            cand_table = "\n".join(cand_rows) or "| - | - | 0.0 | -- | - |"
            monologue = str(intent_data.get("monologue_ja", ""))
            chords = " -> ".join(intent_data.get("chord_progression", []) or [])
            ratio = float(token_stats.get("compression_ratio_pct", 0.0))

            md_content = (
                "---\n"
                "type: stage_dialogue_turn\n"
                f"turn_id: {int(turn_id)}\n"
                f"mode: \"{mode}\"\n"
                f"adopted_candidate: \"{adopted_id}\"\n"
                f"total_score: {adopted_score:.2f}\n"
                f"token_compression_pct: {ratio:.1f}\n"
                "tags: [stage-session, musicality-critic, dual-pedal]\n"
                "---\n\n"
                f"# Stage Turn #{int(turn_id)} ({mode.upper()}) — Adopted Candidate {adopted_id} ({adopted_score:.1f} pts)\n\n"
                f"## 実演家メモ (L2 Deliberation)\n"
                f"> {monologue}\n\n"
                f"## 人間フレーズ知覚 ＆ TokenEconomizer 圧縮\n"
                f"- **人間音列**: `{human_summary.get('pitches_str', '--')}` ({human_summary.get('articulation', '--')})\n"
                f"- **和声進行設計**: `{chords}` (`{intent_data.get('tonal_center', '--')}`)\n"
                f"- **TokenEconomizer 削減率**: `{ratio:.1f}%` (`{token_stats.get('raw_tokens_est', 0)} -> {token_stats.get('compressed_tokens_est', 0)}` tokens)\n"
                f"- **両ペダル制御**: Damper CC#64 `{pedal_summary.get('cc64_min', 0)}..{pedal_summary.get('cc64_max', 0)}` (repedals={pedal_summary.get('repedal_count', 0)}) / "
                f"Una Corda CC#67 `{pedal_summary.get('cc67_min', 0)}..{pedal_summary.get('cc67_max', 0)}`\n\n"
                f"## 4候補 音楽性監査 (MusicalityCritic 6-Axis)\n"
                f"| Candidate | Status | Score | Register | Rejection Reason |\n"
                f"|---|---|---:|---|---|\n"
                f"{cand_table}\n\n"
                f"## 知識グラフリンク\n"
                f"- [[Evolution_Overview]]\n"
                f"- [[04_Human_Directives]]\n"
            )
            md_path.write_text(md_content, encoding="utf-8")

            # Prune oldest stage turn logs beyond 48 files to keep vault crisp
            existing = sorted(self.stage_logs_dir.glob("StageTurn_*.md"), key=lambda p: p.stat().st_mtime)
            while len(existing) > 48:
                victim = existing.pop(0)
                try:
                    victim.unlink()
                except OSError:
                    pass
            self._graph_version += 1
            return md_path

    def record_completed_masterwork(
        self,
        work: Dict[str, Any],
        turn_history: List[Dict[str, Any]],
        asset_paths: Dict[str, str],
        session_tag: str = "stage",
    ) -> Path:
        """
        Records a completed multi-act co-composed masterwork (title, 4-act trajectory,
        seed motif, per-turn rejection reasons, retake revisions, and links to
        .mp3 / .wav / .mid / .musicxml) to `obsidian_vault/02_Hall_of_Fame_Performances/`.
        """
        with self._lock:
            self.hof_dir.mkdir(parents=True, exist_ok=True)
            safe_tag = _slugify(session_tag)
            mode = str(work.get("mode", "cello"))
            note_id = f"Masterwork_{safe_tag}_{mode}"
            md_path = self.hof_dir / f"{note_id}.md"

            title_ja = str(work.get("title_ja", "共同作曲作品"))
            title_en = str(work.get("title_en", "Co-Composed Work"))
            dur_s = float(work.get("total_duration_s", 0.0))
            seed = str(work.get("seed_motif", "--"))
            home_ton = str(work.get("home_tonality", "D_dorian"))

            act_rows: List[str] = []
            for tr in turn_history:
                t_no = tr.get("turn_number", 0)
                act_lbl = tr.get("act_label_ja", "")
                func = tr.get("phrase_function", "")
                ad = tr.get("adopted") or {}
                cands = tr.get("candidates") or []
                rej_str = " / ".join(
                    f"案{c.get('candidate_id')}:{c.get('rejection_tag_ja', '')}"
                    for c in cands
                    if not c.get("adopted")
                )
                act_rows.append(
                    f"| {t_no} | {act_lbl} | `{func}` | `{tr.get('tonal_center')}` | "
                    f"案{ad.get('candidate_id', 'A')} ({ad.get('total_score', 0)}) | {tr.get('monologue_ja', '')} | {rej_str} |"
                )

            retakes = list(work.get("retake_logs") or [])
            retake_lines = [
                f"- Turn {rt.get('turn_number')} 書き直し#{rt.get('retake_index')}: "
                f"`{rt.get('directive_text', '')}` ({rt.get('diff_ja', '')}, "
                f"Score {rt.get('before_score', 0):.1f} -> {rt.get('after_score', 0):.1f})"
                for rt in retakes
            ]
            retake_section = "\n".join(retake_lines) if retake_lines else "- 公開推敲によるやり直しなし"

            links_lines = [
                f"- **MP3完成音源**: `{asset_paths.get('mp3', '')}`" if asset_paths.get("mp3") else "",
                f"- **WAVマスター音源**: `{asset_paths.get('wav', '')}`" if asset_paths.get("wav") else "",
                f"- **標準MIDI (SMF)**: `{asset_paths.get('mid', '')}`" if asset_paths.get("mid") else "",
                f"- **MusicXML大譜表**: `{asset_paths.get('musicxml', '')}`" if asset_paths.get("musicxml") else "",
            ]
            links_block = "\n".join(l for l in links_lines if l) or "- (ローカル出力なし)"

            md_content = (
                "---\n"
                "type: completed_masterwork\n"
                f"title_ja: \"{title_ja}\"\n"
                f"title_en: \"{title_en}\"\n"
                f"mode: \"{mode}\"\n"
                f"home_tonality: \"{home_ton}\"\n"
                f"duration_s: {dur_s:.2f}\n"
                f"midi_file: \"{asset_paths.get('mid', '')}\"\n"
                "tags: [masterwork, co-composition, 4-act-form]\n"
                "---\n\n"
                f"# {title_ja}\n"
                f"*{title_en}*\n\n"
                f"- **編成**: `{'Mode B: Cello + Disklavier' if mode == 'cello' else 'Mode A: Human Piano + Disklavier'}`\n"
                f"- **主調**: `{home_ton}` / **冒頭主題核 (Seed Motif)**: `{seed}`\n"
                f"- **総演奏時間**: `{dur_s:.2f} 秒` ({len(turn_history)} ターン構成)\n\n"
                f"## 完成音源・楽譜ファイル\n"
                f"{links_block}\n\n"
                f"## 公開推敲・書き直し履歴\n"
                f"{retake_section}\n\n"
                f"## 4幕楽曲構成・全ターン推敲アナリーゼ\n"
                f"| Turn | 幕構成 | 楽曲機能 | 調性 | 採用案 | 実演家メモ | ボツ案棄却理由 |\n"
                f"|---:|---|---|---|---|---|---|\n"
                + ("\n".join(act_rows) or "| - | - | - | - | - | - | - |")
                + "\n\n## 知識グラフリンク\n"
                f"- [[Evolution_Overview]]\n"
                f"- [[04_Human_Directives]]\n"
            )
            md_path.write_text(md_content, encoding="utf-8")
            self._graph_version += 1
            return md_path

    def close(self) -> None:
        self._running = False
