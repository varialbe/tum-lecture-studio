"""STT engine interface shared by all backends."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

ProgressFn = Callable[[float], None]  # fraction 0..1 of the current audio file


@dataclass
class Word:
    start: float
    end: float
    word: str  # includes leading whitespace as produced by Whisper tokenisation


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def shifted(self, offset: float) -> Segment:
        return Segment(
            self.start + offset,
            self.end + offset,
            self.text,
            [Word(w.start + offset, w.end + offset, w.word) for w in self.words],
        )

    def to_dict(self, words: bool = False) -> dict:
        d = asdict(self)
        if not words:
            d.pop("words")
        return d


@dataclass
class TranscribeOptions:
    language: str | None = "de"  # None = auto-detect
    initial_prompt: str | None = None
    vad: bool = True
    beam_size: int = 5
    word_timestamps: bool = True


@dataclass
class TranscriptionResult:
    segments: list[Segment]
    language: str | None
    duration: float


class SttEngine(Protocol):
    engine: str
    model: str

    def transcribe(
        self, audio: Path, opts: TranscribeOptions, on_progress: ProgressFn
    ) -> TranscriptionResult:
        """Blocking; called from a worker thread. Timestamps relative to `audio`."""
        ...


class SttUnavailable(Exception):
    """Engine or model can't run on this machine (missing package, no GPU, ...)."""


DEFAULT_VOCABULARY = (
    "Java, RISC-V, Mengenlehre, Induktion, Bijektion, Injektion, Surjektion, Register, Rekursion, "
    "Diskrete Strukturen, Rechnerarchitektur, Assembler, Pipeline, Cache, Graph, Relation, Äquivalenzrelation"
)


def build_prompt(vocabulary: str | None) -> str | None:
    vocab = (vocabulary or "").strip()
    if not vocab:
        return None
    # A natural sentence works better than a bare list for Whisper's decoder prompt.
    return f"Vorlesung an der TU München. Fachbegriffe: {vocab}."
