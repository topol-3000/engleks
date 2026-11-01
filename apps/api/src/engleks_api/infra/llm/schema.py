from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Sent to the proxy as the JSON schema of the reply. No length constraints: OpenAI's
# strict mode rejects some keywords. Validators run on our side and are not in the schema.


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class LLMChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original: str
    replacement: str
    # No "other": reconciliation assigns it to edits the model did not explain.
    category: Literal[
        "grammar", "articles", "spelling", "punctuation", "word_choice", "word_order", "style"
    ]
    explanation: str

    _explanation_not_blank = field_validator("explanation")(_not_blank)


class LLMRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_language: Literal["en", "uk", "ru"]
    output_text: str
    changes: list[LLMChange]

    _output_not_blank = field_validator("output_text")(_not_blank)
