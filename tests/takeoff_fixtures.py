"""Synthetic PDFs for the Field-takeoff tests.

Every name, cable ID and number here is invented. Real construction drawings and
invoices carry customer and contractor details and are never committed.
"""
from __future__ import annotations

import io


def _pdf_string(text: str) -> str:
    """A PDF hex string; comment lines are carriage-return separated, as Adobe
    writes them."""
    return "<" + text.replace("\n", "\r").encode("latin-1").hex() + ">"


def comment_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF whose pages carry the given FreeText comment boxes
    (one inner list per page)."""
    objs: list[str] = []

    def add(body: str) -> int:
        objs.append(body)
        return len(objs)

    catalog = add("")                         # 1, filled in below
    pages_obj = add("")                       # 2
    page_ids = []
    for comments in pages:
        annot_ids = []
        for i, c in enumerate(comments):
            y = 700 - i * 90
            annot_ids.append(add(f"<< /Type /Annot /Subtype /FreeText /Rect [72 {y} 260 {y + 80}] "
                                 f"/Contents {_pdf_string(c)} /T (Inspector) /DA (/Helv 9 Tf 0 g) >>"))
        content = add("<< /Length 0 >>\nstream\n\nendstream")
        annots = " ".join(f"{a} 0 R" for a in annot_ids)
        page_ids.append(add(f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 612 792] "
                            f"/Contents {content} 0 R /Annots [{annots}] >>"))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>"
    objs[pages_obj - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
                           f"/Count {len(page_ids)} >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
              .encode())
    return out.getvalue()


def invoice_pdf(pages: list[list[str]]) -> bytes:
    """An invoice PDF with one page per inner list, each line drawn as one text
    row — the way small-contractor billing software prints them."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for lines in pages:
        y = 740
        for line in lines:
            # "desc | $rate | $amount" → three columns on one row
            parts = [p.strip() for p in line.split("|")]
            for x, part in zip((60, 380, 480), parts):
                c.drawString(x, y, part)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def acme_invoice(number: str, date: str, rows: list[str], total: str, *,
                 project: str = "Project - FB-TEST-0001 Maple Ridge",
                 bill_to: str = "Example Electric Co-op") -> list[str]:
    """One invoice page in the layout the parser is built for."""
    return [f"INVOICE {number}", "ACME UNDERGROUND, LLC", "PO Box 100", "Anytown, NC 00000",
            f"TO: Pat Example, Fiber Manager | {date}", bill_to, "ap@example.com",
            "Job Description | Rate | Total", project, *rows, f"Total | {total}",
            "Thank You for your Business!!!"]
