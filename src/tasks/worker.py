"""In-process async worker: queue, progress, elicitation, cache, warm, cleanup."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from src.audit.brief import write_brief
from src.audit.clone import CloneError, clone_repo, delete_clone
from src.audit.github import GithubError, github_resolve
from src.audit.report import markdown_report
from src.audit.scan_ci import scan_ci
from src.audit.scan_manifests import scan_manifests
from src.audit.scan_secrets import filter_secret_findings, scan_secrets
from src.audit.scan_tests import scan_tests
from src.audit.tree import walk_tree
from src.audit.url_normalize import UrlError, normalize
from src.host.config import get_settings
from src.host.examples import example_by_url, warm_order
from src.skills.router import classify
from src.store import db

log = logging.getLogger("mcp_workbench.worker")

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task | None = None
_warm_task: asyncio.Task | None = None
_warming = False
_started_at = 0.0
_input_events: dict[str, asyncio.Event] = {}


def is_warming() -> bool:
    return _warming


def started_at() -> float:
    return _started_at


async def start_worker() -> None:
    global _queue, _worker_task, _warm_task, _started_at
    _started_at = time.time()
    settings = get_settings()
    settings.clones_root().mkdir(parents=True, exist_ok=True)
    db.init_db()
    stale = db.fail_stale_runs()
    db.reset_concurrent()
    if stale:
        log.info("failed %s stale open runs", stale)
    _queue = asyncio.Queue()
    _worker_task = asyncio.create_task(_loop(), name="mcp-workbench-worker")
    if settings.warm_examples:
        _warm_task = asyncio.create_task(_warm_examples(), name="mcp-workbench-warm")


async def stop_worker() -> None:
    global _worker_task, _warm_task, _queue
    for task in (_warm_task, _worker_task):
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    _worker_task = None
    _warm_task = None
    _queue = None


def enqueue(run_id: str) -> None:
    if _queue is None:
        raise RuntimeError("worker is not running")
    _queue.put_nowait(run_id)


def submit_input(run_id: str, choice: str) -> dict:
    if choice not in {"top-level-only", "full-tree"}:
        raise ValueError("input must be 'top-level-only' or 'full-tree'.")
    run = db.get_run(run_id)
    if not run:
        raise KeyError("unknown run")
    if run["status"] != "input_required":
        raise ValueError("run is not waiting for input.")
    db.update_run(run_id, input_response={"choice": choice}, status="working", stage="walking")
    ev = _input_events.get(run_id)
    if ev:
        ev.set()
    return db.get_run(run_id)  # type: ignore[return-value]


def request_cancel(run_id: str) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise KeyError("unknown run")
    if run["status"] in {"completed", "failed", "cancelled"}:
        return run
    db.update_run(run_id, status="cancelled", stage="cancelled", progress_message="Cancelled.", completed_at=time.time())
    db.add_event(run_id, "cancelled", "Cancelled.")
    ev = _input_events.get(run_id)
    if ev:
        ev.set()
    return db.get_run(run_id)  # type: ignore[return-value]


async def _loop() -> None:
    assert _queue is not None
    while True:
        run_id = await _queue.get()
        try:
            await _execute(run_id)
        except Exception:
            log.exception("run %s crashed", run_id)
            try:
                db.update_run(
                    run_id,
                    status="failed",
                    stage="failed",
                    error="internal worker error",
                    progress_message="Failed.",
                    completed_at=time.time(),
                )
            except Exception:
                pass
        finally:
            _queue.task_done()


async def _progress(run_id: str, stage: str, message: str, *, status: str = "working") -> None:
    db.update_run(run_id, status=status, stage=stage, progress_message=message)
    db.add_event(run_id, stage, message)


def _clone_dest(run_id: str) -> Path:
    return get_settings().clones_root() / run_id


def _still_active(run_id: str) -> bool:
    run = db.get_run(run_id)
    return bool(run and run["status"] in {"queued", "working", "input_required"})


async def _execute(run_id: str) -> None:
    settings = get_settings()
    run = db.get_run(run_id)
    if not run:
        return
    ip = run.get("client_ip") or "unknown"
    is_warm = bool(run.get("is_warm"))
    dest: Path | None = None
    # Non-warm web/MCP submits reserve concurrent before enqueue; warm does not.
    reserved = not is_warm
    if run["status"] == "cancelled":
        if reserved:
            db.release_concurrent(ip)
        return
    try:
        await _progress(run_id, "resolving", "Classifying URL.")
        classified = classify(run["url"])
        if not classified.get("accepted"):
            raise UrlError(classified.get("reason") or "unsupported URL")
        repo = normalize(run["url"])
        example = example_by_url(repo.html_url)
        is_example = example is not None
        db.update_run(
            run_id,
            normalized_url=repo.html_url,
            owner=repo.owner,
            repo=repo.repo,
            branch=repo.branch,
            is_example=is_example,
        )

        await _progress(run_id, "resolving", f"Resolving {repo.owner}/{repo.repo} on GitHub.")
        resolved = await github_resolve(repo)
        sha = resolved.get("sha") or ""
        branch = resolved.get("branch") or repo.branch
        # Canonical casing from GitHub API — used for cache keys.
        cache_owner = resolved.get("owner") or repo.owner
        cache_repo = resolved.get("repo") or repo.repo
        db.update_run(run_id, sha=sha, branch=branch, owner=cache_owner, repo=cache_repo)

        cached = db.cache_get(cache_owner, cache_repo, sha) if sha else None
        # Concurrent already held for non-warm; warm never takes a concurrent slot.
        # Examples skip the daily cap on cache hit (product rule). Non-examples always pay.
        if cached:
            if not is_example:
                reason = db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False)
                if reason:
                    db.update_run(
                        run_id,
                        status="failed",
                        stage="failed",
                        error=reason,
                        progress_message=reason,
                        completed_at=time.time(),
                    )
                    db.add_event(run_id, "failed", reason)
                    return
            await _apply_cache(run_id, repo, resolved, cached, skip_note="cache hit — Claude skipped")
            return

        reason = db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False)
        if reason:
            db.update_run(
                run_id,
                status="failed",
                stage="failed",
                error=reason,
                progress_message=reason,
                completed_at=time.time(),
            )
            db.add_event(run_id, "failed", reason)
            return

        await _progress(run_id, "cloning", f"Shallow clone of {repo.owner}/{repo.repo}.")
        dest = _clone_dest(run_id)
        wall = settings.clone_wall_seconds
        if example and example.get("wall_seconds"):
            wall = int(example["wall_seconds"])
        elif is_warm:
            wall = settings.clone_wall_seconds_warm
        cloned = await clone_repo(repo, dest, branch=branch, wall_seconds=wall)
        if cloned.get("sha") and not sha:
            sha = cloned["sha"]
            db.update_run(run_id, sha=sha)

        cached = db.cache_get(cache_owner, cache_repo, sha) if sha else None
        if cached:
            # Raced into a warm/other fill after we already reserved daily.
            if is_example:
                db.refund_daily(ip)
            await _apply_cache(run_id, repo, resolved, cached, skip_note="cache hit after clone — Claude skipped")
            return

        if not _still_active(run_id):
            return

        await _progress(run_id, "walking", "Walking tree.")
        preview = await asyncio.to_thread(
            walk_tree, dest, top_level_only=False, max_files=settings.max_files + 1
        )
        file_count = int(preview.get("file_count") or 0)
        if file_count > settings.max_files:
            raise RuntimeError(f"repository has {file_count} files; cap is {settings.max_files}.")

        top_level_only = False
        if file_count > settings.elicit_file_threshold:
            choice = await _elicit(run_id, file_count)
            if choice is None:
                return
            top_level_only = choice == "top-level-only"
            await _progress(run_id, "walking", f"Walking tree ({choice}).")
            tree = await asyncio.to_thread(
                walk_tree, dest, top_level_only=top_level_only, max_files=settings.max_files
            )
        else:
            tree = preview

        if not _still_active(run_id):
            return

        files = tree.get("files") or []
        dirs = tree.get("dirs") or []

        await _progress(run_id, "scanning:manifests", "Scanning manifests.")
        await _progress(run_id, "scanning:ci", "Scanning GitHub Actions workflows.")
        await _progress(run_id, "scanning:secrets", "Scanning for secret patterns (redacted).")
        await _progress(run_id, "scanning:tests", "Scanning test layout.")
        manifests, ci, secrets, tests = await asyncio.gather(
            asyncio.to_thread(scan_manifests, dest, files),
            asyncio.to_thread(scan_ci, dest, files),
            asyncio.to_thread(scan_secrets, dest, files),
            asyncio.to_thread(scan_tests, dest, files, dirs),
        )
        secrets = filter_secret_findings(secrets)

        findings = {
            "manifests": manifests,
            "ci": ci,
            "secrets": secrets,
            "tests": tests,
        }
        payload = {
            "owner": repo.owner,
            "repo": repo.repo,
            "branch": branch,
            "sha": sha,
            "description": resolved.get("description") or "",
            "manifests": manifests,
            "ci": ci,
            "secrets": secrets,
            "tests": tests,
            "tree": tree,
        }

        await _progress(run_id, "writing", "Writing report.")
        session = run.get("session_id") or "web"
        resource_uri = f"audit://{session}/{run_id}"
        brief_obj = {"text": "", "source": "deterministic"}
        run_for_report = {
            **(db.get_run(run_id) or {}),
            "findings": findings,
            "tree": tree,
            "brief": "",
            "resource_uri": resource_uri,
            "sha": sha,
            "branch": branch,
        }
        # brief next
        await _progress(run_id, "briefing", "Writing brief.")
        brief_obj = await write_brief(payload, skip_llm=False, client_ip=ip)
        run_for_report["brief"] = brief_obj["text"]
        report_md = markdown_report(run_for_report)

        db.update_run(
            run_id,
            status="completed",
            stage="completed",
            progress_message="Done.",
            findings_json=findings,
            tree_json=_sanitize_tree(tree),
            brief=brief_obj["text"],
            brief_source=brief_obj.get("source"),
            report_md=report_md,
            resource_uri=resource_uri,
            cache_hit=False,
            completed_at=time.time(),
        )
        db.add_event(run_id, "completed", "Done.")
        if sha:
            db.cache_put(
                cache_owner, cache_repo, sha,
                findings=findings, tree=_sanitize_tree(tree),
                brief=brief_obj["text"],
                brief_source=brief_obj.get("source") or "deterministic",
                report_md=report_md,
            )
    except (UrlError, GithubError, CloneError) as exc:
        msg = str(exc)
        db.update_run(
            run_id,
            status="failed",
            stage="failed",
            error=msg,
            progress_message=msg,
            completed_at=time.time(),
        )
        db.add_event(run_id, "failed", msg)
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        log.exception("run %s failed", run_id)
        db.update_run(
            run_id,
            status="failed",
            stage="failed",
            error=msg,
            progress_message=msg,
            completed_at=time.time(),
        )
        db.add_event(run_id, "failed", msg)
    finally:
        if dest is not None:
            delete_clone(dest)
        if reserved:
            db.release_concurrent(ip)


def _sanitize_tree(tree: Any) -> Any:
    if not isinstance(tree, dict):
        return tree
    out = dict(tree)
    if "root" in out:
        out["root"] = "."
    return out


async def _apply_cache(run_id: str, repo: Any, resolved: dict, cached: dict, *, skip_note: str) -> None:
    session = (db.get_run(run_id) or {}).get("session_id") or "web"
    resource_uri = f"audit://{session}/{run_id}"
    findings = cached.get("findings")
    if isinstance(findings, dict) and "secrets" in findings:
        findings = {**findings, "secrets": filter_secret_findings(findings.get("secrets"))}
    db.update_run(
        run_id,
        status="completed",
        stage="completed",
        progress_message=f"Done ({skip_note}).",
        findings_json=findings,
        tree_json=_sanitize_tree(cached.get("tree")),
        brief=cached.get("brief"),
        brief_source=cached.get("brief_source") or "cache",
        report_md=cached.get("report_md"),
        resource_uri=resource_uri,
        cache_hit=True,
        completed_at=time.time(),
        sha=resolved.get("sha") or cached.get("sha"),
        branch=resolved.get("branch"),
        owner=repo.owner,
        repo=repo.repo,
    )
    db.add_event(run_id, "completed", f"Done ({skip_note}).")


async def _elicit(run_id: str, file_count: int) -> str | None:
    settings = get_settings()
    request = {
        "id": "scope",
        "title": "Large tree",
        "message": (
            f"This repository has {file_count} files (threshold {settings.elicit_file_threshold}). "
            "Scan top-level only, or walk the full tree?"
        ),
        "choices": [
            {"id": "top-level-only", "label": "Top-level only"},
            {"id": "full-tree", "label": "Full tree"},
        ],
    }
    ev = asyncio.Event()
    _input_events[run_id] = ev
    await _progress(
        run_id,
        "input_required",
        request["message"],
        status="input_required",
    )
    db.update_run(run_id, input_request=request, status="input_required")
    try:
        try:
            await asyncio.wait_for(ev.wait(), timeout=settings.elicit_timeout_seconds)
        except asyncio.TimeoutError:
            db.update_run(
                run_id,
                status="failed",
                stage="failed",
                error="Elicitation timed out after 5 minutes.",
                progress_message="Elicitation timed out.",
                completed_at=time.time(),
            )
            db.add_event(run_id, "failed", "Elicitation timed out after 5 minutes.")
            return None
        run = db.get_run(run_id)
        if not run or run["status"] == "cancelled":
            return None
        resp = run.get("input_response") or {}
        if isinstance(resp, dict):
            return resp.get("choice") or "full-tree"
        return "full-tree"
    finally:
        _input_events.pop(run_id, None)


async def _warm_examples() -> None:
    global _warming
    _warming = True
    try:
        for ex in warm_order():
            run_id = str(uuid.uuid4())
            log.info("warming %s", ex["url"])
            db.create_run(run_id, ex["url"], client_ip="warm", is_example=True, is_warm=True, session_id="warm")
            enqueue(run_id)
            # wait until this warm run leaves the queue/working set
            for _ in range(240):
                await asyncio.sleep(0.5)
                run = db.get_run(run_id)
                if not run or run["status"] in {"completed", "failed", "cancelled"}:
                    break
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("warm examples failed")
    finally:
        _warming = False
        log.info("warm examples finished")
