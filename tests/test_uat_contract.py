from __future__ import annotations

import pytest

from ._contract import require_symbol


UAT_IDS = [
    "TP-UAT-001",
    "TP-UAT-002",
    "TP-UAT-003",
    "TP-UAT-004",
    "TP-UAT-005",
    "TP-UAT-006",
    "TP-UAT-007",
    "TP-UAT-008",
    "TP-UAT-009",
    "TP-UAT-010",
    "TP-UAT-011",
    "TP-UAT-012",
    "TP-UAT-013",
    "TP-UAT-014",
]


@pytest.mark.parametrize("uat_id", UAT_IDS)
def test_uat_scenarios_declared_and_structured(uat_id: str):
    scenarios = require_symbol(
        "stock_monitor.uat.scenarios",
        "UAT_SCENARIOS",
        uat_id,
    )
    assert isinstance(scenarios, dict), f"[{uat_id}] UAT_SCENARIOS must be a dict."
    assert uat_id in scenarios, f"[{uat_id}] Missing UAT scenario definition."

    scenario = scenarios[uat_id]
    assert isinstance(scenario, dict), f"[{uat_id}] Scenario entry must be dict."
    assert scenario.get("title"), f"[{uat_id}] Scenario title is required."
    assert scenario.get("preconditions"), f"[{uat_id}] Preconditions are required."
    assert scenario.get("steps"), f"[{uat_id}] Steps are required."
    assert scenario.get("expected"), f"[{uat_id}] Expected result is required."
