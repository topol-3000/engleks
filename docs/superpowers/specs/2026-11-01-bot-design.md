# Bot: Design

Status: draft for review · Date: 2026-11-01 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md), [Core Slice](2026-11-01-core-slice-design.md)

## 1. Goal

Make the Telegram bot a usable client of `POST /v1/texts/process`: a user sends text in a private chat, gets it improved (English) or translated (UA/RU → EN), plus a few short English lessons drawn from the edits. This is sub-project 3 of 5 (Foundation, Core slice, **Bot**, Access, Admin dashboard).

Done means: with `make up` and a test bot token, a private chat with the bot improves and translates text, shows lessons, splits long replies, and survives slow LLM calls and API errors without crashing.

## 2. Scope

In scope:

- `ApiClient.process_text` with its own timeout.
- Text handler for private chats.
- Reply rendering: corrected or translated text, then the lessons; splitting into 4096-unit messages.
- User-facing messages for API errors and unsupported input.
- `/start` and `/help` with usage text.

Out of scope, with the reason:

| Item | Where | Why |
| --- | --- | --- |
| `/start <code>`, invite flow | Access | Needs `POST /v1/auth/redeem` |
| `rate_limited`, `forbidden` messages | Access | Error codes don't exist yet; unknown codes already get a generic message (§7) |
| Profiles, history | Own sub-projects | No endpoints yet |
| Webhooks | Later | Long polling is the MVP choice (architecture §9) |
| Bot UI in Ukrainian or Russian | Later | Explanations are English (§3); UI follows |

**Known gap (unchanged from Core slice):** until Access ships, anyone who messages the bot gets an account. The bot token must stay private.

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Reply layout | Clean corrected text first, then a header and the numbered lessons | Easy to copy; lessons read as a short lesson, not an edit log |
| No buttons | Replies carry no inline keyboard | One behaviour per request (`process_text.v3`); the bot stays stateless |
| Formatting | `aiogram.utils.formatting` nodes sent as text + entities, never a parse mode | aiogram computes UTF-16 entity offsets; LLM text is never parsed as markup, so nothing needs escaping |
| `ApiClient` injection | Dispatcher workflow data (`dispatcher["api"] = client`), received as a handler argument | aiogram's built-in mechanism; no DI framework, no globals |
| Bot structure | Thin handlers, pure functions in `rendering/` | The bot has no business logic, so no service layer; rendering is testable without aiogram I/O |
| Process timeout | Separate `BOT_PROCESS_TIMEOUT_S = 130`, `health()` keeps 10 s | The endpoint's worst case is `2 × API_LLM_TIMEOUT_S` (core slice §9); giving up earlier shows an error for a billed success |

No new runtime dependencies, contract changes, Compose or CI changes.

## 4. Components (`apps/bot/src/engleks_bot/`)

| Module | Role |
| --- | --- |
| `api_client.py` | Adds `process_text(telegram_user_id: int, request: ProcessTextRequest) -> ProcessTextResponse`: `POST /v1/texts/process`, `X-Telegram-User-Id` on this request only, body `request.model_dump(mode="json")`, timeout `process_timeout`. `_request` gains optional `json`, `headers`, `timeout` |
| `config.py` | `process_timeout_s: float = 130` |
| `main.py` | Builds `ApiClient(..., process_timeout=settings.process_timeout_s)`, stores it in workflow data, includes routers `start` and `texts`, registers the errors handler |
| `handlers/start.py` | `/start` and `/help`: what the bot does |
| `handlers/texts.py` | Private-chat text → process → reply; other private-chat content → "send me text"; unknown commands → help |
| `handlers/errors.py` | `dispatcher.errors` handler: logs and swallows (§7) |
| `rendering/reply.py` | `render_reply(response: ProcessTextResponse) -> list[Text]` |
| `rendering/errors.py` | `error_text(error: ApiError) -> str` and the other fixed user-facing strings |

## 5. Flows

### Plain text

1. A text message arrives in a private chat and does not start with `/`.
2. `texts.on_text` rejects text over `MAX_INPUT_CHARS` without an API call, builds `ProcessTextRequest(text=message.text)`, then, inside `ChatActionSender.typing` (resends "typing…" every 5 s), calls `api.process_text(message.from_user.id, request)`.
3. `render_reply` returns one or more messages. Each is sent as a reply to the user's message (`allow_sending_without_reply`), with link previews disabled and no keyboard.

A callback query (for example a button on a reply sent by an older version of the bot) has no handler: it makes no API call and sends nothing.

## 6. Rendering (`rendering/reply.py`)

### Layout

```
I went to the store yesterday.          ← output_text, plain, untouched

Lessons                                 ← bold header
1. Use the past simple for a finished action at a stated time.
2. Use "the" for a specific place both speakers know.
```

