"""Judge calibration tooling (WP-018, EVAL-13): sampling, blinding, label import, agreement math.

Everything runs on FAKE runs in tmp directories; packs and labels never touch evals/runs/.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import json
from pathlib import Path

import pytest

from evals import runner
from evals.__main__ import main
from evals.calibration import agreement as ag
from evals.calibration.calibrate import (
    _absolute_section,
    _pairwise_section,
    calibrate,
    load_calibration,
    majority,
    render_section,
    write_calibration,
)
from evals.calibration.labels import import_labels, parse_file, read_labels, validate
from evals.calibration.pack import (
    CSV_COLUMNS,
    LABELS_FORMAT,
    CalibrationError,
    build_pack,
    export_pack,
    key_path,
    load_keys,
    sample,
    settings,
)
from evals.core.config import load_config
from evals.core.storage import RunStore
from evals.core.types import GenerationResult
from evals.generators import GENERATORS
from evals.judge import absolute, pairwise
from evals.tests.fakes import write_tiny_case

CONFIG = load_config()
GEN_NAMES = ("b0-oneshot", "b1-parallel", "b2-chain")
GENS = [GENERATORS[n] for n in GEN_NAMES]
QUIET = {"log": lambda s: None}


def cfg(**calibration):
    c = copy.deepcopy(CONFIG)
    c["calibration"] = {**c.get("calibration", {}), "bootstrap_resamples": 200, **calibration}
    return c


@pytest.fixture(scope="module")
def judged_run(tmp_path_factory):
    """A FAKE dev-labelled run on three tiny cases x 2 reps x B0-B2, pairwise and absolute judged."""
    tmp = tmp_path_factory.mktemp("cal")
    cases = [write_tiny_case(tmp / "cases", case_id=f"tiny-0{i}") for i in (1, 2, 3)]
    store = RunStore("fake-cal", tmp / "runs")
    store.open_manifest(runner.build_manifest(cases, GENS, 2, CONFIG, mode="fake", case_set="dev"))
    asyncio.run(runner.run_generation(store, cases, GENS, 2, CONFIG, **QUIET))
    asyncio.run(runner.judge_run(store, cases, CONFIG, **QUIET))
    asyncio.run(runner.absolute_run(store, cases, CONFIG, **QUIET))
    return store


@pytest.fixture
def run(judged_run, tmp_path):
    """A private copy of the judged run, so tests can export and import freely."""
    import shutil

    dst = tmp_path / "runs"
    shutil.copytree(judged_run.root, dst)
    return RunStore(judged_run.run_id, dst)


def judge_labels(store: RunStore, key: dict, labeller: str, *, flip: set[str] = frozenset(), score_shift: int = 0) -> dict:
    """A labels body that copies the judges (pairwise both-orders verdict, absolute scores); ``flip`` reverses items."""
    jv = store.read_manifest()["judge_version"]
    outcomes = {(o.case_id, o.rep, o.x, o.y): o for o in pairwise.combine(r for r in store.read("judge") if r["judge_version"] == jv)}
    scores = absolute.doc_scores(store.read(absolute.STREAM), store.read_manifest()["absolute_version"])
    labels = []
    for iid, k in key["items"].items():
        if k["kind"] == "pairwise":
            o = outcomes[(k["case_id"], k["rep"], k["pair"][0], k["pair"][1])]
            v = {}
            for q in pairwise.QUESTIONS:
                w = o.winners[q]
                if iid in flip:
                    w = k["label_a"] if w in (None, k["label_b"]) else k["label_b"]
                v[q] = "tie" if w is None else "A" if w == k["label_a"] else "B"
            labels.append({"item_id": iid, "kind": "pairwise", "verdicts": v})
        else:
            s = scores[(k["generator"], k["case_id"], k["rep"])]
            labels.append({"item_id": iid, "kind": "absolute",
                           "scores": {c: min(5, max(1, s[c] + score_shift)) for c in absolute.CRITERIA}})
    return {"format": LABELS_FORMAT, "pack_id": key["pack_id"], "labeller": labeller, "labels": labels}


def write_labels(tmp: Path, body: dict, name: str = "labels.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(body))
    return p


# ---------------------------------------------------------------- agreement math (hand-computed)


def test_eval_13_cohen_kappa_two_categories():
    # p_o = 35/50 = 0.7; p_e = (25*30 + 25*20) / 50^2 = 0.5; kappa = (0.7 - 0.5) / 0.5 = 0.4
    m = [[20, 5], [10, 15]]
    assert ag.cohen_kappa(m) == pytest.approx(0.4)
    assert ag.raw_agreement(m) == pytest.approx(0.7)


def test_eval_13_cohen_kappa_three_way_with_ties():
    # rows (human) x, y, tie = 15, 10, 10; cols (judge) = 13, 12, 10; n = 35, diagonal 24
    # p_o = 24/35; p_e = (15*13 + 10*12 + 10*10) / 35^2 = 415/1225; kappa = (840 - 415) / (1225 - 415) = 425/810
    m = [[10, 2, 3], [1, 8, 1], [2, 2, 6]]
    assert ag.cohen_kappa(m) == pytest.approx(425 / 810)
    # agreement on human non-tie items: (10 + 8) / (15 + 10); both sided: 18 / (10 + 2 + 1 + 8)
    assert ag.decided_agreement(m) == pytest.approx(18 / 25)
    assert ag.decided_count(m) == 25
    assert ag.both_decided_agreement(m) == pytest.approx(18 / 21)


def test_eval_13_quadratic_weighted_kappa_hand_example():
    # pairs (1,2) (2,1) (3,3) (4,5) (5,4); weights (i-j)^2/16.
    # observed: 4 off-by-one cells of weight 1/16 -> (4/16)/5 = 0.05
    # expected: all marginals 1; sum over i,j of (i-j)^2 = 8*1 + 6*4 + 4*9 + 2*16 = 100 -> (100/16)/25 = 0.25
    # kappa_w = 1 - 0.05/0.25 = 0.8
    m = ag.confusion([(1, 2), (2, 1), (3, 3), (4, 5), (5, 4)], ag.SCORE_CATEGORIES)
    assert ag.weighted_kappa(m) == pytest.approx(0.8)
    assert ag.raw_agreement(m) == pytest.approx(0.2)
    assert ag.within_one(m) == pytest.approx(1.0)
    assert ag.mean_difference(m) == pytest.approx(0.0)
    assert ag.weighted_kappa(ag.confusion([(s, s) for s in range(1, 6)], ag.SCORE_CATEGORIES)) == pytest.approx(1.0)


def test_eval_13_kappa_undefined_and_bias():
    one_category = ag.confusion([(3, 3), (3, 3)], ag.SCORE_CATEGORIES)
    assert ag.weighted_kappa(one_category) is None  # no expected disagreement
    assert ag.cohen_kappa([[0, 0], [0, 0]]) is None
    assert ag.decided_agreement([[0, 0, 0], [0, 0, 0], [0, 0, 4]]) is None  # only human ties
    # judge one point above the human on one of two documents
    assert ag.mean_difference(ag.confusion([(2, 3), (4, 4)], ag.SCORE_CATEGORIES)) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        ag.confusion([(0, 3)], ag.SCORE_CATEGORIES)


def test_eval_13_cluster_bootstrap_ci():
    a = ag.confusion([("x", "x"), ("y", "y")], ag.PAIR_CATEGORIES)
    b = ag.confusion([("x", "y"), ("y", "y")], ag.PAIR_CATEGORIES)
    est = ag.estimate({"c1": a, "c2": b}, 3, ag.decided_agreement, resamples=500, seed=1)
    assert est["value"] == pytest.approx(0.75)
    lo, hi = est["ci"]
    assert 0.5 <= lo <= 0.75 <= hi <= 1.0
    assert ag.estimate({"c1": a, "c2": b}, 3, ag.decided_agreement, resamples=500, seed=1) == est  # seeded
    single = ag.estimate({"c1": a}, 3, ag.decided_agreement, resamples=50)
    assert single["ci"] == [1.0, 1.0]
    assert ag.estimate({}, 3, ag.decided_agreement)["value"] is None


def test_eval_13_human_majority():
    assert majority(["x"]) == ("x", True)
    assert majority(["x", "x", "y"]) == ("x", True)
    assert majority(["x", "y"]) == ("tie", False)
    assert majority(["tie", "tie", "x"]) == ("tie", True)


def test_eval_13_pairwise_agreement_hand_example():
    # human majority x, x, y, tie (the last has no majority: one x, one tie)
    # judge          x, tie, y, x
    # non-tie items 3; agreement 2/3; one judge tie on a non-tie item counts against it
    human = {
        ("c1", 1, "g1", "g2"): {"h1": {q: "x" for q in pairwise.QUESTIONS}, "h2": {q: "x" for q in pairwise.QUESTIONS}},
        ("c1", 2, "g1", "g2"): {"h1": {q: "x" for q in pairwise.QUESTIONS}, "h2": {q: "x" for q in pairwise.QUESTIONS}},
        ("c2", 1, "g1", "g2"): {"h1": {q: "y" for q in pairwise.QUESTIONS}, "h2": {q: "y" for q in pairwise.QUESTIONS}},
        ("c2", 2, "g1", "g2"): {"h1": {q: "x" for q in pairwise.QUESTIONS}, "h2": {q: "tie" for q in pairwise.QUESTIONS}},
    }
    judge = {
        ("c1", 1, "g1", "g2"): {q: "x" for q in pairwise.QUESTIONS},
        ("c1", 2, "g1", "g2"): {q: "tie" for q in pairwise.QUESTIONS},
        ("c2", 1, "g1", "g2"): {q: "y" for q in pairwise.QUESTIONS},
        ("c2", 2, "g1", "g2"): {q: "x" for q in pairwise.QUESTIONS},
    }
    s = _pairwise_section(human, judge, settings(cfg()), {"resamples": 100})
    o = s["questions"]["overall"]
    assert o["agreement"]["value"] == pytest.approx(2 / 3)
    assert (o["items"], o["non_tie_items"], o["judge_ties_on_non_tie"], o["no_human_majority"]) == (4, 3, 1, 1)
    assert o["passes"] is False and o["gated"] is True
    assert o["per_labeller"]["h2"] == pytest.approx(2 / 3)
    assert o["per_labeller"]["h1"] == pytest.approx(3 / 4)
    # human vs human: they agree on 3 of 4 items; both chose a side on 3 and agreed on all 3
    hh = s["human_vs_human"][0]
    assert hh["labellers"] == ["h1", "h2"] and hh["items"] == 4
    assert hh["questions"]["overall"]["raw_agreement"]["value"] == pytest.approx(0.75)
    assert hh["questions"]["overall"]["both_sided_agreement"] == pytest.approx(1.0)


def test_eval_13_absolute_kappa_hand_example():
    # one labeller, judge = the hand example above on every criterion -> kappa_w 0.8 (>= 0.6, trusted)
    pts = [(1, 2), (2, 1), (3, 3), (4, 5), (5, 4)]
    human = {("g", f"c{i}", 1): {"h1": {c: h for c in absolute.CRITERIA}} for i, (h, _) in enumerate(pts)}
    judge = {("g", f"c{i}", 1): {c: j for c in absolute.CRITERIA} for i, (_, j) in enumerate(pts)}
    s = _absolute_section(human, judge, settings(cfg()), {"resamples": 100})
    c1 = s["criteria"]["c1_accuracy"]
    assert c1["kappa"]["value"] == pytest.approx(0.8) and c1["trusted"]
    assert c1["within_one"] == pytest.approx(1.0) and c1["pairs"] == 5
    assert s["human_vs_human"] == []
    s = _absolute_section(human, judge, settings(cfg(absolute_kappa=0.9)), {"resamples": 100})
    assert not s["criteria"]["c1_accuracy"]["trusted"]


# ---------------------------------------------------------------- sampling and blinding


def _result(g: str, case_id: str, rep: int) -> GenerationResult:
    return GenerationResult(generator=g, generator_version="1", case_id=case_id, rep=rep, title="T", sections=[],
                            calls=[], wall_ms=0)


def test_eval_13_sampling_is_stratified_and_seeded():
    results = [_result(g, f"c{i}", rep) for g in ("g1", "g2", "g3", "n0-null") for i in range(6) for rep in (1, 2, 3)]
    families = {f"c{i}": f"f{i % 3}" for i in range(6)}
    pairs = [("g1", "g2"), ("g3", "g2"), ("g1", "n0-null")]
    a = sample(results, pairs, families, n_pairs=10, n_docs=9, seed=7, exclude=["n0-null"])
    b = sample(results, pairs, families, n_pairs=10, n_docs=9, seed=7, exclude=["n0-null"])
    c = sample(results, pairs, families, n_pairs=10, n_docs=9, seed=8, exclude=["n0-null"])
    assert a == b and a != c
    pair_slots, doc_slots = a
    assert len(pair_slots) == 10 and len(doc_slots) == 9 and len(set(pair_slots)) == 10
    per_pair = [sum(1 for s in pair_slots if (s.x, s.y) == p) for p in pairs[:2]]
    assert sorted(per_pair) == [5, 5]  # equal share per generator pair; the null pair is excluded
    assert {sum(1 for d in doc_slots if d.generator == g) for g in ("g1", "g2", "g3")} == {3}
    per_case = [sum(1 for s in pair_slots if s.case_id == f"c{i}") for i in range(6)]
    assert max(per_case) - min(per_case) <= 1  # spread over cases (and so families)
    assert not any(d.generator == "n0-null" for d in doc_slots)


def test_eval_13_pack_is_deterministic(run):
    p1, k1 = build_pack(run, cfg(), seed=3, n_pairs=8, n_docs=5)
    p2, k2 = build_pack(run, cfg(), seed=3, n_pairs=8, n_docs=5)
    p3, _ = build_pack(run, cfg(), seed=4, n_pairs=8, n_docs=5)
    strip = lambda d: {k: v for k, v in d.items() if k != "created_at"}  # noqa: E731
    assert strip(p1) == strip(p2) and strip(k1) == strip(k2)
    assert p1["pack_id"] != p3["pack_id"]
    kinds = [i["kind"] for i in p1["items"]]
    assert kinds == ["pairwise"] * 8 + ["absolute"] * 5
    assert [i["n"] for i in p1["items"]] == list(range(1, 14))
    assert set(k1["items"]) == {i["item_id"] for i in p1["items"]}


def test_eval_13_pack_is_blinded(run, tmp_path):
    pack, out = export_pack(run, cfg(), out_dir=tmp_path / "pack", n_pairs=12, n_docs=6)
    files = sorted(p.name for p in out.iterdir())
    assert files == ["labels.csv", "pack.html", "pack.json", "pack.md"]  # no key in the pack folder
    assert key_path(run, pack["pack_id"]).exists()
    secrets = list(GENERATORS) + [g.version for g in GENS] + [f"tiny-0{i}" for i in (1, 2, 3)] + ["label_a", "generator"]
    for f in out.iterdir():
        text = f.read_text(encoding="utf-8")
        for s in secrets:
            assert s not in text, f"{s!r} leaks into {f.name}"
    # the documents are exactly the judge's anonymised Markdown
    key = load_keys(run)[pack["pack_id"]]
    item = next(i for i in pack["items"] if i["kind"] == "pairwise")
    k = key["items"][item["item_id"]]
    assert item["doc_a"] == pairwise.normalised_markdown(run.load_result(k["label_a"], k["case_id"], k["rep"])).strip()
    # A/B sides are random, not always the configured order
    sides = {key["items"][i["item_id"]]["label_a"] == key["items"][i["item_id"]]["pair"][0]
             for i in pack["items"] if i["kind"] == "pairwise"}
    assert sides == {True, False}
    html = (out / "pack.html").read_text()
    assert "__PACK_JSON__" not in html and "</script>" in html and pack["pack_id"] in html
    assert "<script src" not in html and "http" not in html.split("</style>")[0]  # offline, single file


def test_eval_13_no_pack_from_heldout_runs(run):
    run.touch_manifest(case_set="heldout")
    with pytest.raises(CalibrationError, match="held-out"):
        build_pack(run, cfg())


# ---------------------------------------------------------------- import validation


@pytest.fixture
def pack(run, tmp_path):
    p, out = export_pack(run, cfg(), out_dir=tmp_path / "pack", n_pairs=10, n_docs=6)
    return run, load_keys(run)[p["pack_id"]], out


def test_eval_13_import_valid_labels(pack, tmp_path):
    run, key, _ = pack
    store, rows = import_labels(write_labels(tmp_path, judge_labels(run, key, "owner")), runs_dir=run.root)
    assert store.run_id == run.run_id and len(rows) == 16
    stored = read_labels(run)
    assert len(stored) == 16 and {r["labeller"] for r in stored} == {"owner"}
    pw = next(r for r in stored if r["kind"] == "pairwise")
    k = key["items"][pw["item_id"]]
    for q, v in pw["verdicts"].items():  # verdicts are mapped to the configured pair (x, y)
        gen = k["label_a"] if v == "A" else k["label_b"] if v == "B" else None
        assert pw["winners"][q] == ("tie" if gen is None else "x" if gen == k["pair"][0] else "y")


def test_eval_13_import_rejects_wrong_pack(pack, tmp_path):
    run, key, _ = pack
    body = judge_labels(run, key, "owner")
    body["pack_id"] = "pk-0000000000"
    with pytest.raises(CalibrationError, match="wrong pack"):
        import_labels(write_labels(tmp_path, body), runs_dir=run.root)
    with pytest.raises(CalibrationError, match="wrong pack"):
        validate(body, key)


def test_eval_13_import_rejects_missing_items(pack, tmp_path):
    run, key, _ = pack
    body = judge_labels(run, key, "owner")
    body["labels"] = body["labels"][:-3]
    body["labels"][0]["verdicts"].pop("overall")  # half-filled counts as missing
    with pytest.raises(CalibrationError, match="4 item"):
        import_labels(write_labels(tmp_path, body), runs_dir=run.root)
    assert read_labels(run) == []
    _, rows = import_labels(write_labels(tmp_path, body), runs_dir=run.root, allow_missing=True)
    assert len(rows) == 12


def test_eval_13_import_rejects_duplicate_labeller(pack, tmp_path):
    run, key, _ = pack
    path = write_labels(tmp_path, judge_labels(run, key, "owner"))
    import_labels(path, runs_dir=run.root)
    with pytest.raises(CalibrationError, match="already imported"):
        import_labels(path, runs_dir=run.root)
    import_labels(write_labels(tmp_path, judge_labels(run, key, "second"), "b.json"), runs_dir=run.root)
    import_labels(path, runs_dir=run.root, replace=True)
    rows = read_labels(run)
    assert len(rows) == 32 and sorted({r["labeller"] for r in rows}) == ["owner", "second"]


def test_eval_13_import_rejects_bad_values(pack, tmp_path):
    run, key, _ = pack
    body = judge_labels(run, key, "owner")
    pw = next(lab for lab in body["labels"] if lab["kind"] == "pairwise")
    ab = next(lab for lab in body["labels"] if lab["kind"] == "absolute")
    pw["verdicts"]["overall"] = "C"
    ab["scores"]["c2_coherence"] = 6
    body["labels"].append(dict(ab, item_id="item-ffffffff"))
    body["labels"].append(dict(body["labels"][-2]))
    with pytest.raises(CalibrationError) as e:
        validate(body, key)
    msg = str(e.value)
    assert "must be A, B or tie" in msg and "integer from 1 to 5" in msg
    assert "not an item of pack" in msg and "more than once" in msg
    body = judge_labels(run, key, "bad id!")
    with pytest.raises(CalibrationError, match="labeller ID"):
        validate(body, key)


def test_eval_13_import_csv_fallback(pack, tmp_path):
    run, key, out = pack
    body = judge_labels(run, key, "owner")
    by_id = {lab["item_id"]: lab for lab in body["labels"]}
    rows = list(csv.DictReader((out / "labels.csv").open()))
    assert list(rows[0]) == list(CSV_COLUMNS) and len(rows) == 16
    for r in rows:
        lab = by_id[r["item_id"]]
        r.update({k: str(v) for k, v in (lab.get("verdicts") or lab.get("scores")).items()})
    filled = tmp_path / "filled.csv"
    with filled.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(CalibrationError, match="--labeller"):
        import_labels(filled, runs_dir=run.root)
    _, imported = import_labels(filled, labeller="owner", runs_dir=run.root)
    assert len(imported) == 16
    with pytest.raises(CalibrationError, match="saved by labeller"):
        parse_file(write_labels(tmp_path, body), labeller="someone-else")


# ---------------------------------------------------------------- calibration


def test_eval_13_single_labeller_mode(pack, tmp_path):
    run, key, _ = pack
    import_labels(write_labels(tmp_path, judge_labels(run, key, "owner")), runs_dir=run.root)
    s = calibrate(run, cfg())
    assert s["status"] == "insufficient"  # 10 pairs and 6 documents, below 40 and 20
    assert s["labellers"] == ["owner"] and "single labeller" in s["provisional"] and "fake run" in s["provisional"]
    assert s["pairwise"]["human_vs_human"] == [] and s["absolute"]["human_vs_human"] == []
    o = s["pairwise"]["questions"]["overall"]
    assert o["agreement"]["value"] in (None, 1.0) and o["raw_agreement"]["value"] == pytest.approx(1.0)
    assert all(s["absolute"]["criteria"][c]["exact"] == pytest.approx(1.0) for c in absolute.CRITERIA)
    text = "\n".join(render_section(s))
    assert "NOT ENOUGH LABELS" in text and "Not computable: only one labeller" in text and "Single-labeller" in text

    s = calibrate(run, cfg(), recalibration=True)
    assert s["thresholds"]["min_pairwise_labels"] == 10 and s["thresholds"]["min_absolute_labels"] == 10
    s = calibrate(run, cfg(min_pairwise_labels=10, min_absolute_labels=6, gated_questions=["q3_purpose"]))
    q3 = s["pairwise"]["questions"]["q3_purpose"]
    assert q3["non_tie_items"] > 0 and q3["agreement"]["value"] == pytest.approx(1.0)
    assert s["status"] == "pass" and s["provisional"]


def test_eval_13_two_labellers_and_failure(pack, tmp_path):
    run, key, _ = pack
    pair_ids = [i for i, k in key["items"].items() if k["kind"] == "pairwise"]
    import_labels(write_labels(tmp_path, judge_labels(run, key, "owner", flip=set(pair_ids))), runs_dir=run.root)
    import_labels(write_labels(tmp_path, judge_labels(run, key, "second", flip=set(pair_ids), score_shift=1), "b.json"),
                  runs_dir=run.root)
    c = cfg(min_pairwise_labels=10, min_absolute_labels=6, gated_questions=["q3_purpose"])
    s = calibrate(run, c)
    assert s["labellers"] == ["owner", "second"] and s["provisional"] == ["fake run"]
    q3 = s["pairwise"]["questions"]["q3_purpose"]
    assert q3["agreement"]["value"] == pytest.approx(0.0)  # humans always pick the side the judge did not
    assert s["status"] == "fail" and not s["pairwise_ok"]
    hh = s["pairwise"]["human_vs_human"][0]
    assert hh["labellers"] == ["owner", "second"] and hh["questions"]["overall"]["raw_agreement"]["value"] == pytest.approx(1.0)
    assert len(s["absolute"]["human_vs_human"]) == 1
    assert s["absolute"]["criteria"]["c1_accuracy"]["pairs"] == 12  # 6 documents x 2 labellers
    only = calibrate(run, c, labellers=["owner"])
    assert only["labellers"] == ["owner"] and "single labeller" in only["provisional"]


def test_eval_13_cli_flow_and_report(run, tmp_path, capsys):
    common = ["--runs-dir", str(run.root), "--scoreboard-dir", str(tmp_path / "sb")]
    assert main(["label", "export", "--run-id", run.run_id, "--pairs", "6", "--documents", "4",
                 "--out", str(tmp_path / "pk"), *common]) == 0
    key = next(iter(load_keys(run).values()))
    labels = write_labels(tmp_path, judge_labels(run, key, "owner"))
    assert main(["label", "import", str(labels), *common]) == 0
    assert main(["label", "import", str(labels), *common]) == 2  # duplicate labeller
    assert "already imported" in capsys.readouterr().err
    assert main(["calibrate", "--run-id", run.run_id, "--resamples", "100", "--fail-on-miss", *common]) == 1
    assert (run.path / "calibration.md").exists() and load_calibration(run)["status"] == "insufficient"
    assert run.read_manifest()["calibration"]["status"] == "insufficient"
    assert main(["report", "--run-id", run.run_id, *common]) == 0
    report = (run.path / "report.md").read_text()
    assert "## Judge calibration (EVAL-13)" in report and "Stale" not in report
    summary = json.loads((tmp_path / "sb" / f"{run.run_id}.summary.json").read_text())
    assert summary["calibration"]["status"] == "insufficient"


def test_eval_13_write_calibration_roundtrip(pack, tmp_path):
    run, key, _ = pack
    import_labels(write_labels(tmp_path, judge_labels(run, key, "owner")), runs_dir=run.root)
    s = calibrate(run, cfg())
    j, m = write_calibration(run, s)
    assert json.loads(Path(j).read_text())["status"] == s["status"]
    assert Path(m).read_text().startswith(f"# Calibration of run `{run.run_id}`")
