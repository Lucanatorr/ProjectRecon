"""Cross-checking as-built span footages against the drawing's fiber sequentials.

The station a span starts at is a checksum on its footage: the distance to the next
span equals the cable placed plus anything else that consumed stationing.
"""
from __future__ import annotations

from recon.ingest.asbuilt_annot import (
    Annotation,
    SpanRecord,
    parse_annotations,
)
from recon.ingest.stationing import check_stationing


def _span(station, ft, extra=0.0, page=1, route=("48", "F")):
    return SpanRecord(page=page, route=route, station=station, span_ft=ft,
                      extra_ft=extra)


def _ann(pipe: str, page: int = 1) -> Annotation:
    return Annotation(page=page, text="\r".join(p.strip() for p in pipe.split("|")))


# --- the arithmetic ------------------------------------------------------- #
def test_a_span_that_closes_its_own_gap_passes():
    # the span at 19670 places 314 ft, reaching the next station at 19984
    r = check_stationing([_span(19670, 314), _span(19984, 72)])
    assert r.checked == 1 and r.passed == 1 and not r.failures


def test_a_coil_consumes_stationing_and_still_reconciles():
    # 19514 + 364 span + 150 coil = 20028
    r = check_stationing([_span(19514, 364, extra=150), _span(20028, 354)])
    assert r.passed == 1 and not r.failures


def test_a_bad_footage_is_not_rescued_by_its_neighbour():
    # once a route's direction is established, exactly one span owns each gap — a
    # neighbour that happens to carry the right number must not cover for it
    spans = [_span(1000, 100), _span(1100, 100), _span(1200, 100),
             _span(1300, 555),                   # wrong: the gap it owns is 100
             _span(1400, 100), _span(1500, 777)]
    r = check_stationing(spans)
    assert len(r.failures) == 1
    f = r.failures[0]
    assert f.station_from == 1300 and f.gap == 100 and f.stated == 555
    assert f.delta == 455


def test_off_by_one_is_still_a_failure():
    r = check_stationing([_span(15222, 193), _span(15620, 206)])
    assert len(r.failures) == 1          # 398 gap vs 193 stated — exact match only


def test_a_route_that_states_footage_on_arrival_is_read_that_way():
    # real PON 10 (48, F): each span's footage is the run *into* its station
    spans = [_span(23706, 344), _span(23986, 280), _span(24258, 272),
             _span(24470, 212), _span(24708, 238)]
    r = check_stationing(spans)
    assert r.checked == 4 and not r.failures


def test_the_route_direction_does_not_leak_between_routes():
    # an arrival-stated route and a departure-stated one in the same book
    arriving = [_span(1000, 0, route=("48", "F")), _span(1100, 100, route=("48", "F")),
                _span(1250, 150, route=("48", "F"))]
    leaving = [_span(1000, 100, route=("144", "D")), _span(1100, 150, route=("144", "D")),
               _span(1250, 999, route=("144", "D"))]
    r = check_stationing(arriving + leaving)
    assert r.checked == 4 and not r.failures


def test_routes_are_checked_independently():
    # same stations on two routes must not be compared against each other
    spans = [_span(1000, 100, route=("48", "F")), _span(1100, 100, route=("48", "F")),
             _span(1000, 250, route=("144", "D")), _span(1250, 250, route=("144", "D"))]
    r = check_stationing(spans)
    assert r.checked == 2 and not r.failures


def test_a_distant_page_jump_is_unverifiable_not_a_failure():
    r = check_stationing([_span(1000, 100, page=2), _span(5000, 100, page=40)])
    assert r.checked == 0 and r.unverifiable == 1


def test_a_lone_span_cannot_be_checked():
    r = check_stationing([_span(1000, 100)])
    assert r.checked == 0 and r.unverifiable == 1


def test_duplicate_records_do_not_create_a_phantom_gap():
    # the same span drawn on two sheets must not compare against itself
    r = check_stationing([_span(1000, 100), _span(1000, 100, page=2),
                          _span(1100, 100)])
    assert r.checked == 1 and not r.failures


# --- report ---------------------------------------------------------------- #
def test_report_summarises_pass_and_fail():
    r = check_stationing([_span(1000, 100), _span(1100, 100), _span(1500, 100)])
    assert r.checked == 2 and len(r.failures) == 1
    assert "1 of 2 spans reconcile" in r.summary()
    assert "1 do not" in r.summary()


def test_route_label_reads_naturally():
    r = check_stationing([_span(1000, 100, route=("144", "D")),
                          _span(1100, 100, route=("144", "D"))])
    assert r.checks[0].route_label == "144ct distribution"


def test_empty_input_is_handled():
    r = check_stationing([])
    assert r.checked == 0 and "No span stationing" in r.summary()


# --- end to end from real comment shorthand -------------------------------- #
def test_span_records_are_captured_from_comments():
    # the real PON 9 chain: each span's footage is the gap to the next station
    anns = [_ann("AFO 48 (F) | 19670 | AFO - 314 | 1-AFO.BOND", page=4),
            _ann("AFO 48 (F) | 19984 | AFO - 314 | 1-AFO.BOND", page=4)]
    res = parse_annotations(anns)
    assert len(res.span_records) == 2
    rec = res.span_records[0]
    assert rec.station == 19670 and rec.span_ft == 314 and rec.route == ("48", "F")
    assert not check_stationing(res.span_records).failures


def test_a_coil_is_anchored_to_the_station_it_is_listed_at():
    # 19514 + 150 coil = 19664 (the coil's own station), + 364 span = 20028
    res = parse_annotations(
        [_ann("AFO 48 (D) | 19514 | AFO - 364 | AFO Coil - 150 | 19664")])
    rec = res.span_records[0]
    assert rec.span_ft == 364 and rec.extra_ft == 0     # not a blob on the span
    assert len(res.coil_marks) == 1
    mark = res.coil_marks[0]
    assert mark.station == 19664 and mark.ft == 150 and mark.route == ("48", "D")


def test_a_coil_counts_only_in_the_gap_that_contains_its_station():
    from recon.ingest.asbuilt_annot import CoilMark
    spans = [_span(1000, 100), _span(1250, 100), _span(1350, 100)]
    coil = CoilMark(route=("48", "F"), station=1100, ft=150)   # sits in gap 1 only
    r = check_stationing(spans, [coil])
    assert not r.failures            # 100+150 closes gap 1; 100 closes gap 2


def test_an_unanchored_coil_still_falls_back_to_its_span():
    # no station follows the coil, so it stays with the span it was written on
    res = parse_annotations([_ann("AFO 48 (D) | 19514 | AFO - 364 | AFO Coil - 150")])
    assert not res.coil_marks
    assert res.span_records[0].extra_ft == 150
