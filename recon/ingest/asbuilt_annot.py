"""As-built from PDF **comment annotations**.

Contractors mark up the construction PDF with Adobe comments (FreeText
annotations) — one per span/structure, e.g.::

    AFO 288F | 6288 | 410' | AFO BOND | AFO SL

Those comments are stored as machine-readable text in the PDF (not pixels), so we
extract them directly — no OCR — and parse the field shorthand into billable
quantities keyed by the rate sheet's codes.

Domain notes (from the Lumbee/MCT rate sheet + the coordinator):
  * ``AFO SL`` = *strand and lash* aerial fiber placement (per-foot), by fiber
    count → ``AFO SL <count>FOC``. The span footage in an aerial comment is its
    strand-and-lash length.
  * ``AFO S`` = install slack *coil* / snow shoe → ``AFO.S`` (each, not footage).
  * ``ANCHOR`` → ``AFO.GAA``; ``DG / GG`` → ``AFO.GG``; ``AFO BOND`` → ``AFO.BOND``.

Ambiguous shorthand (conduit ``BM60(...)``, ``HST`` MST tails, ``BDO`` pedestal
size, splice can aerial/buried) is deliberately left *unmapped* — surfaced for the
coordinator to resolve in the review grid rather than guessed at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from recon.models import AsBuiltLine, UoM

# --------------------------------------------------------------------------- #
#  rate-code registry (item key -> label, unit, rate code)
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
# Buried fiber in conduit, per fiber count.
_BFO_CODE = {12: "BFO.12.I", 24: "BFO.24.I", 48: "BFO.48.I", 72: "BFO.72.I",
             96: "BFO.96.I", 144: "BFO.144.I", 288: "BFO.288.I"}

# MST tail-length brackets from the rate sheet (Each, priced by tail length).
_MST_BRACKETS = (150, 250, 350, 500, 750, 1000, 1250, 1500)

ITEM_TYPES: dict[str, ItemType] = {
    # Strand & lash with NO fiber count on the comment = strand-only placement.
    "afo_sl": ItemType("afo_sl", "Aerial strand-only (no fiber)",
                       UoM.FT, "AFO SL- STRAND"),
    "coil": ItemType("coil", "Slack coil / snow shoe", UoM.EA, "AFO.S"),
    "anchor": ItemType("anchor", "Screw anchor with down guy", UoM.EA, "AFO.GAA"),
    "down_guy": ItemType("down_guy", "Down guy with guy guard", UoM.EA, "AFO.GG"),
    "bond": ItemType("bond", "Aerial bond", UoM.EA, "AFO.BOND"),
    "marker_post": ItemType("marker_post", "Fiber marking post", UoM.EA, "BM53"),
    "locate_disk": ItemType("locate_disk", "3M locate disk/ball", UoM.EA, "BM55A"),
    "ground_rod": ItemType("ground_rod", "Ground rod", UoM.EA, "BM2"),
    "tracer_wire": ItemType("tracer_wire", "Pull rope / tracer wire", UoM.FT, "BM90"),
    "buried_fiber": ItemType("buried_fiber", "Buried fiber in conduit (count TBD)",
                             UoM.FT, None),
}


def _mst_type(tail: int) -> ItemType:
    """MST keyed by its tail-length bracket (snapped to the nearest rate line)."""
    b = min(_MST_BRACKETS, key=lambda x: abs(x - tail))
    return ItemType(f"mst_{b}", f"{b}' MST (aerial/buried)", UoM.EA, f"MST {b}'")


def _afo_sl_type(count: int | None) -> ItemType:
    if count in _AFO_SL_CODE:
        return ItemType(f"afo_sl_{count}", f"Strand & lash {count}ct aerial fiber",
                        UoM.FT, _AFO_SL_CODE[count])
    return ITEM_TYPES["afo_sl"]


def _bfo_type(count: int | None) -> ItemType:
    if count in _BFO_CODE:
        return ItemType(f"buried_{count}", f"Buried {count}ct fiber in conduit",
                        UoM.FT, _BFO_CODE[count])
    return ITEM_TYPES["buried_fiber"]


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
#  parsing
# --------------------------------------------------------------------------- #
_CABLE = re.compile(r"^([AB])FO\s*0*(\d+)\s*F?\b", re.I)      # AFO 288F, BFO144F
_FT = re.compile(r"(\d+)\s*'")                                # 410'
_TRAIL = re.compile(r"-\s*(\d+)\s*'?\s*$")                    # "- 229" / "- 2"
_BARE = re.compile(r"^(\d+)\s*(MID|TOP|TAIL|UG|BOTTOM)?$", re.I)
_BHF = re.compile(r"^BHF-?(\d+)(T)?", re.I)                   # BHF-30T, BHF30T
_HST = re.compile(r"HST\s*(\d+)\s*-\s*(\d+)", re.I)          # MST: ports-tail
_FT_CEIL = 1000          # bare number below this = feet; at/above = station id
_MULT_CEIL = 6          # trailing "- N" is a count only when small


@dataclass
class AnnotParse:
    qty: dict[str, float] = field(default_factory=dict)        # item key -> quantity
    label: dict[str, str] = field(default_factory=dict)        # item key -> label
    uom: dict[str, str] = field(default_factory=dict)          # item key -> UoM.value
    code: dict[str, str | None] = field(default_factory=dict)  # item key -> rate code
    spans: dict[str, int] = field(default_factory=dict)        # item key -> #records
    records: int = 0
    excluded: list[tuple[int, str]] = field(default_factory=list)
    notes: list[tuple[int, str]] = field(default_factory=list)
    unresolved: list[tuple[int, str]] = field(default_factory=list)

    def _add(self, it: ItemType, amount: float) -> None:
        self.qty[it.key] = self.qty.get(it.key, 0.0) + amount
        self.spans[it.key] = self.spans.get(it.key, 0) + 1
        self.label[it.key] = it.label
        self.uom[it.key] = it.uom.value
        self.code[it.key] = it.code


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_annotations(annotations: list[Annotation]) -> AnnotParse:
    """Classify each comment's tokens into billable quantities. Unrecognised or
    ambiguous tokens are collected in ``unresolved`` for the review grid; spans
    marked DID NOT BUILD are excluded."""
    res = AnnotParse()
    for ann in annotations:
        toks = [_norm(t) for t in ann.text.split("\r") if _norm(t)]
        if not toks:
            continue
        res.records += 1
        page = ann.page
        if "DID NOT BUILD" in " ".join(toks).upper():
            res.excluded.append((page, " | ".join(toks)))
            continue

        prim = None                                        # primary cable (kind, count)
        for t in toks:
            m = _CABLE.match(t)
            if m:
                prim = (m.group(1).upper(), int(m.group(2)))
                break

        for t in toks:
            _classify(t, prim, page, res)
    return res


def _classify(t: str, prim, page: int, res: AnnotParse) -> None:
    up = t.upper()
    ftm = _FT.search(t)
    trail = _TRAIL.search(t)
    mult = int(trail.group(1)) if (trail and int(trail.group(1)) <= _MULT_CEIL) else 1
    ft = int(ftm.group(1)) if ftm else None

    # cable label, possibly carrying its own footage (e.g. "BFO 144F 150'")
    mc = _CABLE.match(t)
    if mc:
        kind, cnt = mc.group(1).upper(), int(mc.group(2))
        if ft is not None:
            res._add(_afo_sl_type(cnt) if kind == "A" else _bfo_type(cnt), ft)
        return

    if up.startswith("AFO SL"):                            # strand & lash label/footage
        if ft is not None:
            res._add(_afo_sl_type(prim[1] if prim and prim[0] == "A" else None), ft)
        elif trail:
            res._add(_afo_sl_type(prim[1] if prim and prim[0] == "A" else None),
                     int(trail.group(1)))
        return
    if up == "AFO S":                                      # slack coil (each)
        res._add(ITEM_TYPES["coil"], 1)
        return
    if "BOND" in up:
        res._add(ITEM_TYPES["bond"], mult)
        return
    if "ANCHOR" in up:
        res._add(ITEM_TYPES["anchor"], mult)
        return
    if "DG" in up and "GG" in up:
        res._add(ITEM_TYPES["down_guy"], mult)
        return
    if up.startswith("HST"):                               # MST: "HST <ports>-<tail>"
        m = _HST.search(t)
        res._add(_mst_type(int(m.group(2))), 1) if m else res.unresolved.append((page, t))
        return
    if "MST" in up:                                        # "4 PORT MST MOVED TO ..."
        res.notes.append((page, t)) if "MOVED" in up else res.unresolved.append((page, t))
        return
    if "SPLICE" in up:                                     # closure type varies → review
        res.unresolved.append((page, t))
        return
    if _BHF.match(t):
        m = _BHF.match(t)
        size, tier = m.group(1), (m.group(2) or "")
        res._add(ItemType(f"handhole_{size}{tier}", f"Handhole/vault BHF-{size}{tier}",
                          UoM.EA, f"BHF-{size}{tier}"), mult)
        return
    if up.startswith("BM53"):
        res._add(ITEM_TYPES["marker_post"], mult)
        return
    if up.startswith("BM55"):
        res._add(ITEM_TYPES["locate_disk"], mult)
        return
    if re.match(r"^BM2\b", up):
        res._add(ITEM_TYPES["ground_rod"], mult)
        return
    if up.startswith("BM90"):                              # tracer wire, per foot
        res._add(ITEM_TYPES["tracer_wire"],
                 ft if ft is not None else (int(trail.group(1)) if trail else 0))
        return
    if up.startswith("BM60") or up.startswith("BM"):       # conduit/bore — ambiguous
        res.unresolved.append((page, t))
        return
    if up.startswith("BDO") or up.startswith("BHF"):       # pedestal/handhole variants
        res.unresolved.append((page, t))
        return
    if ft is not None and prim:                            # standalone span footage
        res._add(_afo_sl_type(prim[1]) if prim[0] == "A" else _bfo_type(prim[1]), ft)
        return
    bm = _BARE.match(t)
    if bm:
        n = int(bm.group(1))
        if n < _FT_CEIL and prim and not bm.group(2):      # small bare number = feet
            res._add(_afo_sl_type(prim[1]) if prim[0] == "A" else _bfo_type(prim[1]), n)
        return                                             # else station id / MID/TOP
    if any(k in up for k in ("MOVED TO", "PREVIOUS PAGE", "PAYING", "PER ",
                             "RIVER CITY")):
        res.notes.append((page, t))
        return
    res.unresolved.append((page, t))


def to_asbuilt_lines(res: AnnotParse,
                     code_map: dict[str, str] | None = None) -> list[AsBuiltLine]:
    """The parsed quantities as ``AsBuiltLine``s for the reconciliation engine.
    ``code_map`` overrides item-key → rate code (the editable per-project crosswalk;
    resolves the ambiguous items). Items still lacking a code carry ``code=None`` so
    they land in the review grid."""
    code_map = code_map or {}
    lines: list[AsBuiltLine] = []
    for key, qty in res.qty.items():
        if not qty:
            continue
        code = code_map.get(key, res.code.get(key))
        n = res.spans.get(key, 0)
        lines.append(AsBuiltLine(
            raw_desc=res.label.get(key, key), qty=float(round(qty, 1)),
            uom=UoM.from_str(res.uom.get(key)) or UoM.EA, code=code,
            source_ref=f"annot:{n} record{'s' if n != 1 else ''}",
            confidence="annot"))
    return lines
