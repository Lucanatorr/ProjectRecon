"""Crosswalk matches on BOTH contract columns (code and description).

Real source text is often the contract code itself ("BHF-48", "AFO.SL"), which a
description-only matcher missed entirely."""
from __future__ import annotations

import pytest

from recon.crosswalk import AliasStore, resolve
from recon.models import ContractItem, UoM


def _contract():
    return [
        ContractItem("AFO.SL", "Placing Strand & Lash Fiber on Distribution Poles",
                     UoM.FT, 1.10, 1000),
        ContractItem("AFO.S", "Install Slack Coils or Snow Shoes", UoM.EA, 45.0, 50),
        ContractItem("BHF-48", 'Labor only to install 30"<VAULT 48" Nontraffic Rated',
                     UoM.EA, 500.0, 20),
        ContractItem("BM81", "Labor to install 10' Riser Guard", UoM.EA, 120.0, 30),
        ContractItem("H01-12R(144)", "73-144 Ribbon Splicing", UoM.EA, 8.0, 500),
        ContractItem("3.1", "Place 144ct ADSS aerial fiber", UoM.FT, 1.85, 42000),
    ]


def test_exact_code_match_wins():
    # previously "no confident contract match" — the code exists verbatim
    m = resolve("BHF-48", _contract())
    assert m.code == "BHF-48"
    assert m.score == 100
    assert m.kind == "code"


def test_exact_code_match_case_and_punctuation_insensitive():
    assert resolve("bhf 48", _contract()).code == "BHF-48"
    assert resolve("afo.sl", _contract()).code == "AFO.SL"


def test_code_match_not_confused_with_similar_code():
    # "AFO.SL" must map to AFO.SL, not the similar-but-different AFO.S
    m = resolve("AFO.SL", _contract())
    assert m.code == "AFO.SL"
    m2 = resolve("AFO.S", _contract())
    assert m2.code == "AFO.S"


def test_another_previously_missed_code():
    m = resolve("BM81", _contract())
    assert m.code == "BM81"
    assert m.kind == "code"


def test_fuzzy_code_match_reports_code_field():
    # near-miss on a code (zero vs letter O) should still surface it as a candidate
    m = resolve("HO1-12R", _contract())
    assert m.candidates
    top = m.candidates[0]
    assert top.code == "H01-12R(144)"
    assert top.field == "code"


def test_description_matching_still_works():
    m = resolve("144F ADSS Aerial Place", _contract())
    assert m.code == "3.1"
    assert m.kind == "auto"
    assert m.candidates[0].field == "description"


def test_alias_still_outranks_code_match():
    store = AliasStore()
    store.confirm("BHF-48", "BM81")          # human said otherwise
    m = resolve("BHF-48", _contract(), store)
    assert m.code == "BM81"
    assert m.kind == "alias"


def test_no_contract_items():
    m = resolve("anything", [])
    assert m.code is None and m.kind == "review"


def test_candidates_carry_both_columns():
    m = resolve("Riser Guard", _contract())
    assert m.candidates
    c = m.candidates[0]
    assert c.code and c.description
    assert c.field in ("code", "description")
