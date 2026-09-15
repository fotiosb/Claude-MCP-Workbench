# Deploy MCP Workbench on Ubuntu 26.04

> **For fotios production** (`mcp.fotios.org` on an existing Ubuntu nginx VPS):
> see **[DEPLOY-mcp.fotios.org.md](DEPLOY-mcp.fotios.org.md)**.
>
> This file remains the generic single-host guide (`workbench.example.com`).


Target: one public host, one Uvicorn worker, nginx in front, systemd, SQLite WAL.
Replace `workbench.example.com` and paths if they differ. Commands assume root
via `sudo`.

Service user: `mcpwork` (nologin, home `/opt/mcp-workbench`).
Data: `/var/lib/mcp-workbench/{db,clones,artifacts,cache}`.
Env: `/etc/mcp-workbench.env` (mode 640, `root:mcpwork`). Template: `.env.example`.

---

## 1. Update the system

```bash
sudo apt-get update
sudo apt-get -y upgrade
sudo timedatectl set-timezone UTC
```

## 2. Install packages

```bash
# Core packages (no nodejs/npm here — see Node section below)
sudo apt-get -y install \
  git nginx \
  python3 python3-pip python3-venv \
  certbot python3-certbot-nginx \
  sqlite3 curl unzip rsync ufw
```

If apt reports `python3-venv : Depends: python3.14-venv ... but it is not going
to be installed`, install the versioned package explicitly:

```bash
sudo apt-get -y install python3.14-venv
# or discover the name:
apt-cache depends python3-venv | head
sudo apt-get -y install python3-venv $(apt-cache depends python3-venv | awk '/Depends:.*venv/{print $2}')
```

**Node — pick one path (never mix):**

- **Distro:** `sudo apt-get -y install nodejs npm`
- **NodeSource** (bundles npm): `sudo apt-get -y install nodejs` only — **do not** also install the Debian `npm` package. Mixing yields `nodejs Conflicts npm`.

```bash
# Distro path:
sudo apt-get -y install nodejs npm

# OR NodeSource path (after setup_20.x), and if apt previously selected distro npm:
sudo apt-get -y remove npm || true
sudo apt-get -y install nodejs
nodejs -v && npm -v
```

If `nodejs -v` is older and you want NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get -y install nodejs
# do not: apt-get install npm
nodejs -v && npm -v
```

## 3. Swap if MemTotal < 2GB

```bash
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 2097152 ]; then
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
```

## 4. Create the service user and directories

```bash
sudo useradd --system --home /opt/mcp-workbench --shell /usr/sbin/nologin mcpwork
sudo mkdir -p /opt/mcp-workbench \
  /var/lib/mcp-workbench/db \
  /var/lib/mcp-workbench/clones \
  /var/lib/mcp-workbench/artifacts \
  /var/lib/mcp-workbench/cache
sudo chown -R mcpwork:mcpwork /var/lib/mcp-workbench
```

## 5. Copy from Windows

From the Windows box (`E:\Upwork2\MCP_Demo`), exclude `.venv` and `web/node_modules`:

```bat
rsync -av --exclude .venv --exclude web/node_modules --exclude __pycache__ --exclude .git ^
  E:\Upwork2\MCP_Demo\ user@workbench.example.com:/tmp/MCP_Demo/
```

Or `scp -r` the same tree, then on the host:

```bash
sudo rsync -a --delete /tmp/MCP_Demo/ /opt/mcp-workbench/ \
  --exclude .venv --exclude web/node_modules --exclude node_modules --exclude __pycache__ --exclude .git
sudo chown -R root:root /opt/mcp-workbench
```

If you are copying a zip instead:

```bash
sudo unzip MCP_Demo.zip -d /tmp
sudo rsync -a --delete /tmp/MCP_Demo/ /opt/mcp-workbench/ \
  --exclude .venv --exclude web/node_modules --exclude node_modules --exclude __pycache__ --exclude .git
