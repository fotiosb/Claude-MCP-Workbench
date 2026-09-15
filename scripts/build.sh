#!/usr/bin/env bash
# Requires: python3+venv, and nodejs with npm on PATH.
# On Ubuntu+NodeSource: install nodejs only (it bundles npm) — not distro npm.
# On Ubuntu 3.14: apt install python3.14-venv if `python3 -m venv` fails.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip
pip install -e .

cd "$ROOT/web"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build
cd "$ROOT"
echo "build ok: $ROOT/web/dist"
