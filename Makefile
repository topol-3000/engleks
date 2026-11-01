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
