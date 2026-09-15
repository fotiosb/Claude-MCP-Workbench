# MCP Workbench — static / logic / feasibility analysis

Date: 2026-09-13  
Tree: `/workspace/MCP_Demo` (same as `E:\Upwork2\MCP_Demo`)

Legend: **FIXED** = patched in Phase 2 · **LEFT AS-IS** = accepted limitation / deferred · **DEFERRED** = incomplete by design (Tasks SDK)

---

## Critical

### C1. SPA catch-all path traversal — **FIXED**
- **File:** `src/host/main.py` (`/{path:path}` static handler)
- **Why:** `WEB_DIST / path` then `is_file()` / `FileResponse` without resolving under `WEB_DIST`. A path like `../.env` / `../pyproject.toml` can escape `web/dist` when the FastAPI static fallback is used (no nginx `root`).
- **Fix:** Resolve candidate, require `relative_to(WEB_DIST.resolve())`, else fall through to `index.html` / 404.

### C2. Unbounded queue before rate-limit reserve — **FIXED**
- **File:** `src/host/main.py` `POST /api/runs`; `src/mcp_server/fallback.py` `start_repo_audit`; `src/tasks/worker.py`; `src/store/db.py`
- **Why:** Runs were inserted + enqueued **before** `check_and_reserve`. With a sequential worker, many requests from one IP could fill the asyncio queue and trigger many GitHub resolves before concurrent/daily caps applied.
- **Fix:** Reserve concurrent (and soft-precheck daily for non-examples) at submit time; worker reserves daily after resolve (preserving example cache-hit exemption) and always releases concurrent in `finally`.

---

## High

### H1. Blocking walk/scan/size on the event loop — **FIXED**
- **File:** `src/tasks/worker.py`, `src/audit/clone.py`
- **Why:** `walk_tree`, scanners, and `dir_bytes` are synchronous and were awaited directly on the asyncio loop. Large trees stall `/healthz` and SSE (one Uvicorn worker).
- **Fix:** `asyncio.to_thread(...)` for walk, scans, and `dir_bytes`.

### H2. Rate-limit TOCTOU (SELECT then UPDATE) — **FIXED**
- **File:** `src/store/db.py` `check_and_reserve`, `consume_llm_slot`
- **Why:** Read-check-write without `BEGIN IMMEDIATE` can double-admit under overlapping awaits / future concurrency.
- **Fix:** Wrap reserve/consume in `BEGIN IMMEDIATE` … `COMMIT` transactions.

### H3. setuptools / import layout — **LEFT AS-IS (verified OK)**
- **File:** `pyproject.toml` `[tool.setuptools.packages.find] where=["."] include=["src*"]`
- **Why (concern):** `include = ["src*"]` looks unusual vs `where=["src"]`.
- **Finding:** Intentional. Top-level package is `src`, so `uvicorn src.host.main:app` and `from src.host...` work after `pip install -e .` (verified imports). Not a bug.

---

## Medium (clear bugs → fixed where noted)

### M1. `is_example` not updated for MCP-started runs — **FIXED**
- **File:** `src/tasks/worker.py`, `src/store/db.py` `update_run` allowlist
- **Why:** MCP `create_run(..., is_example=False)` never corrected; UI/`get_run` could show wrong flag even though cap logic used `example_by_url`.
- **Fix:** Persist `is_example` after classify in the worker.

### M2. JSON-RPC response to `notifications/initialized` — **FIXED**
- **File:** `src/mcp_server/fallback.py`
- **Why:** Notifications with `id: null` should not get a result body; some clients complain.
- **Fix:** Return HTTP 204 / empty handling for notifications (no JSON-RPC result when `id` is null).

### M3. Clone cleanup on cancel/timeout/elicitation — **LEFT AS-IS (verified OK)**
- **File:** `src/tasks/worker.py` `finally: delete_clone(dest)`; `src/audit/clone.py` deletes on failed clone
- **Finding:** All exit paths that set `dest` clean up in `finally`. Elicitation timeout/cancel returns with `dest` set → deleted. GC timer sweeps leftovers after crash. No bug.

### M4. Stale `working` runs after crash — **LEFT AS-IS (verified OK)**
- **File:** `src/tasks/worker.py` `start_worker` → `fail_stale_runs` + `reset_concurrent`
- **Finding:** Matches DEPLOY.md. In-process `_loop` also marks failed on unexpected exceptions.

