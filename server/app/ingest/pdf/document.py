"""Open a PDF from bytes, rejecting ones we can't read (design §6: invalid
PDFs are rejected at upload)."""

from __future__ import annotations

import pymupdf

from app.core.errors import PermanentError


class InvalidPdfError(PermanentError):
    """The file isn't a PDF we can read. The message is for the owner
    (the upload route returns it as a 400)."""


def open_pdf(pdf_bytes: bytes) -> pymupdf.Document:
    """Open `pdf_bytes`; raise `InvalidPdfError` for empty, corrupt,
    password-protected or page-less files. The caller closes the document.

    PDFs that are encrypted only with an owner password (print/copy
    restrictions) open without one, so they are accepted.
    """
    if not pdf_bytes:
        raise InvalidPdfError("The file is empty.")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # pymupdf.FileDataError and friends
        raise InvalidPdfError(f"The file is not a readable PDF ({exc}).") from exc
    try:
        if doc.needs_pass:
            raise InvalidPdfError(
                "The PDF is password-protected. Remove the password and upload it again."
            )
        if doc.page_count < 1:
            raise InvalidPdfError("The PDF has no pages.")
        try:
            doc.load_page(0)
        except Exception as exc:
            raise InvalidPdfError(
                f"The PDF is damaged: its first page can't be read ({exc})."
            ) from exc
    except BaseException:
        doc.close()
        raise
    return doc


def inspect_pdf(pdf_bytes: bytes) -> int:
    """Validate `pdf_bytes` (see `open_pdf`) and return its page count."""
    doc = open_pdf(pdf_bytes)
    try:
        return doc.page_count
    finally:
        doc.close()
