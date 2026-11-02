# Core Slice (`ProcessText`): Design

Status: draft for review · Date: 2026-11-01 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md)

## 1. Goal

Build `ProcessText` end to end: `POST /v1/texts/process` takes a user's text, improves it (English) or translates it (UA/RU → EN) in one LLM call, teaches up to five short English lessons drawn from the edits, and stores the result. This is sub-project 2 of 5 (Foundation, **Core slice**, Bot, Access, Admin dashboard).

Done means: with the LiteLLM proxy and Ollama running, a `curl` to the endpoint returns an improved text with lessons, and the interaction is in Postgres.

## 2. Scope

In scope:

- Domain model.
- LLM gateway: a LiteLLM Proxy Compose service (Ollama upstream in dev, OpenAI in prod, Admin UI on), and one API adapter that calls only the proxy; prompt `process_text.v3`.
- Tables `app_user`, `identity`, `interaction` (migration `0001`).
- Minimal auth: shared bot token plus Telegram user id, auto-creating the user.
- `POST /v1/texts/process` and its contract DTOs.

Out of scope, with the reason:

| Item | Where it goes | Why not now |
| --- | --- | --- |
| Invites, user status, `403 forbidden` | Access | Access replaces auto-create in one function (§8) |
| Rate limits, daily budget, `usage_daily` | Access | Spend is logged per call now; accounting needs limits to be useful |
| Context profiles, `profile_id` | Own sub-project | Adding an optional request field later is a non-breaking change |
| Lesson language, per-user settings, `vocabulary_candidates` | Later prompt version | One behaviour keeps the prompt small enough for local models; saved vocabulary is out of MVP scope |
| Bot handler for this endpoint | Bot | Separate sub-project; see §9 for the timeout it needs |

**Known gap:** until Access ships, anyone who can message the bot gets an account. The bot must not be made public before Access.

## 3. Decisions

| Area | Decision | Why |
| --- | --- | --- |
| LLM gateway | LiteLLM Proxy as its own Compose service; the API calls only the proxy, never a provider or Ollama | Provider keys, model choice and routing live in the proxy, and its Admin UI gives model, key and spend management for free. The proxy image is pinned by digest because LiteLLM was compromised in March 2026 (1.82.7, 1.82.8) and October 2026 (1.107.0, PyPI and official images) |
| Ollama | Reachable only from the proxy, on a separate network | Enforces "no direct Ollama" by network design, not convention |
| LLM port | Task-level `TextRewriter`, not a generic `LLMProvider` | Prompts and schemas stay in `infra`; a generic port would push them through `application`, which the import contracts forbid. Later features get their own task-level ports sharing the proxy client |
| Language detection | By the model, in the same call (`source_language`) | Architecture §1. An alphabet heuristic breaks on mixed text |
| Output | One corrected or translated text, plus up to five lessons in English | Lessons teach reusable patterns; an edit log of every change is noise for a learner |
| One prompt | `process_text.v3`, no template variables | One behaviour for every request. It stays a Jinja template so later versions can add variables without changing how prompts load |
| Identity | Minimal auth now | `interaction.user_id` is `NOT NULL` from day one, no backfill; the Bot sub-project calls the real endpoint |

## 4. Domain model

`domain/text_processing.py`, frozen dataclasses and `StrEnum`s, no I/O.

| Type | Fields | Why |
| --- | --- | --- |
| `Language` | `en`, `uk`, `ru` | The detected source language: users write in these |
| `Usage` | `model`, `prompt_version`, `tokens_in`, `tokens_out`, `cost: Decimal \| None` | Logged and stored per call; per-user cost is what Access budgets need, and the proxy only sees one caller. `Decimal` because sub-cent costs are summed; floats drift |
| `RewriteOutcome` | `source_language`, `output_text`, `explanations: list[str]`, `usage`, `llm_response: Mapping[str, object]` | Returned by `TextRewriter.rewrite(text: str)`. `llm_response` is the validated model output dumped to JSON, for storage; never raw text |
| `Interaction` | `id`, `user_id`, `input_text`, `outcome` | What `InteractionRepository.add` stores |
| `ProcessedText` | `interaction_id`, `source_language`, `output_text`, `explanations` | Use case result |
| `AuthContext` | `user_id` | Use cases never see Telegram details (architecture §4) |
| `Provider` | `telegram` | Identity provider; web comes later |
| `RewriteFailed(reason)` | exception | The LLM could not produce a valid result; `reason` tells causes apart in logs |

