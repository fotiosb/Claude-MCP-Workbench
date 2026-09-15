#!/usr/bin/env bash
# Offline classify + sqlite + healthz. Set SMOKE_NETWORK=1 for GitHub resolve / optional clone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export DATABASE_PATH="${DATABASE_PATH:-$ROOT/data/smoke.db}"
export CLONE_DIR="${CLONE_DIR:-$ROOT/data/smoke-clones}"
export WARM_EXAMPLES="${WARM_EXAMPLES:-0}"
export TRUSTED_PROXY="${TRUSTED_PROXY:-0}"
export MAX_CLONE_BYTES="${MAX_CLONE_BYTES:-157286400}"
mkdir -p "$(dirname "$DATABASE_PATH")" "$CLONE_DIR"

echo "== classify (no network) =="
python3 - <<'PY'
from src.skills.router import classify
from src.audit.url_normalize import normalize

ok = classify("https://github.com/fotiosb/MacPresenterView")
assert ok["accepted"] is True, ok
assert ok["kind"] == "github_repo", ok
assert ok["skill"] == "repo-audit", ok
assert ok["owner"] == "fotiosb"
assert ok["repo"] == "MacPresenterView"

n = normalize("http://www.github.com/fotiosb/MacPresenterView.git/?x=1#y")
assert n.html_url == "https://github.com/fotiosb/MacPresenterView"
assert n.owner == "fotiosb"
n = normalize("https://github.com/fotiosb/MacPresenterView/tree/release/1.2")
assert n.branch == "release/1.2"

def must_fail(url, needle):
    r = classify(url)
    assert r["accepted"] is False, r
    assert needle.lower() in (r["reason"] or "").lower(), (url, r["reason"], needle)

must_fail("https://gist.github.com/fotiosb/abc", "gist")
must_fail("https://gitlab.com/fotiosb/MacPresenterView", "gitlab")
must_fail("https://github.com/fotiosb/MacPresenterView/blob/main/README.md", "blob")
must_fail("git@github.com:fotiosb/MacPresenterView.git", "ssh")
must_fail("https://github.com/fotiosb/MacPresenterView/issues/1", "issue")
must_fail("https://github.com/fotiosb/MacPresenterView/pull/1", "pull")
print("classify ok")
PY

echo "== sqlite init =="
python3 - <<'PY'
from src.store.db import init_db, get_conn
path = init_db()
conn = get_conn()
rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
names = {r[0] for r in rows}
assert {"runs", "cache", "rate_limits", "events"} <= names, names
mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
assert str(mode).lower() == "wal", mode
print(f"sqlite ok {path} journal={mode}")
PY

echo "== healthz (does not wait on warm) =="
PORT="${SMOKE_PORT:-8765}"
python3 -m uvicorn src.host.main:app --host 127.0.0.1 --port "$PORT" --workers 1 >/tmp/mcp-smoke-uvicorn.log 2>&1 &
UV_PID=$!
cleanup() { kill "$UV_PID" 2>/dev/null || true; wait "$UV_PID" 2>/dev/null || true; }
trap cleanup EXIT
ok=0
for i in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/tmp/mcp-smoke-health.json 2>/dev/null; then
    python3 -c "import json; d=json.load(open('/tmp/mcp-smoke-health.json')); assert d.get('ok') is True"
    ok=1
    break
  fi
  sleep 0.2
done
if [[ "$ok" != 1 ]]; then
  echo "healthz failed"
  cat /tmp/mcp-smoke-uvicorn.log || true
  exit 1
fi
echo "healthz ok"

if [[ "${SMOKE_NETWORK:-0}" == "1" ]]; then
  echo "== network: classify + run MacPresenterView =="
  curl -fsS "http://127.0.0.1:${PORT}/api/classify?url=https://github.com/fotiosb/MacPresenterView"
  echo
  curl -fsS -X POST "http://127.0.0.1:${PORT}/api/runs" \
    -H "Content-Type: application/json" \
    -d '{"url":"https://github.com/fotiosb/MacPresenterView"}' \
    >/tmp/mcp-smoke-run.json
  cat /tmp/mcp-smoke-run.json
  echo
  python3 - <<'PY'
import json, os, time, urllib.request
port = os.environ.get("SMOKE_PORT", "8765")
run = json.load(open("/tmp/mcp-smoke-run.json"))
rid = run["run_id"]
for _ in range(90):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs/{rid}") as r:
        body = json.load(r)
    if body.get("status") in {"completed", "failed", "cancelled"}:
        print(body.get("status"), body.get("error") or body.get("resource_uri"))
        if body.get("status") != "completed":
            raise SystemExit("network run failed")
        break
    time.sleep(1)
else:
    raise SystemExit("network run timeout")
PY
fi

echo "smoke_test ok"
