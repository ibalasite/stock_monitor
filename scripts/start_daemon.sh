#!/usr/bin/env bash
# start_daemon.sh — start stock_monitor daemon in background (macOS/Linux)
# Spec: EDD §15.4 FR-20 CR-PLAT-03
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/logs/daemon.pid"
LOG_FILE="${PROJECT_ROOT}/logs/daemon.log"

mkdir -p "${PROJECT_ROOT}/logs"

if [ -f "${PID_FILE}" ]; then
    EXISTING_PID=$(cat "${PID_FILE}")
    if kill -0 "${EXISTING_PID}" 2>/dev/null; then
        echo "Daemon is already running (PID ${EXISTING_PID}). Use stop_daemon.sh to stop it first."
        exit 1
    else
        echo "Stale PID file found (PID ${EXISTING_PID} not running). Removing."
        rm -f "${PID_FILE}"
    fi
fi

cd "${PROJECT_ROOT}"
nohup python3 -m stock_monitor run-daemon >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
echo "${DAEMON_PID}" > "${PID_FILE}"
echo "Daemon started (PID ${DAEMON_PID}). Logs: ${LOG_FILE}"
