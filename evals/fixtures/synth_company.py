"""Seeded synthetic companies: quarterly KPI data (F2) and weekly team notes (F1).

Every function here is a pure function of ``seed`` and its options: the same inputs
always give the same ``data`` and ``truth`` dicts (EVAL-7). Entities are fictional and
figures are distinctive (47.3M, 23.4%, 1,284), never round, so a figure a model
invents rarely matches a real one by chance.

Number conventions (shared with ``evals/graders/numbers.py``):

* **Percentages** are stored as percent numbers under keys ending ``_pct``:
  ``"gross_margin_pct": 23.4`` means 23.4%. Prose "23.4%" matches it directly.
* **Money** is stored in raw currency units: ``"revenue_this": 47300000`` is $47.3M.
  Prose "$47.3M", "47.3 million" and "$47,300,000" all normalise to it.
* **Counts** are plain integers (``"active_customers": 1284``).
* A **missing** KPI (trap T4) is ``null`` in both data.json and truth.json "values".

``truth`` follows the schema in ``EvalCase``'s docstring: "values" (every named figure,
same keys as ``data["metrics"]``), "labels" (how prose may name each figure),
"derived" (figures computable from the data, with their formula) and "facts".
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

YEAR = 2026

COMPANIES = (
    "Brightwell Ceramics",
    "Quorvane Analytics",
    "Halcyra Freight",
    "Marrowfield Foods",
    "Tessaline Robotics",
    "Calderwyn Textiles",
    "Ostrel Biosystems",
    "Vellmora Outdoor",
)

GOOD_NOTES = (
    "The self-serve plan launched in the second month of the quarter.",
    "Two regional distributors renewed on multi-year terms.",
    "The warehouse automation pilot moved into full production.",
    "Support response times improved after the new triage rota.",
    "The partner channel signed its first reseller in the Nordics.",
)
BAD_NOTES = (
    "Two enterprise renewals slipped into the next quarter.",
    "A supplier outage delayed shipments for most of the second month.",
    "Price discounting on the legacy line continued to weigh on revenue.",
    "The largest customer reduced its order volume after a reorganisation.",
)
NEUTRAL_NOTES = (
    "Headcount was flat; two open roles in finance remain unfilled.",
    "The board approved the revised pricing review for next quarter.",
    "The ERP migration is on schedule for completion next quarter.",
)

# Missing-KPI options for trap T4: key -> (table label, prose labels, note explaining the gap).
MISSING_KPIS = {
    "nps": ("NPS", ["NPS", "Net Promoter Score", "net promoter"], "The NPS survey was postponed; no score exists for either quarter."),
    "csat_pct": ("CSAT (%)", ["CSAT", "customer satisfaction"], "The CSAT survey vendor changed; no satisfaction score was collected."),
    "pipeline_coverage": ("Pipeline coverage", ["pipeline coverage"], "Pipeline coverage was not reported because the CRM migration is incomplete."),
}

TEAMS = {
    # slug: (display name, special metric key, special metric label(s), generator)
    "platform": ("Platform", "p95_latency_ms", ["p95 latency", "latency"]),
    "payments": ("Payments", "auth_success_pct", ["authorisation success", "auth success", "authorization success"]),
    "mobile": ("Mobile", "crash_free_pct", ["crash-free", "crash free"]),
    "data": ("Data", "pipeline_freshness_min", ["pipeline freshness", "freshness"]),
    "growth": ("Growth", "signup_conversion_pct", ["signup conversion", "sign-up conversion", "conversion"]),
    "support": ("Support", "first_response_min", ["first response", "first-response"]),
}

PEOPLE = (
    "Ines Varga", "Tomasz Okafor", "Priya Halloran", "Mateo Lindqvist", "Ama Brennan",
    "Kenji Moreau", "Sofia Achterberg", "Dario Nwosu", "Lena Castellanos", "Olu Petrakis",
)

PROJECTS = (
    "Lantern", "Kestrel", "Marlow", "Juniper", "Basalt", "Tamarind", "Cobalt", "Wren", "Sorrel", "Quill",
)


@dataclass
class Fixture:
    """What a family generator returns; ``make_cases`` turns it into a case directory."""

    entity: str
    data: dict[str, Any]
    truth: dict[str, Any]
    info: dict[str, Any] = field(default_factory=dict)  # case-building hints (periods, trap keys, ...)
    attachments: dict[str, str] = field(default_factory=dict)  # file name -> text


# ---------------------------------------------------------------- helpers


def distinct(values: dict[str, float | None], *, exempt: tuple[tuple[str, str], ...] = (), rel: float = 0.02) -> bool:
    """True if no two non-null values are within ``rel`` of each other (by magnitude).

    Also rejects integers 0-10 and years 1900-2100, which the number grader excludes.
    ``exempt`` lists pairs that are close on purpose (the T2 near-collision pair).
    """
    items = [(k, abs(float(v))) for k, v in values.items() if v is not None]
    skip = {frozenset(p) for p in exempt}
    for k, v in items:
        if v <= 10 and float(v).is_integer():
            return False
        if 1900 <= v <= 2100 and float(v).is_integer():
            return False
    for i, (ka, a) in enumerate(items):
        for kb, b in items[i + 1 :]:
            if frozenset((ka, kb)) in skip:
                continue
            if abs(a - b) <= rel * max(a, b):
                return False
    return True


def _tenths(rng: random.Random, lo: int, hi: int) -> float:
    """A one-decimal number in [lo, hi) whose tenths digit is never 0 (23.4, never 23.0)."""
    return rng.randint(lo, hi - 1) + rng.randint(1, 9) / 10


def _money(rng: random.Random, lo_m: int, hi_m: int) -> int:
    """Raw currency units, a multiple of 100,000 with a non-zero tenths-of-a-million digit."""
    return int(round(_tenths(rng, lo_m, hi_m) * 10)) * 100_000


def _round_money(x: float) -> int:
    v = int(round(x / 100_000)) * 100_000
    if (v // 100_000) % 10 == 0:
        v += 100_000
    return v


def _odd_int(rng: random.Random, lo: int, hi: int) -> int:
    """An integer that is not a multiple of 10."""
    while True:
        v = rng.randint(lo, hi)
        if v % 10:
            return v


def _quarter(rng: random.Random) -> tuple[str, str]:
    q = rng.randint(1, 4)
    this = f"Q{q} {YEAR}"
    prior = f"Q{q - 1} {YEAR}" if q > 1 else f"Q4 {YEAR - 1}"
    return this, prior


# ---------------------------------------------------------------- F2 quarterly


def quarterly(seed: int, *, bad_news: bool = False, unit: bool = False, missing: bool = True) -> Fixture:
    """One quarter of KPI data for a fictional company.

    Traps seeded (keys as they appear in truth.json):
      T1 ``revenue_growth_pct`` and ``revenue_change`` are derivable (truth "derived") but not given.
      T2 ``gross_margin_pct`` and ``opex_change_pct`` differ by 0.2-0.3 points.
      T3 (``unit=True``) ``deferred_revenue`` is a raw figure like 4200000 ("$4.2M").
      T4 (``missing=True``) one KPI is null in both quarters.
      T5 (``bad_news=True``) revenue fell against the prior quarter.
    """
    rng = random.Random(seed)
    company = rng.choice(COMPANIES)
    period, prior_period = _quarter(rng)
    missing_key = rng.choice(sorted(MISSING_KPIS)) if missing else None

    for _ in range(1000):
        revenue_prior = _money(rng, 18, 97)
        change = rng.uniform(-0.12, -0.035) if bad_news else rng.uniform(0.04, 0.16)
        revenue_this = _round_money(revenue_prior * (1 + change))
        growth = round((revenue_this / revenue_prior - 1) * 100, 1)
        gm = _tenths(rng, 18, 65)
        gm_prior = round(gm + rng.choice((-1, 1)) * rng.uniform(0.4, 2.6), 1)
        opex_change = round(gm + rng.choice((-0.3, -0.2, 0.2, 0.3)), 1)
        customers_prior = _odd_int(rng, 900, 4900)
        customers = customers_prior + (-1 if bad_news else 1) * _odd_int(rng, 23, 180)
        churn_prior = _tenths(rng, 2, 7)
        churn = round(churn_prior + (0.4 if bad_news else -0.3) + rng.randint(1, 4) / 10, 1)
        deferred = _money(rng, 2, 9) if unit else None

        metrics: dict[str, Any] = {
            "revenue_prior": revenue_prior,
            "revenue_this": revenue_this,
            "gross_margin_prior_pct": gm_prior,
            "gross_margin_pct": gm,
            "opex_change_pct": opex_change,
            "active_customers_prior": customers_prior,
            "active_customers": customers,
            "churn_prior_pct": churn_prior,
            "churn_pct": churn,
        }
        if unit:
            metrics["deferred_revenue"] = deferred
        if missing_key:
            metrics[missing_key] = None

        derived = {
            "revenue_growth_pct": {"value": growth, "formula": "(revenue_this / revenue_prior - 1) * 100"},
            "revenue_change": {"value": revenue_this - revenue_prior, "formula": "revenue_this - revenue_prior"},
        }
        check = dict(metrics)
        check.update({k: d["value"] for k, d in derived.items()})
        if (
            float(growth) % 1 != 0
            and float(gm_prior) % 1 != 0
            and float(opex_change) % 1 != 0
            and float(churn) % 1 != 0
            and (revenue_this < revenue_prior) == bad_news
            and distinct(check, exempt=(("gross_margin_pct", "opex_change_pct"),))
        ):
            break
    else:  # pragma: no cover - the ranges make this practically unreachable
        raise RuntimeError(f"seed {seed}: could not draw distinct figures")

    kpis: list[dict[str, Any]] = [
        {"kpi": "Revenue (USD)", prior_period: revenue_prior, period: revenue_this},
        {"kpi": "Gross margin (%)", prior_period: gm_prior, period: gm},
        {"kpi": "Active customers", prior_period: customers_prior, period: customers},
        {"kpi": "Churn (%)", prior_period: churn_prior, period: churn},
    ]
    if missing_key:
        kpis.append({"kpi": MISSING_KPIS[missing_key][0], prior_period: None, period: None})

    notes = rng.sample(BAD_NOTES if bad_news else GOOD_NOTES, 2) + rng.sample(NEUTRAL_NOTES, 1)
    if unit:
        notes.append("Deferred revenue reflects annual contracts invoiced in advance.")
    if missing_key:
        notes.append(MISSING_KPIS[missing_key][2])

    data = {
        "company": company,
        "period": period,
        "prior_period": prior_period,
        "currency": "USD",
        "metrics": metrics,
        "kpis": kpis,
        "notes": notes,
    }

    labels: dict[str, list[str]] = {
        "revenue_prior": [f"{prior_period} revenue", "prior-quarter revenue", "revenue"],
        "revenue_this": [f"{period} revenue", "revenue"],
        "gross_margin_prior_pct": ["prior-quarter gross margin", "gross margin"],
        "gross_margin_pct": ["gross margin", "margin"],
        "opex_change_pct": ["operating expenses", "opex"],
        "active_customers_prior": ["prior-quarter customers", "active customers", "customers"],
        "active_customers": ["active customers", "customers"],
        "churn_prior_pct": ["prior-quarter churn", "churn"],
        "churn_pct": ["churn"],
    }
    if unit:
        labels["deferred_revenue"] = ["deferred revenue"]
    if missing_key:
        labels[missing_key] = MISSING_KPIS[missing_key][1]

    truth = {
        "values": dict(metrics),
        "labels": labels,
        "derived": derived,
        "facts": {"company": company, "period": period, "prior_period": prior_period, "revenue_direction": "down" if bad_news else "up"},
    }
    info = {"period": period, "prior_period": prior_period, "missing_key": missing_key, "unit": unit, "bad_news": bad_news}
    return Fixture(entity=company, data=data, truth=truth, info=info)


# ---------------------------------------------------------------- F1 weekly


def _team_metrics(rng: random.Random, slug: str) -> float | int:
    if slug == "platform":
        return _odd_int(rng, 180, 690)  # p95 latency, ms
    if slug == "payments":
        return round(rng.randint(951, 994) / 10 + rng.randint(1, 9) / 100, 2)  # 97.37
    if slug == "mobile":
        return round(99 + rng.randint(11, 97) / 100, 2)  # 99.62
    if slug == "data":
        return _odd_int(rng, 21, 95)  # minutes
    if slug == "growth":
        return _tenths(rng, 2, 9) + 0.0  # 4.7 (%)
    return _odd_int(rng, 31, 140)  # support first response, minutes


DONE = (
    "Shipped project {p} to all customers",
    "Finished the {p} migration",
    "Closed the {p} security review",
    "Released the {p} beta to internal users",
    "Retired the old {p} service",
)
DOING = (
    "Load testing for {p} is underway",
    "Design review for {p} is in progress",
    "The {p} rollout is at the canary stage",
    "Data backfill for {p} is running",
)
BLOCKERS = (
    "Waiting on legal sign-off for {p}",
    "Blocked on a vendor API change for {p}",
    "Needs a decision from product on the {p} scope",
)
NEXT = (
    "Start the {p} rollout",
    "Complete the {p} review",
    "Hand {p} over to support",
    "Run the {p} load test",
)


def weekly(seed: int, *, missing: bool = False) -> Fixture:
    """Status notes from three teams for one week.

    Each team has tickets closed, open bugs, deploys and one team-specific metric.
    With ``missing=True`` one team's specific metric is null (trap T4). The notes are
    also rendered as plain-text attachments (one per team) using the same figures.
    """
    rng = random.Random(seed)
    company = rng.choice(COMPANIES)
    slugs = sorted(rng.sample(sorted(TEAMS), 3), key=list(TEAMS).index)
    week = rng.randint(11, 49)
    missing_slug = rng.choice(slugs) if missing else None

    for _ in range(1000):
        values: dict[str, Any] = {"week_number": week}
        per_team: dict[str, dict[str, Any]] = {}
        for slug in slugs:
            m = {
                "tickets_closed": _odd_int(rng, 17, 89),
                "open_bugs": _odd_int(rng, 11, 48),
                "deploys": _odd_int(rng, 12, 39),
                TEAMS[slug][1]: _team_metrics(rng, slug),
            }
            if slug == missing_slug:
                m[TEAMS[slug][1]] = None
            per_team[slug] = m
            for k, v in m.items():
                values[f"{slug}_{k}"] = v
        if distinct(values):
            break
    else:  # pragma: no cover
        raise RuntimeError(f"seed {seed}: could not draw distinct figures")

    projects = rng.sample(PROJECTS, 9)
    people = rng.sample(PEOPLE, 3)
    teams = []
    attachments: dict[str, str] = {}
    labels: dict[str, list[str]] = {"week_number": ["week"]}
    for i, slug in enumerate(slugs):
        name, special, special_labels = TEAMS[slug]
        p1, p2, p3 = projects[i * 3 : i * 3 + 3]
        m = per_team[slug]
        team = {
            "team": name,
            "lead": people[i],
            "done": [rng.choice(DONE).format(p=p1), rng.choice(DONE).format(p=p2)],
            "in_progress": [rng.choice(DOING).format(p=p3)],
            "blockers": [rng.choice(BLOCKERS).format(p=p3)] if rng.random() < 0.67 else [],
            "next_week": [rng.choice(NEXT).format(p=p3)],
            "metrics": m,
        }
        if slug == missing_slug:
            team["notes"] = f"The {special_labels[0]} figure is unavailable this week because the dashboard was down."
        teams.append(team)
        labels[f"{slug}_tickets_closed"] = [f"{name} tickets", "tickets closed", "tickets"]
        labels[f"{slug}_open_bugs"] = [f"{name} open bugs", "open bugs", "bugs"]
        labels[f"{slug}_deploys"] = [f"{name} deploys", "deploys", "deployments"]
        labels[f"{slug}_{special}"] = list(special_labels)
        attachments[f"{slug}_notes.md"] = _team_note(team, special, special_labels[0], week)

    team_metrics = [
        {"team": t["team"], "tickets_closed": t["metrics"]["tickets_closed"], "open_bugs": t["metrics"]["open_bugs"], "deploys": t["metrics"]["deploys"]}
        for t in teams
    ]
    data = {
        "company": company,
        "week": f"{YEAR}-W{week}",
        "week_number": week,
        "teams": teams,
        "team_metrics": team_metrics,
    }
    truth = {
        "values": values,
        "labels": labels,
        "derived": {
            "tickets_closed_total": {
                "value": sum(t["metrics"]["tickets_closed"] for t in teams),
                "formula": " + ".join(f"{s}_tickets_closed" for s in slugs),
            },
            "deploys_total": {
                "value": sum(t["metrics"]["deploys"] for t in teams),
                "formula": " + ".join(f"{s}_deploys" for s in slugs),
            },
        },
        "facts": {"company": company, "week": data["week"], **{f"{s}_lead": t["lead"] for s, t in zip(slugs, teams)}},
    }
    info = {
        "slugs": slugs,
        "names": [TEAMS[s][0] for s in slugs],
        "missing_key": f"{missing_slug}_{TEAMS[missing_slug][1]}" if missing_slug else None,
        "week": week,
    }
    return Fixture(entity=company, data=data, truth=truth, info=info, attachments=attachments)


def _fmt_metric(key: str, v: Any) -> str:
    if v is None:
        return "not available"
    if key.endswith("_pct"):
        return f"{v}%"
    if key.endswith("_ms"):
        return f"{v} ms"
    if key.endswith("_min"):
        return f"{v} minutes"
    return str(v)


def _team_note(team: dict[str, Any], special: str, special_label: str, week: int) -> str:
    m = team["metrics"]
    lines = [
        f"{team['team']} team notes, week {week} (lead: {team['lead']})",
        "",
        "Done:",
        *[f"- {d}" for d in team["done"]],
        "",
        "In progress:",
        *[f"- {d}" for d in team["in_progress"]],
        "",
        "Blockers:",
        *([f"- {b}" for b in team["blockers"]] or ["- None"]),
        "",
        "Next week:",
        *[f"- {n}" for n in team["next_week"]],
        "",
        f"Numbers: {m['tickets_closed']} tickets closed, {m['open_bugs']} open bugs, {m['deploys']} deploys, "
        f"{special_label} {_fmt_metric(special, m[special])}.",
    ]
    if team.get("notes"):
        lines.append(team["notes"])
    return "\n".join(lines) + "\n"
