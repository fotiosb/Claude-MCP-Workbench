# Deploy MCP Workbench → mcp.fotios.org

**Primary production guide** for Fotios’s existing Ubuntu nginx VPS.
Operator path: Windows box `E:\Upwork2\MCP_Demo` → SSH/SFTP → VPS.

nginx is **already running** and serving other sites. `mcp.fotios.org` is **not**
set up yet; there is **no TLS cert yet**. Do **not** delete or overwrite other
`sites-enabled` vhosts. Do **not** wipe the default site unless it is empty and
you understand the impact.

Generic (greenfield) guide: [DEPLOY.md](DEPLOY.md).
Repo nginx file: [nginx/mcp.fotios.org.conf](nginx/mcp.fotios.org.conf)
(same content as [nginx/mcp-workbench.conf](nginx/mcp-workbench.conf)).

Replace `YOU@EMAIL` and `user@SERVER` with your real certbot email and SSH login.

---

## Prerequisites

### DNS

1. Create an **A** record: `mcp.fotios.org` → VPS public IPv4.
2. Create an **AAAA** record only if the VPS has a public IPv6 you intend to use.
3. Wait for propagation, then verify from your PC or any shell:

```bash
dig +short mcp.fotios.org
dig +short AAAA mcp.fotios.org
```

The A answer must be your VPS public IP before you run certbot.

### Access & existing nginx

- You already have SSH (and preferably SFTP) to the VPS.
- nginx is installed and serving other sites — leave those alone.
- Windows project path: `E:\Upwork2\MCP_Demo`

### What you will install

Application code under `/opt/mcp-workbench`, data under `/var/lib/mcp-workbench`,
env at `/etc/mcp-workbench.env`, nginx vhost `mcp.fotios.org` (HTTP first, then
certbot HTTPS).

---

## A. Directory layout on the server (fotios.org family)

```text
/var/www/fotios.org/                    # optional landing / future apex (placeholder)
/var/www/mcp.fotios.org/                # domain dir + optional symlink to static
/var/www/mcp.fotios.org/README.txt
/var/www/mcp.fotios.org/current  →      # optional symlink → /opt/mcp-workbench/web/dist
/opt/mcp-workbench/                     # application code + .venv + web/
/opt/mcp-workbench/web/dist/            # built SPA — nginx root points HERE
/var/lib/mcp-workbench/{db,clones,artifacts,cache}
/etc/mcp-workbench.env
/etc/nginx/sites-available/mcp.fotios.org
/etc/nginx/sites-enabled/mcp.fotios.org → symlink
/etc/nginx/conf.d/mcpwb-limit.conf      # limit_req_zone once in http{}
```

**Important:** for this app, nginx `root` is `/opt/mcp-workbench/web/dist`
(not a bare copy under `/var/www`). Still create `/var/www/fotios.org` and
`/var/www/mcp.fotios.org` so the “domain directory” convention exists; optionally
symlink `current` for operators who expect static under `/var/www`.

```bash
sudo mkdir -p /var/www/fotios.org /var/www/mcp.fotios.org
echo 'fotios.org landing placeholder — MCP Workbench lives at mcp.fotios.org' | sudo tee /var/www/fotios.org/index.html >/dev/null
echo 'mcp.fotios.org — static UI is served from /opt/mcp-workbench/web/dist (see nginx root). Optional: ln -sfn /opt/mcp-workbench/web/dist /var/www/mcp.fotios.org/current' | sudo tee /var/www/mcp.fotios.org/README.txt >/dev/null
```

After the web build exists (section D), optionally:

```bash
sudo ln -sfn /opt/mcp-workbench/web/dist /var/www/mcp.fotios.org/current
```

---

## B. SSH session — packages & user

SSH in as a sudo-capable user:

```bash
ssh user@SERVER
```

### B.1 Update apt indexes (do not force a full upgrade unless you want one)

```bash
sudo apt-get update
```

### B.2 Install anything missing

Do **not** install distro `npm` together with NodeSource `nodejs` — NodeSource’s
`nodejs` package already ships `npm` and **Conflicts** the Debian `npm` package.

Also on Ubuntu with Python 3.14, `python3-venv` needs the matching
`python3.14-venv` package installed explicitly.

