"""FastAPI host: public API, SSE, architecture, static web, MCP at /mcp."""

from __future__ import annotations

import asyncio
import re
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.host.config import get_settings
from src.host.examples import EXAMPLES, example_by_url
from src.host import llm_settings as llm_set
from src.mcp_server.fallback import TOOLS, build_fallback_router, dispatch
from src.mcp_server.server import sdk_lifespan, try_build_sdk_server
from src.skills.router import classify
from src.store import db
from src.tasks import worker
from src.audit.pdf_report import pdf_report
from src.audit.scan_secrets import filter_secret_findings

log = logging.getLogger("mcp_workbench.host")

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
WEB_PUBLIC = Path(__file__).resolve().parents[2] / "web" / "public"


def client_ip(request: Request) -> str:
    """X-Real-IP is honored only when TRUSTED_PROXY=1."""
    settings = get_settings()
    if settings.trusted_proxy:
        forwarded = request.headers.get("x-real-ip")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class RunIn(BaseModel):
    url: str = Field(..., max_length=512)


class InputIn(BaseModel):
    choice: str | None = None
    inputResponses: dict | None = None
    input_responses: dict | None = None


class PasswordIn(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class LlmSettingsIn(BaseModel):
    api_key: str | None = None
    model: str | None = None
    effort: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    db.init_db()
    await worker.start_worker()
    mcp = getattr(app.state, "mcp", None)
    if mcp is not None:
        async with sdk_lifespan(mcp):
            yield
            await worker.stop_worker()
            return
    yield
    await worker.stop_worker()


def create_app() -> FastAPI:
    app = FastAPI(
        title="MCP Workbench",
        description="Public GitHub repo audit, over MCP.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    mcp, sdk_app = try_build_sdk_server()
    app.state.mcp = mcp
    # Public /mcp is always the spec-shaped Streamable HTTP handler so
    # tasks/get|update|cancel work (SDK-incomplete). Official SDK is still
    # used to register tools/resources/prompts when importable.
    app.state.mcp_mode = "sdk+fallback-http" if mcp is not None else "fallback"
    app.state.sdk_app = sdk_app
    app.include_router(build_fallback_router(), prefix="/mcp")

    @app.get("/healthz")
    async def healthz() -> dict:
        # Must not block on warm.
        return {
            "ok": True,
            "warming": worker.is_warming(),
            "uptime_s": round(time.time() - (worker.started_at() or time.time()), 2),
            "mcp": app.state.mcp_mode,
        }

    @app.get("/api/classify")
    async def api_classify(url: str = Query("", max_length=2048)) -> dict:
        return classify(url)

    @app.get("/api/examples")
    async def api_examples() -> dict:
        out = []
        for ex in EXAMPLES:
            item = dict(ex)
            owner = ex.get("owner") or ""
            # Prefer owner/repo parsed from the example URL for cache lookup.
            repo = ""
            url = (ex.get("url") or "").rstrip("/")
            if "github.com/" in url:
                parts = url.split("github.com/", 1)[-1].split("/")
                if len(parts) >= 2:
                    owner = parts[0]
                    repo = parts[1].removesuffix(".git")
            item["cache_hot"] = bool(owner and repo and db.cache_hot_for(owner, repo))
            out.append(item)
        return {"examples": out}

    @app.post("/api/runs")
    async def api_create_run(body: RunIn, request: Request) -> dict:
        settings = get_settings()
        url = (body.url or "").strip()
        if len(url) > settings.max_url_length:
            raise HTTPException(400, f"URL exceeds {settings.max_url_length} characters.")
        classified = classify(url)
        if not classified.get("accepted"):
            raise HTTPException(400, classified.get("reason") or "unsupported URL")
        ip = client_ip(request)
        example = example_by_url(classified.get("normalized_url") or url)
        # Reserve concurrent at submit so the queue cannot be flooded before the worker runs.
        reason = db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True)
        if reason:
            raise HTTPException(429, reason)
        if example is None and db.daily_count(ip) >= settings.max_runs_per_ip_day:
            db.release_concurrent(ip)
            raise HTTPException(
                429,
                f"Daily cap reached ({settings.max_runs_per_ip_day} runs/IP/day).",
            )
        run_id = str(uuid.uuid4())
        try:
            db.create_run(
                run_id,
                url,
                client_ip=ip,
                is_example=example is not None,
                is_warm=False,
                session_id="web",
            )
            worker.enqueue(run_id)
        except Exception as exc:
            db.release_concurrent(ip)
            try:
                db.update_run(
                    run_id,
                    status="failed",
                    stage="failed",
                    error=f"worker unavailable: {exc}",
                    progress_message="Failed.",
                    completed_at=time.time(),
                )
            except Exception:
                pass
            raise HTTPException(503, f"worker unavailable: {exc}") from exc
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    async def api_get_run(run_id: str) -> dict:
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        return _public_run(run)

    @app.get("/api/runs/{run_id}/events")
    async def api_events(run_id: str, request: Request):
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")

        async def gen():
            last_id = 0
            last_emit = 0.0
            while True:
                if await request.is_disconnected():
                    break
                events = db.list_events(run_id, after_id=last_id)
                now = time.time()
                if events:
                    for ev in events:
                        last_id = ev["id"]
                        current = db.get_run(run_id) or {}
                        payload = _progress_payload(
                            run_id,
                            stage=ev["stage"],
                            message=ev["message"],
                            ts=ev["created_at"],
                            event_id=ev["id"],
                            run=current,
                        )
                        yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                        last_emit = now
                elif now - last_emit >= 0.5:
                    current = db.get_run(run_id)
                    if current:
                        payload = _progress_payload(
                            run_id,
                            stage=current.get("stage"),
                            message=current.get("progress_message"),
                            ts=now,
                            event_id=last_id,
                            run=current,
                            heartbeat=True,
                        )
                        yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                    last_emit = now
                current = db.get_run(run_id)
                if current and current["status"] in {"completed", "failed", "cancelled"}:
                    yield f"event: done\ndata: {json.dumps({'status': current['status']})}\n\n"
                    break
                await asyncio.sleep(0.4)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/input")
    async def api_input(run_id: str, body: InputIn) -> dict:
        choice = body.choice
        if not choice and body.inputResponses:
            choice = _choice_from(body.inputResponses)
        if not choice and body.input_responses:
            choice = _choice_from(body.input_responses)
        if choice not in {"top-level-only", "full-tree"}:
            raise HTTPException(400, "choice must be 'top-level-only' or 'full-tree'.")
        try:
            run = worker.submit_input(run_id, choice)
        except KeyError:
            raise HTTPException(404, "unknown run")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _public_run(run)

    @app.get("/api/runs/{run_id}/report")
    async def api_report(run_id: str):
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        if run.get("status") != "completed":
            raise HTTPException(409, "report not ready")
        md = run.get("report_md") or ""
        # Bound response size (reports are scanner summaries; multi-MB is unexpected).
        if len(md) > 2_000_000:
            md = md[:2_000_000] + "\n\n…[truncated]\n"
        owner = re.sub(r"[^A-Za-z0-9._-]+", "_", (run.get("owner") or "repo"))[:64] or "repo"
        repo = re.sub(r"[^A-Za-z0-9._-]+", "_", (run.get("repo") or "audit"))[:64] or "audit"
        filename = f"{owner}-{repo}.md"
        return PlainTextResponse(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/runs/{run_id}/report.pdf")
    async def api_report_pdf(run_id: str):
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        if run.get("status") != "completed":
            raise HTTPException(409, "report not ready")
        findings = run.get("findings") or {}
        if isinstance(findings, dict) and "secrets" in findings:
            findings = {**findings, "secrets": filter_secret_findings(findings.get("secrets"))}
        payload = {**run, "findings": findings}
        try:
            data = pdf_report(payload)
        except Exception as exc:
            log.exception("pdf_report failed")
            raise HTTPException(500, f"pdf generation failed: {exc}") from exc
        if len(data) > 1_500_000:
            raise HTTPException(500, "pdf too large")
        owner = re.sub(r"[^A-Za-z0-9._-]+", "_", (run.get("owner") or "repo"))[:64] or "repo"
        repo = re.sub(r"[^A-Za-z0-9._-]+", "_", (run.get("repo") or "audit"))[:64] or "audit"
        filename = f"{owner}-{repo}-audit.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Length": str(len(data)),
            },
        )

    @app.get("/api/architecture")
    async def api_architecture() -> dict:
        tool_names = [t["name"] for t in TOOLS]
        return {
            "title": "MCP Workbench",
            "deck": "Public GitHub repo audit, over MCP.",
            "mcp_endpoint": "/mcp",
            "protocol": "2026-07-28 Streamable HTTP",
            "mcp_mode": app.state.mcp_mode,
            "tasks": "spec-shaped, SDK-incomplete",
            "tools": tool_names,
            "tool_count": len(tool_names),
            "nodes": [
                {"id": "browser", "label": "Browser"},
                {"id": "host", "label": "FastAPI host (1 worker)"},
                {"id": "mcp", "label": "/mcp Streamable HTTP"},
                {"id": "task", "label": "Task worker"},
                {"id": "git", "label": "git clone (no execute)"},
                {"id": "scan", "label": "static scanners"},
                {"id": "sqlite", "label": "SQLite WAL"},
                {"id": "res", "label": "audit:// tree:// findings://"},
            ],
            "edges": [
                ["browser", "host"],
                ["host", "mcp"],
                ["host", "task"],
                ["mcp", "task"],
                ["task", "git"],
                ["task", "scan"],
                ["task", "sqlite"],
                ["sqlite", "res"],
            ],
            "stages": [
                "queued", "resolving", "cloning", "walking", "input_required",
                "scanning:manifests", "scanning:ci", "scanning:secrets",
                "scanning:tests", "writing", "briefing",
            ],
            "inspector": db.recent_events(20),
        }


    def _settings_authed(request: Request) -> bool:
        token = request.cookies.get(llm_set.COOKIE_NAME)
        return llm_set.verify_session_token(token)

    def _set_admin_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            key=llm_set.COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=llm_set.SESSION_TTL_S,
            path="/",
        )

    def _clear_admin_cookie(response: Response) -> None:
        response.delete_cookie(key=llm_set.COOKIE_NAME, path="/")

    @app.get("/api/settings/status")
    async def settings_status(request: Request) -> dict:
        llm = llm_set.public_llm_status()
        return {
            "password_configured": llm_set.password_configured(),
            "authenticated": _settings_authed(request),
            "llm_configured": llm["llm_configured"],
            "model": llm["model"],
            "effort": llm["effort"],
            "key_suffix": llm["key_suffix"],
        }

    @app.post("/api/settings/setup")
    async def settings_setup(body: PasswordIn, response: Response) -> dict:
        if llm_set.password_configured():
            raise HTTPException(400, "Admin password already configured.")
        try:
            llm_set.save_admin_password(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = llm_set.create_session_token()
        _set_admin_cookie(response, token)
        return {"ok": True, "authenticated": True}

    @app.post("/api/settings/login")
    async def settings_login(body: PasswordIn, response: Response) -> dict:
        if not llm_set.password_configured():
            raise HTTPException(400, "Admin password not configured yet.")
        if not llm_set.verify_password(body.password):
            raise HTTPException(401, "Wrong password.")
        token = llm_set.create_session_token()
        _set_admin_cookie(response, token)
        return {"ok": True, "authenticated": True}

    @app.post("/api/settings/logout")
    async def settings_logout(response: Response) -> dict:
        _clear_admin_cookie(response)
        return {"ok": True}

    @app.get("/api/settings/llm")
    async def settings_get_llm(request: Request) -> dict:
        if not _settings_authed(request):
            raise HTTPException(401, "Authentication required.")
        return llm_set.public_llm_status()

    @app.put("/api/settings/llm")
    async def settings_put_llm(body: LlmSettingsIn, request: Request) -> dict:
        if not _settings_authed(request):
            raise HTTPException(401, "Authentication required.")
        try:
            llm_set.save_llm_settings(
                api_key=body.api_key,
                model=body.model,
                effort=body.effort,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return llm_set.public_llm_status()

    @app.post("/mcp/tasks")
    async def mcp_tasks_bridge(request: Request) -> JSONResponse:
        """Escape hatch if a client POSTs Tasks JSON-RPC beside a mounted SDK app."""
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        return JSONResponse(await dispatch(payload))

    if WEB_DIST.is_dir():
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        examples_dir = WEB_DIST / "examples"
        if examples_dir.is_dir():
            app.mount("/examples", StaticFiles(directory=examples_dir), name="examples")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

        @app.get("/{path:path}")
        async def spa(path: str):
            if path.startswith(("api/", "mcp", "healthz")):
                raise HTTPException(404, "not found")
            root = WEB_DIST.resolve()
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                raise HTTPException(404, "not found")
            if candidate.is_file():
                return FileResponse(candidate)
            index = root / "index.html"
            if index.is_file():
                return FileResponse(index)
            raise HTTPException(404, "not found")
    elif WEB_PUBLIC.is_dir():
        # Dev: still serve the three local example images.
        examples_dir = WEB_PUBLIC / "examples"
        if examples_dir.is_dir():
            app.mount("/examples", StaticFiles(directory=examples_dir), name="examples")

    return app


def _choice_from(payload: dict) -> str | None:
    for value in payload.values():
        if isinstance(value, dict):
            return value.get("choice") or value.get("id") or value.get("value")
        if isinstance(value, str):
            return value
    return None


def _public_tree(tree: Any) -> Any:
    """Drop absolute clone paths from stored trees (incl. pre-fix cache rows)."""
    if not isinstance(tree, dict):
        return tree
    out = dict(tree)
    if "root" in out:
        out["root"] = "."
    return out


def _derive_stage(run: dict[str, Any]) -> str | None:
    stage = run.get("stage")
    if stage:
        return stage
    status = run.get("status")
    if status in {"queued", "completed", "failed", "cancelled", "input_required", "working"}:
        return status
    return None


def _progress_from_run(run: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    created = run.get("created_at")
    ts = now if now is not None else time.time()
    elapsed_ms = None
    if isinstance(created, (int, float)):
        elapsed_ms = max(0, int((ts - created) * 1000))
    stage = _derive_stage(run)
    detail = run.get("progress_message")
    # Optional counters if worker ever stores them on the run row.
    return {
        "stage": stage,
        "detail": detail,
        "elapsed_ms": elapsed_ms,
        "files_seen": run.get("files_seen"),
        "files_total": run.get("files_total"),
        "bytes_seen": run.get("bytes_seen"),
    }


def _progress_payload(
    run_id: str,
    *,
    stage: str | None,
    message: str | None,
    ts: float,
    event_id: int,
    run: dict[str, Any],
    heartbeat: bool = False,
) -> dict[str, Any]:
    prog = _progress_from_run(run, now=ts)
    if stage:
        prog["stage"] = stage
    if message is not None:
        prog["detail"] = message
    payload: dict[str, Any] = {
        "id": event_id,
        "stage": prog.get("stage") or stage,
        "message": message,
        "detail": prog.get("detail"),
        "ts": ts,
        "elapsed_ms": prog.get("elapsed_ms"),
        "files_seen": prog.get("files_seen"),
        "files_total": prog.get("files_total"),
        "bytes_seen": prog.get("bytes_seen"),
        "progress": prog,
    }
    if heartbeat:
        payload["heartbeat"] = True
    return payload



def _public_findings(findings: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip noisy/vendor secret hits from cached findings on read."""
    if not findings or not isinstance(findings, dict):
        return findings
    out = dict(findings)
    if "secrets" in out:
        out["secrets"] = filter_secret_findings(out.get("secrets"))
    return out


def _public_run(run: dict[str, Any]) -> dict:
    status = run.get("status")
    stage = _derive_stage(run)
    # Ensure completed runs always expose a copyable audit:// URI.
    resource_uri = run.get("resource_uri")
    if not resource_uri and status == "completed" and run.get("id"):
        session = run.get("session_id") or "web"
        resource_uri = f"audit://{session}/{run['id']}"
    progress = _progress_from_run(run)
    return {
        "id": run.get("id"),
        "url": run.get("url"),
        "normalized_url": run.get("normalized_url"),
        "owner": run.get("owner"),
        "repo": run.get("repo"),
        "branch": run.get("branch"),
        "sha": run.get("sha"),
        "status": status,
        "stage": stage,
        "progress_message": run.get("progress_message"),
        "progress": progress,
        "error": run.get("error"),
        "cache_hit": run.get("cache_hit"),
        "is_example": run.get("is_example"),
        "findings": _public_findings(run.get("findings")),
        "tree": _public_tree(run.get("tree")),
        "brief": run.get("brief"),
        "brief_source": run.get("brief_source"),
        "resource_uri": resource_uri,
        "input_request": run.get("input_request"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "completed_at": run.get("completed_at"),
    }


app = create_app()