### M5. FastAPI ↔ React contract — **LEFT AS-IS (aligned)**
- **Files:** `src/host/main.py` `_public_run`, `web/src/api.ts`, `web/src/types.ts`
- **Finding:** Shapes match (`run_id`, Run fields, classify, examples, architecture, input `choice`). No mismatch found.

### M6. URL classifier reject/accept matrix — **LEFT AS-IS (verified OK)**
- **File:** `src/audit/url_normalize.py`
- **Finding:** Matches smoke matrix (gist/gitlab/blob/ssh/issues/pulls; tree branch with slashes; http/www/.git strip). Ambiguous `/tree/{branch}/{path}` vs slashy branch is inherent to GitHub URL shape — product accepts slashy branches (smoke: `release/1.2`).

### M7. Config aliases (`APP_BASE_URL`, `DAILY_*`, `ELICIT_WAIT`) — **LEFT AS-IS (verified OK)**
- **File:** `src/host/config.py`
- **Finding:** Aliases present and correct via `AliasChoices`. `DAILY_LLM_CALLS_PER_IP` maps by field name.

### M8. `llm_count` migration — **LEFT AS-IS (verified OK)**
- **File:** `src/store/db.py` `_ensure_columns`
- **Finding:** `ALTER TABLE ... ADD COLUMN llm_count` on existing DBs; schema includes column for new DBs.

### M9. Secret scanner hang/crash — **LEFT AS-IS (acceptable)**
- **File:** `src/audit/scan_secrets.py`
- **Finding:** Caps (`max_read_bytes`, 4000 paths, 200 findings), binary skip, path jail. Can be CPU-heavy on large text trees (mitigated by H1 `to_thread`).

### M10. Path jail / symlink escape in clone reads — **LEFT AS-IS (verified OK)**
- **File:** `src/audit/path_safe.py`, `tree.py` (`followlinks=False`)
- **Finding:** Symlink escape blocked via `resolve()` + `relative_to`. Absolute inputs are stripped to relative-under-root (no escape).

---

## Logic / product fidelity checklist

| Intent | Status |
| --- | --- |
| Classify → Task → scanners → brief → resources | OK |
| Cache key `owner/repo@sha`; Claude skipped on hit | OK (`_apply_cache`, `write_brief` not called) |
| Examples skip daily cap on **cache hit** | OK |
| Warm order #1,#3 then #2 | OK (`warm_order`) |
| `/healthz` not blocked by warm | OK (warm is background task; H1 reduces scan stalls) |
| Elicitation threshold + timeout cleanup | OK |
| 60s / 90s wall clocks | OK (`clone_wall_seconds`, example `wall_seconds`) |
| `MAX_CLONE_BYTES` 150MB | OK |
| Client IP from `X-Real-IP` only if `TRUSTED_PROXY` | OK |
| Never execute cloned code | OK |
| MCP tools share cap machinery | OK (shared `check_and_reserve`; MCP IP bucket is literal `"mcp"`) |
| Tasks get/update/cancel | OK fallback; **DEFERRED** full SDK SEP-2663 |
| Official SDK Streamable HTTP mount | **DEFERRED / by design**: `/mcp` always fallback so Tasks work; SDK used for registration/lifespan when importable |

---

## Feasibility / pragmatics (not bugs)

1. **Windows local preview:** Works if Git + Python 3.11+ on PATH; pathlib used throughout. `GIT_ASKPASS=echo` is fine. Clone delete can be flaky if AV locks `.git` files (`ignore_errors=True`).
2. **Ubuntu deploy as written:** Bootable if `DATA_DIR`/`CLONE_DIR` under `/var/lib/mcp-workbench` (ProtectSystem=strict), env file present, `web/dist` built, one Uvicorn worker. Missing `web/dist` → API/MCP still work; SPA fallback absent until build.
3. **`pip install -e .` + `uvicorn src.host.main:app`:** Verified.
4. **MCP SDK dual path:** Fallback is source of truth for HTTP; Tasks incomplete in SDK — **DEFERRED**.
5. **Timezone / day boundaries:** Caps use UTC day (`utc_day`). Concurrent release uses current UTC day; leftover concurrent on prior day is harmless because lookups are day-keyed (reset on restart).
6. **Disk full / large repo #2:** ~27MB binaries within 150MB cap; disk-full surfaces as clone/OS errors → failed run. Feasible on small VPS with swap (DEPLOY step 3).
7. **`.env.example` `TRUSTED_PROXY=1`:** Footgun for naked local bind without nginx (IP spoof via `X-Real-IP`). Production-correct; local smoke sets `0`.
8. **All MCP clients share `client_ip="mcp"`:** One shared daily/concurrent bucket for MCP — pragmatic without auth, not a code bug.
9. **Cancel during `git clone`:** Cancel sets DB status; clone continues until wall timeout/completion, then worker exits via `_still_active` / `finally`. No mid-clone kill — acceptable v1 limitation.
10. **Progress UI `input_required`:** Stage not in chip list — cosmetic.

