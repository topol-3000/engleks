# syntax=docker/dockerfile:1
# Build context is the repo root: uv needs the workspace lockfile and packages/contracts.
FROM python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
# Pinned upgrades for CVEs the base image predates; drop each once the base image ships it:
# apt 3.0.4 (CVE-2026-109518), zlib1g deb13u1 (CVE-2026-76844, CVE-2026-85091).
# curl is for debugging inside the container. pip is not needed at runtime, and its vendored
# libraries (urllib3) carry CVEs.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        apt=3.0.4 libapt-pkg7.0=3.0.4 zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/bot/pyproject.toml apps/bot/
COPY packages/contracts packages/contracts
COPY apps/api/src apps/api/src
# uv is mounted for this step only, so it does not stay in the image.
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    uv sync --frozen --no-dev --no-editable --package engleks-api
COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
# Request logging is done by RequestIdMiddleware, so uvicorn's access log is off.
CMD ["uvicorn", "engleks_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
