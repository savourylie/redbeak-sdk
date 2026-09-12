# Run `uv sync --all-packages --frozen` once before these offline checks.
export UV_OFFLINE := 1

.PHONY: check format-check lint typecheck test contract-release-check

check: format-check lint typecheck test

format-check:
	uv run --no-sync ruff format --check .

lint:
	uv run --no-sync ruff check .

typecheck:
	uv run --no-sync mypy

test:
	uv run --no-sync pytest

# Requires the real contract tag. Never substitute HEAD for a missing release tag.
contract-release-check:
	uv build --package redbeak-contracts --out-dir dist
	uv run --no-sync python -m redbeak_contracts.verify --repository . --wheel dist/redbeak_contracts-0.1.0-py3-none-any.whl
