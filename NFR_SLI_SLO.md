# NFR_SLI_SLO - Stock Monitoring System

版本：v0.2  
日期：2026-04-17  
來源基準：`PDD_Stock_Monitoring_System.md`（v1.2）、`EDD_Stock_Monitoring_System.md`（v1.2）

## 1. 文件目的
把 PDD/EDD 的非功能需求量化為可監控的 SLI/SLO，作為 BDD 驗收與營運門檻。

## 2. 非功能需求摘要
1. 時區固定 `Asia/Taipei`。
2. 資料源短暫異常不致整體崩潰。
3. 通知與落盤可追蹤，可補償。
4. 估值方法可插拔且版本化。
5. 所有出站 LINE 訊息文案（彙總/摘要/觸發列/測試推播）可模板化調整，不需修改主流程程式碼。
6. **跨平台**：系統需在 macOS 14+（Apple Silicon / Intel）與 Windows 10/11 + Python 3.11+ 均可正常啟動並完整運作（FR-20）。

## 3. SLI 定義
| SLI | 定義 | 計算方式 |
|---|---|---|
| `notification_latency_p95` | 通知延遲 P95 | `send_success_ts - signal_trigger_ts` |
| `notification_accuracy` | 通知準確率 | `correct_minutes / eligible_minutes` |
| `duplicate_suppression_rate` | 冷卻抑制效果 | `1 - duplicate_sent / duplicate_triggered` |
| `minute_single_send_rate` | 每分鐘單封率 | `single_send_minutes / minutes_with_send` |
| `compensation_reconcile_rate` | 補償成功率 | `reconciled / (reconciled + failed)` |
| `market_data_skip_ratio` | 因資料品質跳過比率 | `(timeout+stale+conflict)/all_polled_minutes` |

## 4. SLO 目標
| SLO | Target |
|---|---|
| 通知延遲 | `notification_latency_p95 <= 60s` |
| 通知準確率 | `notification_accuracy >= 99%`（排除資料源中斷分鐘） |
| 重複通知控制 | 同 `stock_no+stock_status` 300 秒內重複發送 `= 0` |
| 每分鐘單封 | `minute_single_send_rate = 100%` |
| 補償完成率 | `compensation_reconcile_rate >= 99%`（24h 窗口） |

## 5. 錯誤預算（Error Budget）
1. 以月為單位：
   - 通知準確率 99% 代表可容許 1% 不準確分鐘（不含資料源中斷分鐘）。
2. 若連續 2 天低於 SLO：
   - 凍結新功能，優先修復可靠性。

## 6. 與 BDD 測試對照
1. `TP-KPI-001` 對應 `notification_accuracy` 分母排除規則。
2. `TP-UAT-001` 對應通知延遲。
3. `TP-UAT-002` 對應重複通知控制。
4. `TP-INT-001` / `TP-UAT-006` 對應每分鐘單封率。
5. `TP-INT-005` / `TP-INT-006` 對應補償 SLI。

## 7. 量測落地建議
1. 每筆訊號保存 `trigger_time_utc` 與 `send_result_time_utc`。
2. 每分鐘輸出聚合監控記錄（成功/失敗/跳過原因）。
3. 以每日批次產生 KPI 報告（供 TEST_PLAN/UAT 簽核）。

## 8. 觸發告警門檻
1. `notification_latency_p95 > 60s` 連續 3 個觀測窗。
2. `pending_delivery_count > 0` 持續超過 10 分鐘。
3. `market_timeout_count` 異常暴增（超過近 7 日平均 3 倍）。
