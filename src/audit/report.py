"""Markdown audit report. Findings first, then brief."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def markdown_report(run: dict[str, Any]) -> str:
    owner = run.get("owner") or "?"
    repo = run.get("repo") or "?"
    sha = run.get("sha") or ""
    branch = run.get("branch") or ""
    url = run.get("normalized_url") or run.get("url") or ""
    uri = run.get("resource_uri") or ""
    findings = run.get("findings") or {}
    secrets = findings.get("secrets") or []
    manifests = findings.get("manifests") or []
    ci = findings.get("ci") or []
    tests = findings.get("tests") or {}
    tree = run.get("tree") or findings.get("tree") or {}
    brief = run.get("brief") or ""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# Audit: {owner}/{repo}",
        "",
        f"- URL: {url}",
        f"- Branch: {branch or 'default'}",
        f"- SHA: `{sha}`" if sha else "- SHA: unknown",
        f"- Resource: `{uri}`" if uri else "",
        f"- Generated: {generated}",
        f"- Cache hit: {'yes' if run.get('cache_hit') else 'no'}",
        "",
        "## Findings",
        "",
        "Pattern hits only. Values are redacted. No CVEs are assigned.",
        "",
    ]
    lines = [ln for ln in lines if ln is not None]

    if secrets:
        lines.append(f"### Possible secrets ({len(secrets)})")
        lines.append("")
        for item in secrets:
            sample = item.get("sample") or ""
            extra = f" — `{sample}`" if sample else ""
            lines.append(
                f"- **{item.get('kind')}** ({item.get('severity')}): `{item.get('path')}` "
                f"— {item.get('detail')}{extra}"
            )
        lines.append("")
    else:
        lines.append("### Possible secrets")
        lines.append("")
        lines.append("None reported by filename / regex / entropy scans.")
        lines.append("")

    lines.append(f"### Manifests ({len(manifests)})")
    lines.append("")
    if manifests:
        for m in manifests:
            lines.append(f"- `{m.get('path')}` ({m.get('name')})")
    else:
        lines.append("None found.")
    lines.append("")

    lines.append(f"### CI ({len(ci)})")
    lines.append("")
    if ci:
        for wf in ci:
            jobs = ", ".join(wf.get("jobs") or []) or "—"
            lines.append(f"- `{wf.get('path')}` jobs: {jobs}")
    else:
        lines.append("No `.github/workflows` files.")
    lines.append("")

    lines.append("### Tests")
    lines.append("")
    if tests.get("present"):
        lines.append(
            f"{tests.get('test_file_count', 0)} test-like files; "
            f"dirs: {', '.join(tests.get('test_dirs') or []) or '—'}"
        )
        if tests.get("frameworks_guess"):
            lines.append("Guessed frameworks: " + "; ".join(tests["frameworks_guess"]))
    else:
        lines.append("No conventional test layout detected.")
    lines.append("")

    lines.append("### Tree")
    lines.append("")
    lines.append(
        f"{tree.get('file_count', 0)} files, {tree.get('dir_count', 0)} directories"
        + (" (top-level only)." if tree.get("top_level_only") else ".")
    )
    shown = (tree.get("files") or [])[:40]
    if shown:
        lines.append("")
        for f in shown:
            flag = " bin" if f.get("binary") else ""
            lines.append(f"- `{f.get('path')}` ({f.get('size', 0)} B{flag})")
        more = (tree.get("file_count") or 0) - len(shown)
        if more > 0:
            lines.append(f"- … {more} more")
    lines.append("")

    lines.append("## Brief")
    lines.append("")
    lines.append(brief.strip() or "_No brief._")
    lines.append("")
    lines.append("---")
    lines.append("Scans are static. Cloned code was not executed. MCP Workbench v1.")
    lines.append("")
    return "\n".join(lines)
