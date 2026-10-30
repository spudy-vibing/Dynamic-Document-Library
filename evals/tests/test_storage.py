"""EVAL-15: run directories, manifest, JSONL and resume."""

from __future__ import annotations

import asyncio

import pytest

from evals.core.llm import FakeLLM
from evals.core.storage import ManifestMismatch, RunStore, case_set_hash, latest_run_id, result_from_dict, result_to_dict
from evals.generators import GENERATORS
from evals.tests.fakes import write_tiny_case


@pytest.fixture
def result(tmp_path):
    case = write_tiny_case(tmp_path / "cases")
    r = asyncio.run(GENERATORS["b1-parallel"].generate(case, FakeLLM("claude-sonnet-5-5"), 1))
    r.flagged_numbers = [("summary", "12%")]
    return r


def test_result_round_trip(result):
    back = result_from_dict(result_to_dict(result))
    assert back == result


def test_save_load_and_resume_semantics(tmp_path, result):
    store = RunStore("r1", tmp_path)
    assert not store.has_result("b1-parallel", result.case_id, 1)
    store.save_result(result)
    assert store.has_result("b1-parallel", result.case_id, 1)
    assert (store.doc_base("b1-parallel", result.case_id, 1).with_suffix(".md")).read_text() == result.markdown
    assert store.load_result("b1-parallel", result.case_id, 1) == result
    assert [r.case_id for r in store.iter_results()] == [result.case_id]

    result.error = "infra: boom"
    store.save_result(result)
    assert not store.has_result("b1-parallel", result.case_id, 1)  # errored slots are retried on resume
    assert store.has_result("b1-parallel", result.case_id, 1, ok_only=False)


def test_jsonl_append_read_and_torn_line(tmp_path):
    store = RunStore("r1", tmp_path)
    store.append("calls", [{"a": 1}, {"a": 2}])
    with open(store.jsonl_path("calls"), "a") as f:
        f.write('{"a": 3')  # interrupted write
    assert store.read("calls") == [{"a": 1}, {"a": 2}]
    store.rewrite("calls", [{"b": 1}])
    assert store.read("calls") == [{"b": 1}]
    assert store.read("missing") == []


def test_manifest_merge_and_mismatch(tmp_path):
    store = RunStore("r1", tmp_path)
    base = {"mode": "fake", "models": {"generator": "m"}, "generators": [{"name": "b0", "version": "1"}],
            "case_ids": ["c1"], "case_paths": ["p1"], "reps": 1}
    m = store.open_manifest(base)
    assert m["run_id"] == "r1" and "created_at" in m
    m = store.open_manifest({**base, "generators": [{"name": "b1", "version": "2"}], "case_ids": ["c2"],
                             "case_paths": ["p2"], "reps": 3})
    assert [g["name"] for g in m["generators"]] == ["b0", "b1"]
    assert m["case_ids"] == ["c1", "c2"] and m["reps"] == 3
    with pytest.raises(ManifestMismatch):
        store.open_manifest({**base, "mode": "live"})
    with pytest.raises(ManifestMismatch):
        store.open_manifest({**base, "generators": [{"name": "b0", "version": "9"}]})


def test_case_set_hash_changes_with_files(tmp_path):
    case = write_tiny_case(tmp_path / "cases")
    h1 = case_set_hash([case])
    (case.path / "data.json").write_text("{}")
    assert case_set_hash([case]) != h1


def test_latest_run_id(tmp_path):
    assert latest_run_id(tmp_path) is None
    RunStore("a", tmp_path).open_manifest({"mode": "fake"})
    assert latest_run_id(tmp_path) == "a"


def test_bad_run_id_rejected(tmp_path):
    with pytest.raises(ValueError):
        RunStore("../escape", tmp_path)
