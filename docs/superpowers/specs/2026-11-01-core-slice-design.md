# Core Slice (`ProcessText`): Design

Status: draft for review · Date: 2026-11-01 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md)

## 1. Goal

Build `ProcessText` end to end: `POST /v1/texts/process` takes a user's text, improves it (English) or translates it (UA/RU → EN) in one LLM call, explains every change, and stores the result. This is sub-project 2 of 5 (Foundation, **Core slice**, Bot, Access, Admin dashboard).

Done means: with Ollama running, a `curl` to the endpoint returns an improved text with explained, positioned changes, and the interaction and its corrections are in Postgres.

## 2. Scope

In scope:

- Domain model, diff engine and reconciliation.
- LLM gateway: one LiteLLM adapter (Ollama in dev, OpenAI in prod), prompt `process_text.v1`.
- Tables `app_user`, `identity`, `interaction`, `correction` (migration `0002`).
- Minimal auth: shared bot token plus Telegram user id, auto-creating the user.
- `POST /v1/texts/process` and its contract DTOs.

Out of scope, with the reason:

| Item | Where it goes | Why not now |
| --- | --- | --- |
| Invites, user status, `403 forbidden` | Access | Access replaces auto-create in one function (§8) |
| Rate limits, daily budget, `usage_daily` | Access | Spend is logged per call now; accounting needs limits to be useful |
| Context profiles, `profile_id` | Own sub-project | Adding an optional request field later is a non-breaking change |
| `GET/PATCH /v1/me/settings` | Later | `explanation_language` is a request field until then (§4) |
| Variants for other modes, `vocabulary_candidates` | Later prompt version | Saved vocabulary is out of MVP scope; variants roughly triple output tokens |
| Bot handler for this endpoint | Bot | Separate sub-project; see §9 for the timeout it needs |

**Known gap:** until Access ships, anyone who can message the bot gets an account. The bot must not be made public before Access.

## 3. Decisions

| Area | Decision | Why |
| --- | --- | --- |
| LLM library | LiteLLM, pinned `==`, hash-locked in `uv.lock` | Dev and prod differ only by the model string in env. Pinned because LiteLLM had PyPI supply-chain compromises in March 2026 (1.82.7, 1.82.8) and October 2026 (1.107.0) |
| LLM port | Task-level `TextRewriter`, not a generic `LLMProvider` | Prompts and schemas stay in `infra`; a generic port would push them through `application`, which the import contracts forbid. Later features get their own task-level ports sharing the LiteLLM client |
| Language detection | By the model, in the same call (`source_language`) | Architecture §1. An alphabet heuristic breaks on mixed text |
| Explanation language | Request field, default `en` | No settings endpoint yet; when it arrives, the stored setting becomes the default |
| Output | One text for the chosen mode, plus changes | §2 |
| Diff vs model | The diff is the truth | The user sees every real edit and never an explanation of an edit that didn't happen |
| Identity | Minimal auth now | `interaction.user_id` is `NOT NULL` from day one, no backfill; the Bot sub-project calls the real endpoint |

## 4. Domain model

`domain/text_processing.py`, frozen dataclasses and `StrEnum`s, no I/O.

| Type | Fields | Why |
| --- | --- | --- |
| `Mode` | `minimal`, `natural`, `polished` | Architecture §1 |
| `Language` | `en`, `uk`, `ru` | One enum for detected and explanation language: users write in these and want explanations in these |
| `Category` | `grammar`, `articles`, `spelling`, `punctuation`, `word_choice`, `word_order`, `style`, `other` | Coarse on purpose: a small, distinct label set is classified more consistently, especially by small local models. `articles` is separate because UA/RU have none, so article errors are among the most frequent for these learners. `word_order` because UA/RU order is freer than English. `other` is assigned by reconciliation, never asked of the model |
| `Usage` | `model`, `prompt_version`, `tokens_in`, `tokens_out`, `cost: Decimal \| None` | Logged and stored per call. `Decimal` because sub-cent costs are summed; floats drift |
| `ModelChange` | `original`, `replacement`, `category`, `explanation` | What the model claims. Untrusted, never returned directly |
| `RewriteRequest` | `text`, `mode`, `explanation_language` | Crosses the port |
| `RewriteOutcome` | `source_language`, `output_text`, `changes: list[ModelChange]`, `usage`, `llm_response: Mapping[str, object]` | Crosses the port. `llm_response` is the validated model output dumped to JSON, for storage; never raw text |
| `Change` | `original`, `replacement`, `category`, `explanation: str \| None`, `span: tuple[int, int] \| None` | Reconciled result. `span` is code-point offsets into the input text; `None` for translations. Insertion: empty `original`; deletion: empty `replacement` |
| `ProcessedText` | `interaction_id`, `source_language`, `mode`, `output_text`, `changes` | Use case result |
| `AuthContext` | `user_id` | Use cases never see Telegram details (architecture §4) |
| `Provider` | `telegram` | Identity provider; web comes later |
| `RewriteFailed(reason)` | exception | The LLM could not produce a valid result; `reason` tells causes apart in logs |

## 5. Contracts

`packages/contracts/src/engleks_contracts/texts.py`:

```python
class ProcessTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)   # validator rejects blank text, does not strip
    mode: Mode = Mode.MINIMAL
    explanation_language: Language = Language.EN

class ChangeItem(BaseModel):
    original: str
    replacement: str
    category: Category
    explanation: str | None
    start: int | None   # Unicode code points into the request text; null for translations
    end: int | None

class ProcessTextResponse(BaseModel):
    interaction_id: UUID
    source_language: Language
    mode: Mode
    output_text: str
    changes: list[ChangeItem]
```

| Decision | Why |
| --- | --- |
| `max_length=4096` | Telegram's limit for one message, so any message the bot receives is accepted unchanged. Splitting input would cut the context the model needs. Also bounds tokens and cost per call |
| Reject blank text, don't strip | Stripping would shift every offset relative to what the client sent |
| Offsets in code points, documented in the DTO docstring | They are Python `str` indices. Telegram entities count UTF-16 units, so the bot must convert, or any emoji before an edit misaligns the highlight |
| `interaction_id` returned | The bot's inline buttons (architecture §2) need a handle to the interaction |
| `Mode`, `Language`, `Category` duplicated in contracts and domain | AGENTS.md rule 5. A unit test asserts equal values |
| New `ErrorCode`s: `unauthorized` (401), `llm_unavailable` (502) | The two failures this slice can produce. `forbidden` and `rate_limited` come with Access; adding values is safe while the bot is the only client |

## 6. LLM gateway (`infra/llm/`)

### Config (`API_` prefix)

| Setting | Why |
| --- | --- |
| `LLM_MODEL`, a LiteLLM model string: `ollama_chat/qwen3:8b` (dev), `openai/<model>` (prod) | LiteLLM routes on the prefix; dev and prod differ only by env |
| `LLM_API_BASE: str \| None` | Ollama's URL in dev; OpenAI needs none |
| `LLM_API_KEY: SecretStr \| None`, Compose secret `llm_api_key` | Secret; Ollama needs none |
| `LLM_TIMEOUT_S = 60` | A 4096-character text with explanations from a small local model can take tens of seconds; without a timeout a hung provider holds the request forever |

### Adapter `LiteLLMRewriter` (implements `TextRewriter`)

