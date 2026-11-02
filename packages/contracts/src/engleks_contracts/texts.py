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


class Category(StrEnum):
    GRAMMAR = "grammar"
    ARTICLES = "articles"
    SPELLING = "spelling"
    PUNCTUATION = "punctuation"
    WORD_CHOICE = "word_choice"
    WORD_ORDER = "word_order"
    STYLE = "style"
    OTHER = "other"


class ProcessTextRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=4096)
    mode: Mode = Mode.NATURAL
    explanation_language: Language = Language.EN

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        # Never strip: offsets in the response index into the text as sent.
        if not text.strip():
            raise ValueError("text must not be blank")
        return text


class ChangeItem(BaseModel):
    """One explained edit.

    `start`/`end` are Unicode code point offsets into the request text (Python `str`
    indices), null for translations. Telegram entities count UTF-16 code units, so a
    Telegram client must convert them before highlighting.
    """

    model_config = ConfigDict(frozen=True)

    original: str
    replacement: str
    category: Category
    explanation: str | None
    start: int | None
    end: int | None


class ProcessTextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    interaction_id: UUID
    source_language: Language
    mode: Mode
    output_text: str
    changes: list[ChangeItem]
