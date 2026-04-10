# language: en
@stock_monitor @bdd @pdd @edd @test_plan
Feature: 台股監控與 LINE 通知系統（完整 BDD 規格）
  As 個人投資者與維運者
  I want 系統在交易時段穩定監控、判斷、通知、落盤與補償
  So that 我可以準時收到可行動訊號，且資料一致可追蹤

  Background:
    Given 系統時區為 "Asia/Taipei"
    And 分鐘時間桶格式為 "YYYY-MM-DD HH:mm"
    And 冷卻秒數設定為 300
    And 盤中輪詢間隔為 60 秒
    And 日結估值排程時間為 "14:00"
    And 系統採用 SQLite 並要求 JSON1 與 PRAGMA foreign_keys=ON
    And 報價新鮮度門檻為 90 秒
    And 行情來源最大重試次數為 3

  Rule: Schema 與 migration 約束（TP-DB-*）

    @TP-DB-001 @schema
    Scenario: [TP-DB-001] watchlist 應限制 cheap_price 不得大於 fair_price
      Given 已完成資料庫 migration
      When 新增 watchlist "2330" with fair 1500 and cheap 1000
      Then 寫入應成功
      When 新增 watchlist "2331" with fair 900 and cheap 1000
      Then 寫入應失敗且錯誤為 CHECK constraint

    @TP-DB-002 @schema
    Scenario: [TP-DB-002] valuation_methods 同 method_name 僅允許一個 enabled=1
      Given 已完成資料庫 migration
      When 插入 valuation method "pe_band:v1" with enabled 1
      Then 寫入應成功
      When 插入 valuation method "pe_band:v2" with enabled 1
      Then 寫入應失敗且錯誤為 partial unique index

    @TP-DB-003 @schema
    Scenario: [TP-DB-003] message 表需滿足 minute_bucket 格式、JSON 與同分鐘唯一鍵
      Given 已完成資料庫 migration
      And 已有 watchlist "2330"
      When 新增 message row for stock "2330" minute "2026-04-10 10:21" methods_hit 為有效 JSON 陣列且包含 "manual_rule"
      Then 寫入應成功
      When 再新增 message row for stock "2330" same minute "2026-04-10 10:21"
      Then 寫入應失敗且錯誤為 unique constraint
      When 新增 message row with minute "2026/04/10 10:22" and methods_hit "{bad}"
      Then 寫入應失敗且錯誤為 CHECK constraint

    @TP-DB-004 @schema
    Scenario: [TP-DB-004] pending_delivery_ledger 可寫入、可依狀態索引查詢
      Given 已完成資料庫 migration
      When 寫入一筆 pending_delivery_ledger with status "PENDING"
      Then 應可成功查回該筆資料
      And 依 status 與 updated_at 查詢應命中索引

    @TP-DB-005 @schema
    Scenario: [TP-DB-005] valuation_snapshots 唯一鍵需包含 method_version
      Given 已完成資料庫 migration
      And 已有 watchlist "2330"
      And 已有 valuation method "pe_band:v1" 與 "pe_band:v2"
      When 新增 valuation_snapshot for stock "2330" trade_date "2026-04-10" method "pe_band:v1"
      Then 寫入應成功
      When 新增 valuation_snapshot for stock "2330" trade_date "2026-04-10" method "pe_band:v2"
      Then 寫入應成功
      When 再新增 valuation_snapshot for stock "2330" trade_date "2026-04-10" method "pe_band:v1"
      Then 寫入應失敗且錯誤為 unique constraint

  Rule: 啟動環境與設定驗證（TP-ENV-* + US-011 + UAT-9）

    @TP-ENV-001 @US-011
    Scenario: [TP-ENV-001] JSON1 不可用時服務必須 fail-fast
      Given 執行環境 SQLite 不支援 JSON1
      When 啟動服務
      Then 啟動應失敗
      And 錯誤訊息應明確包含 "JSON1 unavailable"

    @TP-ENV-002 @US-010
    Scenario: [TP-ENV-002] foreign_keys 必須開啟且健康檢查通過
      Given 服務已成功啟動
      When 執行 "PRAGMA foreign_keys;"
      Then 查詢結果應為 1
      When 呼叫 health check
      Then health status 應為 "ok"

    # 系統同時支援 LINE_* 規範命名與 CHANNEL_*/TARGET_* 別名命名，兩組設定等效。
    @TP-ENV-003 @US-011 @UAT-009 @TP-UAT-009
    Scenario Outline: [US-011/UAT-009] LINE 必要參數缺失或無效時 fail-fast
      Given 啟動參數 "<config_case>"
      When 啟動服務
      Then 啟動應失敗
      And 錯誤訊息應包含 "<expected_error>"
      And log 不得輸出完整 token 明文

      Examples: 規範命名缺失（LINE_*）
        | config_case                       | expected_error                    |
        | missing LINE_CHANNEL_ACCESS_TOKEN | LINE_CHANNEL_ACCESS_TOKEN missing |
        | missing LINE_TO_GROUP_ID          | LINE_TO_GROUP_ID missing          |

      Examples: 別名命名缺失（CHANNEL_*/TARGET_*）
        | config_case                  | expected_error               |
        | missing CHANNEL_ACCESS_TOKEN | CHANNEL_ACCESS_TOKEN missing |
        | missing TARGET_GROUP_ID      | TARGET_GROUP_ID missing      |

      Examples: 設定值無效
        | config_case           | expected_error        |
        | invalid channel token | invalid channel token |
        | invalid group id      | invalid group id      |

  Rule: 訊號優先級、方法合併與冷卻（TP-POL-* + EDD §2）

    @TP-POL-001 @priority
    Scenario: [TP-POL-001] 同分鐘同股票同時命中 status 1/2 僅保留 status 2
      Given 股票 "2330" 在同分鐘有兩個命中
      And 第一個命中為 status 1 from method "manual_rule"
      And 第二個命中為 status 2 from method "pe_band_v1"
      When 套用 PriorityPolicy
      Then 最終狀態應為 status 2
      And 訊息內 methods_hit 應包含 "manual_rule,pe_band_v1"

    @TP-POL-002 @cooldown
    Scenario Outline: [TP-POL-002] 冷卻判斷邊界 <elapsed> 秒
      Given 冷卻鍵 "2330+1" 上次通知時間距今 <elapsed> 秒
      When 執行 CooldownPolicy
      Then 結果應為 "<result>"

      Examples:
        | elapsed | result   |
        | 299     | blocked  |
        | 301     | sendable |

    @TP-POL-003 @cooldown
    Scenario: [TP-POL-003] last_sent_at 為 NULL 應視為可發送
      Given 冷卻鍵 "2330+1" 沒有任何歷史通知
      When 執行 CooldownPolicy
      Then 結果應為 "sendable"

    @TP-POL-004 @idempotency
    Scenario: [TP-POL-004] 同分鐘冪等鍵以 stock_no+minute_bucket 組成，不含 stock_status
      Given 股票 "2330" 在分鐘桶 "2026-04-10 10:21" 先命中 status 1
      And 同股票同分鐘再命中 status 2
      When 產生同分鐘冪等鍵
      Then 兩次冪等鍵應相同
      And 冪等鍵應為 "2330|2026-04-10 10:21"

    @TP-POL-005 @EDD-2-3 @multi_method
    Scenario: [TP-POL-005] 同股票同分鐘多方法皆命中 status 1 時只產生一個股票訊號
      Given 股票 "2330" 同分鐘命中 methods "manual_rule,pe_band_v1,pb_band_v2"
      And 以上方法狀態皆為 status 1
      When 進行股票層級聚合
      Then 只應產生一個股票事件
      And 該股票事件狀態應為 status 1
      And 該股票事件 methods_hit 應列出全部命中方法

    @TP-POL-006a @EDD-2-4 @cooldown
    Scenario: [TP-POL-006a] 第 1 分鐘 status 1 可發，第 2 分鐘 status 2 也可發
      Given 第 1 分鐘股票 "2330" 命中 status 1 並已成功發送
      And 第 2 分鐘股票 "2330" 命中 status 2
      When 套用冷卻規則
      Then 第 2 分鐘事件仍應可發送

    @TP-POL-006b @EDD-2-4 @cooldown
    Scenario: [TP-POL-006b] 第 1 分鐘 status 1 可發，第 2 分鐘 status 1 即使方法不同也不可發
      Given 第 1 分鐘股票 "2330" 命中 status 1 from method "manual_rule" 並已成功發送
      And 第 2 分鐘股票 "2330" 命中 status 1 from method "pe_band_v1"
      When 套用冷卻規則
      Then 第 2 分鐘事件應被擋下
      And 不應更新任何 message.update_time

  Rule: 盤中一分鐘主流程與 LINE/DB 一致性（TP-INT-* + UAT-1/2/3/6/7/8）

    @TP-INT-001 @UAT-006
    Scenario: [TP-INT-001] 同分鐘多股票命中時 LINE 僅發一次且內容含全部股票
      Given 分鐘桶 "2026-04-10 10:21" 有可發事件 "2330 status2" 與 "2317 status1"
      When 執行一次盤中輪詢流程
      Then LINE API 應僅被呼叫 1 次
      And 單一訊息內容應同時包含 "2330" 與 "2317"

    @TP-INT-002 @UAT-007
    Scenario: [TP-INT-002] 同分鐘 status 1 應可升級為 status 2
      Given message 表已有 "2330" minute "2026-04-10 10:21" status 1
      When 同分鐘新輸入為 "2330" status 2
      Then upsert 後該筆 status 應為 2
      And methods_hit 與 message 應更新為該分鐘最終聚合內容

    @TP-INT-003
    Scenario: [TP-INT-003] 同分鐘同狀態但內容差異時可更新為最終聚合內容
      Given message 表已有 "2330" minute "2026-04-10 10:21" status 1 且 methods_hit 僅含 "manual_rule"
      When 同分鐘新輸入為 status 1 且 methods_hit 含 "manual_rule,pb_band_v2"
      Then upsert 後 methods_hit 應更新為同分鐘最終方法清單 "manual_rule,pb_band_v2"
      And message 內容應更新為最新聚合版

    @TP-INT-004
    Scenario: [TP-INT-004] LINE 發送失敗不得寫 message 且需記錄 ERROR
      Given 分鐘桶內有至少一筆可發事件
      And LINE API 回傳 HTTP 500
      When 執行一次盤中輪詢流程
      Then message 表該分鐘應新增 0 筆
      And system_logs 應新增 level "ERROR" 的紀錄

    @TP-INT-005 @UAT-008 @TP-UAT-008
    Scenario: [TP-INT-005] LINE 成功但 DB transaction 失敗時建立補償紀錄
      Given 分鐘桶內有至少一筆可發事件
      And LINE API 回傳成功
      And message 落盤 transaction 發生失敗
      When 執行一次盤中輪詢流程
      Then pending_delivery_ledger 或 pending_delivery.jsonl 應新增 "PENDING" 補償項
      And 該分鐘應視為 "已通知"

    @TP-INT-009
    Scenario: [TP-INT-009] 同分鐘 message 批次寫入失敗時應整批 rollback 為 0 筆
      Given 分鐘桶 "2026-04-10 10:21" 應寫入兩筆 message（2330 與 2317）
      And message transaction 在第二筆寫入時失敗
      When 執行該分鐘落盤
      Then message 表在該分鐘應為 0 筆
      And 不得出現部分成功落盤
      And 補償佇列應建立該分鐘待回補項目

    @TP-INT-010
    Scenario: [TP-INT-010] 行情來源短暫失敗後於重試上限內成功，該分鐘流程可繼續
      Given 該分鐘行情來源第 1 次請求失敗
      And 該分鐘行情來源第 2 次請求成功
      And 失敗次數未超過重試上限
      When 執行一次盤中輪詢流程
      Then 該分鐘應可繼續訊號判斷與通知流程
      And system_logs 應記錄 retry 次數

    @TP-INT-011
    Scenario: [TP-INT-011] 行情來源達重試上限仍失敗時，該分鐘跳過且不補發
      Given 該分鐘行情來源在重試上限內皆失敗
      When 執行一次盤中輪詢流程
      Then 該分鐘不應發送 LINE
      And 該分鐘不應寫入 message
      And system_logs 應新增 ERROR 或 WARN
      And 該分鐘不得補發過期訊號

    @TP-INT-006
    Scenario: [TP-INT-006] 補償回補成功後應標記 RECONCILED 且不重複通知
      Given 存在一筆 pending_delivery_ledger status "PENDING"
      When 執行補償 worker
      Then message 表應成功回補
      And ledger 狀態應更新為 "RECONCILED"
      When 補償 worker 再次執行（此時 ledger 已標記 "RECONCILED"）
      Then 不得重複發送同一分鐘 LINE 訊息

    @TP-INT-007 @US-013
    Scenario: [TP-INT-007] 大盤資料 timeout 時該分鐘跳過通知並記錄 WARN
      Given 該分鐘大盤資料查詢 timeout
      When 執行一次盤中輪詢流程
      Then 該分鐘不應發送 LINE
      And 該分鐘不得補發過期訊號
      And system_logs 應新增 level "WARN" with event "MARKET_TIMEOUT"

    @TP-INT-008
    Scenario: [TP-INT-008] DB 不可寫時 fallback pending_delivery.jsonl 成功
      Given LINE 已發送成功
      And DB 無法寫入 pending_delivery_ledger
      When 執行一次盤中輪詢流程
      Then 應寫入 "logs/pending_delivery.jsonl"
      And system_logs 應記錄 fallback 事件

    @UAT-001 @TP-UAT-001
    Scenario: [UAT-001] 手動門檻觸發時 60 秒內收到 LINE 群組通知
      Given watchlist 設定 "2330 fair=1500 cheap=1000 enabled=1"
      And 市價在 60 秒內達到 "<=1500" 或 "<=1000"
      When 執行盤中輪詢流程
      Then LINE 群組應在 60 秒內收到通知

    @UAT-002 @TP-UAT-002
    Scenario: [UAT-002] 相同 stock_no+stock_status 在 5 分鐘內不重複推送且 update_time 不更新
      Given "2330+status1" 在第 N 分鐘已成功通知
      When 第 N+1 分鐘（<300 秒）再命中 "2330+status1"
      Then LINE 不應再次發送
      And message.update_time 不應變動

    @UAT-003 @TP-UAT-003
    Scenario: [UAT-003] message 表應可查詢核心欄位
      Given 有至少一筆成功通知
      When 查詢 message 表
      Then 每筆應有 stock_no, message, stock_status, update_time

    @UAT-006 @TP-UAT-006
    Scenario: [UAT-006] 同分鐘多股票多方法觸發時只發一封彙總訊息
      Given 同分鐘有多檔股票命中且每檔可能命中多方法
      When 執行該分鐘通知
      Then LINE 僅發送 1 封彙總訊息
      And 訊息應列出所有命中股票與方法

    @UAT-007 @TP-UAT-007
    Scenario: [UAT-007] 同分鐘同股票同時符合 status 1 與 2 時僅呈現 status 2
      Given 股票 "2330" 在同分鐘同時符合 status 1 與 status 2
      When 產生彙總訊息
      Then 該股票應僅以 status 2 呈現與通知

  Rule: 每日 14:00 估值流程（TP-VAL-* + UAT-5）

    @TP-VAL-001 @UAT-005
    Scenario: [TP-VAL-001] 交易日 14:00 應執行估值並產生快照
      Given 今天是交易日
      And 現在時間為 "14:00"
      When 觸發日結估值 job
      Then valuation_snapshots 應新增各 stock x method 的快照

    @TP-VAL-002
    Scenario: [TP-VAL-002] 非交易日 14:00 不執行估值
      Given 今天是非交易日
      And 現在時間為 "14:00"
      When 觸發日結估值 job
      Then 不應新增任何 valuation_snapshots
      And system_logs 應記錄 skip/info

    @TP-VAL-003
    Scenario: [TP-VAL-003] 估值計算失敗不得覆蓋既有快照
      Given 昨日 valuation_snapshots 已存在
      And 今日某方法計算失敗
      When 觸發日結估值 job
      Then 既有快照不應被覆蓋
      And system_logs 應記錄錯誤

    @UAT-005 @TP-UAT-005
    Scenario: [UAT-005] 每交易日 14:00 估值任務執行一次且失敗不覆蓋
      Given 已設定至少一個 enabled valuation method
      When 在交易日 "14:00" 觸發估值
      Then 任務應執行 1 次
      And 計算失敗的方法不應覆蓋舊值

  Rule: 交易日與行情品質控管（UAT-4 + US-013 + PDD v0.6）

    @TP-TRD-001
    Scenario: [TP-TRD-001] 08:45 後若已有當日大盤新資料應視為可交易
      Given 當前時間為 "08:46"
      And 大盤資料來源回傳當日最新資料時間為 "08:45"
      When 執行開盤可交易判斷
      Then 判斷結果應為 "可交易"

    @TP-TRD-002
    Scenario: [TP-TRD-002] 09:00 後仍無當日大盤新資料應視為不開市
      Given 當前時間為 "09:01"
      And 大盤資料來源無當日新資料
      When 執行開盤可交易判斷
      Then 判斷結果應為 "不開市"
      And 該分鐘輪詢應跳過通知流程

    @TP-TRD-003
    Scenario: [TP-TRD-003] 13:30 後屬非交易時段，輪詢應跳過
      Given 當前時間為 "13:31"
      When 排程器觸發每分鐘輪詢
      Then 系統應直接跳過輪詢與通知

    @UAT-004 @TP-UAT-004
    Scenario Outline: [UAT-004] 非交易時段不得進行盤中輪詢
      Given 當前時間條件為 "<time_case>"
      When 排程器觸發每分鐘輪詢
      Then 系統應直接跳過輪詢與通知

      Examples:
        | time_case                 |
        | Saturday                  |
        | Sunday                    |
        | Government holiday        |
        | No market update day      |
        | After 13:30 (post-market) |

    @US-013
    Scenario: [US-013] quote stale 時該分鐘不得觸發通知
      Given 股票 "2330" 最新報價時間距今超過 90 秒
      When 執行該分鐘訊號判斷
      Then 該股票該分鐘不應觸發通知
      And 該分鐘不得補發過期訊號
      And system_logs 應新增 "STALE_QUOTE" WARN

    @US-013
    Scenario: [US-013] 多來源報價衝突超門檻時不得觸發通知
      Given 股票 "2330" 來源 A 與來源 B 價差超過衝突門檻
      When 執行該分鐘訊號判斷
      Then 該股票該分鐘不應觸發通知
      And 該分鐘不得補發過期訊號
      And system_logs 應新增 "DATA_CONFLICT" WARN

  Rule: 重啟後一致性與冪等（US-012 + UAT-10/11）

    @US-012 @UAT-010 @TP-UAT-010
    Scenario: [US-012/UAT-010] 服務重啟後已送出的同分鐘訊號不得再次發送
      Given 分鐘桶 "2026-04-10 10:21" 的 "2330+status2" 已成功發送
      And 服務在 10:22 重啟
      When 系統恢復並重新進入輪詢
      Then "2026-04-10 10:21" 的事件不得重複發送

    @US-012 @UAT-010 @TP-UAT-010
    Scenario: [US-012/UAT-010] 重啟後補償中事件應續跑而非重送
      Given 存在 "2026-04-10 10:21" 的補償項 status "PENDING"
      And 該分鐘 LINE 先前已成功送達
      And 服務重啟
      When 補償 worker 啟動
      Then 應僅執行 message 回補
      And 不得再次發送該分鐘 LINE

    @UAT-011 @TP-UAT-011
    Scenario: [UAT-011] stale 或 data conflict 分鐘不得通知且需有 WARN 證據
      Given 某分鐘存在 stale quote 或 data conflict
      When 執行該分鐘流程
      Then 該分鐘 LINE 發送次數應為 0
      And system_logs 應存在對應 WARN 記錄

  Rule: 時間桶與 KPI 一致性（TP-BKT-* + TP-KPI-*）

    @TP-BKT-001
    Scenario: [TP-BKT-001] 所有 minute_bucket 應由 TimeBucketService 單一入口產生
      Given 系統時間為 "2026-04-10T10:21:37+08:00"
      When 產生 minute_bucket
      Then 只能透過 TimeBucketService 產生 "2026-04-10 10:21"

    @TP-KPI-001
    Scenario: [TP-KPI-001] 通知準確率 KPI 需在排除資料源中斷分鐘後 >= 99%
      Given 統計窗口內總訊號分鐘為 1000
      And 資料源中斷分鐘為 20
      And 正確通知分鐘為 972
      When 計算通知準確率
      Then 分母應為 980
      And 準確率應為 99.18%
      And KPI 驗證結果應為 "pass"
