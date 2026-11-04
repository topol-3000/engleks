# Engleks: MVP Architecture

## 1. Goal and MVP scope

A trainer for English writing, not a translator. The MVP includes:

- **Improve**: one prompt (`process_text.v5`) corrects the text and returns up to five short lessons in English: reusable language patterns drawn from the edits, not an edit log.
- **Translate**: UA/RU → EN with automatic language detection, in the same LLM call and with the same English lessons as Improve.
- **Context profiles**: 2–3 per user, plus request history.
- **Multiple users**: invite codes, usage limits and a simple admin dashboard.

Out of scope for the MVP: SRS/FSRS, saved vocabulary, Explain, How to say, voice, exercises and the web client. The API is ready for a web client later.

## 2. Components

```mermaid
flowchart LR
  U[User] --> TG[Telegram]
  TG --> BOT["Bot (aiogram), thin HTTP client"]
  BOT -->|X-Bot-Token + X-Telegram-User-Id| API["API (FastAPI)"]
  ADM[Admin] -->|/admin, session| API
  WEB["Web (later)"] -.-> API
  API --> APP[Application: use cases]
  APP --> LLM[LLM Gateway]
  APP --> REPO[Repositories]
  REPO --> PG[(PostgreSQL)]
  LLM --> PROXY["LiteLLM proxy"]
  PROXY --> P1[Cloud LLM]
  PROXY -.-> P2["Ollama (dev)"]
```

**Bot**: receives messages, calls the API and renders the reply. Long replies are split into 4096-character messages. The bot has no database and no business logic.

**API**: the single entry point. Layers: Transport (routes, auth, validation) → Application → Domain.

**Application (use cases)**:

- User: `ProcessText` (Improve/Translate), `ManageProfiles`, `GetHistory`, `RedeemInvite`.
- Admin: `ListUsers`, `SetUserStatus`, `SetUserLimits`, `CreateInvite`, `RevokeInvite`, `UsageStats`.

**LLM Gateway**:

- Task-level ports (`TextRewriter`), not a generic `LLMProvider`, so prompts and schemas stay in infra.
- One adapter per port, calling only the LiteLLM proxy (a Compose service). Provider keys, model choice and routing live in the proxy; it serves a cloud LLM in prod and Ollama in dev, and Ollama is reachable only from the proxy.
- Versioned prompt templates.
- Structured output validated with Pydantic, with one retry on invalid JSON.

**Persistence**: PostgreSQL with SQLAlchemy and Alembic. Raw LLM responses, including the lessons, are stored as JSONB.

The migration history was reset before release to a single `0001`. A local database still at an
older revision fails `make migrate` with "Can't locate revision". Recreate only the API's database,
then migrate. Do not use `docker compose down -v`: it also wipes the LiteLLM database (its keys) and
the Ollama models.

```bash
docker compose exec postgres psql -U engleks -d postgres \
  -c 'DROP DATABASE engleks WITH (FORCE)' -c 'CREATE DATABASE engleks'
make migrate
```

**Admin UI**: server-rendered pages (Jinja2 + htmx, or SQLAdmin) mounted at `/admin` inside the API.

## 3. Main flow

```mermaid
sequenceDiagram
  participant B as Bot
  participant A as API
  participant L as LLM Gateway
  participant D as PostgreSQL
  B->>A: POST /v1/texts/process {text}
  A->>D: auth: identity → user, status, limits
  A->>D: rate limit + daily budget check
  A->>A: detect language, build prompt
  A->>L: generate(schema)
  L-->>A: JSON {source_language, output_text, explanations[]}
  A->>D: save interaction; usage_daily += tokens
  A-->>B: ProcessTextResponse {interaction_id, source_language, output_text, explanations[]}
```

Errors:

- `401`: `unauthorized` (bad bot token or user id) or `invite_required` (unknown user).
- `403`: `user_disabled`.
- `429`: `rate_limited` (too many requests in the current minute) or `daily_budget` (today's tokens are spent).
- `502`: LLM failure.

## 4. Authentication (option A)

- The bot and the API share a secret, `X-Bot-Token`. The bot also sends `X-Telegram-User-Id`.
- Signup: `/start <code>` or a deep link → `POST /v1/auth/redeem {code}` (the Telegram id comes from `X-Telegram-User-Id`) → a user and an identity are created.
- Auth is isolated in an `AuthContext → user_id` layer, so use cases don't know about Telegram.
- Possible later options: B (per-user JWT) and C (web login with Telegram Login or OIDC, plus account linking).

## 5. Limits

- Rate limit: N requests per minute per user. Counters in Postgres for the MVP (`rate_limit`, one row per user, upserted on the database's clock), Redis later. A rejected request still counts toward the minute.
- Daily token budget: the unit is tokens (`tokens_in + tokens_out`), summed in `usage_daily` and reset at 00:00 UTC. It is checked before the LLM call against tokens already spent, so the last call can overshoot it, and updated after the call. Failed calls count too: a call that fails after the model replied adds the tokens it used; one with no reply adds a request and no tokens.
- Defaults are set in config. The typed nullable columns `rate_limit_per_minute` and `daily_token_budget` on `app_user` override them for one user.

## 6. Admin dashboard

- **Login**: admin credentials from env, with a session cookie.
- **Users**: status, last activity, requests and tokens for today and the last 7 days. Actions: enable or disable a user, set limits.
- **Invite codes**: create, revoke, see who redeemed them.
- **Overview**: active users, requests and LLM cost per day.

## 7. Data

| Table | Purpose |
| --- | --- |
| `app_user` | `status` (active/disabled), nullable overrides `rate_limit_per_minute` and `daily_token_budget`, `invite_code_id` → `invite_code`. Not `user`: reserved in Postgres |
| `identity` | provider (telegram, later web), `external_id` → `app_user` |
| `invite_code` | `code`, `note`, `max_uses`, `used_count`, `expires_at`, `revoked_at` |
| `context_profile` | audience, tone, channel, glossary |
| `interaction` | input, source language, output, full JSON response (with the lessons), model, prompt version, tokens, cost |
| `usage_daily` | `user_id`, `day`, requests, `tokens_in`, `tokens_out`, cost |
| `rate_limit` | `user_id`, `window_start`, `count`: the current per-minute window counter |

## 8. API (draft)

- `POST /v1/auth/redeem`: request `{code}` (1–64 characters, matched case-insensitively); response `{status}`, `registered` or `already_registered`. Errors: `400 invalid_invite`, `401 unauthorized`, `403 user_disabled`
- `POST /v1/texts/process`: request `{text}` (1–4096 characters, not blank); response
  `{interaction_id, source_language, output_text, explanations}`, where `explanations` are the lessons
- `GET/POST/PATCH /v1/profiles`
- `GET /v1/history`
- `/admin/*`: server-rendered pages

## 9. Deployment

Docker Compose with the services `api`, `bot` (long polling for the MVP), `postgres` and `litellm` (the LLM proxy, with its own database on the same Postgres server), plus `ollama` in dev. Configuration comes from env, with secrets kept separately. Token usage and cost are logged.
