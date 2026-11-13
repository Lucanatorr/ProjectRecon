"""Takeoff — read field documents into quantities.

Two tools that share one core:

* **Drawings** (`takeoff.drawings`): the Adobe comment boxes on marked-up
  construction PDFs → material totals (MATERIALS boxes) and fiber footage by fiber
  count (sequential calculations such as ``48CT 12400-11950=450'``).
* **Invoices** (`takeoff.invoices`): contractor invoice PDFs, one invoice per page
  → every line item with quantity, rate and total, checked and de-duplicated.

Pure Python, no Streamlit — the UI lives in ``takeoff_ui``. Comment extraction is
shared with Recon's as-built ingest (`recon.ingest.asbuilt_annot`), and parsed
invoice lines convert to Recon's own `InvoiceLine` so a takeoff can be handed to a
reconciliation cycle.
"""