```bash
# Core tools (no nodejs/npm in this line)
sudo apt-get -y install \
  git \
  python3 python3-pip python3-venv python3.14-venv \
  certbot python3-certbot-nginx \
  sqlite3 curl unzip rsync
```

If `python3.14-venv` is not found on your release, install whatever `apt-cache
depends python3-venv` names, e.g.:

```bash
apt-cache depends python3-venv | head
sudo apt-get -y install python3-venv $(apt-cache depends python3-venv | awk '/Depends:.*venv/{print $2}')
```

**Node / npm — pick ONE path:**

**Path A (recommended if NodeSource 20 is already configured on this host):**

```bash
# Keep NodeSource nodejs only. Do NOT apt-install the distro "npm" package.
sudo apt-get -y install nodejs
nodejs -v
npm -v    # must work; it comes from the nodejs package
```

**Path B (distro packages only — no NodeSource):**

```bash
# Only if you are NOT using NodeSource. Install BOTH from Ubuntu.
sudo apt-get -y install nodejs npm
nodejs -v
npm -v
```

If you previously mixed them and apt is stuck:

```bash
# See what is selected
apt-cache policy nodejs npm | sed -n '1,40p'
# Prefer NodeSource nodejs; drop the conflicting distro npm package
sudo apt-get -y remove npm
sudo apt-get -y install nodejs
nodejs -v && npm -v
```

nginx is already installed — do not purge or reinstall it blindly.

Node 20+ is required for building `web/` on the server. If `nodejs -v` is older
than v20 and you want NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get -y install nodejs
# again: do NOT also install the distro npm package
nodejs -v && npm -v
```

### B.3 Swap if MemTotal < 2GB

```bash
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
echo "MemTotal=${MEM_KB} kB"
if [ "$MEM_KB" -lt 2097152 ]; then
  if ! swapon --show | grep -q .; then
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  fi
fi
free -h
```

### B.4 Service user and app/data directories

```bash
id mcpwork 2>/dev/null || sudo useradd --system --home /opt/mcp-workbench --shell /usr/sbin/nologin mcpwork

sudo mkdir -p /opt/mcp-workbench \
  /var/lib/mcp-workbench/db \
  /var/lib/mcp-workbench/clones \
  /var/lib/mcp-workbench/artifacts \
  /var/lib/mcp-workbench/cache

sudo chown -R mcpwork:mcpwork /var/lib/mcp-workbench
sudo mkdir -p /var/www/fotios.org /var/www/mcp.fotios.org
```

---

## C. SFTP / SCP from Windows

Exclude `.venv`, `web/node_modules`, `data`, `__pycache__`, and preferably `.git`
from the upload. Staging on the server: `/tmp/MCP_Demo`.

### C.1 WinSCP / SFTP GUI

1. Open WinSCP → New Site → **SFTP**, host = VPS IP or hostname, user = your SSH user.
2. Login (key or password).
3. Remote side: go to `/tmp`. Create folder `MCP_Demo` if needed.
4. Local side: `E:\Upwork2\MCP_Demo`.
5. Upload the project tree. **Do not** upload:
   - `.venv`
   - `web\node_modules`
   - `data` (local SQLite / clones)
   - `__pycache__` folders
6. Optional: in WinSCP Transfer Settings → exclude masks:
   `*/.venv/; */node_modules/; */__pycache__/; */data/; .git/`

### C.2 PowerShell OpenSSH `scp`

From PowerShell on the Windows box:

```powershell
scp -r E:\Upwork2\MCP_Demo user@SERVER:/tmp/MCP_Demo
```

That copies everything including `.venv` / `node_modules` if present — wasteful but works.
Then on the server, rsync with excludes into `/opt` (section C.4).

Prefer a cleaner tree: zip without junk first (C.3), or use `rsync` if you have it
(WSL, cwRsync, or Git Bash with rsync):

```bash
# From WSL or Git Bash (adjust path)
rsync -av --progress \
  --exclude .venv \
  --exclude web/node_modules \
  --exclude node_modules \
  --exclude __pycache__ \
  --exclude data \
  --exclude .git \
  /mnt/e/Upwork2/MCP_Demo/ user@SERVER:/tmp/MCP_Demo/
```

### C.3 Zip on Windows, then scp the zip

PowerShell:

```powershell
cd E:\Upwork2
Compress-Archive -Path MCP_Demo -DestinationPath MCP_Demo.zip -Force
# Better: exclude heavy dirs manually in Explorer, or use 7-Zip excluding .venv and node_modules
scp E:\Upwork2\MCP_Demo.zip user@SERVER:/tmp/MCP_Demo.zip
```

On the server:

```bash
cd /tmp
sudo rm -rf /tmp/MCP_Demo
sudo unzip -o /tmp/MCP_Demo.zip -d /tmp
# If zip rooted as MCP_Demo/, you now have /tmp/MCP_Demo
ls /tmp/MCP_Demo
```

### C.4 Install tree into `/opt/mcp-workbench`

On the server (after upload to `/tmp/MCP_Demo`):

```bash
sudo rsync -a /tmp/MCP_Demo/ /opt/mcp-workbench/ \
  --exclude .venv \
  --exclude web/node_modules \
  --exclude node_modules \
  --exclude __pycache__ \
  --exclude data \
  --exclude .git \
  --exclude mcp_workbench.egg-info

sudo chown -R root:root /opt/mcp-workbench
ls /opt/mcp-workbench
```

Do **not** use `--delete` on first install if you are unsure; on upgrades you may
add `--delete` carefully while still excluding `.venv` and `data`.

---

## D. On server — install app

### D.1 Python venv + editable install

```bash
cd /opt/mcp-workbench
sudo python3 -m venv /opt/mcp-workbench/.venv
sudo /opt/mcp-workbench/.venv/bin/pip install -U pip
sudo /opt/mcp-workbench/.venv/bin/pip install -e /opt/mcp-workbench
```

### D.2 Web UI build

If `web/dist` already exists from a fresh Windows build and you trust it:

```bash
ls -la /opt/mcp-workbench/web/dist/index.html
```

If that file is present and current, you **may skip** npm. Otherwise build on the server:

```bash
cd /opt/mcp-workbench/web
sudo npm ci
sudo npm run build
ls -la /opt/mcp-workbench/web/dist/index.html
```

Optional operator symlink:

```bash
sudo ln -sfn /opt/mcp-workbench/web/dist /var/www/mcp.fotios.org/current
```

---

## E. `/etc/mcp-workbench.env`

```bash
sudo cp /opt/mcp-workbench/.env.example /etc/mcp-workbench.env
sudo chmod 640 /etc/mcp-workbench.env
sudo chown root:mcpwork /etc/mcp-workbench.env
sudo nano /etc/mcp-workbench.env
```

Set the file to this production block (paste over the template contents):

```bash
HOST=127.0.0.1
PORT=8000
APP_BASE_URL=https://mcp.fotios.org
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
ANTHROPIC_MODEL=claude-sonnet-5
ANTHROPIC_EFFORT=medium
```

Notes:

- `ANTHROPIC_API_KEY` may stay empty; briefs fall back to deterministic text.
  You can also set the key later via `https://mcp.fotios.org/settings`.
- `TRUSTED_PROXY=1` is required behind nginx so rate limits use `X-Real-IP`.
- `PUBLIC_BASE_URL` is accepted as an alias for `APP_BASE_URL` if ever needed.
- Mode must stay `640` and owner `root:mcpwork`.

Verify:

```bash
sudo ls -l /etc/mcp-workbench.env
# expect: -rw-r----- 1 root mcpwork ...
```

---

## F. systemd enable

```bash
sudo cp /opt/mcp-workbench/systemd/mcp-workbench.service /etc/systemd/system/
sudo cp /opt/mcp-workbench/systemd/mcp-workbench-gc.service /etc/systemd/system/
sudo cp /opt/mcp-workbench/systemd/mcp-workbench-gc.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now mcp-workbench.service
sudo systemctl enable --now mcp-workbench-gc.timer

sudo systemctl status mcp-workbench.service --no-pager
sudo systemctl list-timers mcp-workbench-gc.timer --no-pager

curl -fsS http://127.0.0.1:8000/healthz
ss -lntp | grep 8000 || sudo ss -lntp | grep 8000
```

`/healthz` must return immediately (even while examples are warming).

If the unit fails:

```bash
sudo journalctl -u mcp-workbench.service -n 80 --no-pager
```

---

## G. nginx vhost for mcp.fotios.org (HTTP first)

### G.1 `limit_req_zone` once under `http{}`

