# Project Structure and Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the repository skeleton (uv workspace, API and bot apps, contracts package, tooling, Compose, migrations, tests, CI) that every MVP feature will be built on.

**Architecture:** One uv workspace with three members: `apps/api` (FastAPI, layer-first: transport/application/domain/infra/admin), `apps/bot` (aiogram, thin HTTP client) and `packages/contracts` (pydantic DTOs shared at the edge). Layer and package boundaries are enforced by import-linter. Everything runs in Docker Compose with migrations as a separate one-off service.

**Tech Stack:** Python 3.14, uv, ruff, Pyright, import-linter, pytest + pytest-asyncio + testcontainers + respx, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, pydantic-settings, structlog, aiogram 3, httpx, PostgreSQL 18, GitHub Actions, Trivy.

**Spec:** [docs/superpowers/specs/2026-11-01-project-structure-design.md](../specs/2026-11-01-project-structure-design.md) (with [docs/architecture.md](../../architecture.md) for context).

## Global Constraints

- Python `3.14` everywhere (`.python-version`, `requires-python = ">=3.14"`, ruff `target-version = "py314"`, Pyright `pythonVersion = "3.14"`).
- Base image `python:3.14-slim-trixie`; Postgres image `postgres:18` pinned to an exact patch tag plus digest; the same pin is used in `compose.yaml` and in the testcontainers fixture.
- One `uv.lock` at the root. Distribution names: `engleks-api`, `engleks-bot`, `engleks-contracts`; import names: `engleks_api`, `engleks_bot`, `engleks_contracts`. `src/` layout everywhere.
- `engleks-contracts` depends only on `pydantic`.
- Env prefixes `API_` and `BOT_`; one `.env` serves both apps. Secrets come from `/run/secrets` (pydantic-settings `secrets_dir`), env vars as fallback. A missing required setting fails startup.
- Settings this plan adds are only those the foundation code reads (`API_DB_*`, `API_LOG_*`, `BOT_*`). The LLM API key, admin password and session secret are added by the specs that consume them (Core slice, Admin).
- Logging: structlog, JSON in containers, console locally. Every request log line carries `request_id`.
- No DI framework: `main.py` is the only place that connects infra adapters to application code.
- No test in CI calls a real LLM or Telegram. The `llm` pytest marker is excluded by default.
- Trivy fails on HIGH or CRITICAL with a fix available; unfixed findings are reported, not failing. Third-party actions are pinned to commit SHAs.
- Work on branch `feat/foundation`, not `main`.

**Local prerequisites:** `uv` and a running Docker. Trivy is not installed locally; it runs from the pinned `aquasec/trivy` image (see Task 10).

## Review Focus

1. **Database down or hanging**: `/readyz` must return 503 with `{"status": "unavailable"}` within about 2 s, not hang or return 500. Pinned in Task 4 (unreachable port, slow probe).
2. **Telegram text with emoji or trailing whitespace**: Telegram counts the 4096 limit in UTF-16 code units and rejects whitespace-only messages. No chunk may exceed 4096 UTF-16 units and no chunk may be whitespace-only. Pinned in Task 6.
3. **API error bodies the bot doesn't expect**: an HTML 502 from a proxy, FastAPI's default 404 body, or an `ErrorCode` value added by a newer API must produce `ApiError(code=None)`, not a crash. Pinned in Task 6.
4. **Hostile `X-Request-ID`**: an oversized value or one with characters outside `[A-Za-z0-9._-]` must be replaced with a generated id, so it never reaches logs verbatim. Pinned in Task 3.
5. **Running locally without `/run/secrets`**: no pydantic-settings warning, and a missing `API_DB_PASSWORD` fails with a validation error naming `db_password`. Pinned in Task 3.

---

## File Map

```text
pyproject.toml                       root: workspace, dev group, ruff/pyright/pytest/import-linter config   (T1, T8)
.python-version  .gitignore  .pre-commit-config.yaml  Makefile                                            (T1, later tasks add targets)
.dockerignore  compose.yaml  .env.example                                                                  (T9)
.github/workflows/ci.yml                                                                                    (T10)
packages/contracts/src/engleks_contracts/{__init__,errors,health}.py   tests/test_contracts.py             (T2)
apps/api/src/engleks_api/
  main.py                create_app, composition root                                                       (T3, T4)
  config.py              Settings                                                                           (T3)
  logging_setup.py       configure_logging                                                                  (T3)
  transport/{middleware,errors,health}.py                                                                   (T3, T4)
  application/check_readiness.py                                                                            (T4)
  domain/ports.py        ReadinessProbe                                                                     (T4)
  infra/db/{engine,probe,base}.py   infra/llm/__init__.py (empty)   admin/__init__.py (empty)               (T4, T5)
apps/api/{alembic.ini, migrations/, Dockerfile}                                                             (T5, T9)
apps/api/tests/unit/  apps/api/tests/integration/                                                           (T3–T5)
apps/bot/src/engleks_bot/{config,logging_setup,api_client,main}.py  rendering/split.py  handlers/start.py   (T6, T7)
apps/bot/{Dockerfile, tests/}                                                                               (T6, T7, T9)
```

---

### Task 1: Workspace skeleton and tooling

**Files:**

- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.pre-commit-config.yaml`, `Makefile`
- Create: `apps/api/pyproject.toml`, `apps/api/src/engleks_api/__init__.py`
- Create: `apps/bot/pyproject.toml`, `apps/bot/src/engleks_bot/__init__.py`
- Create: `packages/contracts/pyproject.toml`, `packages/contracts/src/engleks_contracts/__init__.py`, `packages/contracts/src/engleks_contracts/py.typed`

**Interfaces:**

- Produces: an installable workspace; `make install`, `make lint`, `make fmt`, `make test`, `make test-llm`; pytest config used by every later task (`asyncio_mode = "auto"`, `--import-mode=importlib`, no `__init__.py` in test dirs).

- [ ] **Step 1: Create the branch**

```bash
git switch -c feat/foundation
```

- [ ] **Step 2: Write the root `pyproject.toml`**

Virtual root (`[tool.uv] package = false`), `requires-python = ">=3.14"`, workspace members `["apps/*", "packages/*"]`. `[dependency-groups] dev`: `ruff`, `pyright`, `import-linter`, `pytest`, `pytest-asyncio`, `testcontainers[postgres]`, `respx`, `httpx`, `pre-commit`. Tool config:

```toml
[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "ASYNC", "RUF"]

[tool.pyright]
pythonVersion = "3.14"
typeCheckingMode = "standard"
strict = ["apps/api/src/engleks_api/domain", "apps/api/src/engleks_api/application"]
include = ["apps", "packages"]
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
testpaths = ["packages/contracts/tests", "apps/api/tests", "apps/bot/tests"]
addopts = "--import-mode=importlib -m 'not llm'"
asyncio_mode = "auto"
markers = ["llm: calls a real LLM provider; excluded by default"]
```

- [ ] **Step 3: Write the three member `pyproject.toml` files**

Build backend `uv_build`. Dependencies (lower bounds only; uv resolves):

- `engleks-contracts`: `pydantic>=2.11`.
- `engleks-api`: `engleks-contracts`, `fastapi>=0.115`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic`, `pydantic-settings>=2.6`, `structlog`. `[tool.uv.sources] engleks-contracts = { workspace = true }`.
- `engleks-bot`: `engleks-contracts`, `aiogram>=3`, `httpx`, `pydantic-settings>=2.6`, `structlog`; script `engleks-bot = "engleks_bot.main:main"`; same workspace source.

Package `__init__.py` files are empty.

- [ ] **Step 4: Write `.python-version` (`3.14`), `.gitignore` (`.venv/`, `__pycache__/`, `.env`, `.pytest_cache/`, `.ruff_cache/`), and `.pre-commit-config.yaml`**

Pre-commit: `astral-sh/ruff-pre-commit` with hooks `ruff-check` (`args: [--fix]`) and `ruff-format`; `rev` equals the ruff version resolved in `uv.lock`.

- [ ] **Step 5: Write the `Makefile`** (recipes are tab-indented)

```make
.PHONY: install lint fmt test test-llm
install:
	uv sync --all-packages
	uv run pre-commit install
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
fmt:
	uv run ruff format .
	uv run ruff check --fix .
test:
	uv run pytest
test-llm:
	uv run pytest -m llm || [ $$? -eq 5 ]
```

`test-llm` treats exit code 5 (no tests collected) as success until the first `llm` test exists.

- [ ] **Step 6: Verify**

Run: `make install && make lint && uv run python -c "import engleks_api, engleks_bot, engleks_contracts"`
Expected: `uv.lock` created, lint passes with 0 errors, import succeeds.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "build: add uv workspace skeleton and tooling"
```

---

### Task 2: Contracts package

**Files:**

- Create: `packages/contracts/src/engleks_contracts/errors.py`, `health.py`; re-export from `__init__.py`
- Test: `packages/contracts/tests/test_contracts.py`

**Interfaces:**

- Produces (all importable from `engleks_contracts`):
  - `class ErrorCode(StrEnum)`: `INTERNAL_ERROR = "internal_error"`, `VALIDATION_ERROR = "validation_error"`. Feature specs add the codes from architecture §3 (`invite_required`, `user_disabled`, `rate_limit`, `daily_budget`, `llm_failure`) when they implement them.
  - `class ErrorResponse(BaseModel)`: `code: ErrorCode`, `message: str`.
  - `class HealthStatus(StrEnum)`: `OK = "ok"`, `UNAVAILABLE = "unavailable"`.
  - `class HealthResponse(BaseModel)`: `status: HealthStatus`.
  - All models `frozen=True`; unknown extra fields ignored (pydantic default), so an older bot can parse a newer API.

- [ ] **Step 1: Write the failing tests**

```python
from engleks_contracts import ErrorCode, ErrorResponse, HealthResponse, HealthStatus

def test_health_response_round_trip():
    body = HealthResponse(status=HealthStatus.OK).model_dump(mode="json")
    assert body == {"status": "ok"}
    assert HealthResponse.model_validate(body).status is HealthStatus.OK

def test_error_response_serializes_code_as_string():
    body = ErrorResponse(code=ErrorCode.INTERNAL_ERROR, message="x").model_dump(mode="json")
    assert body == {"code": "internal_error", "message": "x"}

def test_unknown_fields_are_ignored():
    assert HealthResponse.model_validate({"status": "ok", "extra": 1}).status is HealthStatus.OK
```

- [ ] **Step 2: Run** `uv run pytest packages/contracts -v` — Expected: FAIL with `ImportError`.
- [ ] **Step 3: Implement** the four types above.
- [ ] **Step 4: Run** `uv run pytest packages/contracts -v && make lint` — Expected: 3 passed, lint clean.
- [ ] **Step 5: Commit** `git commit -m "feat(contracts): add error and health DTOs"`

---

### Task 3: API settings, logging, app factory and `/healthz`

**Files:**

- Create: `apps/api/src/engleks_api/config.py`, `logging_setup.py`, `main.py`
- Create: `apps/api/src/engleks_api/transport/__init__.py`, `middleware.py`, `errors.py`, `health.py`
- Create (empty packages, so the layout and import contracts exist): `application/__init__.py`, `domain/__init__.py`, `infra/__init__.py`, `infra/db/__init__.py`, `infra/llm/__init__.py`, `admin/__init__.py`
- Test: `apps/api/tests/unit/test_config.py`, `apps/api/tests/integration/conftest.py`, `apps/api/tests/integration/test_http.py`

**Interfaces:**

- Consumes: `ErrorCode`, `ErrorResponse`, `HealthResponse`, `HealthStatus` (Task 2).
- Produces:
  - `class Settings(BaseSettings)` in `config.py`: `env_prefix="API_"`, `env_file=".env"`, `extra="ignore"`, `secrets_dir="/run/secrets"` **only if that directory exists, else `None`** (avoids pydantic-settings' missing-dir warning). Fields: `db_host: str = "localhost"`, `db_port: int = 5432`, `db_name: str = "engleks"`, `db_user: str = "engleks"`, `db_password: SecretStr` (required), `log_format: Literal["json", "console"] = "json"`, `log_level: str = "INFO"`. Method `database_url(self) -> str` builds `postgresql+asyncpg://…` with `sqlalchemy.URL.create(...).render_as_string(hide_password=False)`.
  - `configure_logging(log_format: Literal["json", "console"], log_level: str) -> None` in `logging_setup.py`: structlog with `merge_contextvars`, ISO timestamps, `JSONRenderer` or `ConsoleRenderer`.
  - `RequestIdMiddleware` (pure ASGI) in `transport/middleware.py`: reads `X-Request-ID`; reuses it only if it matches `^[A-Za-z0-9._-]{1,128}$`, else `uuid4().hex`; binds `request_id` into structlog contextvars for the request; sets the response header `X-Request-ID`; logs one `request` line with method, path, status, duration_ms.
  - `register_error_handlers(app: FastAPI) -> None` in `transport/errors.py`: `Exception` → 500 `ErrorResponse(INTERNAL_ERROR, "Internal server error")` (logs the exception, never echoes it); `RequestValidationError` → 422 `ErrorResponse(VALIDATION_ERROR, "Invalid request")`.
  - `router` in `transport/health.py` with `GET /healthz` → 200 `HealthResponse(status=OK)`.
  - `create_app(settings: Settings | None = None) -> FastAPI` in `main.py` (`settings or Settings()`): configures logging, adds middleware, error handlers and the health router. Uvicorn runs it with `--factory`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_config.py`:

```python
def test_database_url_escapes_password():
    s = Settings(db_password=SecretStr("p@ss:/w"), db_host="db", _env_file=None)
    url = make_url(s.database_url())
    assert (url.drivername, url.host, url.password) == ("postgresql+asyncpg", "db", "p@ss:/w")

@pytest.mark.filterwarnings("error")
def test_missing_db_password_fails(monkeypatch):
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="db_password"):
        Settings(_env_file=None)

def test_reads_secret_file(tmp_path, monkeypatch):
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    (tmp_path / "api_db_password").write_text("from-file")
    s = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert s.db_password.get_secret_value() == "from-file"
```

`tests/integration/conftest.py`: fixture `app` = `create_app(Settings(db_password=SecretStr("unused"), _env_file=None))`; fixture `client` = `httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")`.

`tests/integration/test_http.py`:

```python
async def test_healthz_ok(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert HealthResponse.model_validate(r.json()).status is HealthStatus.OK

async def test_request_id_generated(client):
    r = await client.get("/healthz")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])

async def test_request_id_propagated(client):
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"

@pytest.mark.parametrize("bad", ["x" * 129, "bad id!", "a;b"])
async def test_invalid_request_id_replaced(client, bad):
    r = await client.get("/healthz", headers={"X-Request-ID": bad})
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])

async def test_unhandled_error_maps_to_internal_error(app, client):
    async def boom() -> None:
        raise RuntimeError("secret detail")
    app.add_api_route("/boom", boom)
    r = await client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"code": "internal_error", "message": "Internal server error"}

async def test_validation_error_maps(app, client):
    async def needs_int(n: int) -> None: ...
    app.add_api_route("/needs-int", needs_int)
    r = await client.get("/needs-int", params={"n": "abc"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
```

