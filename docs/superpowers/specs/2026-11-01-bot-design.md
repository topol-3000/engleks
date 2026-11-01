# Bot: Design

Status: draft for review · Date: 2026-11-01 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md), [Core Slice](2026-11-01-core-slice-design.md)

## 1. Goal

Make the Telegram bot a usable client of `POST /v1/texts/process`: a user sends text in a private chat, gets it improved (English) or translated (UA/RU → EN) with every change explained, and can re-run the same text in another mode with one tap. This is sub-project 3 of 5 (Foundation, Core slice, **Bot**, Access, Admin dashboard).

Done means: with `make up` and a test bot token, a private chat with the bot improves and translates text, explains changes, re-runs in the other modes, splits long replies, and survives slow LLM calls and API errors without crashing or paying for one tap twice.

## 2. Scope

In scope:

- `ApiClient.process_text` with its own timeout.
- Text handler for private chats, in `minimal` mode.
- Inline buttons that re-run the same text in another mode.
- Reply rendering: corrected text, then the explained changes; splitting into 4096-unit messages.
- User-facing messages for API errors and unsupported input.
- `/start` and `/help` with usage text.

Out of scope, with the reason:

| Item | Where | Why |
| --- | --- | --- |
| `/start <code>`, invite flow | Access | Needs `POST /v1/auth/redeem` |
| `rate_limited`, `forbidden` messages | Access | Error codes don't exist yet; unknown codes already get a generic message (§7) |
| Default mode, explanation language setting | Settings | Needs `/v1/me/settings`; the bot has no storage |
| Profiles, history | Own sub-projects | No endpoints yet |
| Webhooks | Later | Long polling is the MVP choice (architecture §9) |
| Bot UI in Ukrainian or Russian | Later | Explanations are English (§3); UI follows |

**Known gap (unchanged from Core slice):** until Access ships, anyone who messages the bot gets an account. The bot token must stay private.

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Mode selection | Plain text is always `minimal`; inline buttons re-run in another mode | Stateless: the button handler reads the original text from the reply chain. A sticky default would need storage or `/v1/me/settings` |
| Reply layout | Clean corrected text first, then a numbered change list | Easy to copy; offsets aren't needed for display, so no UTF-16 conversion of API offsets |
| Explanation language | Always English (the DTO default) | Simplest; immersive for learners. A setting comes with `/v1/me/settings` |
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
| `main.py` | Builds `ApiClient(..., process_timeout=settings.process_timeout_s)`, stores it in workflow data, includes routers `start`, `texts`, `rerun`, registers the errors handler |
| `handlers/start.py` | `/start` and `/help`: what the bot does and how the buttons work |
| `handlers/texts.py` | Private-chat text → process → reply; other private-chat content → "send me text"; unknown commands → help |
| `handlers/rerun.py` | `RerunCallback` taps → process in that mode → new reply |
| `handlers/errors.py` | `dispatcher.errors` handler: logs and swallows (§7) |
| `rendering/reply.py` | `render_reply(response: ProcessTextResponse) -> list[Text]` |
| `rendering/keyboards.py` | `RerunCallback(CallbackData, prefix="rerun")` with `mode: Mode`; `rerun_keyboard(modes: Iterable[Mode]) -> InlineKeyboardMarkup \| None`; `keyboard_modes(markup) -> list[Mode]` |
| `rendering/errors.py` | `error_text(error: ApiError) -> str` and the other fixed user-facing strings |

## 5. Flows

### Plain text

1. A text message arrives in a private chat and does not start with `/`.
2. `texts.py` builds `ProcessTextRequest(text=message.text)` (mode `minimal`, explanation language `en`), then, inside `ChatActionSender.typing` (resends "typing…" every 5 s), calls `api.process_text(message.from_user.id, request)`.
3. `render_reply` returns one or more messages. Each is sent with `reply_to_message_id=message.message_id` and link previews disabled. The last carries `rerun_keyboard([natural, polished])`.

### Re-run

