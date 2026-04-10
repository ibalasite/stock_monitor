"""Step definition skeletons.

These generic steps provide a minimal executable BDD glue layer.
They intentionally do not encode business assertions yet; assertions are
covered by contract/unit/integration tests in `tests/`.
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