- [ ] **Step 2: Run** `uv run pytest apps/api -v` — Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest apps/api -v && make lint` — Expected: all pass, lint clean.
- [ ] **Step 5: Commit** `git commit -m "feat(api): add settings, logging, request ids, error mapping and /healthz"`

---

### Task 4: Readiness: port, use case, DB probe and `/readyz`

**Files:**

- Create: `apps/api/src/engleks_api/domain/ports.py`, `application/check_readiness.py`, `infra/db/engine.py`, `infra/db/probe.py`
- Modify: `transport/health.py` (add `/readyz`), `main.py` (wire engine and probe)
- Modify: `apps/api/tests/integration/conftest.py` (Postgres container fixtures)
- Test: `apps/api/tests/unit/test_check_readiness.py`, `apps/api/tests/integration/test_readyz.py`

**Interfaces:**

- Consumes: `Settings`, `create_app` (Task 3); `HealthResponse`, `HealthStatus` (Task 2).
- Produces:
  - `class ReadinessProbe(Protocol)` in `domain/ports.py`: attribute `name: str`; `async def check(self) -> None` (raises on failure).
  - `async def check_readiness(probes: Sequence[ReadinessProbe], timeout: float = 2.0) -> bool` in `application/check_readiness.py`: runs probes concurrently, each under `asyncio.timeout(timeout)`; any exception or timeout → logs `readiness_probe_failed` with `probe=name` and returns `False`; no probes → `True`.
  - `build_engine(url: str) -> AsyncEngine` in `infra/db/engine.py`: `pool_pre_ping=True`, `connect_args={"timeout": 2}`. Takes a URL string, so infra never imports `config`.
  - `class DatabaseProbe` in `infra/db/probe.py`: `__init__(self, engine: AsyncEngine)`, `name = "database"`, `check` runs `SELECT 1`.
  - `GET /readyz`: 200 `HealthResponse(OK)` or 503 `HealthResponse(UNAVAILABLE)`. Probes come from `request.app.state.readiness_probes` through a FastAPI dependency.
  - `create_app` builds the engine eagerly (engine creation does not connect, so `ASGITransport` tests work without running the lifespan), stores `[DatabaseProbe(engine)]` on `app.state.readiness_probes`, and disposes the engine in the lifespan shutdown.
  - Integration fixtures: `POSTGRES_IMAGE` constant (same pin as Task 9's compose; resolve the latest `postgres:18.x-trixie` tag and digest with `docker buildx imagetools inspect`); session fixture `postgres` (a started `PostgresContainer(POSTGRES_IMAGE, driver="asyncpg")`); session fixture `postgres_url` → `postgres.get_connection_url()` (async URL); fixture `pg_settings` → `Settings` built from the container host, mapped port, user, password and dbname, `_env_file=None`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_check_readiness.py` with small fake probe classes:

```python
async def test_all_probes_ok():
    assert await check_readiness([OkProbe(), OkProbe()]) is True

async def test_no_probes_is_ready():
    assert await check_readiness([]) is True

async def test_failing_probe_not_ready():
    assert await check_readiness([OkProbe(), FailingProbe()]) is False

async def test_slow_probe_times_out():
    start = time.monotonic()
    assert await check_readiness([SlowProbe(seconds=10)], timeout=0.05) is False
    assert time.monotonic() - start < 1
```

`tests/integration/test_readyz.py`:

```python
async def test_readyz_ok(pg_settings):
    async with client_for(create_app(pg_settings)) as c:
        r = await c.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}

async def test_readyz_db_unreachable():
    settings = Settings(db_password=SecretStr("x"), db_port=free_port(), _env_file=None)
    start = time.monotonic()
    async with client_for(create_app(settings)) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert time.monotonic() - start < 5
```

`client_for(app)` is a fixture returning an async-context factory around the same `ASGITransport` client as Task 3. `free_port()` binds a socket to port 0, reads the port and closes it.

- [ ] **Step 2: Run** `uv run pytest apps/api -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest apps/api -v && make lint` — Expected: all pass (Docker must be running); Pyright strict is clean on `domain` and `application`.
- [ ] **Step 5: Commit** `git commit -m "feat(api): add readiness probe and /readyz"`

---

### Task 5: Alembic, initial migration and DB test fixtures

**Files:**

- Create: `apps/api/src/engleks_api/infra/db/base.py`
- Create: `apps/api/alembic.ini`, `apps/api/migrations/env.py`, `apps/api/migrations/script.py.mako`, `apps/api/migrations/versions/0001_initial.py`
- Modify: `apps/api/tests/integration/conftest.py`, `Makefile`
- Test: `apps/api/tests/integration/test_migrations.py`, `apps/api/tests/integration/test_db_session.py`

**Interfaces:**

- Consumes: `Settings.database_url()` (Task 3), `build_engine` (Task 4), `postgres`, `postgres_url` fixtures (Task 4).
- Produces:
  - `class Base(DeclarativeBase)` in `infra/db/base.py` with `MetaData(naming_convention={"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s"})`.
  - `alembic.ini`: `script_location = %(here)s/migrations` (works from any cwd and in the image). `env.py` follows Alembic's async template; URL = `config.get_main_option("sqlalchemy.url")` if set, else `Settings().database_url()`; `target_metadata = Base.metadata`.
  - `0001_initial.py`: `revision = "0001"`, `down_revision = None`, empty `upgrade`/`downgrade`.
  - Fixtures: `alembic_config(url: str) -> alembic.config.Config` helper; session fixture `migrated_url` (upgrades the container DB to head); fixture `engine` (function-scoped `create_async_engine(migrated_url, poolclass=NullPool)`, avoiding cross-event-loop pools); fixture `rollback_session` returning an async context manager `(engine) -> AsyncSession` that opens a connection, begins a transaction, yields `AsyncSession(bind=conn, join_transaction_mode="create_savepoint")` and rolls back on exit; fixture `db_session` built on it. Feature tests use `db_session`.
  - Makefile: `migrate` → `uv run --package engleks-api alembic -c apps/api/alembic.ini upgrade head`; `revision` → same with `revision --autogenerate -m "$(m)"`.

