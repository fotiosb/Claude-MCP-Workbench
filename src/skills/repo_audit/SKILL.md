# repo-audit

Public GitHub repository audit. One skill pack. v1 of MCP Workbench.

## When to use

The host classified a URL as `github_repo`. The user wants a static look at
what a public repository contains: manifests, CI, possible secrets (redacted),
and test layout. Not a pentest. Not a CVE feed.

## What it does

1. Normalize the URL (`https://github.com/{owner}/{repo}` or `/tree/{branch}`).
2. Resolve the repo unauthenticated. HTTP 404 is **private or missing repo**.
3. Start a Task (`start_repo_audit`). States: `working` | `input_required` |
   `completed` | `failed` | `cancelled`.
4. Shallow clone (`--depth 1`, no submodules, `GIT_TERMINAL_PROMPT=0`,
   `GIT_LFS_SKIP_SMUDGE=1`) under `CLONE_DIR`. Delete always.
5. Walk the tree. If file count exceeds `ELICIT_FILE_THRESHOLD`, elicit
   `top-level-only` | `full-tree` (5 minute timeout).
6. Scan in parallel: manifests, `.github/workflows`, secret patterns
   (regex / entropy / filenames, values redacted), test conventions.
7. Write a markdown report and a brief (Claude if configured and not a cache
   hit; otherwise deterministic). Publish resources.

Cloned code is **never executed**.

## Tools

| Tool | Role |
| --- | --- |
| `classify_url` | URL → skill |
| `github_resolve` | Unauthenticated resolve |
| `repo_clone` | Used only via the Task path |
| `repo_tree` | Stored tree |
| `repo_read_file` | Path metadata (body not retained) |
| `scan_manifests` | Manifest findings |
| `scan_ci` | GitHub Actions |
| `scan_secrets` | Redacted pattern hits |
| `scan_tests` | Test layout |
| `start_repo_audit` | Create the Task |
| `write_audit_report` | Markdown report |
| `get_run` | Status |
| `list_run_resources` | URIs |

## Resources

- `audit://{session}/{run}` — markdown report
- `tree://{session}/{run}` — JSON tree
- `findings://{session}/{run}` — JSON findings
- `skill://index.json` — skill pack index
- `skill://repo-audit/SKILL.md` — this file
- `ui://audit-tree/{run}` — host widget + UI resource (not an MCP Apps iframe)

## Prompt

`repo-audit` — instructs a client to classify, start the Task, poll
`tasks/get`, handle elicitation via `tasks/update`, then read the resources.

## Caps

150 MB clone, 8000 files, 60 s wall (90 s for the large example), 1 concurrent
run per IP (warm exempt), 8 runs/IP/day, URL ≤ 512 characters. Cache key
`owner/repo@sha`, TTL 6 hours. Cached examples do not consume the daily cap.

## What it will not do

- Private repos, gist, issues, PRs, blobs, GitLab, SSH
- Recurse submodules, fetch LFS objects, run installers or tests
- Invent CVEs, score CVSS, or display live secret material
