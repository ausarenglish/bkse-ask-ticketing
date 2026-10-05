"""Independent ground truth for evals/cases.json.

Each answerable case is computed with plain Python loops over the generated
records (ask_ticketing.data.generate_dataset), NOT by running the reference
SQL. Tests then check that reference SQL against a real database agrees.

    uv run python evals/ground_truth.py           # print computed answers
    uv run python evals/ground_truth.py --write   # store them in cases.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

from ask_ticketing.data import AS_OF, Dataset, generate_dataset

CASES_PATH = Path(__file__).with_name("cases.json")


def _sold(ds: Dataset):
    return [t for t in ds.tickets if t.status == "sold"]


def _sold_by_event(ds: Dataset) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for t in _sold(ds):
        out[t.event_id].append(t)
    return out


def _in(d: str, start: str, end: str) -> bool:
    return start <= d <= end


def nets_tickets_sep_2026_by_event_date(ds):
    events = {e.event_id for e in ds.events if e.home_team == "Brooklyn Nets" and _in(e.event_date, "2026-09-01", "2026-09-30")}
    return ["tickets_sold"], [[sum(1 for t in _sold(ds) if t.event_id in events)]]


def purchases_sep_2026_any_event(ds):
    rows = [t for t in _sold(ds) if _in(t.purchase_date, "2026-09-01", "2026-09-30")]
    return ["tickets_sold", "revenue_cents"], [[len(rows), sum(t.price_cents for t in rows)]]


def top5_barclays_revenue_2026_jan_sep(ds):
    sold = _sold_by_event(ds)
    rows = []
    for e in ds.events:
        if e.venue_id == 1 and _in(e.event_date, "2026-01-01", "2026-09-30"):
            ts = sold[e.event_id]
            rows.append([e.event_id, e.name, e.event_date, len(ts), sum(t.price_cents for t in ts)])
    rows.sort(key=lambda r: (-r[4], r[2], r[0]))
    return ["event_id", "name", "event_date", "tickets_sold", "revenue_cents"], rows[:5]


def nets_vs_liberty_2026_jan_sep(ds):
    sold = _sold_by_event(ds)
    rows = []
    for team in ("Brooklyn Nets", "New York Liberty"):
        events = [e for e in ds.events if e.home_team == team and _in(e.event_date, "2026-01-01", "2026-09-30")]
        ts = [t for e in events for t in sold[e.event_id]]
        rows.append([team, len(events), len(ts), sum(t.price_cents for t in ts)])
    return ["home_team", "games", "tickets_sold", "revenue_cents"], rows


def upcoming_by_remaining_inventory(ds):
    sold = _sold_by_event(ds)
    venue = {v.venue_id: v.name for v in ds.venues}
    rows = []
    for e in ds.events:
        if e.event_date > AS_OF.isoformat():
            n = len(sold[e.event_id])
            rows.append([e.event_id, e.name, venue[e.venue_id], e.event_date, e.capacity, n, e.capacity - n])
    rows.sort(key=lambda r: (-r[6], r[3], r[0]))
    return ["event_id", "name", "venue", "event_date", "capacity", "tickets_sold", "remaining_inventory"], rows


def category_revenue_2026_jan_sep(ds):
    sold = _sold_by_event(ds)
    agg: dict[str, list[int]] = {}
    for e in ds.events:
        if _in(e.event_date, "2026-01-01", "2026-09-30"):
            a = agg.setdefault(e.category, [0, 0])
            a[0] += len(sold[e.event_id])
            a[1] += sum(t.price_cents for t in sold[e.event_id])
    rows = [[c, n, rev] for c, (n, rev) in agg.items()]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return ["category", "tickets_sold", "revenue_cents"], rows


def barclays_2024_highest_avg_price(ds):
    sold = _sold_by_event(ds)
    best = None
    for e in ds.events:
        ts = sold[e.event_id]
        if e.venue_id == 1 and _in(e.event_date, "2024-01-01", "2024-12-31") and ts:
            avg = Fraction(sum(t.price_cents for t in ts), len(ts))  # ticket-weighted, exact
            key = (-avg, e.event_id)
            if best is None or key < best[0]:
                best = (key, e, len(ts), avg)
    _, e, n, avg = best
    return ["event_id", "name", "event_date", "tickets_sold", "avg_ticket_price_usd"], [
        [e.event_id, e.name, e.event_date, n, round(float(avg) / 100, 2)]
    ]


def zero_sales_sep_2026(ds):
    venue = {v.venue_id: v.name for v in ds.venues}
    rows = []
    for e in ds.events:
        if _in(e.event_date, "2026-09-01", "2026-09-30"):
            ts = [t for t in ds.tickets if t.event_id == e.event_id]
            if not any(t.status == "sold" for t in ts):
                rows.append([e.event_id, e.name, venue[e.venue_id], e.event_date, sum(t.status == "refunded" for t in ts)])
    rows.sort(key=lambda r: (r[3], r[0]))
    return ["event_id", "name", "venue", "event_date", "refunded_tickets"], rows


def no_match_liberty_jan_2026(ds):
    sold = _sold_by_event(ds)
    rows = [
        [e.event_id, e.name, e.event_date, len(sold[e.event_id])]
        for e in ds.events
        if e.home_team == "New York Liberty" and _in(e.event_date, "2026-01-01", "2026-01-31")
    ]
    return ["event_id", "name", "event_date", "tickets_sold"], rows


def no_match_nets_jul_2026(ds):
    sold = _sold_by_event(ds)
    rows = [
        [e.event_id, e.name, e.event_date, len(sold[e.event_id])]
        for e in ds.events
        if e.home_team == "Brooklyn Nets" and _in(e.event_date, "2026-07-01", "2026-07-31")
    ]
    return ["event_id", "name", "event_date", "tickets_sold"], rows


CALCULATORS = {
    f.__name__: f
    for f in (
        nets_tickets_sep_2026_by_event_date,
        purchases_sep_2026_any_event,
        top5_barclays_revenue_2026_jan_sep,
        nets_vs_liberty_2026_jan_sep,
        upcoming_by_remaining_inventory,
        category_revenue_2026_jan_sep,
        barclays_2024_highest_avg_price,
        zero_sales_sep_2026,
        no_match_liberty_jan_2026,
        no_match_nets_jul_2026,
    )
}


def compute_all(ds: Dataset | None = None) -> dict[str, tuple[list, list]]:
    ds = ds or generate_dataset()
    return {case_id: calc(ds) for case_id, calc in CALCULATORS.items()}


def main(argv: list[str]) -> int:
    results = compute_all()
    if "--write" not in argv:
        print(json.dumps(results, indent=1))
        return 0
    doc = json.loads(CASES_PATH.read_text())
    for case in doc["cases"]:
        if case["id"] in results:
            case["expected_columns"], case["expected_rows"] = results[case["id"]]
    CASES_PATH.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"updated {CASES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
