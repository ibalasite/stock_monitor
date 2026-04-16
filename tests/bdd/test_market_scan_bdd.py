"""BDD scenario runner for FR-19: 全市場估值掃描.

Scenarios are defined in features/market_scan.feature.
All scenarios should be RED until FR-19 (EDD §14) is implemented.
"""

from pytest_bdd import scenarios

from tests.bdd.steps.step_market_scan import *  # noqa: F403

scenarios("../../features/market_scan.feature")
