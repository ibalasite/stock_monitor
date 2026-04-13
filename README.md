# Stock Monitoring System - README

更新日期：2026-04-13  
專案目標：台股價格監控 + LINE 群組通知 + 每日估值 + SQLite 落盤 + 補償機制

## 1. 專案現況摘要
1. 規格文件已完成並完成主要一致性對齊（PDD/EDD/User Story/Test Plan/Feature）。
2. BDD 規格檔已完成：`features/stock_monitoring_system.feature`。
3. `pytest-bdd` 已接上完整 `.feature` 執行層，`stock_monitoring_system.feature` 已有具體 step 實作。
4. `pytest-bdd` 已安裝，`stock_monitoring_smoke.feature` 與完整 `stock_monitoring_system.feature` 皆可執行。
5. `stock_monitor` 主程式套件已建立並實作核心測試契約。
6. 最新狀態：
   - `pytest -q tests`：最近一次基線（2026-04-13）為 `141 passed`（含完整 BDD + unit/integration/UAT contract）
   - Coverage gate：`100%`（line + branch）
   - CI：`.github/workflows/ci.yml` 已啟用（push / pull_request），且已採用 action SHA pin + 鎖版依賴 + `pip-audit`
   - 可執行入口：`python -m stock_monitor init-db|run-once|reconcile-once|valuation-once|run-daemon`
   - Nightly smoke：`.github/workflows/nightly-smoke.yml`（TWSE 必跑、LINE sandbox 可選）
7. 交付文件：
   - `test-report.md`
   - `defect-log.md`
   - `uat-signoff.md`

## 2. 文件地圖（全部文件與用途）
| 文件 | 用途 | 何時使用 |
|---|---|---|
| `PDD_Stock_Monitoring_System.md` | 產品需求（業務規則、範圍、UAT） | 討論需求、調整產品方向時 |
| `EDD_Stock_Monitoring_System.md` | 工程設計（架構、流程、DB schema、規則落地） | 進入實作前、review 設計時 |
| `USER_STORY_ACCEPTANCE_CRITERIA.md` | User Story 與驗收條件 | 排優先序、拆工作項時 |
| `features/stock_monitoring_system.feature` | BDD 可讀規格（業務語言） | 與 PM/QA 對齊行為、做 BDD 驗收時 |
| `TEST_PLAN.md` | 測試策略與 TP 對照矩陣 | 實作測試、追蹤 coverage 與驗收時 |
| `API_CONTRACT.md` | Port/Adapter 與錯誤語意契約 | 寫應用層與基礎設施介面前 |
| `ADR.md` | 架構決策記錄（為什麼這樣設計） | 有架構爭議或變更時 |
| `NFR_SLI_SLO.md` | 非功能需求與量測指標 | 定義 KPI、監控與告警時 |
| `SECURITY_AND_SECRETS.md` | 金鑰與安全規範 | 上線前安全檢查與稽核 |
| `OPERATIONS_RUNBOOK.md` | 維運與故障排除流程 | 日常操作、事故處理時 |
| `CODEX.md` | Codex 執行手冊與 symbol contract | 使用 Codex 開發時 |
| `CLAUDE.md` | Claude 執行手冊（與 CODEX 同語意） | 使用 Claude 開發時 |
| `README.md` | 專案入口與進度看板 | 每次進入專案第一個讀 |

## 3. 開發方法（BDD + Spec/Spac-Driven + TDD）
1. Spec/Spac-Driven：先規格後程式，所有實作以 `PDD + EDD` 為母體。
2. BDD（外層）：先寫/修 `.feature`，再用 `pytest-bdd` 將 scenario 轉成可執行測試，先跑出 Red。
3. TDD（內層）：針對 domain/application 細節寫 pytest 測試，先 Red，再最小實作到 Green，最後 Refactor。
4. 驗收順序採 outside-in：`feature scenario` 綠燈 + `unit/integration` 綠燈，才算完成。

