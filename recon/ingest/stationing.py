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

# Buried runs carry no intermediate sequentials, so their chain can't be walked the
# way an aerial route's can — see the note in check_conduit.
_MAX_BURIED_PAGE_JUMP = 3

# How far a footage can be off its run and still read as a mis-keyed number rather
# than a break. Measured across the four as-builts, failures cluster at either end:
# 16 are within a tenth of their run (+1, +2, -10, +4 ft — plainly typed wrong) and
# 106 are more than a quarter off, which is a route that left the sheet. The cut
# sits in the empty ground between them.
_MISKEY_FRACTION = 0.10


def _longest_claim(items, claim) -> float:
    """The largest single run anyone wrote on this route."""
    return max((claim(i) for i in items if claim(i)), default=0.0)


def _is_break(gap: float, longest: float) -> bool:
    """Whether a run between two sequentials is a break in the chain rather than
    something a span could have built.

    A route leaves the sheet and comes back, so two comments that are neighbours in
    station order are not always neighbours on the ground. The giveaway is size: a
    run longer than the longest span anyone wrote on this route cannot be one span,
    so there is work in between that simply isn't on these sheets — that is not
    something a footage can be blamed for.

    The comparison is deliberately strict rather than padded. A run that *could* be
    one span stays checkable, so an understated footage is flagged rather than
    written off as a break; the cost of guessing wrong that way is a second look,
    while the other way round loses the error entirely.
    """
    return longest > 0 and gap > longest


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
#: The run is the right size but the footage is slightly wrong — the signature of a
#: mis-keyed number rather than a break in the chain. The strongest evidence of a
#: real error the drawing can give, so these come first.
MISKEYED = "miskeyed"
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
    off_by: float = 0.0           # how far off the run, when the verdict is MISKEYED

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
    def miskeyed(self) -> list:
        """Footages the drawing contradicts by a small margin — the strongest
        evidence of a real error, so a supervisor starts here."""
        return self.by_verdict(MISKEYED)

    @property
    def unverified(self) -> list:
        """Spans the drawing can't account for at all."""
        return self.by_verdict(UNVERIFIED)

    @property
    def to_review(self) -> list:
        """Everything a supervisor should look at, worst evidence first."""
        return self.miskeyed + self.unverified

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
        n_m, n_u = len(self.miskeyed), len(self.unverified)
        head = (f"{n_v} of {len(self.verdicts)} span footages verified against the "
                f"drawing's sequentials")
        if not (n_m or n_u):
            return head + ", and none are unaccounted for."
        bits = []
        if n_m:
            bits.append(f"{n_m} disagree with the run they sit on")
        if n_u:
            bits.append(f"{n_u} match no distance on their route")
        return head + " — " + " and ".join(bits) + "." 


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


def _best_convention(ordered: list, coil_ft, longest: float = 0.0) -> tuple:
    """The (ahead, coil_in) reading that this route's own chain supports best.
    Breaks are ignored, so a route that leaves the sheet often can't skew which
    reading looks right."""
    def scores(conv):
        hits = 0
        for a, b in zip(ordered, ordered[1:]):
            gap, stated, owner = _measure(a, b, *conv, coil_ft)
            if owner.span_ft and not _is_break(gap, longest) and gap == stated:
                hits += 1
        return hits

    best, best_hits = _CONVENTIONS[0], -1
    for conv in _CONVENTIONS:
        hits = scores(conv)
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
        longest = _longest_claim(ordered, lambda r: r.pipe_ft or 0)
        for a, b in zip(ordered, ordered[1:]):
            # Aerial spans chain densely enough that the length of a run tells you
            # whether it is a break. Buried runs don't: a route's bores are scattered
            # through the book (one PON 9 route runs pages 154, 140, 153, 137, 17…),
            # so two runs adjacent in station order are often nowhere near each other
            # on the ground and there are no intermediate sequentials to step
            # through. Until buried work carries nodes of its own, how close the two
            # sit in the book separates a break from a discrepancy better than size.
            if abs(b.page - a.page) > _MAX_BURIED_PAGE_JUMP:
                continue
            owner, gap = ((b, b.in_sta - a.out_sta) if fwd >= rev
                          else (a, b.out_sta - a.in_sta))
            if owner.pipe_ft is None:
                continue                            # no conduit claimed on this run
            if _is_break(gap, longest):             # too long to be one conduit run
                continue
            out.append(ConduitCheck(
                route=route, page=owner.page,
                station_from=(a.out_sta if fwd >= rev else a.in_sta),
                station_to=(b.in_sta if fwd >= rev else b.out_sta),
                gap=float(gap), stated=owner.pipe_ft,
                ok=(gap == owner.pipe_ft), delta=owner.pipe_ft - gap))
    return out


