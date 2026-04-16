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
# Prerequisites:
#   LINE_CHANNEL_ACCESS_TOKEN and LINE_TO_GROUP_ID must be set in environment,
#   or edit ~/Library/LaunchAgents/com.stock_monitor.daemon.plist manually.

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

# Inject LINE credentials from environment into start plist
if [ -n "${LINE_CHANNEL_ACCESS_TOKEN:-}" ]; then
    sed -i '' "s|REPLACE_WITH_YOUR_TOKEN|${LINE_CHANNEL_ACCESS_TOKEN}|g" "${START_DST}"
else
    echo "WARNING: LINE_CHANNEL_ACCESS_TOKEN not set — edit ${START_DST} manually."
fi
if [ -n "${LINE_TO_GROUP_ID:-}" ]; then
    sed -i '' "s|REPLACE_WITH_YOUR_GROUP_ID|${LINE_TO_GROUP_ID}|g" "${START_DST}"
else
    echo "WARNING: LINE_TO_GROUP_ID not set — edit ${START_DST} manually."
fi

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
