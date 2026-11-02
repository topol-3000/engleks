from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Mode(StrEnum):
    MINIMAL = "minimal"
    NATURAL = "natural"
    POLISHED = "polished"


class Language(StrEnum):
    EN = "en"
    UK = "uk"
    RU = "ru"


class ProcessTextRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=4096)
    mode: Mode = Mode.NATURAL
    explanation_language: Language = Language.EN

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("text must not be blank")
        return text


class ProcessTextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    interaction_id: UUID
    source_language: Language
    mode: Mode
    output_text: str
    explanations: list[str]
