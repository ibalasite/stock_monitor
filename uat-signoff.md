# UAT Sign-off

## 1. 文件資訊
- 專案: Stock Monitoring System
- 版本: Phase 1 + FR-19 全市場掃描對齊版
- 建立時間: 2026-04-17 +08:00

## 2. UAT 範圍
對照 `TP-UAT-001` ~ `TP-UAT-014`、`TP-UAT-016`（來源: `TEST_PLAN.md` / `features/*.feature`）

## 3. 驗證結果摘要
- 自動化測試: `PASS`
- 測試總數: `327 passed`
- Coverage Gate: `100% PASS`
- 結論: 進入 UAT 簽核階段條件已滿足

## 4. UAT 條目簽核清單

| UAT ID | 項目 | 結果 |
|---|---|---|
| TP-UAT-001 | 手動門檻觸發 60 秒內通知 | Pass |
| TP-UAT-002 | 5 分鐘冷卻不重複推播 | Pass |
| TP-UAT-003 | message 核心欄位可查 | Pass |
| TP-UAT-004 | 非交易時段不輪詢 | Pass |
| TP-UAT-005 | 交易日 14:00 估值執行 | Pass |
| TP-UAT-006 | 同分鐘多股票多方法單封彙總 | Pass |
| TP-UAT-007 | 同分鐘 1/2 同時命中僅通知 2 | Pass |
| TP-UAT-008 | LINE 成功 DB 失敗可補償且不重複 | Pass |
| TP-UAT-009 | LINE 參數錯誤 fail-fast | Pass |
| TP-UAT-010 | 重啟後同分鐘不得重送 | Pass |
| TP-UAT-011 | stale/conflict 分鐘不通知且有 WARN | Pass |
| TP-UAT-012 | 三方法每日估值皆嘗試執行，資料不足方法 skip 且不覆蓋舊快照 | Pass |
| TP-UAT-013 | 開盤第一個可交易分鐘先發監控設定摘要且同日不重複 | Pass |
| TP-UAT-014 | 所有 LINE 出站訊息皆透過模板渲染且無硬編碼最終文案 | Pass |
| TP-UAT-016 | `scan-market` CLI：watchlist upsert + CSV 產出 + 無 LINE 推播 | Pass |

## 5. 簽核欄位

| 角色 | 姓名 | 決議 | 日期 | 備註 |
|---|---|---|---|---|
| Product Owner | ibala | Ready for Sign-off | 2026-04-17 | 已納入 FR-19 全市場掃描驗收條件 |
| QA | ibala | Ready for Sign-off | 2026-04-17 | BDD + 全量測試（含 TP-UAT-016）已通過 |
| Engineering Lead | ibala | Ready for Sign-off | 2026-04-17 | CI/coverage gate=100%，可進入 UAT |

## 6. 備註
- 本文件為「可簽核版本」，最終上線仍需完成人工 UAT 實際操作與正式簽名。
- FR-19 (`TP-UAT-016`) 已納入本次簽核範圍。
