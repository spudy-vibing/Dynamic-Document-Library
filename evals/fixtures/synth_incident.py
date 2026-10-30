"""Seeded synthetic incidents for postmortem cases (F5).

A pure function of ``seed`` and its options (EVAL-7). An incident has a timeline of
timestamped events (UTC, ``HH:MM``), impact figures, a root cause, contributing factors and
action items. The incident is named by a codename, never by a number, and the date's day
is 1-9, so no identifier or date leaks a figure into the prose.

Timeline times are written ``14:07``; the number grader masks clock times, so they are
checked by the claim checker, not by UNR. Durations between any two events are correct
arithmetic, so every pairwise gap in minutes is in truth "derived" and the case allows them
(``minutes_<a>_to_<b>``, plus the named ``time_to_detect_min``, ``time_to_mitigate_min``
and ``duration_min``).

Traps (keys as in truth.json):
  T1 ``failed_request_share_pct`` (failed / total requests x 100) is derivable, not given.
  T3 (``unit=True``) ``sla_credits_usd`` is a raw figure like 18400 ("$18.4K").
  T4 (``missing=True``) ``customers_affected`` is null: the count is still being reconciled.
  T5 every incident is bad news; spin ("impressive response") is a tone failure.
"""

from __future__ import annotations

import random
from typing import Any

from evals.fixtures.synth_company import COMPANIES, PEOPLE, Fixture, _odd_int, _tenths, distinct

YEAR = 2026
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September")
CODENAMES = ("Halberd", "Saltmarsh", "Ferrule", "Gannet", "Kiln", "Thistle", "Larkspur", "Bramble")

SERVICES = {
    # slug: (name, alert, root cause, contributing factors)
    "checkout": (
        "checkout API",
        "CheckoutErrorRateHigh",
        "Release {rel} lowered the database connection-pool limit, so checkout requests queued and timed out under normal weekday load.",
        ("The pool limit was changed in a shared config file without a review from the payments team.",
         "The canary stage ran at off-peak traffic, so the queueing never showed before full rollout."),
    ),
    "search": (
        "search indexer",
        "SearchIndexLagCritical",
        "Release {rel} changed the index mapping, so the indexer rejected documents with the old field type and search results went stale.",
        ("The mapping change had no migration step in the deploy checklist.",
         "The lag alert threshold was set high enough that it fired only after customers noticed."),
    ),
    "auth": (
        "auth gateway",
        "AuthGatewayLoginFailures",
        "Release {rel} rotated the token-signing key before the new key had reached every gateway node, so half the nodes rejected valid sessions.",
        ("Key distribution and key activation were one step instead of two.",
         "The health check tested only the gateway's own endpoint, not a real login."),
    ),
    "webhooks": (
        "billing webhooks",
        "WebhookDeliveryFailureRate",
        "Release {rel} set a retry header the payment provider rejects, so outgoing billing webhooks failed and were not retried.",
        ("Contract tests against the provider's sandbox were skipped for header-only changes.",
         "The dead-letter queue had no alert, so failed deliveries piled up silently."),
    ),
}

EVENTS = (
    # key, text template
    ("deploy", "Release {rel} of the {service} starts rolling out to production."),
    ("impact_start", "Error rates on the {service} begin to climb."),
    ("alert", "The {alert} alert fires and pages the on-call engineer."),
    ("acknowledged", "{oncall} acknowledges the page and opens an incident channel."),
    ("rollback_start", "The team decides to roll back release {rel}."),
    ("mitigated", "The rollback completes and error rates return to baseline."),
    ("resolved", "{ic} declares the incident resolved after monitoring stays clean."),
)

WENT_WELL = (
    "The rollback runbook worked first time.",
    "The incident channel kept a clear written timeline.",
    "Customer support had a holding statement out quickly.",
    "The on-call handover was clean.",
)
WENT_BADLY = (
    "Detection relied on a single alert with a high threshold.",
    "The status page was updated well after customers started reporting errors.",
    "Nobody owned the shared config file that changed.",
    "The rollback needed a manual approval that took time to find.",
)
ACTIONS = (
    "Add a review rule for changes to the shared config file",
    "Run canary stages at peak traffic, not off-peak",
    "Lower the alert threshold and add a second, symptom-based alert",
    "Make rollback approval automatic for severity-two incidents",
    "Add contract tests for header and config-only changes",
    "Alert on dead-letter queue growth",
)


