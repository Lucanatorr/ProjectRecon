"""As-built from PDF comment annotations — the parser that turns contractor Adobe
FreeText shorthand into billable quantities by rate code.

Fixtures are real comments from two conventions in the wild: the Hoke CAB PDF
(cable + station + footage) and the Scotland PON sets (actual rate codes with
segment/route detail)."""
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


def test_bare_afo_is_still_strand_and_lash():
    # the rate-coded convention: "AFO - 344" just omits the .SL
    r = _parse("AFO 48 (F) | 19984 | AFO - 344 | 1-AFO.BANDING | 1-AFO.BOND")
    assert r.qty["afo_sl_48"] == 344
    assert r.code["afo_sl_48"] == "AFO SL48FOC"
    assert r.qty["banding"] == 1 and r.qty["bond"] == 1


def test_afo_sl_with_no_count_is_strand_only():
    r = _parse("AFO SL - 152' | HST 4-1500")
    assert r.qty["afo_sl"] == 152
    assert r.code["afo_sl"] == "AFO SL- STRAND"
    assert r.qty["mst_1500"] == 1


def test_bare_number_is_footage_when_small_station_id_when_large():
    assert _parse("AFO 72F | 51858 | 96 | AFO SL").qty["afo_sl_72"] == 96
    assert "afo_sl_288" not in _parse("AFO 288F | 2204 | 2372").qty


def test_two_segments_in_one_comment_bill_separately():
    r = _parse("AFO 48 (F) | 18396 | AFO - 312 | AFO 144 (D) | 15524 | AFO - 296")
    assert r.qty["afo_sl_48"] == 312 and r.qty["afo_sl_144"] == 296


# --- coils, overlash --------------------------------------------------------#
def test_coil_is_one_each_and_its_footage_is_not_double_counted():
    r = _parse("AFO 48 (F) | 14782 | AFO - 332 | AFO Coil - 150 | 14632")
    assert r.qty["coil"] == 1 and r.code["coil"] == "AFO.S"
    assert r.qty["afo_sl_48"] == 332          # coil's 150 is already inside the span


def test_afo_s_is_a_coil_each_not_confused_with_afo_sl():
    r = _parse("AFO 288 | 6698 | 6623 | AFO BOND | AFO SL | AFO S")
    assert r.qty["coil"] == 1 and r.uom["coil"] == "EA"


def test_overlash_replaces_the_placement_on_that_span():
    r = _parse("AFO.48 (F) | 23706 | AFO - 344 | OLASH - 344")
    assert r.qty["olash"] == 344 and r.code["olash"] == "AFO.OLASH"
    assert "afo_sl_48" not in r.qty           # OLASH only — not both


def test_overlash_without_footage_uses_the_span_footage():
    r = _parse("AFO96 (D) | 14366 | AFO - 194 | AFO OLASH")
    assert r.qty["olash"] == 194
    assert "afo_sl_96" not in r.qty


# --- hardware, guys, multipliers ------------------------------------------ #
def test_anchor_guy_bond_map_to_rate_codes():
    r = _parse("AFO 288F | 5620 | 334' | AFO BOND | AFO SL | ANCHOR | DG / GG")
    assert (r.code["anchor"], r.code["down_guy"], r.code["bond"]) == \
        ("AFO.GAA", "AFO.GG", "AFO.BOND")
    assert r.qty["anchor"] == 1 and r.qty["down_guy"] == 1   # not doubled


def test_dg_alone_implies_its_anchor():
    r = _parse("AFO 48 | 26760 | AFO - 194 | PM2A - 1 | DG - 1")
    assert r.qty["down_guy"] == 1 and r.qty["anchor"] == 1
    assert "pm2a" not in r.qty                # PM2A is not a contract unit


def test_explicit_gaa_stops_dg_from_adding_another():
    r = _parse("1-AFO.GAA | 1-AFO.GG | DG - 1")
    assert r.qty["anchor"] == 1


def test_count_multipliers_are_applied():
    r = _parse("AFO 144F | 11900 | 132' | AFO BOND | ANCHOR - 2 | DG / GG - 2")
    assert r.qty["anchor"] == 2 and r.qty["down_guy"] == 2


def test_quantity_prefix_and_equals_forms():
    r = _parse("2-BM81 | BHF.30T=1 | BM2=2 | 1- BDO(M) | BM53")
    assert r.qty["riser_guard"] == 2 and r.qty["handhole_30T"] == 1
    assert r.qty["ground_rod"] == 2 and r.qty["pedestal_M"] == 1
    assert r.qty["marker_post"] == 1