## 4. 標準開發流程（固定順序）
1. 更新規格：`PDD -> EDD -> ADR/API_CONTRACT`。
2. 更新行為：`.feature`。
3. 建立/更新 BDD glue（`pytest-bdd` scenario + steps），先跑 BDD Red。
4. 更新 `TEST_PLAN` 與內層 pytest 測試（unit/integration），先跑 TDD Red。
5. 實作主程式讓測試逐批轉綠（先 unit/integration，再回歸 BDD scenario）。
6. 重構與補文件。
7. 最終驗收：BDD scenario 全綠 + TP/UAT 全綠 + coverage gate。

## 5. 目前做到哪一步
### 已完成
1. 需求與設計文件齊備。
2. PDD/EDD 已對齊以下關鍵規則：
   - `idempotency_key = stock_no + minute_bucket`（不含 `stock_status`）
   - 冷卻鍵維持 `stock_no + stock_status`
   - LINE 參數 canonical + alias 相容規則
   - `MAX_RETRY_COUNT=3`、`STALE_THRESHOLD_SEC=90`
   - `methods_hit` 必須為 JSON array
3. BDD `.feature` 與 `TEST_PLAN` TP-ID 已對齊。
4. 內層 pytest 契約測試檔已建立（可作為 TDD 基線）。
5. `tests/bdd/` 骨架已建立，scenario glue 已可載入整份 `.feature`。
6. `tests/bdd` step definitions 已完整可執行：
   - `stock_monitoring_system.feature`：完整規格具體行為斷言
   - `stock_monitoring_smoke.feature`：關鍵流程 smoke 驗證
7. `stock_monitor` 套件已建立，必要 symbol 與核心流程已落地。
8. `pytest.ini` 已完成 feature tags 註冊與 coverage gate 設定。
9. CI workflow 已接上 GitHub Actions。
10. 測試報告與簽核文件已產出。
11. `.gitignore` 已補齊長期開發所需忽略規則。
12. 已實作 production adapters：
   - TWSE MIS 行情 adapter
   - LINE Messaging API push adapter
   - SQLite repositories（watchlist/message/pending/log）
13. 已新增非 skeleton BDD smoke scenarios（outside-in）：
   - 交易時段跳過
   - 冷卻抑制
   - 補償回補不重送
14. 已完成 daemon 模式：
   - 交易時段（09:00-13:30）每分鐘輪詢
   - 交易日 14:00 單次估值
   - 補償回補循環持續執行
15. 已新增真實外部依賴 smoke：
   - `scripts/external_dependency_smoke.py`
   - `nightly-smoke.yml`（TWSE + 可選 LINE sandbox）
16. 行情 adapter 已支援 `TWSE + OTC` 雙通道查詢（可覆蓋上櫃股票，如 `3293`）。
17. LINE 通知訊息已改為中文可讀格式（包含股票中文名、代號、現價與門檻描述）：
   - 例：`台積電(2330)目前1950，低於合理價2000`
   - `status=2` 例：`台積電(2330)目前1450，低於便宜價1500（合理價2000）`

## 6. 下一步要做什麼（建議執行順序）
1. 完成正式人工 UAT 簽核（PO/QA/Eng Lead）。
2. 設定 GitHub Secrets（LINE sandbox）並啟用 nightly LINE push 驗證。
3. 實際交易日觀察 daemon 日誌與通知品質，回填 `test-report.md`/`defect-log.md`。
4. 持續維持流程：`PDD/EDD -> feature -> tests -> code`。

## 7. 啟動流程（實際可操作）
### 7.1 現在就可以跑（開發驗證模式）
1. 進入專案目錄：
```powershell
cd C:\Projects\stock
```
2. 建立虛擬環境並啟用：
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
3. 安裝測試依賴：
```powershell
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-dev.txt
```
4. 執行完整測試（等同 CI gate）：
```powershell
python -m pytest -q tests
```
5. 最近一次基線結果（2026-04-13）：
   - `141 passed`
   - coverage `100%`
   - 實際請以你當次執行輸出為準

### 7.2 要跑「真實盤中監控」前必做
目前已可執行單次流程與長駐 daemon（`run-daemon`）。  
本專案建議直接讀取系統環境變數，不把 token 寫入 `.env`。先設定環境變數，再執行：
```powershell
# 當前 shell 生效（關閉視窗後失效）
$env:LINE_CHANNEL_ACCESS_TOKEN = "你的 LINE Channel Access Token"
$env:LINE_TO_GROUP_ID = "你的目標群組 ID（例如 Cxxxxxxxxxx）"

# 驗證變數有讀到
echo $env:LINE_CHANNEL_ACCESS_TOKEN
echo $env:LINE_TO_GROUP_ID
```

若要長期保存（新開 shell 才生效）：
```powershell
setx LINE_CHANNEL_ACCESS_TOKEN "你的 LINE Channel Access Token"
setx LINE_TO_GROUP_ID "你的目標群組 ID（例如 Cxxxxxxxxxx）"
```

設定完成後可直接用以下命令執行：
```powershell
# 1) 初始化資料庫
python -m stock_monitor --db-path data/stock_monitor.db init-db

# 2) 單次盤中監控（會做交易時段判斷、冷卻、推播、落盤）
python -m stock_monitor --db-path data/stock_monitor.db run-once

# 3) 單次補償回補（僅回補 DB，不會重送 LINE）
python -m stock_monitor --db-path data/stock_monitor.db reconcile-once

# 4) 單次估值任務（交易日 14:00 執行，非交易日/非 14:00 會 skip）
python -m stock_monitor --db-path data/stock_monitor.db valuation-once

# 5) 長駐 daemon：
#    - 交易時段（09:00-13:30）每分鐘輪詢
#    - 交易日 14:00 估值
#    - 每圈執行補償回補
python -m stock_monitor --db-path data/stock_monitor.db run-daemon --poll-interval-sec 60 --valuation-time 14:00

# 6) daemon 測試模式（跑固定圈數就停止，方便本機驗證）
python -m stock_monitor --db-path data/stock_monitor.db run-daemon --poll-interval-sec 1 --valuation-time 14:00 --max-loops 5
```

`.env.example` 僅保留為欄位範本，不作為正式 secrets 載入來源。

### 7.2.1 建立 watchlist（必要，不然 `run-once` 會 `empty_watchlist`）
先把要監控的股票與手動門檻寫入 SQLite（以下示範你的三檔）：
```powershell
@'
from stock_monitor.adapters.sqlite_repo import connect_sqlite, apply_schema, SqliteWatchlistRepository

conn = connect_sqlite("data/stock_monitor.db")
apply_schema(conn)
repo = SqliteWatchlistRepository(conn)
repo.upsert_manual_threshold("2330", fair=2000, cheap=1500, enabled=1)
repo.upsert_manual_threshold("2348", fair=72, cheap=68, enabled=1)
repo.upsert_manual_threshold("3293", fair=700, cheap=680, enabled=1)
conn.close()
print("watchlist seeded")
'@ | python -
```
驗證 watchlist 是否已寫入：
```powershell
@'
import sqlite3
conn = sqlite3.connect("data/stock_monitor.db")
rows = conn.execute("SELECT stock_no, manual_fair_price, manual_cheap_price, enabled FROM watchlist ORDER BY stock_no").fetchall()
conn.close()
print(rows)
'@ | python -
```

### 7.2.2 TWSE 憑證驗證問題（Python 可處理，不需關閉 SSL 驗證）
如果你在瀏覽器看得到 TWSE，但 Python 出現憑證錯誤（例如 `SSLCertVerificationError`），通常是 Python trust store 與作業系統憑證鏈不同步造成。  
本專案已在程式內使用 `truststore`（走作業系統信任憑證），不需要用 `verify=False`。

