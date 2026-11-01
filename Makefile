.PHONY: install lint fmt test test-llm migrate revision dev-db up down scan
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
# Keep the version in sync with TRIVY_VERSION in .github/workflows/ci.yml.
TRIVY_IMAGE := aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
TRIVY := docker run --rm -v "$(CURDIR)":/src:ro -v trivy-cache:/root/.cache/ -w /src $(TRIVY_IMAGE)
scan:
	$(TRIVY) fs --no-progress --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --skip-dirs .venv .
