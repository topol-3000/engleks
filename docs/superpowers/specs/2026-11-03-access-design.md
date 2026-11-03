# Access: Design

Status: draft for review · Date: 2026-11-03 · Related: [MVP Architecture](../../architecture.md), [Core Slice](2026-11-01-core-slice-design.md), [Bot](2026-11-01-bot-design.md)

## 1. Goal

Close the Core slice's known gap: today any Telegram user who messages the bot gets an account and unlimited LLM calls. Access adds invite-only signup, user status, a per-user rate limit and a daily token budget, plus a CLI to manage them until the Admin dashboard exists. This is sub-project 4 of 5 (Foundation, Core slice, Bot, **Access**, Admin dashboard).

Done means: with `make up`, a stranger who messages the bot is told they need an invite; `make invite` prints a code; `/start <code>` registers them; they are cut off with a clear message after N requests in a minute or after the daily token budget; `make admin a="user disable <id>"` stops them.

## 2. Scope

In scope:

- Invite codes: create, list, revoke, redeem (`POST /v1/auth/redeem`).
- User status (`active` / `disabled`) and per-user limit overrides.
- Rate limit (requests per minute) and daily token budget, enforced in `ProcessText`.
- `usage_daily` accounting, including tokens spent on failed LLM calls.
- A Typer CLI over the admin use cases, wrapped by `make invite` and `make admin`.
- Bot: `/start <code>` and messages for the new error codes.

Out of scope, with the reason:

| Item | Where | Why |
| --- | --- | --- |
| Admin UI, admin login, `invite_code.created_by` | Admin dashboard | The CLI has no admin identity; the dashboard calls the same use cases |
| Cost-based budget | Later | `cost` is `None` under Ollama, so a cost budget would not work in dev; tokens are always reported |
| `Retry-After` header | Later | The bot's messages state when limits reset; no client needs the header yet |
| Rate limit on redeem | Later | ~10¹⁷ possible codes and Telegram's own throttling make guessing impractical; attempts are logged |
| Existing auto-created users | None | The local DB is rebuilt; no backfill or grandfathering |
| Redis counters | Later | Architecture §5: Postgres for the MVP; only the ledger adapter changes |

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Invites before the dashboard | CLI writing through the same use cases | Access ships independently; the dashboard later adds a UI, not new logic |
| Budget unit | Tokens (`tokens_in + tokens_out`) | Reported by every model, including Ollama in dev |
| Limit storage | Postgres counters behind a `UsageLedger` port | Counts every attempt, including failed ones; survives restarts and replicas; a Redis adapter can replace it |
| Where limits are enforced | In the `process_text` use case | Business rules; a later web client gets them for free |
| Limit overrides | Two nullable `INT` columns on `app_user`, not a `limits_override` JSONB | Typed and checked by Postgres and pyright; nothing to parse |
| Redeem identity | `X-Telegram-User-Id` header, not the body | One parser and one place that says who the caller is |
| Already registered on redeem | `200 already_registered`, code not consumed | Tapping a deep link twice is harmless |
| Invalid invite | One `400 invalid_invite` for unknown, expired, revoked and used up | The client cannot tell the cases apart; the log can |
| CLI library | Typer | Chosen by the owner; subcommands and typed options with little code |

## 4. Data model

New migration `0002_access` (`0001` is on `main` and stays unchanged).

| Table | Change | Columns |
| --- | --- | --- |
| `app_user` | add columns | `status TEXT NOT NULL DEFAULT 'active'`; `rate_limit_per_minute INT NULL`; `daily_token_budget INT NULL`; `invite_code_id UUID NULL` FK → `invite_code` |
| `invite_code` | new | `id UUID` PK; `code TEXT UNIQUE`; `note TEXT NULL`; `max_uses INT NOT NULL DEFAULT 1`; `used_count INT NOT NULL DEFAULT 0`; `expires_at TIMESTAMPTZ NULL`; `revoked_at TIMESTAMPTZ NULL`; `created_at` |
| `usage_daily` | new | `user_id` FK; `day DATE` (UTC); `requests INT`; `tokens_in BIGINT`; `tokens_out BIGINT`; `cost NUMERIC(14,8) NULL`; PK `(user_id, day)` |
| `rate_limit` | new | `user_id` PK FK; `window_start TIMESTAMPTZ`; `count INT` |
| `identity`, `interaction` | unchanged | |

