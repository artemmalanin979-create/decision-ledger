#!/usr/bin/env python3
"""Rebuild the antifraud demo PDFs (product and communication strategies) and passport screenshots.

Requires a local ``google-chrome`` (headless). Not part of the stdlib-only ledger core; run manually:

    python3 scripts/render_antifraud_pdfs.py
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "demo/antifraud-subscription"
ASSETS = REPO / "docs/assets"
CHROME = ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars"]


def md_inline(text: str) -> str:
    s = escape(text)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href=\"\2\">\1</a>", s)
    return s

def md_to_styled_html(md_text: str, doc_title: str) -> str:
    lines = md_text.splitlines()
    out = []
    in_ul = False
    in_ol = False
    in_table = False
    in_code = False

    def close_lists():
        nonlocal in_ul, in_ol, in_table
        if in_ul:
            out.append("</ul>"); in_ul = False
        if in_ol:
            out.append("</ol>"); in_ol = False
        if in_table:
            out.append("</tbody></table>"); in_table = False

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if s.startswith("```"):
            close_lists()
            if not in_code:
                in_code = True; out.append("<pre><code>")
            else:
                in_code = False; out.append("</code></pre>")
            i += 1; continue
        if in_code:
            out.append(escape(raw)); i += 1; continue
        if not s:
            close_lists(); i += 1; continue
        if s == "---":
            close_lists(); out.append("<hr/>"); i += 1; continue
        if s.startswith("|") and s.endswith("|"):
            if not in_table:
                close_lists()
                headers = [md_inline(c.strip()) for c in s.strip("|").split("|")]
                out.append("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>")
                in_table = True
                if i + 1 < len(lines) and re.match(r"^\|[\s:\-\|]+\|$", lines[i+1].strip()):
                    i += 2; continue
            else:
                cells = [md_inline(c.strip()) for c in s.strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            i += 1; continue
        close_lists()
        if s.startswith("# "):
            out.append(f"<h1>{md_inline(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{md_inline(s[3:])}</h2>")
        elif s.startswith("### "):
            out.append(f"<h3>{md_inline(s[4:])}</h3>")
        elif s.startswith("#### "):
            out.append(f"<h4>{md_inline(s[5:])}</h4>")
        elif s.startswith("> "):
            out.append(f"<blockquote>{md_inline(s[2:])}</blockquote>")
        elif re.match(r"^[-*]\s+", s):
            out.append("<ul>")
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                item = re.sub(r"^\s*[-*]\s+", "", lines[i])
                out.append(f"<li>{md_inline(item)}</li>")
                i += 1
            out.append("</ul>"); continue
        elif re.match(r"^\d+\.\s+", s):
            out.append("<ol>")
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                item = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                out.append(f"<li>{md_inline(item)}</li>")
                i += 1
            out.append("</ol>"); continue
        else:
            out.append(f"<p>{md_inline(s)}</p>")
        i += 1
    close_lists()
    body = "\n".join(out)
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"/><title>{escape(doc_title)}</title>
<style>
  @page {{ size: A4; margin: 16mm 15mm 16mm 15mm; }}
  body {{ font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; color: #0F172A; font-size: 10.2pt; line-height: 1.48; margin: 0; }}
  h1 {{ font-size: 18pt; line-height: 1.2; margin: 0 0 10pt; color: #0F172A; border-bottom: 1.5pt solid #0F766E; padding-bottom: 6pt; }}
  h2 {{ font-size: 13pt; margin: 14pt 0 6pt; color: #0F766E; page-break-after: avoid; }}
  h3 {{ font-size: 11pt; margin: 10pt 0 4pt; color: #1E293B; page-break-after: avoid; }}
  p, li {{ margin: 0 0 5pt; }}
  ul, ol {{ margin: 0 0 8pt 16pt; padding: 0; }}
  blockquote {{ margin: 8pt 0; padding: 7pt 10pt; background: #F0FDFA; border-left: 3pt solid #0F766E; color: #1E293B; font-size: 9.6pt; }}
  table {{ width: 100%; border-collapse: collapse; margin: 8pt 0 10pt; font-size: 8.8pt; page-break-inside: auto; }}
  tr {{ page-break-inside: avoid; }}
  th, td {{ border: 0.6pt solid #CBD5E1; padding: 5pt 6pt; text-align: left; vertical-align: top; }}
  th {{ background: #F8FAFC; font-weight: 700; color: #334155; }}
  code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: #F1F5F9; padding: 1pt 3pt; border-radius: 3pt; }}
  pre {{ background: #0F172A; color: #F8FAFC; padding: 8pt 10pt; border-radius: 5pt; overflow-x: auto; font-size: 8.4pt; }}
  pre code {{ background: none; padding: 0; color: inherit; }}
  hr {{ border: none; border-top: 0.7pt solid #E2E8F0; margin: 12pt 0; }}
  a {{ color: #0F766E; text-decoration: none; }}
</style></head><body>{body}</body></html>"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        for stem, title in [
            ("strategy-product", "Продуктовая стратегия 2027 — Линейка защиты от спама и мошенников"),
            ("strategy-comms", "Коммуникационная стратегия 2027 — Защити родителей"),
        ]:
            html_file = tdir / f"{stem}.html"
            html_file.write_text(md_to_styled_html((DEST / f"{stem}.md").read_text(encoding="utf-8"), title), encoding="utf-8")
            pdf_out = DEST / f"{stem}.pdf"
            subprocess.run(CHROME + ["--no-pdf-header-footer", f"--print-to-pdf={pdf_out}", html_file.as_uri()],
                           check=True, capture_output=True)
            print(f"Built {pdf_out.relative_to(REPO)}: {pdf_out.stat().st_size:,} bytes")
    for html_name, png_name, size in [
        ("04-dashboard-stale.html", "antifraud-stale.png", "1440,1040"),
        ("decision-passport.html", "antifraud-passport-top.png", "1440,980"),
    ]:
        png = ASSETS / png_name
        subprocess.run(CHROME + [f"--window-size={size}", f"--screenshot={png}", (DEST / html_name).as_uri()],
                       check=True, capture_output=True)
        print(f"Built {png.relative_to(REPO)}")


if __name__ == "__main__":
    main()