---

## Phase 2 fix list (files)

- `src/host/main.py` — SPA jail; submit-time concurrent reserve + daily precheck
- `src/mcp_server/fallback.py` — same submit reserve; notification handling
- `src/tasks/worker.py` — daily-only reserve when concurrent pre-reserved; `to_thread`; persist `is_example`
- `src/store/db.py` — `BEGIN IMMEDIATE` reserves; `daily_count`; allow `is_example` updates
- `src/audit/clone.py` — `dir_bytes` via `to_thread`

---

## Verification (post-fix)

- `python -c` imports after `pip install -e .`
- Classify / normalize assertions from `scripts/smoke_test.sh`
- Uvicorn brief boot with `WARM_EXAMPLES=0`: `/healthz`, `/api/classify`
- Zip: `/workspace/MCP_Demo.zip`


---

## Post-fix verification (2026-09-13)

| Check | Result |
| --- | --- |
| `pip install -e .` + imports | OK |
| `scripts/smoke_test.sh` (classify, sqlite WAL, healthz) | **PASS** |
| `GET /healthz` with `WARM_EXAMPLES=0` | OK |
| `GET /api/classify` accept MacPresenterView / reject gist | OK |
| MCP `initialize` via fallback `/mcp` | OK (`sdk+fallback-http`) |
| SPA `../` escape | Blocked (no file body leak) |
| `BEGIN IMMEDIATE` reserve / LLM slot | OK |
| `asyncio.to_thread(walk_tree, …)` | OK |
| Zip | `/workspace/MCP_Demo.zip` |


---

## ROUND 2 — deep second-pass (2026-09-13)

Round 1 items (SPA jail, queue-before-caps, to_thread, BEGIN IMMEDIATE, is_example, notification handling) were **re-read as fixed** and are **not** re-listed as open.

### Critical / High — found and fixed

#### R2-H1. Slashy branch GitHub resolve 404 — **FIXED**
- **File:** `src/audit/github.py`
- **Why:** `commits/{branch}` was interpolated raw. Branches like `release/1.2` (accepted by `url_normalize` / smoke) become path segments `/commits/release/1.2` instead of `/commits/release%2F1.2`. httpx does not encode `/` in path. Resolve fails → run failed for any `/tree/feat/foo`-style URL.
- **Fix:** `urllib.parse.quote(branch, safe="")` in the commits URL.

#### R2-H2. Concurrent slot leak if `create_run` fails after reserve — **FIXED**
- **Files:** `src/host/main.py`, `src/mcp_server/fallback.py`
- **Why:** Submit reserved concurrent, then `create_run` outside the `try` that released on enqueue failure. Disk/SQLite errors between reserve and enqueue leaked the IP’s concurrent slot until day rollover / restart.
- **Fix:** Wrap `create_run` + `enqueue` in the same `try`; always `release_concurrent` on failure.

### Medium — found and fixed

#### R2-M1. Absolute clone path leaked via `tree.root` — **FIXED**
- **Files:** `src/audit/tree.py`, `src/host/main.py` (`_public_tree`), `src/tasks/worker.py` (`_sanitize_tree`), `src/mcp_server/fallback.py` (resources + `repo_tree`)
- **Why:** `walk_tree` stored `str(root)` (e.g. `/var/lib/mcp-workbench/clones/<uuid>`). Public API, MCP `tree://`, and cache served it — host path disclosure.
- **Fix:** Emit `root: "."`; sanitize on public/MCP/cache write paths (also cleans pre-fix cache rows on read-out).

