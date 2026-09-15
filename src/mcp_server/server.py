"""Official MCP SDK server when available; Streamable HTTP mounted at /mcp.

Tasks (SEP-2663) are implemented in fallback.py because the SDK is incomplete:
README note: "Tasks: spec-shaped, SDK-incomplete".
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from src.host.config import get_settings
from src.mcp_server.fallback import (
    call_tool,
    read_resource,
    repo_audit_prompt,
    _skill_index,
    _skill_md,
)

log = logging.getLogger("mcp_workbench.mcp")


def try_build_sdk_server() -> tuple[Any, Any] | tuple[None, None]:
    """Return (mcp, streamable_http_app) or (None, None)."""
    MCPServer = None
    try:
        from mcp.server import MCPServer as _S
        MCPServer = _S
    except Exception:
        try:
            from mcp.server.fastmcp import FastMCP as _S
            MCPServer = _S
        except Exception:
            log.info("official mcp SDK not importable; using fallback Streamable HTTP")
            return None, None

    try:
        mcp = MCPServer(get_settings().mcp_server_name)
    except Exception as exc:
        log.warning("MCPServer init failed: %s", exc)
        return None, None

    try:
        @mcp.tool()
        async def classify_url(url: str) -> str:
            """Classify a URL. github_repo maps to repo-audit."""
            result = await call_tool("classify_url", {"url": url})
            return result["content"][0]["text"]

        @mcp.tool()
        async def github_resolve(url: str) -> str:
            """Unauthenticated GitHub resolve. 404 = private or missing repo."""
            result = await call_tool("github_resolve", {"url": url})
            return result["content"][0]["text"]

        @mcp.tool()
        async def repo_clone(url: str, run_id: str = "") -> str:
            """Shallow clone is performed by start_repo_audit (single cleanup path)."""
            result = await call_tool("repo_clone", {"url": url, "run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def repo_tree(run_id: str) -> str:
            """Stored tree for a run."""
            result = await call_tool("repo_tree", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def repo_read_file(run_id: str, path: str) -> str:
            """Metadata for a path in the stored tree. Clone is deleted after scan."""
            result = await call_tool("repo_read_file", {"run_id": run_id, "path": path})
            return result["content"][0]["text"]

        @mcp.tool()
        async def scan_manifests(run_id: str) -> str:
            """Stored manifest findings."""
            result = await call_tool("scan_manifests", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def scan_ci(run_id: str) -> str:
            """Stored CI findings."""
            result = await call_tool("scan_ci", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def scan_secrets(run_id: str) -> str:
            """Stored redacted secret findings."""
            result = await call_tool("scan_secrets", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def scan_tests(run_id: str) -> str:
            """Stored test-layout findings."""
            result = await call_tool("scan_tests", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def start_repo_audit(url: str) -> dict:
            """Start a repo-audit Task. Poll tasks/get (SEP-2663-shaped)."""
            return await call_tool("start_repo_audit", {"url": url})

        @mcp.tool()
        async def write_audit_report(run_id: str) -> str:
            """Markdown report for a run."""
            result = await call_tool("write_audit_report", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def get_run(run_id: str) -> str:
            """Run status and resource URIs."""
            result = await call_tool("get_run", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.tool()
        async def list_run_resources(run_id: str) -> str:
            """audit:// tree:// findings:// ui:// URIs for a run."""
            result = await call_tool("list_run_resources", {"run_id": run_id})
            return result["content"][0]["text"]

        @mcp.resource("skill://index.json")
        def skill_index() -> str:
            """Skill pack index."""
            return _skill_index()

        @mcp.resource("skill://repo-audit/SKILL.md")
        def skill_md() -> str:
            """repo-audit skill document."""
            return _skill_md()

        @mcp.resource("audit://{session}/{run}")
        async def audit_res(session: str, run: str) -> str:
            """Markdown audit report."""
            data = await read_resource(f"audit://{session}/{run}")
            return data["contents"][0]["text"]

        @mcp.resource("tree://{session}/{run}")
        async def tree_res(session: str, run: str) -> str:
            """JSON tree."""
            data = await read_resource(f"tree://{session}/{run}")
            return data["contents"][0]["text"]

        @mcp.resource("findings://{session}/{run}")
        async def findings_res(session: str, run: str) -> str:
            """JSON findings."""
            data = await read_resource(f"findings://{session}/{run}")
            return data["contents"][0]["text"]

        @mcp.resource("ui://audit-tree/{run}")
        async def ui_tree(run: str) -> str:
            """Host widget + UI resource (not an MCP Apps iframe)."""
            data = await read_resource(f"ui://audit-tree/{run}")
            return data["contents"][0]["text"]

        @mcp.prompt()
        def repo_audit(url: str = "") -> str:
            """Public GitHub repo audit over MCP."""
            return repo_audit_prompt(url)["messages"][0]["content"]["text"]
    except Exception:
        log.exception("failed to register MCP tools/resources/prompts")
        return None, None

    app = None
    try:
        if hasattr(mcp, "streamable_http_app"):
            try:
                app = mcp.streamable_http_app(streamable_http_path="/")
            except TypeError:
                app = mcp.streamable_http_app()
    except Exception:
        log.exception("streamable_http_app failed")
        app = None
    return mcp, app


@asynccontextmanager
async def sdk_lifespan(mcp: Any) -> AsyncIterator[None]:
    mgr = getattr(mcp, "session_manager", None)
    if mgr is None:
        yield
        return
    runner = getattr(mgr, "run", None)
    if runner is None:
        yield
        return
    try:
        async with runner():
            yield
    except TypeError:
        # some SDK builds expose a context manager, not a factory
        async with runner:
            yield
