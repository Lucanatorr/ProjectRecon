"""Turn uploads (loose PDFs and zips of PDFs) into named PDF sources.

A zip is a *group*: every PDF inside it belongs to one drawing set, named after
the zip. Loose PDFs have no group. Zip members are read in memory — nothing is
extracted to disk, so a member path like ``../../x.pdf`` can never escape.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class PdfSource:
    name: str                 # file name as shown to the user (zip member path)
    data: bytes
    group: str | None = None  # zip stem for zipped files, None for loose PDFs


# A zip bomb guard: refuse to inflate more than this from one archive.
MAX_UNZIPPED_BYTES = 2 * 1024 ** 3


def _is_pdf_member(info: zipfile.ZipInfo) -> bool:
    p = PurePosixPath(info.filename)
    return (not info.is_dir() and p.suffix.lower() == ".pdf"
            and "__MACOSX" not in p.parts and not p.name.startswith("._"))


def expand(name: str, data: bytes) -> list[PdfSource]:
    """One upload → its PDF sources. A zip yields every PDF member (sorted by
    path); a PDF yields itself; anything else yields nothing."""
    lower = name.lower()
    if lower.endswith(".pdf"):
        return [PdfSource(name=name, data=data)]
    if not lower.endswith(".zip"):
        return []
    group = PurePosixPath(name).stem
    out: list[PdfSource] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = sorted((i for i in zf.infolist() if _is_pdf_member(i)),
                         key=lambda i: i.filename)
        if sum(i.file_size for i in members) > MAX_UNZIPPED_BYTES:
            raise ValueError(f"{name}: the PDFs inside inflate to more than "
                             f"{MAX_UNZIPPED_BYTES // 1024 ** 3} GB")
        for info in members:
            # show the path inside the zip, minus a leading folder that only
            # repeats the zip's own name ("A.zip" → "A/x.pdf" → "x.pdf")
            parts = PurePosixPath(info.filename).parts
            shown = "/".join(parts[1:]) if len(parts) > 1 and parts[0] == group else info.filename
            out.append(PdfSource(name=shown, data=zf.read(info), group=group))
    return out


def expand_all(uploads: list[tuple[str, bytes]]) -> list[PdfSource]:
    out: list[PdfSource] = []
    for name, data in uploads:
        out.extend(expand(name, data))
    return out
