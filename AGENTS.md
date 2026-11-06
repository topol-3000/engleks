# AGENTS.md: engleks

A trainer for English writing, not a translator. Users send text to a Telegram bot; the API
corrects it or translates it from UA/RU, and teaches short English lessons drawn from the
edits. Context profiles, history, invite codes, usage limits and an admin dashboard
complete the MVP.

This file is for coding agents. Read before changing anything big:

- [docs/architecture.md](docs/architecture.md): MVP scope, components, main flow, data, API
- [docs/superpowers/specs/2026-11-01-project-structure-design.md](docs/superpowers/specs/2026-11-01-project-structure-design.md):
  layout, tooling, runtime and CI decisions
- [docs/deployment.md](docs/deployment.md): production server, deploys, rollback, backups (runbook)

The MVP is built as sub-projects, each with its own spec: **Foundation** (done), **Core slice**
(`ProcessText` end to end: LLM gateway, prompts, persistence), **Bot**, **Access**
(invites, auth, rate limits, daily budget), **Deployment** (one arm64 server, GHCR images,
automatic deploys from `main`, nightly backups), **Admin dashboard**.

## Commands

| Task | Command |
|---|---|
| Install deps and hooks | `make install` |
| Lint, format check, types, import contracts | `make lint` |
| Autofix | `make fmt` |
| All tests (integration ones need Docker) | `make test` |
| Tests without Docker | `uv run pytest packages/contracts/tests apps/api/tests/unit apps/bot/tests deploy/tests` (`deploy/tests` needs `jq`) |
| Server script tests | `uv run pytest deploy/tests` |
| One test | `uv run pytest apps/api/tests/unit/test_x.py -k name` |
| Tests that call a real LLM | `make test-llm` |
| Postgres for local runs | `make dev-db` |
| Apply migrations | `make migrate` |
| New migration from models | `make revision m="add interaction"`, then review the file |
| Full stack / stop it | `make up` / `make down` |
| Trivy scan | `make scan` |
| Create an invite (stack running) | `make invite [note=..] [uses=..] [days=..]` |
| Any admin command (stack running) | `make admin a="user disable <id>"` |
| Deploy to production | automatic after a green CI run on `main` (Actions → Deploy); Run workflow with no input redeploys `main`'s head, e.g. after a secret change in Infisical |
| Roll back production | Actions → Deploy → Run workflow with an older commit SHA |

Run `make lint && make test` before saying a task is done. Add dependencies with
`uv add --package engleks-api <pkg>` (or `engleks-bot`, `engleks-contracts`); dev tools go
in the root `dev` group with `uv add --dev <pkg>`. Never use pip.

## Layout

```
apps/api/src/engleks_api/
  main.py          create_app(): composition root; infra is wired in only here and in cli.py
  config.py        Settings (API_ prefix)
  cli.py           engleks-admin (Typer): second composition root, wires infra into the admin use cases
  transport/       routers, FastAPI dependencies, error → HTTP mapping
  admin/           admin pages mounted at /admin
  application/     use cases, one module each
  domain/          entities, value objects, ports (Protocol); no I/O
  infra/db/        SQLAlchemy Base, engine, repositories
  infra/llm/       provider adapters, prompts/ (versioned templates)
apps/api/migrations/   Alembic
apps/api/tests/        unit/ (no Docker), integration/ (testcontainers Postgres)
apps/bot/src/engleks_bot/
  main.py          dispatcher, long polling
  config.py        Settings (BOT_ prefix)
  api_client.py    the only way the bot talks to the API
  handlers/        aiogram routers
  rendering/       message splitting (4096 chars), keyboards
packages/contracts/src/engleks_contracts/   wire DTOs, error codes, shared enums
deploy/litellm/        the LiteLLM proxy image, its config and generate_key.py
deploy/server/         production scripts (setup, render-env, deploy, backup) and systemd units
deploy/infisical/      generate-secrets.sh: the secrets engleks generates itself, for Infisical's .env import
deploy/prod.env        production config values (a PR changes them); copied into the server's .env
deploy/secrets.txt     names of the production secrets; the values live in Infisical
deploy/tests/          pytest tests of deploy/ scripts
compose.prod.yaml      production override: GHCR images instead of builds
```

## Dependency rules

```
transport | admin  →  application  →  domain  ←  infra
main.py wires infra adapters into use cases
```

`make lint` checks these with import-linter:

1. No upward imports between the API layers.
2. `infra` imports only `domain`. `application` and `domain` never import `infra`.
3. `domain` and `application` import no frameworks: no fastapi, sqlalchemy, httpx,
   litellm, aiogram.
4. `engleks_api` and `engleks_bot` never import each other.
5. `engleks_contracts` depends only on pydantic. In the API, only `transport` imports it; the
   domain keeps its own types and `transport` maps between them. When an enum (language)
   exists in both, a unit test asserts both have the same values.

Never add an import-linter exception to get green; change the design instead.

## Where does new code go?

| I need... | Put it in |
|---|---|
| a business concept or rule | `domain/`: frozen dataclass, `StrEnum` |
| to talk to something external | port (`Protocol`) in `domain/ports.py`, adapter in `infra/` |
| an operation the API performs | `application/<verb>_<noun>.py`, a function taking ports as arguments (see `check_readiness.py`) |
| an admin command | a use case in `application/`, a Typer command in `cli.py` |
| an HTTP endpoint | `transport/<feature>.py` (router), `include_router` in `create_app()` |
| a shared object (engine, client, adapter) | built in `create_app()`, stored on `app.state`, read by a `Depends` provider in `transport/` |
| a request or response body | `packages/contracts`, used by both the API and the bot |
| an error the client must understand | `ErrorCode` in `packages/contracts`, mapped in `transport/errors.py` |
| a config value | the app's `config.py` + `.env.example`; a secret also gets a Compose secret; a production value goes in `deploy/prod.env` |
| a production secret | a Compose secret, its name in `deploy/secrets.txt`, its value in Infisical before merging |
| a DB table | model on `Base` in `infra/db/` + `make revision m="..."` |
| a repository | port in `domain/ports.py`, adapter in `infra/db/repositories.py` that takes the `async_sessionmaker` and opens one session and transaction per method (see `SqlInteractionRepository`); built in `create_app()` (or `cli.py`), never in `transport` |
| a per-user counter or quota | port like `UsageLedger` (the adapter counts with an upsert and the DB clock; the use case decides), adapter in `infra/db/repositories.py` |
| an LLM-backed task | task-level port in `domain/ports.py` (e.g. `TextRewriter`), adapter in `infra/llm/` that calls only the LiteLLM proxy over `httpx2` and validates the output into an infra Pydantic model (see `ProxyRewriter`) |
| a prompt | `infra/llm/prompts/<name>.v<N>.j2`; a changed prompt is a new version, never an edit |
| an endpoint that needs the caller | `auth: Annotated[AuthContext, Depends(auth_context)]` from `transport/auth.py`; use cases receive `AuthContext`, never headers |
| an endpoint that runs before the caller has an account | `caller: Annotated[Caller, Depends(bot_caller)]` from `transport/auth.py` (see `transport/invites.py`) |
| a shared object in the bot (`ApiClient`) | dispatcher workflow data in `main.build_dispatcher()`, received as a handler argument |
| a bot command | `handlers/<feature>.py` with a `build_router()` factory, included in `main.build_dispatcher()`; layout in `rendering/` |

## Code rules

- Python 3.14: `X | None`, built-in generics, PEP 695 generics, `type` aliases, `typing.Self`.
- Pyright is strict for `domain` and `application`, standard elsewhere. No `Any` in their
  signatures. Silence pyright only with `# pyright: ignore[<rule>]` and a comment giving the reason.
- Async all the way for I/O; no blocking calls in async code.
- Bot messages are built with `aiogram.utils.formatting` and sent as `**text.as_kwargs()` (text + entities); never a parse mode, never escaping.
- No DI framework. `Settings()` is built only in `create_app()`, `cli.py` and the bot's `main()`;
  everything else gets values or adapters passed in.
