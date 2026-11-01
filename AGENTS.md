# AGENTS.md: engleks

A trainer for English writing, not a translator. Users send text to a Telegram bot; the API
improves it (modes `minimal`, `natural`, `polished`) or translates it from UA/RU, and explains
every change. Context profiles, history, invite codes, usage limits and an admin dashboard
complete the MVP.

This file is for coding agents. Read before changing anything big:

- [docs/architecture.md](docs/architecture.md): MVP scope, components, main flow, data, API
- [docs/superpowers/specs/2026-11-01-project-structure-design.md](docs/superpowers/specs/2026-11-01-project-structure-design.md):
  layout, tooling, runtime and CI decisions

The MVP is built as sub-projects, each with its own spec: **Foundation** (done), **Core slice**
(`ProcessText` end to end: LLM gateway, prompts, diff engine, persistence), **Bot**, **Access**
(invites, auth, rate limits, daily budget), **Admin dashboard**.

## Commands

| Task | Command |
|---|---|
| Install deps and hooks | `make install` |
| Lint, format check, types, import contracts | `make lint` |
| Autofix | `make fmt` |
| All tests (integration ones need Docker) | `make test` |
| Tests without Docker | `uv run pytest packages/contracts/tests apps/api/tests/unit apps/bot/tests` |
| One test | `uv run pytest apps/api/tests/unit/test_x.py -k name` |
| Tests that call a real LLM | `make test-llm` |
| Postgres for local runs | `make dev-db` |
| Apply migrations | `make migrate` |
| New migration from models | `make revision m="add interaction"`, then review the file |
| Full stack / stop it | `make up` / `make down` |
| Trivy scan | `make scan` |

Run `make lint && make test` before saying a task is done. Add dependencies with
`uv add --package engleks-api <pkg>` (or `engleks-bot`, `engleks-contracts`); dev tools go
in the root `dev` group with `uv add --dev <pkg>`. Never use pip.

## Layout

```
apps/api/src/engleks_api/
  main.py          create_app(): composition root, the only place infra is wired in
  config.py        Settings (API_ prefix)
  transport/       routers, FastAPI dependencies, error → HTTP mapping
  admin/           admin pages mounted at /admin
  application/     use cases, one module each
  domain/          entities, value objects, ports (Protocol), diff engine; no I/O
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
   domain keeps its own types and `transport` maps between them. When an enum (mode,
   category) exists in both, a unit test asserts both have the same values.

Never add an import-linter exception to get green; change the design instead.

## Where does new code go?

| I need... | Put it in |
|---|---|
| a business concept or rule | `domain/`: frozen dataclass, `StrEnum` |
| to talk to something external | port (`Protocol`) in `domain/ports.py`, adapter in `infra/` |
| an operation the API performs | `application/<verb>_<noun>.py`, a function taking ports as arguments (see `check_readiness.py`) |
| an HTTP endpoint | `transport/<feature>.py` (router), `include_router` in `create_app()` |
| a shared object (engine, client, adapter) | built in `create_app()`, stored on `app.state`, read by a `Depends` provider in `transport/` |
| a request or response body | `packages/contracts`, used by both the API and the bot |
| an error the client must understand | `ErrorCode` in `packages/contracts`, mapped in `transport/errors.py` |
| a config value | the app's `config.py` + `.env.example`; a secret also gets a Compose secret |
| a DB table | model on `Base` in `infra/db/` + `make revision m="..."` |
| a prompt | `infra/llm/prompts/<name>.v<N>.j2`; a changed prompt is a new version, never an edit |
| a bot command or button | `handlers/<feature>.py` router, included in the bot's `main.py`; layout in `rendering/` |

## Code rules

- Python 3.14: `X | None`, built-in generics, PEP 695 generics, `type` aliases, `typing.Self`.
- Pyright is strict for `domain` and `application`, standard elsewhere. No `Any` in their
  signatures. Silence pyright only with `# pyright: ignore[<rule>]` and a comment giving the reason.
- Async all the way for I/O; no blocking calls in async code.
- No DI framework. `Settings()` is built only in `create_app()` and the bot's `main()`;
  everything else gets values or adapters passed in.
- Secrets are `SecretStr`, read from `/run/secrets` in containers and from `.env` locally.
- LLM output is always parsed into a Pydantic model; raw LLM text never reaches the domain.
- Migrations run in the `migrate` Compose service, never on API startup. A test runs
  `alembic check`, so models and migrations must match. Never edit a migration that is on
  `main`; add a new one.

## Logging

- `log = structlog.stdlib.get_logger()` at module level. Never `print`.
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
  Postgres, `db_session` for repositories (rolled back after each test).
- Bot tests mock the API with `httpx2.MockTransport`, building responses from real `engleks_contracts` DTOs.
- No test calls a real LLM or Telegram, except ones marked `@pytest.mark.llm`
  (run with `make test-llm`, never in CI).

## Boundaries

- Ask before: adding a runtime dependency, a breaking change to an existing contract DTO or
  `/v1` endpoint (removing or renaming a field, changing its meaning), changing CI, Dockerfiles or `compose.yaml`, changing ruff, pyright or import-linter
  config.
- Never: commit `.env` or secrets, skip or disable tests to get green, weaken lint or
  type settings, ignore a Trivy finding that has a fix.

## Keeping this file current

A change that introduces a new pattern (the first repository, LLM adapter, auth dependency,
...) updates this file in the same commit. When a topic outgrows a few bullets here, move it
to `docs/conventions/<topic>.md` and leave a one-line link.
