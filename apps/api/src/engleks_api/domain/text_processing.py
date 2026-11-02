from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class Language(StrEnum):
    EN = "en"
    UK = "uk"
    RU = "ru"


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
class RewriteOutcome:
    source_language: Language
    output_text: str
    explanations: list[str]
    usage: Usage
    llm_response: Mapping[str, object]


@dataclass(frozen=True)
class ProcessedText:
    interaction_id: UUID
    source_language: Language
    output_text: str
    explanations: list[str]


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID


@dataclass(frozen=True)
class Interaction:
    id: UUID
    user_id: UUID
    input_text: str
    outcome: RewriteOutcome


class RewriteFailed(Exception):
    """The LLM could not produce a valid result. `reason` tells causes apart in logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
