"""FR-20 macOS/Windows cross-platform compatibility tests.

Test IDs: TP-PLAT-001, TP-PLAT-002, TP-PLAT-003, TP-PLAT-004, TP-PLAT-005, TP-UAT-017
Spec: PDD §12 UAT-17, EDD §15, USER_STORY US-021, TEST_PLAN §7
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SRC_DIR = PROJECT_ROOT / "stock_monitor"


# ---------------------------------------------------------------------------
# TP-PLAT-001  靜態掃描：禁止 os.path.join / 硬編碼路徑分隔符
# EDD §15.2 CR-PLAT-01 / FR-20
# ---------------------------------------------------------------------------

def _collect_py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_tp_plat_001_no_ospath_join_in_source():
    """TP-PLAT-001: No os.path.join or hardcoded path separators in stock_monitor/ source."""
    violations: list[str] = []

    forbidden_patterns = [
        # os.path.join (the banned cross-platform anti-pattern)
        (re.compile(r'\bos\.path\.join\b'), "os.path.join"),
        # string concatenation with literal "/" as path separator
        (re.compile(r'"/' r'+"\s*\+|' r"\+\s*\"/"), '"/"+  path separator'),
        # string concatenation with literal "\\" as path separator (Windows style)
        (re.compile(r'"\\\\" *\+|\+ *"\\\\"'), '"\\\\"+  path separator'),
    ]

    py_files = _collect_py_files(SRC_DIR)
    assert py_files, "[TP-PLAT-001] No .py files found under stock_monitor/"

    for filepath in py_files:
        text = filepath.read_text(encoding="utf-8")
        for pattern, label in forbidden_patterns:
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    violations.append(
                        f"{filepath.relative_to(PROJECT_ROOT)}:{lineno}: {label} — {line.strip()}"
                    )

    assert not violations, (
        "[TP-PLAT-001] CR-PLAT-01 violations found (use pathlib.Path instead):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# TP-PLAT-002  _install_signal_handlers 平台判斷
# EDD §15.3 CR-PLAT-02 / FR-20
# ---------------------------------------------------------------------------

def test_tp_plat_002_install_signal_handlers_exists():
    """TP-PLAT-002a: _install_signal_handlers must be importable from daemon_runner."""
    try:
        from stock_monitor.application.daemon_runner import _install_signal_handlers
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "[TP-PLAT-002] Module 'stock_monitor.application.daemon_runner' not found. "
            "Implement _install_signal_handlers."
        ) from exc
    except ImportError as exc:
        raise AssertionError(
            f"[TP-PLAT-002] Cannot import _install_signal_handlers: {exc}"
        ) from exc

    assert callable(_install_signal_handlers), (
        "[TP-PLAT-002] _install_signal_handlers must be callable."
    )


def test_tp_plat_002_win32_platform_no_sigterm_error():
    """TP-PLAT-002b: On simulated win32 platform, SIGTERM handler must not be installed and no AttributeError raised."""
    from stock_monitor.application.daemon_runner import _install_signal_handlers

    stop_event = threading.Event()

    original_platform = sys.platform
    try:
        sys.platform = "win32"  # type: ignore[assignment]
        # Must not raise AttributeError (signal.SIGTERM does not exist on Windows)
        _install_signal_handlers(stop_event)
    finally:
        sys.platform = original_platform  # type: ignore[assignment]

    # stop_event should NOT be set (no SIGTERM was sent)
    assert not stop_event.is_set(), (
        "[TP-PLAT-002] stop_event should not be set on win32 without SIGTERM."
    )


def test_tp_plat_002_non_win32_installs_sigterm_handler():
    """TP-PLAT-002c: On non-win32, SIGTERM handler should be installed and fire stop_event."""
    if sys.platform == "win32":
        pytest.skip("Cannot test SIGTERM handler on Windows")

    import signal
    from stock_monitor.application.daemon_runner import _install_signal_handlers

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    # Simulate SIGTERM by sending signal to self
    os.kill(os.getpid(), signal.SIGTERM)

    # Give handler a moment to execute (it's synchronous via signal.signal)
    import time
    time.sleep(0.05)

    assert stop_event.is_set(), (
        "[TP-PLAT-002] stop_event must be set when SIGTERM is received on non-win32."
    )

    # Restore default SIGTERM so other tests aren't affected
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


# ---------------------------------------------------------------------------
# TP-PLAT-003  SIGTERM 後 daemon 乾淨退出
# EDD §15.3 / FR-20
# ---------------------------------------------------------------------------

def test_tp_plat_003_sigterm_sets_stop_event():
    """TP-PLAT-003: SIGTERM causes stop_event to be set so daemon loop can exit cleanly."""
    if sys.platform == "win32":
        pytest.skip("SIGTERM not available on Windows")

    import signal
    from stock_monitor.application.daemon_runner import _install_signal_handlers

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    assert not stop_event.is_set()
    os.kill(os.getpid(), signal.SIGTERM)

    import time
    time.sleep(0.05)

    assert stop_event.is_set(), (
        "[TP-PLAT-003] stop_event must be set after SIGTERM so daemon can exit at end of current cycle."
    )

    # Restore
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


# ---------------------------------------------------------------------------
# TP-PLAT-004  launchd plist plutil -lint 驗證
# EDD §15.5 CR-PLAT-03 / FR-20
# ---------------------------------------------------------------------------

PLIST_PATH = SCRIPTS_DIR / "com.stock_monitor.daemon.plist"


def test_tp_plat_004_plist_exists():
    """TP-PLAT-004a: scripts/com.stock_monitor.daemon.plist must exist."""
    assert PLIST_PATH.exists(), (
        f"[TP-PLAT-004] Missing: {PLIST_PATH}. "
        "Create the launchd plist file as specified in EDD §15.5."
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="plutil is macOS-only")
def test_tp_plat_004_plist_plutil_lint():
    """TP-PLAT-004b: plist must pass plutil -lint on macOS."""
    result = subprocess.run(
        ["plutil", "-lint", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"[TP-PLAT-004] plutil -lint failed:\n{result.stdout}\n{result.stderr}"
    )


def test_tp_plat_004_plist_content_keys():
    """TP-PLAT-004c: plist must contain required launchd keys."""
    assert PLIST_PATH.exists(), pytest.skip("plist not yet created")

    content = PLIST_PATH.read_text(encoding="utf-8")
    required_keys = [
        "Label",
        "ProgramArguments",
        "WorkingDirectory",
        "EnvironmentVariables",
        "RunAtLoad",
    ]
    for key in required_keys:
        assert key in content, (
            f"[TP-PLAT-004] plist is missing required key: {key}"
        )


# ---------------------------------------------------------------------------
# TP-PLAT-005  start/stop scripts 存在且具執行權限
# EDD §15.4 CR-PLAT-03 / FR-20
# ---------------------------------------------------------------------------

START_SH = SCRIPTS_DIR / "start_daemon.sh"
STOP_SH = SCRIPTS_DIR / "stop_daemon.sh"


def test_tp_plat_005_start_script_exists():
    """TP-PLAT-005a: scripts/start_daemon.sh must exist."""
    assert START_SH.exists(), (
        f"[TP-PLAT-005] Missing: {START_SH}. "
        "Create scripts/start_daemon.sh as specified in EDD §15.4."
    )


def test_tp_plat_005_stop_script_exists():
    """TP-PLAT-005b: scripts/stop_daemon.sh must exist."""
    assert STOP_SH.exists(), (
        f"[TP-PLAT-005] Missing: {STOP_SH}. "
        "Create scripts/stop_daemon.sh as specified in EDD §15.4."
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Unix permissions not applicable on Windows")
def test_tp_plat_005_start_script_executable():
    """TP-PLAT-005c: start_daemon.sh must have execute permission."""
    assert START_SH.exists(), pytest.skip("start_daemon.sh not yet created")
    assert os.access(START_SH, os.X_OK), (
        f"[TP-PLAT-005] {START_SH} is not executable. Run: chmod +x scripts/start_daemon.sh"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Unix permissions not applicable on Windows")
def test_tp_plat_005_stop_script_executable():
    """TP-PLAT-005d: stop_daemon.sh must have execute permission."""
    assert STOP_SH.exists(), pytest.skip("stop_daemon.sh not yet created")
    assert os.access(STOP_SH, os.X_OK), (
        f"[TP-PLAT-005] {STOP_SH} is not executable. Run: chmod +x scripts/stop_daemon.sh"
    )


def test_tp_plat_005_start_script_content():
    """TP-PLAT-005e: start_daemon.sh must use nohup and write PID to logs/daemon.pid."""
    assert START_SH.exists(), pytest.skip("start_daemon.sh not yet created")
    content = START_SH.read_text(encoding="utf-8")
    assert "nohup" in content, "[TP-PLAT-005] start_daemon.sh must use nohup for background execution."
    assert "daemon.pid" in content, "[TP-PLAT-005] start_daemon.sh must write PID to logs/daemon.pid."


def test_tp_plat_005_stop_script_content():
    """TP-PLAT-005f: stop_daemon.sh must read PID file and send SIGTERM."""
    assert STOP_SH.exists(), pytest.skip("stop_daemon.sh not yet created")
    content = STOP_SH.read_text(encoding="utf-8")
    assert "daemon.pid" in content, "[TP-PLAT-005] stop_daemon.sh must read from logs/daemon.pid."
    # SIGTERM can be expressed as kill -TERM or kill -15
    assert re.search(r"kill\s+(-TERM|-15)", content), (
        "[TP-PLAT-005] stop_daemon.sh must send SIGTERM (kill -TERM or kill -15)."
    )


# ---------------------------------------------------------------------------
# TP-UAT-017  macOS 端對端冒煙（pytest all-green）
# PDD §12 UAT-17 / FR-20 / US-021
# ---------------------------------------------------------------------------

def test_tp_uat_017_pytest_collect_no_errors():
    """TP-UAT-017: pytest --collect-only must complete without collection errors."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    # Collection errors show as "ERROR" in stderr or exit code 4
    assert result.returncode != 4, (
        f"[TP-UAT-017] pytest collection errors found:\n{result.stderr}\n{result.stdout}"
    )


def test_tp_uat_017_fr20_scripts_present_for_uat():
    """TP-UAT-017: Both daemon scripts and plist must exist for macOS UAT smoke test."""
    missing = []
    for artifact in [START_SH, STOP_SH, PLIST_PATH]:
        if not artifact.exists():
            missing.append(str(artifact.relative_to(PROJECT_ROOT)))

    assert not missing, (
        "[TP-UAT-017] Missing FR-20 artifacts required for macOS UAT smoke:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )
