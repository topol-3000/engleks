# Project Structure and Tooling: Design

Status: draft for review · Date: 2026-11-01 · Related: [MVP Architecture](../../architecture.md)

## 1. Goal

Set up the repository skeleton that every MVP feature will be built on: layout, package boundaries, tooling, local runtime and CI. Feature work (`ProcessText`, auth and limits, admin) is out of scope and gets its own specs.

The architecture document splits the MVP into these sub-projects, in build order:

1. **Foundation** (this spec): layout, tooling, Compose, config, migrations, tests, CI.
2. **Core slice**: `ProcessText` end to end (LLM gateway, prompts, diff engine, persistence).
3. **Bot**: aiogram client of the API.
4. **Access**: invites, identity, auth, rate limits, daily budget.
5. **Admin dashboard.**

## 2. Decisions

| Area | Decision |
| --- | --- |
| Repo shape | One repo, uv workspace with three members: `apps/api`, `apps/bot`, `packages/contracts` |
| API internals | Layer-first: `transport`, `application`, `domain`, `infra`, `admin` |
| Python | 3.14 |
| Base image | `python:3.14-slim-trixie` |
| Packages | uv, one `uv.lock` at the root |
| Lint and format | ruff |
| Types | Pyright: strict for `domain` and `application`, standard elsewhere |
| Boundaries | import-linter |
| Tests | pytest, pytest-asyncio, testcontainers (Postgres), respx |
| DB | PostgreSQL 18, SQLAlchemy 2.0 async with asyncpg, Alembic |
| Config | pydantic-settings, secrets from `/run/secrets` |
| Logging | structlog (JSON in containers, console locally) |
| Task runner | Makefile |
| Pre-commit | ruff only |
| CI | GitHub Actions, including Trivy filesystem and image scans |

## 3. Repository layout

```
engleks/
  pyproject.toml            # workspace root: members, dev deps, ruff/pyright/import-linter config
  uv.lock
  Makefile
  compose.yaml              # postgres, migrate, api, bot, ollama (dev profile)
  .env.example
  .pre-commit-config.yaml
  .github/workflows/ci.yml
  docs/
  apps/
    api/
      pyproject.toml
      Dockerfile
      alembic.ini
      migrations/
      src/engleks_api/
        main.py             # app factory and composition root, mounts /v1 and /admin
        config.py           # pydantic-settings
        transport/          # routers, auth dependencies, error → HTTP mapping
        application/        # use cases, one module each
        domain/             # entities, value objects, ports, diff engine
        infra/
          db/               # SQLAlchemy models, repositories, session
          llm/              # provider adapters, prompts/ (versioned templates)
        admin/              # admin routes and templates/
      tests/
        unit/
        integration/
    bot/
      pyproject.toml
      Dockerfile
      src/engleks_bot/
        main.py             # aiogram dispatcher, long polling
        config.py
        api_client.py       # httpx client, sends X-Bot-Token and X-Telegram-User-Id
        handlers/
        rendering/          # message splitting (4096 chars), inline keyboards
      tests/
  packages/
    contracts/
      pyproject.toml
      src/engleks_contracts/  # request/response DTOs, error codes, enums (mode, category)
```

Notes:

- `src/` layout everywhere, so tests run against the installed package.
- The diff engine lives in `domain/`: it is pure `difflib` computation with no I/O.
- Prompt templates live in `infra/llm/prompts/` with the version in the file name (`improve.v1.j2`). The version is stored on each `interaction`.
- Each app's tests stay inside the app, so bot tests never install API dependencies.

## 4. Dependency rules

```
engleks_api:
  transport ─┐
  admin ─────┼─▶ application ─▶ domain ◀── infra
  main.py (composition root) wires infra adapters into application use cases
```

Enforced by import-linter in CI:

1. **API layers**: `transport | admin` → `application` → `domain`. No upward imports.
2. **Infra depends only on domain**: infra implements domain ports (`LLMProvider`, `UserRepository`, `InteractionRepository`, `UsageCounter`, and so on). `application` never imports `infra`; only `main.py` connects them.
3. **Pure core**: `domain` and `application` do not import `fastapi`, `sqlalchemy`, `httpx`, `litellm` or `aiogram`.
4. **App isolation**: `engleks_bot` and `engleks_api` do not import each other. `engleks_contracts` imports neither and depends only on pydantic.
5. **Contracts at the edge**: only `engleks_api.transport` and `engleks_bot` import `engleks_contracts`. The domain keeps its own types and transport maps between them.

Rule 5 duplicates a few small definitions, such as the `mode` and `category` enums. This is deliberate: it keeps the public API and the domain independent. A unit test asserts that each duplicated enum has the same values in both places.

Wiring uses no DI framework. `main.py` builds adapters from settings, and FastAPI dependencies pass them into use cases per request.

## 5. Runtime and configuration

### Config

- Each app has its own `Settings` class, with env prefixes `API_` and `BOT_`, so one `.env` serves both.
- Secrets are read from `/run/secrets/*` (pydantic-settings `secrets_dir`), with env vars as a local fallback: the shared bot↔API token, the Telegram bot token, the DB password, the LLM API key, the admin password and the session secret.
- `.env.example` lists every key with dummy values. A missing required setting fails startup.

### Compose services

| Service | Image | Notes |
| --- | --- | --- |
| `postgres` | `postgres:18` (pinned) | named volume, healthcheck |
| `migrate` | api image, runs `alembic upgrade head` | one-off, starts when postgres is healthy |
| `api` | api image, uvicorn | starts after `migrate` completes successfully; `/healthz` (liveness), `/readyz` (DB ping) |
| `bot` | bot image, long polling | starts when `api` is healthy |
| `ollama` | `ollama/ollama` | `dev` profile only |

Migrations run as a separate service, never on api startup, so replicas cannot race and a failed migration blocks the deploy.

### Dockerfiles

- Build context is the repo root (uv needs the workspace lockfile, and the bot needs `packages/contracts`).
- Multi-stage: a builder stage with uv installs the app and its workspace dependencies into a virtualenv; the final stage is `python:3.14-slim-trixie` with only that virtualenv and the app.
- Runs as a non-root user. No uv in the final image.

### Local development

- `make dev-db`: start Postgres only, then run the apps with `uv run` and autoreload.
- `make up`: the full stack in containers, as in production.
- Ollama is opt-in with `--profile dev`.

### Logging

structlog, JSON output in containers and console output locally. Every request log line carries `request_id`. LLM calls log `model`, `prompt_version`, `tokens_in`, `tokens_out` and `cost`.

## 6. Testing

| Level | What it covers | How |
| --- | --- | --- |
| Unit (api) | domain, application use cases | in-memory fakes for every port; no Docker |
| Integration (api) | repositories, migrations, HTTP routes | testcontainers Postgres per session, transaction rollback per test, `httpx.ASGITransport` with a fake `LLMProvider` |
| Migrations | schema matches models | `alembic upgrade head` on an empty DB, then `alembic check` |
| Bot | message splitting, keyboards, API client | unit tests; API mocked with `respx` using real `contracts` DTOs |
| LLM (opt-in) | real provider calls | `@pytest.mark.llm`, excluded by default, run with `make test-llm` |

No test in CI calls a real LLM or Telegram.

## 7. Makefile targets

| Target | Action |
| --- | --- |
| `make install` | `uv sync --all-packages` and install pre-commit hooks |
| `make lint` | ruff check, ruff format --check, pyright, lint-imports |
| `make fmt` | ruff format and ruff check --fix |
| `make test` | pytest, excluding `llm` |
| `make test-llm` | pytest, `llm` marker only |
| `make dev-db` | start Postgres only |
| `make up` / `make down` | start or stop the full stack |
| `make migrate` | `alembic upgrade head` against the configured DB |
| `make revision m="..."` | `alembic revision --autogenerate -m "..."` |
| `make scan` | Trivy filesystem scan locally |

## 8. CI

GitHub Actions on pull requests and pushes to `main`. Jobs run in parallel:

1. `lint`: ruff check and ruff format --check.
2. `types`: pyright.
3. `imports`: lint-imports.
4. `test`: pytest, unit and integration (testcontainers uses the runner's Docker).
5. `trivy-fs`: filesystem scan for dependency vulnerabilities (`uv.lock`), Dockerfile and Compose misconfiguration, and secrets.
6. `images`: build the `api` and `bot` images (no push), then a Trivy image scan of each.

Trivy fails on HIGH or CRITICAL findings that have a fix available. Findings without a fix are reported but do not fail the build.

The uv cache is keyed on `uv.lock`. Third-party actions, including Trivy's, are pinned to commit SHAs, not tags.

## 9. Definition of done

- `make up` starts postgres, migrate, api and bot, and `/readyz` returns 200.
- The api serves `/healthz` and `/readyz`. One empty initial Alembic migration exists.
- The bot starts, replies to `/start` with a placeholder, and calls the API health endpoint through `api_client` once at startup.
- `packages/contracts` contains an `ErrorCode` enum and a health response DTO, used by both the api and the bot.
- `make lint` and `make test` pass locally, and every CI job passes, including the import contracts and both Trivy scans.

## 10. Out of scope

- `ProcessText`, the LLM gateway implementation, prompts and the diff engine.
- Database tables beyond the empty initial migration.
- Auth, invites, limits, profiles, history, settings and the admin dashboard.
- Image registry, deployment target and production secrets management.
