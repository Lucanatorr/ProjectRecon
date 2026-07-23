from recon.ingest.normalize import base_uom, normalize, normalize_uom, to_base_qty
from recon.models import UoM


def test_normalize_basic():
    assert normalize("  Place 144ct ADSS Aerial ") == "place 144count adss aerial"


def test_normalize_idempotent():
    once = normalize("144F ADSS Aerial Place")
    assert normalize(once) == once


def test_normalize_fiber_count_variants():
    a = normalize("144ct ADSS aerial")
    b = normalize("144F Aerial (ADSS)")
    c = normalize("144 count aerial adss")
    # all collapse the fiber-count token identically
    assert "144count" in a and "144count" in b and "144count" in c


def test_normalize_abbrev_expansion():
    assert "underground" in normalize("UG bore")
    assert "makeready" in normalize("pole MR")
    assert normalize("Directional Bore 2\"") == "directional bore 2 in"


def test_normalize_none_and_blank():
    assert normalize(None) == ""
    assert normalize("   ") == ""


def test_normalize_uom():
    assert normalize_uom("LF") == UoM.FT
    assert normalize_uom("each") == UoM.EA
    assert normalize_uom("100FT") == UoM.C_FT
    assert normalize_uom("lump sum") == UoM.LS
    assert normalize_uom("widgets") is None


def test_uom_aliases_are_written_in_normalized_form():
    """from_str upper-cases and strips spaces before lookup, so a verbose alias
    like "Per Foot" only works if its key is stored as "PERFOOT"."""
    for variant in ("Per Foot", "per foot", "PER FOOT", "PerFoot", " per  foot "):
        assert normalize_uom(variant) == UoM.FT, variant


def test_uom_conversion():
    assert to_base_qty(3, UoM.C_FT) == 300      # 100FT -> FT
    assert to_base_qty(50, UoM.FT) == 50
    assert base_uom(UoM.C_FT) == UoM.FT
    assert base_uom(UoM.EA) == UoM.EA
