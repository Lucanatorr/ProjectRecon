"""Drawing takeoff: materials and fiber footage from a construction PDF's comments.

Crews and inspectors mark up the construction PDF with Adobe comment boxes
(FreeText annotations). Two kinds carry quantities:

*MATERIALS boxes* — a header line, then one material per line::

    MATERIALS
    2-DEADEND
    3-12" BOLTS
    3'-COPPER
    2 ROLLS LASHING WIRE

*Footage boxes* — a label (fiber count or cable ID) over a sequential
calculation, sometimes with a box total or strand footage::

    TOTALS
    96DF
    8650-7800=850'
    48AF OVERLASH
    3120-2270=850'
    STRAND=850'

The comments are text inside the PDF, so they are read exactly — no OCR. Every
line keeps its file, page and the text as written, so any total traces back to
the comment it came from.

Rules worth knowing:
  * Spelling variants of one material are combined (CURVE / CURVES / CURVED CLAMP
    → CURVE CLAMP; DG → DOWN GUY; GG → GUY GUARD). Kinds that may differ are kept
    apart (NUT / SQ NUT / BUG NUT; WASHER / SQ WASHER / FLAT WASHER). A name that
    is not recognised is kept exactly as written and flagged — never dropped.
  * Footage uses the figure written in the box. A subtraction that disagrees is
    flagged, not silently corrected.
  * A line that only repeats a box total (``MST TOTAL=640'``) is not added — it is
    checked against the box's own calculations instead.
  * Fiber footage is grouped by fiber count (48 / 96 / 288 …, whether written CT,
    AF or DF). Boxes that name only a cable ID are totalled by cable.
  * Two files whose comments are identical (a resubmitted drawing) are counted
    once.
"""
from __future__ import annotations

import io
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from recon.ingest.asbuilt_annot import Annotation, extract_annotations
from takeoff.sources import PdfSource

# --------------------------------------------------------------------------- #
#  reading
# --------------------------------------------------------------------------- #
@dataclass
class DrawingFile:
    name: str
    pages: int
    comments: list[Annotation]
    group: str | None = None
    duplicate_of: str | None = None     # set when its comments repeat another file

    @property
    def counted(self) -> bool:
        return self.duplicate_of is None

    def signature(self) -> Counter:
        return Counter((c.page, _clean(c.text)) for c in self.comments)


def _clean(text: str) -> str:
    return "\n".join(l.strip() for l in re.split(r"\r\n|\r|\n", text or "") if l.strip())


def read_drawing(source: PdfSource) -> DrawingFile:
    """Every comment box in one PDF (shared extractor with Recon's as-built)."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(source.data)) as pdf:
        pages = len(pdf.pages)
    comments = extract_annotations(io.BytesIO(source.data))
    return DrawingFile(name=source.name, pages=pages, comments=comments, group=source.group)


def mark_duplicates(files: list[DrawingFile]) -> None:
    """A file whose comments (page and text) exactly repeat an earlier file's is a
    resubmission: it is kept for reference but not counted."""
    seen: dict[tuple, str] = {}
    for f in sorted(files, key=lambda f: f.name):
        f.duplicate_of = None
        if not f.comments:
            continue
        key = tuple(sorted(f.signature().items()))
        if key in seen:
            f.duplicate_of = seen[key]
        else:
            seen[key] = f.name


# --------------------------------------------------------------------------- #
#  materials
# --------------------------------------------------------------------------- #
_MATERIALS = [  # (pattern on the item text, normalized name, unit)
    (r"^DOUBLE DEAD ?ENDS?$", "DOUBLE DEADEND", "EA"),
    (r"^DEAD ?ENDS?$", "DEADEND", "EA"),
    (r"^(CURVES?|CURE|CURVED|CURVED? CLAMPS?)$", "CURVE CLAMP", "EA"),
    (r"^(STRAIGHTS?|STRAIGHT CLAMPS?)$", "STRAIGHT CLAMP", "EA"),
    (r"^(DG|DOWN ?GUYS?)$", "DOWN GUY", "EA"),
    (r"^(GG|GUY GUARDS?)$", "GUY GUARD", "EA"),
    (r"^ANCHORS?$", "ANCHOR", "EA"),
    (r"^BONDS?$", "BOND", "EA"),
    (r"^AUX EYES?$", "AUX EYE", "EA"),
    (r"^MICKEY MOUSE$", "MICKEY MOUSE", "EA"),
    (r"^SCREW ?EYES?$", "SCREW EYE", "EA"),
    (r"^BUG ?NUTS?$", "BUG NUT", "EA"),
    (r"^SQ[ -]?NUTS?$", "SQ NUT", "EA"),
    (r"^NUTS?$", "NUT", "EA"),
    (r"^SQ WASHERS?$", "SQ WASHER", "EA"),
    (r"^FLAT WASHERS?$", "FLAT WASHER", "EA"),
    (r"^WASHERS?$", "WASHER", "EA"),
    (r"^WEAVERS?$", "WEAVER", "EA"),
    (r"^SLIP ?EYES?$", "SLIPEYE", "EA"),
    (r"^SPLIT ?BOLTS?$", "SPLIT BOLT", "EA"),
    (r"^(PREFORMS?|PREFORSM)$", "PREFORM", "EA"),
    (r"^PIG ?EARS?$", "PIG EAR", "EA"),
    (r"^FIBER TAGS?$", "FIBER TAG", "EA"),
    (r"^STRAPS?$", "STRAP", "EA"),
    (r"^(FT )?COPPER$", "COPPER", "FT"),
    (r"^STRAND$", "STRAND", "FT"),
    (r"^(ROLLS? )?(OF )?LASHI?N+G?N? WIRE$", "LASHING WIRE", "ROLL"),
    (r"^ROLLS? (OF )?WIRE$", "LASHING WIRE", "ROLL"),
]
# Display order for the totals; bolts follow by length, then footage/roll items.
MATERIAL_ORDER = ["STRAIGHT CLAMP", "CURVE CLAMP", "DEADEND", "DOUBLE DEADEND", "DOWN GUY",
                  "GUY GUARD", "ANCHOR", "PREFORM", "PIG EAR", "SLIPEYE", "AUX EYE",
                  "SCREW EYE", "MICKEY MOUSE", "BOND", "WEAVER", "SPLIT BOLT", "NUT",
                  "SQ NUT", "BUG NUT", "WASHER", "SQ WASHER", "FLAT WASHER", "STRAP",
                  "FIBER TAG"]
_TAIL_ORDER = ["COPPER", "STRAND", "LASHING WIRE"]

_QTY_LINE = re.compile(r"^([\d,]*\.?\d+)\s*('|FT\b)?\s*[-–]?\s*(.+)$", re.I)


@dataclass
class MaterialLine:
    file: str
    page: int
    line: str              # as written
    qty: float
    unit: str              # EA | FT | ROLL
    item: str              # normalized name
    written: str           # the item text as written (upper-cased)
    source: str = "MATERIALS box"
    notes: list[str] = field(default_factory=list)   # needs a human look
    info: list[str] = field(default_factory=list)    # how it was read; no action needed


def normalize_material(text: str) -> tuple[str, str, list[str], list[str]]:
    """Item text as written → (normalized name, unit, notes to review, info)."""
    t = re.sub(r"\s+", " ", text.upper()).strip()
    m = re.match(r"^(\d+)\s*([\"']|IN\b)\s*(BOLTS?|BOTLS)$", t)
    if m:
        notes, info = [], []
        if m.group(2) == "'":
            notes.append(f'written {t} (a foot mark); read as a {m.group(1)}" bolt')
        if m.group(1) == "2":
            notes.append('written 2" BOLTS — possibly a typo for 12"; kept as written')
        if m.group(3) == "BOTLS":
            info.append("spelled BOTLS")
        return f'{m.group(1)}" BOLT', "EA", notes, info
    for pattern, name, unit in _MATERIALS:
        if re.match(pattern, t):
            notes, info = [], []
            if name == "LASHING WIRE":
                if "LASH" not in t:
                    info.append(f'written "{t}"; counted as lashing wire')
                if "ROLL" not in t:
                    notes.append("no unit written; counted as rolls")
            if t in ("CURE", "CURVED"):
                info.append(f'written "{t}"; read as a curve clamp')
            if t == "PREFORSM":
                info.append("spelled PREFORSM")
            return name, unit, notes, info
    return t, "EA", ["not a recognised material name; kept exactly as written"], []


def is_materials_box(text: str) -> bool:
    first = _clean(text).split("\n", 1)[0].upper()
    return first.startswith("MATERIAL")


def parse_materials_box(file: str, page: int, text: str) -> tuple[list[MaterialLine], list[str]]:
    """One MATERIALS box → its lines, plus any lines that could not be read."""
    lines = _clean(text).split("\n")
    head = re.sub(r"^MATERIALS?\s*:?\s*", "", lines[0], flags=re.I)
    body = ([head] if head else []) + lines[1:]
    out, bad = [], []
    for raw in body:
        m = _QTY_LINE.match(raw)
        if not m:
            bad.append(raw)
            continue
        qty = float(m.group(1).replace(",", ""))
        written = re.sub(r"^OF\s+", "", m.group(3).strip(), flags=re.I)
        item, unit, notes, info = normalize_material(written)
        if m.group(2) and unit == "EA":
            notes.append("counted item written with a foot mark")
        out.append(MaterialLine(file=file, page=page, line=raw, qty=qty, unit=unit,
                                item=item, written=re.sub(r"\s+", " ", written.upper()),
                                notes=notes, info=info))
    return out, bad


def _flag_outliers(lines: list[MaterialLine]) -> None:
    """A single line far above that material's usual per-box quantity is flagged
    (e.g. 12,100' of strand where every other box says ≤ 1,200')."""
    by_item: dict[tuple, list[MaterialLine]] = defaultdict(list)
    for ln in lines:
        by_item[(ln.item, ln.unit)].append(ln)
    for group in by_item.values():
        if len(group) < 5:
            continue
        for ln in group:
            others = [o.qty for o in group if o is not ln]
            typical = statistics.median(others)
            ceiling = max(others)
            if typical > 0 and ln.qty >= 5 * ceiling and ln.qty >= 10 * typical:
                ln.notes.append(f"unusually large — every other {ln.item.lower()} line is "
                                f"{ceiling:,g} or less; check the drawing. Counted as written")


# --------------------------------------------------------------------------- #
#  fiber footage
# --------------------------------------------------------------------------- #
_CALC = re.compile(r"^(\d+)\s*(M?)\s*[-=]\s*(\d+)\s*(M?)\s*=\s*(\d+)\s*(M?)\s*(?:=\s*(\d+))?\s*['\"]*$", re.I)
_CALC_NO_EQ = re.compile(r"^(\d+)\s*-\s*(\d+)\s*'$")
_LABEL_AND_CALC = re.compile(r"^(.*?[A-Z].*?)\s+(\d+\s*M?\s*[-=]\s*\d+\s*M?\s*=.*)$", re.I)
_STRAND = re.compile(r"^STRAND\s*=\s*([\d,]+)\s*'?$", re.I)
_COUNT = re.compile(r"\b(\d+)\s*(CT|AF|DF)\b", re.I)
_CABLE = re.compile(r"\b(?:MST\s*)?([A-Z]{3})([0-9O]{2,4})\b")


@dataclass
class FootageLine:
    file: str
    page: int
    label: str                 # as written above the calculation
    fiber_count: int | None    # 48 / 96 / 288…; None when only a cable ID is given
    cable: str                 # cable ID when no fiber count is written
    start: int
    end: int | None
    seq_unit: str              # ft | m
    feet: int                  # footage written in the box
    line: str                  # the calculation as written
    notes: list[str] = field(default_factory=list)   # needs a human look
    info: list[str] = field(default_factory=list)    # how it was read; no action needed
    unlabeled: bool = False


@dataclass
class BoxTotalCheck:
    file: str
    page: int
    text: str
    stated: int
    computed: int

    @property
    def matches(self) -> bool:
        return self.stated == self.computed


def is_footage_box(text: str) -> bool:
    return "=" in text and not is_materials_box(text) and bool(
        re.search(r"\d\s*M?\s*[-=]\s*\d", text, re.I))


def classify_label(label: str) -> tuple[int | None, str]:
    up = label.upper()
    m = _COUNT.search(up)
    if m:
        return int(m.group(1)), ""
    c = _CABLE.search(up)
    if c and re.search(r"\d", c.group(2)):
        return None, c.group(1) + c.group(2).replace("O", "0")
    return None, up.strip()


def parse_footage_box(file: str, page: int, text: str):
    """One footage box → (footage lines, box-total checks, strand material lines)."""
    lines = [l for l in _clean(text).split("\n")]
    footage, checks, strand = [], [], []
    label_parts: list[str] = []
    last_label = ""
    after_calc = True
    for raw in lines:
        L = raw.strip()
        U = L.upper()
        if U in ("TOTALS", "TOTAL"):
            continue
        ms = _STRAND.match(U)
        if ms:
            strand.append(MaterialLine(file=file, page=page, line=L,
                                       qty=float(ms.group(1).replace(",", "")), unit="FT",
                                       item="STRAND", written="STRAND", source="footage box",
                                       info=["strand written in a footage box, not a MATERIALS box"]))
            continue
        both = _LABEL_AND_CALC.match(L)
        if both and not _CALC.match(L):
            label_parts, after_calc = [both.group(1)], False
            L = both.group(2).strip()
        if "=" in L and not re.search(r"\d\s*M?\s*[-=]\s*\d", L, re.I):
            # "MST TOTAL=640'" / "TOTALS MST OVERLASH ADDER=900'": a box total
            v = re.search(r"=\s*(\d[\d,]*)", L)
            if v:
                checks.append(BoxTotalCheck(file, page, L, int(v.group(1).replace(",", "")), 0))
            continue
        m, m2 = _CALC.match(L), None
        if not m:
            m2 = _CALC_NO_EQ.match(L)
        if m or m2:
            unlabeled = not label_parts
            label = " ".join(label_parts) if label_parts else last_label
            notes, info = [], []
            if m:
                a, b, stated = int(m.group(1)), int(m.group(3)), int(m.group(5))
                metric = bool(m.group(2) or m.group(4) or m.group(6))
                feet = int(m.group(7)) if m.group(7) else stated
                if re.search(r"\d\s*=\s*\d+\s*=", L) and not m.group(7):
                    info.append('"=" typed for "-" in the subtraction')
                if abs(a - b) != stated:
                    notes.append(f"subtraction: |{a}-{b}| = {abs(a - b):,}, the box says "
                                 f"{stated:,}; the box figure is used")
                if metric:
                    conv = stated * 3.28084
                    (info if abs(conv - feet) <= 1 else notes).append(
                        f"sequentials in meters: {stated} m = {conv:,.1f} ft; the box says {feet:,} ft")
                if a < b:
                    info.append("written low-high")
                if L.endswith("''") or L.endswith('"'):
                    info.append("stray quote marks")
                if not re.search(r"['\"]$", L) and not metric:
                    info.append("no foot mark; read as feet")
            else:
                a, feet = int(m2.group(1)), int(m2.group(2))
                b, metric = None, False
                notes.append(f'written "{L}" — no "=" and no end number; {feet:,} read as the footage')
            count, cable = classify_label(label)
            footage.append(FootageLine(file=file, page=page, label=label, fiber_count=count,
                                       cable=cable, start=a, end=b,
                                       seq_unit="m" if metric else "ft", feet=feet, line=L,
                                       notes=notes, info=info, unlabeled=unlabeled))
            last_label = label
            label_parts, after_calc = [], True
        else:
            if after_calc:
                label_parts = []
            label_parts.append(re.sub(r"^TOTALS?\s+", "", L, flags=re.I))
            after_calc = False
    for c in checks:
        c.computed = sum(f.feet for f in footage)
    return footage, checks, strand


def _resolve_unlabeled(footage: list[FootageLine]) -> None:
    """A calculation with no label of its own takes the label of the calculation
    its sequentials continue (same file: its start is their end, or its end is
    their start), following chains. Otherwise it keeps the label above it."""
    pending = [f for f in footage if f.unlabeled]
    above = {id(f): f.label for f in pending}
    changed = True
    while changed:
        changed = False
        for f in list(pending):
            link = [o for o in footage if o is not f and o.file == f.file and not o.unlabeled
                    and f.end is not None and (o.end == f.start or o.start == f.end)]
            labels = {o.label for o in link}
            if len(labels) == 1:
                o = link[0]
                f.label = o.label
                f.fiber_count, f.cable = classify_label(o.label)
                f.unlabeled = False
                pending.remove(f)
                changed = True
                note = (f"no label of its own; its sequentials continue {o.label} "
                        f"(p{o.page}), so it is counted as {o.label}")
                if o.label != above[id(f)]:
                    note += f" — the line above it says {above[id(f)]}"
                f.notes.append(note)
    for f in pending:
        f.notes.append(f"no label of its own and no connecting sequentials; given the "
                       f"label of the line above ({above[id(f)]})")
        f.unlabeled = False


# --------------------------------------------------------------------------- #
#  the takeoff
# --------------------------------------------------------------------------- #
@dataclass
class DrawingTakeoff:
    name: str
    files: list[DrawingFile]
    materials: list[MaterialLine] = field(default_factory=list)
    footage: list[FootageLine] = field(default_factory=list)
    checks: list[BoxTotalCheck] = field(default_factory=list)
    unreadable: list[tuple[str, int, str]] = field(default_factory=list)  # file, page, line

    @property
    def counted_files(self) -> list[DrawingFile]:
        return [f for f in self.files if f.counted]

    def material_totals(self) -> list[dict]:
        """[{item, unit, qty, lines, pages, written}] in display order."""
        agg: dict[tuple, dict] = {}
        for ln in self.materials:
            a = agg.setdefault((ln.item, ln.unit), {"item": ln.item, "unit": ln.unit, "qty": 0.0,
                                                      "lines": 0, "pages": set(), "written": set()})
            a["qty"] += ln.qty
            a["lines"] += 1
            a["pages"].add((ln.file, ln.page))
            a["written"].add(ln.written)

        def order(key):
            item, unit = key
            if item in MATERIAL_ORDER:
                return (0, MATERIAL_ORDER.index(item), 0)
            m = re.match(r'^(\d+)" BOLT$', item)
            if m:
                return (1, int(m.group(1)), 0)
            if item in _TAIL_ORDER:
                return (3, _TAIL_ORDER.index(item), 0)
            return (2, 0, item)
        out = []
        for key in sorted(agg, key=order):
            a = agg[key]
            out.append({**a, "pages": len(a["pages"]), "written": sorted(a["written"]),
                        "known": not any("not a recognised" in n for ln in self.materials
                                         if (ln.item, ln.unit) == key for n in ln.notes)})
        return out

    def fiber_totals(self) -> list[dict]:
        """[{fiber_count, feet, calcs}] — every footage line that names a count."""
        agg: dict[int, dict] = {}
        for f in self.footage:
            if f.fiber_count is None:
                continue
            a = agg.setdefault(f.fiber_count, {"fiber_count": f.fiber_count, "feet": 0, "calcs": 0})
            a["feet"] += f.feet
            a["calcs"] += 1
        return [agg[k] for k in sorted(agg)]

    def fiber_by_label(self) -> list[dict]:
        agg: dict[tuple, dict] = {}
        for f in self.footage:
            if f.fiber_count is None:
                continue
            a = agg.setdefault((f.fiber_count, f.label), {"fiber_count": f.fiber_count,
                                                           "label": f.label, "feet": 0, "calcs": 0})
            a["feet"] += f.feet
            a["calcs"] += 1
        return [agg[k] for k in sorted(agg, key=lambda k: (k[0], k[1]))]

    def cable_totals(self) -> list[dict]:
        """Footage from boxes that name only a cable ID (no fiber count written)."""
        agg: dict[str, dict] = {}
        for f in self.footage:
            if f.fiber_count is not None:
                continue
            a = agg.setdefault(f.cable, {"cable": f.cable, "feet": 0, "calcs": 0, "labels": set()})
            a["feet"] += f.feet
            a["calcs"] += 1
            a["labels"].add(f.label)
        return [{**agg[k], "labels": sorted(agg[k]["labels"])} for k in sorted(agg)]

    def flagged(self) -> list[tuple[str, int, str, str]]:
        """Everything worth a human look: (file, page, line, note)."""
        out = [(m.file, m.page, m.line, n) for m in self.materials for n in m.notes
               if m.source == "MATERIALS box"]
        out += [(f.file, f.page, f.line, n) for f in self.footage for n in f.notes]
        out += [(c.file, c.page, c.text, f"box total {c.stated:,} but its calculations add to "
                 f"{c.computed:,}") for c in self.checks if not c.matches]
        out += [(f, p, l, "could not be read as a quantity line") for f, p, l in self.unreadable]
        return out


def build_takeoff(name: str, files: list[DrawingFile]) -> DrawingTakeoff:
    """Parse every comment in the counted files of one drawing set."""
    mark_duplicates(files)
    t = DrawingTakeoff(name=name, files=files)
    for f in t.counted_files:
        for c in f.comments:
            if is_materials_box(c.text):
                lines, bad = parse_materials_box(f.name, c.page, c.text)
                t.materials.extend(lines)
                t.unreadable.extend((f.name, c.page, b) for b in bad)
            elif is_footage_box(c.text):
                footage, checks, strand = parse_footage_box(f.name, c.page, c.text)
                t.footage.extend(footage)
                t.checks.extend(checks)
                t.materials.extend(strand)
    _resolve_unlabeled(t.footage)
    _flag_outliers([m for m in t.materials if m.source == "MATERIALS box"])
    return t


def takeoffs_from_sources(sources: list[PdfSource], loose_name: str = "Drawings") -> list[DrawingTakeoff]:
    """Group sources into drawing sets — one per zip, plus one for loose PDFs —
    and build a takeoff for each."""
    groups: dict[str, list[DrawingFile]] = {}
    for s in sources:
        groups.setdefault(s.group or loose_name, []).append(read_drawing(s))
    return [build_takeoff(name, files) for name, files in groups.items()]