- A `NULL` override means "use the config default".
- "Who redeemed this code" is `app_user.invite_code_id`: a user signs up once, with one code.
- `rate_limit` holds one row per user. The upsert sets `count = 1` when `window_start` moved to a new minute, else `count + 1`, so the table never grows.
- `usage_daily.cost` becomes `NULL` once any added cost is unknown, matching how the rewriter sums costs today.

**Codes:** 12 characters from an uppercase alphabet without look-alikes (`ABCDEFGHJKMNPQRSTUVWXYZ23456789`, 31 characters), generated with `secrets`. They fit Telegram deep links (`[A-Za-z0-9_-]`, up to 64). Input is uppercased before lookup. Stored in plaintext so the CLI and dashboard can list them; a code grants signup only and can be revoked.

**Domain** (`domain/access.py`):

| Type | Fields | Notes |
| --- | --- | --- |
| `UserStatus` | `active`, `disabled` | `StrEnum` |
| `Limits` | `requests_per_minute`, `daily_tokens` | Frozen dataclass; effective limits = config defaults with non-`NULL` overrides applied |
| `UserAccess` | `user_id`, `status`, `rate_limit_per_minute: int \| None`, `daily_token_budget: int \| None` | What `IdentityRepository.find_user` returns |
| `Admission` | `requests_this_minute`, `tokens_today` | What `UsageLedger.admit` returns |
| `Invite` | `id`, `code`, `note`, `max_uses`, `used_count`, `expires_at`, `revoked_at`, `created_at` | For create and list |
| `RedeemResult` | `registered`, `already_registered` | `StrEnum` |
| Exceptions | `InviteRequired`, `UserDisabled`, `InvalidInvite(reason)`, `RateLimited`, `BudgetExhausted` | Mapped in `transport/errors.py` |

`AuthContext` gains `limits: Limits`. `RewriteFailed` gains `usage: Usage | None`.

**Config** (`API_` prefix, documented in `.env.example`): `rate_limit_per_minute = 10`, `daily_token_budget = 200000` (~60 calls at ~3k tokens each), `bot_username: str | None = None` (CLI only, to print the deep link).

## 5. Auth and redeem

`authenticate(provider, external_id, identities, defaults)` no longer creates users:

| Case | Result |
| --- | --- |
| Bad or missing `X-Bot-Token` or user-id header | `401 unauthorized` (unchanged, transport) |
| Identity not found | `InviteRequired` → `401 invite_required` |
| User `disabled` | `UserDisabled` → `403 user_disabled` |
| Active | `AuthContext(user_id, limits)` |

`IdentityRepository.get_or_create_user` is replaced by `find_user(provider, external_id) -> UserAccess | None` (one query: identity joined to user).

In `transport/auth.py`, a new `bot_caller` dependency checks the token and parses the Telegram id into a `Caller(provider, external_id)`; `auth_context` builds on it. Redeem uses `bot_caller` alone, since the caller has no account yet. Default `Limits` are built in `create_app()` from `Settings` and stored on `app.state`.

**`POST /v1/auth/redeem`**: body `RedeemInviteRequest {code}` (1–64 characters); response `200 RedeemInviteResponse {status: "registered" | "already_registered"}`.

`redeem_invite(caller, code, identities, invites)`:

1. If the identity exists: `already_registered` if active, `UserDisabled` if disabled. The code is not touched.
2. Otherwise `InviteRepository.redeem(code, provider, external_id)`, in one transaction:
   1. `UPDATE invite_code SET used_count = used_count + 1 WHERE code = :code AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now()) AND used_count < max_uses RETURNING id`. No row: look the code up to name the reason (`unknown`, `expired`, `revoked`, `used_up`) and raise `InvalidInvite(reason)`.
   2. Insert `app_user` (with `invite_code_id`) and `identity` with `ON CONFLICT DO NOTHING` on `(provider, external_id)`.
   3. If the identity insert conflicted (a concurrent redeem by the same user), roll back so the code is not consumed, and return `already_registered`.

Logged: `invite_redeemed` (`user_id`, `invite_code_id`), `invite_rejected` (`reason`). Never the code itself.

**Contract changes:**

- New `ErrorCode`s: `invite_required`, `user_disabled`, `invalid_invite`, `rate_limited`, `daily_budget`. New DTOs `RedeemInviteRequest`, `RedeemInviteResponse`, `RedeemStatus`. All additive.
- Changed meaning, approved by the owner: `POST /v1/texts/process` for an unknown identity returned a result (auto-created user) and now returns `401 invite_required`. The bot is the only client and is updated in the same change.

## 6. Limits

**Port** `UsageLedger` in `domain/ports.py`, adapter `SqlUsageLedger` in `infra/db/repositories.py`:

- `admit(user_id) -> Admission`: one transaction that upserts `rate_limit` with `window_start = date_trunc('minute', now())` and returns the new count, then reads today's `tokens_in + tokens_out` from `usage_daily` (`day = (now() AT TIME ZONE 'UTC')::date`; 0 if no row). The database's clock is the only clock.
- `record(user_id, usage: Usage | None)`: upserts today's `usage_daily` row with `requests + 1` and adds the tokens and cost (0 tokens when `usage` is `None`).

The adapter counts; the use case decides.

**`process_text`:**

1. `admission = await ledger.admit(auth.user_id)`
2. `admission.requests_this_minute > auth.limits.requests_per_minute` → `RateLimited` → `429 rate_limited`
3. `admission.tokens_today >= auth.limits.daily_tokens` → `BudgetExhausted` → `429 daily_budget`
4. `rewriter.rewrite(text)`:
   - success: `ledger.record(user_id, outcome.usage)`, then `interactions.add(...)`;
   - `RewriteFailed` as `exc`: `ledger.record(user_id, exc.usage)`, then re-raise (`502`, unchanged).

**Partial usage on failure** (`ProxyRewriter` already sums usage across attempts):

| Failure | `RewriteFailed.usage` |
| --- | --- |
| Invalid output on both attempts | Both attempts' tokens |
| Attempt 1 invalid, attempt 2 times out or errors | Attempt 1's tokens |
| Attempt 1 times out, connection error, HTTP error, malformed response | `None` |

**Behaviour:**

- A rejected request (`429`) bumps the rate-limit counter but not `usage_daily.requests`; `usage_daily` counts calls that reached the LLM.
- The budget is checked against tokens already spent, so the last admitted call, and each concurrent one, can overshoot by one call. Accepted for a soft daily cap.
- `record` and `interactions.add` are separate transactions, as today; no connection is held across the LLM call. If `add` fails after `record`, the tokens stay counted, which errs toward the budget.
- The rate limit resets each minute, the budget at 00:00 UTC.
- Logged: `rate_limited` and `daily_budget_exhausted` with `user_id`, the count and the limit.

## 7. CLI

`engleks_api/cli.py` is a second composition root next to `main.py`: it builds `Settings()`, an engine and the repositories, then calls the use cases. Typer (a new `engleks-api` runtime dependency) defines the commands; each wraps its async use case in `asyncio.run`. Output is plain text via `typer.echo`; this is the CLI's purpose, so the "never `print`" logging rule does not apply to its output. Users are addressed by Telegram id.

