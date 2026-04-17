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

# CR-SEC-06: resolve credentials — env var takes priority; fall back to Keychain
if [ -z "${LINE_CHANNEL_ACCESS_TOKEN:-}" ]; then
    LINE_CHANNEL_ACCESS_TOKEN=$(security find-generic-password -s stock_monitor -a LINE_TOKEN -w 2>/dev/null || true)
fi
if [ -z "${LINE_TO_GROUP_ID:-}" ]; then
    LINE_TO_GROUP_ID=$(security find-generic-password -s stock_monitor -a LINE_GROUP_ID -w 2>/dev/null || true)
fi
if [ -z "${LINE_CHANNEL_ACCESS_TOKEN:-}" ] || [ -z "${LINE_TO_GROUP_ID:-}" ]; then
    echo "ERROR: LINE_CHANNEL_ACCESS_TOKEN / LINE_TO_GROUP_ID not found in env or Keychain."
    echo "  Run: security add-generic-password -s stock_monitor -a LINE_TOKEN    -w '<token>'"
    echo "  Run: security add-generic-password -s stock_monitor -a LINE_GROUP_ID -w '<group_id>'"
    exit 1
fi
export LINE_CHANNEL_ACCESS_TOKEN LINE_TO_GROUP_ID

# Resolve Python 3.11+ interpreter (project requires Python 3.11+)
PYTHON=$(command -v python3.11 2>/dev/null || command -v python3 2>/dev/null)
if [ -z "${PYTHON}" ]; then
    echo "ERROR: Python 3.11+ not found. Install Python 3.11 before running the daemon."
    exit 1
fi

nohup env PYTHONUNBUFFERED=1 "${PYTHON}" -m stock_monitor run-daemon >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
echo "${DAEMON_PID}" > "${PID_FILE}"
echo "Daemon started (PID ${DAEMON_PID}). Logs: ${LOG_FILE}"
