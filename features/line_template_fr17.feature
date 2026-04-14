# language: en
@bdd @test_plan @stock_monitor @FR-17
Feature: FR-17 File-based Jinja2 Template Loading（外部 .j2 模板載入）
  As 企劃 / 文案人員
  I want 以記事本直接修改 templates/line/*.j2 純文字檔即可改變 LINE 推播文案
  So that 調整 wording 完全不需工程人員介入，不需 Code Review

  Background:
    Given LineTemplateRenderer is available in stock_monitor.application.message_template

  Rule: Key 白名單驗證（OWASP A01 路徑遍歷防護）

    @TP-FR17-001
    Scenario: [TP-FR17-001] Path traversal key "../secret" 必須 raise ValueError
      # EDD §2.8: 禁止含 /、\、.. 的 key（path traversal, OWASP A01）
      Given template key "../secret"
      When render_line_template_message is called with that key and empty context
      Then a ValueError should be raised

    @TP-FR17-002
    Scenario: [TP-FR17-002] Key 含正斜線 "foo/bar" 必須 raise ValueError
      # EDD §2.8: ^[a-z0-9_]+$ 白名單，/ 不在白名單內
      Given template key "foo/bar"
      When render_line_template_message is called with that key and empty context
      Then a ValueError should be raised

    @TP-FR17-003
    Scenario: [TP-FR17-003] Key 含反斜線 "foo\bar" 必須 raise ValueError
      Given template key "foo\\bar"
      When render_line_template_message is called with that key and empty context
      Then a ValueError should be raised

    @TP-FR17-004
    Scenario: [TP-FR17-004] 合法 key "line_minute_digest_v1" 必須通過白名單驗證
      # 符合 ^[a-z0-9_]+$ 的 key 必須正常執行
      Given template key "line_minute_digest_v1"
      When render_line_template_message is called with that key and context {"minute_bucket": "2026-04-15 10:00"}
      Then no exception should be raised
      And the result should be non-empty

  Rule: LINE_TEMPLATE_DIR 環境變數覆蓋

    @TP-FR17-005
    Scenario: [TP-FR17-005] LINE_TEMPLATE_DIR 指向自訂目錄時讀取該目錄的 .j2 檔
      # EDD §2.8: 預設 templates/line/；可由 LINE_TEMPLATE_DIR env var 覆蓋
      Given LINE_TEMPLATE_DIR is set to a temp directory
      And a template file "line_minute_digest_v1.j2" in that directory containing "CUSTOM:{{ minute_bucket }}"
      When render_line_template_message is called with key "line_minute_digest_v1" and context {"minute_bucket": "2026-04-15 10:00"}
      Then the rendered output should contain "CUSTOM:"
      And the rendered output should contain "2026-04-15 10:00"

    @TP-FR17-006
    Scenario: [TP-FR17-006] Jinja2 FileSystemLoader 渲染 .j2 內容而非 hardcoded Python 邏輯
      # EDD §2.8: renderer 必須使用 Jinja2 Environment(loader=FileSystemLoader(...))
      Given LINE_TEMPLATE_DIR is set to a temp directory
      And a template file "line_test_push_v1.j2" in that directory containing "PUSH:{{ message }}"
      When render_line_template_message is called with key "line_test_push_v1" and context {"message": "hello"}
      Then the rendered output should be "PUSH:hello"

  Rule: Template 不存在時 Fallback + WARN log

    @TP-FR17-007
    Scenario: [TP-FR17-007] 模板檔案不存在時 fallback 至內建預設並寫 WARN log
      # EDD §2.8: TemplateNotFound → fallback 至 message_template.py 內嵌預設 + WARN log (TEMPLATE_NOT_FOUND)
      # 不得靜默降格，不得 raise exception 中斷主流程
      Given LINE_TEMPLATE_DIR is set to an empty temp directory
      When render_line_template_message is called with key "line_minute_digest_v1" and context {"minute_bucket": "2026-04-15 10:00"}
      Then no exception should be raised
      And the result should be non-empty
      And a WARN log with event "TEMPLATE_NOT_FOUND" should be emitted

  Rule: StrictUndefined — 未定義變數立即報錯

    @TP-FR17-008
    Scenario: [TP-FR17-008] 模板中有未定義變數時記錄 TEMPLATE_RENDER_FAILED 且不送出未知文案
      # EDD §2.8: StrictUndefined — 未定義變數立即報錯（禁止靜默空字串）
      # render 失敗需寫 ERROR log (TEMPLATE_RENDER_FAILED)；不得送出 hardcoded 未知格式
      Given LINE_TEMPLATE_DIR is set to a temp directory
      And a template file "line_test_push_v1.j2" in that directory containing "{{ undefined_var }}"
      When render_line_template_message is called with key "line_test_push_v1" and empty context
      Then a ERROR log with event "TEMPLATE_RENDER_FAILED" should be emitted or an exception should be raised

  Rule: 架構驗證 — Jinja2 Environment + FileSystemLoader

    @TP-FR17-009
    Scenario: [TP-FR17-009] LineTemplateRenderer 原始碼中必須使用 Jinja2 Environment + FileSystemLoader
      # EDD §2.8: Environment(loader=FileSystemLoader(template_dir), undefined=StrictUndefined, autoescape=False)
      When the source code of LineTemplateRenderer is inspected
      Then it should reference "FileSystemLoader" or "Environment" or "jinja2"
