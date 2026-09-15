"""Walk a clone without leaving the jail. Never execute files."""

from __future__ import annotations

import os
from pathlib import Path

from src.audit.path_safe import is_probably_binary
from src.host.config import get_settings

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    "coverage",
    ".idea",
    ".vscode",
}


def walk_tree(root: Path, *, top_level_only: bool = False, max_files: int | None = None) -> dict:
    settings = get_settings()
    cap = max_files if max_files is not None else settings.max_files
    root = Path(root).resolve()
    files: list[dict] = []
    dirs: list[str] = []
    truncated = False
    file_count = 0

    if top_level_only:
        try:
            entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            entries = []
        for entry in entries:
            rel = entry.name
            if entry.is_dir():
                dirs.append(rel + "/")
            else:
                file_count += 1
                if len(files) < cap:
                    try:
                        size = entry.stat().st_size if not entry.is_symlink() else 0
                    except OSError:
                        size = 0
                    files.append(
                        {
                            "path": rel,
                            "size": size,
                            "binary": is_probably_binary(entry),
                        }
                    )
                else:
                    truncated = True
        return {
            "root": ".",
            "files": files,
            "dirs": dirs,
            "file_count": file_count,
            "dir_count": len(dirs),
            "top_level_only": True,
            "truncated": truncated,
        }

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and not (Path(dirpath) / d).is_symlink()
        )
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
            dirs.extend(d + "/" for d in dirnames)
        else:
            dirs.append(rel_dir.replace("\\", "/") + "/")
        for name in sorted(filenames):
            fp = Path(dirpath) / name
            if fp.is_symlink():
                continue
            file_count += 1
            rel = str(Path(rel_dir) / name).replace("\\", "/") if rel_dir else name
            if len(files) >= cap:
                truncated = True
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                size = 0
            files.append({"path": rel, "size": size, "binary": is_probably_binary(fp)})

    return {
        "root": ".",
        "files": files,
        "dirs": dirs,
        "file_count": file_count,
        "dir_count": len(dirs),
        "top_level_only": False,
        "truncated": truncated,
    }
