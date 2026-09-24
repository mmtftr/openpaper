"""pymupdf work for the ingest stages.

Everything here is plain, synchronous and picklable (bytes in, dataclasses
out), so the worker can run it in its process pool via `ctx.cpu(fn, ...)`.

- `document` — open/validate a PDF (`open_pdf`, `inspect_pdf`, `InvalidPdfError`)
- `text` — per-page text layer + the PDF's embedded (info/XMP) metadata
- `render` — first-page preview
- `figures` — figure boxes rendered at 300 DPI
"""