| Decision | Why |
| --- | --- |
| Prompt `prompts/process_text.v1.j2`, rendered with Jinja2; version string `process_text.v1` logged and stored | AGENTS.md: a changed prompt is a new file. The stored version lets prompt versions be compared |
| Instructions in the system message; the user's text alone in the user message, never in the template | Text like "ignore previous instructions" stays data. An identical system prefix qualifies for OpenAI's automatic prompt caching (prefixes of 1024+ tokens) |
| `temperature = 0.2` | Corrections should be reproducible |
| Infra-only Pydantic model `LLMRewrite` → JSON schema as `response_format` | LiteLLM passes it to OpenAI as strict structured output and to Ollama as `format`: one code path for both |
| Validate with `model_validate_json`; on failure retry once, appending the invalid reply and the validation error | Architecture §2 asks for one retry. A plain resend at low temperature tends to repeat the mistake; a small model needs to be told what was wrong |
| `Usage` sums both attempts | Both cost money |
| Cost from `litellm.completion_cost`; `None` and a `llm_cost_unknown` log when LiteLLM has no price | Ollama is free, so `0`. For an unpriced cloud model `0` would be a lie |
| Timeout, connection, auth, provider errors and a second invalid reply raise `RewriteFailed(reason)` | Use cases must not know LiteLLM's exception classes. The client can't fix any of these, so one error code; `reason` separates them in logs |
| The completion function is a constructor argument, default `litellm.acompletion` | Tests script exact consecutive responses ("invalid, then valid"); LiteLLM's `mock_response` cannot, and ties tests to its internals |
| Every attempt logs `llm_call`: `model`, `prompt_version`, `attempt`, `tokens_in`, `tokens_out`, `cost`, `duration_ms` | AGENTS.md logging rules |
| LiteLLM's loggers forced to `WARNING` regardless of `API_LOG_LEVEL` | At `DEBUG` LiteLLM logs request bodies, which contain the user's text |
| `litellm` imported only in `infra/llm/` | Already enforced by import-linter |

### Dependency hardening

- `litellm` pinned `==` to a release that is ≥ 1.83.7 (earlier versions trip `make scan` on proxy CVEs, e.g. CVE-2026-42208, CVE-2026-42271), published before 2026-10-21, and not 1.82.7, 1.82.8 or 1.107.0. The exact version is chosen and checked in the plan.
- `jinja2` declared directly: LiteLLM pulls it in, but we import it.
- `uv.lock` records hashes; `uv sync` verifies them.

## 7. Diff engine and reconciliation (`domain/diff.py`)

Runs only when `source_language == en`. For `uk`/`ru` the model's changes pass through with `span=None`: there is no shared text to align a translation against.

### Tokenizing and diffing

| Decision | Why |
| --- | --- |
| Tokens: `re.finditer(r"\w+(?:['’]\w+)*\|[^\w\s]")`, each keeping `start`/`end` in the source | Word-level, as architecture §2 asks. Punctuation as its own token makes an added comma `"" → ","`, not `"word" → "word,"`. Contractions stay one token, so `dont → don't` is one change. `’` because phones insert curly apostrophes |
| Whitespace is not a token | Whitespace-only edits teach nothing, and Telegram clients may alter whitespace |
| `difflib.SequenceMatcher(None, a, b, autojunk=False)` | Standard library. With the default `autojunk`, tokens over 1% of a 200+ token sequence count as junk, so `the` and `,` stop anchoring the alignment on texts of roughly 200 words, well within the 4096-character limit |
| Each non-equal opcode → `Hunk(span, original, replacement)`; `original = input[span]` from first token start to last token end; an insertion gets a zero-length span at the next token's start or at the end of the text | Slicing the real input keeps offsets and displayed text consistent, with the user's own spacing |
| No optimization | At most about 900 tokens; quadratic worst case is negligible |

### Reconciliation

| Rule | Why |
| --- | --- |
| A model change matches a hunk when `original` and `replacement` each agree after normalization (casefold, collapsed whitespace), where agree means one contains the other; an empty side matches only an empty side | Models quote a little more or less context than the diff ("in **the** morning" vs "the"). The empty-side rule stops `""` matching everything |
| Model changes in order; each takes the first matching hunk in text order, preferring unclaimed hunks | Models list changes roughly in text order; settles repeated words without fuzzy scoring |
| One `Change` per hunk; several matches in one hunk → first category wins, explanations joined | The hunk is the unit the user sees highlighted |
| Unmatched hunk → `category=other`, `explanation=None`; model change with no hunk → dropped | "The diff is the truth" |
| Result sorted by `span.start`; identical input and output → no changes | Render order; an empty list is the honest "nothing to fix" |
| Log `diff_reconciled` with `hunks`, `matched`, `unexplained`, `dropped` | Measures how faithful each prompt version is. Counts only |

## 8. Persistence and auth

