"""UAT scenario declarations."""

from __future__ import annotations


UAT_SCENARIOS = {
    "TP-UAT-001": {
        "title": "手動門檻觸發 60 秒內通知",
        "preconditions": ["watchlist 已有 2330 fair=1500 cheap=1000"],
        "steps": ["執行盤中輪詢並命中門檻"],
        "expected": ["LINE 在 60 秒內送達"],
    },
    "TP-UAT-002": {
        "title": "5 分鐘冷卻不重複推播",
        "preconditions": ["已有 2330+status1 成功通知"],
        "steps": ["300 秒內再次命中同鍵"],
        "expected": ["不重送且 update_time 不更新"],
    },
    "TP-UAT-003": {
        "title": "message 核心欄位可查",
        "preconditions": ["至少一筆通知已寫入"],
        "steps": ["查詢 message 表"],
        "expected": ["存在 stock_no/message/stock_status/update_time"],
    },
    "TP-UAT-004": {
        "title": "非交易時段不輪詢",
        "preconditions": ["當前為週末/假日/收盤後"],
        "steps": ["觸發每分鐘排程"],
        "expected": ["輪詢與通知皆跳過"],
    },
    "TP-UAT-005": {
        "title": "交易日 14:00 估值執行",
        "preconditions": ["今日為交易日"],
        "steps": ["14:00 觸發估值工作"],
        "expected": ["估值成功寫入；失敗不覆蓋舊值"],
    },
    "TP-UAT-006": {
        "title": "同分鐘多股票多方法單封彙總",
        "preconditions": ["同分鐘存在多筆可發事件"],
        "steps": ["執行通知流程"],
        "expected": ["僅發一封且含全部命中"],
    },
    "TP-UAT-007": {
        "title": "同分鐘 1/2 同時命中僅通知 2",
        "preconditions": ["同股票同分鐘同時命中 fair 與 cheap"],
        "steps": ["套用優先級規則"],
        "expected": ["最終通知狀態為 2"],
    },
    "TP-UAT-008": {
        "title": "LINE 成功 DB 失敗可補償且不重複",
        "preconditions": ["LINE 成功且 DB transaction 失敗"],
        "steps": ["檢查 pending ledger 並執行回補"],
        "expected": ["回補成功且不重複發送"],
    },
    "TP-UAT-009": {
        "title": "LINE 參數錯誤 fail-fast",
        "preconditions": ["LINE token 或 group id 缺失/無效"],
        "steps": ["啟動服務"],
        "expected": ["啟動失敗且錯誤可操作"],
    },
    "TP-UAT-010": {
        "title": "重啟後同分鐘不得重送",
        "preconditions": ["同分鐘事件已發送完成"],
        "steps": ["重啟服務並恢復流程"],
        "expected": ["不重複發送已送事件"],
    },
    "TP-UAT-011": {
        "title": "stale/conflict 分鐘不通知且有 WARN",
        "preconditions": ["存在 stale quote 或 data conflict"],
        "steps": ["執行該分鐘流程"],
        "expected": ["LINE 發送次數 0 且有對應 WARN"],
    },
}

