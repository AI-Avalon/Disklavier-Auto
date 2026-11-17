"""
Layer 2: Musical Intent & Thought Trace Layer (Ollama GPU Structured Outputs / RTX 3070 8GB Safe).

Enforces Pydantic JSON Schema output without letting the LLM generate raw MIDI note numbers.
Strictly adheres to `skills/chamber-music-diction/SKILL.md`:
- Zero AI Slop (no flattery, no anthropomorphic poetry, no emojis, no exclamation marks).
- Concise chamber-music score-reading Japanese (35-60 chars).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.stage.layer1_perception import PerceptionSnapshot


class MusicalIntentSchema(BaseModel):
    monologue_ja: str = Field(
        ...,
        description="舞台投影用の簡潔な音楽的思考メモ（室内楽アナリーゼ調、35〜60文字、感嘆符・お世辞禁止）",
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


BANNED_PHRASES = (
    "悲痛な叫び",
    "共鳴しました",
    "歓喜",
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

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def validate_chamber_music_monologue(text: str) -> bool:
    if not text or not text.strip():
        return False
    if EMOJI_PATTERN.search(text):
        return False
    for banned in BANNED_PHRASES:
        if banned in text:
            return False
    return True


def sanitize_monologue_ja(text: str, fallback: str) -> str:
    cleaned = EMOJI_PATTERN.sub("", (text or "").strip())
    cleaned = cleaned.replace("！", "。").replace("!", "。")
    cleaned = re.sub(r"^(私は|AIとして|ピアニストとして)[、,\s]*", "", cleaned)
    for banned in BANNED_PHRASES:
        if banned in cleaned:
            return fallback
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 12 or not validate_chamber_music_monologue(cleaned):
        return fallback
    if len(cleaned) > 60:
        # Trim cleanly at period or comma boundary within 60 chars
        cut = cleaned[:59]
        last_p = max(cut.rfind("。"), cut.rfind("、"))
        if last_p >= 24:
            cleaned = cut[: last_p + 1]
        else:
            cleaned = cut + "。"
    return cleaned


class MusicalIntentEngine:
    """
    Produces `MusicalIntentSchema` from L1 perception + MC directives.
    Uses local Ollama structured JSON outputs when available (temperature=0.2, num_ctx=2048),
    and falls back deterministically to chamber-music score-reading rules if Ollama is offline.
    """

    SYSTEM_PROMPT = (
        "あなたは室内楽リハーサル中の熟練ピアニストである。"
        "お世辞、擬人化ポエム、感嘆符（！）、絵文字、AIの自己言及は絶対に使用しない。"
        "monologue_ja は必ず35〜60文字以内の日本語（体言止めまたは簡潔な常体）で、"
        "和声・音域・対位法・ダンパー操作に基づく具体的な音楽判断のみを記述すること。"
        "例:『チェロがG線上で半音階的に緊張を高めている。長三度を省いた空虚五度で響きの余白を残し、次の解決を待つ。』"
    )

    def __init__(
        self,
        model_name: str = "qwen3.5:4b",
        ollama_host: str = "http://127.0.0.1:11434",
        use_ollama: bool = True,
        timeout_s: float = 4.5,
    ) -> None:
        self.model_name = model_name
        self.ollama_host = ollama_host
        self.use_ollama = use_ollama
        self.timeout_s = timeout_s
        self.last_source: str = "rule_fallback"
        self.last_latency_ms: float = 0.0

    def _rule_based_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
    ) -> MusicalIntentSchema:
        hints = directive_hints or {}
        pitches = [int(n.get("pitch", n.get("note", 60))) for n in human_notes if int(n.get("pitch", n.get("note", 0))) > 0]
        if not pitches and perception.midi_pitch > 0:
            pitches = [perception.midi_pitch]
        mean_pitch = sum(pitches) / len(pitches) if pitches else 60.0

        # Select tonal center & chord progression based on pitch & directive hints
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
        }
        chords = progression_map.get(tonal_center, ["Dm7", "Gm7", "A7sus4", "Dm"])

        # Avoid collision with human fundamental range (especially Cello 100-600Hz ≈ MIDI 43-74)
        if perception.mode == "cello":
            if mean_pitch >= 62:
                reg = [38, 60]  # Stay in warm bass/tenor below high cello
            else:
                reg = [69, 91]  # Float upper voices above cello G/D string
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

        # Craft concise, authentic chamber-music score reading note (35-60 chars)
        if perception.mode == "cello":
            if perception.articulation == "staccato":
                monologue = "高音域のスタッカート動機を捕捉。同音反復は避け、バス声部の反行形で対位法的に受ける。"
            elif tonal_center == "D_dorian":
                monologue = "開放弦の余韻と減速を検知。ダンパーを1/3残し、動機をドリア旋法へ変奏して提示する。"
            else:
                monologue = "チェロが中音域で緊張を高めている。長三度を省いた空虚五度で響きの余白を残し解決を待つ。"
        else:
            if hints.get("raw_text"):
                monologue = "口頭指示を反映し調性重心を再設定。既存動機を反行転回させ、中声部の濁りを排して接続する。"
            elif stance == "provoke":
                monologue = "上声部の跳躍動機に対し短六度反行で挑発。ダンパーを浅く保ち、和声的緊張を次小節へ送る。"
            else:
                monologue = "鍵盤上のフレーズ終止を検知。人声帯の重複を避け、高音域の分散和音で対話の余白を開く。"

        rejection_criteria = [
            "チェロ中音域との濁り" if perception.mode == "cello" else "厚すぎる三度重複",
            "同形反復による停滞",
            "並達五度・八度の発生",
        ]

        monologue = sanitize_monologue_ja(monologue, monologue)
        return MusicalIntentSchema(
            monologue_ja=monologue,
            interaction_stance=stance,
            tonal_center=tonal_center,
            chord_progression=chords,
            target_density=round(target_density, 2),
            register_range=reg,
            pedal_strategy=pedal_strategy,
            rejection_criteria=rejection_criteria,
        )

    def decide_intent(
        self,
        perception: PerceptionSnapshot,
        human_notes: List[Dict[str, Any]],
        directive_hints: Optional[Dict[str, Any]] = None,
    ) -> MusicalIntentSchema:
        t0 = time.perf_counter()
        fallback = self._rule_based_intent(perception, human_notes, directive_hints)
        if not self.use_ollama:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return fallback

        try:
            import ollama

            client = ollama.Client(host=self.ollama_host, timeout=self.timeout_s)
            pitches = [int(n.get("pitch", n.get("note", 60))) for n in human_notes[-8:]]
            user_prompt = (
                f"Mode={perception.mode}, F0={perception.f0_hz}Hz({perception.note_name}), "
                f"Articulation={perception.articulation}, RecentPitches={pitches}, "
                f"BreathGauge={perception.breath_gauge}, Directive={directive_hints or {}}.\n"
                "次のピアノ応答の音楽意図JSONを出力せよ。"
            )
            resp = client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                format=MusicalIntentSchema.model_json_schema(),
                options={"temperature": 0.2, "num_ctx": 2048, "num_predict": 220},
                think=False,
            )
            raw_content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
            data = json.loads(raw_content)
            data["monologue_ja"] = sanitize_monologue_ja(
                str(data.get("monologue_ja", "")),
                fallback.monologue_ja,
            )
            if "target_density" in data:
                data["target_density"] = max(0.0, min(1.0, float(data["target_density"])))
            if not isinstance(data.get("register_range"), list) or len(data["register_range"]) != 2:
                data["register_range"] = fallback.register_range
            elif min(data["register_range"]) < 21 or max(data["register_range"]) > 108:
                data["register_range"] = fallback.register_range
            if not isinstance(data.get("chord_progression"), list) or len(data["chord_progression"]) < 2:
                data["chord_progression"] = fallback.chord_progression
            if not isinstance(data.get("rejection_criteria"), list) or len(data["rejection_criteria"]) < 1:
                data["rejection_criteria"] = fallback.rejection_criteria
            parsed = MusicalIntentSchema.model_validate(data)
            self.last_source = f"ollama:{self.model_name}"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return parsed
        except Exception:
            self.last_source = "rule_fallback"
            self.last_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return fallback