- Secrets are `SecretStr`, read from `/run/secrets` in containers and from `.env` locally.
- LLM output is always parsed into a Pydantic model; raw LLM text never reaches the domain.
- The user's text goes to the LLM only as the user message, wrapped verbatim in `<text>` tags;
  instructions to the model (including the retry's schema correction) are system messages.
- The API calls LLMs only through the LiteLLM proxy, never a provider or Ollama directly,
  and never depends on the `litellm` package.
- Migrations run in the `migrate` Compose service, never on API startup. A test runs
  `alembic check`, so models and migrations must match. Never edit a migration that is on
  `main`; add a new one.
- Every migration must work with the previous release's code: a rollback restores the old
  images but never downgrades the schema. Add columns nullable or with a default; drop or
  rename only in a later release.

## Logging

- `log = structlog.stdlib.get_logger()` at module level. Never `print`.
  `cli.py` writes its output with `typer.echo`; that is output, not logging, so the `print` rule does not apply.
- Events are snake_case names with key-value fields, not prose:
  `log.warning("readiness_probe_failed", probe=probe.name, error=repr(exc))`.
- Never log secrets, the user's text or the LLM's output. Log ids, lengths and counts instead.
- Every LLM call logs `model`, `prompt_version`, `tokens_in`, `tokens_out` and `cost`.
- Log an error once, where it is handled.

## Testing

- Test the requirement, not the implementation: happy path, edge cases, failures. If a
  requirement is unclear, stop and ask. Every bug fix starts with a failing test.
- Unit tests (`apps/api/tests/unit/`): domain and use cases with small in-memory fakes that
  satisfy the port's Protocol (see `test_check_readiness.py`). No Docker, no network.
- Integration tests (`apps/api/tests/integration/`): fixtures in `conftest.py`.
  `client` for HTTP without a DB, `client_for(create_app(pg_settings))` for HTTP with
  Postgres, `db_session` for a raw session and `sessions` for repositories (a sessionmaker
  whose commits are savepoints; both are rolled back after each test).
  HTTP tests replace the LLM with `app.dependency_overrides[text_rewriter] = lambda: fake`.
  HTTP tests of authenticated endpoints need a registered user: use the `register` fixture,
  e.g. `register(async_sessionmaker(engine, expire_on_commit=False), rate_limit_per_minute=...)`.
  CLI tests use `typer.testing.CliRunner` with `build_cli(pg_settings)` via `asyncio.to_thread`.
- Bot tests mock the API with `httpx2.MockTransport`, building responses from real `engleks_contracts` DTOs. Handler tests use real aiogram objects bound to a `Bot` on the `RecordingSession` fixture (`apps/bot/tests/conftest.py`), never `AsyncMock(spec=Message)`. Bot routing (filters, commands, callbacks, injection, the errors handler) is tested through `build_dispatcher(...).feed_update` on the `RecordingSession` bot.
- Server script tests (`deploy/tests/`) run the bash scripts with the `server` fixture, whose
  `git`, `docker`, `restic` and `curl` are one logging stub (`deploy/tests/conftest.py`);
  assert on `server.calls()` and the fake `.env`. The tests need `jq` installed. The fixture
  runs the real `engleks-render-env` against a fake Infisical: `set_secrets` sets what it
  serves and `inputs()` shows what the stubs received. Scripts take `ENGLEKS_DIR`,
  `BACKUP_DIR`, `ENGLEKS_LOCK_TIMEOUT` and `ENGLEKS_INFISICAL_CREDENTIALS` from the
  environment for this. Every script is shellcheck-clean (`make lint`).
- No test calls a real LLM or Telegram, except ones marked `@pytest.mark.llm`
  (run with `make test-llm`, never in CI).

## Boundaries

- Ask before: adding a runtime dependency, a breaking change to an existing contract DTO or
  `/v1` endpoint (removing or renaming a field, changing its meaning), changing CI, Dockerfiles or `compose.yaml`, changing ruff, pyright or import-linter
  config. `compose.prod.yaml`, `deploy/server/*` and `.github/workflows/deploy.yml` count as
  CI, Dockerfiles and Compose here.
- Never: commit `.env` or secrets, skip or disable tests to get green, weaken lint or
  type settings, ignore a Trivy finding that has a fix.

## Keeping this file current

A change that introduces a new pattern (the first repository, LLM adapter, auth dependency,
...) updates this file in the same commit. When a topic outgrows a few bullets here, move it
to `docs/conventions/<topic>.md` and leave a one-line link.
