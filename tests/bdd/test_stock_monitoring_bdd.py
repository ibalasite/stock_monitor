"""BDD scenario collection glue.

This file loads the full feature suite and generic step bindings.
Behavioral assertions are covered by unit/integration/UAT contract tests.
"""

from pytest_bdd import scenarios

# Re-export step fixtures into this module namespace for pytest discovery.
from tests.bdd.steps.step_skeleton import *  # noqa: F403

scenarios("../../features/stock_monitoring_system.feature")