def test_code_spelling_variants_normalize():
    r = _parse("1-AFO-6AA | AFO.66 | 1-AFO BOND | 1AFO.BANDING | AFO TRANS2")
    assert r.qty["anchor"] == 1 and r.qty["down_guy"] == 1
    assert r.qty["bond"] == 1 and r.qty["banding"] == 1 and r.qty["transfer"] == 1


# --- buried fiber, conduit -------------------------------------------------#
def test_buried_fiber_in_conduit_vs_existing_duct():
    a = _parse("BFO.48.I (F) | In - 10478 | Out - 10378 | BFO - 736 | Pipe - 636")
    assert a.qty["bfo_48_I"] == 736 and a.code["bfo_48_I"] == "BFO.48.I"
    assert "conduit" not in " ".join(a.label)      # Pipe is reference only
    b = _parse("BFO.48.IE (F) | In - 19140 | BFO - 1240 | Fiber Only")
    assert b.qty["bfo_48_IE"] == 1240 and b.code["bfo_48_IE"] == "BFO.48.IE"


def test_conduit_way_count_size_and_method():
    r = _parse('BM60(2-1.25") | Plow=636\'')
    assert r.qty["bm60_2_1.25_P"] == 636         # plow keeps its way count
    assert r.code["bm60_2_1.25_P"] == "BM60(2)(1.25) P"


def test_single_pipe_bore_is_the_base_dp_unit():
    # pricing-critical: one 1.25" pipe bills the base bore unit
    r = _parse('BM60(1-1.25") | Bore=53\'', "BM60-(1.25)DP-460'")
    assert r.code["bm60_1.25_DP"] == "BM60-(1.25)DP"
    assert r.qty["bm60_1.25_DP"] == 513          # 53 + 460
    assert "bm60_1.25_DPD" not in r.qty


def test_multi_pipe_bore_bills_the_base_plus_the_dual_adder():
    # a multi-pipe bore carries the base bore unit *and* the adder
    r = _parse('BM60(2-1.25") | Bore=240\'', 'BM60(3-1.25") | Bore=153\'',
               'BM60(4-1.25") | Bore=236\'')
    assert r.code["bm60_1.25_DPD"] == "BM60-(1.25)DPD Dual"
    assert r.code["bm60_1.25_DP"] == "BM60-(1.25)DP"
    assert r.qty["bm60_1.25_DPD"] == 629         # 240 + 153 + 236
    assert r.qty["bm60_1.25_DP"] == 629          # base at the same footage


def test_explicit_dual_marking_wins_over_way_count():
    r = _parse("BM60-(1.25)DPD-DUAL-212'", "BM60 (1.25) DP Dual 116'")
    assert r.qty["bm60_1.25_DPD"] == 328
    assert r.qty["bm60_1.25_DP"] == 328          # base derived for each


def test_base_is_not_double_counted_when_the_comment_writes_it_out():
    # real PON 9 comment: the contractor billed base + adder as two lines, so the
    # adder must not derive a second base on top of it
    r = _parse("BM60-(1.25)DP-212' | BM60-(1.25)DPD-DUAL-212' | 5-BM81")
    assert r.qty["bm60_1.25_DP"] == 212          # not 424
    assert r.qty["bm60_1.25_DPD"] == 212


def test_a_separate_bore_still_gets_its_own_base():
    # an explicit base at one footage must not suppress the base of a different bore
    r = _parse("BM60-(1.25)DP-212' | BM60(3-1.25\") Bore=153'")
    assert r.qty["bm60_1.25_DP"] == 365          # 212 explicit + 153 derived
    assert r.qty["bm60_1.25_DPD"] == 153


def test_bare_size_is_not_split_into_a_way_count():
    # BM60(1.25")DP is ONE 1.25" pipe — the decimal must not read as count 1 size .25
    r = _parse('BM60(1.25")DP - 254\'', "BM60(1.25')DP - 782'")
    assert r.qty["bm60_1.25_DP"] == 1036         # 254 + 782, both single-pipe
    assert "bm60_1.25_DPD" not in r.qty


def test_footage_written_before_the_code_is_still_read():
    # PON 9 style: "250' BM60-(1.25) DP"
    r = _parse("250' BM60-(1.25) DP", "202' BM60-(1.25) DP")
    assert r.qty["bm60_1.25_DP"] == 452


def test_railroad_and_four_inch_bores_keep_their_size():
    r = _parse('BM60(1)(4") DP RR - 236\'', 'BM60(4")DP SDR11 Rail Road - 128\'')
    assert r.code["bm60_4_DP"] == "BM60-(4)DP"
    assert r.qty["bm60_4_DP"] == 364


