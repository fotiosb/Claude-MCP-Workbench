"""SQLite WAL store: runs, cache, rate_limits, events. Artifact TTL GC."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.host.config import get_settings

log = logging.getLogger("mcp_workbench.store")

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    normalized_url TEXT,
    owner TEXT,
    repo TEXT,
    branch TEXT,
    sha TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    progress_message TEXT,
    client_ip TEXT,
    is_example INTEGER DEFAULT 0,
    is_warm INTEGER DEFAULT 0,
    cache_hit INTEGER DEFAULT 0,
    error TEXT,
    findings_json TEXT,
    tree_json TEXT,
    brief TEXT,
    brief_source TEXT,
    report_md TEXT,
    input_request TEXT,
    input_response TEXT,
    resource_uri TEXT,
    session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    owner TEXT,
    repo TEXT,
    sha TEXT,
    findings_json TEXT,
    tree_json TEXT,
    brief TEXT,
    brief_source TEXT,
    report_md TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    ip TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    concurrent INTEGER DEFAULT 0,
    llm_count INTEGER DEFAULT 0,
    PRIMARY KEY (ip, day)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    stage TEXT,
    message TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at);
"""


def _db_path() -> Path:
    path = get_settings().database_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    global _initialized
    path = _db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if not _initialized:
        with _init_lock:
            if not _initialized:
                conn.executescript(SCHEMA)
                _ensure_columns(conn)
                conn.commit()
                _initialized = True
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def init_db() -> Path:
    global _initialized
    _initialized = False
    conn = connect()
    conn.close()
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
    return _db_path()


def _now() -> float:
    return time.time()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first schema (existing DBs)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rate_limits)").fetchall()}
    if "llm_count" not in cols:
        conn.execute("ALTER TABLE rate_limits ADD COLUMN llm_count INTEGER DEFAULT 0")


def _row_to_run(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("findings_json", "tree_json"):
        raw = d.get(key)
        mapped = key.replace("_json", "")
        if raw:
            try:
                d[mapped] = json.loads(raw)
            except json.JSONDecodeError:
                d[mapped] = None
        else:
            d[mapped] = None
    for key in ("input_request", "input_response"):
        raw = d.get(key)
        if raw:
            try:
                d[key] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    d["is_example"] = bool(d.get("is_example"))
    d["is_warm"] = bool(d.get("is_warm"))
    d["cache_hit"] = bool(d.get("cache_hit"))
    return d


def create_run(
    run_id: str,
    url: str,
    *,
    client_ip: str,
    is_example: bool = False,
    is_warm: bool = False,
    session_id: str | None = None,
) -> dict:
    now = _now()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO runs (id, url, status, stage, progress_message, client_ip,
                          is_example, is_warm, session_id, created_at, updated_at)
        VALUES (?, ?, 'queued', 'queued', 'Queued.', ?, ?, ?, ?, ?, ?)
        """,
        (run_id, url, client_ip, int(is_example), int(is_warm), session_id, now, now),
    )
    conn.commit()
    add_event(run_id, "queued", "Queued.")
    return get_run(run_id)  # type: ignore[return-value]


def get_run(run_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


def update_run(run_id: str, **fields: Any) -> dict | None:
    if not fields:
        return get_run(run_id)
    allowed = {
        "normalized_url", "owner", "repo", "branch", "sha", "status", "stage",
        "progress_message", "error", "findings_json", "tree_json", "brief",
        "brief_source", "report_md", "input_request", "input_response",
        "resource_uri", "session_id", "cache_hit", "completed_at", "is_example",
    }
    sets = ["updated_at = ?"]
    values: list[Any] = [_now()]
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"findings_json", "tree_json", "input_request", "input_response"} and not isinstance(value, str):
            value = json.dumps(value) if value is not None else None
        if key in {"cache_hit", "is_example", "is_warm"}:
            value = int(bool(value))
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(run_id)
    conn = get_conn()
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return get_run(run_id)


def add_event(run_id: str | None, stage: str, message: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (run_id, stage, message, created_at) VALUES (?, ?, ?, ?)",
        (run_id, stage, message, _now()),
    )
    conn.commit()


def list_events(run_id: str, after_id: int = 0) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, run_id, stage, message, created_at FROM events WHERE run_id = ? AND id > ? ORDER BY id ASC",
        (run_id, after_id),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_events(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, run_id, stage, message, created_at FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def cache_key(owner: str, repo: str, sha: str) -> str:
    """Case-fold owner/repo; GitHub names are case-insensitive for lookup."""
    return f"{(owner or '').lower()}/{(repo or '').lower()}@{sha}"


def cache_get(owner: str, repo: str, sha: str) -> dict | None:
    if not sha:
        return None
    settings = get_settings()
    key = cache_key(owner, repo, sha)
    cutoff = _now() - settings.cache_ttl_hours * 3600
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cache WHERE cache_key = ? AND created_at >= ?",
        (key, cutoff),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for key_json, mapped in (("findings_json", "findings"), ("tree_json", "tree")):
        raw = d.get(key_json)
        d[mapped] = json.loads(raw) if raw else None
    return d


def cache_put(owner: str, repo: str, sha: str, *, findings: Any, tree: Any, brief: str, brief_source: str, report_md: str) -> None:
    if not sha:
        return
    key = cache_key(owner, repo, sha)
    conn = get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO cache
            (cache_key, owner, repo, sha, findings_json, tree_json, brief, brief_source, report_md, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key, owner, repo, sha,
            json.dumps(findings), json.dumps(tree),
            brief, brief_source, report_md, _now(),
        ),
    )
    conn.commit()


def cache_hot_for(owner: str, repo: str) -> bool:
    """True if any non-expired cache row exists for case-folded owner/repo."""
    if not owner or not repo:
        return False
    settings = get_settings()
    cutoff = _now() - settings.cache_ttl_hours * 3600
    conn = get_conn()
    row = conn.execute(
        """
        SELECT 1 FROM cache
         WHERE lower(owner) = lower(?)
           AND lower(repo) = lower(?)
           AND created_at >= ?
         LIMIT 1
        """,
        (owner, repo, cutoff),
    ).fetchone()
    return row is not None


def fail_stale_runs() -> int:
    """Mark leftover working/input_required runs as failed on process start."""
    conn = get_conn()
    now = _now()
    cur = conn.execute(
        """
        UPDATE runs
           SET status = 'failed',
               error = 'Process restarted while this run was still open.',
               stage = 'failed',
               progress_message = 'Failed: host restarted.',
               updated_at = ?,
               completed_at = ?
         WHERE status IN ('queued', 'working', 'input_required')
        """,
        (now, now),
    )
    conn.commit()
    return cur.rowcount or 0


def reset_concurrent() -> None:
    conn = get_conn()
    conn.execute("UPDATE rate_limits SET concurrent = 0")
    conn.commit()


def daily_count(ip: str) -> int:
    day = utc_day()
    conn = get_conn()
    row = conn.execute(
        "SELECT count FROM rate_limits WHERE ip = ? AND day = ?",
        (ip, day),
    ).fetchone()
    return int(row["count"]) if row else 0


def count_open_runs(ip: str) -> int:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM runs
         WHERE client_ip = ?
           AND status IN ('queued', 'working', 'input_required')
        """,
        (ip,),
    ).fetchone()
    return int(row["n"]) if row else 0


