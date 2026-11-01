# syntax=docker/dockerfile:1
# Build context is the repo root: uv needs the workspace lockfile and packages/contracts.
ARG PYTHON_IMAGE=python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/bot/pyproject.toml apps/bot/
COPY packages/contracts/pyproject.toml packages/contracts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package engleks-api
COPY packages/contracts/src packages/contracts/src
COPY apps/api/src apps/api/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package engleks-api

FROM ${PYTHON_IMAGE}
# pip is not needed at runtime, and its vendored libraries (urllib3) carry CVEs.
RUN python -m pip uninstall --yes pip \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Request logging is done by RequestIdMiddleware, so uvicorn's access log is off.
CMD ["uvicorn", "engleks_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
