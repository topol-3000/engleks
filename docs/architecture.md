# Engleks: MVP Architecture

## 1. Goal and MVP scope

A trainer for English writing, not a translator. The MVP includes:

- **Improve**: modes `minimal`, `natural` (default) and `polished`. Returns the improved text and up to five short lessons: reusable language patterns drawn from the edits, not an edit log. Since `process_text.v3` every mode uses the same prompt, and lessons are always in English.
- **Translate**: UA/RU → EN with automatic language detection, in the same LLM call as Improve.
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

**Bot**: receives messages, calls the API and renders the reply. Long replies are split into 4096-character messages, with inline buttons. The bot has no database and no business logic.

**API**: the single entry point. Layers: Transport (routes, auth, validation) → Application → Domain.

**Application (use cases)**:

- User: `ProcessText` (Improve/Translate), `ManageProfiles`, `GetHistory`, `RedeemInvite`.
- Admin: `ListUsers`, `SetUserStatus`, `SetUserLimits`, `CreateInvite`, `RevokeInvite`, `UsageStats`.

**LLM Gateway**:

- Task-level ports (`TextRewriter`), not a generic `LLMProvider`, so prompts and schemas stay in infra.
- One adapter per port, calling only the LiteLLM proxy (a Compose service). Provider keys, model choice and routing live in the proxy; it serves a cloud LLM in prod and Ollama in dev, and Ollama is reachable only from the proxy.
- Versioned prompt templates.
- Structured output validated with Pydantic, with one retry on invalid JSON.

**Persistence**: PostgreSQL with SQLAlchemy and Alembic. Raw LLM responses are stored as JSONB.

**Admin UI**: server-rendered pages (Jinja2 + htmx, or SQLAdmin) mounted at `/admin` inside the API.

## 3. Main flow

```mermaid
sequenceDiagram
  participant B as Bot
  participant A as API
  participant L as LLM Gateway
  participant D as PostgreSQL
  B->>A: POST /v1/texts/process {text, mode, profile_id}
  A->>D: auth: identity → user, status, limits
  A->>D: rate limit + daily budget check
  A->>A: detect language, build prompt
  A->>L: generate(schema)
  L-->>A: JSON {source_language, output_text, explanations[]}
  A->>D: save interaction; usage_daily += tokens
  A-->>B: ProcessTextResponse {interaction_id, source_language, mode, output_text, explanations[]}
```

Errors:

- `401`: unknown user, invite required.
- `403`: user disabled.
- `429`: `rate_limit` or `daily_budget`.
- `502`: LLM failure.

## 4. Authentication (option A)

- The bot and the API share a secret, `X-Bot-Token`. The bot also sends `X-Telegram-User-Id`.
- Signup: `/start <code>` or a deep link → `POST /v1/auth/redeem {telegram_id, code}` → a user and an identity are created.
- Auth is isolated in an `AuthContext → user_id` layer, so use cases don't know about Telegram.
- Possible later options: B (per-user JWT) and C (web login with Telegram Login or OIDC, plus account linking).

## 5. Limits

- Rate limit: N requests per minute per user. Counters in Postgres for the MVP, Redis later.
- Daily token budget: checked before the LLM call and updated after it.
- Defaults are set in config. `app_user.limits_override` changes them for one user.

## 6. Admin dashboard

- **Login**: admin credentials from env, with a session cookie.
- **Users**: status, last activity, requests and tokens for today and the last 7 days. Actions: enable or disable a user, set limits.
- **Invite codes**: create, revoke, see who redeemed them.
- **Overview**: active users, requests and LLM cost per day.

## 7. Data

| Table | Purpose |
| --- | --- |
| `app_user` | status (active/disabled), `limits_override`, settings (explanation language, default mode). Not `user`: reserved in Postgres |
| `identity` | provider (telegram, later web), `external_id` → `app_user` |
| `invite_code` | code, `created_by`, `max_uses`, `used_count`, `expires_at`, revoked |
| `context_profile` | audience, tone, channel, glossary |
| `interaction` | input, mode, language, full JSON response, model, prompt version, tokens, cost |
| `correction` | original, replacement, category, explanation, span → interaction. Not written since `process_text.v3`; lessons are in `interaction`'s JSON response |
| `usage_daily` | `user_id`, date, requests, `tokens_in`, `tokens_out`, cost |

## 8. API (draft)

- `POST /v1/auth/redeem`
- `POST /v1/texts/process`
- `GET/POST/PATCH /v1/profiles`
- `GET /v1/history`
- `GET/PATCH /v1/me/settings`
- `/admin/*`: server-rendered pages

## 9. Deployment

Docker Compose with the services `api`, `bot` (long polling for the MVP), `postgres` and `litellm` (the LLM proxy, with its own database on the same Postgres server), plus `ollama` in dev. Configuration comes from env, with secrets kept separately. Token usage and cost are logged.
