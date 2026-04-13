# 台股估值人物蒸餾（艾蜜莉／股海老牛／雷司紀）

更新日期：2026-04-13  
目的：將三位公開方法蒸餾成可落地於本專案（`fair_price` / `cheap_price`）的估值規則。

---

## 1) 艾蜜莉（紅綠燈估價法）

### 核心思維
- 用多估值法交叉後取平均，降低單一估值偏誤。
- 用安全邊際折數（例如 0.9）再保守化。

### 可程式化規則（依公開內容）
- 股利法：便宜價=`平均股利*15`，合理價=`平均股利*20`，昂貴價=`平均股利*30`
- 歷年股價法：便宜價=`10年每年最低價平均`，合理價=`10年每年平均價平均`，昂貴價=`10年每年最高價平均`
- 本益比法：以 `(近一年EPS + 近10年EPS)/2` 乘上歷年低/均/高本益比平均，得便宜/合理/昂貴價
- 股價淨值比法：`近一季淨值` 乘上歷年低/均/高 PB 平均，得便宜/合理/昂貴價
- 最後把可用方法做平均，再乘安全邊際折數

### 你的系統映射建議
- `fair_price` = 「合理價」  
- `cheap_price` = 「便宜價」  
- `method_name` 建議：
  - `emily_dividend_blended`
  - `emily_price_band`
  - `emily_pe_band`
  - `emily_pb_band`
  - `emily_composite`

### 適用與限制
- 適用：資料充足、可取得長期歷史數據的台股個股。
- 限制：景氣循環劇烈、一次性損益扭曲 EPS 時，PE 法易失真。

---

## 2) 股海老牛（平均股利估價 + 左右側交易）

### 核心思維
- 左側：價值股（高殖利率）越跌越買。
- 右側：成長股用本益比區間與趨勢加碼/減碼。

### 可程式化規則（依公開內容）
- 平均股利估價：
  - 便宜價：以平均股利反推約 6% 殖利率（常見倍數約 16x）
  - 合理價：以平均股利反推約 5% 殖利率（常見倍數約 20x）
  - 昂貴價：以平均股利反推約 3%~4% 殖利率（常見倍數約 25x~32x，公開文章常見 32x）
- 成長股輔助：依產業/公司類型給 PE 區間（如 8~12、12~16、16~20、20~24）判斷高低估

### 你的系統映射建議
- `fair_price` = 合理價（5% 殖利率反推）
- `cheap_price` = 便宜價（6% 殖利率反推）
- `method_name` 建議：
  - `oldbull_dividend_yield_v1`
  - `oldbull_dividend_band`
  - `oldbull_growth_pe_band`

### 適用與限制
- 適用：金融、傳產、高股利或股利政策相對穩定標的。
- 限制：高成長但低配息公司，僅用股利法會低估。

---

## 3) 雷司紀（四法框架 + 安全邊際）

### 核心思維
- 估值不是單點答案，是多方法交叉與安全邊際管理。
- 四法：PE、平均股利、PB、NCAV。

### 可程式化規則（依公開內容）
- PE：`股價 = PE * EPS`（可用歷史低/均/高 PE 反推買入/合理/賣出價）
- 平均股利：`股價 = 平均股利 * 期望回本年限`
- PB：`股價淨值比 = 股價 / 每股淨值`
- NCAV：`(流動資產 - 總負債) / 流通股數`
- 安全邊際：估值後打折（如 0.9；NCAV 常見更保守折扣）

### 你的系統映射建議
- `fair_price` = 各法中位或加權後的合理價
- `cheap_price` = `fair_price * margin_factor`（例如 0.85 或 0.9）
- `method_name` 建議：
  - `raysky_pe`
  - `raysky_dividend`
  - `raysky_pb`
  - `raysky_ncav`
  - `raysky_blended_margin_v1`

### 適用與限制
- 適用：要把估值做成可配置策略池（method registry）的系統設計。
- 限制：NCAV 在台股可用樣本較少，且需處理財報時點與資產折價。

---

## 4) 三家方法統一落地規格（供你 Phase 2 直接實作）

### 共同欄位（建議）
- `method_name`
- `method_version`
- `stock_no`
- `trade_date`
- `fair_price`
- `cheap_price`
- `input_snapshot_json`（保留估值輸入）
- `safety_margin_factor`

### 統一決策邏輯（與現有規則相容）
- 若 `market_price <= cheap_price` → `stock_status=2`
- 否則若 `market_price <= fair_price` → `stock_status=1`
- 同分鐘同股票多方法命中：以 `status=2` 優先，`methods_hit` 收全量

---

## 5) 先做哪個最穩（實作優先序）

1. `oldbull_dividend_yield_v1`（資料需求最低，台股最容易先跑）
2. `emily_composite_v1`（四法平均，能快速提升穩健度）
3. `raysky_blended_margin_v1`（含 NCAV，資料品質與清洗要求最高，最後做）

---

## 來源（公開網頁）
- 艾蜜莉紅綠燈算法與四法公式（CMoney）  
  https://cmnews.com.tw/article/emily-3c27e56c-d95c-11ef-b371-f00f7406f718
- 艾蜜莉估價模式（CMoney）  
  https://cmnews.com.tw/article/emily-178b6b40-2a75-11e3-a948-8e16b0c899e7
- 股海老牛：平均股利估價法、6%/5%/4%（鏡週刊）  
  https://www.mirrormedia.mg/story/20230524money003
- 股海老牛：16/20/32 倍與左右側（鏡週刊）  
  https://www.mirrormedia.mg/story/20240108money004
- 雷司紀：四種合理股價法與安全邊際（Raysky）  
  https://www.rayskyinvest.com/16980/value-investing-pe-pb-ncav-dividend

---

## 註記
- 上述為「方法蒸餾與工程落地建議」，不是投資建議。
- 若你同意，下一步我可以直接把這三家方法做成 `valuation method` 介面與第一批 failing tests（TDD 先紅燈）。
