"""Zero-pip PDF text extraction using system pdftotext -layout with pure-stdlib zlib fallback."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import zlib

MAX_PDF_TEXT_BYTES = 16 * 1024 * 1024


def _stdlib_pdf_fallback(raw: bytes) -> list[str]:
    """Minimal pure-Python fallback extractor for uncompressed or FlateDecode PDF text streams."""
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.S):
        stream_bytes = m.group(1)
        decoded = stream_bytes
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decoded = zlib.decompress(stream_bytes, wbits)
                break
            except zlib.error:
                continue
        text_ops = re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decoded)
        if text_ops:
            line = " ".join(op.decode("latin-1", errors="ignore") for op in text_ops)
            chunks.append(line)
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", decoded, flags=re.S):
            parts = re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr)
            if parts:
                chunks.append("".join(p.decode("latin-1", errors="ignore") for p in parts))
    joined = "\n".join(c.strip() for c in chunks if c.strip())
    return [joined] if joined else [""]


def extract_pdf_pages(path: Path) -> list[str]:
    """Extract per-page layout-preserving text from a PDF file."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"PDF file not found: {path}")
    raw_head = path.read_bytes()[:8]
    if not raw_head.startswith(b"%PDF-"):
        raise ValueError(f"Not a valid PDF header in {path.name}")

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        res = subprocess.run(
            [pdftotext_bin, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if res.returncode == 0 and res.stdout:
            if len(res.stdout) > MAX_PDF_TEXT_BYTES:
                raise ValueError(f"Extracted PDF text exceeds {MAX_PDF_TEXT_BYTES} bytes")
            text = res.stdout.decode("utf-8", errors="replace")
            pages = [p.rstrip() for p in text.split("\f")]
            while pages and pages[-1] == "":
                pages.pop()
            if pages:
                return pages

    raw = path.read_bytes()
    if len(raw) > MAX_PDF_TEXT_BYTES:
        raise ValueError(f"PDF exceeds {MAX_PDF_TEXT_BYTES} bytes for stdlib fallback")
    return _stdlib_pdf_fallback(raw)


def extract_pdf_text(path: Path, *, max_pages: int | None = None) -> dict:
    """Return structured page count and Markdown-formatted page text for CLI and MCP."""
    pages = extract_pdf_pages(path)
    selected = pages[:max_pages] if (max_pages is not None and max_pages > 0) else pages
    rendered_pages = [
        f"--- Страница {idx} ---\n{page_text}"
        for idx, page_text in enumerate(selected, 1)
    ]
    return {
        "file": path.name,
        "total_pages": len(pages),
        "returned_pages": len(selected),
        "text": "\n\n".join(rendered_pages),
    }