1. `callback.message` is the bot's tapped reply (the message with the keyboard); its `reply_to_message` is the user's original message.
2. If `(chat_id, original message_id)` is already in the process-local in-flight set: `callback.answer("Already working on it")`, stop.
3. Read the remaining modes from the tapped reply's keyboard with `keyboard_modes`, then **remove the whole keyboard** from the tapped reply (`edit_reply_markup(reply_markup=None)`), add the key to the in-flight set and `callback.answer("Rewriting in <mode>…")`.
4. Process the original text in the tapped mode, as in the plain-text flow.
5. Send the new reply to the **original** message. Its keyboard holds the remaining modes minus the tapped one, or none when that is empty.
6. Remove the key from the in-flight set (in `finally`).

**One live keyboard per original message.** Only the newest reply for an original message has buttons, and they are exactly the modes not yet shown. First reply `[Natural] [Polished]`; tap Natural → that keyboard goes, the new reply has `[Polished]`; tap Polished → that keyboard goes, the newest reply has none. No mode can be requested twice through the buttons, and the state lives in the Telegram messages, not in the bot.

The in-flight set covers the race where a second tap arrives before the keyboard is removed. It is lost on restart; it is a guard, not storage.

## 6. Rendering (`rendering/reply.py`)

### Layout

```
I went to the store yesterday.          ← output_text, plain, untouched

Changes · minimal                       ← bold header
1. ~has went~ → went · grammar
   Use past simple for a finished action at a stated time.
2. ~store~ → the store · articles
   A specific store needs "the".
```

| Case | Rendering |
| --- | --- |
| Edit | `n. ~original~ → replacement · category`; explanation on the next line, indented 3 spaces |
| Insertion (`original == ""`) | `n. + replacement · category` |
| Deletion (`replacement == ""`) | `n. ~original~ removed · category` |
| `explanation is None` | No explanation line |
| Category label | Underscores become spaces: `word_choice` → `word choice` |
| Translation (`source_language` is `uk` or `ru`) | Header `Translated from Ukrainian · minimal` (or `Russian`); items `n. original → replacement · category`, no strikethrough: the source wasn't wrong |
| English, no changes | Output text, then `No changes needed · <mode>` |

The header always names the mode, so replies from re-runs can be told apart.

### Packing

- Blocks, in order: the output text, the header, then one block per change.
- Separators: a blank line after the output text, a newline between the header and each item.
- Length is measured in UTF-16 code units of the **visible** text (entities don't count toward Telegram's limit), reusing the unit count in `rendering/split.py`.
- Greedy: blocks are appended to the current message; when the next block would push it past 4096, a new message starts.
- A block longer than 4096 alone (realistically only `output_text`) is split with `split_message` into plain chunks, each its own message.
- Messages are sent with `**text.as_kwargs()`.

### Keyboard (`rendering/keyboards.py`)

`rerun_keyboard(modes)` returns one button per mode, labelled `Minimal` / `Natural` / `Polished`, ordered minimal, natural, polished, in one row; `None` when `modes` is empty. `keyboard_modes(markup)` unpacks `RerunCallback`s from a markup, ignoring buttons that aren't re-run buttons.

## 7. Errors and edge cases

### API errors (`error_text`)

| `ApiError` | Message |
| --- | --- |
| `status_code is None` (network failure, timeout) | "The service is unavailable right now. Please try again in a minute." |
| `401` / `unauthorized` | "You don't have access to this bot yet." |
| `422` / `validation_error` | "I couldn't process this text." |
| `502` / `llm_unavailable` | "The language model didn't respond. Please try again." |
| Anything else, including `code is None` | "Something went wrong. Please try again." |

The handler that catches the error logs `api_call_failed` (`status_code`, `code`, `mode`) once and replies to the user's message with the text. A `ValidationError` building `ProcessTextRequest` gets the 422 message without a network call.

### Re-run

