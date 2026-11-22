"""
Compatibility Re-Export for `src/stage/notagen_live_engine.py`.

All live NotaGen-Small (quantized), Anticipatory Music Transformer, MIDI-GPT CPU,
PeriodStructurePlanner, ABCStreamParser, and LivePolyphonyHarmonyGuard implementations
live in `src/stage/notagen_engine.py`.
"""

from src.stage.notagen_engine import (
    ABCStreamParser,
    AnticipatoryContinuationEngine,
    BarPeriodPlan,
    LivePolyphonyHarmonyGuard,
    MIDIGPTAccompanimentEngine,
    NOTAGEN_MODEL_DIR,
    NOTAGEN_QUANT_CKPT,
    NotaGenEngine,
    NotaGenLiveEngine,
    NotaGenSmallQuantizedModel,
    PeriodStructurePlanner,
    midi_to_abc_token,
)

__all__ = [
    "ABCStreamParser",
    "AnticipatoryContinuationEngine",
    "BarPeriodPlan",
    "LivePolyphonyHarmonyGuard",
    "MIDIGPTAccompanimentEngine",
    "NOTAGEN_MODEL_DIR",
    "NOTAGEN_QUANT_CKPT",
    "NotaGenEngine",
    "NotaGenLiveEngine",
    "NotaGenSmallQuantizedModel",
    "PeriodStructurePlanner",
    "midi_to_abc_token",
]
