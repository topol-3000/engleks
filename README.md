# engleks

A Telegram trainer for English writing: it improves or translates your text and explains
every change. A FastAPI service does the work; an aiogram bot is a thin client of it.

## Quick start

```bash
make install
cp .env.example .env    # set BOT_TELEGRAM_TOKEN to run the bot
make dev-db             # PostgreSQL only
make migrate
uv run uvicorn engleks_api.main:create_app --factory --reload   # http://localhost:8000/docs
uv run engleks-bot
```

Or run the whole stack in containers: `make up` (`make down` to stop).

- [AGENTS.md](AGENTS.md): commands, layout, rules and conventions
- [docs/architecture.md](docs/architecture.md): MVP scope and architecture
