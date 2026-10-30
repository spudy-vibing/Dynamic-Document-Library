# Developer entry points (WP-001). `make check` is the gate every WP must pass (DEVELOPMENT_PLAN §0.5).
# The eval harness is separate: `uv run --project evals pytest`.

UV ?= uv
RUN = $(UV) run

.PHONY: check lint format types test doctest ownership traceability evals-test evals clean

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

# The offline eval job (WP-019, .github/workflows/evals-offline.yml): no key, FakeLLM only.
# Kept out of `check`, which is the library gate (DEVELOPMENT_PLAN §0.5).
EVALS_SMOKE_DIR ?= $(or $(TMPDIR),/tmp)/doccode-evals-smoke
EVALS_SMOKE_CASES ?= w-calderwyn-5120 q-brightwell-2417 r-halcyx-sync-7781 i-larkspur-6347
EVALS_SMOKE_GENERATORS ?= b0-oneshot b1-parallel b2-chain p1-frame p4-editor

evals: evals-test
	$(UV) run --project evals python -m evals.checks.check_imports
	$(UV) run --project evals python -m evals.fixtures.make_cases --check
	rm -rf "$(EVALS_SMOKE_DIR)"
	$(UV) run --project evals python -m evals all --cases $(EVALS_SMOKE_CASES) \
		--generators $(EVALS_SMOKE_GENERATORS) --reps 1 --run-id fake-smoke \
		--runs-dir "$(EVALS_SMOKE_DIR)/runs" --scoreboard-dir "$(EVALS_SMOKE_DIR)/scoreboard"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist
