"""Invoice takeoff: every line item, quantity and total from contractor invoice PDFs.

Built for the one-invoice-per-page layout small contractors print from billing
software — a header (``INVOICE A101``, vendor, ``TO:`` block, date), a
*Job Description / Rate / Total* table where each row reads::

    120ft- Directional Bore- Pulling 2"    $7.50/ft    $900.00

optional ``Project - …`` and location lines between rows, and a closing
``Total $3,650.00``. Text is read from the PDF itself — no OCR.

Every row is checked: quantity × rate against the billed amount, the rows
against the printed invoice total, and each rate against the rate usually billed
for the same kind of work. A page that repeats an invoice already in the batch
(same number, same rows, same total) is kept for reference but counted once.
"""
from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from takeoff.sources import PdfSource

_MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
           "November|December")
_DATE_WORDS = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s*(\d{{4}})", re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
# the number must carry a digit, so "INVOICE INFORMATION" / "Invoice Date" never match
_INVOICE_NO = re.compile(r"\bINVOICE\s*(?:NO\.?|NUMBER|#)?\s*:?\s*([A-Z]{0,4}\d[A-Z0-9-]*)\b", re.I)
_ITEM = re.compile(
    r"^(?P<desc>.*?\S)\s+\$\s*(?P<rate>[\d,]*\.?\d+)\s*(?:/\s*(?P<per>[A-Za-z]+))?"
    r"\s+\$\s*(?P<amt>\d[\d ,]*\.\d{2})\s*$")
_TOTAL = re.compile(r"^TOTAL\s*:?\s*\$\s*(\d[\d ,]*\.\d{2})\s*$", re.I)
_LONE_TOTAL = re.compile(r"^TOTAL\s*:?$", re.I)
_LONE_MONEY = re.compile(r"^\$\s*(\d[\d ,]*\.\d{2})\s*$")
_HEADER = re.compile(r"\bDESCRIPTION\b.*\b(RATE|PRICE)\b", re.I)


@dataclass
class InvoiceItem:
    file: str
    page: int
    invoice: str
    date: date | None
    project: str
    section: str              # location sub-heading on multi-site invoices
    desc: str                 # as written
    qty: float
    unit: str                 # FT | EA | HR
    rate: float
    rate_unit: str            # the "/ft", "/ea"… as written ("" when absent)
    amount: float             # as billed
    work_item: str = ""
    notes: list[str] = field(default_factory=list)   # needs a human look
    info: list[str] = field(default_factory=list)    # how it was read

    @property
    def qty_x_rate(self) -> float:
        return round(self.qty * self.rate, 2)

    @property
    def difference(self) -> float:
        return round(self.amount - self.qty_x_rate, 2)


@dataclass
class InvoiceDoc:
    file: str
    page: int
    number: str
    date: date | None
    vendor: str
    bill_to: str
    project: str
    total: float | None
    items: list[InvoiceItem] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    duplicate_of: tuple[str, int] | None = None    # (file, page) it repeats

    @property
    def counted(self) -> bool:
        return self.duplicate_of is None

    @property
    def line_sum(self) -> float:
        return round(sum(i.amount for i in self.items), 2)

    @property
    def ref(self) -> str:
        return f"{self.file} p{self.page}"

    def signature(self) -> tuple:
        return (self.number, self.total, tuple((i.desc, i.amount) for i in self.items))


# --------------------------------------------------------------------------- #
#  reading
# --------------------------------------------------------------------------- #
def _money(s: str) -> float:
    return float(re.sub(r"[\s$,]", "", s))


def _parse_date(text: str) -> date | None:
    m = _DATE_WORDS.search(text)
    if m:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    m = _DATE_NUM.search(text)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _quantity(desc: str, rate_unit: str) -> tuple[float, str, list[str], list[str]]:
    """Quantity and unit from a description such as ``-1,473ft- Directional Bore``,
    ``4-Set Small Vault``, ``2 Men, truck… - 3 hours`` or ``Set LV Box`` (no qty)."""
    d = re.sub(r"^[-\s]+", "", desc)      # a leading "-" is a bullet, not a minus
    notes, info = [], []
    per = rate_unit.lower()
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", d, re.I)
    lead = re.match(r"^([\d,]+(?:\.\d+)?)\s*(ft|lf|'|ea)?\b", d, re.I)
    if hours:
        qty, unit = float(hours.group(1)), "HR"
        info.append("quantity is the hours written in the description")
    elif lead:
        qty = float(lead.group(1).replace(",", ""))
        mark = (lead.group(2) or "").lower()
        unit = "FT" if mark in ("ft", "lf", "'") or per in ("ft", "lf") else "EA"
        if not mark and per in ("ft", "lf"):
            info.append('no "ft" after the quantity; the rate is per foot')
    else:
        qty, unit = 1.0, "EA"
        notes.append("no quantity written; 1 assumed")
    if per and per not in ("ft", "lf", "ea", "each", "hr", "hour"):
        notes.append(f'rate unit written "/{rate_unit}"')
    return qty, unit, notes, info


def parse_page(text: str, file: str, page: int) -> InvoiceDoc | None:
    """One page's text → an invoice, or None when the page is not an invoice."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    num = next((m.group(1) for l in lines if (m := _INVOICE_NO.search(l))), None)
    if not num:
        return None
    first = next(i for i, l in enumerate(lines) if _INVOICE_NO.search(l))
    vendor = lines[first + 1] if first + 1 < len(lines) else ""
    dt = _parse_date(text)
    bill_to = ""
    for i, l in enumerate(lines):
        if re.match(r"^(BILL\s*)?TO\s*:", l, re.I):
            for nxt in lines[i + 1:i + 4]:
                if "@" not in nxt and not _HEADER.search(nxt):
                    bill_to = nxt
                    break
            break
    start = next((i + 1 for i, l in enumerate(lines) if _HEADER.search(l)), first + 1)
    doc = InvoiceDoc(file=file, page=page, number=num, date=dt, vendor=vendor,
                     bill_to=bill_to, project="", total=None)
    section_buf: list[str] = []
    section = ""
    body = lines[start:]
    for k, l in enumerate(body):
        if l.lower().startswith("thank you"):
            break
        mt = _TOTAL.match(l)
        if mt:
            doc.total = _money(mt.group(1))
            continue
        # some layouts print the total amount on its own line just above or
        # below a bare "Total"
        if _LONE_TOTAL.match(l):
            continue
        ml = _LONE_MONEY.match(l)
        if ml and ((k + 1 < len(body) and _LONE_TOTAL.match(body[k + 1]))
                   or (k > 0 and _LONE_TOTAL.match(body[k - 1]))):
            doc.total = _money(ml.group(1))
            continue
        if re.match(r"^PROJECT\s*[-:]", l, re.I):
            doc.project = re.sub(r"^PROJECT\s*[-:]\s*", "", l, flags=re.I)
            section_buf = []
            continue
        mi = _ITEM.match(l)
        if mi:
            if section_buf:
                section = " ".join(section_buf).rstrip(" -")
                section_buf = []
            desc = mi.group("desc").strip()
            rate_unit = mi.group("per") or ""
            qty, unit, notes, info = _quantity(desc, rate_unit)
            amt_txt = mi.group("amt")
            if re.search(r"\d\s+[\d,]", amt_txt):
                info.append(f'amount printed with stray spaces ("${amt_txt.strip()}")')
            doc.items.append(InvoiceItem(
                file=file, page=page, invoice=num, date=dt, project=doc.project,
                section=section, desc=desc, qty=qty, unit=unit,
                rate=float(mi.group("rate").replace(",", "")), rate_unit=rate_unit,
                amount=_money(amt_txt), notes=notes, info=info))
            continue
        if "$" in l:
            doc.unreadable.append(l)
        else:
            section_buf.append(l)
    if not doc.items and doc.total is None:
        return None            # an invoice number alone (a cover sheet, a reference) isn't an invoice
    return doc


def _candidate_pages(data: bytes) -> tuple[int, list[int]]:
    """(page count, 0-based pages that could be invoices). PDFium reads a page's
    raw text in milliseconds, where a full layout read of a drawing sheet can take
    seconds, so pages with no "INVOICE" or no "$" amount are skipped up front."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            text = tp.get_text_range()
            tp.close()
            page.close()
            if "$" in text and re.search(r"\bINVOICE\b", text, re.I):
                out.append(i)
        return len(pdf), out
    finally:
        pdf.close()


def read_invoices(source: PdfSource) -> tuple[list[InvoiceDoc], list[int]]:
    """Every invoice in one PDF (one per page), plus the pages that weren't invoices."""
    import pdfplumber
    n_pages, candidates = _candidate_pages(source.data)
    docs = []
    if candidates:
        with pdfplumber.open(io.BytesIO(source.data), pages=[i + 1 for i in candidates]) as pdf:
            for i, page in zip(candidates, pdf.pages):
                doc = parse_page(page.extract_text() or "", source.name, i + 1)
                if doc is not None:
                    docs.append(doc)
                page.flush_cache()
    found = {d.page for d in docs}
    return docs, [p for p in range(1, n_pages + 1) if p not in found]


# --------------------------------------------------------------------------- #
#  classification and checks
# --------------------------------------------------------------------------- #
def work_item(desc: str) -> str:
    """Group a line into a kind of work, so quantities can be totalled across
    invoices that word the same work differently. Unknown work keeps its own
    wording (minus the quantity) as its group."""
    d = desc.lower()
    if "men, truck" in d or "labor" in d or "crew" in d:
        return "Crew: men, truck, trailer & excavator"
    if "directional" in d and "1 1/4" in d:
        return 'Directional drilling, pulling 1 (1 1/4")'
    if "directional" in d or re.search(r"\bbore\b", d):
        return 'Directional bore, pulling 2"'
    if "plow/trench additional duct" in d or "trench/plow additional duct" in d:
        return "Plow/trench additional duct"
    if "trench" in d or "plow" in d:
        return "Trench/plow"
    if "additional duct" in d or re.fullmatch(r"[-\s\d,]*(ft)?[-\s]*pulled additional\s*", d):
        return "Pull additional duct"
    if re.search(r"\b24\s*ct\b", d):
        return "Pull 24ct fiber through existing duct"
    if "additional fiber" in d or "additonal fiber" in d:
        return "Pull additional fiber (same duct)"
    if "fiber" in d or "fiver" in d:
        return "Pull fiber through installed/existing duct"
    if "pedestal" in d:
        return "Set/place pedestal"
    if "drop vault" in d:
        return "Set drop vault"
    if "extra large" in d or "extra lv" in d or "36x60x36" in d:
        return "Set extra large vault 36x60x36"
    if "removed" in d or "remove " in d:
        return "Remove vault/box"
    if "large vault" in d or "lv box" in d or "24x48x36" in d:
        return "Set large vault / LV box"
    if "small vault" in d or "24x36x36" in d:
        return "Set small vault / box / handhole 24x36x36"
    return re.sub(r"^[-\s]*[\d,.]+\s*(ft)?\s*[-]?\s*", "", desc).strip() or desc


_FOOTAGE_WORK = ('Directional bore, pulling 2"', 'Directional drilling, pulling 1 (1 1/4")',
                 "Plow/trench additional duct", "Trench/plow", "Pull additional duct",
                 "Pull 24ct fiber through existing duct", "Pull additional fiber (same duct)",
                 "Pull fiber through installed/existing duct")
_COUNTED_WORK = ("Set/place pedestal", "Set drop vault", "Set extra large vault 36x60x36",
                 "Remove vault/box", "Set large vault / LV box",
                 "Set small vault / box / handhole 24x36x36")
_UNIT_BY_WORK = {**{w: "FT" for w in _FOOTAGE_WORK}, **{w: "EA" for w in _COUNTED_WORK}}


