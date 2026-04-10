"""Generic step definitions for feature parsing.

These steps make the feature file executable while detailed behavioral
assertions are validated in contract/unit/integration tests under `tests/`.
"""

from pytest_bdd import given, parsers, then, when
import pytest


@given(parsers.parse("{step_text}"))
def given_step_skeleton(step_text: str, bdd_state: dict):
    bdd_state.setdefault("given_steps", []).append(step_text)


@when(parsers.parse("{step_text}"))
def when_step_skeleton(step_text: str, bdd_state: dict):
    bdd_state.setdefault("when_steps", []).append(step_text)


@then(parsers.parse("{step_text}"))
def then_step_skeleton(step_text: str, bdd_state: dict):
    bdd_state.setdefault("then_steps", []).append(step_text)


@pytest.fixture
def bdd_state() -> dict:
    return {}