#### R2-M2. `tasks/update` silent success with bad/missing choice — **FIXED**
- **File:** `src/mcp_server/fallback.py` `task_update`
- **Why:** Invalid or missing `inputResponses` returned `{result:{}}` without applying elicitation — clients thought the Task was updated while the worker still waited / timed out.
- **Fix:** Require `top-level-only` | `full-tree`; otherwise JSON-RPC error `-32602`.

#### R2-M3. Frontend EventSource thrash on status changes — **FIXED**
- **File:** `web/src/App.tsx`
- **Why:** `useEffect(..., [run?.id, run?.status])` closed/reopened SSE on every `queued→working` and `input_required` transition, risking missed `done` events and reconnect storms.
- **Fix:** Depend on `run?.id` only; refresh-on-progress; close when terminal.

#### R2-M4. Progress / architecture chips missing `input_required` — **FIXED**
- **Files:** `web/src/components/Progress.tsx`, `src/host/main.py` architecture `stages`
- **Why:** Elicitation stage invisible in the chip row (called out in Round 1 as cosmetic; fixed).
- **Fix:** Insert `input_required` (label `input`) in UI chips and architecture stage list.

#### R2-M5. nginx SSE upstream headers / short read timeout — **FIXED**
- **File:** `nginx/mcp-workbench.conf`
- **Why:** `/api/` lacked `Connection ""` (recommended for long-lived SSE). `proxy_read_timeout 120s` was shorter than elicitation wall (300s); heartbeats usually save this, but 360s is safer if a heartbeat gap occurs.
- **Fix:** `proxy_set_header Connection "";` + `proxy_read_timeout 360s` on `/api/`.

#### R2-M6. Windows read-only `.git` leftovers after `delete_clone` — **FIXED**
- **File:** `src/audit/clone.py`
- **Why:** `shutil.rmtree(..., ignore_errors=True)` leaves read-only git objects on Windows 10 preview; dirs accumulate until GC.
- **Fix:** chmod walk (clear read-only) then `rmtree(ignore_errors=True)`.

#### R2-M7. `RunIn` max_length 2048 vs `MAX_URL_LENGTH` 512 — **FIXED**
- **File:** `src/host/main.py`
- **Why:** Pydantic accepted up to 2048 then failed later in classify/normalize — confusing 400s.
- **Fix:** Align `Field(max_length=512)`.

#### R2-M8. `.env.example` `TRUSTED_PROXY=1` local footgun — **FIXED**
- **File:** `.env.example`
- **Why:** Default `1` on naked local bind trusts spoofable `X-Real-IP`.
- **Fix:** Default `0` with comment; production DEPLOY still sets `1` behind nginx.

### LEFT AS-IS (examined, not bugs or accepted limits)

| ID | Topic | Notes |
| --- | --- | --- |
| R2-L1 | Warm holds the single worker queue | Concurrent not taken by warm (correct). User runs may wait behind warm #1→#3→#2; `/healthz` stays responsive. Product tradeoff. |
| R2-L2 | Cancel does not kill mid-`git clone` | Status flipped; clone finishes/times out; `finally` releases concurrent + deletes. v1 OK. |
| R2-L3 | UTC day boundary concurrent | `release_concurrent` keys today’s day; yesterday’s leftover concurrent is unused (day-keyed). Harmless. |
| R2-L4 | LLM slot spent when Claude errors | Prevents retry hammering; deterministic fallback. |
| R2-L5 | All MCP clients share `client_ip="mcp"` | Pragmatic without auth. |
| R2-L6 | SKILL.md package-data | `pyproject` `"src.skills.repo_audit" = ["SKILL.md"]`; `_skill_md` reads via `Path(__file__).parents[1]/skills/...` — works editable and installed. |
| R2-L7 | ProtectSystem=strict vs `.env` | Env is `/etc/mcp-workbench.env`; data under `/var/lib/...` in `ReadWritePaths`. Correct as written. |
| R2-L8 | Report IDOR by UUID | By design; report body from DB, not filesystem — no path traversal. |
| R2-L9 | `update_run` allowlist | Includes every field the worker writes (`is_example`, findings/tree JSON, etc.). |
| R2-L10 | api.ts / types.ts vs `_public_run` | Field-by-field match after R2 (tree still typed without requiring `root`). |
| R2-L11 | Notification HTTP status 202 vs 204 | Empty body for `id: null`; 202 is acceptable for Streamable HTTP. |
| R2-L12 | How-this-works always visible | No gate required by product; Architecture always shown. |
| R2-L13 | WEB_DIST relative to source tree | Relies on `pip install -e` + WorkingDirectory as in DEPLOY. Non-editable wheel would miss `web/dist` — deploy uses `-e`. |

