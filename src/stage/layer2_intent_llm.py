"""
Layer 2: Autonomous Musical Reasoning, TokenEconomizer Compression & Factual Deliberation Trace.

Enforces:
1. First-Principles Musical Reasoning: LLM designs two-hand voicing structure, right-hand contour,
   left-hand bass/inner motion, 4 distinct candidate hypotheses, and continuous dual-pedal (CC#64/CC#67)
   curves — NEVER relying on hardcoded "〇〇風" style presets.
2. `TokenEconomizer` integration + RTX 3070 (`keep_alive=-1`) VRAM pinning for minimum-latency Ollama calls.
3. `MusicalLogicValidator` censorship: zero contradictions, zero AI poetry clichés, zero "〇〇風" labels,
   and strict 45-74 char factual performer memo citing L1 pitches, L3 rejections, two-hand voicing & pedals.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from src.stage.layer1_perception import PerceptionSnapshot, midi_to_note_name
from src.stage.token_economizer import TokenEconomizer


class MusicalIntentSchema(BaseModel):
    monologue_ja: str = Field(
        ...,
        description="演奏家の実用メモ（状況・ボツ案と却下理由・採用案と両手音域/ペダル、45〜74文字、AIポエム・矛盾・〇〇風禁止）",
    )
    interaction_stance: Literal["listen", "counterpoint", "imitate", "provoke", "cadence"]
    tonal_center: str = Field(..., description="調性・旋法（例: D_dorian, G_minor, F_major, D_minor, A_minor）")
    chord_progression: List[str] = Field(..., min_length=2, max_length=8)
    target_density: float = Field(..., ge=0.0, le=1.0)
    register_range: List[int] = Field(..., min_length=2, max_length=2)
    pedal_strategy: Literal["dry", "half_clear", "resonant"]
    act_number: int = Field(default=1, ge=1, le=4)
    act_label_ja: str = Field(default="第1幕: 種子提示と問いかけ")
    phrase_function: Literal[
        "question_half_cadence",
        "dialogic_sequence",
        "polyphonic_climax",
        "recapitulation_resolution",
    ] = Field(default="question_half_cadence")
    voice_leading_bridge: str = Field(default="A4->A4")
    motivic_transform: str = Field(default="inversion_question")
    seed_motif_str: str = Field(default="--")
    is_retake: bool = Field(default=False)
    retake_summary_ja: str = Field(default="")
    voicing_structure: str = Field(
        default="LH_open5th_10th_arpeggio + RH_cantabile_9th",
        description="左手バス・内声と右手和音ボイシングの3〜5声配置設計",
    )
    rh_contour: str = Field(
        default="cantabile_gap_fill_single_apex",
        description="右手モチーフの音程コンター・掛留解決・跳躍回復・単一頂点方針",
    )
    lh_motion: str = Field(
        default="root_fifth_tenth_breath_inner_suspension",
        description="左手バス・内声の進行方針",
    )
    candidate_approaches: List[str] = Field(
        default_factory=lambda: [
            "thematic_act_polyphony",
            "pedal_point_suspension",
            "chromatic_modulation_inner",
            "contrastive_flaw_check",
        ],
        min_length=4,
        max_length=4,
    )
    damper_cc64_plan: List[int] = Field(default_factory=lambda: [0, 68, 108, 12, 86, 0])
    una_corda_cc67_plan: List[int] = Field(default_factory=lambda: [24, 72, 92, 52, 18])
    rejection_criteria: List[str] = Field(..., min_length=1, max_length=5)

    @field_validator("register_range")
    @classmethod
    def _validate_register(cls, val: List[int]) -> List[int]:
        lo = max(21, min(96, int(val[0])))
        hi = max(lo + 12, min(108, int(val[1])))
        return [lo, hi]


class LLMPolicyDesignSchema(BaseModel):
    interaction_stance: Literal["listen", "counterpoint", "imitate", "provoke", "cadence"]
    tonal_center: Literal["D_dorian", "D_minor", "G_minor", "C_major", "F_major", "A_minor"]
    phrase_function: Literal[
        "question_half_cadence",
        "dialogic_sequence",
        "polyphonic_climax",
        "recapitulation_resolution",
    ]
    voice_leading_bridge: str
    motivic_transform: str
    chord_progression: List[str] = Field(..., min_length=3, max_length=6)
    target_density: float = Field(..., ge=0.20, le=0.88)
    pedal_strategy: Literal["dry", "half_clear", "resonant"]
    voicing_structure: str
    rh_contour: str
    lh_motion: str
    candidate_approaches: List[str] = Field(..., min_length=4, max_length=4)
    damper_cc64_plan: List[int] = Field(..., min_length=4, max_length=6)
    una_corda_cc67_plan: List[int] = Field(..., min_length=4, max_length=6)


class FactualMonologueSchema(BaseModel):
    monologue_ja: str = Field(
        ...,
        description="45〜74文字の実用メモ。①検出楽器・音列・奏法 ②ボツ案IDと具体的欠点 ③採用案IDと両手ボイシング・両ペダル操作のみを事実で書く。",
    )


class WorkTitleSchema(BaseModel):
    title_ja: str = Field(..., description="調性と形式・楽器編成を示す飾りのない音楽的な日本語作品名（例: ニ短調の問いかけと五度の回想 — チェロと自動演奏ピアノのための対話的幻想曲）")
    title_en: str = Field(..., description="対応する英語タイトル")


class ActDesignSchema(BaseModel):
    act: int = Field(..., ge=1, le=4)
    chords: List[str] = Field(..., min_length=4, max_length=4)
    lh_texture: Literal[
        "open_arpeggio_10th",
        "broken_chord_barcarolle",
        "chorale_voicing",
        "countermelody_tenor",
        "alberti_lyrical",
    ]
    melody_offsets: List[int] = Field(..., min_length=8, max_length=8)
    dynamic_vel: int = Field(..., ge=48, le=90)


class LLMFullPieceDesignSchema(BaseModel):
    title_ja: str = Field(..., description="28文字以内の純日本語クラシック作品名（例: ニ短調の対話的幻想曲）")
    title_en: str = Field(..., description="Concise English classical title (max 60 chars)")
    tonal_center: str
    bpm: float = Field(..., ge=60.0, le=102.0)
    composer_note_ja: str = Field(..., description="改行なし1文(25-55文字)の和声・終止メモ")
    seed_degrees: List[int] = Field(..., min_length=6, max_length=8)
    acts: List[ActDesignSchema] = Field(..., min_length=4, max_length=4)


BANNED_PHRASES = (
    # Musical theory contradictions
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
    # Banned composer/genre "〇〇風" imitation presets & labels
    "バロック風",
    "印象派風",
    "ロマン派風",
    "古典派風",
    "現代音楽風",
    "ドビュッシー風",
    "バッハ風",
    "ショパン風",
    "ブラームス風",
    "フォーレ風",
    "リスト風",
    "ラフマニノフ風",
    "カノン風",
    "ジャズ風",
    "クラシック風",
    "風な",
    "風に",
    # Unnatural / theatrical LLM phrasing
    "演奏せよ",
    "せよ。",
    "奏でよう",
    "しよう。",
    "棄ち",
    "G小調",
    "D小調",
    "F大調",
    "Dドリア",
    # AI poetry / clichés
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
    re.compile(r"(バロック|印象派|ロマン派|古典派|現代|バッハ|ドビュッシー|ショパン|ブラームス|フォーレ|リスト|ラフマニノフ|モーツァルト|ベートーヴェン|カノン|ジャズ|クラシック|古)風"),
    re.compile(r"[A-G][b#]?\s*[小大]調"),
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
    Rejects musical contradictions, AI poetry clichés, "〇〇風" style labels, and L1/L3 mismatches.
    """

    MIN_LEN = 45
    MAX_LEN = 74

    @classmethod
    def check_text(
        cls,
        text: str,
        perception: Optional[PerceptionSnapshot] = None,
        adopted_candidate: Optional[Any] = None,
        rejected_candidates: Optional[List[Any]] = None,
        human_notes: Optional[List[Dict[str, Any]]] = None,
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

        if re.search(r"(不足|停滞|衝突|過密|発生|単調|欠如|次善|不適|混濁|重複|低下)ため", t):
            violations.append("unnatural_particle_before_tame")
        if re.search(r"\b(staccato|legato|piano|cello)\b", t, flags=re.IGNORECASE):
            violations.append("raw_english_term_in_japanese_memo")
        if not (t.endswith("。") or t.endswith("る") or t.endswith("く") or t.endswith("す") or t.endswith("む")):
            violations.append("missing_natural_ending")
        if t.endswith("う。") or t.endswith("よう。"):
            violations.append("theatrical_volitional_ending")

        if perception is not None:
            if perception.mode == "cello" and ("ピアノ打鍵を検出" in t or "鍵盤の終止を検出" in t or "連弾" in t):
                violations.append("mode_mismatch:expected_cello")
            if perception.mode == "piano" and ("チェロが" in t or "チェロの" in t or "弓圧" in t):
                violations.append("mode_mismatch:expected_piano")
            if perception.articulation == "staccato" and "レガート" in t and "スタッカート" not in t:
                violations.append("articulation_mismatch:expected_staccato")
            if perception.articulation == "legato" and "スタッカート" in t and "レガート" not in t:
                violations.append("articulation_mismatch:expected_legato")

        if adopted_candidate is not None and rejected_candidates:
            cid = getattr(adopted_candidate, "candidate_id", "")
            rej_ids = [getattr(r, "candidate_id", "") for r in rejected_candidates]
            has_adopted_ref = f"案{cid}" in t
            has_rej_ref = any(f"案{rid}" in t for rid in rej_ids if rid)
            if not (has_adopted_ref and has_rej_ref):
                violations.append("missing_l3_adopted_or_rejected_reference")
            if not re.search(r"[A-G]#?\d", t):
                violations.append("missing_concrete_pitch_name")
            first_clause = t.split("。")[0]
            if not re.search(r"[A-G]#?\d", first_clause):
                violations.append("missing_human_pitch_in_situation_clause")
            else:
                # Ensure first clause cites an actual human detected pitch, not an AI piano candidate range
                expected_human_names = set()
                if perception is not None and getattr(perception, "note_name", "--") not in ("", "--"):
                    expected_human_names.add(perception.note_name)
                if human_notes:
                    for hn in human_notes:
                        p = int(hn.get("pitch", hn.get("note", 0)) or 0)
                        if p > 0:
                            expected_human_names.add(midi_to_note_name(p))
                elif perception is not None and getattr(perception, "recent_human_notes", None):
                    for hn in perception.recent_human_notes:
                        p = int(hn.get("pitch", hn.get("note", 0)) or 0)
                        if p > 0:
                            expected_human_names.add(midi_to_note_name(p))
                if expected_human_names and not any(nm in first_clause for nm in expected_human_names):
                    violations.append("situation_clause_cited_wrong_pitch")
            if perception is not None:
                expected_inst = "チェロ" if perception.mode == "cello" else "ピアノ"
                if expected_inst not in t:
                    violations.append(f"missing_instrument:{expected_inst}")
            if not any(kw in t for kw in ("ペダル", "ウナ", "踏み替え", "ハーフ", "ドライ")):
                violations.append("missing_pedal_reference")

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

        part1 = f"{inst}{pitch_span}の{art_ja}を検出。"

        rej = None
        if rejected_candidates:
            rej = next((c for c in rejected_candidates if getattr(c, "candidate_id", "") == "D"), None)
            if rej is None:
                rej = min(rejected_candidates, key=lambda c: getattr(c, "total_score", 0.0))
        if rej is not None:
            rid = getattr(rej, "candidate_id", "D")
            r_detail = getattr(rej, "rejection_detail", {}) or {}
            short_r = str(r_detail.get("short_reason_ja") or "単調音階で和声を欠く")
            # Keep natural Japanese syntax before 捨て / ボツにし
            part2 = f"案{rid}は{short_r}ため捨て、"
        else:
            part2 = "案Dは中域混濁のため捨て、"

        pedal_ja = {
            "dry": "浅い踏み替え",
            "half_clear": "両ペダル制御",
            "resonant": "ウナ・コルダ＋踏替",
        }.get(pedal_strategy, "両ペダル制御")

        aid = getattr(adopted_candidate, "candidate_id", "A") if adopted_candidate is not None else "A"
        arange = getattr(adopted_candidate, "pitch_range_str", "D2-E5") if adopted_candidate is not None else "D2-E5"
        part3 = f"案{aid}の両手和音{arange}を{pedal_ja}で置く。"

        memo = f"{part1}{part2}{part3}"
        if len(memo) > cls.MAX_LEN:
            part1_s = f"{inst}{last_name}の{art_ja}を検出。"
            part2_s = f"案{getattr(rej, 'candidate_id', 'D') if rej else 'D'}は濁るため捨て、"
            part3_s = f"案{aid}({arange})を{pedal_ja}で置く。"
            memo = f"{part1_s}{part2_s}{part3_s}"
        if len(memo) > cls.MAX_LEN:
            memo = memo[: cls.MAX_LEN - 1] + "。"
        if len(memo) < cls.MIN_LEN:
            memo = f"{inst}{pitch_span}の{art_ja}を確認。{part2}{part3}"
        if len(memo) > cls.MAX_LEN:
            memo = memo[: cls.MAX_LEN - 1] + "。"
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
    First-Principles Musical Reasoning Engine (L2).
    Uses TokenEconomizer + RTX 3070 Ollama (`keep_alive=-1`) to design harmony,
    two-hand voicing, contours, and continuous dual-pedal plans, then generates a
    strict factual rehearsal memo after L3 MusicalityCritic evaluation.
    """

    POLICY_PROMPT = (
        "あなたは室内楽のピアニスト兼作曲家である。〇〇風という固定ラベルは絶対禁止。"
        "圧縮入力(IN/FORM/HIST/CORPUS/RULE)の現在の幕(Act 1=問いかけ半終止, Act 2=模進発展, Act 3=多声クライマックス, Act 4=ウナ・コルダ回想と解決)・"
        "冒頭種子動機(Seed)・直前終止からの声部ブリッジ(Br)・回避帯域・参照ボイシングから、"
        "phrase_function(問いかけ/模進/頂点/終止)、voice_leading_bridge、motivic_transform、"
        "4候補の作曲方針(A:主題展開, B:バス保続音と掛留解決, C:内声半音階と近親調転調, D:比較棄却案)、"
        "両手ボイシング、和声進行、連続CC#64/CC#67ペダル曲線(0..127)を自律設計しJSONで出力せよ。"
    )

    DELIBERATION_PROMPT = (
        "あなたは室内楽リハーサルの日本人演奏家である。装飾のない事実ベースの実用メモのみを書け。\n"
        "厳禁表現:『〜風』『〜よう』『〜しよう』『奏でる』『緊張を高める』『余白を残して』『解決を待つ』『共鳴』『呼応』『寄り添う』『紡ぐ』『織りなす』『悲痛』『歓喜』『魂』『演奏せよ』『G小調』『F大調』『長三度の空虚五度』。\n"
        "必須構成（46〜72文字・常体、句点「。」止め、「〜不足ため」等の誤文法禁止）:\n"
        "1.『チェロ（またはピアノ）[音列]の[奏法]を検出。』\n"
        "2.『案[ID]は[具体的欠点]ため捨て、』\n"
        "3.『案[採用ID]の両手[音域]を[ペダル/ウナ・コルダ操作]で置く。』"
    )

    def __init__(
        self,
        model_name: str = "qwen3.5:4b",
        ollama_host: str = "http://127.0.0.1:11434",
        use_ollama: bool = True,
        timeout_s: float = 8.0,
        economizer: Optional[TokenEconomizer] = None,
    ) -> None:
        self.model_name = model_name
        self.ollama_host = ollama_host
        self.use_ollama = use_ollama
        self.timeout_s = timeout_s
        self.economizer = economizer or TokenEconomizer()
        self.last_source: str = "rule_fallback"
        self.last_latency_ms: float = 0.0
        self.last_ttft_ms: float = 0.0
        self.last_validator_rewrites: int = 0
        self.last_token_stats: Dict[str, Any] = dict(self.economizer.last_stats)
        self.last_compact_prompt: str = ""

    def _chat_with_ttft(
        self,
        client: Any,
        messages: List[Dict[str, str]],
        fmt_schema: Dict[str, Any],
        options: Dict[str, Any],
    ) -> str:
        t0 = time.perf_counter()
        ttft_ms: Optional[float] = None
        chunks: List[str] = []
        for ch in client.chat(
            model=self.model_name,
            messages=messages,
            format=fmt_schema,
            options=options,
            keep_alive=-1,
            think=False,
            stream=True,
        ):
            if ttft_ms is None:
                ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            txt = ch["message"]["content"] if isinstance(ch, dict) else ch.message.content
            if txt:
                chunks.append(txt)
        if ttft_ms is not None:
            self.last_ttft_ms = ttft_ms
        else:
            self.last_ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return "".join(chunks)

    @staticmethod
    def _macro_dict(macro_plan: Optional[Any]) -> Dict[str, Any]:
        if macro_plan is None:
            return {}
        if isinstance(macro_plan, dict):
            return dict(macro_plan)
        return {
            "act_number": getattr(macro_plan, "act_number", 1),
            "act_label_ja": getattr(macro_plan, "act_label_ja", "第1幕: 種子提示と問いかけ"),
            "phrase_function": getattr(macro_plan, "phrase_function", "question_half_cadence"),
            "target_tonality": getattr(macro_plan, "target_tonality", "D_dorian"),
            "recommended_chords": list(getattr(macro_plan, "recommended_chords", []) or []),
            "motivic_transform": getattr(macro_plan, "motivic_transform", "inversion_question"),
            "seed_motif_str": getattr(macro_plan, "seed_motif_str", "--"),
            "voice_leading_bridge_desc": getattr(macro_plan, "voice_leading_bridge_desc", "A4->A4"),
            "target_apex_pitch": getattr(macro_plan, "target_apex_pitch", 79),
            "pedal_cc64_hint": list(getattr(macro_plan, "pedal_cc64_hint", []) or []),
            "pedal_cc67_hint": list(getattr(macro_plan, "pedal_cc67_hint", []) or []),
            "is_retake": bool(getattr(macro_plan, "is_retake", False)),
            "retake_diff_ja": str(getattr(macro_plan, "retake_diff_ja", "")),
        }

    def _rule_based_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
        macro_plan: Optional[Any] = None,
    ) -> MusicalIntentSchema:
        hints = directive_hints or {}
        macro = self._macro_dict(macro_plan)
        pitches = [
            int(n.get("pitch", n.get("note", 60)))
            for n in human_notes
            if int(n.get("pitch", n.get("note", 0))) > 0
        ]
        if not pitches and perception.midi_pitch > 0:
            pitches = [perception.midi_pitch]
        mean_pitch = sum(pitches) / len(pitches) if pitches else 55.0

        tonal_center = str(hints.get("tonal_hint") or macro.get("target_tonality") or "")
        if not tonal_center:
            if perception.mode == "cello":
                tonal_center = "D_dorian" if perception.articulation == "legato" else "G_minor"
            else:
                tonal_center = "D_minor" if mean_pitch < 62 else "F_major"

        progression_map = {
            "D_dorian": ["Dm9", "Gm11", "BbM7#11", "A7sus4"],
            "G_minor": ["Gm9", "EbM7#11", "Cm11", "D7b9"],
            "D_minor": ["Dm", "Gm6", "BbM7", "A7sus4"],
            "F_major": ["FM9", "Dm11", "BbM7#11", "C9sus4"],
            "C_major": ["CM9", "Am11", "Fmaj9", "G13sus4"],
            "A_minor": ["Am9", "Dm11", "Fmaj7#11", "E7sus4"],
        }
        chords = list(macro.get("recommended_chords") or progression_map.get(tonal_center, ["Dm9", "Gm11", "A7sus4", "Dm9"]))

        # Wide two-hand register framing around human voice to preserve 100-600Hz clarity
        if perception.mode == "cello":
            reg = [36, 86] if mean_pitch < 62 else [34, 81]
        else:
            reg = [36, 88]

        act_num = int(macro.get("act_number", 1))
        act_label = str(macro.get("act_label_ja") or "第1幕: 種子提示と問いかけ")
        phrase_func = str(macro.get("phrase_function") or "question_half_cadence")

        default_stance = (
            "cadence" if act_num == 4
            else ("provoke" if act_num == 3 else ("counterpoint" if perception.articulation == "staccato" else "imitate"))
        )
        stance: Literal["listen", "counterpoint", "imitate", "provoke", "cadence"] = hints.get(
            "stance_hint",
            default_stance,
        )
        if perception.turn_ready and stance == "listen":
            stance = "counterpoint"

        pedal_strategy: Literal["dry", "half_clear", "resonant"] = hints.get(
            "pedal_hint",
            "resonant" if act_num in (3, 4) else ("dry" if perception.articulation == "staccato" else "half_clear"),
        )

        base_density = 0.68 if act_num == 3 else (0.38 if act_num == 4 else (0.56 if perception.articulation == "staccato" else 0.46))
        target_density = max(0.22, min(0.86, base_density + float(hints.get("density_delta", 0.0))))

        if macro.get("pedal_cc64_hint") and macro.get("pedal_cc67_hint"):
            cc64_plan = [max(0, min(127, int(v))) for v in macro["pedal_cc64_hint"]]
            cc67_plan = [max(0, min(127, int(v))) for v in macro["pedal_cc67_hint"]]
        elif pedal_strategy == "dry":
            cc64_plan = [0, 58, 0, 52, 0]
            cc67_plan = [18, 44, 62, 32, 0]
        elif pedal_strategy == "resonant":
            cc64_plan = [0, 88, 114, 14, 96, 0]
            cc67_plan = [64, 96, 114, 86, 48] if act_num == 4 else [14, 38, 22, 54, 18]
        else:
            cc64_plan = [0, 70, 96, 12, 78, 0]
            cc67_plan = [26, 68, 86, 54, 18]

        rejection_criteria = [
            "4音以上の単調な順次音階進行",
            f"{midi_to_note_name(int(mean_pitch))}倍音域との短2度衝突",
            "主題動機や跳躍回復・両手伴奏イディオムの欠如",
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
            act_number=act_num,
            act_label_ja=act_label,
            phrase_function=phrase_func,  # type: ignore[arg-type]
            voice_leading_bridge=str(macro.get("voice_leading_bridge_desc") or "A4->A4"),
            motivic_transform=str(macro.get("motivic_transform") or "inversion_question"),
            seed_motif_str=str(macro.get("seed_motif_str") or "--"),
            is_retake=bool(macro.get("is_retake", False)),
            retake_summary_ja=str(macro.get("retake_diff_ja") or ""),
            voicing_structure="LH_root_5th_10th_arpeggio + RH_cantabile_9th_spread",
            rh_contour="cantabile_gap_fill_single_apex",
            lh_motion="pedal_point_inner_chromatic_suspension",
            candidate_approaches=[
                "thematic_act_polyphony",
                "pedal_point_suspension",
                "chromatic_modulation_inner",
                "contrastive_flaw_check",
            ],
            damper_cc64_plan=cc64_plan,
            una_corda_cc67_plan=cc67_plan,
            rejection_criteria=rejection_criteria,
        )

    def decide_policy(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
        corpus_refs: Optional[List[Dict[str, Any]]] = None,
        use_llm_design: bool = True,
        macro_plan: Optional[Any] = None,
    ) -> MusicalIntentSchema:
        """
        Determines autonomous compositional policy (macro phrase function, voice-leading bridge,
        4 candidate strategies, voicing, contour, harmony, dual pedal curves) via TokenEconomizer
        + Ollama RTX 3070 (`keep_alive=-1`) with deterministic rule fallback.
        """
        macro_dict = self._macro_dict(macro_plan)
        base = self._rule_based_intent(perception, human_notes, directive_hints, macro_plan=macro_dict)
        econ = self.economizer.build_economized_context(
            perception=perception,
            human_notes=human_notes,
            corpus_refs=corpus_refs or [],
            directive_hints=directive_hints,
            macro_form_state=macro_dict,
        )
        self.last_token_stats = dict(self.economizer.last_stats)
        self.last_compact_prompt = econ.compact_prompt

        if not self.use_ollama or not use_llm_design or not human_notes:
            return base

        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            raw_content = self._chat_with_ttft(
                client,
                messages=[
                    {"role": "system", "content": self.POLICY_PROMPT},
                    {"role": "user", "content": econ.compact_prompt},
                ],
                fmt_schema=LLMPolicyDesignSchema.model_json_schema(),
                options={"temperature": 0.25, "num_ctx": 1536, "num_predict": 240},
            )
            designed = LLMPolicyDesignSchema.model_validate_json(raw_content)

            hints = directive_hints or {}
            tonal = str(hints.get("tonal_hint") or macro_dict.get("target_tonality") or designed.tonal_center)
            stance = hints.get("stance_hint") or designed.interaction_stance
            pedal_strat = hints.get("pedal_hint") or designed.pedal_strategy
            phrase_fn = macro_dict.get("phrase_function") or designed.phrase_function

            cc64 = [max(0, min(127, int(v))) for v in designed.damper_cc64_plan]
            cc67 = [max(0, min(127, int(v))) for v in designed.una_corda_cc67_plan]
            if max(cc64) < 40:
                cc64 = base.damper_cc64_plan
            if max(cc67) < 20:
                cc67 = base.una_corda_cc67_plan

            return base.model_copy(
                update={
                    "interaction_stance": stance,
                    "tonal_center": tonal,
                    "phrase_function": phrase_fn,
                    "voice_leading_bridge": (designed.voice_leading_bridge or base.voice_leading_bridge)[:48],
                    "motivic_transform": (designed.motivic_transform or base.motivic_transform)[:48],
                    "chord_progression": designed.chord_progression,
                    "target_density": float(designed.target_density),
                    "pedal_strategy": pedal_strat,
                    "voicing_structure": designed.voicing_structure[:72],
                    "rh_contour": designed.rh_contour[:56],
                    "lh_motion": designed.lh_motion[:56],
                    "candidate_approaches": designed.candidate_approaches,
                    "damper_cc64_plan": cc64,
                    "una_corda_cc67_plan": cc67,
                }
            )
        except Exception:
            return base

    def finalize_deliberation_monologue(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        intent: MusicalIntentSchema,
        turn_result: Any,
        directive_hints: Optional[Dict[str, Any]] = None,
        corpus_refs: Optional[List[Dict[str, Any]]] = None,
        macro_plan: Optional[Any] = None,
    ) -> MusicalIntentSchema:
        """
        Produces the final factual performer memo (`monologue_ja`) AFTER L3 MusicalityCritic
        scoring, compressing context with TokenEconomizer and validating with MusicalLogicValidator.
        """
        t0 = time.perf_counter()
        adopted = getattr(turn_result, "adopted_candidate", None)
        candidates = getattr(turn_result, "candidates", []) or []
        rejected = [c for c in candidates if not getattr(c, "adopted", False)]
        macro_dict = self._macro_dict(macro_plan)

        fallback_memo = MusicalLogicValidator.build_factual_memo(
            perception=perception,
            human_notes=human_notes,
            pedal_strategy=intent.pedal_strategy,
            adopted_candidate=adopted,
            rejected_candidates=rejected,
        )

        # Record outcome into TokenEconomizer failure-loop ledger
        rej_failures: List[str] = []
        for r in rejected:
            rej_failures.extend(list((getattr(r, "rejection_detail", {}) or {}).get("failures", [])))
        self.economizer.record_turn_outcome(intent.tonal_center, intent.chord_progression, rej_failures[:3])

        cand_summaries = [
            {
                "id": c.candidate_id,
                "score": c.total_score,
                "range": c.pitch_range_str,
                "reason": "ADOPTED" if c.adopted else (c.rejection_detail.get("short_reason_ja") or "rejected"),
            }
            for c in candidates
        ]
        econ = self.economizer.build_economized_context(
            perception=perception,
            human_notes=human_notes,
            corpus_refs=corpus_refs or [],
            directive_hints=directive_hints,
            candidate_summaries=cand_summaries,
            macro_form_state=macro_dict,
        )
        self.last_token_stats = dict(self.economizer.last_stats)
        self.last_compact_prompt = econ.compact_prompt

        if not self.use_ollama:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return intent.model_copy(update={"monologue_ja": fallback_memo})

        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            pedal_ja = {
                "dry": "浅いペダル踏み替え",
                "half_clear": "ハーフペダルとウナ・コルダ",
                "resonant": "ウナ・コルダ＋ペダル踏み替え",
            }.get(intent.pedal_strategy, "両ペダル制御")
            adopted_id = adopted.candidate_id if adopted is not None else "A"
            adopted_reg = adopted.pitch_range_str if adopted is not None else "D2-E5"
            adopted_notes = adopted.sample_notes_str if adopted is not None else "--"

            h_names = econ.human_summary.get("pitches_str", perception.note_name)
            user_prompt = (
                f"{econ.compact_prompt}\n"
                f"幕={intent.act_label_ja}, 人間検出音(1文目に必須)={h_names}, 採用=案{adopted_id}({adopted_reg}, {adopted_notes}), "
                f"ボイシング={intent.voicing_structure}, ペダル={pedal_ja}\n"
                "1文目は必ず人間検出音のみを使い、46〜72文字の自然な日本語メモ（〇〇風・ポエム・誤文法禁止、句点止め）をJSON出力せよ。"
            )

            final_memo: Optional[str] = None
            last_errs: List[str] = []
            for attempt in range(2):
                prompt_msg = (
                    user_prompt
                    if attempt == 0
                    else user_prompt
                    + f"\n修正要求({','.join(last_errs[:3])}): 必ず46〜72文字で「チェロ/ピアノ{h_names}の[奏法]を検出。」「案[ID]は[欠点]ため捨て、」「案{adopted_id}の両手{adopted_reg}を[ペダル/ウナ・コルダ]で置く。」を書け。"
                )
                raw_content = self._chat_with_ttft(
                    client,
                    messages=[
                        {"role": "system", "content": self.DELIBERATION_PROMPT},
                        {"role": "user", "content": prompt_msg},
                    ],
                    fmt_schema=FactualMonologueSchema.model_json_schema(),
                    options={"temperature": 0.12, "num_ctx": 1536, "num_predict": 110},
                )
                data = json.loads(raw_content)
                cand_text = str(data.get("monologue_ja", "")).strip()
                cleaned = EMOJI_PATTERN.sub("", cand_text).replace("！", "。").replace("!", "。")
                ok, errs = MusicalLogicValidator.check_text(
                    cleaned,
                    perception=perception,
                    adopted_candidate=adopted,
                    rejected_candidates=rejected,
                    human_notes=human_notes,
                )
                if ok:
                    final_memo = cleaned
                    break
                last_errs = errs
                for err in errs:
                    self.economizer.record_failure(f"memo:{err}")

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

    def generate_work_title(self, summary_ctx: Dict[str, Any]) -> Tuple[str, str]:
        """Generates a grounded, non-poetic musical work title via Ollama with fallback."""
        fb_ja = str(summary_ctx.get("fallback_title_ja", "ニ短調の対話的幻想曲 — 自動演奏ピアノと室内楽のために"))
        fb_en = str(summary_ctx.get("fallback_title_en", "Dialogic Fantasia for Chamber Instrument and Disklavier"))
        if not self.use_ollama:
            return fb_ja, fb_en
        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            prompt = (
                f"編成={summary_ctx.get('mode')}, 主調={summary_ctx.get('home_tonality')}, "
                f"冒頭動機={summary_ctx.get('seed_motif')}, ターン数={summary_ctx.get('turns_count')}, "
                f"総演奏時間={summary_ctx.get('total_duration_s')}秒\n"
                "気取った比喩・AIポエム語(魂/共鳴/寄り添う/紡ぐ/織りなす/悲痛/歓喜/〇〇風)や感嘆符を一切使わず、"
                "調性・動機特徴・楽器編成に基づく格調高く実用的なクラシック作品タイトル(日本語・英語)をJSON出力せよ。"
            )
            resp = client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "あなたはクラシック音楽の楽譜出版編集者である。調性と形式に基づく簡潔な作品名を付ける。"},
                    {"role": "user", "content": prompt},
                ],
                format=WorkTitleSchema.model_json_schema(),
                options={"temperature": 0.2, "num_ctx": 1024, "num_predict": 96},
                keep_alive=-1,
                think=False,
            )
            raw = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
            parsed = WorkTitleSchema.model_validate_json(raw)
            t_ja = EMOJI_PATTERN.sub("", parsed.title_ja).replace("！", "").replace("!", "").strip()
            t_en = EMOJI_PATTERN.sub("", parsed.title_en).replace("!", "").strip()
            if any(b in t_ja for b in BANNED_PHRASES) or any(p.search(t_ja) for p in CONTRADICTION_REGEXES):
                return fb_ja, fb_en
            if len(t_ja) < 6 or len(t_ja) > 64:
                return fb_ja, fb_en
            return t_ja, (t_en[:80] if len(t_en) >= 6 else fb_en)
        except Exception:
            return fb_ja, fb_en

    def decide_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
        turn_result: Optional[Any] = None,
    ) -> MusicalIntentSchema:
        policy = self.decide_policy(perception, human_notes, directive_hints, use_llm_design=False)
        if turn_result is not None:
            return self.finalize_deliberation_monologue(
                perception, human_notes, policy, turn_result, directive_hints
            )
        return policy

    def design_full_4act_piece(
        self,
        mode: str,
        tonality: str,
        bpm: float,
        seed_motif_hint: str,
        corpus_laws: Optional[Dict[str, Any]] = None,
        prog_variant: int = 0,
        retake_feedback: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """
        Uses Ollama (RTX 3070, keep_alive=-1, FlashAttention/Q8 KV) + TokenEconomizer
        to design a cohesive 4-Act polyphonic piece (functional chord progressions,
        cantabile melody degree contours, LH textures, dynamics, and grounded title).
        Falls back cleanly to functional harmony blueprints when Ollama is unavailable.
        """
        from src.stage.harmonic_beauty_realizer import (
            LH_TEXTURES,
            TONALITY_SPECS,
            build_functional_4act_chords,
        )

        ton_key = tonality if tonality in TONALITY_SPECS else "D_minor"
        t_spec = TONALITY_SPECS[ton_key]
        progs = build_functional_4act_chords(ton_key, variant=prog_variant)
        base_tex = LH_TEXTURES[prog_variant % len(LH_TEXTURES)]
        tex_2 = LH_TEXTURES[(prog_variant + 1) % len(LH_TEXTURES)]
        tex_3 = LH_TEXTURES[(prog_variant + 2) % len(LH_TEXTURES)]
        tex_4 = "chorale_voicing" if prog_variant % 2 == 0 else "open_arpeggio_10th"

        default_acts = [
            {"act": 1, "chords": progs[1], "lh_texture": base_tex, "melody_offsets": [0, 3, 2, 5, 4, 2, 5, 4], "dynamic_vel": 62},
            {"act": 2, "chords": progs[2], "lh_texture": tex_2, "melody_offsets": [2, 4, 6, 5, 7, 5, 8, 6], "dynamic_vel": 70},
            {"act": 3, "chords": progs[3], "lh_texture": tex_3, "melody_offsets": [4, 7, 5, 9, 7, 11, 8, 5], "dynamic_vel": 82},
            {"act": 4, "chords": progs[4], "lh_texture": tex_4, "melody_offsets": [4, 2, 5, 3, 2, 0, 2, 0], "dynamic_vel": 54},
        ]
        inst_ja = "チェロと自動演奏ピアノ" if mode == "cello" else "2台ピアノ連弾"
        inst_en = "Cello and Disklavier" if mode == "cello" else "Two Pianos"
        form_ja = ("幻想曲", "エレジー", "対話的前奏曲", "夜想曲")[prog_variant % 4]
        form_en = ("Fantasia", "Elegy", "Dialogic Prelude", "Nocturne")[prog_variant % 4]
        ton_ja = str(t_spec["ja"])
        ton_en = ton_key.replace("_", " ")
        fallback = {
            "title_ja": f"{ton_ja}の{form_ja} 第{prog_variant + 1}番 — {inst_ja}のための4幕対話",
            "title_en": f"{form_en} No.{prog_variant + 1} in {ton_en} for {inst_en}",
            "tonal_center": ton_key,
            "bpm": float(bpm),
            "seed_degrees": [0, 3, 2, 5, 4, 2, 5, 4],
            "prog_variant": prog_variant,
            "acts": default_acts,
            "composer_note_ja": f"{ton_ja}の4幕構成。第1幕で提示した動機を{progs[1][0]}→{progs[1][-1]}の半終止で問いかけ、第3幕頂点を経て第4幕で{progs[4][-1]}へ完全終止する。",
        }
        if not self.use_ollama:
            return fallback, "rule_fallback"

        try:
            import ollama

            law_str = (corpus_laws or {}).get("law_summary", "gap_fill>=0.75,spread=18st")
            retake_str = f" | RETAKE={retake_feedback}" if retake_feedback else ""
            prompt = (
                f"MODE={mode}|KEY={ton_key}({ton_ja})|BPM={bpm:.0f}|SEED={seed_motif_hint}|CORPUS={law_str}{retake_str}\n"
                f"標準和声候補: Act1={progs[1]}, Act2={progs[2]}, Act3={progs[3]}, Act4={progs[4]}\n"
                "規則: (1)〇〇風/AIポエム/『叫び』/英語調名禁止。(2)title_jaは格調高い28文字以内の日本語クラシック曲名。"
                "(3)4幕(問いかけ->模進転調->頂点->ウナコルダ終止)でlh_textureを幕ごとに変え、melody_offsetsは跳躍+逆行回復を含む整数8個(連番禁止)。"
                "(4)composer_note_jaは実務的な和声解説1文(28-65文字・句点止め、メタ指示語禁止)。"
            )
            client = ollama.Client(host=self.ollama_host, timeout=max(24.0, self.timeout_s))
            resp = client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "あなたはクラシック作曲家である。簡潔で有効なJSONのみ出力せよ。"},
                    {"role": "user", "content": prompt},
                ],
                format=LLMFullPieceDesignSchema.model_json_schema(),
                options={"temperature": 0.35, "num_ctx": 1536, "num_predict": 850},
                keep_alive=-1,
                think=False,
            )
            raw = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
            parsed = LLMFullPieceDesignSchema.model_validate_json(raw)

            from src.stage.harmonic_beauty_realizer import sanitize_chamber_title_ja, sanitize_composer_note_ja

            title_ja = EMOJI_PATTERN.sub("", parsed.title_ja).replace("！", "").replace("!", "").strip()
            title_en = EMOJI_PATTERN.sub("", parsed.title_en).replace("!", "").strip()
            note_ja = EMOJI_PATTERN.sub("", parsed.composer_note_ja).replace("\n", " ").replace("！", "。").replace("!", "。").strip()
            title_ja = sanitize_chamber_title_ja(title_ja, ton_ja, mode, prog_variant + 1)
            if any(b in title_ja for b in BANNED_PHRASES) or any(p.search(title_ja) for p in CONTRADICTION_REGEXES):
                title_ja = fallback["title_ja"]
            if not title_en or len(title_en) < 6:
                title_en = fallback["title_en"]
            note_ja = sanitize_composer_note_ja(note_ja, ton_ja, [c for a in parsed.acts for c in a.chords])
            if (
                not note_ja
                or len(note_ja) < 15
                or len(note_ja) > 136
                or any(b in note_ja for b in BANNED_PHRASES)
                or any(p.search(note_ja) for p in CONTRADICTION_REGEXES)
            ):
                note_ja = fallback["composer_note_ja"]

            acts_out = []
            for act_idx in range(4):
                a = parsed.acts[act_idx]
                acts_out.append(
                    {
                        "act": act_idx + 1,
                        "chords": [str(c).strip() for c in a.chords[:4]],
                        "lh_texture": a.lh_texture,
                        "melody_offsets": [max(-5, min(14, int(x))) for x in a.melody_offsets[:8]],
                        "dynamic_vel": max(48, min(90, int(a.dynamic_vel))),
                    }
                )
            return {
                "title_ja": title_ja[:72],
                "title_en": title_en[:84],
                "tonal_center": ton_key,
                "bpm": float(parsed.bpm or bpm),
                "seed_degrees": [max(-5, min(12, int(x))) for x in parsed.seed_degrees[:8]],
                "prog_variant": prog_variant,
                "acts": acts_out,
                "composer_note_ja": note_ja[:140],
            }, f"ollama:{self.model_name}"
        except Exception:
            return fallback, "rule_fallback"
