"""BDD scenario collection for FR-14 template rendering contract."""

from pytest_bdd import scenarios

# Re-export step fixtures into this module namespace for pytest discovery.
from tests.bdd.steps.step_line_template_rendering import *  # noqa: F403

scenarios("../../features/line_template_rendering.feature")

