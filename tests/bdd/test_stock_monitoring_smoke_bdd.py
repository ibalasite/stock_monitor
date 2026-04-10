"""Concrete BDD smoke scenarios with runtime assertions."""

from pytest_bdd import scenarios

from tests.bdd.steps.step_smoke_runtime import *  # noqa: F403


scenarios("../../features/stock_monitoring_smoke.feature")

