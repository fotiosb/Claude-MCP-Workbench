"""Scan .github/workflows without executing them."""

from __future__ import annotations

from pathlib import Path

from src.audit.path_safe import is_probably_binary, resolve_inside
from src.host.config import get_settings


def scan_ci(root: Path, tree_files: list[dict] | None = None) -> list[dict]:
    settings = get_settings()
    root = Path(root)
    workflows: list[str] = []
    if tree_files:
        for item in tree_files:
            p = item["path"].replace("\\", "/")
            if p.startswith(".github/workflows/") and p.lower().endswith((".yml", ".yaml")):
                workflows.append(p)
    else:
        wf = root / ".github" / "workflows"
        if wf.is_dir():
            for fp in sorted(wf.iterdir()):
                if fp.suffix.lower() in {".yml", ".yaml"} and fp.is_file():
                    workflows.append(str(fp.relative_to(root)).replace("\\", "/"))

    out: list[dict] = []
    for rel in workflows[:80]:
        try:
            path = resolve_inside(root, rel)
        except Exception:
            continue
        if not path.is_file() or is_probably_binary(path):
            continue
        try:
            text = path.read_bytes()[: settings.max_read_bytes].decode("utf-8", errors="replace")
        except OSError:
            continue
        triggers: list[str] = []
        jobs: list[str] = []
        uses: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("on:") or stripped.startswith("on :"):
                triggers.append(stripped)
            if line.startswith("  ") and stripped.endswith(":") and "jobs:" not in stripped:
                # cheap job-name scrape
                if not stripped.startswith("-") and ":" in stripped and "uses:" not in stripped:
                    name = stripped.split(":", 1)[0]
                    if name and " " not in name and name not in {"on", "env", "defaults", "permissions", "concurrency", "name"}:
                        jobs.append(name)
            if "uses:" in stripped:
                uses.append(stripped.split("uses:", 1)[1].strip().strip("\"'"))
        out.append(
            {
                "path": rel,
                "triggers": triggers[:8],
                "jobs": jobs[:20],
                "actions": uses[:30],
                "lines": len(text.splitlines()),
            }
        )
    return out
