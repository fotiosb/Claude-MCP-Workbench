"""PDF audit report via fpdf2. Clean, bounded, no binary blobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fpdf import FPDF


MAX_PDF_BYTES = 1_500_000


class _ReportPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}  |  MCP Workbench  |  static scan only", align="C")


def _safe(text: str) -> str:
    """fpdf core fonts are latin-1; strip unsupported glyphs lightly."""
    if not text:
        return ""
    return (
        str(text)
        .replace("\u2026", "...")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00b7", "|")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )


def _ensure_x(pdf: _ReportPDF) -> None:
    pdf.set_x(pdf.l_margin)


def _section(pdf: _ReportPDF, title: str) -> None:
    pdf.ln(4)
    _ensure_x(pdf)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(30, 40, 60)
    pdf.cell(0, 8, _safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(120, 140, 170)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    _ensure_x(pdf)
    pdf.set_text_color(20, 20, 20)


def _body(pdf: _ReportPDF, text: str, size: int = 10) -> None:
    _ensure_x(pdf)
    pdf.set_font("Helvetica", "", size)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(usable, 5, _safe(text))
    _ensure_x(pdf)


def _bullet(pdf: _ReportPDF, text: str) -> None:
    _ensure_x(pdf)
    pdf.set_font("Helvetica", "", 10)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(usable, 5, _safe(f"- {text}"))
    _ensure_x(pdf)


def pdf_report(run: dict[str, Any]) -> bytes:
    """Build a clean PDF for a completed run. Raises ValueError if oversized."""
    owner = run.get("owner") or "?"
    repo = run.get("repo") or "?"
    sha = run.get("sha") or ""
    branch = run.get("branch") or ""
    url = run.get("normalized_url") or run.get("url") or ""
    findings = run.get("findings") or {}
    secrets = findings.get("secrets") or []
    manifests = findings.get("manifests") or []
    ci = findings.get("ci") or []
    tests = findings.get("tests") or {}
    brief = (run.get("brief") or "").strip()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pdf = _ReportPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(16, 16, 16)
    pdf.set_y(16)
    _ensure_x(pdf)

    # Title
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 30, 50)
    pdf.cell(0, 10, _safe(f"Audit: {owner}/{repo}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 90)
    pdf.cell(0, 6, _safe(f"Generated {generated}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _ensure_x(pdf)

    _section(pdf, "Repository")
    _bullet(pdf, f"URL: {url}")
    _bullet(pdf, f"Branch: {branch or 'default'}")
    _bullet(pdf, f"SHA: {sha or 'unknown'}")
    _bullet(pdf, f"Cache hit: {'yes' if run.get('cache_hit') else 'no'}")

    tests_yes = "yes" if tests.get("present") else "no"
    _section(pdf, "Summary")
    _body(
        pdf,
        f"{len(manifests)} manifests  |  {len(ci)} workflows  |  "
        f"tests {tests_yes}  |  {len(secrets)} credential signals",
        size=11,
    )

    _section(pdf, f"Manifests ({len(manifests)})")
    if manifests:
        for m in manifests[:80]:
            _bullet(pdf, f"{m.get('path')} ({m.get('name')})")
        if len(manifests) > 80:
            _body(pdf, f"... and {len(manifests) - 80} more")
    else:
        _body(pdf, "None found.")

    _section(pdf, f"CI / workflows ({len(ci)})")
    if ci:
        for wf in ci[:60]:
            jobs = ", ".join(wf.get("jobs") or []) or "-"
            _bullet(pdf, f"{wf.get('path')} -- jobs: {jobs}")
        if len(ci) > 60:
            _body(pdf, f"... and {len(ci) - 60} more")
    else:
        _body(pdf, "No .github/workflows files.")

    _section(pdf, "Tests")
    if tests.get("present"):
        dirs = ", ".join(tests.get("test_dirs") or []) or "-"
        fw = "; ".join(tests.get("frameworks_guess") or [])
        line = f"{tests.get('test_file_count', 0)} test-like files; dirs: {dirs}"
        if fw:
            line += f"; frameworks: {fw}"
        _body(pdf, line)
    else:
        _body(pdf, "No conventional test layout detected.")

    _section(pdf, f"Credential signals ({len(secrets)})")
    if secrets:
        _body(pdf, "High-signal patterns only; values redacted. Dependency trees ignored.")
        pdf.ln(1)
        for item in secrets[:80]:
            sample = item.get("sample") or ""
            extra = f" -- {sample}" if sample else ""
            _bullet(
                pdf,
                f"{item.get('kind')} ({item.get('severity')}): {item.get('path')} "
                f"-- {item.get('detail')}{extra}",
            )
        if len(secrets) > 80:
            _body(pdf, f"... and {len(secrets) - 80} more")
    else:
        _body(pdf, "None in project source (dependency trees are ignored).")

    _section(pdf, "Brief")
    if brief:
        clipped = brief if len(brief) <= 6000 else brief[:6000] + "\n\n...[truncated]"
        _body(pdf, clipped)
    else:
        _body(pdf, "No brief.")

    pdf.ln(6)
    _ensure_x(pdf)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(
        usable,
        4,
        _safe(
            "Scans are static. Cloned code was not executed. "
            "MCP Workbench v1 -- public GitHub only."
        ),
    )

    raw = pdf.output()
    if isinstance(raw, str):
        data = raw.encode("latin-1")
    else:
        data = bytes(raw)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(f"PDF too large ({len(data)} bytes)")
    return data
