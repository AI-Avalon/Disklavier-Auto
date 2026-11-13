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
from src.telemetry.telemetry_store import BASE_DIR


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
        self.directives_path = self.vault_dir / "04_Human_Directives.md"

        self.on_directives_changed = on_directives_changed
        self._lock = threading.RLock()
        self._last_directives_hash: str = ""
        self._hof_records: List[Dict[str, Any]] = []
        self._discovery_backlinks: Dict[str, List[str]] = {}
        self._motif_backlinks: Dict[str, List[str]] = {}
        self._last_dashboard_gen: int = -1

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
            except Exception:
                pass
            time.sleep(0.5)

    def get_note_content(self, note_id: str) -> Optional[Dict[str, Any]]:
        safe_id = _slugify(note_id)
        for folder in (
            self.dashboard_dir,
            self.motif_dir,
            self.hof_dir,
            self.discovery_dir,
            self.vault_dir,
        ):
            cand = folder / f"{safe_id}.md"
            if cand.exists():
                txt = cand.read_text(encoding="utf-8", errors="ignore")
                m_midi = re.search(r"midi_file:\s*\"([^\"]+)\"", txt)
                return {
                    "id": safe_id,
                    "path": str(cand.relative_to(BASE_DIR)),
                    "markdown": txt,
                    "midi_rel": m_midi.group(1) if m_midi else None,
                }
        return None

    def get_knowledge_graph_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            nodes: List[Dict[str, Any]] = []
            edges: List[Dict[str, str]] = []
            known_ids = set()

            cat_dirs = [
                ("dashboard", self.dashboard_dir),
                ("motif", self.motif_dir),
                ("hall_of_fame", self.hof_dir),
                ("discovery", self.discovery_dir),
            ]
            all_md: List[Tuple[str, Path]] = []
            for cat, d in cat_dirs:
                for p in sorted(d.glob("*.md")):
                    all_md.append((cat, p))
            if self.directives_path.exists():
                all_md.append(("directives", self.directives_path))

            for cat, md_path in all_md:
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
                        "rel_path": str(md_path.relative_to(BASE_DIR)),
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

            return {
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
                "hof_leaderboard": list(self._hof_records[:10]),
                "directives_raw": self.directives_path.read_text(encoding="utf-8", errors="ignore")
                if self.directives_path.exists()
                else "",
            }

    def close(self) -> None:
        self._running = False
