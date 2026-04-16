# 01 — System Context（C4 Level 1）

> **C4 L1** 描述系統與外部世界的邊界。  
> 對齊 EDD §1、§3.3、§7。

---

## 1.1 系統說明

| 項目 | 說明 |
|---|---|
| 系統名稱 | Stock Monitor（台股監控與通知系統） |
| 部署形式 | 本機 Windows 單機 Python 程序 |
| 核心功能 | 盤中每分鐘价格監控 + LINE 群組通知 + 每日 14:00 估值日結 |
| 外部依賴 | TWSE MIS API、Yahoo Finance TW HTML、LINE Messaging API、SQLite（本機） |

---

## 1.2 系統情境圖

```mermaid
flowchart TD
    %% ── 使用者 ──────────────────────
    OPER["👤 操作員 / Operator\n設定股票、閾值\n查看日誌"]

    %% ── 核心系統 ────────────────────
    SYS["⚙️ Stock Monitor\n台股每分鐘監控\n14:00 估值日結\nLINE 群組彙總通知"]
    DB[("🗄️ SQLite\ndata/stock_monitor.db\nwatchlist / message\nvaluation_snapshots")]

    %% ── 行情 API ─────────────────────
    subgraph MarketAPI["行情 API（HTTPS）"]
        direction TB
        TWSE["📡 TWSE MIS API\nmis.twse.com.tw\nTSE 即時行情（主）"]
        OTC["📡 TPEx MIS API\nmis.twse.com.tw\nOTC 即時行情（主）"]
        YAHOO["🌐 Yahoo Finance TW\ntw.stock.yahoo.com\nHTML 行情（副・備援）"]
        TWSE ~~~ OTC ~~~ YAHOO
    end

    %% ── 通知輸出 ──────────────────────
    LINE_API["💬 LINE Messaging API\napi.line.me\n官方帳號群組推播"]
    LINE_USER["👥 LINE 群組成員\n接收價格通知\n與開盤摘要"]

    %% ── 連線 ────────────────────────
    OPER  -- "CLI / PowerShell\ninit-db / run-daemon" --> SYS
    SYS   -- "讀寫" --> DB
    SYS   -- "GET 委賣一 / HTML scraping\nHTTPS" --> MarketAPI
    SYS   -- "POST push message\nBearer token" --> LINE_API
    LINE_API -- "LINE push message\n每分鐘彙總 / 開盤摘要" --> LINE_USER
```

---

## 1.3 外部系統說明

| 外部系統 | 角色 | 備註 |
|---|---|---|
| TWSE MIS API | 行情主來源 | 委賣五檔 `a` 欄位；`a` 空時讀 `_price_cache` |
| Yahoo Finance TW | 行情副來源 | HTML scraping；盤後 fallback `regularMarketPrice` |
| LINE Messaging API | 通知輸出 | Bearer token；每分鐘最多 1 封；CR-SEC-01 token 不得 log |
| SQLite（本機） | 狀態持久化 | WAL mode；JSON1 必須可用（fail-fast） |