| Case | Rendering |
| --- | --- |
| English with lessons | Header `Lessons`, then `n. <lesson>` per line |
| Translation (`source_language` is `uk` or `ru`) | Header `Translated from Ukrainian` (or `Russian`), then the lessons, if any |
| English without lessons | Output text only. Minor fixes may go unexplained, so "no changes" would be a false claim |

### Packing

- Blocks, in order: the output text, the header, then one block per lesson.
- Separators: a blank line after the output text, a newline between the header and each item.
- Length is measured in UTF-16 code units of the **visible** text (entities don't count toward Telegram's limit), reusing the unit count in `rendering/split.py`.
- Greedy: blocks are appended to the current message; when the next block would push it past 4096, a new message starts.
- A block longer than 4096 alone (realistically only `output_text`) is split with `split_message` into plain chunks, each its own message.
- Messages are sent with `**text.as_kwargs()`.

## 7. Errors and edge cases

### API errors (`error_text`)

| `ApiError` | Message |
| --- | --- |
| `status_code is None` (network failure, timeout) | "The service is unavailable right now. Please try again in a minute." |
| `401` / `unauthorized` | "You don't have access to this bot yet." |
| `422` / `validation_error` | "I couldn't process this text." |
| `502` / `llm_unavailable` | "The language model didn't respond. Please try again." |
| Anything else, including `code is None` | "Something went wrong. Please try again." |

The handler that catches the error logs `api_call_failed` (`status_code`, `code`) once and replies to the user's message with the text. A `ValidationError` building `ProcessTextRequest` gets the 422 message without a network call.

### Messages

| Case | Behaviour |
| --- | --- |
| Group, supergroup, channel | Ignored: routers filter `chat.type == "private"` |
| Photo, voice, sticker, captioned media, other non-text | "Send me text and I'll correct it." |
| Unknown `/command` | Help text; text starting with `/` never reaches the API |
| Edited message | Ignored: no `edited_message` handler |
| Text longer than `MAX_INPUT_CHARS` (1000) | "This text is too long…" without an API call |
| Callback query | Ignored: no handler |
| Forwarded text | Processed as text |

### Telegram failures

A `dispatcher.errors` handler logs `update_failed` (`update_id`, `error_type`) and swallows the exception, so polling continues (user blocked the bot, a rendering bug rejected by Telegram). A request that already reached the LLM stays billed; nothing more can be done.

## 8. Config and logging

`.env.example` gains `BOT_PROCESS_TIMEOUT_S=130` with a comment: must exceed `2 × API_LLM_TIMEOUT_S`. Compose is unchanged; the default applies.

Log events (ids, lengths and counts only, never the user's text or the model's output):

| Event | Fields |
| --- | --- |
| `text_processed` | `interaction_id`, `source_language`, `text_len`, `explanations`, `messages`, `duration_ms` |
| `input_too_long` | `text_len`, `limit` |
| `api_call_failed` | `status_code`, `code` |
| `update_failed` | `update_id`, `error_type` |

## 9. Testing

No test calls Telegram or an LLM. API responses come from `httpx2.MockTransport` built from real `engleks_contracts` DTOs.

| File | Covers |
| --- | --- |
| `test_api_client.py` (extended) | `process_text`: method, path, both headers, JSON body, parsed response; `401`/`422`/`502` bodies → `ApiError` codes; process timeout applied (`request.extensions["timeout"]`) while `health()` keeps 10 s |
| `test_reply.py` | Every layout row in §6; packing at block boundaries; every message ≤ 4096 UTF-16 units with astral characters; oversized `output_text` split; the bold header after an emoji has the right UTF-16 entity offset |
| `test_errors.py` | The §7 table, parametrized, with an unknown code and `status_code=None` |
| `test_texts.py` | Handler called with real aiogram objects on the `RecordingSession` bot: request body is exactly `{text}`; chunks reply to the original with no keyboard; error replies; over-limit, `/`-text and non-text make no API call; logs never contain text |
| `test_routing.py` | `Dispatcher.feed_update` with a real `Bot` on a `RecordingSession` (fixture in `apps/bot/tests/conftest.py`, records outgoing methods, returns canned results): private-only filters, commands vs text, a stale `rerun:` callback ignored, `api` injection, errors handler swallowing `TelegramForbiddenError` |

Manual check before merging, with `make up` and a test bot token: an English text, a Ukrainian text, a long text with many lessons (split), and the `litellm` container stopped (the 502 message).

## 10. Changes outside the bot

- `.env.example`: `BOT_PROCESS_TIMEOUT_S` (§8).
- `AGENTS.md` (its "keeping this file current" rule): `ApiClient` reaches handlers through dispatcher workflow data; messages are built with `aiogram.utils.formatting`, never a parse mode; routing is tested through `RecordingSession` and `feed_update`.

## 11. Definition of done

- `make lint && make test` pass.
- The manual check in §9 passes.
- No log line contains the user's text or the model's output.
- The bot answers nothing in groups.