## 5. Contracts

`packages/contracts/src/engleks_contracts/texts.py`:

```python
class ProcessTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)   # validator rejects blank text, does not strip

class ProcessTextResponse(BaseModel):
    interaction_id: UUID
    source_language: Language   # en | uk | ru
    output_text: str
    explanations: list[str]     # English lessons; the prompt asks for at most 5, not validated
```

| Decision | Why |
| --- | --- |
| `max_length=4096` | Telegram's limit for one message, so any message the bot receives is accepted unchanged. Splitting input would cut the context the model needs. Also bounds tokens and cost per call |
| Reject blank text, don't strip | The model sees exactly what the user wrote |
| Unknown request fields are ignored | Pydantic's default; a client built for an older contract still gets a 200 |
| `interaction_id` returned | A handle for history and later features |
| `Language` duplicated in contracts and domain | AGENTS.md rule 5. A unit test asserts equal values |
| New `ErrorCode`s: `unauthorized` (401), `llm_unavailable` (502) | The two failures this slice can produce. `forbidden` and `rate_limited` come with Access; adding values is safe while the bot is the only client |

## 6. LLM gateway

```
network default:  api ──HTTP──▶ litellm:4000 ──▶ postgres (database `litellm`)
network llm:                    litellm ──▶ ollama:11434     (dev profile)
                                litellm ──▶ api.openai.com   (prod)
```

### LiteLLM Proxy (Compose service `litellm`)

