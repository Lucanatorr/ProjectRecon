"""Field takeoff — drawings: materials and fiber footage from PDF comment boxes.

All comments here are synthetic, written in the conventions found on real
marked-up construction PDFs (MATERIALS boxes; footage boxes with a fiber count
or cable ID over a sequential calculation)."""
from __future__ import annotations

import io
import zipfile

import pytest

from recon.ingest.asbuilt_annot import Annotation
from takeoff.drawings import (
    DrawingFile,
    build_takeoff,
    mark_duplicates,
    normalize_material,
    parse_footage_box,
    parse_materials_box,
    takeoffs_from_sources,
)
from takeoff.sources import PdfSource, expand, expand_all
from tests.takeoff_fixtures import comment_pdf


def _file(name, *pages, group=None):
    """A DrawingFile from comment text, one tuple of comments per page."""
    comments = [Annotation(page=p, text=c.replace("\n", "\r"))
                for p, cs in enumerate(pages, 1) for c in cs]
    return DrawingFile(name=name, pages=len(pages), comments=comments, group=group)


def _totals(t):
    return {(a["item"], a["unit"]): a["qty"] for a in t.material_totals()}


# --- materials ------------------------------------------------------------- #
def test_materials_box_lines_and_units():
    lines, bad = parse_materials_box("f.pdf", 3, "MATERIALS\r2-DEADEND\r3-12\" BOLTS\r"
                                     "3'-COPPER\r2 ROLLS LASHING WIRE\r.5 ROLL OF LASHING WIRE")
    assert bad == []
    got = [(l.qty, l.unit, l.item) for l in lines]
    assert got == [(2, "EA", "DEADEND"), (3, "EA", '12" BOLT'), (3, "FT", "COPPER"),
                   (2, "ROLL", "LASHING WIRE"), (0.5, "ROLL", "LASHING WIRE")]
    assert all(l.page == 3 and l.file == "f.pdf" for l in lines)


@pytest.mark.parametrize("written, name", [
    ("CURVES", "CURVE CLAMP"), ("CURVED CLAMP", "CURVE CLAMP"), ("CURE", "CURVE CLAMP"),
    ("STRAIGHTS", "STRAIGHT CLAMP"), ("STRAIGHT CLAMPS", "STRAIGHT CLAMP"),
    ("DG", "DOWN GUY"), ("DOWNGUY", "DOWN GUY"), ("GG", "GUY GUARD"),
    ("PIGEARS", "PIG EAR"), ("SLIP EYES", "SLIPEYE"), ("SPLITBOLT", "SPLIT BOLT"),
    ("SQ-NUTS", "SQ NUT"), ("BUGNUTS", "BUG NUT"), ("FT COPPER", "COPPER"),
    ("ROLLS LASHINGN WIRE", "LASHING WIRE"), ("ROLL WIRE", "LASHING WIRE"),
    ('14" BOLTS', '14" BOLT'), ('12" BOTLS', '12" BOLT'), ("PREFORSM", "PREFORM"),
])
def test_spelling_variants_combine(written, name):
    assert normalize_material(written)[0] == name


def test_kinds_that_may_differ_stay_apart():
    names = {normalize_material(w)[0] for w in ("NUTS", "SQ NUTS", "BUG NUTS",
                                                "WASHERS", "SQ WASHERS", "FLAT WASHER")}
    assert len(names) == 6


def test_unrecognised_material_is_kept_and_flagged_never_dropped():
    lines, bad = parse_materials_box("f.pdf", 1, "MATERIALS\r4-WIDGET HANGER")
    assert bad == [] and lines[0].item == "WIDGET HANGER" and lines[0].qty == 4
    assert any("not a recognised" in n for n in lines[0].notes)


def test_bolt_with_a_foot_mark_reads_as_inches_and_is_flagged():
    name, unit, notes, _ = normalize_material("12' BOLT")
    assert (name, unit) == ('12" BOLT', "EA") and notes


def test_material_outlier_is_flagged_but_counted():
    boxes = [f"MATERIALS\r{ft}' STRAND" for ft in (400, 520, 610, 700, 480)] + \
            ["MATERIALS\r12,100' STRAND"]
    t = build_takeoff("set", [_file("a.pdf", *[[b] for b in boxes])])
    assert _totals(t)[("STRAND", "FT")] == 400 + 520 + 610 + 700 + 480 + 12100
    flagged = [why for *_, why in t.flagged()]
    assert sum("unusually large" in w for w in flagged) == 1


# --- fiber footage --------------------------------------------------------- #
def test_footage_by_fiber_count_and_cable():
    t = build_takeoff("set", [_file(
        "a.pdf",
        ["48CT\n12000-11500=500'", "TOTALS\n96DF\n3000-2600=400'\n48AF OVERLASH\n900-500=400'"],
        ["288ct\n5000-4250=750'", "ZZA017\n70000-69800=200'"])])
    assert [(a["fiber_count"], a["feet"]) for a in t.fiber_totals()] == [(48, 900), (96, 400), (288, 750)]
    assert [(c["cable"], c["feet"]) for c in t.cable_totals()] == [("ZZA017", 200)]
    labels = {(a["fiber_count"], a["label"]) for a in t.fiber_by_label()}
    assert (48, "48AF OVERLASH") in labels and (48, "48CT") in labels


def test_box_figure_is_used_and_a_bad_subtraction_is_flagged():
    fl, _, _ = parse_footage_box("a.pdf", 2, "ZZA101\r41200-40850=725'")
    assert fl[0].feet == 725
    assert any("350" in n for n in fl[0].notes)