### Round 2 fix file list

- `src/audit/github.py` — encode branch
- `src/audit/tree.py` — `root: "."`
- `src/audit/clone.py` — Windows-safe delete
- `src/host/main.py` — create_run+enqueue try; `_public_tree`; RunIn 512; architecture stage
- `src/mcp_server/fallback.py` — create_run try; tasks/update validation; tree strip
- `src/tasks/worker.py` — `_sanitize_tree` on complete/cache
- `web/src/App.tsx` — SSE deps
- `web/src/components/Progress.tsx` — `input_required` chip
- `nginx/mcp-workbench.conf` — SSE Connection + timeout
- `.env.example` — `TRUSTED_PROXY=0`

### Round 2 verification

- imports after `pip install -e .`
- classify matrix / smoke_test.sh
- uvicorn `WARM_EXAMPLES=0` → healthz + classify
- Zip `/workspace/MCP_Demo.zip` including `MCP_Demo/ANALYSIS.md`


---

## ROUND 3 — adversarial third-pass (2026-09-13)

Rounds 1–2 items were **re-read as fixed** and are **not** re-opened.

### New issues found and fixed

| ID | Sev | Topic | Evidence | Fix |
| --- | --- | --- | --- | --- |
| R3-H1 | High (Windows) | `path_safe` drive-letter / UNC / ADS / NUL escape | `PureWindowsPath(r"D:\\clones\\uuid") / "C:/Windows/cmd.exe"` → `C:\\Windows\\cmd.exe` (discards jail root). POSIX was safe (segment stays under root). NUL raised bare `ValueError`. | Reject drive `X:`, UNC `//`, `:` segments (ADS), and embedded NUL in `src/audit/path_safe.py`. |
| R3-M1 | Medium | `github_resolve` commits 403/429 swallowed | `github.py`: repo endpoint raised on 403; commits path only special-cased 404 then returned `sha=None` on 403/429/5xx. | Shared `_rate_or_http_error` for 403/429; raise on any commits `>=400`. |
| R3-M2 | Medium | Example post-clone cache-hit burned daily | Worker reserved `consume_daily=True` on miss, then after clone could `_apply_cache` for an example without `refund_daily` — contradicts “examples skip daily on cache hit”. | Restructure: skip daily on pre-clone example hit; on post-clone example hit call `refund_daily`. |
| R3-M3 | Medium | Cache key casing collision / miss | Key was `owner/repo@sha` with URL casing; GitHub is case-insensitive → duplicate misses / split cache. | `cache_key()` lowercases owner/repo; worker stores API-canonical owner/repo. |
| R3-M4 | Medium | Frontend double-start / double-input race | `App.tsx`: tile `onPick` ignored `busy` (setState async); two fast clicks → two `createRun`. Input buttons had no lock. | `useRef` locks in `start` / `onInput`. |
| R3-M5 | Medium | Report download hardening | Any status could fetch report; no `Content-Disposition`; unbounded body. | 409 unless `completed`; sanitized filename attachment; 2MB truncate; `nosniff`. |
| R3-M6 | Medium | `ui://` tree still leaked absolute `root` | `fallback.read_resource` sanitized `tree://` but not `ui-tree` widget payload. | Sanitize `root: "."` for ui-tree. |
| R3-M7 | Medium | `DATA_DIR=""` → cwd | `Path("").resolve()` is process cwd — surprising vs `./data`. | Treat empty `data_dir` / `database_path` / `clone_dir` as unset. |
| R3-M8 | Medium | DEPLOY.md nginx drift after R2 | Embedded server block still `proxy_read_timeout 120s` on `/api/` without `Connection ""`; live conf had 360s + Connection. | Synced DEPLOY.md to match `nginx/mcp-workbench.conf`. |
| R3-L1 | Low | Secret regex unbounded quantifier | `[^'"]{12,}` on large quote-less text → avoidable backtracking within `max_read_bytes`. | Bound to `{12,500}`. |

### Cap accounting end-to-end (examined)

State machine (non-warm web/MCP):

