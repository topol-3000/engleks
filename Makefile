.PHONY: install lint fmt test test-llm migrate revision dev-db up down scan litellm-image llm-key invite admin
install:
	uv sync --all-packages
	uv run pre-commit install
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run lint-imports
	uv run pre-commit run shellcheck --all-files
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
# Image scans read the local image through the Docker socket.
TRIVY_DOCKER := docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ $(TRIVY_IMAGE)
# trivy fs never looks inside images, and the proxy is the largest third-party image we run.
scan: litellm-image
	$(TRIVY) fs --no-progress --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --skip-dirs .venv .
	$(TRIVY_DOCKER) image --no-progress --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 engleks-litellm:local
litellm-image:
	docker build -t engleks-litellm:local litellm
# Prints the API's virtual key for .env (API_LLM_API_KEY). Needs the litellm service running.
llm-key:
	docker compose exec litellm python /litellm/generate_key.py
# Run engleks-admin in the running api container.
invite:
	docker compose exec api engleks-admin invite create $(if $(note),--note "$(note)") $(if $(uses),--uses $(uses)) $(if $(days),--days $(days))
admin:
	docker compose exec api engleks-admin $(a)