### Tables (migration `0002`)

| Table | Columns | Why |
| --- | --- | --- |
| `app_user` | `id`, `created_at` | Not `user`: reserved in Postgres, so every raw query would need quoting. Status, limits and settings come with the code that reads them (Access, Settings) |
| `identity` | `id`, `user_id` FK, `provider`, `external_id TEXT`, `created_at`, `UNIQUE(provider, external_id)` | Keeps Telegram out of `app_user` (architecture §4). `TEXT` because OIDC subjects are strings. The unique constraint makes find-or-create safe |
| `interaction` | `id`, `user_id` FK, `created_at`, `mode`, `source_language`, `explanation_language`, `input_text`, `output_text`, `llm_response JSONB`, `model`, `prompt_version`, `tokens_in`, `tokens_out`, `cost NUMERIC(12,8) NULL`; index `(user_id, created_at DESC)` | Architecture §7. `llm_response` allows auditing prompts and replaying reconciliation without paying again. The index serves `GetHistory` |
| `correction` | `id`, `interaction_id` FK `ON DELETE CASCADE`, `position`, `original`, `replacement`, `category`, `explanation NULL`, `span_start NULL`, `span_end NULL` | One row per `Change`; `position` keeps display order; cascade avoids orphans |

| Decision | Why |
| --- | --- |
| UUIDv7 primary keys (`uuid.uuid7()`, Python 3.14) | Time-ordered, so inserts append to the B-tree; no row-count leak once ids reach clients |
| Enums stored as `TEXT`, validated by domain enums | Native Postgres enums need a migration per new value, and Alembic autogenerate doesn't detect value changes, so `alembic check` wouldn't catch drift. Only our code writes these columns |
| Failed LLM calls not persisted, only logged | No table needs them until Access budget accounting |

### Repositories and sessions

| Decision | Why |
| --- | --- |
| Ports `IdentityRepository.get_or_create_user(provider, external_id) -> UUID` and `InteractionRepository.add(...)` in `domain/ports.py`; SQLAlchemy adapters in `infra/db/` | AGENTS.md port and adapter rule; use cases unit-tested with fakes |
| Find-or-create: `INSERT … ON CONFLICT DO NOTHING`, then `SELECT` | A user's first two messages can arrive at once; read-then-insert would crash one on the unique constraint |
| Each repository method is one transaction and commits itself; no unit-of-work port yet | Each write covers one aggregate. A unit of work earns its place when a use case writes two aggregates atomically (possibly `usage_daily` in Access) |
| `async_sessionmaker(expire_on_commit=False)` built in `create_app()`, on `app.state`; request-scoped session via `Depends` | AGENTS.md shared-object pattern |
| Auth commits before the LLM call; no connection is held during it | A session holds its pooled connection until the transaction ends. With the default pool (5 + 10 overflow), 15 slow LLM calls would block every other request, `/readyz` included |

### Minimal auth

| Decision | Why |
| --- | --- |
| `API_SERVICE_TOKEN: SecretStr`; `transport/auth.py` compares it to `X-Bot-Token` with `hmac.compare_digest` | Shared secret from architecture §4; the bot already sends the header. `==` leaks the token through timing |
| `X-Telegram-User-Id` must be a positive integer; missing or invalid headers → `401 unauthorized` | Garbage never becomes an identity |
| `application/authenticate.py`: `(Provider.TELEGRAM, external_id)` → `AuthContext`, auto-creating the user | Access changes this one function (unknown identity → `401`, invite required); nothing else changes |

## 9. Use case and endpoint

```
POST /v1/texts/process
  transport/auth.py     headers → authenticate() → AuthContext                  (commits)
  transport/texts.py    ProcessTextRequest → RewriteRequest
  application/process_text.py
      rewriter.rewrite(request)              → RewriteOutcome   (≤ 60 s, 1 retry)
      en:    changes = reconcile(diff(input, output), outcome.changes)
      uk/ru: changes = model changes, span=None
      interactions.add(...)                  (one transaction: interaction + corrections)
  transport             ProcessedText → ProcessTextResponse
```

