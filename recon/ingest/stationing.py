"""Cross-check as-built span footages against the drawing's fiber sequentials.

Contractors station their routes: every aerial span comment carries the sequential
(station) where the span begins, and the footage written on it is the cable placed
running **forward** from that station. Those stations are therefore a **checksum on
the footage** — the distance to the next station equals what that span placed, plus
anything else on it that consumes stationing (slack coils, up / down-pole risers)::

    station[n+1] - station[n] == span_ft[n] + extra_ft[n]

Each gap is checked against **exactly one** span, so a mis-keyed footage can't hide
behind its neighbour. Which of the two owns the gap is a per-route convention, and
it is not the same on every route: most state the footage running forward from the
span's own station, but some state the run arriving at it. The direction is settled
once per route by seeing which way its chain reconciles, then applied to every gap
on that route.

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
    """One span checked against the stationing it is meant to close: the run from
    its own station forward to the next one on the route."""
    route: tuple                  # (cable count, 'F' | 'D' | '')
    page: int                     # the sheet the span being checked is drawn on
    station_from: int             # the span's own station
    station_to: int               # the next station on the route
    gap: float                    # what the stationing says was built
    stated: float                 # what this span claims (span + coils/risers)
    ok: bool
    delta: float = 0.0            # stated - gap; signed, so over/under is visible
    raw: str = ""                 # the comment text, for the reviewer

    @property
    def route_label(self) -> str:
        count, tag = self.route
        name = {"F": "feeder", "D": "distribution"}.get(tag, "")
        return f"{count}ct {name}".strip() if count else (name or "route")


#: A span's footage closes its chain gap exactly — verified against the drawing.
VERIFIED = "verified"
#: It doesn't close the chain gap, but it does equal a real distance between two
#: sequentials on its route — usually a break in the chain (a span drawn on another
#: sheet), not a bad footage.
PLAUSIBLE = "plausible"
#: It matches no distance between any two sequentials on the route. Most likely a
#: mis-stated footage — review these first.
UNVERIFIED = "unverified"


@dataclass
class SpanVerdict:
    """What the drawing says about one span's footage."""
    page: int
    route: tuple
    station: int
    span_ft: float
    verdict: str

    @property
    def route_label(self) -> str:
        count, tag = self.route
        name = {"F": "feeder", "D": "distribution"}.get(tag, "")
        return f"{count}ct {name}".strip() if count else (name or "route")


@dataclass
class StationingReport:
    checks: list = field(default_factory=list)      # list[SpanCheck]
    unverifiable: int = 0        # spans with no neighbour to check against
    verdicts: list = field(default_factory=list)    # list[SpanVerdict]
    conduit: list = field(default_factory=list)     # list[ConduitCheck]

    @property
    def conduit_failures(self) -> list:
        return [c for c in self.conduit if not c.ok]

    def by_verdict(self, verdict: str) -> list:
        return [v for v in self.verdicts if v.verdict == verdict]

    @property
    def unverified(self) -> list:
        """The spans worth a supervisor's attention first."""
        return self.by_verdict(UNVERIFIED)

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
        if not self.verdicts:
            return "No span stationing available to cross-check."
        n_v = len(self.by_verdict(VERIFIED))
        n_u = len(self.unverified)
        head = (f"{n_v} of {len(self.verdicts)} span footages verified against the "
                f"drawing's sequentials")
        return head + (f" — {n_u} match no distance on their route and need a look."
                       if n_u else ", and none are unaccounted for.")


# How a route writes its spans. Contractors are consistent within a route but not
# between them, so the convention is read off each route's own chain:
#   ahead    — the footage is the run *arriving* at the span's station, not leaving it
#   coil_in  — a coil's length is already inside the span footage (the span is
#              measured to where the coil ends) rather than added on top of it
_CONVENTIONS = ((False, False), (True, False), (False, True), (True, True))


def _measure(a, b, ahead: bool, coil_in: bool, coil_ft):
    """The run between two spans and the footage claimed to have built it."""
    if coil_in:
        gap = float(b.end - a.end)
        extra = 0.0
    else:
        gap = float(b.station - a.station)
        extra = coil_ft(a.station, b.station)
    owner = b if ahead else a
    return gap, owner.span_ft + owner.extra_ft + extra, owner


def _best_convention(ordered: list, coil_ft) -> tuple:
    """The (ahead, coil_in) reading that this route's own chain supports best."""
    best, best_hits = _CONVENTIONS[0], -1
    for conv in _CONVENTIONS:
        hits = sum(1 for a, b in zip(ordered, ordered[1:])
                   if (lambda g, s, _o: g == s)(*_measure(a, b, *conv, coil_ft)))
        if hits > best_hits:
            best, best_hits = conv, hits
    return best


def _untagged_remap(span_records: list) -> dict:
    """Where untagged comments really belong. Contractors tag most spans with the
    feeder/distribution route but not every one; an untagged span still sits among
    the stations of a real route, so fold it into the same-cable route whose station
    range it falls closest to. Returns the routes to rewrite."""
    by_route: dict = {}
    for s in span_records:
        by_route.setdefault(s.route, []).append(s)
    ranges = {r: (min(x.station for x in v), max(x.station for x in v))
              for r, v in by_route.items() if r[1]}

    remap: dict = {}
    for route, spans in by_route.items():
        count, tag = route
        if tag:
            continue
        cands = [r for r in ranges if r[0] == count]
        if not cands:
            continue
        centre = sum(x.station for x in spans) / len(spans)

        def distance(r, _c=centre):
            lo, hi = ranges[r]
            return 0 if lo <= _c <= hi else min(abs(_c - lo), abs(_c - hi))

        remap[route] = min(cands, key=distance)
    return remap


