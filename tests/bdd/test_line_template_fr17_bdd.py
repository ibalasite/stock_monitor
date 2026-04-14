"""BDD scenario runner for FR-17: File-based Jinja2 Template Loading.

Scenarios are defined in features/line_template_fr17.feature.
All scenarios should be RED until FR-17 (EDD §2.8) is implemented.
"""

from pytest_bdd import scenarios

from tests.bdd.steps.step_line_template_fr17 import *  # noqa: F403

scenarios("../../features/line_template_fr17.feature")
