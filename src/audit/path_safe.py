"""Path jail: every read stays inside the clone root."""

from __future__ import annotations

from pathlib import Path


class PathJailError(ValueError):
    pass


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve rel against root; raise if it escapes.

    Rejects POSIX absolutes, ``..``, NUL, Windows drive letters / UNC, and
    alternate-data-stream ``:`` segments. On Windows, a segment like ``C:`` or
    ``C:/Windows`` would otherwise discard the clone root when joined.
    """
    if rel is None:
        raise PathJailError("Path is required.")
    text = str(rel)
    if "\x00" in text:
        raise PathJailError("NUL in path is not allowed.")
    text = text.replace("\\", "/")
    # Strip one leading slash only after rejecting drive / UNC forms.
    if text.startswith("//") or text.startswith("\\\\"):
        raise PathJailError("Absolute paths are not allowed.")
    text = text.lstrip("/")
    if text.startswith("~"):
        raise PathJailError("Absolute paths are not allowed.")
    # Windows drive-absolute: "C:" or "C:/..."
    if len(text) >= 2 and text[0].isalpha() and text[1] == ":":
        raise PathJailError("Absolute paths are not allowed.")
    parts = []
    for piece in text.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            raise PathJailError("Parent-directory traversal is not allowed.")
        # Block drive remnants and NTFS ADS ("file:Zone.Identifier").
        if ":" in piece:
            raise PathJailError("Invalid path segment.")
        parts.append(piece)
    root_real = Path(root).resolve()
    candidate = (root_real.joinpath(*parts) if parts else root_real).resolve()
    try:
        candidate.relative_to(root_real)
    except ValueError as exc:
        raise PathJailError("Path escapes the clone directory.") from exc
    return candidate


def is_probably_binary(path: Path, sample: bytes | None = None) -> bool:
    ext = path.suffix.lower()
    binary_ext = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
        ".pdf", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class",
        ".pyc", ".pyo", ".whl", ".egg",
        ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wav", ".flac",
        ".psd", ".ai", ".sketch",
        ".sqlite", ".db", ".wasm",
        ".dmg", ".iso", ".img",
        ".node", ".nexe",
    }
    if ext in binary_ext:
        return True
    if sample is None:
        try:
            with path.open("rb") as fh:
                sample = fh.read(8000)
        except OSError:
            return True
    if b"\x00" in sample:
        return True
    return False