1. **Submit** — `consume_concurrent=True`, soft daily precheck for non-examples; on soft-fail or `create_run`/enqueue fail → `release_concurrent`.
2. **Worker start (cancelled while queued)** — release concurrent, return (before `try`).
3. **Pre-clone cache hit** — example: no daily; non-example: daily reserve then apply cache; `finally` releases concurrent.
4. **Miss** — daily reserve; clone; optional post-clone example hit → `refund_daily` + cache apply.
5. **Cancel / elicit timeout / clone fail / exception** — `finally` always `release_concurrent` when `reserved`.
6. **Warm** — `reserved=False`; no concurrent; may consume daily for `client_ip=warm` on miss.

Probe: `scripts/cap_accounting_probe.py` (PASS). No double-release (extra release clamps at 0). No never-release on normal paths. Dead `refund_daily` is now used for R3-M2.

Residual edge: if a run row vanishes before `_execute` loads it (`if not run: return`), concurrent could leak until restart/`reset_concurrent` — GC only deletes terminal runs, so practical risk is negligible.

### Items examined and confirmed OK

| Hunt item | Result |
| --- | --- |
| Progress / SSE terminal + heartbeat | Emits prior events then `done` on terminal; heartbeat ~0.5s; nginx `/api/` 360s; client `onerror` → `getRun` poll fallback. |
| Private 404 cached | `GithubError` before any `cache_put`. |
| github default branch / tag-as-branch | `repo.branch or default_branch`; commits API accepts tags/SHAs; slashy encoding kept from R2. |
| `repo_read_file` | Metadata-only (clone deleted); no FS read / no cap bypass. |
| MCP resource URI traversal | `run_id` is DB key only; unknown → empty/unknown text. |
| Tools bypassing web caps | `classify_url` / `github_resolve` are read-only; `start_repo_audit` shares reserve; `repo_clone` refused. |
| Inspector | Real `db.recent_events(20)`; not fabricated. |
| Warm + user race | Single worker; warm sequential; missing git → failed warm run, continues. |
| Relative `DATA_DIR` / wrong cwd | DEPLOY `WorkingDirectory=/opt/mcp-workbench`; relative `./data` is a local footgun — documented, empty-string fixed. |
| systemd GC | `ExecStart=... init_db(); gc_artifacts()` — symbols exist; `mcpwork` user matches main unit. |
| Brief LLM IP | Worker passes `client_ip=ip` into `write_brief`. |
| Prompt injection from repo → Claude | Feasibility: description/findings JSON sent under system “do not invent”; not a code bug — see limitations. |
| TypeScript after R2 | `tsc -b && vite build` PASS (no `any`-driven contract break observed). |
| mcpwork user consistency | DEPLOY + both units use `mcpwork`. |

### Round 3 fix file list

- `src/audit/path_safe.py` — Windows/NUL/ADS jail
- `src/audit/github.py` — 403/429 + commits errors; canonical owner
- `src/tasks/worker.py` — daily/cache hit accounting + refund; canonical cache names
- `src/store/db.py` — `cache_key()` case-fold
- `src/host/config.py` — empty DATA_DIR/CLONE_DIR/DATABASE_PATH
- `src/host/main.py` — report 409 / disposition / size bound
- `src/mcp_server/fallback.py` — ui-tree root sanitize
- `src/audit/scan_secrets.py` — regex bound
- `web/src/App.tsx` — start/input ref locks; rebuilt `web/dist`
- `DEPLOY.md` — nginx `/api/` 360s + Connection
- `scripts/cap_accounting_probe.py` — new probe

### Remaining limitations

1. Cancel does not kill mid-`git clone` (R2-L2).
2. All MCP clients share `client_ip="mcp"` bucket.
3. Claude brief can be influenced by attacker-controlled GitHub description / file-derived findings text (system prompt mitigates invention; not a sandbox).
4. Warm still occupies the single worker queue ahead of user runs.
5. Report IDOR by UUID (by design).
6. Secret scan remains CPU-bound on large text trees (`to_thread` + caps; no per-file wall clock).
7. Vanished run row after concurrent reserve (theoretical) until process restart.

### Round 3 verification

- `scripts/cap_accounting_probe.py` PASS
- `scripts/smoke_test.sh` PASS (classify, WAL, healthz)
- path_safe rejects `C:/…`, UNC, ADS, NUL, `..`
- slashy branch `release/1.2` → `release%2F1.2`
- `npm run build` (`tsc -b`) PASS
- Zip `/workspace/MCP_Demo.zip` including `MCP_Demo/ANALYSIS.md`
