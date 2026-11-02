from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Sent to the proxy as the JSON schema of the reply. No length constraints: OpenAI's
# strict mode rejects some keywords. Validators run on our side and are not in the schema.


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class LLMRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_language: Literal["en", "uk", "ru"]
    output_text: str
    explanations: list[str]

    _output_not_blank = field_validator("output_text")(_not_blank)

    @field_validator("explanations")
    @classmethod
    def _explanations_not_blank(cls, explanations: list[str]) -> list[str]:
        return [_not_blank(e) for e in explanations]
