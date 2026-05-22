.PHONY: help install check test test-slow lint format typecheck imports clean

help:
	@echo "Targets:"
	@echo "  install    - sync dev environment (uv sync --all-extras --dev)"
	@echo "  check      - run all quality gates: lint, typecheck, imports, test"
	@echo "  test       - run fast test suite (excludes 'slow' marker)"
	@echo "  test-slow  - run all tests including slow self-play tests"
	@echo "  lint       - ruff check (lint only, no fixes)"
	@echo "  format     - ruff format (rewrites files)"
	@echo "  typecheck  - pyright strict on domain layers"
	@echo "  imports    - import-linter dependency-matrix check"
	@echo "  clean      - remove caches and build artifacts"

install:
	uv sync --all-extras --group dev

check: lint typecheck imports test

test:
	uv run pytest -m "not slow"

test-slow:
	uv run pytest

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run pyright

imports:
	uv run lint-imports

clean:
	rm -rf .pytest_cache .ruff_cache .pyright .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