sudo chown -R root:root /opt/mcp-workbench
```

## 6. Python virtualenv and install

```bash
cd /opt/mcp-workbench
sudo python3 -m venv /opt/mcp-workbench/.venv
sudo /opt/mcp-workbench/.venv/bin/pip install -U pip
sudo /opt/mcp-workbench/.venv/bin/pip install -e /opt/mcp-workbench
```

## 7. Build the web UI

```bash
cd /opt/mcp-workbench/web
sudo npm ci
sudo npm run build
```

`web/dist/` is what nginx and the FastAPI fallback both serve.

## 8. Environment file

Copy the repo template to `/etc/mcp-workbench.env` (mode 640, `root:mcpwork`):

```bash
sudo cp /opt/mcp-workbench/.env.example /etc/mcp-workbench.env
sudo chmod 640 /etc/mcp-workbench.env
sudo chown root:mcpwork /etc/mcp-workbench.env
sudo nano /etc/mcp-workbench.env
```

Required production values:

```
HOST=127.0.0.1
PORT=8000
APP_BASE_URL=https://workbench.example.com
MCP_SERVER_NAME=mcp-workbench
DATA_DIR=/var/lib/mcp-workbench
CLONE_DIR=/var/lib/mcp-workbench/clones
MAX_CLONE_BYTES=157286400
MAX_FILES=8000
MAX_CONCURRENT_PER_IP=1
DAILY_RUNS_PER_IP=8
DAILY_LLM_CALLS_PER_IP=20
ELICIT_FILE_THRESHOLD=1500
ELICIT_WAIT_SECONDS=300
CACHE_TTL_HOURS=6
WARM_EXAMPLES=1
ARTIFACT_TTL_HOURS=24
TRUSTED_PROXY=1
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

`ANTHROPIC_API_KEY` is optional; the brief falls back to deterministic text.
`PUBLIC_BASE_URL` is still accepted as an alias for `APP_BASE_URL`.

## 9. Install systemd units

```bash
sudo cp /opt/mcp-workbench/systemd/mcp-workbench.service /etc/systemd/system/
sudo cp /opt/mcp-workbench/systemd/mcp-workbench-gc.service /etc/systemd/system/
sudo cp /opt/mcp-workbench/systemd/mcp-workbench-gc.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Full unit (repeated from `systemd/mcp-workbench.service`):

```ini
[Unit]
Description=MCP Workbench (FastAPI + Uvicorn, one worker)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mcpwork
Group=mcpwork
WorkingDirectory=/opt/mcp-workbench
EnvironmentFile=/etc/mcp-workbench.env
Environment=GIT_TERMINAL_PROMPT=0
Environment=GIT_LFS_SKIP_SMUDGE=1
ExecStart=/opt/mcp-workbench/.venv/bin/uvicorn src.host.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mcp-workbench

[Install]
WantedBy=multi-user.target
```

## 10. Start the app (before nginx)

```bash
sudo systemctl enable --now mcp-workbench.service
sudo systemctl status mcp-workbench.service --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

`/healthz` must return immediately even while examples are warming.

## 11. Enable artifact GC timer

```bash
sudo systemctl enable --now mcp-workbench-gc.timer
sudo systemctl list-timers mcp-workbench-gc.timer --no-pager
```

The timer runs `mcp-workbench-gc.service` daily: expired cache rows, old
runs/events, leftover clone directories (`ARTIFACT_TTL_HOURS=24`).

## 12. nginx HTTP :80 first

```bash
sudo cp /opt/mcp-workbench/nginx/mcp-workbench.conf /etc/nginx/sites-available/mcp-workbench.conf
sudo ln -sfn /etc/nginx/sites-available/mcp-workbench.conf /etc/nginx/sites-enabled/mcp-workbench.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx
```

Full server block (repeated from `nginx/mcp-workbench.conf`):

