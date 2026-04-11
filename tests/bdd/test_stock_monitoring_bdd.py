"""BDD scenario collection glue for full feature behavior assertions."""

from pytest_bdd import scenarios

# Re-export step fixtures into this module namespace for pytest discovery.
from tests.bdd.steps.step_full_runtime import *  # noqa: F403

scenarios("../../features/stock_monitoring_system.feature")
