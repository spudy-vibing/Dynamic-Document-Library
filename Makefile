# Developer entry points (WP-001). `make check` is the gate every WP must pass (DEVELOPMENT_PLAN §0.5).
# The eval harness is separate: `uv run --project evals pytest`.

UV ?= uv
RUN = $(UV) run

.PHONY: check lint format types test doctest ownership traceability evals-test clean

check: lint types test doctest ownership traceability

lint:
	$(RUN) ruff check
	$(RUN) ruff format --check

format:
	$(RUN) ruff check --fix
	$(RUN) ruff format

types:
	$(RUN) mypy --strict src scripts

test:
	$(RUN) pytest -q --cov

doctest:
	$(RUN) python -m doctest scripts/check_ownership.py scripts/traceability.py

ownership:
	$(RUN) python scripts/check_ownership.py

traceability:
	$(RUN) python scripts/traceability.py

evals-test:
	$(UV) run --project evals pytest

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist
