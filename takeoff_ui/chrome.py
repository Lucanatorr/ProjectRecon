"""Field-takeoff sidebar and top bar, in the Reconciliation app's visual language."""
from __future__ import annotations

from takeoff_ui.state import VIEWS, TakeoffState
from ui.theme import _FIBER, _esc, app_switch_html


def sidebar_html(state: TakeoffState, sid: str, recon_step: str) -> str:
    q = f"&sid={sid}" if sid else ""
    ribbon = "".join(f'<i style="background:{c}"></i>' for c in _FIBER)
    counts = {
        "drawings": sum(len(t.counted_files) for t in state.drawings),
        "invoices": len(state.invoices.counted_docs) if state.invoices else 0,
    }
    nav = "".join(
        f'<a class="{"on" if key == state.view else ""}" href="?app=takeoff&view={key}{q}" '
        f'target="_self">{_esc(title)}<span class="ct">{counts[key]}</span></a>'
        for key, title, _ in VIEWS)
    loaded = []
    for t in state.drawings:
        loaded.append(f'<a href="?app=takeoff&view=drawings{q}" target="_self">{_esc(t.name)}'
                      f'<small>{len(t.counted_files)} drawing file(s)</small></a>')
    for name in state.invoice_uploads:
        loaded.append(f'<a href="?app=takeoff&view=invoices{q}" target="_self">{_esc(name)}'
                      f'<small>invoices</small></a>')
    loaded_block = (f'<div class="recent"><div class="recent__l">Loaded this session</div>'
                    f'{"".join(loaded)}</div>' if loaded else "")
    return f"""
    <div class="side__brand">
      <a class="side__logo" href="?app=takeoff{q}" target="_self">
        <div class="side__mark"><span></span></div>
        <div>
          <div class="side__name">Project Recon</div>
          <div class="side__sub">Field Takeoff</div>
        </div>
      </a>
      <div class="side__ribbon" title="Fiber color code">{ribbon}</div>
    </div>
    {app_switch_html("takeoff", sid, recon_step)}
    <nav class="hnav">{nav}</nav>{loaded_block}
    <div class="side__foot">
      <div class="proj">Field takeoff</div>
      <div class="proj__name">Read field documents</div>
      <div class="proj__meta">Comment boxes &amp; invoice PDFs — no OCR</div>
    </div>
    """


def topbar_html(title: str, crumb: str = "Field takeoff") -> str:
    return f"""
    <div class="top">
      <div>
        <div class="top__crumb">{_esc(crumb)} &nbsp;›&nbsp; {_esc(title)}</div>
        <div class="top__title">{_esc(title)}</div>
      </div>
      <div class="top__actions"></div>
    </div>
    """
