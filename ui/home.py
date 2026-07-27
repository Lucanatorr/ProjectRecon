"""Home / landing and its sub-pages — a portfolio dashboard over every saved
project, plus the flows that hang off it:

  * render_home       — project list (Cards / Table via ?hv=)
  * render_new_project — create a project and attach contractors (?step=new)
  * render_contractors — the contractor registry (?step=contractors)
  * render_project     — per-contractor cycle history + trend, start next cycle,
                         add a contractor (?step=project&pid=)
  * render_cycle       — read-only drill-in of a saved cycle's full reconciliation
                         (?step=cycle&cid=)

Display is the same custom-HTML approach as the wizard; state-mutating actions
(create / attach / start cycle) use Streamlit widgets so they run real Python.
"""
from __future__ import annotations

import streamlit as st

from ui.db import (
    attach_contractors,
    contractor_names,
    contractors_overview,
    create_contractor,
    create_project,
    cycle_detail,
    load_saved_contract,
    next_cycle_no,
    portfolio,
    project_exists,
    project_overview,
)
from ui.state import get_sid, new_session
from ui.theme import (
    _esc,
    _money_compact,
    home_cards_html,
    home_table_html,
    home_topbar_html,
    kpi_row_html,
    lede,
    project_status_chip,
    project_topbar_html,
    recon_list_html,
    sidebar_home_html,
    table_html,
    td,
    trend_html,
)

_LEGEND = ('<div class="legend"><i style="background:#9fb2cc"></i> Built '
           '<i style="background:var(--blue);margin-left:8px"></i> Billed</div>')


def _recents(projects: list[dict], sid: str) -> list[dict]:
    q = f"&sid={sid}" if sid else ""
    out = []
    for p in projects[:3]:
        n = p["n_cycles"]
        meta = f'{n} cycle{"" if n == 1 else "s"} · {p.get("latest_period") or "—"}'
        out.append({"name": p["name"], "meta": meta,
                    "href": f"?step=project&pid={p['id']}{q}"})
    return out


