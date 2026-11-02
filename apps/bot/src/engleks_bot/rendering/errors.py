from engleks_bot.api_client import ApiError
from engleks_contracts import ErrorCode

MAX_INPUT_CHARS = 1000

UNAVAILABLE = "The service is unavailable right now. Please try again in a minute."
NO_ACCESS = "You don't have access to this bot yet."
CANNOT_PROCESS = "I couldn't process this text."
TOO_LONG = f"This text is too long. Please send up to {MAX_INPUT_CHARS} characters at a time."
LLM_DOWN = "The language model didn't respond. Please try again."
GENERIC = "Something went wrong. Please try again."
SEND_TEXT = "Send me text and I'll correct it."


def error_text(error: ApiError) -> str:
    if error.status_code is None:
        return UNAVAILABLE
    match error.code:
        case ErrorCode.UNAUTHORIZED:
            return NO_ACCESS
        case ErrorCode.VALIDATION_ERROR:
            return CANNOT_PROCESS
        case ErrorCode.LLM_UNAVAILABLE:
            return LLM_DOWN
        case _:
            return GENERIC
