#!/usr/bin/env bash
# Stop a scratch server by port and (optionally) delete its data dir.
#   tools/scratch/stop.sh <port> [name-to-delete]
set -u
PORT=$1
# pattern that does not match this script's own command line
pids=$(pgrep -f "backend.cli serve --port ${PORT:0:-1}[${PORT: -1}]" || true)
[ -n "$pids" ] && kill $pids && echo "stopped $pids" || echo "nothing listening for --port $PORT"
[ -n "${2:-}" ] && rm -rf "/opt/data/hh-scratch/$2" && echo "removed /opt/data/hh-scratch/$2"
exit 0
