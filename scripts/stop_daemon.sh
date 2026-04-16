#!/usr/bin/env bash
# stop_daemon.sh — stop stock_monitor daemon gracefully via SIGTERM (macOS/Linux)
# Spec: EDD §15.4 FR-20 CR-PLAT-03
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/logs/daemon.pid"

if [ ! -f "${PID_FILE}" ]; then
    echo "No PID file found at ${PID_FILE}. Is the daemon running?"
    exit 1
fi

DAEMON_PID=$(cat "${PID_FILE}")

if ! kill -0 "${DAEMON_PID}" 2>/dev/null; then
    echo "Process ${DAEMON_PID} is not running. Removing stale PID file."
    rm -f "${PID_FILE}"
    exit 0
fi

echo "Sending SIGTERM to daemon (PID ${DAEMON_PID})..."
kill -TERM "${DAEMON_PID}"
rm -f "${PID_FILE}"
echo "Daemon stopped (SIGTERM sent). It will exit at end of current cycle."
