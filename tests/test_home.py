"""Sprint 5.1 — Home / landing: portfolio wrappers and the HTML builders that
render the project list (cards + table), sidebar, and top bar."""
from __future__ import annotations

import pytest

from recon.models import AsBuiltLine, ContractItem, InvoiceLine, UoM
from recon.persistence import Database
from recon.reconcile import reconcile
from ui.theme import (
    home_cards_html,
    home_table_html,
    home_topbar_html,
    project_status_chip,
    sidebar_home_html,
)


def _seed(db, project, cycle_no, *, contractor, built, billed, period, price=10.0):
    contract = [ContractItem("A", "Unit A", UoM.EA, price, 1000)]
    rows = reconcile([AsBuiltLine("A", built, UoM.EA, code="A")],
                     [InvoiceLine("INV", "A", billed, price, billed * price, code="A")],
                     contract)
    db.save_cycle_snapshot(
        project_name=project, contractor=contractor, area="Zone 2",
        cycle_no=cycle_no, period_label=period, billing_mode="cumulative",
        retainage_pct=10.0, prior_billed=0.0, contract_items=contract, rows=rows)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """A temp database with two projects, pointed at by the ui.db wrappers."""
    path = tmp_path / "home.db"
    monkeypatch.setattr("recon.persistence.DB_PATH", path)
    db = Database(path)
    _seed(db, "Robeson CAB", 1, contractor="Rivr Tech",
          built=100, billed=100, period="Jul")
    _seed(db, "Bladen FTTH", 1, contractor="Ace Fiber Constructors",
          built=200, billed=260, period="Aug")             # over-billed → flagged
    _seed(db, "Bladen FTTH", 1, contractor="PineBelt Utilities",
          built=50, billed=50, period="Aug")
    db.close()
    return path


# --- wrappers ------------------------------------------------------------- #
def test_portfolio_lists_projects_with_stats(seeded_db):
    from ui.db import portfolio
    ps = {p["name"]: p for p in portfolio()}
    assert set(ps) == {"Robeson CAB", "Bladen FTTH"}
    bladen = ps["Bladen FTTH"]
    assert bladen["n_cycles"] == 2
    assert sorted(bladen["contractors"]) == ["Ace Fiber Constructors",
                                             "PineBelt Utilities"]
    assert bladen["flagged"] > 0                            # cycle over-billed


def test_project_overview_has_cycles_and_trend(seeded_db):
    from ui.db import portfolio, project_overview
    pid = {p["name"]: p["id"] for p in portfolio()}["Bladen FTTH"]
    ov = project_overview(pid)
    assert ov["name"] == "Bladen FTTH"
    assert len(ov["cycles"]) == 2
    assert len(ov["trend"]) == 2
    assert ov["billed"] == pytest.approx(260 * 10 + 50 * 10)


def test_project_overview_none_for_unknown_id(seeded_db):
    from ui.db import project_overview
    assert project_overview(999999) is None


# --- 5.2 create / attach -------------------------------------------------- #
def test_create_project_registers_and_attaches_contractors(seeded_db):
    from ui.db import attach_contractors, create_project, project_overview
    pid = create_project("Sampson OSP", "South River EMC", ["Alpha Co", "Beta Co"])
    ov = project_overview(pid)
    assert ov["name"] == "Sampson OSP"
    assert sorted(ov["contractors"]) == ["Alpha Co", "Beta Co"]
    attach_contractors(pid, ["Beta Co", "Gamma Co"])       # Beta is a no-op dup
    assert sorted(project_overview(pid)["contractors"]) == [
        "Alpha Co", "Beta Co", "Gamma Co"]


def test_project_exists_guards_duplicate_names(seeded_db):
    from ui.db import project_exists
    assert project_exists("Robeson CAB") is True
    assert project_exists("Nonexistent Job") is False


def test_contractors_overview_lists_projects_per_contractor(seeded_db):
    from ui.db import contractors_overview
    by_name = {c["name"]: c for c in contractors_overview()}
    assert by_name["Rivr Tech"]["projects"] == ["Robeson CAB"]


