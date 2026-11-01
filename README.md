# engleks

A Telegram trainer for English writing: it improves or translates your text and explains
every change. A FastAPI service does the work; an aiogram bot is a thin client of it.

## Quick start

```bash
make install
cp .env.example .env    # set BOT_TELEGRAM_TOKEN to run the bot; change the LITELLM_* values
make up                 # the whole stack, with the LiteLLM proxy and Ollama (dev profile)
docker compose exec ollama ollama pull gemma3:4b
make llm-key            # paste the printed API_LLM_API_KEY line into .env
# Secret values are not part of Compose's change detection, so force the restart:
docker compose up -d --force-recreate --no-deps api
```

The API is at http://localhost:8000/docs and the LiteLLM Admin UI at
http://127.0.0.1:4000/ui (log in with `LITELLM_UI_USERNAME`/`LITELLM_UI_PASSWORD`).
`make down` stops the stack.

To run the API and bot on the host instead, keep `make up` running for the proxy and:

```bash
docker compose stop api bot
make migrate
uv run uvicorn engleks_api.main:create_app --factory --reload
uv run engleks-bot
```

**Existing dev volume:** Postgres creates the proxy's role and database only on an empty
data directory. If your `pgdata` volume predates the proxy, recreate it once
(`make down`, `docker volume rm engleks_pgdata`, `make up`; this deletes local data), or
keep the data and run the init script once against it:

```bash
docker compose up -d postgres
docker compose exec postgres sh /docker-entrypoint-initdb.d/10-litellm.sh
```

## Changing a secret

Compose passes secrets from `.env` into containers, but a changed value does not count as a
change: `make up` keeps the old one. After editing a secret in `.env`, recreate the services
that use it:

```bash
docker compose up -d --force-recreate --no-deps <service>...
```

| Secret in `.env` | Services | Also needed |
| --- | --- | --- |
| `BOT_SERVICE_TOKEN` | `api`, `bot` | set `API_SERVICE_TOKEN` to the same value for host runs |
| `BOT_TELEGRAM_TOKEN` | `bot` | |
| `API_LLM_API_KEY` | `api` | |
| `LITELLM_UI_USERNAME`, `LITELLM_UI_PASSWORD`, `OPENAI_API_KEY` | `litellm` | |
| `LITELLM_MASTER_KEY` | `litellm` | it invalidates the API's key: then run `make llm-key`, put the key into `.env` and recreate `api` |
| `LITELLM_DB_PASSWORD` | `litellm` | change the role's password first (below) |
| `API_DB_PASSWORD` | `migrate`, `api` | change the role's password first (below) |

Postgres sets passwords only when it creates a role, so a database password also has to be
changed inside Postgres, before recreating the services:

```bash
set -a; . ./.env; set +a
# LITELLM_DB_PASSWORD
echo "ALTER ROLE litellm PASSWORD :'pw';" | docker compose exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$1"' sh "$LITELLM_DB_PASSWORD"
# API_DB_PASSWORD
echo "ALTER ROLE CURRENT_USER PASSWORD :'pw';" | docker compose exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$1"' sh "$API_DB_PASSWORD"
```

- [AGENTS.md](AGENTS.md): commands, layout, rules and conventions
- [docs/architecture.md](docs/architecture.md): MVP scope and architecture
