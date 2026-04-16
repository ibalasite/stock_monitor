# TEST_PLAN - Stock Monitor System

版本：v1.1  
日期：2026-04-17  
依據文件：[EDD_Stock_Monitoring_System.md](c:/Projects/stock/EDD_Stock_Monitoring_System.md), [PDD_Stock_Monitoring_System.md](c:/Projects/stock/PDD_Stock_Monitoring_System.md), [USER_STORY_ACCEPTANCE_CRITERIA.md](c:/Projects/stock/USER_STORY_ACCEPTANCE_CRITERIA.md)

## 1. 測試目標
1. 驗證系統符合 EDD 的業務規則與資料一致性要求。  
2. 驗證通知、冷卻、補償機制在失敗情境下不重複通知。  
3. 驗證每日 14:00 估值流程與方法版本規則可正確落地。  
4. 驗證交易時段邊界（08:45/09:00）、時間桶一致性與 KPI 計算。  
5. 驗證開盤第一個可交易分鐘會先發送「監控設定摘要」且同日不重複。  
6. 以 TDD 流程實作（先測試再主程式），並達成 coverage gate 100%。  
7. 驗證所有出站 LINE 訊息文案由 Template 渲染（非程式硬編碼），且模板變更可在不改主流程下生效。  
8. 驗證 Code Review 定版的 16 項改善行動（EDD §13）中具可驗證行為的項目均有對應測試案例。
9. 驗證雙行情來源（TWSE + Yahoo Finance）Composite Adapter 的 Freshness-First 邏輯與 Yahoo 失敗容錯（EDD §13.5 / PDD FR-15，TP-ADP-001~004）。
10. 驗證全市場估值掃描（FR-19）：清單擷取、三分類邏輯、watchlist upsert、CSV 輸出（TP-SCAN-001~006、TP-UAT-016）。
11. 驗證 macOS / Windows 跨平台相容（FR-20）：pathlib 路徑、SIGTERM 優雅關閉、bash 腳本、launchd plist 格式（TP-PLAT-001~005、TP-UAT-017）。

## 2. 測試範圍
### In Scope
1. Schema 與 migration（含 constraint/index/partial unique index）。  
2. Domain policy（`PriorityPolicy`, `CooldownPolicy`, `SignalPolicy`）。  
3. 盤中每分鐘流程與單一彙總訊息。  
4. LINE 發送成功/失敗路徑。  
5. `pending_delivery_ledger` 補償流程。  
6. 交易日判斷與開盤資料判斷（含 08:45/09:00 邊界）。  
7. 每日 14:00 估值計算與快照寫入。  
8. `TimeBucketService` 單一入口規則。  
9. 通知延遲與準確率 KPI。  
10. 開盤監控設定摘要通知（股票/方法/fair/cheap 與同日去重）。  
11. LINE 模板載入與渲染失敗處理（`TEMPLATE_NOT_FOUND` / `TEMPLATE_RENDER_FAILED`），涵蓋彙總/摘要/觸發列/測試推播。  
12. 全市場估值掃描（FR-19）：`AllListedStocksPort`/`TwseAllListedStocksProvider` 清單擷取、三分類邏輯（below_cheap / above_cheap_below_fair / uncalculable）、watchlist upsert、CSV 輸出。  

### Out of Scope
1. 自動下單。  
2. 多節點分散式部署。  
3. 非台股市場整合。  

## 3. 測試環境
1. OS：Windows（與目前開發環境一致）。  
2. DB：SQLite（需 JSON1，`PRAGMA foreign_keys=ON`）。  
3. Timezone：`Asia/Taipei`。  
4. LINE：測試 channel token + 測試群組。  
5. Market data：Mock provider + 一組公開來源 smoke test。  

## 4. 進出場條件
### Entry Criteria
1. migration 可完整執行。  
2. `.env` 已設定必要參數。  
3. Mock LINE 與 Mock MarketData 可用。  
4. 測試框架已啟用 coverage 報告（line/branch/function/statements）。  

### Exit Criteria
1. Critical/High 缺陷為 0。  
2. PDD UAT 14 條全數通過。  
3. 補償流程與冷卻流程測試全數通過。  
4. Coverage 四項指標皆為 100%。  

## 5. 測試資料
1. 股票：`2330`, `2317`。  
2. 手動門檻：`2330 fair=1500, cheap=1000`; `2317 fair=145, cheap=130`。  
3. 估值方法：`emily_composite_v1`, `oldbull_dividend_yield_v1`, `raysky_blended_margin_v1`。  
4. 時間桶：`YYYY-MM-DD HH:mm`（Asia/Taipei）。  
5. 行情來源最大重試次數：`MAX_RETRY_COUNT=3`。  
6. 報價新鮮度門檻：`STALE_THRESHOLD_SEC=90`。  