| Decision | Why |
| --- | --- |
| `process_text(auth, request, rewriter, interactions) -> ProcessedText`, a function taking ports | The `check_readiness` pattern |
| Interaction id generated in the use case (UUIDv7) | The use case builds the response that carries it; no round trip to learn our own id |
| Nothing saved when the rewrite fails | There is no result; tokens are already logged |
| `RewriteFailed` → `502 llm_unavailable`, logged once in the transport handler with `reason` | AGENTS.md: log an error once, where it is handled |
| 401, 422 and 502 declared in the route's `responses=` | Visible in OpenAPI for the future web client |
| Rewriter built in `create_app()`, read via a `Depends` provider | Shared-object pattern; HTTP tests replace it through `app.dependency_overrides` |

**For the Bot sub-project:** `LLM_TIMEOUT_S` applies per attempt, so the endpoint's worst case is about `2 × LLM_TIMEOUT_S` (120 s by default). `ApiClient`'s 10-second timeout must exceed that for this call. Otherwise the bot gives up while the API is still working, and the user sees an error for a request that succeeded and was billed.

## 10. Testing

| Level | Cases | Why |
| --- | --- | --- |
| Contracts | Round trip; blank text rejected; 4096 characters accepted, 4097 rejected | Boundaries the bot relies on |
| Unit: enum parity | `Mode`, `Language`, `Category` equal in contracts and domain | AGENTS.md rule 5 |
| Unit: diff | Table-driven: comma insert, article insert, replacement, deletion, `dont → don't`, curly apostrophe, whitespace-only (no change), repeated words, emoji before an edit, a 300-token text (`autojunk` regression), identical text | One row per decision in §7 |
| Unit: reconcile | Exact match, containment with extra context, empty-side rule, unexplained → `other`, invented change dropped, two model changes in one hunk | One row per rule in §7 |
| Unit: use case | Fakes: EN path saves spans; UK path skips the diff; failure saves nothing | Use case requirements, no Docker |
| Unit: authenticate | Same identity twice → same user | Find-or-create contract |
| Unit: adapter | Scripted completion function: valid; invalid then valid (corrective message sent, usage summed); invalid twice → `RewriteFailed`; timeout → `RewriteFailed`; user text absent from the system message | §6 decisions |
| Integration: Postgres | Concurrent find-or-create via `asyncio.gather` → one user; interaction with corrections round-trips; existing `alembic check` passes | Only a real database proves `ON CONFLICT` and model/migration parity |
| Integration: HTTP | `client_for(create_app(pg_settings))`, fake rewriter: 401 without or with a wrong token, 401 with a bad user id, 422 blank text, 502 on failure, 200 with rows saved | Endpoint contract end to end, no LLM |
| `@pytest.mark.llm` | Configured model: English text with known errors → valid result with at least one explained change; UA text → `source_language = uk` | Proves prompt and schema with real models; asserts structure, not wording |

## 11. Configuration, Compose and docs changes

Approved as part of this design (AGENTS.md "ask before" items):

- Runtime dependencies `litellm` (pinned, §6) and `jinja2`.
- `compose.yaml`: mount the existing `service_token` secret into `api` as `api_service_token`; add an `llm_api_key` secret for `api`; set `API_LLM_MODEL` and `API_LLM_API_BASE` on `api`, pointing at the `ollama` service for dev.

Updated in the same change:

- `.env.example`: `API_SERVICE_TOKEN`, `API_LLM_MODEL`, `API_LLM_API_BASE`, `API_LLM_API_KEY`, `API_LLM_TIMEOUT_S`.
- `docs/architecture.md`: `user` → `app_user`, the category list, the response shape.
- `AGENTS.md`: the first repository, LLM adapter and auth dependency patterns (its "keeping this file current" rule).

## 12. Definition of done

- `make lint && make test` pass; `make test-llm` passes against Ollama.
- `make scan` reports no fixable findings.
- With `make up`, a `curl` to `/v1/texts/process` with valid headers returns explained, positioned changes for English input and a translation for Ukrainian input, and the rows are in `interaction` and `correction`.
- No log line contains the user's text or the model's output.
