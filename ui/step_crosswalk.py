"""Step 3 — Crosswalk: resolve unmapped descriptions.

Review items render as the mockup's .xw cards. Actions are query-param links
(Confirm accepts the default mapping; Change reveals a selectbox), which work
because wizard state survives the reload. Confirmed mappings persist to the
session alias store."""
from __future__ import annotations

from urllib.parse import quote

import streamlit as st

from recon.crosswalk import resolve
from ui.state import WizardState, get_sid
from ui.theme import card_close, card_open, lede, xw_card_html, xw_resolved_html

NOT_IN_CONTRACT = "__not_in_contract__"
# Below this fuzzy score a suggestion is too weak to accept by default; the card
# defaults to not-in-contract instead (matches the mockup).
SUGGEST_THRESHOLD = 60

_LEDE = ("This is the spreadsheet matching you do by hand — automated. Descriptions "
         "that mapped themselves from prior jobs are confirmed; review the rest and "
         "the tool remembers them for good.")


def _all_descs(state: WizardState) -> list[str]:
    seen, out = set(), []
    for ln in list(state.asbuilt) + list(state.invoices):
        if ln.raw_desc not in seen:
            seen.add(ln.raw_desc)
            out.append(ln.raw_desc)
    return out


def _href(sid: str, **params) -> str:
    q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"?step=crosswalk&sid={sid}&{q}"


def _process_actions(state: WizardState) -> None:
    """Apply any confirm / not-in-contract / reopen action from the query string."""
    qp = st.query_params
    if "xwc" in qp:                       # confirm suggested default
        desc = qp["xwc"]
        m = resolve(desc, state.contract, state.aliases)
        if m.candidates and m.candidates[0].score >= SUGGEST_THRESHOLD:
            _confirm_mapping(state, desc, m.candidates[0].code)
        else:
            _mark_not_in_contract(state, desc)
    if "xwmap" in qp:                     # confirm a specific code (from Change)
        desc = qp.get("xwmap")
        code = qp.get("code")
        if desc and code:
            if code == NOT_IN_CONTRACT:
                _mark_not_in_contract(state, desc)
            else:
                _confirm_mapping(state, desc, code)
    if "xwreopen" in qp:                  # move a resolved item back to review
        _reopen_mapping(state, qp["xwreopen"])


def _confirm_mapping(state: WizardState, desc: str, code: str) -> None:
    """Confirm a description → code mapping, learning it globally (FR-7) and
    recording it in the audit log (FR-17)."""
    from ui.db import confirm_alias
    if state.resolved.get(desc) == code:
        return                            # already settled — don't re-log
    state.not_in_contract.discard(desc)
    state.resolved[desc] = code
    state.aliases.confirm(desc, code)
    confirm_alias(desc, code, actor=state.reviewer or None)


def _mark_not_in_contract(state: WizardState, desc: str) -> None:
    from ui.db import log_action
    if desc in state.not_in_contract:
        return
    state.resolved.pop(desc, None)
    state.not_in_contract.add(desc)
    log_action("mark_not_in_contract", "alias", actor=state.reviewer or None,
               detail={"desc": desc})


def _reopen_mapping(state: WizardState, desc: str) -> None:
    from ui.db import delete_alias
    was_mapped = desc in state.resolved or desc in state.not_in_contract
    state.resolved.pop(desc, None)
    state.not_in_contract.discard(desc)
    state.aliases.remove(desc)
    if was_mapped:
        delete_alias(desc, actor=state.reviewer or None)


