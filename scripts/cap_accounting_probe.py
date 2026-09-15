#!/usr/bin/env python3
"""Round-3 unit-style probe: concurrent/daily reserve release across worker-like paths."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

td = tempfile.mkdtemp(prefix="mcp-cap-")
os.environ["DATABASE_PATH"] = str(Path(td) / "t.db")
os.environ["DATA_DIR"] = td
os.environ["MAX_CONCURRENT_PER_IP"] = "1"
os.environ["DAILY_RUNS_PER_IP"] = "2"
os.environ["WARM_EXAMPLES"] = "0"
os.environ["TRUSTED_PROXY"] = "0"

from src.host.config import reset_settings

reset_settings()
from src.store import db

db.init_db()
ip = "9.9.9.9"


def concurrent(ip: str) -> int:
    day = db.utc_day()
    row = db.get_conn().execute(
        "SELECT concurrent FROM rate_limits WHERE ip = ? AND day = ?", (ip, day)
    ).fetchone()
    return int(row["concurrent"]) if row else 0


def main() -> None:
    # Submit reserve
    assert db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True) is None
    assert concurrent(ip) == 1
    # Second submit blocked
    assert db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True) is not None

    # Soft daily precheck fail → release (mirrors main.py)
    db.release_concurrent(ip)
    assert concurrent(ip) == 0

    # Worker: example cache-hit skips daily
    assert db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True) is None
    # no daily consume for example hit
    assert db.daily_count(ip) == 0
    db.release_concurrent(ip)

    # Worker: miss → daily + (concurrent already held)
    assert db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True) is None
    assert db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False) is None
    assert db.daily_count(ip) == 1
    # Post-clone example cache hit → refund
    db.refund_daily(ip)
    assert db.daily_count(ip) == 0
    db.release_concurrent(ip)
    assert concurrent(ip) == 0

    # create_run-fail path: reserve then release without enqueue
    assert db.check_and_reserve(ip, consume_daily=False, consume_concurrent=True) is None
    db.release_concurrent(ip)
    assert concurrent(ip) == 0

    # Daily cap
    assert db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False) is None
    assert db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False) is None
    assert db.check_and_reserve(ip, consume_daily=True, consume_concurrent=False) is not None

    # Extra release is safe (no negative)
    db.release_concurrent(ip)
    db.release_concurrent(ip)
    assert concurrent(ip) == 0

    print("cap_accounting_probe OK", td)


if __name__ == "__main__":
    main()