## 6. 測試矩陣（需求追蹤）
> **BDD 覆蓋規則**：類型含 `BDD` 的測試案例須有對應 `.feature` 場景與 `@tag`。類型為 `Unit` 或 `Migration` 者以 pytest 單元測試覆蓋，不要求 BDD 場景（例：TP-DB-006, TP-VAL-007, TP-SEC-003, TP-ARCH-005/006, TP-CODE-001~004 均屬此類）。
| 測試ID | 需求對應 | 類型 | 驗收重點 |
|---|---|---|---|
| TP-DB-001 | EDD §6.1 | Migration | `watchlist` 約束與欄位型別正確 |
| TP-DB-002 | EDD §6.2 | Migration | `ux_method_single_enabled` 生效 |
| TP-DB-003 | EDD §6.4 | Migration | `message` unique/format/json 約束生效 |
| TP-DB-004 | EDD §6.5 | Migration | `pending_delivery_ledger` 可正常寫讀 |
| TP-DB-005 | EDD §6.3 | Migration | `valuation_snapshots` 唯一鍵需含 `method_version` |
| TP-DB-006 | EDD §6.1 / FR-18 | Migration | `watchlist` 需有 `stock_name TEXT NOT NULL DEFAULT ''` 欄位，可 UPDATE，migration 補欄正確（2 子案例：① fresh schema 含欄；② 舊 DB 缺欄 → `apply_schema()` 自動補欄，既有資料保留） |
| TP-ENV-001 | EDD §8.1 | Integration | JSON1 不可用時服務 fail-fast |
| TP-ENV-002 | EDD §8.1 | Integration | `PRAGMA foreign_keys=ON` 與 health check 驗證 |
| TP-ENV-003 | PDD §7 FR-09 / EDD §7.1 | Integration | LINE 設定鍵値驗證與錯誤訊息（規範名 `LINE_*`、別名 `CHANNEL_*/TARGET_*`、無效値三組 Examples） |
| TP-POL-001 | EDD §2.2 | Unit | 同時命中 1/2 只保留 2 |
| TP-POL-002 | EDD §2.4 | Unit | 5 分鐘冷卻判斷正確 |
| TP-POL-003 | EDD §7.4 | Unit | `last_sent_at IS NULL` 可發送 |
| TP-POL-004 | EDD §2.4 / §7.3 | Unit | 同分鐘冪等鍵僅由 `stock_no+minute_bucket` 組成 |
| TP-POL-005 | EDD §2.3 | Unit | 同股票同分鐘多方法皆 status 1，只產生一個股票層級訊號（methods_hit 含全部） |
| TP-POL-006a | EDD §2.4 | Unit | 最近 status 1 發送後 60s，status 2 冷卻鍵不同应可發 |
| TP-POL-006b | EDD §2.4 | Unit | 最近 status 1 發送後 60s，同 status 1 不同方法仍應被擋 |
| TP-INT-001 | EDD §4.1 | Integration | 同分鐘多股票只發 1 封 LINE |
| TP-INT-002 | EDD §7.3 | Integration | 同分鐘 `status=1 -> status=2` 升級成功 |
| TP-INT-003 | EDD §7.3 | Integration | 同狀態但內容差異可更新 |
| TP-INT-004 | EDD §7.3 | Integration | LINE 失敗不寫 `message` |
| TP-INT-005 | EDD §7.5 | Integration | LINE 成功 + DB 失敗 -> 建立補償紀錄 |
| TP-INT-006 | EDD §7.5 | Integration | 補償回補成功後 `RECONCILED` 且不重複通知 |
| TP-INT-007 | EDD §5.2 / PDD §7 FR-02 | Integration | 大盤資料逾時 -> 該分鐘跳過通知、寫 WARN、且不得補發 |
| TP-INT-008 | EDD §7.5 | Integration | DB 不可寫時 fallback `pending_delivery.jsonl` 成功 |
| TP-INT-009 | EDD §7.3 | Integration | message 批次失敗需整批 rollback（0 筆） |
| TP-INT-010 | PDD §7 FR-02 | Integration | 行情來源短暫失敗在重試上限內成功可繼續該分鐘流程 |
| TP-INT-011 | PDD §7 FR-02 | Integration | 行情來源重試耗盡仍失敗時跳過且不得補發 |
| TP-INT-012 | PDD §7 FR-13/FR-14 | Integration | 開盤第一個可交易分鐘發送監控設定摘要（模板渲染），且同日僅一次 |
| TP-TPL-001 | PDD §7 FR-14 / EDD §7.6 | Integration | 變更 opening summary template 後，不改主流程程式即可改變 LINE 文案 |
| TP-TPL-002 | PDD §7 FR-14 / EDD §7.6 | Integration | 模板缺失或渲染失敗時記錄 `TEMPLATE_*` 錯誤且不得默默回退未知格式 |
| TP-TPL-003 | PDD §7 FR-14 / EDD §7.6 | Integration | 每分鐘彙總通知與單股觸發列需透過 `template_key + context` 產生，不得直接硬編碼最終文案 |
| TP-TPL-004 | PDD §7 FR-14 / EDD §7.6 | Integration | 若提供測試推播功能，測試推播訊息也必須走模板渲染 |
| TP-TRD-001 | EDD §5.2 | Integration | 08:45 後有大盤新資料 -> 可交易 |
| TP-TRD-002 | EDD §5.2 | Integration | 09:00 後無大盤新資料 -> 不開市 |
| TP-TRD-003 | EDD §8 / UAT-4 | Integration | 13:30 後（TRADING_END）应判定為非交易時段，輪詢應跳過 |
| TP-BKT-001 | EDD §7.3 | Unit | `TimeBucketService` 為 minute_bucket 唯一來源 |
| TP-KPI-001 | PDD §3.2 | Unit | 準確率分母需排除資料源中斷分鐘 |
| TP-VAL-001 | EDD §4.2 | Integration | 14:00 交易日執行估值 |
| TP-VAL-002 | EDD §4.2 | Integration | 非交易日不執行估值 |
| TP-VAL-003 | EDD §6.3 | Integration | 估值失敗不覆蓋舊快照 |
| TP-VAL-007 | PDD §7 FR-18 / EDD §6.1 | Integration | 14:00 估値時將股票中文名稱存入 `watchlist.stock_name` |
| TP-VAL-008 | EDD §13.3 CR-VAL-01 | Unit | 14:01 時估値仸然執行（不被精確時間門欄排除）；13:59 時則跨過 || TP-DAEMON-001 | EDD §13.3 CR-DAEMON-01 | Unit | daemon loop body 拋出 exception 時 daemon 不崩潰，寫入 `DAEMON_LOOP_EXCEPTION` ERROR log 後繼續執行 |
| TP-TPL-005 | EDD §13.3 CR-TPL-01 | Unit | `render_line_template_message` 和 `LineTemplateRenderer.render()` 對相同模板目錄第二次呼叫不再重建 `Environment`（證明 `_env_cache` 正常工作） || TP-VAL-004 | PDD §7 FR-11 / EDD §9.1 | Integration | 三方法同日可同時產生快照 |
| TP-VAL-005 | PDD §7 FR-12 / EDD §9.2 | Integration | 單方法資料不足僅該方法 skip，不影響其它方法 |
| TP-VAL-006 | PDD §7 FR-12 / EDD §6.7 | Integration | 主來源失敗時可切換備援並成功估值 |
| TP-SCAN-001 | EDD §14.2 / PDD FR-19 | Unit | `TwseAllListedStocksProvider.get_all_listed_stocks()` 成功回傳 TSE+OTC 清單（含 stock_no/stock_name/yesterday_close/market）；清單不可為空 |
| TP-SCAN-002 | EDD §14.2 / PDD FR-19 | Unit | HTTP 失敗 retry 3 次後拋例外；取回清單為空時亦拋例外，不靜默回傳空清單 |
| TP-SCAN-003 | EDD §14.3 / PDD FR-19 | Integration | `run_market_scan_job` 三分類正確：`below_cheap`、`above_cheap_below_fair`、`uncalculable` 各自分入正確桶 |
| TP-SCAN-004 | EDD §14.3 / PDD FR-19 | Integration | `below_cheap` 股票 upsert watchlist；已有 `enabled=0` 者 fair/cheap/name 更新但 `enabled` 不被強制改 |
| TP-SCAN-005 | EDD §14.3 / PDD FR-19 | Integration | `above_cheap_below_fair` 股票輸出至 `scan_results_above_cheap.csv`，含 8 必要欄位 |
| TP-SCAN-006 | EDD §14.3 / PDD FR-19 | Integration | `uncalculable` 輸出 `scan_results_uncalculable.csv`（含 skip_reasons）；個別例外不中斷掃描，寫 `MARKET_SCAN_STOCK_ERROR` ERROR log |
| TP-SCAN-007 | EDD §14.5 / PDD FR-19 | Integration | `scan-market` CLI 必須注入 DB 啟用方法；若 `enabled=1` 方法數為 0 則 fail-fast（非 0 exit code）且不輸出 CSV |
| TP-UAT-001 | PDD §12 UAT-1 | UAT | 手動門檻觸發 60 秒內通知 |
| TP-UAT-002 | PDD §12 UAT-2 | UAT | 5 分鐘冷卻不重複推播 |
| TP-UAT-003 | PDD §12 UAT-3 | UAT | message 核心欄位可查 |
| TP-UAT-004 | PDD §12 UAT-4 | UAT | 非交易時段跳過盤中輪詢 |
| TP-UAT-005 | PDD §12 UAT-5 | UAT | 14:00 估值執行且失敗不覆蓋 |
| TP-UAT-006 | PDD §12 UAT-6 | UAT | 同分鐘多股票多方法只發一封 |
| TP-UAT-007 | PDD §12 UAT-7 | UAT | 同分鐘 1/2 同時命中僅通知 2 |
| TP-UAT-008 | PDD §12 UAT-8 | UAT | LINE 成功 DB 失敗可補償且不重複 |
| TP-UAT-009 | PDD §12 UAT-9 | UAT | LINE 必填參數錯誤 fail-fast |
| TP-UAT-010 | PDD §12 UAT-10 | UAT | 重啟後不得重複通知同分鐘事件 |
| TP-UAT-011 | PDD §12 UAT-11 | UAT | stale/conflict 分鐘不得通知且有 WARN |
| TP-UAT-012 | PDD §12 UAT-12 | UAT | 三方法每日皆嘗試估值，資料不足方法 skip 且不覆蓋舊快照 |
| TP-UAT-013 | PDD §12 UAT-13 | UAT | 開盤第一個可交易分鐘先發監控設定摘要且同日不重複 |
| TP-UAT-014 | PDD §12 UAT-14 | UAT | 所有 LINE 出站訊息皆透過模板渲染，且程式碼無硬編碼最終文案 |
| TP-UAT-016 | PDD §12 UAT-16 / FR-19 | UAT | `scan-market` CLI 執行後：watchlist 正確 upsert、兩個 CSV 正確產出、全程無 LINE 推播 |
| TP-UAT-017 | PDD §12 UAT-17 / FR-20 / US-021 | UAT | macOS 端對端冒煙：pytest 全綠 + coverage 100%；start/stop daemon 腳本可用；SIGTERM 乾淨退出；plist 通過 plutil lint |
| TP-PLAT-001 | EDD §15.2 CR-PLAT-01 / FR-20 | Unit | 靜態掃描 `stock_monitor/` 下所有 `.py` 檔，確認無 `os.path.join`、`"/"+`、`"\\"+` 硬編碼路徑分隔符 |
| TP-PLAT-002 | EDD §15.3 CR-PLAT-02 / FR-20 | Unit | `_install_signal_handlers` 存在於 `daemon_runner`；在模擬 `sys.platform="win32"` 條件下不安裝 SIGTERM handler，不拋 AttributeError |
| TP-PLAT-003 | EDD §15.3 / FR-20 | Integration | daemon 收到 SIGTERM 後，stop_event 被設定，主迴圈在當前週期結束後退出，exit code 為 0 |
| TP-PLAT-004 | EDD §15.5 CR-PLAT-03 / FR-20 | Unit | `scripts/com.stock_monitor.daemon.plist` 存在且 `plutil -lint` 驗證通過 |
| TP-PLAT-005 | EDD §15.4 CR-PLAT-03 / FR-20 | Unit | `scripts/start_daemon.sh` 與 `scripts/stop_daemon.sh` 存在且具執行權限（`-x`） |
| TP-SEC-001 | EDD §13.1 CR-SEC-01 | Unit | `LinePushClient` 的 `repr()` 輸出不得包含 token 明文 |
| TP-SEC-002 | EDD §13.1 CR-SEC-03 / §13.3 CR-CODE-05 | Unit | 無效時區名稱引發啟動時 `ValueError`，不得靜默 fallback UTC |
| TP-SEC-003 | EDD §13.1 CR-SEC-04 / EDD §13.5 CR-ADP-04 | Unit | HTTP 回應讀取有大小上限，超大回應不得無限占用記憶體（TWSE adapter + Yahoo adapter 均涵蓋；Yahoo `MAX_RESPONSE_BYTES` 需 ≤ 1 MB，與 TWSE 相同） |
| TP-SEC-004 | EDD §13.1 CR-SEC-05 | Unit | `LinePushClient.send()` HTTP 回應讀取也必須受 `MAX_RESPONSE_BYTES`（1 MB）上限限制，與 TWSE/Yahoo adapter 一致 |
| TP-ARCH-001 | EDD §13.2 CR-ARCH-01/02 | Unit | `ValuationCalculator` 可從 `stock_monitor.application.valuation_calculator` import；`app.py` 不含計算邏輯；`scenario_case` 分支不存在於生產估值流程 |
| TP-ARCH-002 | EDD §13.2 CR-ARCH-03 | Unit | `render_line_template_message` 在整個專案內只有一份定義，來源為 `message_template.py` |
| TP-ARCH-003 | EDD §13.3 CR-CODE-03 | Unit | `MinuteCycleConfig` dataclass 存在於 `runtime_service.py`，`run_minute_cycle` 接受它作為設定入口 |
| TP-ARCH-004 | EDD §13.2 CR-ARCH-06 / §13.3 CR-CODE-06 | Integration | 開盤摘要冪等狀態儲存於 DB（非 log 字串比對）；daemon 在 09:01 後重啟時數同日仍可發送開盤摘要 |
| TP-ARCH-005 | EDD §13.2 CR-ARCH-04 | Unit | `app.py` 不得定義 `_run_daemon_loop` 及 `_build_runtime`；只保留入口與指令路由 |
| TP-ADP-001 | EDD §13.5 CR-ADP-01 / PDD §7 FR-15 | Unit | `YahooFinanceMarketDataProvider` HTTP 4xx/5xx/timeout 失敗時寫 WARN log 並回傳空 dict，不 raise exception 影響主流程 |
| TP-ADP-002 | EDD §13.5 CR-ADP-02 / PDD §7 FR-15 | Unit | `CompositeMarketDataProvider` 以 `tick_at` 較新者勝；相等時 TWSE 優先；兩者皆無時不加入結果 dict（呼叫端觸發 STALE_QUOTE）|
| TP-ADP-003 | EDD §13.5 CR-ADP-03 / PDD §7 FR-15 | Unit | `TwseRealtimeMarketDataProvider` quotes 含 `exchange` 欄位（`tse`/`otc`）；`_price_cache` 在 `a` 欄为空/`-` 時回傳最後已知委賣一；冷啟動 cache 空回傳無此股票 |
| TP-ADP-004 | EDD §13.5 CR-ADP-04 / EDD §13.1 CR-SEC-04 | Unit | `YahooFinanceMarketDataProvider` HTTP 回應受 `MAX_RESPONSE_BYTES` 限制；截斷後 JSON 無效時 WARN 且回傳空 dict |
| TP-NAME-001 | PDD §7 FR-18 / EDD §6.1 | Unit | TWSE 與 Yahoo adapter `get_realtime_quotes()` 回傳 dict 不含 `name` 欄位；`get_stock_names()` 方法存在並從 cache 回傳；`evaluate_manual_threshold_hits` 從 `watchlist_row["stock_name"]` 取名稱；`evaluate_valuation_snapshot_hits` 接受 `stock_name_map` 參數 |
| TP-NAME-002 | PDD §7 FR-18 / EDD §6.1 | Unit/BDD | `build_minute_rows` 接受 `stock_name_map` 參數，`display_label` 使用 DB 股票名稱（優先於 hit 內的 `stock_name`）；`run_minute_cycle` 觸發通知列包含 DB 名稱 `{stock_name}({stock_no})` |
| TP-NAME-003 | PDD §7 FR-18 / EDD §6.1 | Integration/BDD | 只有 14:00 估值 job（`run_daily_valuation_job`）才從行情 API 抓取並呼叫 `update_stock_names()` 寫回 DB；盤中 `run_minute_cycle` 不寫名稱 |
| TP-ARCH-006 | EDD §13.2 CR-ARCH-05 | Unit | `merge_minute_message` 需有生產呼叫點或標記為私有（`_merge_minute_message`） |
| TP-CODE-001 | EDD §13.3 CR-CODE-01 | Unit | `build_minute_rows` 中 `render_line_template_message` 呼叫次數 ≤ 1（統一單一 context 呼叫，消除三段重複） |
| TP-CODE-002 | EDD §13.3 CR-CODE-02 | Unit | `reconcile_pending_once` 函式簽名不含 `line_client` 參數（移除永遠不用的參數） |
| TP-CODE-003 | EDD §13.3 CR-CODE-04 | Unit | `aggregate_minute_notifications` 不使用 f-string 組裝觸發列；改用 `render_line_template_message` 渲染每列 |
| TP-CODE-004 | EDD §13.3 CR-CODE-06 | Integration | `_send_opening_summary_if_needed` 不受限於精確 `09:00`；任何交易分鐘且當日尚未發送時均可觸發 |