@dataclass
class ConduitCheck:
    """One buried run's conduit, checked against the stationing. The pipe reaches
    from where the previous run exited to where this one enters."""
    route: tuple
    page: int
    station_from: int
    station_to: int
    gap: float                    # what the stationing says was placed
    stated: float                 # what the comment claims
    ok: bool
    delta: float = 0.0

    @property
    def route_label(self) -> str:
        count, tag = self.route
        name = {"F": "feeder", "D": "distribution"}.get(tag, "")
        return f"{count}ct {name}".strip() if count else (name or "route")


def check_conduit(buried_runs: list) -> list:
    """Cross-check each buried run's conduit footage against the drawing.

    A run enters the ground at ``In`` and leaves at ``Out``; the conduit that gets
    it there was placed from the previous run's exit, so::

        In[n] - Out[n-1] == Pipe[n]

    Runs are walked in the direction the route is stationed, which differs between
    routes, so the direction is read off each route's own chain."""
    routes: dict = {}
    for r in buried_runs:
        if r.in_sta is not None and r.out_sta is not None:
            routes.setdefault(r.route, []).append(r)

    out: list = []
    for route, runs in routes.items():
        uniq = {(r.in_sta, r.out_sta, r.pipe_ft): r for r in runs}
        ordered = sorted(uniq.values(), key=lambda r: r.in_sta)
        if len(ordered) < 2:
            continue
        # ascending or descending stationing — whichever the chain supports
        fwd = sum(1 for a, b in zip(ordered, ordered[1:])
                  if b.pipe_ft is not None and b.in_sta - a.out_sta == b.pipe_ft)
        rev = sum(1 for a, b in zip(ordered, ordered[1:])
                  if a.pipe_ft is not None and b.out_sta - a.in_sta == a.pipe_ft)
        for a, b in zip(ordered, ordered[1:]):
            if abs(b.page - a.page) > _MAX_PAGE_JUMP:
                continue                            # route left the sheet run
            owner, gap = ((b, b.in_sta - a.out_sta) if fwd >= rev
                          else (a, b.out_sta - a.in_sta))
            if owner.pipe_ft is None:
                continue                            # no conduit claimed on this run
            out.append(ConduitCheck(
                route=route, page=owner.page,
                station_from=(a.out_sta if fwd >= rev else a.in_sta),
                station_to=(b.in_sta if fwd >= rev else b.out_sta),
                gap=float(gap), stated=owner.pipe_ft,
                ok=(gap == owner.pipe_ft), delta=owner.pipe_ft - gap))
    return out


def check_stationing(span_records: list, coil_marks: list | None = None,
                     buried_runs: list | None = None) -> StationingReport:
    """Walk each route in station order and compare every adjacent pair's gap
    against the footage written on the comments, plus any coil sitting inside it.
    Buried runs are checked the same way, against the conduit they needed."""
    report = StationingReport()
    report.conduit = check_conduit(buried_runs or [])
    remap = _untagged_remap(span_records)
    routes: dict = {}
    for s in span_records:
        routes.setdefault(remap.get(s.route, s.route), []).append(s)

    coils: dict = {}
    for c in (coil_marks or []):
        coils.setdefault(remap.get(c.route, c.route), []).append(c)

    # every sequential known on each route, for the fallback plausibility check
    stations: dict = {}
    for s in span_records:
        stations.setdefault(remap.get(s.route, s.route), set()).update(
            (s.station, s.end))
    for c in (coil_marks or []):
        stations.setdefault(remap.get(c.route, c.route), set()).add(c.station)

    verified: set = set()

    for route, spans in routes.items():
        # de-dupe identical records (the same span drawn on two sheets) and order
        # by station so the walk follows the route, not the page order
        uniq = {(s.station, s.span_ft, s.extra_ft): s for s in spans}
        ordered = sorted(uniq.values(), key=lambda s: s.station)
        if len(ordered) < 2:
            report.unverifiable += len(ordered)
            continue

        marks = coils.get(route, ())

        def coil_ft(lo: int, hi: int, _m=marks) -> float:
            """Coil / riser footage sitting inside this gap — a coil belongs to
            whichever gap contains the station it is listed at."""
            return sum(c.ft for c in _m if lo < c.station <= hi)

        conv = _best_convention(ordered, coil_ft)
        for a, b in zip(ordered, ordered[1:]):
            if abs(b.page - a.page) > _MAX_PAGE_JUMP:
                report.unverifiable += 1            # route left the sheet run
                continue
            # exactly one span owns the run, read the way this route writes them,
            # and its footage has to close it on its own
            gap, stated, owner = _measure(a, b, *conv, coil_ft)
            ok = gap == stated
            report.checks.append(SpanCheck(
                route=route, page=owner.page, station_from=a.station,
                station_to=b.station, gap=gap, stated=stated, ok=ok,
                delta=stated - gap, raw=owner.raw))
            if ok:
                verified.add((route, owner.station, owner.span_ft))

    # every span gets a verdict, including those with no neighbour to chain to
    for s in span_records:
        route = remap.get(s.route, s.route)
        key = (route, s.station, s.span_ft)
        if key in verified:
            verdict = VERIFIED
        else:
            known = sorted(stations.get(route, ()))
            reachable = {abs(x - y) for y in (s.station, s.end) for x in known}
            verdict = PLAUSIBLE if s.span_ft in reachable else UNVERIFIED
        report.verdicts.append(SpanVerdict(
            page=s.page, route=route, station=s.station, span_ft=s.span_ft,
            verdict=verdict))
    return report
