# syntax=docker/dockerfile:1
# The upstream LiteLLM image, pinned by digest, with the HIGH/CRITICAL findings that have a
# fix patched in place. No upstream image both predates the 1.107.0 compromise and scans
# clean, and `make scan` must report no fixable findings.
# Pinned by digest: LiteLLM releases were compromised in 2026 (1.82.7, 1.82.8, 1.107.0).
# This ARG is the only place the version lives: Compose and `make scan` build this file.
ARG LITELLM_BASE_IMAGE=ghcr.io/berriai/litellm:v1.106.1@sha256:1acf80698f7eb47021b0bf5aed01ba9e80098cf74cdb3e588515bdd48a129fa6

FROM ${LITELLM_BASE_IMAGE}
# The base image pins ca-certificates-bundle in /etc/apk/world, so upgrade alone skips it.
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates-bundle=20260909-r3
# Exact versions: the first with each fix, plus the new dependencies they pull in. uv is
# mounted for this step only, so no installer stays in the image. Both the proxy's venv and
# the bundled admin tool's are patched. filelock 4 is outside litellm's declared <4: the fix
# has no 3.x release, and the only users (litellm's CLI client commands, huggingface_hub)
# need FileLock/SoftFileLock/Timeout, which 4.x keeps.
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0,source=/uv,target=/bin/uv \
    UV_NO_CACHE=1 uv pip install --python /app/.venv/bin/python \
        aiohttp==3.14.4 certifi==2026.10.18 ddtrace==4.15.6 dnspython==2.9.0 \
        filelock==4.2.1 oauthlib==4.0.1 pyasn1==0.6.5 pypdf==6.21.4 \
        ddtrace-internal==0.1.0 yarl==1.25.1 \
    && UV_NO_CACHE=1 uv pip install --python /opt/liteadmin/bin/python \
        aiohttp==3.14.4 certifi==2026.10.18
# The upstream image runs as root; use its built-in unprivileged account.
USER nonroot