def _render_sidebar(active: str, sid: str, projects: list[dict]) -> None:
    n_cycles = sum(p["n_cycles"] for p in projects)
    st.sidebar.markdown(
        sidebar_home_html(len(projects), len(contractor_names()), n_cycles,
                          _recents(projects, sid), sid, active=active),
        unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
#  Home — project list
# --------------------------------------------------------------------------- #
def _portfolio_kpis(projects: list[dict]) -> list[dict]:
    n = len(projects)
    n_active = sum(1 for p in projects if (p["status"] or "active").lower() == "active")
    contractors = {c for p in projects for c in p["contractors"]}
    n_cycles = sum(p["n_cycles"] for p in projects)
    built = sum(p.get("built") or 0.0 for p in projects)
    billed = sum(p.get("billed") or 0.0 for p in projects)
    flagged = sum(p.get("flagged") or 0.0 for p in projects)
    return [
        {"label": "Projects", "value": str(n),
         "sub": f"{n_active} active · {n - n_active} closed"},
        {"label": "Contractors", "value": str(len(contractors)),
         "sub": "across all jobs"},
        {"label": "Cycles reconciled", "value": str(n_cycles), "sub": "to date"},
        {"label": "Billed to date", "value": _money_compact(billed),
         "sub": f"documented {_money_compact(built)}"},
        {"label": "Flagged (open)", "value": f"${flagged:,.0f}",
         "sub": "held for review", "flag": True},
    ]


def render_home(state) -> None:
    sid = get_sid()
    projects = portfolio()
    _render_sidebar("projects", sid, projects)

    view = st.query_params.get("hv", "cards")
    if view not in ("cards", "table"):
        view = "cards"
    new_href = f"?step=new&sid={sid}"
    st.markdown(home_topbar_html(view, sid, new_href), unsafe_allow_html=True)

    if not projects:
        st.markdown(
            '<div class="empty"><div class="empty__t">No projects yet</div>'
            '<div class="empty__d">Create a project and its contractors, then '
            'reconcile the first cycle — each saved cycle builds the project’s '
            'history here.</div>'
            f'<a class="btn btn--pri" href="{new_href}" target="_self">'
            '+ New project</a></div>', unsafe_allow_html=True)
        return

    st.markdown(kpi_row_html(_portfolio_kpis(projects)), unsafe_allow_html=True)
    st.markdown('<div class="bar-row"><div class="card__t">All projects</div>'
                f'{_LEGEND}</div>', unsafe_allow_html=True)
    if view == "table":
        st.markdown(home_table_html(projects, sid), unsafe_allow_html=True)
    else:
        st.markdown(home_cards_html(projects, sid), unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
#  New project
# --------------------------------------------------------------------------- #
def render_new_project(state) -> None:
    sid = get_sid()
    _render_sidebar("projects", sid, portfolio())
    st.markdown(project_topbar_html("New project", sid), unsafe_allow_html=True)
    st.markdown(lede("Name the job, then register the contractors billing against "
                     "it. You'll reconcile each contractor's cycles from the "
                     "project page."), unsafe_allow_html=True)

    with st.form("new_project"):
        name = st.text_input("Project name",
                             placeholder="e.g. Robeson CAB — PON 5")
        area = st.text_input("Area / owner",
                            placeholder="e.g. Lumbee River EMC")
        existing = contractor_names()
        picked = st.multiselect("Contractors (existing)", existing)
        added = st.text_input("Add new contractors",
                             placeholder="comma-separated, e.g. Rivr Tech, Ace Fiber")
        submitted = st.form_submit_button("Create project", type="primary")

    if submitted:
        nm = name.strip()
        names = list(picked) + [n.strip() for n in added.split(",") if n.strip()]
        if not nm:
            st.error("Enter a project name.")
        elif project_exists(nm):
            st.error(f"A project named “{nm}” already exists.")
        else:
            pid = create_project(nm, area.strip() or None, names)
            st.query_params["step"] = "project"
            st.query_params["pid"] = str(pid)
            st.rerun()


# --------------------------------------------------------------------------- #
#  Contractors registry
# --------------------------------------------------------------------------- #
def render_contractors(state) -> None:
    sid = get_sid()
    _render_sidebar("contractors", sid, portfolio())
    st.markdown(project_topbar_html("Contractors", sid), unsafe_allow_html=True)
    st.markdown(lede("Every contractor you've registered. Add one here, or attach "
                     "them to a project from that project's page."),
                unsafe_allow_html=True)

    with st.form("new_contractor"):
        c1, c2, c3 = st.columns([2, 2, 1])
        name = c1.text_input("Contractor name")
        company = c2.text_input("Company (optional)")
        c3.write("")
        c3.write("")
        submitted = c3.form_submit_button("Add", type="primary")
    if submitted:
        if not name.strip():
            st.error("Enter a contractor name.")
        else:
            create_contractor(name.strip(), company.strip() or None)
            st.rerun()

    rows = contractors_overview()
    if not rows:
        st.caption("No contractors yet — add one above.")
        return
    headers = [("Contractor", ""), ("Company", ""), ("Projects", "r")]
    body = [[td(r["name"]), td(r["company"] or "—"),
             td(", ".join(r["projects"]) or "—", "r")] for r in rows]
    st.markdown(table_html(headers, body), unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
#  Project detail — per contractor
# --------------------------------------------------------------------------- #
def _detail_kpis(ov: dict) -> list[dict]:
    n = len(ov["cycles"])
    return [
        {"label": "Cycles reconciled", "value": str(n), "sub": "saved to date"},
        {"label": "Built to date", "value": _money_compact(ov["built"]),
         "sub": "documented work"},
        {"label": "Billed to date", "value": _money_compact(ov["billed"]),
         "sub": "gross, cumulative"},
        {"label": "Flagged (open)", "value": f"${ov['flagged']:,.0f}",
         "sub": "held for review", "flag": True},
    ]


def _contractor_cycles_table(cycles: list[dict], sid: str) -> str:
    q = f"&sid={sid}" if sid else ""
    headers = [("Cycle", ""), ("Period", ""), ("Mode", ""), ("Billed", "r"),
               ("Expected", "r"), ("Flagged", "r"), ("Net", "r"), ("", "r")]
    body = []
    for s in cycles:
        cid = s["cycle_id"]
        flag_td = (f'<td class="r num" style="color:var(--critical)">'
                   f'${s["flagged"]:,.0f}</td>' if s["flagged"] > 0.5
                   else '<td class="r num">$0</td>')
        body.append([
            f'<td><a class="plink" href="?step=cycle&cid={cid}{q}" '
            f'target="_self">{s["cycle_no"]:02d}</a></td>',
            td(s["period_label"] or "—"),
            td(s["billing_mode"] or "—"),
            td(f'${s["billed"]:,.0f}', "r num"),
            td(f'${s["expected"]:,.0f}', "r num"),
            flag_td,
            td(f'${s["net"]:,.0f}', "r num"),
            f'<td class="r"><a class="plink" href="?step=cycle&cid={cid}{q}" '
            f'target="_self">Open →</a></td>',
        ])
    return table_html(headers, body)


def _start_cycle(ov: dict, contractor: str) -> None:
    """Seed a fresh wizard session with this project's saved contract and the
    contractor's next cycle number, then hand off to the wizard (prior billing is
    loaded, contractor-scoped, when the cycle reconciles)."""
    nxt = next_cycle_no(ov["id"], contractor)
    contract = load_saved_contract(ov["id"])
    sid2 = new_session(
        project_name=ov["name"], area=ov["area"] or "", contractor=contractor,
        cycle_no=nxt, period_label="", contract=contract,
        billing_mode="cumulative",
        done={"contract"} if contract else set())
    st.query_params["step"] = "contract"
    st.query_params["sid"] = sid2
    st.rerun()


def render_project(state) -> None:
    sid = get_sid()
    raw = st.query_params.get("pid", "")
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        pid = None
    ov = project_overview(pid) if pid is not None else None

    if ov is None:
        st.sidebar.markdown(sidebar_home_html(0, 0, 0, [], sid),
                            unsafe_allow_html=True)
        st.markdown(project_topbar_html("Project not found", sid),
                    unsafe_allow_html=True)
        st.warning("That project could not be found. It may have been removed.")
        return

    _render_sidebar("projects", sid, portfolio())
    st.markdown(project_topbar_html(ov["name"], sid), unsafe_allow_html=True)

    cons = "".join(f'<span class="con">{_esc(c)}</span>' for c in ov["contractors"]) \
        or '<span class="con" style="color:var(--muted-2)">No contractor yet</span>'
    st.markdown(
        '<div class="pcard__head" style="margin-bottom:6px">'
        f'<div class="pcard__cons">{cons}</div>'
        f'{project_status_chip(ov["status"])}</div>', unsafe_allow_html=True)

    _add_contractor(ov)

    if ov["cycles"]:
        st.markdown(kpi_row_html(_detail_kpis(ov)), unsafe_allow_html=True)

    # group cycles by contractor; show attached contractors even with no cycles yet
    groups: dict[str, list[dict]] = {}
    for s in ov["cycles"]:
        groups.setdefault(s.get("contractor") or "— Unattributed", []).append(s)
    for c in ov["contractors"]:
        groups.setdefault(c, [])

    ordered = sorted(k for k in groups if not k.startswith("— "))
    ordered += [k for k in groups if k.startswith("— ")]     # legacy bucket last

    for contractor in ordered:
        cycles = groups[contractor]
        legacy = contractor.startswith("— ")
        st.markdown(f'<div class="card__t" style="margin-top:18px">'
                    f'{_esc(contractor)}</div>', unsafe_allow_html=True)
        if cycles:
            st.markdown(_contractor_cycles_table(cycles, sid),
                        unsafe_allow_html=True)
            st.markdown(trend_html(cycles), unsafe_allow_html=True)
        else:
            st.caption("No saved cycles yet.")
        if not legacy:
            nxt = next_cycle_no(ov["id"], contractor)
            if st.button(f"Start cycle {nxt:02d} · {contractor}",
                         key=f"start_{ov['id']}_{contractor}", type="primary"):
                _start_cycle(ov, contractor)

    st.markdown('<div class="hint">Opening a cycle shows its full reconciliation, '
                'read-only. Starting a cycle loads this project’s saved bid '
                'schedule and the contractor’s prior billing.</div>',
                unsafe_allow_html=True)


def _add_contractor(ov: dict) -> None:
    with st.expander("Add a contractor to this project"):
        existing = [c for c in contractor_names() if c not in ov["contractors"]]
        with st.form(f"attach_{ov['id']}"):
            picked = st.multiselect("Existing contractors", existing)
            added = st.text_input("Or add new (comma-separated)")
            submitted = st.form_submit_button("Attach", type="primary")
        if submitted:
            names = list(picked) + [n.strip() for n in added.split(",") if n.strip()]
            if not names:
                st.error("Pick or name at least one contractor.")
            else:
                attach_contractors(ov["id"], names)
                st.rerun()


# --------------------------------------------------------------------------- #
#  Read-only cycle drill-in
# --------------------------------------------------------------------------- #
def _cycle_topbar(detail: dict, sid: str) -> str:
    q = f"&sid={sid}" if sid else ""
    title = f'{detail["project_name"]} · Cycle {detail["cycle_no"]:02d}'
    crumb = (f'Home › {_esc(detail["project_name"])} › '
             f'Cycle {detail["cycle_no"]:02d}')
    back = (f'<a class="btn" href="?step=project&pid={detail["project_id"]}{q}" '
            f'target="_self">← Back to project</a>')
    return (f'<div class="top"><div><div class="top__crumb">{crumb}</div>'
            f'<div class="top__title">{_esc(title)}</div></div>'
            f'<div class="top__actions">{back}</div></div>')


def _cycle_kpis(totals, retainage_pct: float) -> list[dict]:
    return [
        {"label": "Billed this cycle", "value": f"${totals.total_billed:,.0f}",
         "sub": "gross, before retainage"},
        {"label": "Expected (built × contract)",
         "value": f"${totals.total_expected:,.0f}", "sub": "documented work"},
        {"label": "Flagged over-billing",
         "value": f"${totals.flagged_over_billing:,.0f}",
         "sub": f"{totals.n_critical} critical items", "flag": True},
        {"label": f"Retainage held ({retainage_pct:.0f}%)",
         "value": f"${totals.retainage_held:,.0f}", "sub": "withheld this cycle"},
        {"label": "Net recommended", "value": f"${totals.net_recommended:,.0f}",
         "sub": "after flags & retainage"},
    ]


def render_cycle(state) -> None:
    from recon.reconcile import cycle_totals

    sid = get_sid()
    raw = st.query_params.get("cid", "")
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        cid = None
    detail = cycle_detail(cid) if cid is not None else None

    if detail is None:
        st.sidebar.markdown(sidebar_home_html(0, 0, 0, [], sid),
                            unsafe_allow_html=True)
        st.markdown(project_topbar_html("Cycle not found", sid),
                    unsafe_allow_html=True)
        st.warning("That cycle could not be found.")
        return

    _render_sidebar("projects", sid, portfolio())
    st.markdown(_cycle_topbar(detail, sid), unsafe_allow_html=True)

    meta = " · ".join(x for x in [
        detail["contractor"], detail["period_label"],
        f'{detail["billing_mode"]} billing' if detail["billing_mode"] else ""] if x)
    st.markdown(f'<div class="lede">Read-only view of a saved cycle — {_esc(meta)}.'
                '</div>', unsafe_allow_html=True)

    rows = detail["rows"]
    totals = cycle_totals(rows, retainage_pct=detail["retainage_pct"])
    st.markdown(kpi_row_html(_cycle_kpis(totals, detail["retainage_pct"])),
                unsafe_allow_html=True)
    st.markdown(recon_list_html(rows, detail["resolutions"]),
                unsafe_allow_html=True)
    st.markdown('<div class="hint">Reviewer decisions are shown as saved. Source-'
                'file feeds aren’t stored with the cycle, so those lines are '
                'omitted here.</div>', unsafe_allow_html=True)
