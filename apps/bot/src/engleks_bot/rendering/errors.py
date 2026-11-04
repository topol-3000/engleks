from engleks_bot.api_client import ApiError
from engleks_contracts import ErrorCode

MAX_INPUT_CHARS = 1000

UNAVAILABLE = "The service is unavailable right now. Please try again in a minute."
NO_ACCESS = "You don't have access to this bot yet."
CANNOT_PROCESS = "I couldn't process this text."
TOO_LONG = f"This text is too long. Please send up to {MAX_INPUT_CHARS} characters at a time."
LLM_DOWN = "The language model didn't respond. Please try again."
GENERIC = "Something went wrong. Please try again."
NEED_INVITE = "You need an invite to use Engleks. Send /start followed by your code."
DISABLED = "Your access to Engleks is disabled."
TOO_MANY = "Too many messages. Try again in a minute."
DAILY_LIMIT = "You've reached today's limit. It resets at 00:00 UTC."
INVALID_INVITE = "That invite code doesn't work. Ask for a new one."
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
        case ErrorCode.INVITE_REQUIRED:
            return NEED_INVITE
        case ErrorCode.USER_DISABLED:
            return DISABLED
        case ErrorCode.RATE_LIMITED:
            return TOO_MANY
        case ErrorCode.DAILY_BUDGET:
            return DAILY_LIMIT
        case ErrorCode.INVALID_INVITE:
            return INVALID_INVITE
        case _:
            return GENERIC