## 7. 詳細測試案例
| 測試ID | 前置條件 | 步驟 | 預期結果 |
|---|---|---|---|
| TP-DB-001 | DB 空庫 | 建立 `watchlist` 並寫入有效/無效資料 | 有效資料成功；無效（如 `cheap > fair`）失敗 |
| TP-DB-002 | DB 空庫 | 建立 `valuation_methods` 並插入同 `method_name` 兩筆 `enabled=1` | 第二筆失敗（unique constraint） |
| TP-DB-003 | DB 空庫 | 插入無效 `minute_bucket` 或無效 JSON | 寫入失敗（CHECK constraint） |
| TP-DB-004 | DB 空庫 | 寫入 `pending_delivery_ledger` 並查詢狀態索引 | 成功寫讀，索引查詢可用 |
| TP-DB-005 | DB 空庫 | 同 `stock_no+trade_date+method_name` 下，分別插入 `v1`、`v2`，再重複插入 `v1` | `v1`/`v2` 可共存，重複 `v1` 失敗（unique constraint） |
| TP-ENV-001 | 關閉/移除 JSON1 支援 | 啟動服務 | 啟動失敗且輸出明確 fail-fast 錯誤 |
| TP-ENV-002 | DB 啟動後 | 查 `PRAGMA foreign_keys;` 並打 health check | 回傳 `ON`，健康檢查通過 |
| TP-ENV-003 | 缺少/錯誤 LINE token/groupId | 以 `LINE_*` 或 `CHANNEL_*/TARGET_*` 任一命名啟動服務 | 缺少必要值時 fail-fast，錯誤訊息明確且不洩漏 token |
| TP-POL-001 | 準備同一股票同分鐘兩訊號 | 先餵 `status=1` 再餵 `status=2` | 輸出僅保留 `status=2` |
| TP-POL-002 | 建立既有 `update_time` | 分別測 `299s` 與 `301s` 間隔 | 299s 不發，301s 可發 |
| TP-POL-003 | 無歷史通知紀錄 | 執行冷卻判斷 | 視為可發送 |
| TP-POL-004 | 同股票同分鐘先後命中 `status=1/2` | 分別產生兩次同分鐘冪等鍵 | 兩次鍵值相同，且不含 `stock_status` |
| TP-INT-001 | Mock LINE、兩檔股票命中 | 執行一次 1 分鐘輪詢 | LINE 僅被呼叫一次，內容含兩檔 |
| TP-INT-002 | 先已有同分鐘 `status=1` 記錄 | 同分鐘觸發 `status=2` | `message` 升級為 `status=2` |
| TP-INT-003 | 已有同分鐘同狀態紀錄 | 同分鐘更新 `methods_hit/message` | 記錄內容更新為最終聚合結果 |
| TP-INT-004 | Mock LINE 回 500 | 執行輪詢 | `message` 不新增，`system_logs` 新增 ERROR |
| TP-INT-005 | Mock LINE 成功、Mock DB transaction fail | 執行輪詢 | `pending_delivery_ledger` 或 jsonl 有 PENDING（@UAT-008） |
| TP-INT-006 | 已存在 PENDING | 執行補償 worker | `message` 成功回補，ledger 轉 `RECONCILED`；再次執行不重送（證據同 TP-INT-005） |
| TP-INT-007 | Mock 大盤資料 timeout | 執行輪詢 | 不發 LINE，記錄 WARN，且該分鐘不得補發 |
| TP-INT-008 | Mock DB 寫入不可用 | 執行輪詢 | 建立 `pending_delivery.jsonl` 待補償項 |
| TP-INT-009 | 同分鐘兩筆 message 待寫 | 模擬第二筆寫入失敗 | transaction rollback，該分鐘 `message=0` 筆 |
| TP-INT-010 | Mock 行情來源首輪失敗次輪成功（重試上限=3） | 執行輪詢 | 該分鐘流程可繼續，且 logs 有 retry 記錄 |
| TP-INT-011 | Mock 行情來源重試耗盡仍失敗（超過3次） | 執行輪詢 | 該分鐘跳過、不通知、不寫 message、不得補發 |
| TP-INT-012 | 交易日第一個可交易分鐘且 watchlist/方法可用 | 觸發開盤摘要流程，再於同交易日重複觸發 | 首次發送 1 封摘要（由模板渲染，含股票/方法/fair/cheap）；同日第二次不重複發送 |
| TP-TPL-001 | 已有可用 template v1，且可切換到 template v2 | 不改主流程程式僅替換模板後觸發摘要通知 | LINE 內容跟隨模板變更 |
| TP-TPL-002 | 指定不存在模板 key 或模板語法錯誤 | 觸發通知渲染流程 | 記錄 `TEMPLATE_NOT_FOUND` 或 `TEMPLATE_RENDER_FAILED`，且不得送出未知格式 |
| TP-TPL-003 | 盤中有可發送事件且存在摘要/彙總/觸發列模板 | 執行一分鐘流程並檢查 message composition 呼叫鏈 | 所有送 LINE 內容均來自 template render，業務流程不直接拼最終文案 |
| TP-TPL-004 | 系統提供 test push CLI/腳本 且 test push template 已配置 | 觸發 test push | test push 訊息可送達且使用模板文案 |
| TP-TRD-001 | 時間 08:45 後 | 大盤有當日新資料 | 判定可交易 |
| TP-TRD-002 | 時間 09:00 後 | 大盤無當日新資料 | 判定不開市且該分鐘跳過通知 |
| TP-TRD-003 | 時間 13:31 | 輪詢觸發 | 判定非交易時段，直接跳過輪詢 |
| TP-POL-005 | 同股票同分鐘 3 個方法皆 status 1 | 進行股票層級聯合 | 只產生 1 個股票事件，methods_hit 包含全部方法 |
| TP-POL-006a | 2330+1 已在 60s 前發送 | 對 2330+2 執行冷卻判斷 | 冷卻鍵不同，應可發送 |
| TP-POL-006b | 2330+1 已在 60s 前發送 | 對另一方法產生的 2330+1 執行冷卻判斷 | 相同key（status相同），應被擋且不更新 update_time |
| TP-BKT-001 | 有系統時間輸入 | 產生 `minute_bucket` | 僅允許 `TimeBucketService` 產生，格式固定 |
| TP-KPI-001 | 準確率統計窗口資料 | 計算準確率 | 分母排除資料源中斷分鐘後再計算 |
| TP-VAL-001 | 交易日 14:00 | 觸發日結 job | 產生各方法估值快照 |
| TP-VAL-002 | 非交易日 14:00 | 觸發日結 job | 不執行估值，僅記錄 skip/info |
| TP-VAL-003 | 既有昨日估值 | 模擬今日計算失敗 | 不覆蓋昨日快照，記錄錯誤 |
| TP-VAL-004 | 三方法所需資料皆可用 | 觸發日結 job | `emily/oldbull/raysky` 各新增一筆快照 |
| TP-VAL-005 | raysky 缺 `current_assets` | 觸發日結 job | raysky 記錄 `SKIP_INSUFFICIENT_DATA`，其餘方法成功 |
| TP-VAL-006 | 主來源逾時、備援可用 | 觸發日結 job | 該方法可成功計算，且有來源切換 log |
| TP-UAT-001 | 系統可運行 | 依 PDD UAT-1 執行並記錄證據 | 通過且具通知時間證據 |
| TP-UAT-002 | 系統可運行 | 依 PDD UAT-2 執行並記錄證據 | 通過且具冷卻抑制證據 |
| TP-UAT-003 | 系統可運行 | 依 PDD UAT-3 執行並記錄證據 | 通過且具資料查詢證據 |
| TP-UAT-004 | 系統可運行 | 依 PDD UAT-4 執行（含週末/假日/無大盤/13:30後）並記錄證據 | 通過且具時段跳過證據 |
| TP-UAT-005 | 系統可運行 | 依 PDD UAT-5 執行並記錄證據 | 通過且具估值執行證據 |
| TP-UAT-006 | 系統可運行 | 依 PDD UAT-6 執行並記錄證據 | 通過且具單封彙總證據 |
| TP-UAT-007 | 系統可運行 | 依 PDD UAT-7 執行並記錄證據 | 通過且具狀態優先證據 |
| TP-UAT-008 | 系統可運行 | 依 PDD UAT-8 執行並記錄證據 | 通過且具補償與不重複證據 |
| TP-UAT-009 | 系統可運行 | 依 PDD UAT-9 執行並記錄證據 | 通過且具 fail-fast 錯誤證據 |
| TP-UAT-010 | 系統可運行 | 依 PDD UAT-10 執行並記錄證據 | 通過且具重啟去重證據 |
| TP-UAT-011 | 系統可運行 | 依 PDD UAT-11 執行並記錄證據 | 通過且具 WARN 證據，且該分鐘無補發 |
| TP-UAT-012 | 系統可運行 | 依 PDD UAT-12 執行並記錄證據 | 通過且具三方法執行/skip與不覆蓋舊值證據 |
| TP-UAT-013 | 系統可運行 | 依 PDD UAT-13 執行並記錄證據 | 通過且具開盤摘要內容與同日去重證據 |
| TP-UAT-014 | 系統可運行 | 依 PDD UAT-14 執行並記錄證據 | 通過且具彙總/摘要/觸發列/測試推播模板渲染證據 |
| TP-UAT-016 | 系統可運行、DB 已初始化、`valuation_methods` 含 3 方法 | 依 PDD UAT-16 執行 `scan-market` 並記錄證據 | 通過且具 watchlist upsert 截圖/log、兩個 CSV 內容、`system_logs` 無 `LINE_SEND` 事件 |
| TP-UAT-017 | macOS 環境、python3.11 已安裝、env vars 已設 | ① `python -m pytest -q tests`；② `bash scripts/start_daemon.sh`，確認 PID 寫入；③ `bash scripts/stop_daemon.sh`，確認乾淨退出；④ `plutil -lint scripts/com.stock_monitor.daemon.plist` | ① 全綠 + coverage 100%；② PID 檔存在，行程可見；③ 行程消失，PID 檔移除；④ 輸出 `OK` |
| TP-PLAT-001 | 無（靜態掃描） | `grep -rn "os\.path\.join\|\"\/\" *+\|\"\\\\\\\\\"\|os\.sep" stock_monitor/` | grep 回傳空（無硬編碼路徑） |
| TP-PLAT-002 | 無 | 從 `daemon_runner` import `_install_signal_handlers`；模擬 `sys.platform = "win32"` 呼叫之；確認無 `signal.SIGTERM` handler 被安裝 | import 成功；no AttributeError；`signal.getsignal(signal.SIGTERM)` 仍為預設值 |
| TP-PLAT-003 | daemon 可正常啟動 | 以 `threading.Timer` 在 daemon 啟動後 2 秒送 SIGTERM；觀察 loop 退出 | loop 在當前週期後退出；exit code 0；log 含「Daemon stopped」 |
| TP-PLAT-004 | macOS + `plutil` CLI 可用 | `plutil -lint scripts/com.stock_monitor.daemon.plist` | exit code 0；stdout 含 `OK` |
| TP-PLAT-005 | 無 | `os.access("scripts/start_daemon.sh", os.X_OK)` + `os.access("scripts/stop_daemon.sh", os.X_OK)` | 兩者皆回傳 `True` |
| TP-SEC-001 | `LinePushClient` 已載入 | 呼叫 `repr(LinePushClient(token="abc", to="xyz"))` | 輸出字串不包含 `abc`；欄位顯示為 `***` 或省略 |
| TP-SEC-002 | 無 | 以無效時區名稱（如 `"Invalid/Tz"`）初始化 `TimeBucketService` 或呼叫 `_resolve_timezone` | 立即 `raise ValueError`，不繼續執行，不返回 UTC |
| TP-SEC-003 | Mock HTTP server 回傳超過 1 MB 的 body | 分別呼叫 TWSE adapter 與 Yahoo adapter 的 URL 讀取路徑 | 兩個 adapter 均只讀取至各自的 `MAX_RESPONSE_BYTES`（≤ 1 MB），不引發 `MemoryError`；Yahoo `MAX_RESPONSE_BYTES = 1_048_576` |
| TP-ARCH-001 | 空 DB + 一組主僅依數據 | `from stock_monitor.application.valuation_calculator import ManualValuationCalculator`；檢查 `app.py` 不含計算類；執行一次估值確認無 `scenario_case` log 事件 | import 成功；`app.py` grep 不出計算專屬名稱；system_logs 無偽造 skip 事件 |
| TP-ARCH-002 | 專案已 import | 用 `grep -r "def render_line_template_message"` 掃瞄所有 `.py` | 僅在 `message_template.py` 出現一次；`runtime_service.py` 改用 import |
| TP-ARCH-003 | DB 設定 + Mock LINE | `from stock_monitor.application.runtime_service import MinuteCycleConfig`；以 `MinuteCycleConfig` 呼叫 `run_minute_cycle` | import 成功；函式接受 config 物件呼叫正常 |
| TP-ARCH-004 | 交易日 + watchlist 可用 | 設定「當日開盤摘要尚未發送」於 DB；在 09:01 後啟動 daemon 一次輪詢 | daemon 成功發送 1 封開盤摘要；冪等 key 從 DB 表讀取（不依賴 log LIKE 掃描） |
| TP-NAME-001 | TWSE / Yahoo adapter 啟動 | 呼叫 `get_realtime_quotes(["2330"])`，HTML/API 含名稱 "台積電_API"；再呼叫 `get_stock_names(["2330"])` | `quotes["2330"]` 不含 `"name"` key；`get_stock_names` 從 cache 回傳 `{"2330": "台積電_API"}`；`evaluate_manual_threshold_hits` 從 watchlist_row["stock_name"] 取名稱 |
| TP-NAME-002 | DB watchlist "2330" stock_name="台積電_DB"；API 報價不含 name；市場價跌破合理價 | 執行一次分鐘輪詢（`run_minute_cycle`） | 觸發通知列 `display_label` 為 `台積電_DB(2330)`；`build_minute_rows` 從 `stock_name_map` 取 DB 名稱，不再從 quote 取 |
| TP-NAME-003 | DB watchlist "2330" stock_name=""；行情來源對 "2330" 回傳名稱 "台積電" | 在交易日 14:00 觸發 `run_daily_valuation_job`（傳入 watchlist_repo + market_data_provider） | `watchlist.stock_name` 更新為 "台積電"；`run_minute_cycle` 本身不呼叫 `update_stock_names` |
| TP-SCAN-001 | stub provider 回傳 3 筆（tse 2 筆 + otc 1 筆） | 呼叫 `get_all_listed_stocks()` | 回傳 list，每筆含 stock_no/stock_name/yesterday_close/market，長度=3（不為空） |
| TP-SCAN-002 | mock HTTP 連續失敗 | 呼叫 `get_all_listed_stocks()` retry 3 次 | 拋例外（不回傳空清單）；若回傳清單為空亦拋例外 |
| TP-SCAN-003 | stub 3 支股票：A close≤cheap, B cheap<close≤fair, C 全方法 SKIP | 呼叫 `run_market_scan_job` | `below_cheap`=[A], `above_cheap_below_fair`=[B], `uncalculable`=[C] |
| TP-SCAN-004 | DB watchlist 已存在 2330 `enabled=0`；2330 掃描結果為 below_cheap | 執行 `run_market_scan_job` | 2330 upsert：fair/cheap/name 正確更新；`enabled` 維持 0（不強制覆蓋為 1） |
| TP-SCAN-005 | 1 支 above_cheap_below_fair 股票 | 執行 `run_market_scan_job`，output_dir 設為臨時目錄 | `scan_results_above_cheap.csv` 存在；欄位含 stock_no/stock_name/agg_fair_price/agg_cheap_price/yesterday_close/methods_computed/methods_skipped/skip_reasons |
| TP-SCAN-006 | 1 支全方法 SKIP；1 支在計算時 raise exception | 執行 `run_market_scan_job` | `scan_results_uncalculable.csv` 含 SKIP 股票及原因；exception 股票寫入 ERROR log `MARKET_SCAN_STOCK_ERROR`；整體掃描未中斷 |
| TP-SCAN-007 | DB `valuation_methods` 全部 disabled 或空表 | 執行 `python -m stock_monitor scan-market` | CLI 以非 0 exit code 結束，stderr/stdout 含 `MARKET_SCAN_METHODS_EMPTY`，且 output 目錄不產生掃描 CSV |