1. 重新安裝專案鎖版依賴（含 `truststore`）：
```powershell
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-dev.txt
```
2. 驗證可抓到台股即時價：
```powershell
@'
from stock_monitor.adapters.market_data_twse import TwseRealtimeMarketDataProvider
p = TwseRealtimeMarketDataProvider(timeout_sec=10)
print(p.get_realtime_quotes(["2330"]).get("2330"))
'@ | python -
```
3. 預期輸出包含 `stock_no='2330'`、`price`、`tick_at`。若有輸出代表憑證問題已排除。

### 7.3 LINE Bot token / group id 申請與設定（Step by Step）
先說明（避免誤會）：  
LINE Messaging API 不能直接綁一般 LINE 個人帳號發送 API 訊息，仍需要一個 LINE Official Account（OA）對應的 Messaging API channel。  
你是個人開發者也可以建立 OA，不需要公司身分。

#### 7.3.1 你會用到的網站
1. LINE 台灣官方帳號申請入口（免費開設帳號）：`https://tw.linebiz.com/account/`
2. LINE 台灣管理頁入口（會導到 OA Manager）：`https://tw.linebiz.com/login/`
3. LINE Official Account Manager（管理 OA）：`https://manager.line.biz/`
4. LINE Developers Console（管理 channel/token/webhook）：`https://developers.line.biz/console/`
5. Webhook 測試接收頁（先拿 group id 用）：`https://webhook.site/`

#### 7.3.2 申請與開通流程（點擊路徑）
1. 建立 OA（個人身分可）  
開 `https://tw.linebiz.com/account/`，點「免費開設帳號」，依頁面指示用 LINE 帳號或 email 建立/登入 Business ID，完成 OA 建立。
2. 在 OA Manager 啟用 Messaging API  
開 `https://tw.linebiz.com/login/`（或直接 `https://manager.line.biz/`）登入 OA 後台，選你的 OA，進 Messaging API 相關設定，點「啟用 Messaging API」。
3. 選 Provider（第一次會要求）  
啟用時會導向 Developers 相關設定，建立或選擇 provider。這個 provider 後續不可任意更換，建議用你個人專用 provider。
4. 進 Developers Console 確認 channel  
開 `https://developers.line.biz/console/`，選 provider，確認有一個 Messaging API channel。
5. 發行 token  
在該 channel 的 `Messaging API` 頁籤發行 Channel Access Token。建議用短期或 v2.1 類型，不建議長效 token。
6. 暫時設定 Webhook URL 來抓 group id（如果你從沒設定過，就從這步開始）  
`groupId` 只會出現在 webhook 事件內容裡，LINE 後台不會直接顯示，所以要先找一個接收 webhook 的地方。  
這裡用 `https://webhook.site/` 只是「臨時接收測試事件」，不是你的正式系統。  
操作：
1) 開 `https://webhook.site/`，頁面會自動產生一條專屬 URL（例如 `https://webhook.site/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）  
2) 複製這條 URL  
3) 開 `https://developers.line.biz/console/` -> 你的 channel -> `Messaging API`  
4) 在 `Webhook URL` 貼上剛剛的 URL  
5) 按 `Verify`，看到成功後把 `Use webhook` 切成啟用
7. 開啟群組加入權限（在 Developers Console）  
開 `https://developers.line.biz/console/` -> 選你的 channel -> `Messaging API` 頁籤 -> 找到 `Allow bot to join group chats` -> 切到 `Enabled`。
8. 建立你的私人通知群組（在 LINE 手機 App）  
LINE App -> `聊天` -> 右上角「新增聊天」-> `建立群組` -> 先勾選你自己 + (必要時)一個測試帳號 -> 建立群組後，於群組成員管理把你的 OA 邀請進來。
9. 觸發 webhook 取得 `groupId`（在 webhook.site 看事件）  
回到你剛建立且 Bot 已加入的群組，發一則訊息（例如 `group id test`）。  
再回 `webhook.site` 同一頁：
1) 左側會出現最新一筆請求（通常是 POST）  
2) 點那筆請求  
3) 在右側/下方看 `Request Body`（JSON 內容）  
4) 找 `events[0].source.groupId`
範例如下：
```json
{
  "events": [
    {
      "source": {
        "type": "group",
        "groupId": "Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  ]
}
```
複製 `source.groupId`（`C...`）就是你要填的 `LINE_TO_GROUP_ID`。
10. 抓到 `groupId` 後立刻移除臨時 webhook（回 Developers Console）  
`Messaging API` 頁籤 -> `Webhook URL` 改掉 `webhook.site`（改正式網址）或先把 `Use webhook` 關閉（若暫時不收 webhook 事件）。
11. 把 secrets 放環境變數（不寫 `.env`，在 Windows PowerShell）  
在 PowerShell 設定：
```powershell
setx LINE_CHANNEL_ACCESS_TOKEN "你的token"
setx LINE_TO_GROUP_ID "Cxxxxxxxxxx"
```
關閉並重開終端後生效。
12. 本機驗證（在專案目錄）  
```powershell
python -m stock_monitor --db-path data/stock_monitor.db init-db
python -m stock_monitor --db-path data/stock_monitor.db run-once
```
若群組收到訊息代表串接成功。若沒收到，先回 Developers Console 檢查 token 是否過期、`LINE_TO_GROUP_ID` 是否為同一個群組的 `C...`。

