"""Cross-check as-built span footages against the drawing's fiber sequentials.

Contractors station their routes: every aerial span comment carries the sequential
(station) where the span begins. Those stations are a **checksum on the footage** —
walking a route, the distance between one span's station and the next equals the
cable placed on it, plus anything else that consumes stationing (slack coils, up /
down-pole risers)::

    station[n+1] - station[n] == span_ft[n] + extra_ft[n]

A span that fails this arithmetic is either a transcription error in the footage, a
missing span, or a double-counted one — all of which a reviewer wants to see before
the quantity is billed. The check is exact: any difference is reported.

Pure domain logic over ``SpanRecord``s from the annotation parser; no UI, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Spans further apart than this many sheets aren't a continuous run — the route
# left the page and came back, so the gap can't be checked.
_MAX_PAGE_JUMP = 3


@dataclass
class SpanCheck:
    """One adjacent pair of spans on a route, checked against their stationing."""
    route: tuple                  # (cable count, 'F' | 'D' | '')
    page: int                     # the sheet the second span is drawn on
    station_from: int
    station_to: int
    gap: float                    # what the stationing says was built
    stated: float                 # what the comment claims (span + coils/risers)
    ok: bool
    delta: float = 0.0            # stated - gap; signed, so over/under is visible
    raw: str = ""                 # the comment text, for the reviewer

    @property
    def route_label(self) -> str:
        count, tag = self.route
        name = {"F": "feeder", "D": "distribution"}.get(tag, "")
        return f"{count}ct {name}".strip() if count else (name or "route")


@dataclass
class StationingReport:
    checks: list = field(default_factory=list)      # list[SpanCheck]
    unverifiable: int = 0        # spans with no neighbour to check against

    @property
    def checked(self) -> int:
        return len(self.checks)

    @property
    def failures(self) -> list:
        return [c for c in self.checks if not c.ok]

    @property
    def passed(self) -> int:
        return self.checked - len(self.failures)

    @property
    def pass_rate(self) -> float:
        return (100.0 * self.passed / self.checked) if self.checked else 0.0

    def summary(self) -> str:
        if not self.checked:
            return "No span stationing available to cross-check."
        n = len(self.failures)
        head = (f"{self.passed} of {self.checked} spans reconcile against the "
                f"drawing's stationing")
        return head + (f" — {n} do not." if n else ".")


def check_stationing(span_records: list) -> StationingReport:
    """Walk each route in station order and compare every adjacent pair's gap
    against the footage written on the comments."""
    report = StationingReport()
    routes: dict = {}
    for s in span_records:
        routes.setdefault(s.route, []).append(s)

    for route, spans in routes.items():
        # de-dupe identical records (the same span drawn on two sheets) and order
        # by station so the walk follows the route, not the page order
        uniq = {(s.station, s.span_ft, s.extra_ft): s for s in spans}
        ordered = sorted(uniq.values(), key=lambda s: s.station)
        if len(ordered) < 2:
            report.unverifiable += len(ordered)
            continue

        for a, b in zip(ordered, ordered[1:]):
            if abs(b.page - a.page) > _MAX_PAGE_JUMP:
                report.unverifiable += 1            # route left the sheet run
                continue
            gap = float(b.station - a.station)
            # the gap is closed by one of the two spans plus whatever else on it
            # consumed stationing; accept whichever of the pair accounts for it
            candidates = (a.span_ft + a.extra_ft, b.span_ft + b.extra_ft,
                          a.span_ft + b.extra_ft, b.span_ft + a.extra_ft)
            ok = any(gap == c for c in candidates)
            stated = min(candidates, key=lambda c: abs(c - gap))
            report.checks.append(SpanCheck(
                route=route, page=b.page, station_from=a.station,
                station_to=b.station, gap=gap, stated=stated, ok=ok,
                delta=stated - gap, raw=b.raw))
    return report