def render(state: WizardState) -> None:
    st.markdown(lede(_LEDE), unsafe_allow_html=True)
    if not state.contract:
        st.warning("Load a bid schedule first (Contract step).")
        return
    if not state.asbuilt and not state.invoices:
        st.info("Add as-built and/or invoices first.")
        return

    _process_actions(state)
    sid = get_sid()
    descs = _all_descs(state)
    code_desc = {ci.code: ci.description for ci in state.contract}

    auto, review = [], []
    for d in descs:
        if d in state.not_in_contract:
            auto.append((d, NOT_IN_CONTRACT, "not in contract"))
            continue
        if d in state.resolved:
            auto.append((d, state.resolved[d], "confirmed"))
            continue
        m = resolve(d, state.contract, state.aliases)
        if m.code is not None:
            state.resolved[d] = m.code          # accept auto/alias mappings
            auto.append((d, m.code, m.kind))
        else:
            review.append((d, m))

    edit = st.query_params.get("xwe")           # desc currently in "Change" mode

    # Build the review card as one HTML block so the .card wraps the .xw rows.
    header = card_open(
        f"Needs review · {len(review)} of {len(descs)}",
        "Confirmed mappings are saved to the shared crosswalk and reused on every "
        "future reconciliation.")
    cards, edit_items = [], []
    for d, m in review:
        if edit == d:
            edit_items.append((d, m))
            continue
        if m.candidates and m.candidates[0].score >= SUGGEST_THRESHOLD:
            c = m.candidates[0]
            sug = (f'maps to → <b>{c.code} · {code_desc.get(c.code, c.description)}</b> '
                   f'<span class="score">{c.score:.0f}% · {c.field}</span>')
            cards.append(xw_card_html(d, sug, _href(sid, xwc=d), _href(sid, xwe=d),
                                      confirm_label="Confirm"))
        else:
            sug = ('<span style="color:var(--critical)">no confident contract match '
                   '— likely needs a change order</span>')
            cards.append(xw_card_html(d, sug, _href(sid, xwc=d), _href(sid, xwe=d),
                                      confirm_label="Mark not-in-contract", low=True))
    if not review:
        cards.append('<div class="card__note">All descriptions resolved. Continue '
                     'to reconciliation.</div>')
    st.markdown(header + "".join(cards) + card_close(), unsafe_allow_html=True)

    for d, m in edit_items:
        _edit_widget(state, d, m, sid)

    # resolved list
    if auto:
        with st.expander(f"Mapped · {len(auto)}", expanded=not review):
            for d, code, kind in auto:
                if code == NOT_IN_CONTRACT:
                    target = '<b>not in contract</b> (needs change order)'
                else:
                    target = f'<b>{code} · {code_desc.get(code, "")}</b> <i>({kind})</i>'
                st.markdown(xw_resolved_html(d, target, _href(sid, xwreopen=d)),
                            unsafe_allow_html=True)

    if not review:
        st.success("All descriptions resolved. Continue to reconciliation.")
        state.done.add("crosswalk")


def _edit_widget(state: WizardState, desc: str, m, sid: str) -> None:
    """Streamlit selectbox for the one item being changed."""
    with st.container(border=True):
        st.markdown(f'<div class="xw__from">Source text</div>'
                    f'<div class="xw__raw">{desc}</div>', unsafe_allow_html=True)
        NIC = "— not in contract —"
        cand = [f"{c.code} · {c.description}" for c in m.candidates]
        opts = cand + [o for o in
                       (f"{ci.code} · {ci.description}" for ci in state.contract)
                       if o not in cand] + [NIC]
        choice = st.selectbox("Map to", opts, key=f"xwedit_{desc}")
        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Save", type="primary", key=f"xwsave_{desc}"):
                code = NOT_IN_CONTRACT if choice == NIC else choice.split(" · ")[0]
                if code == NOT_IN_CONTRACT:
                    _mark_not_in_contract(state, desc)
                else:
                    _confirm_mapping(state, desc, code)
                st.query_params.pop("xwe", None)
                st.rerun()


def apply_codes(state: WizardState) -> None:
    """Write resolved codes onto the as-built and invoice lines (called before
    reconcile). Descriptions marked not-in-contract keep code=None."""
    for ln in list(state.asbuilt) + list(state.invoices):
        if ln.raw_desc in state.not_in_contract:
            ln.code = None
        elif ln.raw_desc in state.resolved:
            ln.code = state.resolved[ln.raw_desc]
        else:
            ln.code = resolve(ln.raw_desc, state.contract, state.aliases).code