## 8. 失敗模式測試
1. LINE 成功、DB 失敗（最關鍵）。  
2. DB 批次寫入半途失敗 rollback（不得部分成功）。  
3. 補償回補失敗重試遞增。  
4. 大盤資料源 timeout/stale/data conflict。  
5. JSON1 不可用啟動 fail-fast。  
6. 服務重啟後重複通知風險。  
7. 行情來源重試耗盡仍失敗時，必須跳過該分鐘且不得補發。  
8. timeout/stale/data conflict 被跳過分鐘，不得補發過期訊號。  
9. 同一交易日因重啟/排程重複觸發造成開盤摘要重送。  
10. **SEC/ARCH 改善失敗模式**：token 輸出在 debug log，無效時區造成時間偏移，HTTP 超大回應耗盡記憶體。
## 9. 測試執行順序
1. Schema/Migration + Environment 測試（TP-DB-* + TP-ENV-*）。  
2. Unit 測試（TP-POL-* + TP-BKT-* + TP-KPI-*）。  
3. Integration 測試（TP-INT-* + TP-TRD-* + TP-VAL-*）。  
4. Security / Architecture 合約測試（TP-SEC-* + TP-ARCH-*）。  
5. UAT（TP-UAT-*）。  
6. Coverage gate（100%）檢查。  

## 10. TDD 實作策略（必遵守）
1. Red：先寫測試案例，確認 fail。  
2. Green：只寫最少主程式讓該案例通過。  
3. Refactor：重構並保持測試綠燈。  
4. 每完成一組案例即提交（小步提交）。  
5. 禁止先寫主流程再補測試。  

## 11. Coverage Gate（100%）
1. 指標：`lines=100%`, `branches=100%`, `functions=100%`, `statements=100%`。  
2. 低於門檻直接 fail（本機與 CI 同規則）。  
3. 覆蓋率排除需明確列名單並附理由（預設不排除）。  
4. 每次修 bug 必須先新增重現測試，再修程式。  

## 12. 缺陷分級
1. Critical：重複通知、漏通知、資料破壞。  
2. High：補償失效、冷卻失效、方法版本約束失效。  
3. Medium：訊息內容不完整、日志缺漏。  
4. Low：格式/文案問題。  

## 13. 產出物
1. `test-report.md`（總結與通過率）。  
2. `defect-log.md`（缺陷列表與修復狀態）。  
3. `uat-signoff.md`（UAT 14 條簽核）。  
