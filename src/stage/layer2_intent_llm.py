"""
Layer 2: Musical Intent & Factual Deliberation Trace Layer (Ollama GPU Structured Outputs).

Strictly enforces `skills/chamber-music-diction/SKILL.md`:
1. Zero musical theory contradictions (e.g. "長三度の空虚五度", "スタッカートのレガート", etc.).
2. Zero AI poetry / clichés (e.g. "緊張を高める", "余白を残して", "解決を待つ", "共鳴", "紡ぐ", etc.).
3. Post-L3 Factual Memo Format (40-75 Japanese chars) with 3 concrete elements:
   - 【状況】Actual detected pitches & articulation from L1
   - 【迷い・却下理由】Rejected candidate ID & concrete acoustic/voice-leading flaw from L3
   - 【決定】Adopted candidate ID, concrete pitch register/notes, and pedal choice
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from src.stage.layer1_perception import PerceptionSnapshot, midi_to_note_name


class MusicalIntentSchema(BaseModel):
    monologue_ja: str = Field(
        ...,
        description="演奏家の実用メモ（状況・ボツ案と却下理由・採用案と音域/ペダル、40〜75文字、AIポエム・矛盾禁止）",
    )
    interaction_stance: Literal["listen", "counterpoint", "imitate", "provoke", "cadence"]
    tonal_center: str = Field(..., description="調性・旋法（例: D_dorian, G_minor, C_major）")
    chord_progression: List[str] = Field(..., min_length=2, max_length=8)
    target_density: float = Field(..., ge=0.0, le=1.0)
    register_range: List[int] = Field(..., min_length=2, max_length=2)
    pedal_strategy: Literal["dry", "half_clear", "resonant"]
    rejection_criteria: List[str] = Field(..., min_length=1, max_length=5)

    @field_validator("register_range")
    @classmethod
    def _validate_register(cls, val: List[int]) -> List[int]:
        lo = max(21, min(100, int(val[0])))
        hi = max(lo + 7, min(108, int(val[1])))
        return [lo, hi]


class FactualMonologueSchema(BaseModel):
    monologue_ja: str = Field(
        ...,
        description="40〜75文字の飾り気のない演奏メモ。①検出音と奏法 ②ボツ案IDと却下理由 ③採用案IDと音域・ペダルのみを事実で書く。",
    )


BANNED_PHRASES = (
    # Musical theory contradictions (literal)
    "長三度の空虚五度",
    "短三度の空虚五度",
    "三度を含む空虚五度",
    "長3度の空虚5度",
    "短3度の空虚5度",
    "3度を含む空虚5度",
    "単音の和音",
    "単音による和音",
    "無音の強奏",
    "休符の強奏",
    "スタッカートのレガート",
    "レガートのスタッカート",
    "ペダル無しの深い残響",
    "ノンペダルの深い残響",
    "ドライな深い残響",
    # Unnatural / clipped / command-like / theatrical LLM phrasing
    "演奏せよ",
    "せよ。",
    "奏でよう",
    "しよう。",
    "棄ち",
    "G小調",
    "D小調",
    "F大調",
    "Dドリア",
    # AI poetry / clichés / filler
    "緊張を高める",
    "緊張を高め",
    "余白を残して",
    "余白を残し",
    "解決を待つ",
    "解決を待ち",
    "共鳴",
    "呼応し",
    "呼応する",
    "寄り添う",
    "寄り添い",
    "紡ぐ",
    "紡ぎ",
    "織りなす",
    "織り成す",
    "悲痛",
    "歓喜",
    "魂",
    "アルゴリズム",
    "回路",
    "静寂を切り裂く",
    "だろう",
    "かもしれない",
    # Flattery / meta / symbols
    "素晴らしい",
    "感動的",
    "美しい演奏",
    "ありがとう",
    "私のアルゴリズム",
    "心の震え",
    "AIとして",
    "THINKING",
    "NEURAL",
    "!",
    "！",
    "♪",
    "✨",
    "🎹",
    "🎻",
    "🔥",
)

CONTRADICTION_REGEXES = (
    re.compile(r"(長三度|短三度|長3度|短3度|三度|3度).{0,8}空虚(五度|5度)"),
    re.compile(r"空虚(五度|5度).{0,8}(長三度|短三度|長3度|短3度|三度|3度)(?!を省|を抜|なし)"),
    re.compile(r"(ペダル無し|ノンペダル|ドライ).{0,6}(深い残響|豊かな残響|長い残響)"),
    re.compile(r"(スタッカート).{0,4}(レガート)|(レガート).{0,4}(スタッカート)"),
    re.compile(r"単音.{0,4}(和音|コード|トライアド)"),
    re.compile(r"(無音|休符).{0,4}(強奏|フォルテ|打鍵)"),
)

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


class MusicalLogicValidator:
    """
    Programmatic censor & consistency checker for chamber-music deliberation notes.
    Rejects musical contradictions, AI poetry clichés, and L1/L3 factual mismatches.
    """

    MIN_LEN = 40
    MAX_LEN = 75

    @classmethod
    def check_text(
        cls,
        text: str,
        perception: Optional[PerceptionSnapshot] = None,
        adopted_candidate: Optional[Any] = None,
        rejected_candidates: Optional[List[Any]] = None,
    ) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        t = (text or "").strip()
        if not t:
            return False, ["empty"]

        if EMOJI_PATTERN.search(t):
            violations.append("emoji_detected")

        for banned in BANNED_PHRASES:
            if banned in t:
                violations.append(f"banned_phrase:{banned}")

        for pat in CONTRADICTION_REGEXES:
            if pat.search(t):
                violations.append(f"contradiction_regex:{pat.pattern}")

        if len(t) < cls.MIN_LEN:
            violations.append(f"too_short:{len(t)}")
        elif len(t) > cls.MAX_LEN:
            violations.append(f"too_long:{len(t)}")

        # Unnatural particle concatenation before ため ("不足ため", "十分ため")
        if re.search(r"(不足|停滞|衝突|過密|発生)ため", t):
            violations.append("unnatural_particle_before_tame")
        if not (t.endswith("。") or t.endswith("る") or t.endswith("く") or t.endswith("す") or t.endswith("む")):
            violations.append("missing_natural_ending")
        if t.endswith("う。") or t.endswith("よう。"):
            violations.append("theatrical_volitional_ending")

        if perception is not None:
            if perception.mode == "cello" and ("ピアノ打鍵を検出" in t or "鍵盤の終止を検出" in t):
                violations.append("mode_mismatch:expected_cello")
            if perception.mode == "piano" and ("チェロが" in t or "チェロの" in t or "弓圧" in t):
                violations.append("mode_mismatch:expected_piano")
            if perception.articulation == "staccato" and "レガート" in t and "スタッカート" not in t:
                violations.append("articulation_mismatch:expected_staccato")
            if perception.articulation == "legato" and "スタッカート" in t and "レガート" not in t:
                violations.append("articulation_mismatch:expected_legato")

        # If L3 adopted/rejected candidates are supplied, require referencing a real candidate or register
        if adopted_candidate is not None and rejected_candidates:
            cid = getattr(adopted_candidate, "candidate_id", "")
            rej_ids = [getattr(r, "candidate_id", "") for r in rejected_candidates]
            has_adopted_ref = f"案{cid}" in t
            has_rej_ref = any(f"案{rid}" in t for rid in rej_ids if rid)
            if not (has_adopted_ref and has_rej_ref):
                violations.append("missing_l3_adopted_or_rejected_reference")
            # Require citing concrete note name in situation clause AND instrument/register
            has_note_token = bool(re.search(r"[A-G]#?\d", t))
            if not has_note_token:
                violations.append("missing_concrete_pitch_name")
            first_clause = t.split("。")[0]
            if not re.search(r"[A-G]#?\d", first_clause):
                violations.append("missing_human_pitch_in_situation_clause")
            if perception is not None:
                expected_inst = "チェロ" if perception.mode == "cello" else "ピアノ"
                if expected_inst not in t:
                    violations.append(f"missing_instrument:{expected_inst}")

        return len(violations) == 0, violations

    @classmethod
    def build_factual_memo(
        cls,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        pedal_strategy: str,
        adopted_candidate: Optional[Any] = None,
        rejected_candidates: Optional[List[Any]] = None,
    ) -> str:
        pitches = [
            int(n.get("pitch", n.get("note", 0)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ]
        if not pitches and perception.midi_pitch > 0:
            pitches = [int(perception.midi_pitch)]
        if not pitches:
            pitches = [50]

        first_name = midi_to_note_name(pitches[0])
        last_name = midi_to_note_name(pitches[-1])
        pitch_span = f"{first_name}→{last_name}" if first_name != last_name else last_name
        inst = "チェロ" if perception.mode == "cello" else "ピアノ"
        art_ja = "スタッカート" if perception.articulation == "staccato" else "レガート"

        # Part 1: 【状況】
        part1 = f"{inst}{pitch_span}の{art_ja}を検出。"

        # Part 2: 【迷い・却下理由】
        rej = None
        if rejected_candidates:
            # Prefer showing the dense/clash candidate (often D) or lowest-scored candidate
            rej = next((c for c in rejected_candidates if getattr(c, "candidate_id", "") == "D"), None)
            if rej is None:
                rej = min(rejected_candidates, key=lambda c: getattr(c, "total_score", 0.0))
        if rej is not None:
            rid = getattr(rej, "candidate_id", "D")
            r_detail = getattr(rej, "rejection_detail", {}) or {}
            short_r = str(r_detail.get("short_reason_ja") or "倍音域が濁る")
            part2 = f"案{rid}は{short_r}ためボツにし、"
        else:
            part2 = "案Dは中音域が濁るためボツにし、"

        # Part 3: 【決定】
        pedal_ja = {
            "dry": "ペダル無し",
            "half_clear": "浅いペダル",
            "resonant": "1/2ペダル",
        }.get(pedal_strategy, "浅いペダル")

        if adopted_candidate is not None:
            aid = getattr(adopted_candidate, "candidate_id", "A")
            arange = getattr(adopted_candidate, "pitch_range_str", "A4-E5")
            part3 = f"案{aid}の{arange}を{pedal_ja}で置く。"
        else:
            part3 = f"案Aの高音域反行を{pedal_ja}で置く。"

        memo = f"{part1}{part2}{part3}"
        # Guarantee exact length window [40, 75]
        if len(memo) > cls.MAX_LEN:
            memo = f"{inst}{last_name}の{art_ja}を検出。案{getattr(rej, 'candidate_id', 'D') if rej else 'D'}は濁るため捨て、案{getattr(adopted_candidate, 'candidate_id', 'A') if adopted_candidate else 'A'}の{getattr(adopted_candidate, 'pitch_range_str', 'A4-E5') if adopted_candidate else 'A4-E5'}を{pedal_ja}で置く。"
        if len(memo) > cls.MAX_LEN:
            memo = memo[: cls.MAX_LEN - 1] + "。"
        if len(memo) < cls.MIN_LEN:
            memo = f"{inst}{pitch_span}の{art_ja}を確認。{part2}{part3}"
        return memo


def validate_chamber_music_monologue(
    text: str,
    perception: Optional[PerceptionSnapshot] = None,
    adopted_candidate: Optional[Any] = None,
    rejected_candidates: Optional[List[Any]] = None,
) -> bool:
    ok, _ = MusicalLogicValidator.check_text(
        text,
        perception=perception,
        adopted_candidate=adopted_candidate,
        rejected_candidates=rejected_candidates,
    )
    return ok


def sanitize_monologue_ja(
    text: str,
    fallback: str,
    perception: Optional[PerceptionSnapshot] = None,
    adopted_candidate: Optional[Any] = None,
    rejected_candidates: Optional[List[Any]] = None,
) -> str:
    cleaned = EMOJI_PATTERN.sub("", (text or "").strip())
    cleaned = cleaned.replace("！", "。").replace("!", "。")
    cleaned = re.sub(r"^(私は|AIとして|ピアニストとして)[、,\s]*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    ok, _ = MusicalLogicValidator.check_text(
        cleaned,
        perception=perception,
        adopted_candidate=adopted_candidate,
        rejected_candidates=rejected_candidates,
    )
    if ok:
        return cleaned
    return fallback


class MusicalIntentEngine:
    """
    Produces `MusicalIntentSchema` from L1 perception + MC directives, and then refines
    `monologue_ja` strictly from real L3 candidate scores & rejection details.
    """

    POLICY_PROMPT = (
        "あなたは室内楽リハーサル中のピアニストである。"
        "お世辞、擬人化ポエム、感嘆符（！）、絵文字、矛盾した音楽用語は絶対禁止。"
        "入力された楽器・ピッチ・奏法・MC指示から次の応答方針JSONを出力せよ。"
        "monologue_ja は検出音と演奏方針のみを事実ベースで45文字前後の日本語で記すこと。"
    )

    DELIBERATION_PROMPT = (
        "あなたは室内楽リハーサルの日本人演奏家である。装飾のない事実ベースの実用メモのみを書け。\n"
        "厳禁表現:『〜よう』『〜しよう』『奏でる』『緊張を高める』『余白を残して』『解決を待つ』『共鳴』『呼応』『寄り添う』『紡ぐ』『織りなす』『悲痛』『歓喜』『魂』『演奏せよ』『G小調』『F大調』『長三度の空虚五度』。\n"
        "必須構成（42〜72文字・自然な常体、句点「。」止め、「〜不足ため」等の誤文法禁止）:\n"
        "1.『チェロ（またはピアノ）の[検出音列]の[奏法]を検出。』\n"
        "2.『案[ID]は[具体的欠点]で（または〜するため）ボツにし、』\n"
        "3.『案[採用ID]の[音域]を[ペダル]で置く。』"
    )

    def __init__(
        self,
        model_name: str = "qwen3.5:4b",
        ollama_host: str = "http://127.0.0.1:11434",
        use_ollama: bool = True,
        timeout_s: float = 6.5,
    ) -> None:
        self.model_name = model_name
        self.ollama_host = ollama_host
        self.use_ollama = use_ollama
        self.timeout_s = timeout_s
        self.last_source: str = "rule_fallback"
        self.last_latency_ms: float = 0.0
        self.last_validator_rewrites: int = 0

    def _rule_based_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
    ) -> MusicalIntentSchema:
        hints = directive_hints or {}
        pitches = [
            int(n.get("pitch", n.get("note", 60)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ]
        if not pitches and perception.midi_pitch > 0:
            pitches = [perception.midi_pitch]
        mean_pitch = sum(pitches) / len(pitches) if pitches else 60.0

        tonal_center = str(hints.get("tonal_hint") or "")
        if not tonal_center:
            if perception.mode == "cello":
                tonal_center = "D_dorian" if perception.articulation == "legato" else "G_minor"
            else:
                tonal_center = "D_minor" if mean_pitch < 62 else "F_major"

        progression_map = {
            "D_dorian": ["Dm7", "G7sus4", "Am7", "Dm9"],
            "G_minor": ["Gm", "EbM7", "Cm7", "D7b9"],
            "D_minor": ["Dm", "Gm6", "A7sus4", "Dm"],
            "F_major": ["FM7", "Dm7", "BbM7", "C9sus4"],
            "C_major": ["CM7", "Am7", "Dm7", "G7sus4"],
            "A_minor": ["Am7", "Dm7", "E7sus4", "Am9"],
        }
        chords = progression_map.get(tonal_center, ["Dm7", "Gm7", "A7sus4", "Dm"])

        if perception.mode == "cello":
            if mean_pitch >= 62:
                reg = [38, 60]
            else:
                reg = [69, 91]
        else:
            if mean_pitch >= 66:
                reg = [36, 59]
            else:
                reg = [65, 89]

        stance: Literal["listen", "counterpoint", "imitate", "provoke", "cadence"] = hints.get(
            "stance_hint",
            "counterpoint" if perception.articulation == "staccato" else "imitate",
        )
        if perception.turn_ready and stance == "listen":
            stance = "counterpoint"

        pedal_strategy: Literal["dry", "half_clear", "resonant"] = hints.get(
            "pedal_hint",
            "dry" if perception.articulation == "staccato" else "half_clear",
        )

        base_density = 0.52 if perception.articulation == "staccato" else 0.38
        target_density = max(0.20, min(0.85, base_density + float(hints.get("density_delta", 0.0))))

        rejection_criteria = [
            f"{midi_to_note_name(int(mean_pitch))}倍音域との短2度衝突" if perception.mode == "cello" else "中音域の三度重複",
            "同音反復による声部停滞",
            "並達五度・八度の発生",
        ]

        factual_memo = MusicalLogicValidator.build_factual_memo(
            perception=perception,
            human_notes=human_notes,
            pedal_strategy=pedal_strategy,
        )

        return MusicalIntentSchema(
            monologue_ja=factual_memo,
            interaction_stance=stance,
            tonal_center=tonal_center,
            chord_progression=chords,
            target_density=round(target_density, 2),
            register_range=reg,
            pedal_strategy=pedal_strategy,
            rejection_criteria=rejection_criteria,
        )

    def decide_policy(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
    ) -> MusicalIntentSchema:
        """
        Determines musical stance, tonal center, register separation, and pedal policy from L1 + MC hints.
        """
        return self._rule_based_intent(perception, human_notes, directive_hints)

    def finalize_deliberation_monologue(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        turn_result: Any,
        directive_hints: Optional[Dict[str, Any]] = None,
    ) -> MusicalIntentSchema:
        """
        Produces the final factual performer memo (`monologue_ja`) AFTER L3 candidates are scored,
        grounding the Ollama output in the real adopted/rejected candidates and validating against
        `MusicalLogicValidator`.
        """
        t0 = time.perf_counter()
        adopted = getattr(turn_result, "adopted_candidate", None)
        candidates = getattr(turn_result, "candidates", []) or []
        rejected = [c for c in candidates if not getattr(c, "adopted", False)]

        fallback_memo = MusicalLogicValidator.build_factual_memo(
            perception=perception,
            human_notes=human_notes,
            pedal_strategy=intent.pedal_strategy,
            adopted_candidate=adopted,
            rejected_candidates=rejected,
        )

        if not self.use_ollama:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return intent.model_copy(update={"monologue_ja": fallback_memo})

        tonal_ja_map = {
            "D_dorian": "Dドリア旋法",
            "D_minor": "Dマイナー",
            "G_minor": "Gマイナー",
            "C_major": "Cメジャー",
            "F_major": "Fメジャー",
            "A_minor": "Aマイナー",
        }
        tonal_ja = tonal_ja_map.get(intent.tonal_center, intent.tonal_center)

        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            pitches = [
                int(n.get("pitch", n.get("note", 60)))
                for n in human_notes[-6:]
                if int(n.get("pitch", n.get("note", 0))) > 0
            ]
            pitch_names = [midi_to_note_name(p) for p in pitches] or [perception.note_name]
            rej_summaries = [
                f"案{c.candidate_id}({c.pitch_range_str}): {c.rejection_detail.get('short_reason_ja', c.rejection_tag_ja)}"
                for c in rejected
            ]
            adopted_summary = (
                f"案{adopted.candidate_id}({adopted.pitch_range_str}, 音列={adopted.sample_notes_str}, スコア={adopted.total_score})"
                if adopted is not None
                else "案A"
            )
            pedal_ja = {"dry": "ペダル無し", "half_clear": "浅いペダル", "resonant": "1/2ペダル"}.get(
                intent.pedal_strategy, "浅いペダル"
            )
            user_prompt = (
                f"検出楽器={'チェロ' if perception.mode == 'cello' else 'ピアノ'}, "
                f"検出音列={'→'.join(pitch_names)}, 奏法={'スタッカート' if perception.articulation == 'staccato' else 'レガート'}, "
                f"調性={tonal_ja}, ペダル={pedal_ja}, MC指示={directive_hints or {}}\n"
                f"ボツ案: {' / '.join(rej_summaries)}\n"
                f"採用案: {adopted_summary}\n"
                "上記の実データだけを使って42〜72文字の自然な日本語メモ（句点「。」止め）をJSONで出力せよ。"
            )

            final_memo: Optional[str] = None
            for attempt in range(2):
                prompt_msg = (
                    user_prompt
                    if attempt == 0
                    else user_prompt
                    + "\n警告: 前回は禁止表現・矛盾・文字数逸脱があった。必ず42〜72文字で「検出音」「ボツ案IDと理由」「採用案IDと音域・ペダル」を事実のみで書け。"
                )
                resp = client.chat(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self.DELIBERATION_PROMPT},
                        {"role": "user", "content": prompt_msg},
                    ],
                    format=FactualMonologueSchema.model_json_schema(),
                    options={
                        "temperature": 0.15,
                        "num_ctx": 1024,
                        "num_predict": 110,
                    },
                    keep_alive="15m",
                    think=False,
                )
                raw_content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
                data = json.loads(raw_content)
                cand_text = str(data.get("monologue_ja", "")).strip()
                cleaned = EMOJI_PATTERN.sub("", cand_text).replace("！", "。").replace("!", "。")
                ok, _ = MusicalLogicValidator.check_text(
                    cleaned,
                    perception=perception,
                    adopted_candidate=adopted,
                    rejected_candidates=rejected,
                )
                if ok:
                    final_memo = cleaned
                    break

            if final_memo is None:
                self.last_validator_rewrites += 1
                final_memo = fallback_memo

            self.last_source = f"ollama:{self.model_name}"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return intent.model_copy(update={"monologue_ja": final_memo})
        except Exception:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return intent.model_copy(update={"monologue_ja": fallback_memo})

    def decide_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
        turn_result: Optional[Any] = None,
    ) -> MusicalIntentSchema:
        policy = self.decide_policy(perception, human_notes, directive_hints)
        if turn_result is not None:
            return self.finalize_deliberation_monologue(
                perception, human_notes, policy, turn_result, directive_hints
            )

        t0 = time.perf_counter()
        if not self.use_ollama:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return policy

        # Lightweight Ollama call when invoked standalone without L3 turn_result
        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            pitches = [
                int(n.get("pitch", n.get("note", 60)))
                for n in human_notes[-6:]
                if int(n.get("pitch", n.get("note", 0))) > 0
            ]
            pitch_names = [midi_to_note_name(p) for p in pitches] or [perception.note_name]
            user_prompt = (
                f"楽器={'チェロ' if perception.mode == 'cello' else 'ピアノ'}, "
                f"音列={'→'.join(pitch_names)}, 奏法={perception.articulation}, "
                f"採用音域=MIDI {policy.register_range[0]}..{policy.register_range[1]}, "
                f"ペダル={policy.pedal_strategy}, 指示={directive_hints or {}}\n"
                "案Dを倍音濁りでボツにし案Aを採用する42〜72文字の実用メモをJSONで出力せよ。"
            )
            resp = client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.DELIBERATION_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                format=FactualMonologueSchema.model_json_schema(),
                options={"temperature": 0.15, "num_ctx": 1024, "num_predict": 110},
                keep_alive="15m",
                think=False,
            )
            raw_content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
            data = json.loads(raw_content)
            memo = sanitize_monologue_ja(
                str(data.get("monologue_ja", "")),
                policy.monologue_ja,
                perception=perception,
            )
            self.last_source = f"ollama:{self.model_name}"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return policy.model_copy(update={"monologue_ja": memo})
        except Exception:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return policy
