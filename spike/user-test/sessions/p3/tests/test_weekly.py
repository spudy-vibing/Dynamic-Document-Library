"""P3 Task 3: build examples/01_weekly_status.py, run it on the mock, check fan-in order.

Written during a SIMULATED session (agent playing P3). Lives under sessions/p3/
instead of spike/tests/ so the session does not touch files outside its folder.
"""

import importlib.util
from pathlib import Path

from doccode import MockProvider

SPIKE = Path(__file__).resolve().parents[4]          # .../spike
EXAMPLE = SPIKE / "examples" / "01_weekly_status.py"
MOCK = EXAMPLE.with_suffix(".mock.json")


def _load_example():
    # The file name starts with a digit, so it cannot be imported normally.
    spec = importlib.util.spec_from_file_location("weekly_status", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_next_week_exists_and_runs_last():
    doc = _load_example().build()
    content = doc.execute(provider=MockProvider(str(MOCK)))

    assert content.ok
    section = content.find("next-week")              # raises if missing
    assert section.title == "Next Week"

    records = {p.id: p for p in content.run.prompts}
    assert set(records) == {"shipped", "in-progress", "blockers", "next-week"}
    assert all(p.status == "ok" for p in records.values())
    upstream_done = max(records[i].ended_at for i in ("shipped", "in-progress", "blockers"))
    assert records["next-week"].started_at >= upstream_done


def test_plan_puts_next_week_in_last_level():
    # The plan is the CI-facing contract; check it textually since plan() has no documented API.
    text = str(_load_example().build().plan())
    last_level = text.split("level 2:")[1]
    assert "next-week" in last_level
    for upstream in ("shipped", "in-progress", "blockers"):
        assert upstream in last_level            # listed as its dependencies


def test_no_provider_is_a_config_error():
    # What CI would see if someone dropped --dry-run and there is no key.
    import pytest
    from doccode import ConfigError      # works, but is not listed in doccode.__all__

    with pytest.raises(ConfigError) as exc:
        _load_example().build().execute()
    assert "provider" in str(exc.value).lower()
