"""As-built from PDF **comment annotations**.

Contractors mark up the construction PDF with Adobe comments (FreeText
annotations) — one per span/structure. Two conventions are in the wild:

*Shorthand* (cable + station + footage, hardware by name)::

    AFO 288F | 6288 | 410' | AFO BOND | AFO SL

*Rate-coded* (the actual contract codes, with segment/route detail)::

    AFO 48 (F) | 19984 | AFO - 72 | 1-AFO.BANDING | 1-AFO.BOND
    BFO.48.I (F) | In - 10478 | Tip - 10428 | Out - 10378 | BFO - 736 | Pipe - 636
    BM60(2-1.25") | Plow=636'

Both are machine-readable text, so they're extracted directly — no OCR — and
parsed into quantities keyed by the rate sheet's codes.

Billing rules (from the coordinator):
  * Aerial span footage is **strand & lash** — a bare ``AFO`` just omits the
    ``.SL``; both forms bill ``AFO SL <count>FOC``, counted once.
  * ``OLASH`` on a span **replaces** the placement: an overlashed span bills
    ``AFO.OLASH`` only (its own footage, else the span's).
  * ``AFO Coil`` = one ``AFO.S`` per coil; its footage is already inside the span.
  * ``Pipe`` in a buried comment is reference — conduit bills from the ``BM60`` line.
  * A bare ``Bore`` is always the ``DP`` (hydraulic bore) unit.
  * ``DG`` = down guy (``AFO.GG``) and implies its anchor (``AFO.GAA``) *only* when
    the comment has no explicit GAA.
  * ``Duplicate Page`` comments are ignored entirely.
  * ``PM2A`` is not a contract unit.

Anything that can't be confidently classified is surfaced in ``unresolved`` for the
review grid rather than guessed at, and every derived line is matched against the
loaded bid schedule (see ``to_asbuilt_lines``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from recon.models import AsBuiltLine, UoM

# --------------------------------------------------------------------------- #
#  rate-code registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ItemType:
    key: str
    label: str
    uom: UoM
    code: str | None          # rate-sheet code; None = needs the coordinator


# Strand-and-lash aerial fiber, per fiber count (rate sheet exact codes).
_AFO_SL_CODE = {24: "AFO SL 24FOC", 48: "AFO SL48FOC", 72: "AFO SL 72FOC",
                96: "AFO SL 96FOC", 144: "AFO SL 144FOC", 288: "AFO SL 288FOC"}
# Buried fiber, per fiber count: in new conduit (.I) or existing duct (.IE).
_BFO_COUNTS = (12, 24, 48, 72, 96, 144, 288)

# MST tail-length brackets (Each, priced by tail length) for HST-style comments.
_MST_BRACKETS = (150, 250, 350, 500, 750, 1000, 1250, 1500)

ITEM_TYPES: dict[str, ItemType] = {
    "afo_sl": ItemType("afo_sl", "Aerial strand-only (no fiber)",
                       UoM.FT, "AFO SL- STRAND"),
    "coil": ItemType("coil", "Slack coil / snow shoe", UoM.EA, "AFO.S"),
    "anchor": ItemType("anchor", "Screw anchor with down guy", UoM.EA, "AFO.GAA"),
    "down_guy": ItemType("down_guy", "Down guy with guy guard", UoM.EA, "AFO.GG"),
    "bond": ItemType("bond", "Aerial bond", UoM.EA, "AFO.BOND"),
    "banding": ItemType("banding", "Metal banding on pole", UoM.EA, "AFO.BANDING"),
    "transfer": ItemType("transfer", "Pole transfer (same pole)", UoM.EA, "AFO.TRAN2"),
    "eye": ItemType("eye", "Auxiliary eye", UoM.EA, "AFO.EYE"),
    "olash": ItemType("olash", "Overlash fiber", UoM.FT, "AFO.OLASH"),
    "relash": ItemType("relash", "Delash / relash", UoM.FT, "AFO.RELASH"),
    "riser_guard": ItemType("riser_guard", "Riser guard (10')", UoM.EA, "BM81"),
    "marker_post": ItemType("marker_post", "Fiber marking post", UoM.EA, "BM53"),
    "locate_post": ItemType("locate_post", "Locate post", UoM.EA, "BM55"),
    "locate_disk": ItemType("locate_disk", "3M locate disk/ball", UoM.EA, "BM55A"),
    "ground_rod": ItemType("ground_rod", "Ground rod", UoM.EA, "BM2"),
    "tracer_wire": ItemType("tracer_wire", "Pull rope / tracer wire", UoM.FT, "BM90"),
    "handdig": ItemType("handdig", "Hand dig", UoM.FT, "BMHD"),
    "afo_rtd": ItemType("afo_rtd", "Aerial MST/RTD tail", UoM.FT, "AFO.RTD"),
    "bfo_rtd": ItemType("bfo_rtd", "Buried MST/RTD tail in conduit", UoM.FT,
                        "BFO.RTD.I"),
}

# Handhole/vault dimensions → BHF size bracket (coordinator's mapping).
_BHF_DIMS = {(17, 30): "BHF-17T", (24, 36): "BHF-30T", (30, 48): "BHF-48T"}

# Tokens that are notes / cross-references, never billable.
_NOTE_HINTS = ("ON PAGE", "THIS POLE", "HH ON", "MOVED TO", "PREVIOUS PAGE",
               "PER ", "LOWERED", "ADDED", "CHANGED", "DIG UP", "POT HOLE",
               "TIE INTO", "SPECTRUM", "PAYING", "RIVER CITY", "FIBER ONLY",
               "DUPLICATE")
# Station/geometry markers — positional reference, not a quantity.
_STATION_WORDS = ("IN", "OUT", "END", "TIP", "TOP", "BOP", "HEAD", "TAIL",
                  "MID", "BOTTOM", "H", "T")


def _afo_sl_type(count: int | None) -> ItemType:
    if count in _AFO_SL_CODE:
        return ItemType(f"afo_sl_{count}", f"Strand & lash {count}ct aerial fiber",
                        UoM.FT, _AFO_SL_CODE[count])
    return ITEM_TYPES["afo_sl"]


def _bfo_type(count: int | None, existing_duct: bool = False) -> ItemType:
    suffix = "IE" if existing_duct else "I"
    where = "existing duct" if existing_duct else "conduit"
    if count in _BFO_COUNTS:
        return ItemType(f"bfo_{count}_{suffix}",
                        f"Buried {count}ct fiber in {where}", UoM.FT,
                        f"BFO.{count}.{suffix}")
    return ItemType(f"bfo_{suffix}", f"Buried fiber in {where} (count TBD)",
                    UoM.FT, None)


def _mst_type(tail: int) -> ItemType:
    """MST keyed by its tail-length bracket (snapped to the nearest rate line)."""
    b = min(_MST_BRACKETS, key=lambda x: abs(x - tail))
    return ItemType(f"mst_{b}", f"{b}' MST (aerial/buried)", UoM.EA, f"MST {b}'")


def _conduit_type(n_ways: int | None, size: str | None, method: str) -> ItemType:
    """Conduit/bore keyed by way-count, size, and install method. The code mirrors
    the rate sheet's own spelling, e.g. ``BM60(2)(1.25) P`` / ``BM60-(1.25)DP``.

    **Bores price by pipe count, and the two units are far apart** — a single pipe
    is the base ``BM60-(1.25)DP``; two or more pipes pulled back together bill the
    ``BM60-(1.25)DPD Dual`` adder. An explicit DPD/Dual marking always wins, since
    the contractor is stating it outright.
    """
    n = n_ways or 1
    sz = size or "1.25"

    if method in ("DP", "DPD"):
        dual = method == "DPD" or n > 1
        code = f"BM60-({sz})DPD Dual" if dual else f"BM60-({sz})DP"
        label = (f"Bore {sz}\" — dual / multi-pipe adder" if dual
                 else f"Bore {sz}\" — single pipe, directional")
        return ItemType(f"bm60_{sz}_{'DPD' if dual else 'DP'}", label, UoM.FT, code)

    if method == "MB":
        return ItemType(f"bm60_{sz}_MB", f"Bore {sz}\" — missile", UoM.FT,
                        f"BM60-({sz})MB")

    label = {"P": "plow", "T": "open trench", "TD": "joint trench"}.get(method, method)
    return ItemType(f"bm60_{n}_{sz}_{method}",
                    f"Conduit {n}×{sz}\" — {label}", UoM.FT,
                    f"BM60({n})({sz}) {method}")


# --------------------------------------------------------------------------- #
#  extraction (pdfplumber; no OCR)
# --------------------------------------------------------------------------- #
@dataclass
class Annotation:
    page: int
    text: str
    author: str | None = None
    x: float | None = None
    y: float | None = None


def extract_annotations(pdf_path) -> list[Annotation]:
    """Every FreeText comment in the PDF, in page order. Machine-readable text —
    no OCR involved."""
    import pdfplumber

    out: list[Annotation] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            for a in (page.annots or []):
                data = a.get("data") or {}
                if "FREETEXT" not in str(data.get("Subtype", "")).upper():
                    continue
                text = (a.get("contents") or "").strip()
                if not text:
                    continue
                out.append(Annotation(page=i + 1, text=text, author=a.get("title"),
                                      x=a.get("x0"), y=a.get("top")))
            page.flush_cache()
    return out


# --------------------------------------------------------------------------- #
#  normalization
# --------------------------------------------------------------------------- #
_ALIASES = (
    (r"\bAFO[\s.\-]*6AA\b", "AFO.GAA"), (r"\bAFO[\s.\-]*GAA\b", "AFO.GAA"),
    (r"\bAFO[\s.\-]*66\b", "AFO.GG"), (r"\bAFO[\s.\-]*GG\b", "AFO.GG"),
    (r"\bAFO[\s.\-]*BOND\b", "AFO.BOND"),
    (r"\bAFO[\s.\-]*BANDING\b", "AFO.BANDING"),
    (r"\bAFO[\s.\-]*TRANS?2\b", "AFO.TRAN2"),
    (r"\bAFO[\s.\-]*TRANS?1\b", "AFO.TRAN1"),
    (r"\bAFO[\s.\-]*OLASH\b", "AFO.OLASH"), (r"\bAFO[\s.\-]*DELASH\b", "AFO.RELASH"),
    (r"\bAFO[\s.\-]*RELASH\b", "AFO.RELASH"),
    (r"\bAFO[\s.\-]*EYE\b", "AFO.EYE"),
    (r"\bAFO[\s.\-]*S\b", "AFO.S"),
    (r"\bAFO[\s.\-]*SL\b", "AFO.SL"),
)


def _norm(s: str) -> str:
    """Collapse whitespace and strip stray punctuation from a token."""
    return re.sub(r"\s+", " ", s).strip().strip("|").strip()


def _canon(token: str) -> str:
    """Upper-case a token and fold the spelling variants (AFO BOND / AFO-BOND /
    AFO.BOND → AFO.BOND) so one rule matches them all."""
    t = _norm(token).upper().replace("’", "'")
    for pat, repl in _ALIASES:
        t = re.sub(pat, repl, t)
    return t


def _num(text: str) -> float | None:
    """First number in the text, tolerating thousands commas and a foot mark."""
    m = re.search(r"(\d[\d,]*)(?:\.\d+)?", text)
    return float(m.group(1).replace(",", "")) if m else None


# Codes whose trailing number is a SIZE, not a quantity (BHF-10, BHF-48T, BM60…).
_SIZED = re.compile(r"^\(?\s*\d*\s*[-)]?\s*(BHF|BM60|BDO|BM81|BM90|BM55|BM53|BM2)",
                    re.I)


def _qty_prefix(token: str) -> tuple[int, str]:
    """Split a leading quantity: '2-BM81' → (2, 'BM81'); 'BHF.17T=1' → (1, 'BHF.17T');
    'BM2' → (1, 'BM2'). A trailing number on a sized code (BHF-10) is its size, not
    a count, so it is left attached."""
    t = _norm(token).lstrip("(").replace(")", " ", 1).strip() \
        if re.match(r"^\(\s*\d+\s*\)", _norm(token)) else _norm(token)
    m = re.match(r"^\(\s*(\d+)\s*\)\s*(.+)$", t)          # "(2) BM2"
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = re.match(r"^(\d+)\s*[-\s]?\s*([A-Za-z].*)$", t)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = re.match(r"^(.*?)\s*[=]\s*(\d+)$", t)
    if m:
        return int(m.group(2)), m.group(1).strip()
    m = re.match(r"^(.*?)\s*-\s*(\d+)$", t)
    if m and not re.search(r"\d\s*'", t) and not _SIZED.match(t):
        return int(m.group(2)), m.group(1).strip()
    return 1, t


# --------------------------------------------------------------------------- #
#  parse result
# --------------------------------------------------------------------------- #
@dataclass
class AnnotParse:
    qty: dict[str, float] = field(default_factory=dict)
    label: dict[str, str] = field(default_factory=dict)
    uom: dict[str, str] = field(default_factory=dict)
    code: dict[str, str | None] = field(default_factory=dict)
    spans: dict[str, int] = field(default_factory=dict)
    records: int = 0
    excluded: list[tuple[int, str]] = field(default_factory=list)
    notes: list[tuple[int, str]] = field(default_factory=list)
    unresolved: list[tuple[int, str]] = field(default_factory=list)

    def add(self, it: ItemType, amount: float) -> None:
        if not amount:
            return
        self.qty[it.key] = self.qty.get(it.key, 0.0) + amount
        self.spans[it.key] = self.spans.get(it.key, 0) + 1
        self.label[it.key] = it.label
        self.uom[it.key] = it.uom.value
        self.code[it.key] = it.code


# --------------------------------------------------------------------------- #
#  patterns
# --------------------------------------------------------------------------- #
_CABLE = re.compile(r"^([AB])FO[\s.]*0*(\d+)\s*F?\b", re.I)      # AFO 48 (F), BFO.96.I
_FT = re.compile(r"(\d[\d,]*)\s*'")
_TRAIL = re.compile(r"[-=]\s*(\d[\d,]*)\s*'?\s*$")
_BARE = re.compile(r"^(\d[\d,]*)\s*(MID|TOP|TAIL|UG|BOTTOM)?$", re.I)
_BHF = re.compile(r"^BHF[\s.\-]*(\d+)\s*(T)?", re.I)
_BDO = re.compile(r"^BDO\s*\(?\s*([SML])\s*\)?", re.I)
_HST = re.compile(r"HST\s*(\d+)\s*-\s*(\d+)", re.I)
_DIMS = re.compile(r"(\d+)\s*[xX]\s*(\d+)(?:\s*[xX]\s*\d+)?")
_BM60 = re.compile(r"BM60", re.I)
# way-count then size: (2-1.25") · (3)(1.25") · ((3)(1.25") · (3)1.25)
# the size always carries a decimal or an inch mark, which keeps "(3)" from being
# read as a size when the forms nest.
_WAYS = re.compile(r"\(*\s*(\d+)\s*\)?\s*[-(]?\s*(\d+\.\d+|\d+\s*(?=\"))\s*\"?")
_SIZE_ONLY = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*\"?\s*\)")
_FT_CEIL = 1200          # bare number below this = feet; at/above = station id
_MULT_CEIL = 8


def parse_annotations(annotations: list[Annotation]) -> AnnotParse:
    """Classify every comment into billable quantities."""
    res = AnnotParse()
    for ann in annotations:
        raw_toks = [t for t in re.split(r"[\r\n]", ann.text)]
        toks = [_norm(t) for t in raw_toks if _norm(t)]
        if not toks:
            continue
        res.records += 1
        joined = " | ".join(toks).upper()

        if "DUPLICATE" in joined:                    # duplicate page → ignore whole
            res.excluded.append((ann.page, " | ".join(toks)))
            continue
        if "DID NOT BUILD" in joined:
            res.excluded.append((ann.page, " | ".join(toks)))
            continue

        _parse_comment(toks, ann.page, res)
    return res


def _parse_comment(toks: list[str], page: int, res: AnnotParse) -> None:
    """One comment: walk its tokens, tracking the current cable segment so span
    footage attaches to the right cable count/kind."""
    # an explicit anchor anywhere in the comment means a DG must not add its own
    has_gaa = any(("AFO.GAA" in _canon(t)) or ("ANCHOR" in _canon(t)) for t in toks)
    seg_kind: str | None = None        # 'A' | 'B'
    seg_count: int | None = None
    seg_ie = False                     # buried into existing duct
    # a BM60 spec often sits on its own line with the footage on the next
    # ("BM60(2-1.25\")" then "Bore=636'") — carry it forward within the comment
    pending_conduit: tuple[int | None, str | None] | None = None
    # aerial span footage is held back until the whole comment is read, because an
    # OLASH token replaces it (coordinator's rule)
    pending_afo: list[tuple[int | None, float]] = []
    olash_ft: float | None = None
    olash_seen = False

    for tok in toks:
        t = _canon(tok)
        if not t:
            continue

        mc = _CABLE.match(t)
        if mc:
            seg_kind = mc.group(1).upper()
            seg_count = int(mc.group(2))
            seg_ie = bool(re.search(r"\.\s*IE\b|\bIE\b", t))
            ft = _FT.search(t) or _TRAIL.search(t)
            if ft:                                   # cable label carrying footage
                val = float(ft.group(1).replace(",", ""))
                if seg_kind == "A":
                    pending_afo.append((seg_count, val))
                else:
                    res.add(_bfo_type(seg_count, seg_ie), val)
            continue

        (handled, olash_ft, olash_seen, pending_afo,
         pending_conduit) = _classify(
            t, tok, page, res, seg_kind, seg_count, seg_ie,
            has_gaa, pending_afo, olash_ft, olash_seen, pending_conduit)
        if not handled:
            res.unresolved.append((page, tok))

    # settle the aerial footage: OLASH replaces placement on the same comment
    if olash_seen:
        ft = olash_ft if olash_ft else sum(v for _, v in pending_afo)
        if ft:
            res.add(ITEM_TYPES["olash"], ft)
    else:
        for count, val in pending_afo:
            res.add(_afo_sl_type(count), val)


def _classify(t, raw, page, res, seg_kind, seg_count, seg_ie,
              has_gaa, pending_afo, olash_ft, olash_seen, pending_conduit=None):
    """Classify one token. Returns
    (handled, olash_ft, olash_seen, pending_afo, pending_conduit)."""
    ret = lambda ok: (ok, olash_ft, olash_seen, pending_afo,  # noqa: E731
                      pending_conduit)
    n, body = _qty_prefix(t)
    b = _canon(body)
    ft_m = _FT.search(t) or _TRAIL.search(t)
    ft = float(ft_m.group(1).replace(",", "")) if ft_m else None

    # --- notes / non-billable ------------------------------------------------
    if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}(\s*/\s*\d{2,4})?", b):   # a date
        res.notes.append((page, raw))
        return ret(True)
    if any(h in t for h in _NOTE_HINTS) and not _BM60.search(t):
        res.notes.append((page, raw))
        return ret(True)
    if b.startswith("PM2A"):                       # not a contract unit
        return ret(True)
    if re.match(r"^PIPE\b", b):                    # reference — conduit bills BM60
        return ret(True)
    if re.fullmatch(r"[_\-—–.\s]+", b):            # separator / doodle
        return ret(True)

    # --- overlash / relash ---------------------------------------------------
    if "AFO.OLASH" in t or re.match(r"^OLASH\b", b):
        return (True, (ft if ft else olash_ft), True, pending_afo, pending_conduit)
    if "AFO.RELASH" in t:
        res.add(ITEM_TYPES["relash"], ft or 0)
        return ret(True)

    # --- aerial span footage / coils / risers --------------------------------
    if re.match(r"^AFO\b", b) or re.match(r"^AFO\.SL\b", b):
        if "COIL" in b:                             # 1 AFO.S per coil, ft ignored
            res.add(ITEM_TYPES["coil"], 1)
            return ret(True)
        if "UP POLE" in b or "DOWN POLE" in b:      # riser footage — part of span
            return ret(True)
        if "AFO.S" == b or b.startswith("AFO.S "):
            res.add(ITEM_TYPES["coil"], n)
            return ret(True)
        if "RTD" in b:
            res.add(ITEM_TYPES["afo_rtd"], ft or 0)
            return ret(True)
        for key, item in (("AFO.BOND", "bond"), ("AFO.BANDING", "banding"),
                          ("AFO.GAA", "anchor"), ("AFO.GG", "down_guy"),
                          ("AFO.TRAN2", "transfer"), ("AFO.EYE", "eye")):
            if key in b:
                res.add(ITEM_TYPES[item], n)
                return ret(True)
        val = ft if ft is not None else _trailing_qty(b)
        if val is not None:
            return (True, olash_ft, olash_seen,
                    pending_afo + [(seg_count, val)], pending_conduit)
        return ret(True)                            # bare "AFO 48" label

    # --- buried fiber --------------------------------------------------------
    if re.match(r"^BFO\b", b):
        if "RTD" in b:
            res.add(ITEM_TYPES["bfo_rtd"], ft or 0)
            return ret(True)
        val = ft if ft is not None else _trailing_qty(b)
        if val is not None:
            res.add(_bfo_type(seg_count, seg_ie), val)
        return ret(True)

    # --- conduit / bore ------------------------------------------------------
    if _BM60.search(t):
        method = _conduit_method(t)
        ways, size = _conduit_spec(t)
        val = ft if ft is not None else _trailing_qty(t)
        if val:
            res.add(_conduit_type(ways, size, method), val)
            return (True, olash_ft, olash_seen, pending_afo, None)
        # spec only — footage is on a following token ("Bore=636'")
        return (True, olash_ft, olash_seen, pending_afo, (ways, size))
    # a Plow/Bore/Trench footage line completing the conduit spec above it
    if re.match(r"^(PLOW|BORE|TRENCH|DP|DPD|MB)\b", b) and (ft or _trailing_qty(b)):
        val = ft or _trailing_qty(b)
        ways, size = pending_conduit or (None, None)
        res.add(_conduit_type(ways, size, _conduit_method(t)), val)
        return (True, olash_ft, olash_seen, pending_afo, None)

    # --- MST / RTD -----------------------------------------------------------
    if b.startswith("HST"):
        m = _HST.search(t)
        if m:
            res.add(_mst_type(int(m.group(2))), 1)
            return ret(True)
        return ret(False)
    if "RTD" in b:
        item = "bfo_rtd" if seg_kind == "B" or b.startswith("BFO") else "afo_rtd"
        if ft:
            res.add(ITEM_TYPES[item], ft)
            return ret(True)
        # "RTD 4-750" / "RTD 4150" — port count + tail bracket, billed per unit
        m = re.search(r"RTD\D*(\d)\s*-?\s*(\d{3,4})\b", b)
        if m:
            res.add(_mst_type(int(m.group(2))), 1)
            return ret(True)
        return ret(False)                           # H:/T: only → review

    # --- guys / anchors ------------------------------------------------------
    if b in ("DG", "DG / GG", "DG/GG") or re.match(r"^DG\b", b):
        res.add(ITEM_TYPES["down_guy"], n)
        if not has_gaa:                             # DG implies its anchor
            res.add(ITEM_TYPES["anchor"], n)
        return ret(True)
    if "ANCHOR" in b:
        res.add(ITEM_TYPES["anchor"], n)
        return ret(True)

    # --- structures ----------------------------------------------------------
    m = _BHF.match(b)
    if m:
        size, tier = m.group(1), (m.group(2) or "")
        res.add(ItemType(f"handhole_{size}{tier}", f"Handhole/vault BHF-{size}{tier}",
                         UoM.EA, f"BHF-{size}{tier}"), n)
        return ret(True)
    m = _BDO.match(b)
    if m:
        sz = m.group(1).upper()
        res.add(ItemType(f"pedestal_{sz}", f"Pedestal BDO({sz})", UoM.EA,
                         f"BDO({sz})"), n)
        return ret(True)
    m = _DIMS.search(t)                              # 17x30x24 → BHF bracket
    if m and not re.search(r"BM60", t):
        code = _BHF_DIMS.get((int(m.group(1)), int(m.group(2))))
        if code:
            res.add(ItemType(f"handhole_{code}", f"Handhole/vault {code}",
                             UoM.EA, code), 1)
            return ret(True)
        return ret(False)                            # unknown dims → review

    for pat, item in ((r"^BM81\b", "riser_guard"), (r"^BM53\b", "marker_post"),
                      (r"^BM55A\b", "locate_disk"), (r"^BM55\b", "locate_post"),
                      (r"^BM2\b", "ground_rod"), (r"^BMHD\b", "handdig")):
        if re.match(pat, b):
            res.add(ITEM_TYPES[item], n)
            return ret(True)
    if re.match(r"^BM90\b", b):
        res.add(ITEM_TYPES["tracer_wire"], ft or _trailing_qty(b) or 0)
        return ret(True)
    if "SPLICE" in b:                                # aerial/buried closure varies
        return ret(False)

    if re.match(r"^TRANS?[12]\b", b):                # bare TRAN2 / TRANS2
        res.add(ITEM_TYPES["transfer" if b.rstrip('S').endswith("2")
                           else "transfer"], n)
        return ret(True)

    # --- stations / geometry (positional reference, not billable) ------------
    head = re.split(r"[\s\-:=]", b)[0]
    if head in _STATION_WORDS:
        return ret(True)
    # a bare number continues the current span: with a foot mark it is always
    # footage; without one, only a small value is (larger is a station id).
    bare = re.fullmatch(r"([\d,]+)\s*('?)", b)
    if bare and seg_kind:
        val = float(bare.group(1).replace(",", ""))
        is_ft = bool(bare.group(2)) or val < _FT_CEIL
        if is_ft:
            if seg_kind == "A":
                return (True, olash_ft, olash_seen,
                        pending_afo + [(seg_count, val)], pending_conduit)
            res.add(_bfo_type(seg_count, seg_ie), val)
            return ret(True)
        return ret(True)                             # station id
    if _BARE.match(b):
        return ret(True)                             # station id / footage marker
    if not b:
        return ret(True)
    return ret(False)


def _trailing_qty(text: str) -> float | None:
    """A trailing '- 344' / '= 636' quantity (feet) on a token."""
    m = _TRAIL.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    return val if val < 100000 else None


def _conduit_method(t: str) -> str:
    """Install method for a BM60 conduit token. A bare 'Bore' is always DP."""
    up = t.upper()
    if "DPD" in up or "DUAL" in up:
        return "DPD"
    if "MB" in up and "BORE" in up:
        return "MB"
    if "PLOW" in up:
        return "P"
    if re.search(r"\bTD\b", up):
        return "TD"
    if "TRENCH" in up or re.search(r"\bT\b\s*-?\s*\d", up):
        return "T"
    if "DP" in up or "BORE" in up:
        return "DP"
    return "DP"


def _conduit_spec(t: str) -> tuple[int | None, str | None]:
    """(way-count, size) from BM60(2-1.25") / BM60(3)(1.25") / BM60-(1.25).

    Parsed from the text *after* ``BM60`` so the 60 in the code is never read as a
    way count."""
    tail = re.sub(r"^.*?BM\s*60", "", t, flags=re.I) or t
    m = _WAYS.search(tail)                     # (2-1.25") or (3)(1.25")
    if m:
        return int(m.group(1)), m.group(2)
    m = _SIZE_ONLY.search(tail)                # (1.25")
    if m:
        return 1, m.group(1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*\"", tail)
    if m:
        return 1, m.group(1)
    m = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*\)", tail)   # (1.25) without the inch mark
    return (1, m.group(1)) if m else (None, None)


# --------------------------------------------------------------------------- #
#  → AsBuiltLines, matched against the loaded contract
# --------------------------------------------------------------------------- #
def to_asbuilt_lines(res: AnnotParse, contract=None, aliases=None
                     ) -> tuple[list[AsBuiltLine], dict[str, str]]:
    """The parsed quantities as ``AsBuiltLine``s, **matched against the loaded
    contract** (the bid schedule loads before the as-built, so its items are on
    hand). Each item's rate-code hint or description is resolved to a contract code
    the same way the crosswalk does — matching on both the code and the description
    columns. Returns ``(lines, resolved)`` where ``resolved`` maps raw_desc → code
    for the confident matches (so they skip crosswalk review); anything that doesn't
    match confidently carries ``code=None`` and is sent to the crosswalk to verify.
    """
    lines: list[AsBuiltLine] = []
    resolved: dict[str, str] = {}
    for key, qty in res.qty.items():
        if not qty:
            continue
        label = res.label.get(key, key)
        code = _resolve_against_contract(res.code.get(key), label, contract, aliases)
        if code:
            resolved[label] = code
        n = res.spans.get(key, 0)
        lines.append(AsBuiltLine(
            raw_desc=label, qty=float(round(qty, 1)),
            uom=UoM.from_str(res.uom.get(key)) or UoM.EA, code=code,
            source_ref=f"annot:{n} record{'s' if n != 1 else ''}",
            confidence="annot"))
    return lines, resolved


def _resolve_against_contract(hint, label, contract, aliases):
    """A parsed item's contract code, via the crosswalk engine — its rate-code hint
    (an exact code match) first, then its description (fuzzy). None when neither is
    confident, so the item goes to the crosswalk step."""
    if not contract:
        return None
    from recon.crosswalk import AliasStore, resolve
    aliases = aliases if aliases is not None else AliasStore()
    for text in (hint, label):
        if text:
            m = resolve(text, contract, aliases)
            if m.code:
                return m.code
    return None
