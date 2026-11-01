from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class Mode(StrEnum):
    MINIMAL = "minimal"
    NATURAL = "natural"
    POLISHED = "polished"


class Language(StrEnum):
    EN = "en"
    UK = "uk"
    RU = "ru"


class Category(StrEnum):
    GRAMMAR = "grammar"
    ARTICLES = "articles"
    SPELLING = "spelling"
    PUNCTUATION = "punctuation"
    WORD_CHOICE = "word_choice"
    WORD_ORDER = "word_order"
    STYLE = "style"
    OTHER = "other"


class Provider(StrEnum):
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class Usage:
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost: Decimal | None


@dataclass(frozen=True)
class ModelChange:
    """A change as the model reports it. Untrusted until reconciled with the diff."""

    original: str
    replacement: str
    category: Category
    explanation: str


@dataclass(frozen=True)
class RewriteRequest:
    text: str
    mode: Mode
    explanation_language: Language


@dataclass(frozen=True)
class RewriteOutcome:
    source_language: Language
    output_text: str
    changes: list[ModelChange]
    usage: Usage
    llm_response: Mapping[str, object]


@dataclass(frozen=True)
class Change:
    """A reconciled change. `span` is code point offsets into the input; None for translations."""

    original: str
    replacement: str
    category: Category
    explanation: str | None
    span: tuple[int, int] | None


@dataclass(frozen=True)
class ProcessedText:
    interaction_id: UUID
    source_language: Language
    mode: Mode
    output_text: str
    changes: list[Change]


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID


@dataclass(frozen=True)
class Interaction:
    id: UUID
    user_id: UUID
    request: RewriteRequest
    outcome: RewriteOutcome
    changes: list[Change]


class RewriteFailed(Exception):
    """The LLM could not produce a valid result. `reason` tells causes apart in logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