```
engleks-admin invite create [--note TEXT] [--uses N=1] [--days D]   prints the code, plus the t.me deep link if API_BOT_USERNAME is set
engleks-admin invite list                                          code, note, used/max, expires, revoked
engleks-admin invite revoke CODE
engleks-admin user list                                            telegram id, status, overrides, today's requests and tokens
engleks-admin user disable TELEGRAM_ID
engleks-admin user enable TELEGRAM_ID
engleks-admin user set-limits TELEGRAM_ID [--per-minute N] [--daily-tokens N] [--reset]
```

`engleks-admin` is a `[project.scripts]` entry point of `engleks-api`. Make targets run it in the running `api` container, so no Dockerfile or Compose change is needed:

```make
make invite [note="Anna"] [uses=1] [days=7]   # docker compose exec api engleks-admin invite create ...
make admin a="user disable 123456"            # docker compose exec api engleks-admin $(a)
```

**Use cases** (`application/`, one module each): `redeem_invite`, `create_invite`, `list_invites`, `revoke_invite`, `list_users`, `set_user_status`, `set_user_limits`. Ports: `InviteRepository` (`create`, `list`, `revoke`, `redeem`) and `UserAdminRepository` (`list_with_usage`, `set_status`, `set_limits`, by provider and external id). Unknown code or Telegram id in the CLI exits with status 1 and a message.

**Import-linter** (approved): add `engleks_api.cli` to the modules that `infra` must not import and to "Contracts only at the edge", as `main` is today. Contracts only get stricter.

## 8. Bot

- `ApiClient.redeem_invite(telegram_user_id, code) -> RedeemInviteResponse`.
- The `start` handler reads `CommandObject.args`:
  - with a code: call redeem; `registered` → welcome plus help text; `already_registered` → help text; `ApiError` with `invalid_invite` → "That invite code doesn't work. Ask for a new one.";
  - without a code: help text, as today.
- `rendering/errors.py` adds:
  - `invite_required`: "You need an invite to use Engleks. Send /start followed by your code."
  - `user_disabled`: "Your access to Engleks is disabled."
  - `rate_limited`: "Too many messages. Try again in a minute."
  - `daily_budget`: "You've reached today's limit. It resets at 00:00 UTC."

Unknown codes keep falling back to the generic message.

## 9. Testing

**Unit (fakes, no Docker):**

- `authenticate`: unknown identity, disabled user, active user, overrides applied over defaults (each one alone and both).
- `redeem_invite`: new user registered; already registered (active and disabled) leaves the code alone; invalid invite propagates.
- `process_text`: N requests in the window pass and N+1 is rejected; `tokens_today == budget` is rejected and `budget - 1` passes; a rejected request never calls the rewriter or `record`; a failure records partial usage and re-raises; a failure with `usage=None` still records the request; success records usage, then adds the interaction.
- Code generation: length 12, alphabet only.
- `ProxyRewriter`: `RewriteFailed.usage` for each row of the table in §6.

**Integration (Postgres):**

- `SqlInviteRepository.redeem`: expired, revoked, used up and unknown each raise with the right reason; for the last use of a code, of two concurrent redeems by different users exactly one succeeds; a concurrent redeem by the same user consumes the code once.
- `SqlUsageLedger`: counts within a minute; resets after `window_start` is moved back a minute; `record` sums across calls; `cost` turns `NULL` once a cost is unknown.
- HTTP: every new status and error code on `/v1/texts/process` and `/v1/auth/redeem`.
- CLI: each command through `typer.testing.CliRunner` against Postgres.
- `alembic check` passes.

**Bot:** `/start <code>` for each outcome and `/start` alone through `build_dispatcher(...).feed_update`, the API mocked with `MockTransport` and real DTOs; rendering of each new error code.

## 10. Docs

- `docs/architecture.md`: redeem takes the id from the header; typed override columns instead of `limits_override`; `note` instead of `created_by`; the CLI; new error codes in §3.
- `AGENTS.md`: the CLI as a second composition root and where commands go; the `typer.echo` exception to the `print` rule; `UsageLedger` as the pattern for counters; the `bot_caller` dependency for endpoints that run before an account exists; the Core slice's known gap removed.