def test_metric_sequentials_use_the_boxes_feet():
    fl, _, _ = parse_footage_box("a.pdf", 1, "96CT\r4200m-4010m=190m=623'")
    assert (fl[0].fiber_count, fl[0].feet, fl[0].seq_unit) == (96, 623, "m")
    assert fl[0].notes == []           # 190 m is 623.4 ft: consistent, so info only
    assert any("meters" in i for i in fl[0].info)


@pytest.mark.parametrize("line, feet", [
    ("8520=8090=430'", 430),              # "=" typed for "-"
    ("9300-1040'", 1040),                 # end number missing
    ("15400-14420=980''", 980),           # doubled foot mark
    ("22100-20600=1500", 1500),           # no foot mark
])
def test_malformed_calculations_still_read(line, feet):
    fl, _, _ = parse_footage_box("a.pdf", 1, f"48CT\r{line}")
    assert fl[0].feet == feet


def test_box_total_lines_are_checked_not_added():
    fl, checks, _ = parse_footage_box(
        "a.pdf", 1, "TOTALS MST OVERLASH ADDER=900'\rMSTZZA097\r51300-50850=450'\r6200-5750=450'")
    assert sum(f.feet for f in fl) == 900
    assert len(checks) == 1 and checks[0].matches


def test_strand_in_a_footage_box_is_a_material():
    t = build_takeoff("set", [_file("a.pdf", ["TOTALS\n96DF\n500-100=400'\nSTRAND=380'"])])
    assert _totals(t)[("STRAND", "FT")] == 380
    assert t.fiber_totals()[0]["feet"] == 400


def test_unlabelled_line_follows_its_sequential_chain_not_the_line_above():
    t = build_takeoff("set", [_file(
        "a.pdf",
        ["MST OVERLASH\nMSTZZA097\n51300-50850=450'\n6200-5750=450'"],
        ["MSTZZA097\n51900-51300=600'\n5750-5100=650'"],
        ["MSTZZA100\n5100-4800=300'"])])
    cables = {c["cable"]: c["feet"] for c in t.cable_totals()}
    assert cables == {"ZZA097": 450 + 600, "ZZA100": 450 + 650 + 300}
    notes = [n for f in t.footage for n in f.notes]
    assert sum("sequentials continue" in n for n in notes) == 2


def test_cable_id_with_a_letter_o_typed_for_zero():
    fl, _, _ = parse_footage_box("a.pdf", 1, "MST ZZDO62\r15400-14420=980'")
    assert fl[0].cable == "ZZD062"


# --- files ------------------------------------------------------------------ #
def test_resubmitted_drawing_is_counted_once():
    a = _file("4438_v1.pdf", ["MATERIALS\n2-STRAIGHT", "96DF\n500-100=400'"])
    b = _file("4438_v2.pdf", ["MATERIALS\n2-STRAIGHT", "96DF\n500-100=400'"])
    c = _file("4448.pdf", ["MATERIALS\n1-STRAIGHT"])
    t = build_takeoff("set", [b, a, c])
    assert b.duplicate_of == "4438_v1.pdf" and a.counted and c.counted
    assert _totals(t)[("STRAIGHT CLAMP", "EA")] == 3
    assert t.fiber_totals()[0]["feet"] == 400


def test_files_without_comments_are_not_duplicates_of_each_other():
    a, b = _file("a.pdf", []), _file("b.pdf", [])
    mark_duplicates([a, b])
    assert a.counted and b.counted


# --- end to end through real PDF bytes and zips ---------------------------- #
def test_end_to_end_from_pdf_and_zip_groups():
    pdf1 = comment_pdf([["MATERIALS\n2-STRAIGHT\n4-NUTS", "48CT\n1000-600=400'"]])
    pdf2 = comment_pdf([["MATERIALS\n1-CURVE\n2-NUTS"], ["96DF\n500-100=400'"]])
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("ProjA/sheet1.pdf", pdf1)
        z.writestr("ProjA/sheet1-resubmitted.pdf", pdf1)
        z.writestr("__MACOSX/ProjA/._sheet1.pdf", b"junk")
        z.writestr("ProjA/notes.txt", b"ignored")
    sources = expand_all([("ProjA.zip", zbuf.getvalue()), ("loose.pdf", pdf2)])
    assert sorted(s.name for s in sources) == ["loose.pdf", "sheet1-resubmitted.pdf", "sheet1.pdf"]
    sets = {t.name: t for t in takeoffs_from_sources(sources, loose_name="Loose")}
    assert set(sets) == {"ProjA", "Loose"}
    a = sets["ProjA"]
    assert len(a.counted_files) == 1
    assert _totals(a) == {("STRAIGHT CLAMP", "EA"): 2, ("NUT", "EA"): 4}
    assert [(x["fiber_count"], x["feet"]) for x in a.fiber_totals()] == [(48, 400)]
    loose = sets["Loose"]
    assert loose.files[0].pages == 2
    assert _totals(loose) == {("CURVE CLAMP", "EA"): 1, ("NUT", "EA"): 2}
    assert [(x["fiber_count"], x["feet"]) for x in loose.fiber_totals()] == [(96, 400)]


def test_zip_member_paths_cannot_escape():
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("../../evil.pdf", comment_pdf([["MATERIALS\n1-NUT"]]))
    sources = expand("x.zip", zbuf.getvalue())
    assert len(sources) == 1 and isinstance(sources[0], PdfSource)   # read in memory only


def test_non_pdf_uploads_are_ignored():
    assert expand("notes.txt", b"hello") == []