def test_conduit_spec_and_footage_can_span_two_tokens():
    r = _parse('BM60(4-1.25") | Bore=236\'')
    assert r.qty["bm60_1.25_DPD"] == 236
    assert r.qty["bm60_1.25_DP"] == 236


def test_markers_and_tracer_wire():
    r = _parse("BFO 144F | 18720 | 375' | BM53 | BM2 | BM90 - 258'")
    assert r.qty["bfo_144_I"] == 375
    assert r.qty["marker_post"] == 1 and r.qty["ground_rod"] == 1
    assert r.qty["tracer_wire"] == 258


def test_handhole_dimensions_map_to_size_brackets():
    r = _parse("BHF-17 (17x30x24) | BM2", "24x36x36 | BM53", "BHF(30x48x36)")
    assert r.qty["handhole_17"] >= 1 or r.qty["handhole_BHF-17T"] >= 1
    assert r.qty["handhole_BHF-30T"] == 1
    assert r.qty["handhole_BHF-48T"] == 1


# --- MSTs ------------------------------------------------------------------#
def test_hst_becomes_an_mst_keyed_by_tail_length():
    r = _parse("HST 6-150", "HST 4-750", "HST 8-500")
    assert r.qty["mst_150"] == 1 and r.qty["mst_750"] == 1 and r.qty["mst_500"] == 1
    assert r.code["mst_500"] == "MST 500'" and r.uom["mst_500"] == "EA"


def test_odd_mst_tail_snaps_to_nearest_rate_bracket():
    assert _parse("HST 6-160").qty["mst_150"] == 1


def test_rtd_tail_bills_per_foot():
    r = _parse("1-BFO.RTD-150' | HEAD 7330' | TAIL 7178'")
    assert r.qty["bfo_rtd"] == 150 and r.code["bfo_rtd"] == "BFO.RTD.I"


# --- notes, exclusions, ambiguous ----------------------------------------- #
def test_duplicate_page_is_excluded_entirely():
    r = _parse("Duplicate Page", "AFO 72F | 55626 | 302' | AFO SL")
    assert len(r.excluded) == 1
    assert r.qty["afo_sl_72"] == 302


def test_did_not_build_is_excluded():
    r = _parse("DID NOT BUILD PER WAYLON", "AFO 72F | 55626 | 302' | AFO SL")
    assert r.records == 2 and len(r.excluded) == 1


def test_cross_references_and_dates_are_notes():
    r = _parse("This pole on Page 5", "2/26", "4 PORT MST MOVED TO 0467601")
    assert len(r.notes) == 3 and not r.unresolved


def test_ambiguous_items_are_left_for_review():
    r = _parse("BDO", "***SPLICE***")
    keys = [t for _, t in r.unresolved]
    assert "BDO" in keys                     # pedestal size varies
    assert any("SPLICE" in k for k in keys)  # aerial vs buried closure varies


# --- to AsBuiltLine: match against the loaded contract --------------------- #
def _contract(*codes):
    from recon.models import ContractItem
    from recon.models import UoM as U
    return [ContractItem(c, f"{c} description", U.FT, 1.0, 0) for c in codes]


def test_lines_match_against_the_loaded_contract():
    r = _parse("AFO 288F | 6288 | 410' | AFO BOND | AFO SL",
               "BFO 144F | 18720 | 375' | BM53")
    contract = _contract("AFO SL 288FOC", "AFO.BOND", "BFO.144.I", "BM53")
    lines, resolved = to_asbuilt_lines(r, contract)
    by_code = {ln.code: ln for ln in lines}
    assert by_code["AFO SL 288FOC"].qty == 410
    assert by_code["AFO SL 288FOC"].uom == UoM.FT
    assert by_code["AFO SL 288FOC"].confidence == "annot"
    assert by_code["BFO.144.I"].qty == 375 and by_code["BM53"].qty == 1
    assert resolved[by_code["AFO SL 288FOC"].raw_desc] == "AFO SL 288FOC"


def test_unmatched_items_carry_no_code_for_the_crosswalk():
    r = _parse("AFO 288F | 6288 | 410' | AFO SL")
    lines, resolved = to_asbuilt_lines(r, _contract("SOMETHING.ELSE"))
    assert lines[0].code is None
    assert resolved == {}


def test_no_contract_leaves_everything_for_the_crosswalk():
    r = _parse("AFO 288F | 6288 | 410' | AFO SL")
    lines, resolved = to_asbuilt_lines(r)
    assert lines[0].code is None and resolved == {}
