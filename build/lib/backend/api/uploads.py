"""Shared handling for user-uploaded documents.

Both the intake flow (`routers/contracts.py`) and clause extraction
(`routers/extract.py`) accept the same four file types under the same limits, and both
need bytes turned into text. Keeping one implementation here means a parser fix reaches
every entry point, and means the limits cannot drift apart silently.

`extract_text` raises rather than returning a best-effort decode. An earlier version in
the extract router wrapped everything in `except Exception: data.decode(errors=...)`,
which turned an unreadable PDF into a page of mojibake that then scored against the
clause library as though it were prose. A refusal the user can act on beats a plausible
wrong answer.
"""

from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException

__all__ = [
    "ALLOWED_UPLOADS",
    "MAX_UPLOADS",
    "MAX_UPLOAD_BYTES",
    "extract_text",
    "reject_unsupported",
]

MAX_UPLOADS = 5
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOADS = {".txt", ".md", ".docx", ".pdf"}

_UNSUPPORTED = "Only TXT, Markdown, DOCX, and PDF files are supported."


def _suffix(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def reject_unsupported(filename: str) -> None:
    """Raise 415 unless `filename` carries a supported extension."""
    if _suffix(filename) not in ALLOWED_UPLOADS:
        raise HTTPException(status_code=415, detail=_UNSUPPORTED)


def extract_text(filename: str, data: bytes) -> str:
    """Return the plain text of an uploaded document.

    Raises `HTTPException(415)` for an unsupported extension. Parser failures propagate;
    callers decide whether that is a 422 or a hard error.
    """
    suffix = _suffix(filename)
    if suffix in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace").strip()
    if suffix == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(BytesIO(data)).paragraphs).strip()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(
            page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages
        ).strip()
    raise HTTPException(status_code=415, detail=_UNSUPPORTED)
