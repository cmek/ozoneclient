.PHONY: help sync lint test test-part1 test-part2 test-negative test-all collect clean

# Pass extra pytest args with PYTEST_ARGS, e.g. `make test-part1 PYTEST_ARGS="-s -k cache"`
PYTEST_ARGS ?=

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync: ## Install project + dev dependencies (pytest)
	uv sync --group dev

lint: ## Lint the client and tests
	uv run ruff check src tests

test: test-part1 ## Default test target: Part 1 only (safe, creates nothing)

test-part1: ## Part 1 — non-mutating validation (safe to run frequently / in CI)
	uv run pytest tests/test_part1_validation.py $(PYTEST_ARGS)

test-part2: ## Part 2 — mutating lifecycle (creates real Ozone objects; run deliberately)
	OZONE_RUN_MUTATING=1 uv run pytest tests/test_part2_lifecycle.py $(PYTEST_ARGS)

test-negative: ## Part 2 incl. exploratory write-rejection negatives
	OZONE_RUN_MUTATING=1 OZONE_RUN_NEGATIVE=1 uv run pytest tests/test_part2_lifecycle.py $(PYTEST_ARGS)

test-all: ## Run every test (Part 1 + mutating + negatives)
	OZONE_RUN_MUTATING=1 OZONE_RUN_NEGATIVE=1 uv run pytest $(PYTEST_ARGS)

collect: ## List the test cases without running them
	uv run pytest --collect-only -q

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
