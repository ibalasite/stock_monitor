"""BDD scenario collection for opening summary runtime behavior."""

from pytest_bdd import scenarios

# Re-export step fixtures into this module namespace for pytest discovery.
from tests.bdd.steps.step_opening_summary_runtime import *  # noqa: F403

scenarios("../../features/opening_summary_runtime.feature")

