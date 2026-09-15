"""Detect test directories and files by convention. No execution."""

from __future__ import annotations

import re
from pathlib import Path

TEST_DIR_NAMES = {
    "test",
    "tests",
    "__tests__",
    "spec",
    "specs",
    "e2e",
    "cypress",
    "playwright",
}

TEST_FILE_RE = re.compile(
    r"(?:^test_.*|_test\.(?:py|go|rs)$|\.(?:test|spec)\.(?:js|ts|tsx|jsx)$"
    r"|_spec\.(?:rb|js|ts)$|Tests\.cs$)",
    re.IGNORECASE,
)


def scan_tests(root: Path, tree_files: list[dict] | None = None, tree_dirs: list[str] | None = None) -> dict:
    root = Path(root)
    test_files: list[str] = []
    test_dirs: list[str] = []

    if tree_dirs:
        for d in tree_dirs:
            name = Path(d.rstrip("/")).name.lower()
            if name in TEST_DIR_NAMES:
                test_dirs.append(d if d.endswith("/") else d + "/")

    if tree_files:
        for item in tree_files:
            p = item["path"].replace("\\", "/")
            base = Path(p).name
            parent = Path(p).parent.name.lower()
            if TEST_FILE_RE.search(base) or parent in TEST_DIR_NAMES:
                test_files.append(p)
    else:
        import os

        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = str(Path(dirpath).relative_to(root)).replace("\\", "/")
            if Path(dirpath).name.lower() in TEST_DIR_NAMES:
                test_dirs.append((rel_dir if rel_dir != "." else Path(dirpath).name) + "/")
            for name in filenames:
                rel = str(Path(dirpath, name).relative_to(root)).replace("\\", "/")
                if TEST_FILE_RE.search(name) or Path(dirpath).name.lower() in TEST_DIR_NAMES:
                    test_files.append(rel)

    frameworks: list[str] = []
    joined = " ".join(test_files).lower()
    if any(x.endswith(".py") or "/test_" in x or x.startswith("test_") for x in test_files):
        frameworks.append("pytest/unittest (Python files present)")
    if ".test.ts" in joined or ".test.tsx" in joined or ".spec.ts" in joined:
        frameworks.append("Jest/Vitest (*.test.ts / *.spec.ts)")
    if "cypress" in joined:
        frameworks.append("Cypress")
    if "playwright" in joined:
        frameworks.append("Playwright")
    if any(x.endswith("_test.go") for x in test_files):
        frameworks.append("Go testing")

    return {
        "test_dirs": sorted(set(test_dirs))[:80],
        "test_files": test_files[:200],
        "test_file_count": len(test_files),
        "frameworks_guess": frameworks,
        "present": bool(test_files or test_dirs),
    }
