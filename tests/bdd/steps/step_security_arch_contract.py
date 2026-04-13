"""
BDD step definitions for TP-SEC-* and TP-ARCH-* scenarios.

These scenarios correspond to Code Review v0.8 improvement actions
defined in EDD §13. Steps are marked as pending until the respective
CR-* action items are implemented.
"""
import pytest
from pytest_bdd import given, when, then


# ---------------------------------------------------------------------------
# TP-SEC-001  CR-SEC-01: token repr
# ---------------------------------------------------------------------------

@given('LinePushClient 以 token "{token}" 初始化')
def given_line_push_client_with_token(token):
    pytest.skip("Pending CR-SEC-01: field(repr=False) not yet implemented")


@when("對 LinePushClient 實例呼叫 repr()")
def when_call_repr_on_client():
    pytest.skip("Pending CR-SEC-01")


@then('repr 輸出不應包含 "{text}"')
def then_repr_should_not_contain(text):
    pytest.skip("Pending CR-SEC-01")


@then("LinePushClient 仍可正常發出 LINE API 請求")
def then_client_still_functional():
    pytest.skip("Pending CR-SEC-01")


# ---------------------------------------------------------------------------
# TP-SEC-002  CR-SEC-03 / CR-CODE-05: timezone validation
# ---------------------------------------------------------------------------

@given('使用無效時區名稱 "{tz_name}"')
def given_invalid_tz_name(tz_name):
    pytest.skip("Pending CR-SEC-03/CR-CODE-05: timezone raise not yet enforced")


@when("初始化 TimeBucketService 或呼叫 _resolve_timezone")
def when_init_with_invalid_tz():
    pytest.skip("Pending CR-SEC-03")


@then("應立即 raise ValueError")
def then_should_raise_value_error():
    pytest.skip("Pending CR-SEC-03")


@then("不應繼續執行後續邏輯")
def then_should_not_continue():
    pytest.skip("Pending CR-SEC-03")


@then("不應 fallback 至 UTC 時區")
def then_should_not_fallback_utc():
    pytest.skip("Pending CR-SEC-03")


# ---------------------------------------------------------------------------
# TP-ARCH-001  CR-ARCH-01/02 / CR-SEC-02: calculator in application layer
# ---------------------------------------------------------------------------

@given("stock_monitor.application.valuation_calculator 模組可 import")
def given_valuation_calculator_module_importable():
    pytest.skip("Pending CR-ARCH-01: ManualValuationCalculator not moved yet")


@when("執行一次估值計算（正常情境）")
def when_run_valuation_normal():
    pytest.skip("Pending CR-ARCH-01")


@then("ManualValuationCalculator 應可從 application.valuation_calculator import")
def then_calculator_importable_from_application():
    pytest.skip("Pending CR-ARCH-01")


@then("app.py 不應包含估值計算專屬 class 或 function 定義")
def then_apppy_no_calc_logic():
    pytest.skip("Pending CR-ARCH-01")


@then("system_logs 不應出現 scenario_case 相關的偽造 skip 事件")
def then_no_fake_scenario_case_log():
    pytest.skip("Pending CR-ARCH-02/CR-SEC-02")


# ---------------------------------------------------------------------------
# TP-ARCH-002  CR-ARCH-03: single render definition
# ---------------------------------------------------------------------------

@given("已載入 stock_monitor.application.message_template")
def given_message_template_loaded():
    pytest.skip("Pending CR-ARCH-03: duplicate render_line_template_message not yet removed")


@when('在整個專案中搜尋 "def render_line_template_message"')
def when_search_render_fn_definition():
    pytest.skip("Pending CR-ARCH-03")


@then("只應在 message_template.py 中找到一個定義")
def then_only_one_definition():
    pytest.skip("Pending CR-ARCH-03")


@then("runtime_service.py 不應包含 render_line_template_message 函式定義")
def then_no_def_in_runtime_service():
    pytest.skip("Pending CR-ARCH-03")


# ---------------------------------------------------------------------------
# TP-ARCH-003  CR-CODE-03: MinuteCycleConfig
# ---------------------------------------------------------------------------

@given("stock_monitor.application.runtime_service 模組可 import")
def given_runtime_service_importable():
    pytest.skip("Pending CR-CODE-03: MinuteCycleConfig not yet introduced")


@when("從 runtime_service import MinuteCycleConfig")
def when_import_minute_cycle_config():
    pytest.skip("Pending CR-CODE-03")


@then("import 應成功")
def then_import_succeeds():
    pytest.skip("Pending CR-CODE-03")


@then("MinuteCycleConfig 應為 dataclass 或具名 config 型別")
def then_minute_cycle_config_is_dataclass():
    pytest.skip("Pending CR-CODE-03")


@then("run_minute_cycle 應接受 MinuteCycleConfig 作為設定入口")
def then_run_minute_cycle_accepts_config():
    pytest.skip("Pending CR-CODE-03")
