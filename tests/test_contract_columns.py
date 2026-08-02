"""Reading a bid schedule / rate sheet whose column headers share words.

A real rate sheet is headed CODE | UNIT DESCRIPTION | UNIT | RATE, where the work
item is under "unit description" and the unit of measure is under bare "unit". The
unit of measure decides the tolerance a quantity is held to — feet get a band,
each must match exactly — so reading it off the wrong column turns every footage
line into a false critical.
"""
from __future__ import annotations

import pandas as pd
import pytest

from recon.contract import _resolve_columns, load_bid_schedule
from recon.models import UoM


def _sheet(tmp_path, columns, rows, name="rates.xlsx"):
    path = tmp_path / name
    pd.DataFrame(rows, columns=columns).to_excel(path, index=False)
    return path


def test_unit_description_and_unit_go_to_different_fields():
    df = pd.DataFrame(columns=["CODE", "UNIT DESCRIPTION", "UNIT", "RATE"])
    resolved = _resolve_columns(df)
    assert resolved["description"] == "UNIT DESCRIPTION"
    assert resolved["uom"] == "UNIT"
    assert resolved["code"] == "CODE"
    assert resolved["unit_price"] == "RATE"


def test_a_column_is_claimed_by_only_one_field():
    df = pd.DataFrame(columns=["CODE", "UNIT DESCRIPTION", "UNIT", "RATE"])
    resolved = _resolve_columns(df)
    assert len(set(resolved.values())) == len(resolved)


def test_per_foot_rates_load_as_feet_not_each(tmp_path):
    path = _sheet(
        tmp_path, ["CODE", "UNIT DESCRIPTION", "UNIT", "RATE"],
        [["AFO.SL", "Placing Strand & Lash Fiber", "Per Foot", 2.41],
         ["AFO.GAA", "Place Screw Anchor With Down Guy", "Each", 102],
         ["BM60-(1.25)DP", "1.25\" conduit bore", "Per Foot", 10.8]])
    by_code = {c.code: c for c in load_bid_schedule(path)}
    assert by_code["AFO.SL"].uom == UoM.FT
    assert by_code["BM60-(1.25)DP"].uom == UoM.FT
    assert by_code["AFO.GAA"].uom == UoM.EA
    assert by_code["AFO.SL"].unit_price == pytest.approx(2.41)


def test_a_conventional_bid_schedule_still_reads(tmp_path):
    # the ordinary shape must keep working — "unit" alone still means the measure
    path = _sheet(
        tmp_path, ["Code", "Description", "UoM", "Unit Price", "Est Qty"],
        [["3.1", "Place 144ct ADSS aerial fiber", "FT", 1.85, 42000]])
    item = load_bid_schedule(path)[0]
    assert item.code == "3.1" and item.uom == UoM.FT
    assert item.unit_price == pytest.approx(1.85) and item.est_qty == 42000