def _with_nodes(spans: list, node_stations, cls):
    """The route's chain, stepping through *every* sequential on it — not only the
    ones a span was written at. A pole with no footage on its comment is still a
    point the route passes through, and skipping it makes the next span look like it
    spans two poles. Nodes with no span of their own carry no footage and are only
    boundaries."""
    have = {s.station for s in spans}
    filler = [cls(page=_nearest_page(spans, st), route=spans[0].route, station=st,
                  span_ft=0.0) for st in node_stations if st not in have]
    return sorted(spans + filler, key=lambda s: s.station)


def _nearest_page(spans: list, station: int) -> int:
    """The sheet a bare sequential most likely sits on — the nearest span's."""
    return min(spans, key=lambda s: abs(s.station - station)).page


def check_stationing(span_records: list, coil_marks: list | None = None,
                     buried_runs: list | None = None,
                     route_stations: dict | None = None) -> StationingReport:
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
    near: dict = {}

    for route, spans in routes.items():
        # de-dupe identical records (the same span drawn on two sheets) and order
        # by station so the walk follows the route, not the page order
        uniq = {(s.station, s.span_ft, s.extra_ft): s for s in spans}
        ordered = sorted(uniq.values(), key=lambda s: s.station)
        if len(ordered) < 2:
            report.unverifiable += len(ordered)
            continue
        # step through every sequential on the route, not only the ones with a span
        nodes = set()
        for r0, sts in (route_stations or {}).items():
            if remap.get(r0, r0) == route:
                nodes |= set(sts)
        if nodes:
            ordered = _with_nodes(ordered, nodes, type(ordered[0]))

        marks = coils.get(route, ())

        def coil_ft(lo: int, hi: int, _m=marks) -> float:
            """Coil / riser footage sitting inside this gap — a coil belongs to
            whichever gap contains the station it is listed at."""
            return sum(c.ft for c in _m if lo < c.station <= hi)

        longest = _longest_claim(ordered, lambda s: (s.span_ft or 0) + s.extra_ft)
        conv = _best_convention(ordered, coil_ft, longest)
        for a, b in zip(ordered, ordered[1:]):
            # exactly one span owns the run, read the way this route writes them,
            # and its footage has to close it on its own
            gap, stated, owner = _measure(a, b, *conv, coil_ft)
            if not owner.span_ft:                   # a bare sequential claims nothing
                report.unverifiable += 1
                continue
            if _is_break(gap, longest):             # the route left these sheets
                report.unverifiable += 1
                continue
            ok = gap == stated
            report.checks.append(SpanCheck(
                route=route, page=owner.page, station_from=a.station,
                station_to=b.station, gap=gap, stated=stated, ok=ok,
                delta=stated - gap, raw=owner.raw))
            key = (route, owner.station, owner.span_ft)
            if ok:
                verified.add(key)
            elif gap and abs(stated - gap) <= gap * _MISKEY_FRACTION:
                # the run is the right size — the number on it isn't
                near[key] = min(near.get(key, 1e9), stated - gap, key=abs)

    # every span gets a verdict, including those with no neighbour to chain to
    for s in span_records:
        route = remap.get(s.route, s.route)
        key = (route, s.station, s.span_ft)
        if key in verified:
            verdict = VERIFIED
        elif key in near:
            verdict = MISKEYED
        else:
            known = sorted(stations.get(route, ()))
            reachable = {abs(x - y) for y in (s.station, s.end) for x in known}
            verdict = PLAUSIBLE if s.span_ft in reachable else UNVERIFIED
        report.verdicts.append(SpanVerdict(
            page=s.page, route=route, station=s.station, span_ft=s.span_ft,
            verdict=verdict, off_by=near.get(key, 0.0)))
    return report
