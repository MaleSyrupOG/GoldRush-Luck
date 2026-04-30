# GoldRush — developer Makefile
# Run `make help` to see available targets.

.PHONY: help setup lint format type audit test test-unit test-integration test-property test-e2e \
        run-dev-luck run-dev-dw migrate-up migrate-down clean

help:
	@echo "GoldRush — developer targets"
	@echo ""
	@echo "  setup              install dependencies via uv"
	@echo "  lint               ruff check"
	@echo "  format             ruff format"
	@echo "  type               mypy --strict on critical packages"
	@echo "  audit              pip-audit"
	@echo "  test               run unit + integration"
	@echo "  test-unit          unit tests only (fast)"
	@echo "  test-integration   integration tests (needs Postgres)"
	@echo "  test-property      hypothesis property tests"
	@echo "  test-e2e           end-to-end tests (Discord sandbox)"
	@echo "  run-dev-luck       run the Luck bot in dev mode"
	@echo "  run-dev-dw         run the D/W bot in dev mode"
	@echo "  migrate-up         alembic upgrade head"
	@echo "  migrate-down       alembic downgrade -1"
	@echo "  clean              remove caches and build artefacts"

setup:
	uv sync --frozen

lint:
	uv run ruff check .

format:
	uv run ruff format .

type:
	uv run mypy --strict goldrush_core goldrush_luck goldrush_deposit_withdraw

audit:
	uv run pip-audit --strict

test:
	$(MAKE) test-unit
	$(MAKE) test-integration

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

test-property:
	uv run pytest tests/property -v

test-e2e:
	uv run pytest tests/e2e -v -m e2e

run-dev-luck:
	uv run python -m goldrush_luck

run-dev-dw:
	uv run python -m goldrush_deposit_withdraw

migrate-up:
	uv run alembic -c ops/alembic/alembic.ini upgrade head

migrate-down:
	uv run alembic -c ops/alembic/alembic.ini downgrade -1

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