`limit_req_zone` **cannot** live inside a `server{}` block. Put it once in
`conf.d` (included from `http{}` on Ubuntu).

Check whether zone `mcpwb` already exists:

```bash
sudo grep -R "zone=mcpwb" /etc/nginx/ 2>/dev/null || true
```

If **no** match, create:

```bash
echo 'limit_req_zone $binary_remote_addr zone=mcpwb:10m rate=10r/s;' | sudo tee /etc/nginx/conf.d/mcpwb-limit.conf
```

If a match **already** exists, **do not** create a duplicate — nginx will error
with `duplicate limit_req_zone "mcpwb"`.

The repo vhost files comment out the zone line and remind you of this rule.

### G.2 Install the site (do not remove other sites)

```bash
sudo cp /opt/mcp-workbench/nginx/mcp.fotios.org.conf /etc/nginx/sites-available/mcp.fotios.org
# Or: sudo cp /opt/mcp-workbench/nginx/mcp-workbench.conf /etc/nginx/sites-available/mcp.fotios.org

sudo ln -sfn /etc/nginx/sites-available/mcp.fotios.org /etc/nginx/sites-enabled/mcp.fotios.org

# Do NOT: rm sites-enabled/default  (unless you intentionally want that)
ls -la /etc/nginx/sites-enabled/

sudo nginx -t
sudo systemctl reload nginx
```

If `nginx -t` complains about duplicate `limit_req_zone`, remove the zone line
from the vhost (it should already be commented) and/or delete a duplicate
`mcpwb-limit.conf` you accidentally added twice.

### G.3 Local and public HTTP tests (before certbot)

```bash
curl -fsS -H 'Host: mcp.fotios.org' http://127.0.0.1/healthz
curl -fsS http://mcp.fotios.org/healthz
```

Both should return JSON with `"ok": true`.

If the Host header test works but the public name fails → DNS not pointed yet.
If both return another site’s HTML → another `default_server` or wrong
`server_name`; check `sites-enabled` and `nginx -T | grep -A2 server_name`.

---

## H. certbot auto-renewable HTTPS

Only after section G HTTP tests succeed and DNS points here:

```bash
sudo certbot --nginx -d mcp.fotios.org --agree-tos -m YOU@EMAIL --redirect
```

Certbot will:

- Obtain a Let’s Encrypt certificate via HTTP-01 on port 80.
- Modify `/etc/nginx/sites-available/mcp.fotios.org` to add `listen 443 ssl`
  and the certificate paths.
- Add an HTTP→HTTPS redirect when `--redirect` is used.
- Install a renew timer or cron hook on Ubuntu.

Check the renew timer:

```bash
systemctl status certbot.timer --no-pager || true
systemctl list-timers 'certbot*' --no-pager || true
# Some images use cron instead of the timer — either is fine if renew works.
```

Dry-run renew:

```bash
sudo certbot renew --dry-run
```

Verify HTTPS:

```bash
curl -fsS https://mcp.fotios.org/healthz
```

Confirm `TRUSTED_PROXY=1` is still set in `/etc/mcp-workbench.env`.

---

## I. Firewall note

If **ufw is already on**, ensure 80/443 (and SSH) are allowed. Do **not**
blindly `ufw --force enable` or reset rules on a shared host.

```bash
sudo ufw status
```

If active and 80/443 missing:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

If ufw is inactive, you can leave it alone (provider firewall / security group
may already open 80/443). Only enable ufw if you manage this host’s firewall
yourself and understand existing rules:

```bash
# Only if you choose to manage ufw on this box:
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

---

## J. Post-deploy checklist

1. **Health:** `curl -fsS https://mcp.fotios.org/healthz`
2. **Settings:** open `https://mcp.fotios.org/settings` — set password / API key if desired.
3. **Three example tiles:** open `https://mcp.fotios.org/` and confirm tiles load
   (warm may take a minute; health must still be OK).
4. **Classify smoke:**

```bash
curl -fsS 'https://mcp.fotios.org/api/classify?url=https://github.com/fotiosb/MacPresenterView'
curl -fsS https://mcp.fotios.org/api/examples
curl -fsS https://mcp.fotios.org/api/architecture
```

5. **Logs:**

```bash
sudo journalctl -u mcp-workbench.service -n 80 --no-pager
sudo journalctl -u mcp-workbench.service -f
```

