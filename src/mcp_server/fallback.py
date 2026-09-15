"""Streamable HTTP JSON-RPC fallback + SEP-2663-shaped Tasks.

Used when the official SDK is missing or when we need to serve
tasks/get, tasks/update, tasks/cancel ourselves (SDK-incomplete).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Awaitable

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from src.audit.github import github_resolve
from src.audit.path_safe import PathJailError, resolve_inside
from src.audit.url_normalize import UrlError, normalize
from src.host.config import get_settings
from src.host.examples import EXAMPLES, example_by_url
from src.skills.router import classify
from src.store import db
from src.tasks import worker

log = logging.getLogger("mcp_workbench.mcp")

PROTOCOL_VERSION = "2026-07-28"

TOOLS = [
    {
        "name": "classify_url",
        "description": "Classify a URL. github_repo maps to the repo-audit skill; everything else is unsupported.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "github_resolve",
        "description": "Unauthenticated GitHub resolve. 404 is reported as private or missing repo.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "repo_clone",
        "description": "Shallow-clone a public GitHub repo under CLONE_DIR. Never executes the clone.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "repo_tree",
        "description": "Return the stored tree for a run (after start_repo_audit).",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "repo_read_file",
        "description": "Read a text file from a completed run's stored tree metadata only is not enough — files are not kept after scan. Prefer findings/report resources.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "path": {"type": "string"}}, "required": ["run_id", "path"]},
    },
    {
        "name": "scan_manifests",
        "description": "Return stored manifest findings for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "scan_ci",
        "description": "Return stored CI findings for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "scan_secrets",
        "description": "Return stored redacted secret findings for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "scan_tests",
        "description": "Return stored test-layout findings for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "start_repo_audit",
        "description": "Start a repo-audit Task (SEP-2663-shaped). Returns a task handle; poll tasks/get.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "write_audit_report",
        "description": "Return the markdown report for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "get_run",
        "description": "Return run status, stage, findings summary, and resource URIs.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "list_run_resources",
        "description": "List audit://, tree://, findings:// and ui:// URIs for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
]


def _skill_md() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "skills" / "repo_audit" / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "# repo-audit\n"


def _skill_index() -> str:
    return json.dumps(
        {
            "skills": [
                {
                    "id": "repo-audit",
                    "name": "repo-audit",
                    "description": "Public GitHub repository audit: classify, shallow clone, static scan.",
                    "uri": "skill://repo-audit/SKILL.md",
                }
            ]
        },
        indent=2,
    )


def _run_resources(run_id: str, session: str = "web") -> list[dict]:
    return [
        {"uri": f"audit://{session}/{run_id}", "name": "audit report", "mimeType": "text/markdown"},
        {"uri": f"tree://{session}/{run_id}", "name": "repository tree", "mimeType": "application/json"},
        {"uri": f"findings://{session}/{run_id}", "name": "findings", "mimeType": "application/json"},
        {"uri": f"ui://audit-tree/{run_id}", "name": "host widget (tree)", "mimeType": "application/json"},
    ]


def _text_result(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _json_result(obj: Any) -> dict:
    return _text_result(json.dumps(obj, indent=2, default=str))


async def call_tool(name: str, arguments: dict[str, Any]) -> dict:
    args = arguments or {}
    if name == "classify_url":
        return _json_result(classify(args.get("url") or ""))
    if name == "github_resolve":
        try:
            repo = normalize(args.get("url") or "")
            resolved = await github_resolve(repo)
            return _json_result(resolved)
        except UrlError as exc:
            return _text_result(str(exc), is_error=True)
        except Exception as exc:
            return _text_result(str(exc), is_error=True)
    if name == "start_repo_audit":
        url = args.get("url") or ""
        classified = classify(url)
        if not classified.get("accepted"):
            return _text_result(classified.get("reason") or "unsupported", is_error=True)
        settings = get_settings()
        ip = "mcp"
        example = example_by_url(classified.get("normalized_url") or url)
        reason = db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True)
        if reason:
            return _text_result(reason, is_error=True)
        if example is None and db.daily_count(ip) >= settings.max_runs_per_ip_day:
            db.release_concurrent(ip)
            return _text_result(
                f"Daily cap reached ({settings.max_runs_per_ip_day} runs/IP/day).",
                is_error=True,
            )
        run_id = str(uuid.uuid4())
        try:
            db.create_run(
                run_id,
                url,
                client_ip=ip,
                is_example=example is not None,
                session_id="mcp",
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
                )
            except Exception:
                pass
            return _text_result(f"worker unavailable: {exc}", is_error=True)
        # SEP-2663-shaped CreateTaskResult
        return {
            "resultType": "task",
            "taskId": run_id,
            "status": "working",
            "ttlMs": 24 * 3600 * 1000,
            "pollIntervalMs": 500,
            "content": [{"type": "text", "text": json.dumps({"taskId": run_id, "status": "working"})}],
        }
    if name in {"repo_tree", "scan_manifests", "scan_ci", "scan_secrets", "scan_tests", "write_audit_report", "get_run", "list_run_resources", "repo_read_file", "repo_clone"}:
        run_id = args.get("run_id")
        if name == "repo_clone":
            # Cloning is performed by the Task worker. Direct clone is refused to
            # keep a single cleanup path.
            return _text_result(
                "repo_clone is performed by start_repo_audit (Task). "
                "Call start_repo_audit, then poll tasks/get / get_run.",
                is_error=True,
            )
        if not run_id:
            return _text_result("run_id is required.", is_error=True)
        run = db.get_run(run_id)
        if not run:
            return _text_result("unknown run", is_error=True)
        if name == "get_run":
            slim = {k: run.get(k) for k in (
                "id", "url", "normalized_url", "owner", "repo", "branch", "sha",
                "status", "stage", "progress_message", "error", "cache_hit",
                "brief", "brief_source", "resource_uri",
            )}
            return _json_result(slim)
        if name == "repo_tree":
            tree = dict(run.get("tree") or {})
            if "root" in tree:
                tree["root"] = "."
            return _json_result(tree)
        if name == "scan_manifests":
            return _json_result((run.get("findings") or {}).get("manifests") or [])
        if name == "scan_ci":
            return _json_result((run.get("findings") or {}).get("ci") or [])
        if name == "scan_secrets":
            return _json_result((run.get("findings") or {}).get("secrets") or [])
        if name == "scan_tests":
            return _json_result((run.get("findings") or {}).get("tests") or {})
        if name == "write_audit_report":
            return _text_result(run.get("report_md") or "report not ready")
        if name == "list_run_resources":
            session = run.get("session_id") or "web"
            return _json_result(_run_resources(run_id, session))
        if name == "repo_read_file":
            # Clones are deleted after scan. Point the client at stored tree entries.
            path = args.get("path") or ""
            tree = run.get("tree") or {}
            for item in tree.get("files") or []:
                if item.get("path") == path:
                    return _json_result({"path": path, "size": item.get("size"), "binary": item.get("binary"), "note": "file body is not retained after scan; clone is deleted."})
            return _text_result("path not in stored tree (clone is deleted after scan).", is_error=True)
    return _text_result(f"unknown tool: {name}", is_error=True)


def _parse_resource_uri(uri: str) -> tuple[str, str | None, str | None]:
    """Return (kind, session, run_id) or skill/ui variants."""
    if uri == "skill://index.json":
        return "skill-index", None, None
    if uri == "skill://repo-audit/SKILL.md":
        return "skill-md", None, None
    if uri.startswith("ui://audit-tree/"):
        return "ui-tree", None, uri.rsplit("/", 1)[-1]
    for prefix, kind in (("audit://", "audit"), ("tree://", "tree"), ("findings://", "findings")):
        if uri.startswith(prefix):
            rest = uri[len(prefix):]
            parts = rest.split("/", 1)
            if len(parts) == 2:
                return kind, parts[0], parts[1]
            return kind, "web", parts[0]
    return "unknown", None, None


async def read_resource(uri: str) -> dict:
    kind, session, run_id = _parse_resource_uri(uri)
    if kind == "skill-index":
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": _skill_index()}]}
    if kind == "skill-md":
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": _skill_md()}]}
    if kind == "unknown" or not run_id:
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "unknown resource"}]}
    run = db.get_run(run_id)
    if not run:
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "unknown run"}]}
    if kind == "audit":
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": run.get("report_md") or ""}]}
    if kind == "tree":
        tree = dict(run.get("tree") or {})
        if "root" in tree:
            tree["root"] = "."
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(tree, default=str)}]}
    if kind == "findings":
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(run.get("findings") or {}, default=str)}]}
    if kind == "ui-tree":
        tree = dict(run.get("tree") or {})
        if "root" in tree:
            tree["root"] = "."
        widget = {
            "type": "host-widget",
            "title": "Audit tree",
            "note": "Not an MCP Apps iframe. Host widget + UI resource only.",
            "run_id": run_id,
            "tree": tree,
        }
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(widget, default=str)}]}
    return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": ""}]}


def list_resources() -> list[dict]:
    items = [
        {"uri": "skill://index.json", "name": "skill index", "mimeType": "application/json"},
        {"uri": "skill://repo-audit/SKILL.md", "name": "repo-audit skill", "mimeType": "text/markdown"},
    ]
    # include a few recent completed runs so resources/list is not empty after use
    for ev in db.recent_events(20):
        rid = ev.get("run_id")
        if not rid:
            continue
        run = db.get_run(rid)
        if run and run.get("resource_uri"):
            session = run.get("session_id") or "web"
            for r in _run_resources(rid, session):
                if r not in items:
                    items.append(r)
            break
    return items


def list_resource_templates() -> list[dict]:
    return [
        {"uriTemplate": "audit://{session}/{run}", "name": "audit report", "mimeType": "text/markdown"},
        {"uriTemplate": "tree://{session}/{run}", "name": "repository tree", "mimeType": "application/json"},
        {"uriTemplate": "findings://{session}/{run}", "name": "findings", "mimeType": "application/json"},
        {"uriTemplate": "ui://audit-tree/{run}", "name": "host widget (tree)", "mimeType": "application/json"},
    ]


def repo_audit_prompt(url: str = "") -> dict:
    text = (
        "Audit a public GitHub repository with the repo-audit skill.\n"
        "1. classify_url\n"
        "2. start_repo_audit (Task) and poll tasks/get\n"
        "3. If status is input_required, present the elicitation and tasks/update\n"
        "4. Read findings:// and audit:// resources\n"
        "Do not execute cloned code. Do not invent CVEs or secrets.\n"
    )
    if url:
        text += f"\nTarget URL: {url}\n"
    return {
        "description": "Public GitHub repo audit over MCP.",
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


def task_get(task_id: str) -> dict:
    run = db.get_run(task_id)
    if not run:
        return {"error": {"code": -32602, "message": "unknown taskId"}}
    status = run["status"]
    if status == "queued":
        status = "working"
    body: dict[str, Any] = {
        "taskId": task_id,
        "status": status,
        "ttlMs": 24 * 3600 * 1000,
        "pollIntervalMs": 500,
        "statusMessage": run.get("progress_message") or run.get("stage") or "",
    }
    if status == "input_required" and run.get("input_request"):
        req = run["input_request"]
        body["inputRequests"] = {req.get("id") or "scope": req}
    if status == "completed":
        body["result"] = {
            "content": [{"type": "text", "text": json.dumps({
                "run_id": task_id,
                "resource_uri": run.get("resource_uri"),
                "brief": run.get("brief"),
                "cache_hit": run.get("cache_hit"),
            }, default=str)}],
            "isError": False,
        }
    if status == "failed":
        body["error"] = {"code": -32000, "message": run.get("error") or "failed"}
    return {"result": body}


def task_update(task_id: str, input_responses: dict | None) -> dict:
    run = db.get_run(task_id)
    if not run:
        return {"error": {"code": -32602, "message": "unknown taskId"}}
    responses = input_responses or {}
    choice = None
    if isinstance(responses, dict):
        for value in responses.values():
            if isinstance(value, dict):
                choice = value.get("choice") or value.get("id") or value.get("value")
            elif isinstance(value, str):
                choice = value
            if choice:
                break
    if choice not in {"top-level-only", "full-tree"}:
        return {"error": {"code": -32602, "message": "inputResponses must include choice 'top-level-only' or 'full-tree'."}}
    try:
        worker.submit_input(task_id, choice)
    except Exception as exc:
        return {"error": {"code": -32602, "message": str(exc)}}
    return {"result": {}}


def task_cancel(task_id: str) -> dict:
    try:
        worker.request_cancel(task_id)
    except KeyError:
        return {"error": {"code": -32602, "message": "unknown taskId"}}
    return {"result": {}}


async def dispatch(message: dict) -> dict:
    mid = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, msg: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {"subscribe": False},
                "prompts": {},
                "extensions": {"io.modelcontextprotocol/tasks": {}},
            },
            "serverInfo": {"name": get_settings().mcp_server_name, "version": "1.0.0"},
            "instructions": (
                "Public GitHub repo audit. Use classify_url then start_repo_audit. "
                "Tasks: spec-shaped, SDK-incomplete — poll tasks/get."
            ),
        })
    if method == "notifications/initialized":
        # Notifications must not receive a JSON-RPC response body.
        return {"jsonrpc": "2.0", "id": mid, "_notification": True} if mid is not None else {"_notification": True}
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        result = await call_tool(name, arguments)
        return ok(result)
    if method == "resources/list":
        return ok({"resources": list_resources()})
    if method == "resources/templates/list":
        return ok({"resourceTemplates": list_resource_templates()})
    if method == "resources/read":
        return ok(await read_resource(params.get("uri") or ""))
    if method == "prompts/list":
        return ok({"prompts": [{"name": "repo-audit", "description": "Public GitHub repo audit over MCP.", "arguments": [{"name": "url", "required": False}]}]})
    if method == "prompts/get":
        return ok(repo_audit_prompt((params.get("arguments") or {}).get("url") or params.get("url") or ""))
    if method == "tasks/get":
        payload = task_get(params.get("taskId") or params.get("task_id") or "")
        if "error" in payload:
            return {"jsonrpc": "2.0", "id": mid, **payload}
        return ok(payload["result"])
    if method == "tasks/update":
        payload = task_update(params.get("taskId") or "", params.get("inputResponses") or params.get("input_responses"))
        if "error" in payload:
            return {"jsonrpc": "2.0", "id": mid, **payload}
        return ok(payload["result"])
    if method == "tasks/cancel":
        payload = task_cancel(params.get("taskId") or "")
        if "error" in payload:
            return {"jsonrpc": "2.0", "id": mid, **payload}
        return ok(payload["result"])
    if method == "tasks/result":
        return err(-32601, "Method not found (tasks/result removed in SEP-2663)")
    if method == "skills/list":
        return err(-32601, "skills/list is not required; use skill://index.json and the repo-audit prompt")
    return err(-32601, f"Method not found: {method}")


def build_fallback_router() -> APIRouter:
    router = APIRouter()

    @router.api_route("", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    @router.api_route("/", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        if request.method == "GET":
            async def ping():
                yield "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"ping\"}\n\n"
            return StreamingResponse(ping(), media_type="text/event-stream")
        if request.method == "DELETE":
            return Response(status_code=204)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
        if isinstance(payload, list):
            results = []
            for msg in payload:
                result = await dispatch(msg)
                if isinstance(result, dict) and result.get("_notification") and msg.get("id") is None:
                    continue
                if isinstance(result, dict):
                    result.pop("_notification", None)
                results.append(result)
            if not results:
                return Response(status_code=202)
            return JSONResponse(results)
        result = await dispatch(payload)
        if isinstance(result, dict) and result.get("_notification") and payload.get("id") is None:
            return Response(status_code=202)
        if isinstance(result, dict):
            result.pop("_notification", None)
        headers = {}
        if (payload.get("method") == "initialize"):
            headers["Mcp-Session-Id"] = str(uuid.uuid4())
        return JSONResponse(result, headers=headers)

    return router
