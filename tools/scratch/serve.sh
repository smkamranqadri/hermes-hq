#!/usr/bin/env bash
# Start a throwaway hermes-hq server from ANY checkout/worktree, on its own data dir.
#   tools/scratch/serve.sh <checkout-dir> <port> [name]
# Data lives in /opt/data/hh-scratch/<name> (default: basename of the checkout). Password: pw-test.
# Serves the checkout's OWN backend/static (frontend must be built there first) — the script
# proves it by comparing the served index.html asset hash with the checkout's dist.
set -euo pipefail
CK=$(cd "$1" && pwd); PORT=$2; NAME=${3:-$(basename "$CK")}
MAIN=/opt/data/projects/hermes-hq
HOME_DIR=/opt/data/hh-scratch/$NAME
mkdir -p "$HOME_DIR/profiles"
LOG=$HOME_DIR/serve.log
# `python -m backend.cli` with cwd = checkout puts the checkout first on sys.path; the console
# script `hermes-hq` would import backend/core from the LIVE checkout (editable install .pth).
( cd "$CK" && HERMES_HQ_HOME="$HOME_DIR" HERMES_HQ_PASSWORD=pw-test WM_PROFILES_DIR="$HOME_DIR/profiles" \
  WM_DB= WM_RUNS_DIR= WM_HERMES= \
  setsid nohup "$MAIN/.venv/bin/python" -m backend.cli serve --port "$PORT" --no-dispatcher >"$LOG" 2>&1 </dev/null & )
for i in $(seq 1 40); do
  if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then break; fi; sleep 0.5
done
curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "server did not come up; log: $LOG"; tail -20 "$LOG"; exit 1; }
served=$(curl -s "http://127.0.0.1:$PORT/" | grep -o 'assets/index-[^"]*\.js' | head -1 || true)
local_hash=$(grep -o 'assets/index-[^"]*\.js' "$CK/backend/static/index.html" 2>/dev/null | head -1 || true)
echo "scratch server: http://127.0.0.1:$PORT  home=$HOME_DIR  log=$LOG"
echo "served bundle: ${served:-none}   checkout dist: ${local_hash:-none}"
[ -n "$served" ] && [ "$served" = "$local_hash" ] && echo "bundle check: OK (serving this checkout's build)" || echo "bundle check: MISMATCH — build the frontend in this checkout first (npm run build)"