def _clock(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def incident(seed: int, *, unit: bool = False, missing: bool = False) -> Fixture:
    rng = random.Random(seed)
    company = rng.choice(COMPANIES)
    slug = rng.choice(sorted(SERVICES))
    service, alert, cause, factors = SERVICES[slug]
    codename = rng.choice(CODENAMES)
    month = rng.randrange(len(MONTHS))
    day = rng.randint(1, 9)
    rel = f"{rng.randint(3, 9)}.{rng.randint(11, 40)}.{rng.randint(1, 9)}"
    oncall, ic, *owners = rng.sample(PEOPLE, 6)

    gaps = [  # minutes after the previous event
        rng.randint(4, 14),  # deploy -> impact
        rng.randint(6, 19),  # impact -> alert
        rng.randint(3, 12),  # alert -> acknowledged
        rng.randint(11, 27),  # acknowledged -> rollback decided
        rng.randint(13, 31),  # rollback -> mitigated
        rng.randint(21, 44),  # mitigated -> resolved
    ]
    start = rng.randint(8 * 60, 19 * 60)
    times = [start]
    for g in gaps:
        times.append(times[-1] + g)
    t = dict(zip((k for k, _ in EVENTS), times))

    minutes: dict[str, dict[str, Any]] = {}
    keys = [k for k, _ in EVENTS]
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            minutes[f"minutes_{a}_to_{b}"] = {"value": t[b] - t[a], "formula": f"{b} - {a} (minutes)"}
    named = {
        "time_to_detect_min": ("alert", "impact_start"),
        "time_to_mitigate_min": ("mitigated", "impact_start"),
        "duration_min": ("resolved", "impact_start"),
    }
    allowed_minutes = {v["value"] for v in minutes.values()}

    for _ in range(1000):
        total = rng.randint(1_200_000, 6_900_000)
        failed = int(total * rng.uniform(0.04, 0.31))
        share = round(failed / total * 100, 1)
        peak = _tenths(rng, 21, 78)
        customers = _odd_int(rng, 140, 4900)
        credits = int(round(_tenths(rng, 12, 95) * 10)) * 100 if unit else None
        values: dict[str, Any] = {
            "requests_total": total,
            "requests_failed": failed,
            "peak_error_rate_pct": peak,
            "customers_affected": None if missing else customers,
        }
        if unit:
            values["sla_credits_usd"] = credits
        check = {k: v for k, v in values.items() if v is not None}
        check["failed_request_share_pct"] = share
        if (
            failed % 10
            and share % 1
            and distinct(check)
            and not any(abs(share - m) <= 0.02 * max(share, m) for m in allowed_minutes)
            and not any(abs(peak - m) <= 0.02 * max(peak, m) for m in allowed_minutes)
            and (credits is None or credits % 1000)
        ):
            break
    else:  # pragma: no cover
        raise RuntimeError(f"seed {seed}: could not draw distinct figures")

    fmt = {"rel": rel, "service": service, "alert": alert, "oncall": oncall, "ic": ic}
    timeline = [{"time_utc": _clock(t[k]), "event": text.format(**fmt)} for k, text in EVENTS]
    derived = {
        **{k: {"value": t[a] - t[b], "formula": f"{a} - {b} (minutes)"} for k, (a, b) in named.items()},
        **minutes,
        "failed_request_share_pct": {"value": share, "formula": "requests_failed / requests_total * 100"},
    }
    impact: dict[str, Any] = dict(values)
    notes = [f"Root cause: {cause.format(rel=rel)}"]
    if missing:
        notes.append("The number of affected customers is still being reconciled with the billing system; no count is available yet.")
    if unit:
        notes.append("SLA credits are owed to enterprise customers whose monthly availability fell below the contracted level.")

    date = f"{day} {MONTHS[month]} {YEAR}"
    data = {
        "company": company,
        "incident": f"the {codename} incident",
        "service": service,
        "severity": "SEV-2",
        "date": date,
        "timezone": "UTC",
        "timeline": timeline,
        "impact": impact,
        "root_cause": cause.format(rel=rel),
        "contributing_factors": list(factors),
        "went_well": rng.sample(WENT_WELL, 2),
        "went_badly": rng.sample(WENT_BADLY, 2),
        "action_items": [{"action": a, "owner": o} for a, o in zip(rng.sample(ACTIONS, 4), owners)],
        "notes": notes,
    }
    labels: dict[str, list[str]] = {
        "requests_total": ["total requests", "requests"],
        "requests_failed": ["failed requests", "requests failed", "failed"],
        "peak_error_rate_pct": ["peak error rate", "error rate"],
        "customers_affected": ["customers affected", "affected customers", "customers"],
    }
    if unit:
        labels["sla_credits_usd"] = ["SLA credits", "service credits", "credits"]
    truth = {
        "values": values,
        "labels": labels,
        "derived": derived,
        "facts": {
            "company": company,
            "codename": codename,
            "service": service,
            "date": date,
            "release": rel,
            "root_cause": cause.format(rel=rel),
            "incident_start_utc": _clock(t["impact_start"]),
            "alert_utc": _clock(t["alert"]),
            "mitigated_utc": _clock(t["mitigated"]),
            "resolved_utc": _clock(t["resolved"]),
        },
    }
    info = {"codename": codename, "service": service, "events": len(timeline), "unit": unit, "missing": missing,
            "minute_keys": [*named, *minutes]}
    return Fixture(entity=company, data=data, truth=truth, info=info)
