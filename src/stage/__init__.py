"""
5-Layer Stage Process Visualization & Real-Time Interactive Composition System
for Yamaha Disklavier and Chamber Music Performance (Windows 11 / RTX 3070 8GB).
"""

from src.stage.layer1_perception import (
    CelloAcousticPerception,
    EchoSuppressionLedger,
    NLMSEchoCanceller,
    PerceptionSnapshot,
    TurnTakingDetector,
    VerbalDirectiveRecognizer,
)
from src.stage.layer2_intent_llm import (
    BANNED_PHRASES,
    MusicalIntentEngine,
    MusicalIntentSchema,
    sanitize_monologue_ja,
    validate_chamber_music_monologue,
)
from src.stage.layer3_candidate_composer import (
    CandidatePhrase,
    CompositionTurnResult,
    MultipleCandidateComposer,
    ZeroConfigCorpusLoader,
)
from src.stage.layer5_disklavier_output import (
    DisklavierStageOutputLayer,
    OutputTelemetry,
)

__all__ = [
    "BANNED_PHRASES",
    "CandidatePhrase",
    "CelloAcousticPerception",
    "CompositionTurnResult",
    "DisklavierStageOutputLayer",
    "EchoSuppressionLedger",
    "MultipleCandidateComposer",
    "MusicalIntentEngine",
    "MusicalIntentSchema",
    "NLMSEchoCanceller",
    "OutputTelemetry",
    "PerceptionSnapshot",
    "TurnTakingDetector",
    "VerbalDirectiveRecognizer",
    "ZeroConfigCorpusLoader",
    "sanitize_monologue_ja",
    "validate_chamber_music_monologue",
]
