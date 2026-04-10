"""BDD scenario collection glue.

This file intentionally loads the full feature suite first.
Steps are currently skeleton implementations to keep tests red.
"""

from pytest_bdd import scenarios

# Re-export step fixtures into this module namespace for pytest discovery.
from tests.bdd.steps.step_skeleton import *  # noqa: F403

scenarios("../../features/stock_monitoring_system.feature")
