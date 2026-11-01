.PHONY: install lint fmt test test-llm migrate revision dev-db up down
install:
	uv sync --all-packages
	uv run pre-commit install
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run lint-imports
fmt:
	uv run ruff format .
	uv run ruff check --fix .
test:
	uv run pytest
test-llm:
	uv run pytest -m llm || [ $$? -eq 5 ]
ALEMBIC := uv run --package engleks-api alembic -c apps/api/alembic.ini
migrate:
	$(ALEMBIC) upgrade head
revision:
	$(ALEMBIC) revision --autogenerate -m "$(m)"
dev-db:
	docker compose up -d --wait postgres
up:
	docker compose up -d --build
down:
	docker compose down
