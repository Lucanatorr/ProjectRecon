"""As-built from PDF comment annotations — the parser that turns contractor Adobe
FreeText shorthand into billable quantities by rate code. Fixtures are real
comments from the Hoke CAB construction PDF."""
from __future__ import annotations

from recon.ingest.asbuilt_annot import (
    Annotation,
    parse_annotations,
    to_asbuilt_lines,
)
from recon.models import UoM


def _ann(pipe: str, page: int = 1) -> Annotation:
    """Build an Annotation from pipe-separated shorthand (real comments are
    carriage-return delimited)."""
    return Annotation(page=page, text="\r".join(p.strip() for p in pipe.split("|")))


def _parse(*comments):
    return parse_annotations([_ann(c) for c in comments])


# --- aerial strand & lash (footage by fiber count) ------------------------ #
def test_aerial_span_footage_bills_as_strand_and_lash_by_count():
    r = _parse("AFO 288F | 6288 | 410' | AFO BOND | AFO SL")
    assert r.qty["afo_sl_288"] == 410
    assert r.code["afo_sl_288"] == "AFO SL 288FOC"
    assert r.uom["afo_sl_288"] == "FT"
    assert r.qty["bond"] == 1


def test_afo_sl_with_no_count_is_strand_only():
    r = _parse("AFO SL - 152' | HST 4-1500")
    assert r.qty["afo_sl"] == 152                       # no count → strand-only
    assert r.code["afo_sl"] == "AFO SL- STRAND"
    assert r.qty["mst_1500"] == 1                       # HST = MST by tail length


def test_bare_small_number_is_footage_large_is_station_id():
    r = _parse("AFO 72F | 51858 | 96 | AFO BOND | AFO SL")
    assert r.qty["afo_sl_72"] == 96                     # 96 = feet
    r2 = _parse("AFO 288F | 2204 | 2372")               # both ids, no footage
    assert "afo_sl_288" not in r2.qty


# --- AFO S is the coil (each), not strand-and-lash ------------------------ #
def test_afo_s_is_a_coil_each_not_confused_with_afo_sl():
    r = _parse("AFO 288 | 6698 | 6623 | AFO BOND | AFO SL | AFO S")
    assert r.qty["coil"] == 1
    assert r.code["coil"] == "AFO.S"
    assert r.uom["coil"] == "EA"
    assert "afo_sl_288" not in r.qty                    # no footage in this span


# --- hardware + multipliers ----------------------------------------------- #
def test_anchor_guy_bond_map_to_rate_codes():
    r = _parse("AFO 288F | 5620 | 334' | AFO BOND | AFO SL | ANCHOR | DG / GG")
    assert (r.code["anchor"], r.code["down_guy"], r.code["bond"]) == \
        ("AFO.GAA", "AFO.GG", "AFO.BOND")
    assert r.qty["anchor"] == 1 and r.qty["down_guy"] == 1


def test_count_multipliers_are_applied():
    r = _parse("AFO 144F | 11900 | 132' | AFO BOND | AFO SL | ANCHOR - 2 | DG / GG - 2")
    assert r.qty["anchor"] == 2 and r.qty["down_guy"] == 2


# --- buried + markers ----------------------------------------------------- #
def test_buried_fiber_and_markers():
    r = _parse("BFO 144F | BHF-30T | 18720 | 18795 MID | 18870 | 375' | BM53 | BM2")
    assert r.qty["buried_144"] == 375 and r.code["buried_144"] == "BFO.144.I"
    assert r.qty["handhole_30T"] == 1 and r.code["handhole_30T"] == "BHF-30T"
    assert r.qty["marker_post"] == 1 and r.code["marker_post"] == "BM53"
    assert r.qty["ground_rod"] == 1 and r.code["ground_rod"] == "BM2"


def test_mixed_aerial_and_buried_in_one_comment():
    r = _parse("AFO 144F | 14080 | 312' | BHF-30T | 14114 | 14264 | "
               "BFO 144F 150' | BM2 | AFO BOND | AFO SL")
    assert r.qty["afo_sl_144"] == 312           # aerial strand & lash
    assert r.qty["buried_144"] == 150           # buried fiber in the same note


# --- notes, exclusions, ambiguous ----------------------------------------- #
def test_did_not_build_is_excluded():
    r = _parse("DID NOT BUILD PER WAYLON", "AFO 72F | 55626 | 302' | AFO SL")
    assert r.records == 2 and len(r.excluded) == 1
    assert r.qty.get("afo_sl_72") == 302        # the built span still counts


def test_moved_to_is_a_note_not_unresolved():
    r = _parse("4 PORT MST MOVED TO 0467601")
    assert r.notes and not r.unresolved


def test_conduit_pedestal_splice_are_left_for_review():
    r = _parse("BM60(3)(1.25) - 300", "BDO", "SPLICE CAN RIVER CITY PAYING")
    keys = [t for _, t in r.unresolved]
    assert "BM60(3)(1.25) - 300" in keys and "BDO" in keys
    assert any("SPLICE" in k for k in keys)


def test_hst_becomes_an_mst_keyed_by_tail_length():
    r = _parse("HST 6-150", "HST 4-750", "HST 8-500")
    assert r.qty["mst_150"] == 1 and r.qty["mst_750"] == 1 and r.qty["mst_500"] == 1
    assert r.code["mst_500"] == "MST 500'" and r.uom["mst_500"] == "EA"


def test_odd_mst_tail_snaps_to_nearest_rate_bracket():
    r = _parse("HST 6-160")
    assert r.qty["mst_150"] == 1                        # 160 → nearest bracket 150


# --- to AsBuiltLine -------------------------------------------------------- #
def test_to_asbuilt_lines_carry_codes_and_confidence():
    r = _parse("AFO 288F | 6288 | 410' | AFO BOND | AFO SL",
               "BFO 144F | 18720 | 375' | BM53")
    lines = {ln.code: ln for ln in to_asbuilt_lines(r)}
    assert lines["AFO SL 288FOC"].qty == 410
    assert lines["AFO SL 288FOC"].uom == UoM.FT
    assert lines["AFO SL 288FOC"].confidence == "annot"
    assert lines["BFO.144.I"].qty == 375
    assert lines["BM53"].qty == 1


def test_code_map_resolves_an_ambiguous_item():
    r = _parse("BHF-30T | 14746 | 14896 | BFO 144F- 150'")   # buried + handhole
    # a coordinator maps the buried-144 to the 'existing duct' variant:
    lines = {ln.code: ln for ln in to_asbuilt_lines(r, {"buried_144": "BFO.144.IE"})}
    assert "BFO.144.IE" in lines and "BFO.144.I" not in lines
