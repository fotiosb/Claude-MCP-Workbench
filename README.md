# Claude MCP Workbench

**Public GitHub repo audit, over MCP.**

Live demo: **[https://mcp.fotios.org](https://mcp.fotios.org)**

[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-4b6bfb)](https://mcp.fotios.org/mcp)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Live demo](https://img.shields.io/badge/demo-mcp.fotios.org-0ea5e9)](https://mcp.fotios.org)

A live MCP host with a small web UI. v1 audits **public GitHub repositories only**. Caps instead of accounts. Not a startup product page — a tool.

---

## What it is

- A **FastAPI** host (one Uvicorn worker) plus a Vite/React UI.
- One skill pack, **`repo-audit`**: classify a URL → start a Task → shallow-clone → scan without executing → publish MCP resources.
- **Streamable HTTP** at `/mcp` (protocol **2026-07-28**). Tasks are SEP-2663-shaped.
- Public UI: one task chip (**GitHub repo**), three owner example tiles, URL field, live SSE progress, filtered findings, tree, brief, **Open PDF** / **Download PDF**, markdown report.
- **SQLite WAL**. Cache by `owner/repo@sha` (6 h). Settings page for Anthropic key/model/effort.

## What it is not

- Not a SaaS, account system, or marketplace.
- Not a vulnerability scanner or CVE database — it does not invent secrets or CVEs.
- Not a general git host: no GitLab, gist, issues, PRs, blobs, or SSH.
- Not an executor: cloned code is **never** run, installed, or tested.
- Not MCP Apps: there is no iframe. The tree is a **host widget + UI resource** (`ui://audit-tree/{run}`).

---

## Architecture

```
  Browser / MCP client
        │
        ├─ REST + SSE  /api/runs…     ─┐
        ├─ UI          / , /settings   │  FastAPI host (1 worker)
        └─ Streamable  /mcp            ─┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         Task worker   SQLite WAL   Anthropic
         (clone+scan)  + artifacts  (optional brief)
              │
              ▼
         shallow clone → manifests | ci | secrets | tests
              │
              ▼
         audit://  tree://  findings://  ui://audit-tree/{run}
```

**Stages** (SSE, progress ≤ ~500 ms cadence):

`queued → resolving → cloning → walking → [input_required] → scanning:manifests|ci|secrets|tests → writing → briefing`

If the tree exceeds `ELICIT_FILE_THRESHOLD` (default **1500** files), the Task moves to `input_required` (`top-level-only` | `full-tree`, wait `ELICIT_WAIT_SECONDS=300`).

---

## Features

| Area | What you get |
| --- | --- |
| Audit stages | Live progress chip bar + SSE on `/api/runs/{id}/events` |
| Scans | Manifests, `.github/workflows`, redacted credential signals, test-layout heuristics |
| Findings | UI summary with **filtered** secret hits (noisy/vendor paths stripped on read) |
| Tree / brief | JSON tree (paths jail-relative) + Claude brief when configured, else deterministic |
| PDF | `GET /api/runs/{id}/report.pdf` — **Open PDF** and **Download PDF** in the UI |
| Settings | `/settings` — admin password (min 8), Anthropic API key, model, effort (`low`\|`medium`\|`high`); persists under `DATA_DIR`, overrides env without restart |
| MCP | Tools, resources, `repo-audit` prompt, Streamable HTTP at `/mcp` |
| Caps | Per-IP concurrent + daily run/LLM limits; no user accounts |

---

## Quick start

### Linux

```bash
cd Claude-MCP-Workbench   # or your clone path
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cd web && npm ci && npm run build && cd ..
cp .env.example .env      # edit as needed
uvicorn src.host.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Dev UI with proxy: `cd web && npm run dev` (Vite proxies `/api`, `/healthz`, `/mcp`).

Helpers: `scripts/build.sh` (install + web build); `scripts/smoke_test.sh` (classify without network; `SMOKE_NETWORK=1` hits GitHub).

### Windows

```bat
cd Claude-MCP-Workbench
python -m venv .venv
.venv\Scripts\pip install -e .
cd web && npm ci && npm run build
```

Copy `.env.example` → `.env`, then:

```bat
uvicorn src.host.main:app --host 127.0.0.1 --port 8000
```

Or `npm run dev` in `web/` (Vite proxy).

Requires **Python 3.11+**, **Node/npm**, and **git** on `PATH`.

---

## MCP honesty (tools / resources / prompts / tasks)

**Implemented**

| Kind | Surface |
| --- | --- |
| Tools | `classify_url`, `github_resolve`, `repo_clone`, `repo_tree`, `repo_read_file`, `scan_manifests`, `scan_ci`, `scan_secrets`, `scan_tests`, `start_repo_audit`, `write_audit_report`, `get_run`, `list_run_resources` |
| Resources | `audit://`, `tree://`, `findings://`, `skill://index.json`, `skill://repo-audit/SKILL.md`, `ui://audit-tree/{run}` |
| Prompt | `repo-audit` |
| Transport | Streamable HTTP at `/mcp` |
| Tasks | `tasks/get`, `tasks/update`, `tasks/cancel` — **spec-shaped, SDK-incomplete** (SEP-2663). `tasks/result` and `tasks/list` are **not** served (`-32601`). |

Official `mcp` SDK is preferred for tools / resources / prompts when importable; otherwise a fallback Streamable HTTP router is used. Tasks live beside the SDK because SEP-2663 coverage is incomplete.

**Deferred / not in v1**

- `skills/list` — skills are published as resources + the `repo-audit` prompt (`skill://index.json`)
- MCP Apps / iframe UI
- OAuth, authenticated GitHub, private repos
- Completions, resource subscriptions, sampling
- Multi-skill routing beyond `github_repo → repo-audit`

---

## Safety model

- `GIT_TERMINAL_PROMPT=0`, `GIT_LFS_SKIP_SMUDGE=1`, no `--recurse-submodules`
- Shallow clone (`--depth 1`) only under `CLONE_DIR`; delete always
- Path jail; skip binaries; **256 KB** read cap; secret **redaction**
- Public findings filter strips noisy/vendor secret hits on API read
- **Never** execute, install, or test cloned code
- Client IP from `X-Real-IP` **only** when `TRUSTED_PROXY=1`

---

## Caps (no auth)

| Cap | Default |
| --- | --- |
| Clone size | `MAX_CLONE_BYTES=157286400` (150 MB) |
| Files | `MAX_FILES=8000` |
| Wall clock | 60 s (90 s for the large example tile) |
| Concurrent / IP | 1 (warm exempt) |
| Runs / IP / day | `DAILY_RUNS_PER_IP=8` |
| LLM calls / IP / day | `DAILY_LLM_CALLS_PER_IP=20` (deterministic brief if over / no key) |
| URL length | 512 |
| File read | 256 KB |
| Elicit threshold | `ELICIT_FILE_THRESHOLD=1500` |
| Cache TTL | 6 hours (`owner/repo@sha`) |
| Artifact TTL | 24 hours |

Cached example runs do not consume the daily run cap. Claude is skipped on a cache hit.

---

## Deploy

- **Production (fotios):** [DEPLOY-mcp.fotios.org.md](DEPLOY-mcp.fotios.org.md) — nginx VPS, `mcp.fotios.org`, SSH/SFTP, certbot.
- **Generic Ubuntu:** [DEPLOY.md](DEPLOY.md) — service user `mcpwork`, env `/etc/mcp-workbench.env`, systemd + nginx samples under `systemd/` and `nginx/`.

**Apt note:** do not install distro `npm` alongside NodeSource `nodejs` (NodeSource already ships `npm`). On Python 3.14 hosts, install matching `python3.14-venv` as well as `python3-venv`.

---

## Owner example tiles

Shipped under `web/public/examples/` (local SVGs — no hotlinks). Author: **fotiosb**.

1. [Multi-Agent Behavioral Video Analysis](https://github.com/fotiosb/Multi-Agent-Behavioral-Video-Analysis) — RTSP anomaly, YOLO + Gemini + Claude, FastAPI / React (small).
2. [Residential Proxy Aggregator](https://github.com/fotiosb/residential-proxy-aggregator) — Windows edge nodes → SOCKS5 pool (~27 MB binaries; cache warm).
3. [MacPresenterView](https://github.com/fotiosb/MacPresenterView) — Slides → NDI notes, Node + Chrome extension (small).

`WARM_EXAMPLES=1` warms #1 and #3 first, then #2.

---

## How to add a skill

1. Create `src/skills/<name>/SKILL.md`.
2. Teach `src/skills/router.py` a new `kind` → skill id.
3. Add tools / resources / a prompt in `src/mcp_server/`.
4. If the skill is long-running, enqueue through `src/tasks/worker.py` so it shares Task states, SSE, and cleanup.

v1 only routes `github_repo` → `repo-audit`. Everything else is `unsupported` with a one-line reason.

---

## HTTP API (host)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/healthz` | Does not block on warm |
| GET | `/api/classify?url=` | URL → kind / skill |
| GET | `/api/examples` | Three owner tiles |
| POST | `/api/runs` | `{url}` → `{run_id}` (UUIDv4) |
| GET | `/api/runs/{id}` | Status, findings, tree, brief |
| GET | `/api/runs/{id}/events` | SSE progress |
| POST | `/api/runs/{id}/input` | Elicitation choice |
| GET | `/api/runs/{id}/report` | Markdown |
| GET | `/api/runs/{id}/report.pdf` | PDF (Open / Download) |
| GET | `/api/architecture` | Diagram metadata + tools + recent events |
| GET/POST/PUT | `/api/settings/...` | Password-gated Anthropic settings |
| — | `/settings` | Settings UI |
| — | `/mcp` | Streamable HTTP MCP |

---

## Project layout

```
.
├── LICENSE
├── README.md
├── ANALYSIS.md
├── DEPLOY.md
├── DEPLOY-mcp.fotios.org.md
├── .env.example
├── package.json              # workspace root → web/
├── pyproject.toml            # mcp-workbench (Python)
├── nginx/                    # sample site configs
├── systemd/                  # service + GC timer
├── scripts/
│   ├── build.sh
│   ├── smoke_test.sh
│   └── cap_accounting_probe.py
├── src/
│   ├── host/                 # FastAPI app, config, examples, settings
│   ├── mcp_server/           # SDK mount + Streamable HTTP fallback
│   ├── skills/               # router + repo_audit/SKILL.md
│   ├── tasks/                # audit worker
│   ├── audit/                # clone, scans, report, PDF, brief
│   └── store/                # SQLite
└── web/
    ├── public/examples/      # tile SVGs
    └── src/                  # React UI (Workbench + Settings)
```

Runtime data (`DATA_DIR`, clones, artifacts, cache) stays local and is gitignored.

---

## License

[MIT](LICENSE) © Fotios Basagiannis. The three example repositories remain under their own licenses.

## Author

**Fotios Basagiannis** — [fotiosb](https://github.com/fotiosb) · live host [mcp.fotios.org](https://mcp.fotios.org)