若你完全不想建立 OA：  
本專案現行 LINE 推播路徑將無法使用（拿不到可用的 channel token / group id）。  
可選擇改用其他通知通道（例如 Telegram Bot、Discord Webhook），或後續加一層通知 adapter。

官方文件（建議對照）：
1. Messaging API 開始使用與建立 channel：https://developers.line.biz/en/docs/messaging-api/getting-started/
2. Build a bot（token / webhook URL / verify / use webhook）：https://developers.line.biz/en/docs/messaging-api/building-bot/
3. 群組聊天與 `groupId`：https://developers.line.biz/en/docs/messaging-api/group-chats
4. Webhook URL 驗證：https://developers.line.biz/en/docs/messaging-api/verify-webhook-url/
5. Webhook 簽章驗證：https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/
6. 開發安全建議（token 生命週期）：https://developers.line.biz/en/docs/partner-docs/development-guidelines/

## 8. 常用命令
```powershell
# 跑全部測試
python -m pytest -q tests

# 安裝鎖版測試依賴（含 hashes）
python -m pip install --require-hashes -r requirements-dev.txt

# 供應鏈弱點掃描（與 CI 相同）
python -m pip_audit --progress-spinner=off --requirement requirements-dev.txt

# 重新產生鎖版依賴（更新 requirements-dev.txt）
python -m piptools compile --generate-hashes --output-file requirements-dev.txt requirements-dev.in

# 只跑 BDD 測試
python -m pytest -q tests/bdd --no-cov

# BDD 詳細輸出（列出每個 scenario 與狀態）
python -m pytest tests/bdd --no-cov -vv -ra

# 只列出本次會執行的 BDD 情境（不執行）
python -m pytest tests/bdd --collect-only --no-cov

# 顯示 Gherkin 風格輸出（Given/When/Then）
python -m pytest tests/bdd --no-cov --gherkin-terminal-reporter -s


# 跑單一測試模組
python -m pytest -q tests/test_policy_rules.py

# 只測 LINE 通道（不送出）
python scripts/test_line_push.py --dry-run

# 只測 LINE 通道（實際送一則訊息）
python scripts/test_line_push.py --message "LINE 通道測試"

# 真實外部依賴 smoke（TWSE only）
python scripts/external_dependency_smoke.py --stock-no 2330 --skip-line

# 真實外部依賴 smoke（含 LINE sandbox push）
python scripts/external_dependency_smoke.py --stock-no 2330 --line-send --require-line-config
```

## 9. 文件維護規則
1. 規格有變更時，必須同步更新：`PDD/EDD/feature/TEST_PLAN/CODEX/CLAUDE/README`。
2. 任何新功能都要有對應：
   - 至少一個 User Story
   - 至少一個 `.feature` Scenario
   - 至少一個 TP 測試案例
3. 未更新文件不得視為完成。
