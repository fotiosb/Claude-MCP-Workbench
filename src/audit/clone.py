"""Shallow clone. Never execute anything inside the clone."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from src.audit.url_normalize import NormalizedRepo
from src.host.config import get_settings

log = logging.getLogger("mcp_workbench.clone")

GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_ASKPASS": "echo",
    "GCM_INTERACTIVE": "never",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class CloneError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    env.pop("GIT_ASKPASS_REQUIRE", None)
    return env


async def _run_git(args: list[str], *, cwd: Path | None, timeout: int) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        env=_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise CloneError(f"git timed out after {timeout}s.")
    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, out, err


def dir_bytes(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        # do not follow symlinks out of the tree
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                if fp.is_symlink():
                    continue
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def delete_clone(path: Path | None) -> None:
    if not path:
        return
    try:
        if not path.exists():
            return
        # Windows: .git objects are often read-only; clear that before rmtree.
        import stat

        for dirpath, dirnames, filenames in os.walk(path):
            for name in filenames + dirnames:
                fp = Path(dirpath) / name
                try:
                    os.chmod(fp, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                except OSError:
                    pass
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except OSError:
            pass
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        log.warning("failed to delete clone %s: %s", path, exc)


async def clone_repo(
    repo: NormalizedRepo,
    dest: Path,
    *,
    branch: str | None = None,
    wall_seconds: int | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Shallow clone --depth 1, no submodules, no LFS smudge, no prompts."""
    settings = get_settings()
    wall = wall_seconds if wall_seconds is not None else settings.clone_wall_seconds
    cap = max_bytes if max_bytes is not None else settings.max_clone_bytes
    dest = Path(dest)
    if dest.exists():
        delete_clone(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "git",
        "clone",
        "--depth", "1",
        "--single-branch",
        "--no-recurse-submodules",
        "--quiet",
    ]
    use_branch = branch if branch is not None else repo.branch
    if use_branch:
        args.extend(["--branch", use_branch])
    args.extend([repo.clone_url, str(dest)])

    code, _out, err = await _run_git(args, cwd=None, timeout=wall)
    if code != 0:
        delete_clone(dest)
        snippet = (err or "").strip().splitlines()
        tail = snippet[-1] if snippet else "git clone failed"
        if "not found" in tail.lower() or "repository not found" in (err or "").lower():
            raise CloneError("private or missing repo")
        if "Authentication failed" in (err or "") or "could not read Username" in (err or ""):
            raise CloneError("private or missing repo")
        raise CloneError(f"clone failed: {tail}")

    size = await asyncio.to_thread(dir_bytes, dest)
    if size > cap:
        delete_clone(dest)
        raise CloneError(f"clone exceeds MAX_CLONE_BYTES ({cap} bytes); got {size} bytes.")

    sha = ""
    try:
        code, out, _ = await _run_git(["git", "rev-parse", "HEAD"], cwd=dest, timeout=10)
        if code == 0:
            sha = out.strip()
    except CloneError:
        sha = ""

    return {"path": str(dest), "bytes": size, "sha": sha, "branch": use_branch}