def check_and_reserve(ip: str, *, consume_daily: bool, consume_concurrent: bool) -> str | None:
    """Return a rejection reason or None if reserved. Uses BEGIN IMMEDIATE."""
    settings = get_settings()
    day = utc_day()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT count, concurrent FROM rate_limits WHERE ip = ? AND day = ?",
            (ip, day),
        ).fetchone()
        count = int(row["count"]) if row else 0
        concurrent = int(row["concurrent"]) if row else 0
        if consume_concurrent and concurrent >= settings.max_concurrent_per_ip:
            conn.execute("ROLLBACK")
            return f"One run already in flight from this address (cap {settings.max_concurrent_per_ip}/IP)."
        if consume_daily and count >= settings.max_runs_per_ip_day:
            conn.execute("ROLLBACK")
            return f"Daily cap reached ({settings.max_runs_per_ip_day} runs/IP/day)."
        if row:
            conn.execute(
                """
                UPDATE rate_limits
                   SET count = count + ?, concurrent = concurrent + ?
                 WHERE ip = ? AND day = ?
                """,
                (1 if consume_daily else 0, 1 if consume_concurrent else 0, ip, day),
            )
        else:
            conn.execute(
                "INSERT INTO rate_limits (ip, day, count, concurrent, llm_count) VALUES (?, ?, ?, ?, 0)",
                (ip, day, 1 if consume_daily else 0, 1 if consume_concurrent else 0),
            )
        conn.commit()
        return None
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def release_concurrent(ip: str) -> None:
    day = utc_day()
    conn = get_conn()
    conn.execute(
        """
        UPDATE rate_limits
           SET concurrent = CASE WHEN concurrent > 0 THEN concurrent - 1 ELSE 0 END
         WHERE ip = ? AND day = ?
        """,
        (ip, day),
    )
    conn.commit()


def consume_llm_slot(ip: str) -> bool:
    """Increment the daily LLM counter. True if under cap (consumed)."""
    settings = get_settings()
    day = utc_day()
    cap = int(settings.daily_llm_calls_per_ip)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT llm_count FROM rate_limits WHERE ip = ? AND day = ?",
            (ip, day),
        ).fetchone()
        used = int(row["llm_count"] or 0) if row else 0
        if used >= cap:
            conn.execute("ROLLBACK")
            return False
        if row:
            conn.execute(
                "UPDATE rate_limits SET llm_count = llm_count + 1 WHERE ip = ? AND day = ?",
                (ip, day),
            )
        else:
            conn.execute(
                "INSERT INTO rate_limits (ip, day, count, concurrent, llm_count) VALUES (?, ?, 0, 0, 1)",
                (ip, day),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def refund_daily(ip: str) -> None:
    day = utc_day()
    conn = get_conn()
    conn.execute(
        """
        UPDATE rate_limits
           SET count = CASE WHEN count > 0 THEN count - 1 ELSE 0 END
         WHERE ip = ? AND day = ?
        """,
        (ip, day),
    )
    conn.commit()


def gc_artifacts() -> dict:
    """Drop expired cache rows, old events/runs, leftover clone dirs."""
    settings = get_settings()
    now = _now()
    cache_cut = now - settings.cache_ttl_hours * 3600
    art_cut = now - settings.artifact_ttl_hours * 3600
    conn = get_conn()
    c1 = conn.execute("DELETE FROM cache WHERE created_at < ?", (cache_cut,)).rowcount
    c2 = conn.execute("DELETE FROM events WHERE created_at < ?", (art_cut,)).rowcount
    c3 = conn.execute(
        "DELETE FROM runs WHERE created_at < ? AND status IN ('completed', 'failed', 'cancelled')",
        (art_cut,),
    ).rowcount
    conn.commit()
    removed_dirs = 0
    root = settings.clones_root()
    if root.is_dir():
        import shutil

        for child in root.iterdir():
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue
            if age > settings.artifact_ttl_hours * 3600:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed_dirs += 1
    log.info("gc cache=%s events=%s runs=%s dirs=%s", c1, c2, c3, removed_dirs)
    return {"cache": c1 or 0, "events": c2 or 0, "runs": c3 or 0, "clone_dirs": removed_dirs}