| Decision | Why |
| --- | --- |
| Image `ghcr.io/berriai/litellm` pinned by digest; version ≥ 1.83.7, published before 2026-10-21, not 1.82.7, 1.82.8 or 1.107.0. The plan picks and checks the exact version | A digest cannot be swapped under us; the October compromise shipped in official images under a normal tag. ≥ 1.83.7 fixes the 2026 proxy CVEs (e.g. CVE-2026-42208 SQL injection in key checks, CVE-2026-42271 command execution) |
| Same proxy in dev and prod; only its upstream changes | One code path everywhere; dev exercises the hop prod uses |
| `ollama` on a separate `llm` network shared only with `litellm`; its host port is removed; models are pulled with `docker compose exec ollama ollama pull …` | The API cannot reach Ollama even by mistake; the host port was the other way around the proxy |
| `litellm` publishes `127.0.0.1:4000`, like `postgres` | An API run on the host (`make dev-db` + `uv run`) must reach the proxy; localhost keeps it off the network |
| One config file, `deploy/litellm/config.yaml`, with one model alias `engleks-rewrite` whose upstream comes from env: `model: os.environ/LLM_UPSTREAM_MODEL`, `api_base: os.environ/LLM_UPSTREAM_API_BASE`, `api_key: os.environ/OPENAI_API_KEY` | The API always asks for `engleks-rewrite`; switching Ollama ↔ OpenAI or changing models is a proxy env change, no API redeploy. One file means dev and prod configs cannot drift |
| `num_retries: 0`; proxy request timeout just under `API_LLM_TIMEOUT_S` | Retries live in one place (the API's corrective retry), so the worst case stays predictable. The API gets a clean error instead of timing out first |
| Admin UI enabled at `/ui`, reachable only on `127.0.0.1:4000` (prod: SSH tunnel) | Model management, keys, LLM spend and request logs without building them. Not exposed publicly, given the 2026 auth-bypass and SQL-injection CVEs |
| UI login with `UI_USERNAME`/`UI_PASSWORD`, separate from the master key | A leaked UI password is not a leaked master key; the master key rotates without changing logins |
| The API uses a virtual key restricted to `engleks-rewrite`; the master key is for admin work only | A leaked API key can call one model and cannot manage the proxy |
| Prompts and responses are not stored in proxy spend logs; proxy log level `INFO`, never `DEBUG` | AGENTS.md: never log the user's text or the model's output. The plan checks the setting names against the pinned version |
| Secrets `litellm_master_key`, `litellm_db_password`, `litellm_ui_username`, `litellm_ui_password`, `openai_api_key` (prod) are Compose secrets; a small `entrypoint` exports them from `/run/secrets/*`, then `exec`s LiteLLM | LiteLLM reads keys from env vars, not files; AGENTS.md keeps secrets in `/run/secrets`. The plan checks the image has `sh` |

### Proxy database

| Decision | Why |
| --- | --- |
| Database `litellm` with its own role on our Postgres server, created by an init script in `docker-entrypoint-initdb.d` | The Admin UI needs a database (logins, keys, spend). One server to run and back up. The separate role means a compromised proxy cannot read our tables. A separate database keeps LiteLLM's Prisma tables out of Alembic's view: in a shared schema, autogenerate would try to drop them |
| LiteLLM migrates its own database at startup | That is how LiteLLM ships; Alembic stays responsible only for `engleks` |
| Existing dev volumes are recreated once (`make down`, remove `pgdata`, `make up`) | Postgres runs init scripts only on an empty data directory |
| `make llm-key` creates the API's virtual key via the proxy's `/key/generate` (master key, model `engleks-rewrite`) and prints it for `.env` | A one-time bootstrap that is repeatable and documented, rather than clicks in the UI |

### API config (`API_` prefix)

| Setting | Why |
| --- | --- |
| `LLM_BASE_URL` (`http://litellm:4000/v1` in Compose, `http://localhost:4000/v1` on the host) | The proxy's OpenAI-compatible endpoint |
| `LLM_MODEL = engleks-rewrite` | The proxy alias, not a provider model |
| `LLM_API_KEY: SecretStr`, Compose secret `api_llm_key` | The API's virtual key |
| `LLM_TIMEOUT_S = 60` | A 4096-character text with explanations from a small local model can take tens of seconds; without a timeout a hung upstream holds the request forever |

### Adapter `ProxyRewriter` (`infra/llm/`, implements `TextRewriter`)

| Decision | Why |
| --- | --- |
| `httpx2` POST to `{LLM_BASE_URL}/chat/completions` with `Authorization: Bearer <virtual key>` | The proxy is OpenAI-compatible; `httpx2` is already in the workspace. The API does not depend on the `litellm` package |
| Prompt `prompts/process_text.v3.j2`, rendered with Jinja2 and no variables; version string `process_text.v3` logged and stored | AGENTS.md: a changed prompt is a new file. The stored version lets prompt versions be compared |
| Instructions in the system message; the user's text alone in the user message, never in the template | Text like "ignore previous instructions" stays data. An identical system prefix qualifies for OpenAI's automatic prompt caching (prefixes of 1024+ tokens) |
| `temperature = 0.2` | Corrections should be reproducible |
| Infra-only Pydantic model `LLMRewrite` → JSON schema as `response_format` | The proxy passes it to OpenAI as strict structured output and to Ollama as `format`: one code path for both |
| Validate with `model_validate_json`; on failure retry once, appending the invalid reply and the validation error | Architecture §2 asks for one retry. A plain resend at low temperature tends to repeat the mistake; a small model needs to be told what was wrong |
| Tokens from the body's `usage`; model from the body's `model`; cost from the `x-litellm-response-cost` header, `None` plus a `llm_cost_unknown` log when missing | The proxy computes cost from LiteLLM's price map. `0` for an unpriced model would be a lie. The `llm` test checks `model` names the upstream model, not the alias, since that is what we store |
| `Usage` sums both attempts | Both cost money |
| Timeout, connection errors, non-2xx responses and a second invalid reply raise `RewriteFailed(reason)` | Use cases must not know HTTP details. The client can't fix any of these, so one error code; `reason` separates them in logs |
| An `httpx2` transport is a constructor argument | Tests inject `httpx2.MockTransport` to script exact consecutive responses ("invalid, then valid"), as the bot tests do |
| Every attempt logs `llm_call`: `model`, `prompt_version`, `attempt`, `tokens_in`, `tokens_out`, `cost`, `duration_ms` | AGENTS.md logging rules |

### Dependencies and scanning

- API runtime dependencies: `httpx2` (already in the workspace and lock) and `jinja2`. No `litellm` Python package; it stays on import-linter's forbidden lists.
- `make scan` and the CI scan job add `trivy image` for the pinned LiteLLM image: `trivy fs` never looks inside images, and the proxy is the largest new risk.

## 7. Lessons

The model returns `explanations`: up to five short lessons in English, each a reusable pattern drawn
from a correction it made or a non-literal translation choice, not a description of one edit. They
are returned as the model wrote them (after the blank-string check in `LLMRewrite`) and are stored
only inside `interaction.llm_response`. Minor fixes may go unexplained, so an empty list does not
mean the text was unchanged.

## 8. Persistence and auth

### Tables (migration `0001`)

| Table | Columns | Why |
| --- | --- | --- |
| `app_user` | `id`, `created_at` | Not `user`: reserved in Postgres, so every raw query would need quoting. Status, limits and settings come with the code that reads them (Access, Settings) |
| `identity` | `id`, `user_id` FK, `provider`, `external_id TEXT`, `created_at`, `UNIQUE(provider, external_id)` | Keeps Telegram out of `app_user` (architecture §4). `TEXT` because OIDC subjects are strings. The unique constraint makes find-or-create safe |
| `interaction` | `id`, `user_id` FK, `created_at`, `source_language`, `input_text`, `output_text`, `llm_response JSONB`, `model`, `prompt_version`, `tokens_in`, `tokens_out`, `cost NUMERIC(12,8) NULL`; index `(user_id, created_at DESC)` | Architecture §7. `llm_response` (with the lessons) allows auditing prompt versions without paying again. The index serves `GetHistory` |

| Decision | Why |
| --- | --- |
| UUIDv7 primary keys (`uuid.uuid7()`, Python 3.14) | Time-ordered, so inserts append to the B-tree; no row-count leak once ids reach clients |
| Enums stored as `TEXT`, validated by domain enums | Native Postgres enums need a migration per new value, and Alembic autogenerate doesn't detect value changes, so `alembic check` wouldn't catch drift. Only our code writes these columns |
| Failed LLM calls not persisted, only logged | No table needs them until Access budget accounting |

### Repositories and sessions

| Decision | Why |
| --- | --- |
| Ports `IdentityRepository.get_or_create_user(provider, external_id) -> UUID` and `InteractionRepository.add(interaction)` in `domain/ports.py`; SQLAlchemy adapters in `infra/db/` | AGENTS.md port and adapter rule; use cases unit-tested with fakes |
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
  transport/texts.py    ProcessTextRequest.text
  application/process_text.py
      rewriter.rewrite(text)                 → RewriteOutcome   (≤ 60 s, 1 retry)
      interactions.add(Interaction(...))     (one transaction)
  transport             ProcessedText → ProcessTextResponse
```

| Decision | Why |
| --- | --- |
| `process_text(auth, text, rewriter, interactions) -> ProcessedText`, a function taking ports | The `check_readiness` pattern |
| Interaction id generated in the use case (UUIDv7) | The use case builds the response that carries it; no round trip to learn our own id |
| Nothing saved when the rewrite fails | There is no result; tokens are already logged |
| `RewriteFailed` → `502 llm_unavailable`, logged once in the transport handler with `reason` | AGENTS.md: log an error once, where it is handled |
| 401, 422 and 502 declared in the route's `responses=` | Visible in OpenAPI for the future web client |
| Rewriter built in `create_app()`, read via a `Depends` provider | Shared-object pattern; HTTP tests replace it through `app.dependency_overrides` |

**For the Bot sub-project:** `LLM_TIMEOUT_S` applies per attempt, so the endpoint's worst case is about `2 × LLM_TIMEOUT_S` (120 s by default). `ApiClient`'s 10-second timeout must exceed that for this call. Otherwise the bot gives up while the API is still working, and the user sees an error for a request that succeeded and was billed.

## 10. Testing

| Level | Cases | Why |
| --- | --- | --- |
| Contracts | Request is only `text` and ignores unknown fields; response round trip with exactly its four fields; blank text rejected; 4096 characters accepted, 4097 rejected | Boundaries the bot relies on |
| Unit: enum parity | `Language` equal in contracts and domain | AGENTS.md rule 5 |
| Unit: use case | Fakes: the outcome is returned and saved with the input text; a translation without lessons; failure saves nothing | Use case requirements, no Docker |
| Unit: authenticate | Same identity twice → same user | Find-or-create contract |
| Unit: adapter | `httpx2.MockTransport`: valid; invalid then valid (corrective message sent, usage summed); invalid twice → `RewriteFailed`; timeout and non-2xx → `RewriteFailed`; missing cost header → `cost=None`; bearer key and `engleks-rewrite` model sent; user text absent from the system message; the system message is the v3 template verbatim | §6 decisions |
| Integration: Postgres | Concurrent find-or-create via `asyncio.gather` → one user; interaction round-trips; `alembic check` passes; the schema has exactly `app_user`, `identity`, `interaction` | Only a real database proves `ON CONFLICT` and model/migration parity |
| Integration: HTTP | `client_for(create_app(pg_settings))`, fake rewriter: 401 without or with a wrong token, 401 with a bad user id, 422 blank text, 502 on failure, 200 with rows saved, 200 when an old client sends extra fields | Endpoint contract end to end, no LLM |
| `@pytest.mark.llm` | Through the running proxy: English text with known errors → corrected text with 1–5 lessons; UA text → `source_language = uk`; stored `model` is the upstream model, not the alias | Proves prompt, schema and proxy hop with real models; asserts structure, not wording |

## 11. Configuration, Compose and docs changes

Approved as part of this design (AGENTS.md "ask before" items):

- API runtime dependencies `httpx2` and `jinja2`.
- `compose.yaml`:
  - new `litellm` service (§6), pinned by digest, publishing `127.0.0.1:4000`, `DATABASE_URL` to the `litellm` database, secrets exported by an entrypoint;
  - new `llm` network shared by `litellm` and `ollama` only; `ollama` loses its host port;
  - Postgres init script creating the `litellm` role and database;
  - new secrets `litellm_master_key`, `litellm_db_password`, `litellm_ui_username`, `litellm_ui_password`, `openai_api_key` (prod), `api_llm_key`; existing `service_token` also mounted into `api` as `api_service_token`;
  - `API_LLM_BASE_URL` and `API_LLM_MODEL` set on `api`.
- CI and `make scan`: add `trivy image` for the pinned LiteLLM image.

Updated in the same change:

- `Makefile`: `llm-key` target (§6).
- `deploy/litellm/config.yaml` (§6).
- `.env.example`: `API_SERVICE_TOKEN`, `API_LLM_BASE_URL`, `API_LLM_MODEL`, `API_LLM_API_KEY`, `API_LLM_TIMEOUT_S`, and the proxy's `LLM_UPSTREAM_MODEL`, `LLM_UPSTREAM_API_BASE`, master key, DB password, UI credentials, `OPENAI_API_KEY`.
- `README.md`: dev setup gains pulling the Ollama model, recreating the dev volume once, and `make llm-key`.
- `docs/architecture.md`: `user` → `app_user`, the response shape.
- `docs/architecture.md` also: the LiteLLM proxy in the component diagram and in §9 Deployment (add `litellm` to `api`, `bot` and `postgres`, plus `ollama` in dev).
- `AGENTS.md`: the first repository, LLM adapter (via the proxy) and auth dependency patterns (its "keeping this file current" rule).

## 12. Definition of done

- `make lint && make test` pass; `make test-llm` passes through the proxy against Ollama.
- `make scan` (filesystem and LiteLLM image) reports no fixable findings.
- The API container cannot resolve or reach `ollama`.
- The LiteLLM Admin UI is reachable on `127.0.0.1:4000/ui` and shows the `engleks-rewrite` model and the API's virtual key.
- With `make up`, a `curl` to `/v1/texts/process` with valid headers returns a correction with lessons for English input and a translation for Ukrainian input, and the rows are in `interaction`.
- No log line contains the user's text or the model's output.
