"""Scan well-known package manifests. Read-only, size-capped."""

from __future__ import annotations

import json
from pathlib import Path

from src.audit.path_safe import is_probably_binary, resolve_inside
from src.host.config import get_settings

MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "setup.py",
    "setup.cfg",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Package.swift",
    "mix.exs",
    "pubspec.yaml",
}


def _read_text(path: Path, limit: int) -> str:
    data = path.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")


def _summarize(name: str, text: str) -> dict:
    summary: dict = {"name": name}
    if name == "package.json":
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            summary["parse_error"] = True
            return summary
        summary["package"] = body.get("name")
        summary["version"] = body.get("version")
        summary["scripts"] = sorted((body.get("scripts") or {}).keys())[:40]
        deps = sorted((body.get("dependencies") or {}).keys())
        dev = sorted((body.get("devDependencies") or {}).keys())
        summary["dependencies"] = deps[:80]
        summary["devDependencies"] = dev[:80]
        summary["dependency_count"] = len(deps) + len(dev)
        return summary
    if name == "pyproject.toml":
        summary["has_project"] = "[project]" in text
        summary["has_tool_poetry"] = "[tool.poetry]" in text
        summary["has_setuptools"] = "setuptools" in text
        return summary
    if name == "Cargo.toml":
        summary["has_package"] = "[package]" in text
        summary["has_workspace"] = "[workspace]" in text
        return summary
    if name == "go.mod":
        first = text.splitlines()[0] if text.splitlines() else ""
        summary["module"] = first
        return summary
    if name in {"requirements.txt", "Gemfile", "composer.json", "pom.xml", "build.gradle", "build.gradle.kts"}:
        summary["lines"] = len(text.splitlines())
        return summary
    return summary


def scan_manifests(root: Path, tree_files: list[dict] | None = None) -> list[dict]:
    settings = get_settings()
    root = Path(root)
    found: list[dict] = []
    candidates: list[str] = []
    if tree_files:
        for item in tree_files:
            name = Path(item["path"]).name
            if name in MANIFEST_NAMES:
                candidates.append(item["path"])
    else:
        for dirpath, dirnames, filenames in __import__("os").walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv"}]
            for name in filenames:
                if name in MANIFEST_NAMES:
                    rel = str(Path(dirpath, name).relative_to(root)).replace("\\", "/")
                    candidates.append(rel)

    for rel in candidates[:200]:
        try:
            path = resolve_inside(root, rel)
        except Exception:
            continue
        if not path.is_file() or is_probably_binary(path):
            continue
        try:
            text = _read_text(path, settings.max_read_bytes)
        except OSError:
            continue
        rec = _summarize(path.name, text)
        rec["path"] = rel
        rec["bytes"] = min(path.stat().st_size, settings.max_read_bytes)
        found.append(rec)
    return found