@dataclass
class InvoiceSet:
    docs: list[InvoiceDoc]
    skipped_pages: list[tuple[str, int]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)   # same number, different content

    @property
    def counted_docs(self) -> list[InvoiceDoc]:
        return [d for d in self.docs if d.counted]

    def items(self, counted_only: bool = False) -> list[InvoiceItem]:
        docs = self.counted_docs if counted_only else self.docs
        return [i for d in docs for i in d.items]

    def totals(self) -> dict:
        def tot(docs):
            return round(sum(d.total if d.total is not None else d.line_sum for d in docs), 2)
        return {"unique_invoices": len(self.counted_docs), "unique_total": tot(self.counted_docs),
                "pages": len(self.docs), "all_pages_total": tot(self.docs),
                "repeat_pages": len(self.docs) - len(self.counted_docs),
                "repeat_total": round(tot(self.docs) - tot(self.counted_docs), 2)}

    def by_work_item(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for i in self.items(counted_only=True):
            a = agg.setdefault(i.work_item, {"work_item": i.work_item, "lines": 0, "qty": 0.0,
                                             "units": set(), "amount": 0.0, "rates": set()})
            a["lines"] += 1
            a["qty"] += i.qty
            a["units"].add(i.unit)
            a["amount"] += i.amount
            a["rates"].add(i.rate)
        out = [{**a, "units": sorted(a["units"]), "rates": sorted(a["rates"]),
                "amount": round(a["amount"], 2)} for a in agg.values()]
        return sorted(out, key=lambda a: -a["amount"])

    def flagged(self) -> list[tuple[str, str, str]]:
        """(where, what was written, why it needs a look)."""
        out = [(f"{i.invoice} · {i.file} p{i.page}", i.desc, n)
               for d in self.docs if d.counted for i in d.items for n in i.notes]
        for d in self.docs:
            if d.total is not None and abs(d.line_sum - d.total) > 0.005:
                out.append((f"{d.number} · {d.ref}", f"Total ${d.total:,.2f}",
                            f"the lines add to ${d.line_sum:,.2f}"))
            if d.total is None:
                out.append((f"{d.number} · {d.ref}", "(no Total line)",
                            "no printed total found; the lines are summed instead"))
            out += [(f"{d.number} · {d.ref}", u, "a $ amount that is not a line item")
                    for u in d.unreadable]
        out += [("", c, "invoice number reused with different lines") for c in self.conflicts]
        return out


def check(invoices: InvoiceSet) -> InvoiceSet:
    """Mark repeat copies, classify work, and run the arithmetic / rate checks."""
    first: dict[str, InvoiceDoc] = {}
    for d in invoices.docs:
        d.duplicate_of = None
        f = first.get(d.number)
        if f is None:
            first[d.number] = d
        elif f.signature() == d.signature():
            d.duplicate_of = (f.file, f.page)
        else:
            invoices.conflicts.append(f"{d.number}: {f.ref} and {d.ref} differ — both counted")
    for i in invoices.items():
        i.work_item = work_item(i.desc)
        # the kind of work decides the unit; a rate printed "/ft" on a pedestal or
        # "/ea" on pulled duct is a typo on the invoice, noted but not followed
        kind = _UNIT_BY_WORK.get(i.work_item)
        if kind and i.unit != "HR" and kind != i.unit:
            i.notes.append(f'rate written "${i.rate:,.2f}/{i.rate_unit}" for '
                           f'{"a counted item" if kind == "EA" else "footage"}; read as '
                           f'{"each" if kind == "EA" else "per foot"}')
            i.unit = kind
        if i.difference:
            i.notes.append(f"quantity × rate = ${i.qty_x_rate:,.2f}, but ${i.amount:,.2f} is "
                           f"billed (difference {i.difference:+,.2f})")
        if i.work_item == "Pull additional duct" and "duct" not in i.desc.lower():
            i.info.append('written "Pulled Additional" with no item; read as additional duct')
        if i.work_item.startswith("Directional bore") and '2"' not in i.desc:
            i.info.append('"Pulling 2\\"" not written; grouped with the 2" bore')
    # a rate that differs from the rate usually billed for the same work
    rates: dict[str, Counter] = defaultdict(Counter)
    for i in invoices.items(counted_only=True):
        rates[i.work_item][i.rate] += 1
    for i in invoices.items(counted_only=True):
        common = rates[i.work_item].most_common()
        if len(common) > 1 and common[0][1] > common[1][1] and i.rate != common[0][0]:
            i.notes.append(f"rate ${i.rate:,.2f}; this work is usually billed at "
                           f"${common[0][0]:,.2f}")
    return invoices


def invoices_from_sources(sources: list[PdfSource]) -> InvoiceSet:
    docs, skipped = [], []
    for s in sources:
        d, sk = read_invoices(s)
        docs.extend(d)
        skipped.extend((s.name, p) for p in sk)
    return check(InvoiceSet(docs=docs, skipped_pages=skipped))


# --------------------------------------------------------------------------- #
#  hand-off to Recon
# --------------------------------------------------------------------------- #
def to_recon_lines(invoices: InvoiceSet):
    """The counted invoice lines as Recon `InvoiceLine`s, ready for a
    reconciliation cycle's Invoices step. Each invoice bills its own period
    (not a cumulative pay app), so lines are discrete. The work-item grouping is
    the description, so the Crosswalk maps each kind of work once."""
    from recon.models import InvoiceLine
    return [InvoiceLine(invoice_id=i.invoice, raw_desc=i.work_item, qty=i.qty,
                        unit_price=i.rate, amount=i.amount,
                        period=i.date.isoformat() if i.date else None, is_cumulative=False,
                        source_file=i.file, line_ref=f"p{i.page}: {i.desc}")
            for i in invoices.items(counted_only=True)]
