"""OCR fallback for scanned as-built PDFs (Phase 4).

A page with no text layer is rendered to an image (via pdfplumber/pypdfium2 — no
Poppler needed), OCR'd with Tesseract, and the recognised words are reassembled
into an approximate table grid so the normal table parser can read it.

Everything produced here is tagged ``confidence="ocr"`` and routed through the
editable review grid — OCR output is never silently trusted (NFR-4).

Requires the Tesseract binary. When it is absent the extractor degrades
gracefully and tells the coordinator how to install it.
"""
from __future__ import annotations

from bisect import bisect_right

INSTALL_HINT = (
    "Install Tesseract OCR and make sure `tesseract` is on PATH — on Windows: "
    "winget install UB-Mannheim.TesseractOCR"
)


def ocr_status() -> tuple[bool, str]:
    """(available, human-readable status). Never raises."""
    try:
        import pytesseract
    except ImportError:
        return False, f"pytesseract is not installed (pip install pytesseract). {INSTALL_HINT}"
    try:
        version = pytesseract.get_tesseract_version()
    except Exception:
        return False, f"Tesseract binary not found. {INSTALL_HINT}"
    return True, f"Tesseract {version}"


def words_to_grid(words: list[dict], *, cell_gap: int = 25,
                  col_gap: int = 25) -> list[list[str]]:
    """Reassemble OCR words into a table grid.

    Two passes, because a word gap alone can't tell "next word in this cell" from
    "next column":

    1. Within each line, consecutive words are merged into one cell while the gap
       between them stays under ``cell_gap`` — so "144F Aerial Fiber" survives as a
       single description instead of splitting into three columns.
    2. The resulting cell start positions are clustered page-wide into column
       bands. Deriving bands across the whole page (not per line) keeps rows
       aligned when a cell is empty, which the positional column mapping needs.

    ``words`` are dicts with text / left / top / line (a hashable line id) and,
    ideally, width — without width, words can only be separated by their left edges.
    """
    words = [w for w in words if str(w.get("text", "")).strip()]
    if not words:
        return []

    lines: dict = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)

    # pass 1 — merge words into cells per line
    line_cells: list[tuple[int, list[tuple[int, str]]]] = []   # (top, [(start, text)])
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda x: int(x["left"]))
        cells: list[tuple[int, str]] = []
        start = int(ordered[0]["left"])
        text = str(ordered[0]["text"]).strip()
        cursor = start + int(ordered[0].get("width", 0))
        for w in ordered[1:]:
            left = int(w["left"])
            if left - cursor > cell_gap:                       # a real column break
                cells.append((start, text))
                start, text = left, str(w["text"]).strip()
            else:
                text = f"{text} {str(w['text']).strip()}".strip()
            cursor = left + int(w.get("width", 0))
        cells.append((start, text))
        line_cells.append((min(int(x["top"]) for x in line_words), cells))

    # pass 2 — cluster cell starts into page-wide column bands
    starts = sorted(s for _, cells in line_cells for s, _ in cells)
    bands = [starts[0]]
    prev = starts[0]
    for s in starts[1:]:
        if s - prev > col_gap:
            bands.append(s)
        prev = s

    grid: list[list[str]] = []
    for _, cells in sorted(line_cells, key=lambda lc: lc[0]):
        row = [""] * len(bands)
        for start, text in cells:
            idx = max(0, min(bisect_right(bands, start) - 1, len(bands) - 1))
            row[idx] = (row[idx] + " " + text).strip()
        grid.append(row)
    return grid


def page_to_grid(page, *, resolution: int | None = None,
                 min_conf: int | None = None) -> list[list[str]]:
    """Render a pdfplumber page, OCR it, and return an approximate table grid.

    Returns [] when OCR is unavailable or nothing legible is found.
    """
    from config import OCR

    resolution = OCR.resolution if resolution is None else resolution
    min_conf = OCR.min_confidence if min_conf is None else min_conf

    available, _ = ocr_status()
    if not available:
        return []
    import pytesseract
    from pytesseract import Output

    try:
        image = page.to_image(resolution=resolution).original
        data = pytesseract.image_to_data(image, output_type=Output.DICT)
    except Exception:
        return []

    words = []
    for i, text in enumerate(data.get("text", [])):
        if not str(text).strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf:
            continue
        words.append({
            "text": text,
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data.get("width", [0] * len(data["text"]))[i],
            "line": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
        })
    # scale the gaps with render resolution (~0.08in at 300dpi)
    gap = max(12, resolution // 12)
    return words_to_grid(words, cell_gap=gap, col_gap=gap)
