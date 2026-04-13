@bdd @test_plan @stock_monitor @UAT-014
Feature: LINE Template Rendering Contract (FR-14)
  Rule: All outbound LINE messages should be template-driven
    # Scope from PDD FR-14:
    # - minute digest
    # - opening summary
    # - trigger row (status 1/2 content line)
    # - test push (if provided)

    @TP-TPL-001
    Scenario: [TP-TPL-001] opening summary renderer contract exists in application layer
      Given FR-14 template renderer contract should exist
      When loading opening summary template renderer
      Then opening summary template renderer symbol should be available

    @TP-TPL-002
    Scenario: [TP-TPL-002] runtime should expose template render hook for line messages
      Given FR-14 runtime template hook should exist
      When loading runtime template render hook
      Then runtime template hook symbol should be available

    @TP-TPL-003
    Scenario: [TP-TPL-003] minute digest and trigger row must be rendered via template_key + context
      # PDD FR-14: 業務層只能傳遞 template_key + context，不得直接拼接最終 LINE 文案
      # 對應 TEST_PLAN TP-TPL-003
      Given FR-14 TRIGGER_ROW_TEMPLATE_KEY constant should be defined in runtime_service
      And FR-14 MINUTE_DIGEST_TEMPLATE_KEY constant should be defined in monitoring_workflow
      When build_minute_rows is called with a sendable hit
      Then the trigger row message must be produced by render_line_template_message
      And render_line_template_message must be called with a trigger row template_key and context dict
      And no hardcoded final LINE text should be assembled as a plain f-string in build_minute_rows

    @TP-TPL-004
    Scenario: [TP-TPL-004] test push message must use template rendering
      # PDD FR-14: 測試推播 / 營運驗證推播（若系統提供）也必須模板化
      # 對應 TEST_PLAN TP-TPL-004
      Given FR-14 TEST_PUSH_TEMPLATE_KEY constant should be defined in runtime_service
      When test push function is invoked
      Then test push message must be produced through render_line_template_message
      And TEST_PUSH_TEMPLATE_KEY must not be empty or None

    @UAT-014 @TP-UAT-014
    Scenario: [UAT-014/TP-UAT-014] all outbound LINE messages produced via template_key + context
      # PDD §12 UAT-14 / FR-14 全量驗收
      # 所有出站 LINE 訊息（彙總、摘要、觸發列、測試推播）皆須 template 渲染
      Given runtime service composes outbound LINE messages during a minute cycle
      When any LINE message type is produced (minute digest, opening summary, trigger row)
      Then all messages must be routed through render_line_template_message
      And TRIGGER_ROW_TEMPLATE_KEY must exist as a named constant
      And MINUTE_DIGEST_TEMPLATE_KEY must exist as a named constant
      And no message text may be a plain hardcoded string bypassing template rendering