| Case | Behaviour |
| --- | --- |
| API call fails | The tapped reply's keyboard is **restored** as it was, so the user can retry; the error is sent as a reply to the original |
| Duplicate tap in flight | `callback.answer("Already working on it")`, no call |
| `reply_to_message is None` (original deleted) | Alert (`show_alert=True`): "The original message is gone. Send the text again."; keyboard removed |
| `callback.message` is `InaccessibleMessage` | Alert: "This message is too old. Send the text again." |

### Messages

| Case | Behaviour |
| --- | --- |
| Group, supergroup, channel | Ignored: routers filter `chat.type == "private"` |
| Photo, voice, sticker, captioned media, other non-text | "Send me text and I'll correct it." |
| Unknown `/command` | Help text; text starting with `/` never reaches the API |
| Edited message | Ignored: no `edited_message` handler |
| Forwarded text | Processed as text |

### Telegram failures

A `dispatcher.errors` handler logs `update_failed` (`update_id`, `error_type`) and swallows the exception, so polling continues (user blocked the bot, a rendering bug rejected by Telegram). A request that already reached the LLM stays billed; nothing more can be done.

## 8. Config and logging

`.env.example` gains `BOT_PROCESS_TIMEOUT_S=130` with a comment: must exceed `2 × API_LLM_TIMEOUT_S`. Compose is unchanged; the default applies.

Log events (ids, lengths and counts only, never the user's text or the model's output):

| Event | Fields |
| --- | --- |
| `text_processed` | `interaction_id`, `mode`, `source_language`, `text_len`, `changes`, `messages`, `duration_ms` |
| `rerun_requested` | `mode` |
| `rerun_duplicate_ignored` | `mode` |
| `api_call_failed` | `status_code`, `code`, `mode` |
| `update_failed` | `update_id`, `error_type` |

## 9. Testing

No test calls Telegram or an LLM. API responses come from `httpx2.MockTransport` built from real `engleks_contracts` DTOs.

| File | Covers |
| --- | --- |
| `test_api_client.py` (extended) | `process_text`: method, path, both headers, JSON body, parsed response; `401`/`422`/`502` bodies → `ApiError` codes; process timeout applied (`request.extensions["timeout"]`) while `health()` keeps 10 s |
| `test_reply.py` | Every layout row in §6; mode in the header; packing at block boundaries; every message ≤ 4096 UTF-16 units with astral characters; oversized `output_text` split; a strikethrough after an emoji has the right UTF-16 entity offset |
| `test_keyboards.py` | Order, `None` when empty, `RerunCallback` round trip, `keyboard_modes` ignores foreign buttons |
| `test_errors.py` | The §7 table, parametrized, with an unknown code and `status_code=None` |
| `test_texts.py` | Handler called with `AsyncMock(spec=Message)`: chunks reply to the original, only the last has the keyboard; error replies; `/`-text and non-text make no API call |
| `test_rerun.py` | Keyboard removed before the API call; next keyboard = remaining modes minus tapped; restored on failure; duplicate tap makes no second call (the `MockTransport` handler waits on an `asyncio.Event`); deleted original and `InaccessibleMessage` alerts |
| `test_routing.py` | `Dispatcher.feed_update` with a real `Bot` on a `RecordingSession` (fixture in `apps/bot/tests/conftest.py`, records outgoing methods, returns canned results): private-only filters, commands vs text, callback filter, `api` injection, errors handler swallowing `TelegramForbiddenError` |

Manual check before merging, with `make up` and a test bot token: an English text, a Ukrainian text, Natural then Polished, a ~4000-character text (split), and the `litellm` container stopped (the 502 message).

## 10. Changes outside the bot

- `.env.example`: `BOT_PROCESS_TIMEOUT_S` (§8).
- `AGENTS.md` (its "keeping this file current" rule): `ApiClient` reaches handlers through dispatcher workflow data; messages are built with `aiogram.utils.formatting`, never a parse mode; routing is tested through `RecordingSession` and `feed_update`.

## 11. Definition of done

- `make lint && make test` pass.
- The manual check in §9 passes.
- No log line contains the user's text or the model's output.
- The bot answers nothing in groups.