Look for warm of example tiles; failures on warm must not kill `/healthz`.

6. **Add to Claude:** URL `https://mcp.fotios.org/mcp`, transport **Streamable HTTP**,
   protocol **2026-07-28**. Use the `repo-audit` prompt.

```bash
curl -sS -D - https://mcp.fotios.org/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"deploy-check","version":"1"}}}'
```

7. **GC timer:** `sudo systemctl list-timers mcp-workbench-gc.timer --no-pager`

---

## K. Troubleshooting

| Symptom | Likely cause | Fix |
|--------|----------------|-----|
| **`python3-venv` / `python3.14-venv` or `nodejs Conflicts npm`** | Mixed NodeSource `nodejs` + distro `npm`, or missing versioned venv | Install `python3.14-venv` (or the depends of `python3-venv`). `apt-get remove npm` then `apt-get install nodejs` only. See §B.2. |
| Public curl fails / wrong IP | DNS not pointed | Fix A/AAAA; `dig +short mcp.fotios.org` |
| certbot HTTP-01 fails | nginx not answering `Host: mcp.fotios.org` on :80 | Finish section G; `curl -H 'Host: mcp.fotios.org' http://127.0.0.1/healthz` |
| Wrong site / default page | Another `default_server` catching the host | `nginx -T \| grep -E 'server_name\|default_server'`; ensure `mcp.fotios.org` vhost is enabled |
| Blank UI / 404 static | Wrong `root` or missing `web/dist` | `ls /opt/mcp-workbench/web/dist/index.html`; rebuild with `npm ci && npm run build` |
| 502 Bad Gateway | Port 8000 not listening / app down | `systemctl status mcp-workbench`; `curl http://127.0.0.1:8000/healthz`; `journalctl -u mcp-workbench -n 80` |
| `duplicate limit_req_zone` | Zone defined twice | Keep a single definition in `/etc/nginx/conf.d/mcpwb-limit.conf`; remove extras |
| Service won’t start / DB errors | `ProtectSystem=strict` + wrong paths | `DATA_DIR`/`CLONE_DIR` must be under `/var/lib/mcp-workbench`; unit `ReadWritePaths=` must include it |
| Missing env | No `/etc/mcp-workbench.env` | Copy from `.env.example`, mode 640, `root:mcpwork` |
| OOM on npm / warm | <2GB RAM no swap | Section B.3 |
| Rate limits wrong client | `TRUSTED_PROXY=0` | Set `TRUSTED_PROXY=1`, restart service |
| SELinux denials | N/A on stock Ubuntu | Not applicable |

Useful commands:

```bash
dig +short mcp.fotios.org
curl -v -H 'Host: mcp.fotios.org' http://127.0.0.1/healthz
sudo nginx -t
sudo nginx -T 2>/dev/null | grep -E 'server_name|root |listen '
sudo systemctl status mcp-workbench.service --no-pager
sudo journalctl -u mcp-workbench.service -n 100 --no-pager
ss -lntp | grep -E ':80|:443|:8000'
ls -la /opt/mcp-workbench/web/dist/
sudo ls -la /var/lib/mcp-workbench/
```

---

## Upgrade (later)

```bash
sudo systemctl stop mcp-workbench.service
# upload new tree to /tmp/MCP_Demo (section C), then:
sudo rsync -a /tmp/MCP_Demo/ /opt/mcp-workbench/ \
  --exclude .venv \
  --exclude web/node_modules \
  --exclude node_modules \
  --exclude __pycache__ \
  --exclude data \
  --exclude .git
sudo /opt/mcp-workbench/.venv/bin/pip install -e /opt/mcp-workbench
cd /opt/mcp-workbench/web && sudo npm ci && sudo npm run build
sudo systemctl start mcp-workbench.service
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS https://mcp.fotios.org/healthz
```

Do **not** overwrite `/etc/mcp-workbench.env` on upgrade.

---

## SQLite backup

```bash
sudo -u mcpwork sqlite3 /var/lib/mcp-workbench/db/workbench.db \
  ".backup /var/lib/mcp-workbench/db/workbench.backup.db"
sudo ls -l /var/lib/mcp-workbench/db/
```

Prefer `.backup` over copying the DB file while the service is running (WAL mode).

---

One Uvicorn worker only. Do not raise `--workers` — the in-process task queue
and clone cleanup live in that single process.
