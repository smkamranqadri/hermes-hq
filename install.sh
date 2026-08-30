#!/usr/bin/env bash
# hermes-hq — one-command install on a server where Hermes Agent is already set up.
#
# Usage:  bash install.sh [--no-service] [--host H] [--port P] [--interval S]
# Re-runnable: every step skips itself when already done.
set -euo pipefail

cyan()  { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m✓ %s\033[0m\n" "$*"; }
red()   { printf "\033[31m✗ %s\033[0m\n" "$*"; }
die()   { red "$*"; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_SERVICE=0; HOST="0.0.0.0"; PORT="9010"; INTERVAL="20"
while [ $# -gt 0 ]; do
  case "$1" in
    --no-service) NO_SERVICE=1 ;;
    --host) HOST="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    --interval) INTERVAL="$2"; shift ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

cyan "hermes-hq installer — $HERE"

# 1. prerequisites (hermes-hq manages a Hermes team; it does not install Hermes itself)
command -v hermes >/dev/null 2>&1 || die "hermes not found on PATH — install Hermes Agent first: https://github.com/NousResearch/hermes-agent"
command -v uv     >/dev/null 2>&1 || die "uv not found — https://github.com/astral-sh/uv"
command -v node   >/dev/null 2>&1 || die "Node not found (need >= 22)"
NODE_MAJOR="$(node -e 'console.log(process.versions.node.split(".")[0])')"
[ "$NODE_MAJOR" -ge 22 ] || die "Node >= 22 required (found $(node --version))"
PY_OK="$(uv python find '>=3.11' >/dev/null 2>&1 && echo 1 || echo 0)"
[ "$PY_OK" = 1 ] || die "Python >= 3.11 not found by uv"
green "prerequisites: hermes, uv, node $(node --version)"

# 2. venv + package
if [ -x "$HERE/.venv/bin/hermes-hq" ]; then
  green "venv exists (.venv) — reinstalling the package in place"
else
  cyan "creating .venv"
  uv venv "$HERE/.venv"
fi
uv pip install --python "$HERE/.venv/bin/python" -q -e "$HERE"
green "python package installed"

# 3. frontend
if [ -d "$HERE/frontend/node_modules" ]; then
  green "frontend deps present"
else
  cyan "npm install (frontend)"
  npm install --prefix "$HERE/frontend" --no-audit --no-fund --loglevel=error
fi
cyan "building the UI"
npm run --prefix "$HERE/frontend" build >/dev/null
green "UI built into backend/static"

# 4. login password
HQ_HOME="${HERMES_HQ_HOME:-$(hermes_home_guess=$("$HERE/.venv/bin/python" -c 'import sys; sys.path.insert(0, "'"$HERE"'"); from core import wm_store; print(wm_store.hq_home())'); echo "$hermes_home_guess")}"
if [ -f "$HQ_HOME/password" ]; then
  green "login password already set ($HQ_HOME/password)"
else
  cyan "a login password will be generated on first start (printed once, saved to $HQ_HOME/password)"
fi

# 5. service
if [ "$NO_SERVICE" = 1 ]; then
  green "skipping service install (--no-service). Start manually: $HERE/.venv/bin/hermes-hq serve --host $HOST --port $PORT --interval $INTERVAL"
else
  cyan "installing the service (supervisor auto-detected)"
  "$HERE/.venv/bin/hermes-hq" service install --host "$HOST" --port "$PORT" --interval "$INTERVAL"
  "$HERE/.venv/bin/hermes-hq" service status || true
fi

green "done — open http://$HOST:$PORT (add agents from templates on the Agents page)"
cyan  "updates: '$HERE/.venv/bin/hermes-hq service update' (auto-update runs daily at 05:00 PKT; 'service auto-update --off' disables)"
