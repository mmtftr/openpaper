#!/usr/bin/env python3
"""
One-off probe: parse a PDF with Azure-hosted Mistral Document AI and dump
the response so we can compare against the current pymupdf4llm output.

Usage:
  AZURE_API_KEY=... python scripts/probe_mistral_ocr.py path/to/file.pdf
  AZURE_API_KEY=... AZURE_OCR_ENDPOINT=https://...services.ai.azure.com/providers/mistral/azure/ocr \\
      python scripts/probe_mistral_ocr.py path/to/file.pdf

Writes <pdf_basename>.ocr.json and <pdf_basename>.ocr.md next to the PDF.
"""

import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_ENDPOINT = (
    "https://your-azure-resource.services.ai.azure.com/providers/mistral/azure/ocr"
    "?api-version=2024-05-01-preview"
)
DEFAULT_MODEL = "mistral-document-ai-2512"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    pdf_path = Path(sys.argv[1]).resolve()
    if not pdf_path.exists():
        print(f"file not found: {pdf_path}")
        return 1

    api_key = (
        os.environ.get("MISTRAL_API_KEY")
        or os.environ.get("AZURE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        print("set MISTRAL_API_KEY or AZURE_API_KEY")
        return 1

    if os.environ.get("MISTRAL_API_KEY"):
        endpoint = os.environ.get("OCR_ENDPOINT", "https://api.mistral.ai/v1/ocr")
        model = os.environ.get("OCR_MODEL", "mistral-ocr-latest")
    else:
        endpoint = os.environ.get("OCR_ENDPOINT", DEFAULT_ENDPOINT)
        model = os.environ.get("OCR_MODEL", DEFAULT_MODEL)

    file_bytes = pdf_path.read_bytes()
    b64 = base64.b64encode(file_bytes).decode("ascii")
    suffix = pdf_path.suffix.lower()
    if suffix == ".pdf":
        mime = "application/pdf"
        doc_type = "document_url"
        url_field = "document_url"
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }[suffix]
        doc_type = "image_url"
        url_field = "image_url"
    else:
        print(f"unsupported file type: {suffix}")
        return 1

    print(
        f"file: {pdf_path.name} ({mime})  size: {len(file_bytes)/1024:.1f} KiB  "
        f"b64: {len(b64)/1024:.1f} KiB"
    )

    payload = {
        "model": model,
        "document": {
            "type": doc_type,
            url_field: f"data:{mime};base64,{b64}",
        },
        "include_image_base64": True,
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    print(f"POST {endpoint}  model={model}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.reason}")
        print(e.read().decode("utf-8", errors="replace")[:4000])
        return 1
    except urllib.error.URLError as e:
        print(f"network error: {e.reason}")
        return 1

    elapsed = time.time() - t0
    print(f"status {status}  elapsed {elapsed:.2f}s  bytes {len(raw)}")

    out_dir = pdf_path.parent
    json_path = out_dir / f"{pdf_path.stem}.ocr.json"
    md_path = out_dir / f"{pdf_path.stem}.ocr.md"

    json_path.write_bytes(raw)
    print(f"wrote {json_path}  ({json_path.stat().st_size} bytes)")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"could not parse JSON response: {e}")
        return 1

    pages = data.get("pages") or []
    md_chunks: list[str] = []
    for page in pages:
        idx = page.get("index")
        text = page.get("markdown") or page.get("text") or ""
        md_chunks.append(f"\n\n<!-- page {idx} -->\n\n{text}")

    md_path.write_text("".join(md_chunks))
    total_chars = sum(len(c) for c in md_chunks)
    print(
        f"wrote {md_path}  pages={len(pages)}  total_chars={total_chars}  "
        f"avg/page={total_chars // max(len(pages),1)}"
    )

    top_keys = sorted(data.keys())
    print(f"top-level keys: {top_keys}")
    if pages:
        print(f"page[0] keys: {sorted(pages[0].keys())}")
        first_imgs = pages[0].get("images") or []
        print(f"page[0] images: {len(first_imgs)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