# --- 5.3 next cycle / saved contract -------------------------------------- #
def test_next_cycle_no_is_per_contractor(seeded_db):
    from ui.db import next_cycle_no, portfolio
    pid = {p["name"]: p["id"] for p in portfolio()}["Robeson CAB"]
    assert next_cycle_no(pid, "Rivr Tech") == 2            # cycle 1 already saved
    assert next_cycle_no(pid, "Never Billed Co") == 1      # nothing saved yet


def test_load_saved_contract_round_trips(seeded_db):
    from ui.db import load_saved_contract, portfolio
    pid = {p["name"]: p["id"] for p in portfolio()}["Robeson CAB"]
    contract = load_saved_contract(pid)
    assert [c.code for c in contract] == ["A"]


# --- 5.4 read-only drill-in ----------------------------------------------- #
def test_cycle_detail_rebuilds_the_reconciliation(seeded_db):
    from ui.db import cycle_detail, portfolio, project_overview
    pid = {p["name"]: p["id"] for p in portfolio()}["Bladen FTTH"]
    cid = project_overview(pid)["cycles"][0]["cycle_id"]
    d = cycle_detail(cid)
    assert d["project_name"] == "Bladen FTTH"
    assert d["contractor"] in ("Ace Fiber Constructors", "PineBelt Utilities")
    assert d["rows"] and d["rows"][0].code == "A"
    # the over-billed Ace cycle reconstructs with a positive variance + a flag
    over = [r for r in d["rows"] if r.amount_variance > 0]
    assert over and over[0].flags


def test_cycle_detail_none_for_unknown_id(seeded_db):
    from ui.db import cycle_detail
    assert cycle_detail(999999) is None


# --- builders ------------------------------------------------------------- #
def _projects():
    return [
        {"id": 1, "name": "Robeson CAB", "area": "PON 5", "status": "active",
         "contractors": ["Rivr Tech"], "n_cycles": 4, "built": 357792.0,
         "billed": 390010.0, "flagged": 35408.0, "latest_period": "Jul 2026"},
        {"id": 2, "name": "Harnett CAB", "area": "PON 2", "status": "archived",
         "contractors": ["MetroFiber LLC"], "n_cycles": 7, "built": 884300.0,
         "billed": 884300.0, "flagged": 0.0, "latest_period": "Feb 2026"},
    ]


def test_cards_render_projects_and_open_links(seeded_db=None):
    html = home_cards_html(_projects(), sid="abc")
    assert "Robeson CAB" in html and "Harnett CAB" in html
    assert "?step=project&pid=1&sid=abc" in html
    assert "Rivr Tech" in html                              # contractor chip
    assert "bfill--over" in html                            # flagged project tinted
    assert "\n" not in html                                 # blank-line-free (HTML block)


def test_table_renders_rows_and_links():
    html = home_table_html(_projects(), sid="abc")
    assert "?step=project&pid=2&sid=abc" in html
    assert "MetroFiber LLC" in html
    assert "$390,010" in html
    assert "\n" not in html


def test_status_chip_maps_state_to_class():
    assert "pchip--ok" in project_status_chip("active")
    assert "pchip--warn" in project_status_chip("closeout")
    assert "pchip--muted" in project_status_chip("archived")
    assert "pchip--ok" in project_status_chip(None)         # defaults to active


def test_topbar_toggle_marks_the_active_view():
    cards = home_topbar_html("cards", sid="x", new_href="?step=contract&sid=y")
    assert 'href="?step=home&hv=cards&sid=x"' in cards
    assert 'href="?step=home&hv=table&sid=x"' in cards
    # the active chip carries is-on; make sure exactly the cards chip does
    assert 'fchip is-on" href="?step=home&hv=cards' in cards
    assert 'fchip" href="?step=home&hv=table' in cards
    assert "+ New project" in cards


def test_sidebar_home_is_blank_line_free_with_and_without_recents():
    with_recents = sidebar_home_html(
        2, 3, 11, [{"name": "Robeson", "meta": "4 cycles", "href": "?x"}], sid="s")
    without = sidebar_home_html(0, 0, 0, [], sid="s")
    for html in (with_recents, without):
        assert "\n\n" not in html                           # no empty-line spill
        # a whitespace-only *interior* line would also terminate the HTML block
        # (leading/trailing indentation lines are harmless, as in sidebar_html)
        for line in html.split("\n")[1:-1]:
            assert line.strip() != "", f"whitespace-only interior line: {line!r}"
    assert "Recent" in with_recents and "Recent" not in without
