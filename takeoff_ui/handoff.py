"""Field takeoff → Reconciliation hand-off.

Kept free of Streamlit calls so it can be tested directly: it only rewrites the
Reconciliation wizard's state for the same session.
"""
from __future__ import annotations

from takeoff.invoices import InvoiceSet, to_recon_lines


def send_invoices_to_recon(ws, invoices: InvoiceSet) -> str:
    """Make the counted invoice lines the Reconciliation cycle's invoices.

    These invoices each bill their own period, so the cycle is switched to
    *discrete* billing (lines are summed, not read as pay-app to-date figures).
    Returns a one-line summary, also left as the wizard's flash message."""
    lines = to_recon_lines(invoices)
    ws.invoices = lines
    ws.invoice_files = sorted({ln.source_file for ln in lines if ln.source_file})
    ws.billing_mode = "discrete"
    ws.pending_pdf, ws.pending_pdf_name, ws.invoice_profile_note = None, "", ""
    ws.done.discard("invoices")      # the reviewer still confirms them in the Invoices step
    t = invoices.totals()
    summary = (f"{len(lines)} line item(s) from {t['unique_invoices']} invoice(s) "
               f"(${t['unique_total']:,.2f}) sent from Field takeoff; billing mode set to "
               "discrete")
    ws.flash = summary + "."
    try:
        from ui.db import log_action
        log_action("load_invoices", "invoice", actor=ws.reviewer or None,
                   detail={"files": ws.invoice_files, "lines": len(lines),
                           "billing_mode": "discrete", "source": "field takeoff"})
    except Exception:            # the audit log must never block the hand-off
        pass
    return summary