- [ ] **Step 1: Write the failing tests**

`test_migrations.py` (sync tests; Alembic's async `env.py` calls `asyncio.run`):

```python
def test_upgrade_downgrade_and_check(postgres_url):
    cfg = alembic_config(postgres_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models and schema differ
```

`postgres_url` is the Task 4 session fixture.

`test_db_session.py`:

```python
async def test_rollback_session_discards_changes(engine, rollback_session):
    async with rollback_session(engine) as s:
        await s.execute(text("CREATE TABLE rollback_probe (id int)"))
        await s.commit()  # commits the savepoint only
    async with engine.connect() as conn:
        exists = await conn.scalar(text("SELECT to_regclass('rollback_probe') IS NOT NULL"))
    assert exists is False
```

- [ ] **Step 2: Run** `uv run pytest apps/api/tests/integration -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest apps/api -v && make lint` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(api): add Alembic with empty initial migration and DB fixtures"`

---

### Task 6: Bot settings, API client and message splitting

**Files:**

- Create: `apps/bot/src/engleks_bot/config.py`, `api_client.py`, `rendering/__init__.py`, `rendering/split.py`
- Test: `apps/bot/tests/test_config.py`, `apps/bot/tests/test_api_client.py`, `apps/bot/tests/test_split.py`

**Interfaces:**

- Consumes: `ErrorCode`, `ErrorResponse`, `HealthResponse`, `HealthStatus` (Task 2).
- Produces:
  - `class Settings(BaseSettings)` in `config.py`: `env_prefix="BOT_"`, `env_file=".env"`, `extra="ignore"`, the same conditional `secrets_dir` as the API. Fields: `telegram_token: SecretStr`, `service_token: SecretStr` (both required), `api_base_url: str = "http://localhost:8000"`, `log_format: Literal["json", "console"] = "json"`, `log_level: str = "INFO"`.
  - `class ApiError(Exception)` in `api_client.py`: `status_code: int | None` (None = transport failure), `code: ErrorCode | None` (None when the body isn't a valid `ErrorResponse` or the code is unknown).
  - `class ApiClient` in `api_client.py`: `__init__(self, base_url: str, service_token: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 10.0)`; sends `X-Bot-Token` on every request; `async def health(self) -> HealthResponse` (`GET /healthz`); `async def aclose(self) -> None`; async context manager. Non-2xx → `ApiError(status, parsed code or None)`; `httpx.HTTPError` → `ApiError(None, None)` chained from the original. `X-Telegram-User-Id` arrives with the first per-user call (Core slice).
  - `split_message(text: str, limit: int = 4096) -> list[str]` in `rendering/split.py`. Length is measured in UTF-16 code units (`len(s.encode("utf-16-le")) // 2`). Algorithm: take the longest prefix within `limit`; cut after its last `"\n"`, else after its last `" "`, else at the prefix end; repeat on the remainder. Drop whitespace-only chunks. Otherwise `"".join(chunks) == text`.

- [ ] **Step 1: Write the failing tests**

`test_split.py`:

```python
def u16(s): return len(s.encode("utf-16-le")) // 2

def test_short_text_single_chunk(): assert split_message("hello") == ["hello"]
def test_empty_and_blank(): assert split_message("") == [] and split_message("   \n") == []
def test_hard_split(): assert [len(c) for c in split_message("a" * 5000)] == [4096, 904]

def test_prefers_newline():
    assert split_message("a" * 4000 + "\n" + "b" * 200) == ["a" * 4000 + "\n", "b" * 200]

def test_prefers_space_without_newline():
    assert split_message("a" * 4000 + " " + "b" * 200) == ["a" * 4000 + " ", "b" * 200]

def test_trailing_whitespace_not_sent_alone():
    assert split_message("a" * 4096 + "\n") == ["a" * 4096]

def test_astral_chars_counted_as_two_units():
    chunks = split_message("😀" * 3000)
    assert all(u16(c) <= 4096 for c in chunks)
    assert chunks[0] == "😀" * 2048 and "".join(chunks) == "😀" * 3000
```

`test_api_client.py` (respx; bodies built from real contract DTOs):

```python
async def test_health_ok_sends_bot_token(respx_mock):
    route = respx_mock.get("http://api/healthz").respond(
        json=HealthResponse(status=HealthStatus.OK).model_dump(mode="json"))
    async with ApiClient("http://api", "tok") as c:
        assert (await c.health()).status is HealthStatus.OK
    assert route.calls.last.request.headers["X-Bot-Token"] == "tok"

async def test_error_response_parsed(respx_mock):
    respx_mock.get("http://api/healthz").respond(500,
        json=ErrorResponse(code=ErrorCode.INTERNAL_ERROR, message="x").model_dump(mode="json"))
    with pytest.raises(ApiError) as e: ...  # health()
    assert (e.value.status_code, e.value.code) == (500, ErrorCode.INTERNAL_ERROR)

@pytest.mark.parametrize("response", [
    httpx.Response(502, text="<html>Bad Gateway</html>"),
    httpx.Response(404, json={"detail": "Not Found"}),
    httpx.Response(429, json={"code": "some_future_code", "message": "x"}),
])
async def test_unexpected_error_body_gives_code_none(respx_mock, response):
    ...  # ApiError with status_code == response.status_code and code is None

async def test_transport_failure(respx_mock):
    respx_mock.get("http://api/healthz").mock(side_effect=httpx.ConnectError("down"))
    ...  # ApiError with status_code is None and code is None
```

`test_config.py`: a missing `BOT_TELEGRAM_TOKEN` raises `ValidationError` matching `telegram_token` (`_env_file=None`, env cleared with `monkeypatch`).

- [ ] **Step 2: Run** `uv run pytest apps/bot -v` — Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest apps/bot -v && make lint` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(bot): add settings, API client and message splitting"`

---

### Task 7: Bot runtime: `/start` handler and entry point

**Files:**

- Create: `apps/bot/src/engleks_bot/logging_setup.py`, `handlers/__init__.py`, `handlers/start.py`, `main.py`
- Test: `apps/bot/tests/test_start.py`, `apps/bot/tests/test_main.py`

**Interfaces:**

- Consumes: `Settings`, `ApiClient`, `ApiError` (Task 6).
- Produces:
  - `configure_logging(log_format, log_level) -> None` in `logging_setup.py`, same behaviour as the API's (duplicated on purpose: the apps must not import each other, and contracts holds only DTOs).
  - `START_TEXT = "Hi! I'm Engleks, your English writing trainer. I'm not ready yet, so check back soon."` and `router: aiogram.Router` in `handlers/start.py`, with `async def start(message: Message) -> None` registered on `CommandStart()`, replying with `START_TEXT`.
  - In `main.py`: `async def check_api(client: ApiClient) -> None` calls `client.health()` once, logs `api_healthy` on success, and on `ApiError` logs `api_unreachable` and re-raises (the process exits non-zero, Compose restarts it). `async def run(settings: Settings) -> None` configures logging, opens `ApiClient(settings.api_base_url, settings.service_token.get_secret_value())`, awaits `check_api`, builds `Dispatcher` with `router`, and runs `start_polling(Bot(settings.telegram_token.get_secret_value()))`. `def main() -> None: asyncio.run(run(Settings()))`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_start_replies_with_placeholder():
    message = AsyncMock(spec=Message)
    await start(message)
    message.answer.assert_awaited_once_with(START_TEXT)

async def test_check_api_ok(respx_mock):
    respx_mock.get("http://api/healthz").respond(json={"status": "ok"})
    async with ApiClient("http://api", "tok") as c:
        await check_api(c)

async def test_check_api_raises_when_down(respx_mock):
    respx_mock.get("http://api/healthz").mock(side_effect=httpx.ConnectError("down"))
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError):
            await check_api(c)
```

- [ ] **Step 2: Run** `uv run pytest apps/bot -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `uv run pytest && make lint` — Expected: the whole suite passes.
- [ ] **Step 5: Commit** `git commit -m "feat(bot): add /start placeholder and startup API health check"`

---

### Task 8: Import contracts

**Files:**

- Modify: `pyproject.toml` (`[tool.importlinter]`), `Makefile` (`lint` gains `uv run lint-imports`)

**Interfaces:**

- Consumes: every package from Tasks 2–7.
- Produces: the spec §4 rules as import-linter contracts, run by `make lint` and the CI `imports` job.

- [ ] **Step 1: Write the contracts**

```toml
[tool.importlinter]
root_packages = ["engleks_api", "engleks_bot", "engleks_contracts"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "API layers"
type = "layers"
containers = ["engleks_api"]
layers = ["transport | admin", "application", "domain"]

[[tool.importlinter.contracts]]
name = "Application and domain never import infra"
type = "forbidden"
source_modules = ["engleks_api.application", "engleks_api.domain"]
forbidden_modules = ["engleks_api.infra"]

[[tool.importlinter.contracts]]
name = "Infra depends only on domain"
type = "forbidden"
source_modules = ["engleks_api.infra"]
forbidden_modules = ["engleks_api.application", "engleks_api.transport", "engleks_api.admin", "engleks_api.main", "engleks_api.config"]

[[tool.importlinter.contracts]]
name = "Pure core"
type = "forbidden"
source_modules = ["engleks_api.domain", "engleks_api.application"]
forbidden_modules = ["fastapi", "sqlalchemy", "httpx", "litellm", "aiogram"]

[[tool.importlinter.contracts]]
name = "Apps are independent"
type = "independence"
modules = ["engleks_api", "engleks_bot"]

[[tool.importlinter.contracts]]
name = "Contracts import neither app"
type = "forbidden"
source_modules = ["engleks_contracts"]
forbidden_modules = ["engleks_api", "engleks_bot"]

[[tool.importlinter.contracts]]
name = "Contracts only at the edge"
type = "forbidden"
source_modules = ["engleks_api.application", "engleks_api.domain", "engleks_api.infra", "engleks_api.admin", "engleks_api.main", "engleks_api.config"]
forbidden_modules = ["engleks_contracts"]
```

If `lint-imports` rejects `litellm` because it isn't installed yet, remove it from "Pure core" and add a `# TODO(core-slice)` comment; the Core slice adds it back together with the dependency.

The spec's enum-parity unit test (for enums duplicated between contracts and domain) isn't added here because the foundation duplicates no enum. The Core slice adds it with `Mode` and `Category`.

- [ ] **Step 2: Run** `uv run lint-imports` — Expected: `Contracts: 7 kept, 0 broken.`
- [ ] **Step 3: Prove a contract bites** — add `import sqlalchemy` to `domain/ports.py`, run `uv run lint-imports`, expect `Pure core BROKEN`; then `git checkout apps/api/src/engleks_api/domain/ports.py`.
- [ ] **Step 4: Run** `make lint` — Expected: clean.
- [ ] **Step 5: Commit** `git commit -m "build: enforce layer and package boundaries with import-linter"`

---

### Task 9: Dockerfiles, Compose and local runtime

**Files:**

- Create: `apps/api/Dockerfile`, `apps/bot/Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`
- Modify: `Makefile` (`dev-db`, `up`, `down`)

**Interfaces:**

- Consumes: `create_app` factory, `alembic.ini`, `engleks-bot` script, `Settings` field names (Tasks 3–7).
- Produces: images `engleks-api:local` and `engleks-bot:local`, and the Compose stack from spec §5.

Decisions:

- **Dockerfiles** (build context = repo root). Builder: `python:3.14-slim-trixie` + `COPY --from=ghcr.io/astral-sh/uv:<pinned version> /uv /bin/uv`; env `UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0`. Copy `pyproject.toml`, `uv.lock` and all three member `pyproject.toml` files, then `uv sync --frozen --no-dev --no-install-workspace --package engleks-<app>` (dependency layer cache), then copy sources and `uv sync --frozen --no-dev --no-editable --package engleks-<app>`. Final: `python:3.14-slim-trixie`, user `app` (uid/gid 10001), `/app/.venv` copied to the same path, `PATH=/app/.venv/bin:$PATH`, no uv. The API image also copies `apps/api/alembic.ini` and `apps/api/migrations/` into `/app` with `WORKDIR /app`; `CMD ["uvicorn", "engleks_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]`. Bot `CMD ["engleks-bot"]`.
- **`.dockerignore`**: `.git`, `.venv`, `**/__pycache__`, `.env`, `**/tests`, `docs`, caches.
- **Secrets**: Compose secrets sourced from env (`environment:` source, read from `.env`) and mounted with per-app `target` names, so pydantic-settings finds `/run/secrets/<prefix>_<field>`:
  - `db_password` ← `API_DB_PASSWORD` → target `api_db_password` (api, migrate, postgres via `POSTGRES_PASSWORD_FILE=/run/secrets/api_db_password`)
  - `service_token` ← `BOT_SERVICE_TOKEN` → `bot_service_token` (bot)
  - `telegram_token` ← `BOT_TELEGRAM_TOKEN` → `bot_telegram_token` (bot)
- **Services**: as in spec §5. `postgres` (pinned image = `POSTGRES_IMAGE` from Task 4, named volume `pgdata`, `127.0.0.1:5432:5432`, healthcheck `pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}`, `POSTGRES_USER=${API_DB_USER}`, `POSTGRES_DB=${API_DB_NAME}`); `migrate` (api image, `command: ["alembic", "upgrade", "head"]`, depends on postgres `service_healthy`); `api` (`127.0.0.1:8000:8000`, depends on migrate `service_completed_successfully`, healthcheck `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz')"`); `bot` (depends on api `service_healthy`, `restart: unless-stopped`); `ollama` (pinned `ollama/ollama` tag, `profiles: [dev]`, volume). Containers set `API_DB_HOST=postgres`, `API_LOG_FORMAT=json`, `BOT_API_BASE_URL=http://api:8000`, `BOT_LOG_FORMAT=json`; `api`/`migrate` share the build through a YAML anchor `x-api-build`.
- **`.env.example`**: every `API_*` and `BOT_*` key from Tasks 3 and 6, with `API_DB_HOST=localhost`, `API_LOG_FORMAT=console`, `BOT_LOG_FORMAT=console`, `API_DB_PASSWORD=change-me`, `BOT_SERVICE_TOKEN=change-me`, and `BOT_TELEGRAM_TOKEN=123456:replace-with-botfather-token`. That value passes aiogram's token format check but doesn't match Trivy's Telegram-token secret rule (`\d+:A[\w-]{34}`); don't use a realistic-looking dummy.
- **Makefile**: `dev-db` → `docker compose up -d --wait postgres`; `up` → `docker compose up -d --build`; `down` → `docker compose down`.

- [ ] **Step 1: Resolve pins** — `docker buildx imagetools inspect` for `python:3.14-slim-trixie`, the chosen `postgres:18.x-trixie`, `ghcr.io/astral-sh/uv:<version>` and `ollama/ollama:<version>`; write each as `tag@sha256:…`. Update `POSTGRES_IMAGE` in the Task 4 conftest to the same value.
- [ ] **Step 2: Write** the Dockerfiles, `.dockerignore`, `compose.yaml`, `.env.example` and Makefile targets.
- [ ] **Step 3: Validate config** — `cp .env.example .env && docker compose config -q` — Expected: no output, exit 0.
- [ ] **Step 4: Check image properties**

```bash
docker compose build
docker run --rm --entrypoint id engleks-api:local -u             # expect 10001
docker run --rm --entrypoint sh engleks-api:local -c 'command -v uv || echo none'   # expect none
```

- [ ] **Step 5: Bring up the stack** — put a real BotFather token in `.env` (`BOT_TELEGRAM_TOKEN`), then `make up`, then `curl -s localhost:8000/readyz`.
Expected: `{"status":"ok"}`; `docker compose ps -a` shows `migrate` exited 0; `docker compose logs bot` shows an `api_healthy` JSON line.

- [ ] **Step 6: Manual check** — send `/start` to the bot in Telegram; expect `START_TEXT`. Then `make down`.
- [ ] **Step 7: Check local dev mode** — `make dev-db`, then `uv run --package engleks-api uvicorn engleks_api.main:create_app --factory --reload` in one shell and `curl -s localhost:8000/readyz` → `{"status":"ok"}` with console-format logs. `make down`.
- [ ] **Step 8: Commit** `git commit -m "build: add Dockerfiles and Compose stack"` (never commit `.env`).

---

### Task 10: CI and Trivy

**Files:**

- Create: `.github/workflows/ci.yml`
- Modify: `Makefile` (`scan`)

**Interfaces:**

- Consumes: `make lint` pieces, `pytest`, both Dockerfiles.
- Produces: six parallel jobs (`lint`, `types`, `imports`, `test`, `trivy-fs`, `images`) on `pull_request` and `push` to `main`.

Decisions:

- Top level: `permissions: contents: read`; `concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }`.
- Python jobs: `actions/checkout@<sha>`, `astral-sh/setup-uv@<sha>` with `enable-cache: true` and `cache-dependency-glob: uv.lock`, then `uv sync --all-packages --frozen`, then one command: `uv run ruff check . && uv run ruff format --check .` / `uv run pyright` / `uv run lint-imports` / `uv run pytest`.
- Trivy runs twice in each scan: a **report** step (`severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL`, `exit-code: 0`, `format: table`) and a **gate** step (`severity: HIGH,CRITICAL`, `ignore-unfixed: true`, `exit-code: 1`). Both use `aquasecurity/trivy-action@<sha>` with an exact `version:` input for the Trivy binary.
  - `trivy-fs`: `scan-type: fs`, `scan-ref: .`, `scanners: vuln,misconfig,secret`, `skip-dirs: .venv`.
  - `images`: matrix `app: [api, bot]`; `docker build -f apps/${{ matrix.app }}/Dockerfile -t engleks-${{ matrix.app }}:ci .` (no push), then report and gate with `image-ref: engleks-${{ matrix.app }}:ci`.
- SHA pinning: for each action, take the latest release tag and resolve it with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`; write `uses: owner/repo@<sha> # <tag>`. For `aquasecurity/trivy-action`, check the SHA against the release on GitHub before pinning (its tags have been tampered with in the past).
- `make scan` runs Trivy from Docker, pinned to the same Trivy version as the CI `version:` input (resolve the digest with `docker buildx imagetools inspect`). A named volume caches the vulnerability DB between runs:

```make
TRIVY_IMAGE := aquasec/trivy:<version>@sha256:<digest>
TRIVY := docker run --rm -v "$(CURDIR)":/src:ro -v trivy-cache:/root/.cache/ -w /src $(TRIVY_IMAGE)
scan:
	$(TRIVY) fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --skip-dirs .venv .
```

- [ ] **Step 1: Write** `ci.yml` and the `scan` target.
- [ ] **Step 2: Run** `make scan` locally — Expected: exit 0. If it fails, fix the finding (bump the dependency, fix the Dockerfile) rather than ignoring it. Also run `docker run --rm -v "$PWD":/src:ro -w /src <TRIVY_IMAGE> config compose.yaml` and note whether Trivy reports anything for Compose. If Trivy doesn't support Compose files, say so in the PR description, because spec §8 assumes it does.
- [ ] **Step 3: Validate the workflow** — `uvx zizmor .github/workflows/ci.yml` (or `actionlint` if installed) — Expected: no errors; every `uses:` is a 40-character SHA.
- [ ] **Step 4: Commit** `git commit -m "ci: add lint, types, imports, test and Trivy jobs"`
- [ ] **Step 5: Run definition-of-done checks** (spec §9) — `make lint && make test` pass locally; `make up` then `/readyz` → 200; bot `/start` replies (Task 9). After the user confirms, push `feat/foundation`, open a PR, and confirm with `gh pr checks --watch` that all six jobs (eight checks with the image matrix) pass.

---

## Spec Coverage

| Spec section | Task |
| --- | --- |
| §2 decisions, §3 layout | 1 (workspace, tooling), 2–7 (packages), 9 (images) |
| §4 dependency rules | 8 (contracts 1–5; enum-parity test deferred to Core slice, no duplicates yet) |
| §5 config | 3, 6 (Settings, secrets_dir, prefixes), 9 (`.env.example`, Compose secrets) |
| §5 Compose, Dockerfiles, local dev | 9 |
| §5 logging | 3, 7 (LLM log fields arrive with the LLM gateway in Core slice) |
| §6 testing | 3–7 (unit, integration with testcontainers + rollback, migrations check, respx bot tests), 1 (`llm` marker) |
| §7 Makefile | 1, 5, 8, 9, 10 |
| §8 CI | 10 |
| §9 definition of done | 9 (Steps 5–6), 10 (Step 5) |
