"""
5-Layer Stage Process Visualization & Real-Time Interactive Composition System
for Yamaha Disklavier and Chamber Music Performance (Windows 11 / RTX 3070 8GB).

Uses PEP 562 lazy attribute exports so lightweight imports (e.g., `src.stage.favicon`
or `src.stage.harmonic_beauty_realizer` in CI) do not eagerly load heavy optional modules.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

_EXPORT_MAP: Dict[str, Tuple[str, str]] = {
    "CelloAcousticPerception": ("src.stage.layer1_perception", "CelloAcousticPerception"),
    "EchoSuppressionLedger": ("src.stage.layer1_perception", "EchoSuppressionLedger"),
    "NLMSEchoCanceller": ("src.stage.layer1_perception", "NLMSEchoCanceller"),
    "PerceptionSnapshot": ("src.stage.layer1_perception", "PerceptionSnapshot"),
    "TurnTakingDetector": ("src.stage.layer1_perception", "TurnTakingDetector"),
    "VerbalDirectiveRecognizer": ("src.stage.layer1_perception", "VerbalDirectiveRecognizer"),
    "BANNED_PHRASES": ("src.stage.layer2_intent_llm", "BANNED_PHRASES"),
    "MusicalIntentEngine": ("src.stage.layer2_intent_llm", "MusicalIntentEngine"),
    "MusicalIntentSchema": ("src.stage.layer2_intent_llm", "MusicalIntentSchema"),
    "sanitize_monologue_ja": ("src.stage.layer2_intent_llm", "sanitize_monologue_ja"),
    "validate_chamber_music_monologue": ("src.stage.layer2_intent_llm", "validate_chamber_music_monologue"),
    "CandidatePhrase": ("src.stage.layer3_candidate_composer", "CandidatePhrase"),
    "CompositionTurnResult": ("src.stage.layer3_candidate_composer", "CompositionTurnResult"),
    "MultipleCandidateComposer": ("src.stage.layer3_candidate_composer", "MultipleCandidateComposer"),
    "ZeroConfigCorpusLoader": ("src.stage.layer3_candidate_composer", "ZeroConfigCorpusLoader"),
    "DisklavierStageOutputLayer": ("src.stage.layer5_disklavier_output", "DisklavierStageOutputLayer"),
    "OutputTelemetry": ("src.stage.layer5_disklavier_output", "OutputTelemetry"),
}

__all__ = sorted(_EXPORT_MAP.keys())


def __getattr__(name: str) -> Any:
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr_name)
