#!/usr/bin/env bash
# register_launchd_agents.sh
# Installs two launchd agents for StockMonitor on macOS:
#   com.stock_monitor.daemon — weekdays 08:50 (start daemon)
#   com.stock_monitor.stop   — weekdays 14:30 (stop daemon)
#
# Equivalent to: scripts/register_scheduled_tasks.ps1 on Windows
#
# Usage:
#   bash scripts/register_launchd_agents.sh              # install + load
#   bash scripts/register_launchd_agents.sh --uninstall  # unload + remove
#
# Prerequisites (one-time Keychain setup — CR-SEC-06):
#   security add-generic-password -s stock_monitor -a LINE_TOKEN    -w "<your_token>"
#   security add-generic-password -s stock_monitor -a LINE_GROUP_ID -w "<your_group_id>"
#   Token is retrieved at runtime by start_daemon.sh; never stored in the plist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENTS_DIR="${HOME}/Library/LaunchAgents"

START_SRC="${SCRIPT_DIR}/com.stock_monitor.daemon.plist"
STOP_SRC="${SCRIPT_DIR}/com.stock_monitor.stop.plist"
START_DST="${AGENTS_DIR}/com.stock_monitor.daemon.plist"
STOP_DST="${AGENTS_DIR}/com.stock_monitor.stop.plist"

# ---- uninstall ----
if [[ "${1:-}" == "--uninstall" ]]; then
    for dst in "${START_DST}" "${STOP_DST}"; do
        if [ -f "${dst}" ]; then
            launchctl unload "${dst}" 2>/dev/null && echo "Unloaded: ${dst}" || true
            rm -f "${dst}" && echo "Removed:  ${dst}"
        fi
    done
    echo "StockMonitor agents uninstalled."
    exit 0
fi

# ---- install ----
mkdir -p "${AGENTS_DIR}"
mkdir -p "${PROJECT_ROOT}/logs"

# Copy plists, replacing placeholder path with actual project root
sed "s|/Users/tobala/projects/stock_monitor|${PROJECT_ROOT}|g" \
    "${START_SRC}" > "${START_DST}"
sed "s|/Users/tobala/projects/stock_monitor|${PROJECT_ROOT}|g" \
    "${STOP_SRC}" > "${STOP_DST}"

# CR-SEC-06: Token injection removed.
# Credentials are stored in macOS Keychain and retrieved by start_daemon.sh at runtime.
# Run one-time setup if not done yet:
#   security add-generic-password -s stock_monitor -a LINE_TOKEN    -w "<your_token>"
#   security add-generic-password -s stock_monitor -a LINE_GROUP_ID -w "<your_group_id>"

# Load agents (unload first to handle re-registration)
for dst in "${START_DST}" "${STOP_DST}"; do
    launchctl unload "${dst}" 2>/dev/null || true
    launchctl load "${dst}"
done

echo ""
echo "=== Registered agents ==="
launchctl list | grep stock_monitor || echo "  (none found — check: launchctl list | grep stock_monitor)"
echo ""
echo "StockMonitor-Start  08:50 weekdays → start_daemon.sh"
echo "StockMonitor-Stop   14:30 weekdays → stop_daemon.sh"