```nginx
limit_req_zone $binary_remote_addr zone=mcpwb:10m rate=10r/s;

server {
    listen 80;
    listen [::]:80;
    server_name workbench.example.com;

    root /opt/mcp-workbench/web/dist;
    index index.html;

    client_max_body_size 8k;

    limit_req zone=mcpwb burst=20 nodelay;

    location /healthz {
        proxy_pass http://127.0.0.1:8000/healthz;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 120s;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE (/api/runs/*/events): keep upstream connection reusable and unbuffered.
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 360s;
    }

    location /mcp {
        proxy_pass http://127.0.0.1:8000/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 120s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Static files: `root` = `web/dist`, `try_files` to `/index.html`.
Proxied: `/api/`, `/mcp`, `/healthz` with `X-Real-IP $remote_addr`,
`proxy_buffering off`, `proxy_set_header Connection ""` on `/api/` and `/mcp`, `proxy_read_timeout 360s` on `/api/` (SSE / elicitation), `120s` on `/healthz` and `/mcp`, `client_max_body_size 8k`, and `limit_req`.

## 13. TLS (Let's Encrypt) after HTTP works

```bash
sudo certbot --nginx -d workbench.example.com
```

Confirm `TRUSTED_PROXY=1` so the app trusts `X-Real-IP`.

## 14. Firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

## 15. Smoke the public surface

```bash
curl -fsS https://workbench.example.com/healthz
curl -fsS 'https://workbench.example.com/api/classify?url=https://github.com/fotiosb/MacPresenterView'
curl -fsS https://workbench.example.com/api/examples
curl -fsS https://workbench.example.com/api/architecture
```

Classify must accept MacPresenterView and reject gist / gitlab / blob
(see `scripts/smoke_test.sh`).

## 16. Confirm warm examples

```bash
sudo journalctl -u mcp-workbench.service -n 80 --no-pager
```

Look for warm of tiles #1 and #3, then #2. Health checks must not wait.

## 17. Failures

- **`/healthz` must not wait on warm.** It returns immediately with
  `{"ok": true, "warming": true|false, ...}`. If it hangs, the worker
  startup is blocking the event loop — do not put warm work in the
  lifespan before yield.
- **Warm failed, health still OK.** Example clones can fail (GitHub
  rate-limit, wall clock). That does not fail the unit or `/healthz`.
- **ProtectSystem=strict + SQLite.** Writes only succeed under
  `ReadWritePaths=/var/lib/mcp-workbench`. If `DATA_DIR` / `CLONE_DIR`
  point elsewhere, the service cannot create the DB or clones.
- **Missing `/etc/mcp-workbench.env`.** systemd will not start
  (`EnvironmentFile=`). Copy `.env.example`, mode 640, `root:mcpwork`.
- **MemTotal < 2GB without swap.** `npm ci` / `npm run build` or the
  first warm clone can OOM. Create the 2GB swap in step 3.
- **certbot before :80 works.** HTTP must answer on port 80 first;
  then run certbot.
- **Stale runs after crash.** On start, leftover `working` /
  `input_required` / `queued` runs are marked failed and per-IP
  concurrent counters are reset.
- **LLM cap / no key.** Over `DAILY_LLM_CALLS_PER_IP` (default 20) or
  an empty `ANTHROPIC_API_KEY` uses the deterministic brief. Cache hits
  skip Claude.

## 18. Logs

```bash
sudo journalctl -u mcp-workbench.service -f
sudo journalctl -u mcp-workbench-gc.service -n 50 --no-pager
sudo tail -n 50 /var/log/nginx/access.log
```

## 19. SQLite backup

```bash
sudo -u mcpwork sqlite3 /var/lib/mcp-workbench/db/workbench.db ".backup /var/lib/mcp-workbench/db/workbench.backup.db"
sudo ls -l /var/lib/mcp-workbench/ /var/lib/mcp-workbench/db/
```

WAL mode: prefer `.backup` over copying the file while the service is up.

## 20. Upgrade path

```bash
sudo systemctl stop mcp-workbench.service
sudo rsync -a /path/to/new/MCP_Demo/ /opt/mcp-workbench/ \
  --exclude .venv --exclude web/node_modules --exclude node_modules --exclude data
sudo /opt/mcp-workbench/.venv/bin/pip install -e /opt/mcp-workbench
cd /opt/mcp-workbench/web && sudo npm ci && sudo npm run build
sudo systemctl start mcp-workbench.service
curl -fsS http://127.0.0.1:8000/healthz
```

Do not overwrite `/etc/mcp-workbench.env` on upgrade.

## 21. Verify MCP and Add to Claude

```bash
curl -sS -D - https://workbench.example.com/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"deploy-check","version":"1"}}}'
```

Add to Claude (or any Streamable HTTP client): URL
`https://workbench.example.com/mcp`, transport **Streamable HTTP**,
protocol **2026-07-28**. Use the `repo-audit` prompt. Tasks are
spec-shaped; poll `tasks/get`.

---

One worker only. Do not scale Uvicorn workers in front of the in-process
Task queue — the queue and clone cleanup live in that process.
