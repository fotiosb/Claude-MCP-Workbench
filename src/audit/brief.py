"""Deterministic brief, plus optional Claude. Never invent secrets or CVEs."""

from __future__ import annotations

import logging
from typing import Any

from src.store import db

log = logging.getLogger("mcp_workbench.brief")

SYSTEM = (
    "You write a short operator brief for a public GitHub repository audit. "
    "Use only the supplied findings. Do not invent CVEs, secrets, vulnerabilities, "
    "or dependencies that are not in the data. Do not recommend exploits. "
    "If something is unknown, say so. Redacted values stay redacted. "
    "Tone: tool, not marketing. Three to eight short paragraphs or bullets."
)


def deterministic_brief(payload: dict[str, Any]) -> str:
    owner = payload.get("owner") or "?"
    repo = payload.get("repo") or "?"
    sha = (payload.get("sha") or "")[:12]
    branch = payload.get("branch") or ""
    description = payload.get("description") or ""
    manifests = payload.get("manifests") or []
    ci = payload.get("ci") or []
    secrets = payload.get("secrets") or []
    tests = payload.get("tests") or {}
    tree = payload.get("tree") or {}
    file_count = tree.get("file_count") or 0
    top_only = tree.get("top_level_only")

    lines = [
        f"## {owner}/{repo}",
        "",
        f"Resolved {owner}/{repo}"
        + (f" @ {branch}" if branch else "")
        + (f" (`{sha}`)" if sha else "")
        + ".",
    ]
    if description:
        lines.append(f"GitHub description: {description}")
    lines.append(
        f"Tree: {file_count} files"
        + (" (top-level only — elicitation chose a shallow walk)." if top_only else ".")
    )

    if manifests:
        names = sorted({m.get("name") or Path_name(m.get("path")) for m in manifests})
        lines.append(f"Manifests: {', '.join(n for n in names if n)}.")
        pkg = next((m for m in manifests if m.get("name") == "package.json"), None)
        if pkg and pkg.get("dependencies") is not None:
            lines.append(
                f"package.json reports {pkg.get('dependency_count', 0)} declared npm dependencies "
                f"({len(pkg.get('scripts') or [])} scripts)."
            )
        if any(m.get("name") == "pyproject.toml" for m in manifests):
            lines.append("Python project metadata present (pyproject.toml).")
        if any(m.get("name") == "requirements.txt" for m in manifests):
            lines.append("requirements.txt present.")
        if any(m.get("name") == "Cargo.toml" for m in manifests):
            lines.append("Rust crate / workspace (Cargo.toml).")
        if any(m.get("name") == "go.mod" for m in manifests):
            lines.append("Go module (go.mod).")
    else:
        lines.append("No well-known package manifests found at scanned paths.")

    if ci:
        lines.append(f"CI: {len(ci)} GitHub Actions workflow file(s).")
        for wf in ci[:6]:
            jobs = ", ".join(wf.get("jobs") or []) or "jobs not parsed"
            lines.append(f"- `{wf.get('path')}` — {jobs}")
    else:
        lines.append("CI: no `.github/workflows` files found.")

    if secrets:
        high = sum(1 for s in secrets if s.get("severity") == "high")
        medium = sum(1 for s in secrets if s.get("severity") == "medium")
        low = sum(1 for s in secrets if s.get("severity") == "low")
        lines.append(
            f"Secret scan (regex / entropy / filenames): {len(secrets)} possible finding(s) "
            f"— {high} high, {medium} medium, {low} low. Values are redacted. "
            "These are pattern hits, not confirmed leaks and not CVEs."
        )
    else:
        lines.append("Secret scan: no regex/filename hits in the scanned text files.")

    if tests.get("present"):
        lines.append(
            f"Tests: {tests.get('test_file_count', 0)} test-like file(s) "
            f"across {len(tests.get('test_dirs') or [])} test-like directories. "
            + ("Guessed: " + "; ".join(tests.get("frameworks_guess") or []) if tests.get("frameworks_guess") else "")
        )
    else:
        lines.append("Tests: no conventional test files or directories detected.")

    lines.extend(
        [
            "",
            "This brief is derived only from a shallow clone and static scans. "
            "Cloned code was not executed. No vulnerability identifiers were invented.",
        ]
    )
    return "\n".join(lines)


def Path_name(path: str | None) -> str:
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


async def maybe_claude_brief(payload: dict[str, Any]) -> str | None:
    from src.host.llm_settings import get_effective_llm

    llm = get_effective_llm()
    api_key = llm.get("anthropic_api_key") or ""
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        log.info("anthropic SDK not installed; using deterministic brief")
        return None
    # Build a compact, already-redacted context. Do not send raw file bodies.
    compact = {
        "owner": payload.get("owner"),
        "repo": payload.get("repo"),
        "branch": payload.get("branch"),
        "sha": payload.get("sha"),
        "description": payload.get("description"),
        "file_count": (payload.get("tree") or {}).get("file_count"),
        "top_level_only": (payload.get("tree") or {}).get("top_level_only"),
        "manifests": payload.get("manifests"),
        "ci": payload.get("ci"),
        "secrets": payload.get("secrets"),
        "tests": payload.get("tests"),
    }
    model = llm.get("anthropic_model") or "claude-sonnet-4-20250514"
    effort = llm.get("anthropic_effort") or "medium"
    user_content = (
        "Write the brief from this JSON. Do not add facts that are not present.\n\n"
        + __import__("json").dumps(compact, default=str)[:20000]
    )
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        kwargs = {
            "model": model,
            "max_tokens": 800,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": user_content}],
            "output_config": {"effort": effort},
        }
        try:
            msg = await client.messages.create(**kwargs)
        except TypeError:
            # Older SDKs / models may reject output_config — still send model+key.
            kwargs.pop("output_config", None)
            msg = await client.messages.create(**kwargs)
        parts = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text = "\n".join(parts).strip()
        return text or None
    except Exception as exc:
        log.warning("Claude brief failed; falling back. %s", exc)
        return None


async def write_brief(
    payload: dict[str, Any],
    *,
    skip_llm: bool = False,
    client_ip: str | None = None,
) -> dict:
    from src.host.llm_settings import get_effective_llm

    fallback = deterministic_brief(payload)
    if skip_llm:
        return {"text": fallback, "source": "deterministic", "skipped_llm": True}
    llm = get_effective_llm()
    if not llm.get("anthropic_api_key"):
        return {"text": fallback, "source": "deterministic", "skipped_llm": False}
    ip = client_ip or "unknown"
    if not db.consume_llm_slot(ip):
        log.info("daily LLM cap reached for %s; using deterministic brief", ip)
        return {"text": fallback, "source": "deterministic", "skipped_llm": True}
    claude = await maybe_claude_brief(payload)
    if claude:
        return {"text": claude, "source": "claude", "skipped_llm": False}
    return {"text": fallback, "source": "deterministic", "skipped_llm": False}
