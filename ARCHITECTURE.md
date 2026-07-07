# Agentic AI Investment Team 架構藍圖

本專案目標是建立一個個人用的 AI 投研團隊。Dashboard 扮演資料中樞，Agent 扮演研究員、分析師與風控官，使用者本人保留最終投資決策權。

核心原則：

- AI 只做研究、交叉驗證、風險提示與建議，不自動下單。
- 所有結論都必須可追溯到價格資料、新聞來源、技術指標或使用者設定。
- 不用單一大型 prompt 包辦所有判斷，改用分工明確的工作流。
- 先建立乾淨資料層，再導入 LLM 與多 Agent。

## 一、數據層：微型彭博終端

Dashboard 與資料庫是整個系統的地基。Agent 不直接依賴臨時網路搜尋下結論，而是優先從本機資料庫與已保存的快照取得資料。

### 資料來源

第一階段：

- 美股、ETF、全球標的：Yahoo Finance chart/search endpoint。
- 台股：先支援 Yahoo Finance 的 `.TW` 代號，後續補 FinMind 或交易所資料。
- 原物料：Yahoo Finance 期貨代號，例如 `GC=F`、`CL=F`、`HG=F`。
- 虛擬貨幣：先支援 Yahoo Finance 的 `BTC-USD`、`ETH-USD`，後續補 Binance 或 OKX K 線。
- 新聞：Yahoo Finance search news，後續補 Reuters、SEC、公開資訊觀測站、RSS 或搜尋 API。

第二階段：

- 總經：美債殖利率、美元指數、CPI、Fed 會議、PMI。
- 籌碼：台股三大法人買賣超、融資融券、外資持股。
- 加密貨幣鏈上/衍生品：交易所資金費率、未平倉量、大戶流向。

### 資料庫

第一階段使用 SQLite，因為本機開發簡單、部署成本低，足以保存個人投研快照。

建議資料表：

- `universe`：標的清單、資產類別、幣別、產業分類、是否啟用監控。
- `price_snapshots`：每日或盤中價格快照，包含 OHLC、成交量、日漲跌、5 日/20 日/60 日變化。
- `technical_signals`：RSI、MACD、均線、歷史波動率、布林通道、均線糾纏度。
- `news_items`：新聞標題、來源、連結、發布時間、相關標的、情緒標籤。
- `event_alerts`：波動超標、新聞語氣異常、量價異常、風險事件。
- `agent_reports`：各 Agent 的結構化研究輸出與引用資料。
- `recommendations`：策略整合後的建議、權重、停損、信心分數與限制。
- `user_decisions`：使用者最後決策與備註，用於回顧與改善。

### Dashboard

Dashboard 應分成三個視角：

- 市場監控：最新警示、波動超標、新聞事件、資料更新狀態。
- 標的詳情：價格走勢、技術指標、新聞、風險、Agent 報告。
- 投資組合：目前持倉、現金比例、集中度、資產類別曝險、建議調整。

目前專案已具備第一版「市場監控 + 標的卡片」，下一步要把單次查詢結果寫入 SQLite，形成歷史資料。

## 二、核心層：Agentic AI 投研團隊

Agent 應該是明確的職責分工，而不是聊天式大 prompt。

### Agent 角色

1. Data Retrieval Agent

負責從資料庫與 API 取資料，輸出乾淨的結構化上下文。

輸出範例：

```json
{
  "symbol": "AAPL",
  "price": 311.23,
  "change_1d": -0.41,
  "change_5d": -0.41,
  "volatility_20d": 21.8,
  "news_count": 8,
  "data_gaps": ["pe", "eps"]
}
```

2. Technical Analyst Agent

負責量價與技術面，例如：

- 均線排列。
- RSI/MACD。
- 布林通道。
- 20 日與 60 日波動率。
- 價格是否接近近期支撐/壓力。

3. Macro & News Analyst Agent

負責新聞、總經與產業事件：

- 過去 24 小時新聞摘要。
- 利多/利空分類。
- 是否有新聞可解釋價格波動。
- 事件與價格時間序列是否吻合。
- 資料來源可靠度。

4. Flow / On-chain Agent

依資產類別切換分析：

- 台股：三大法人、融資融券、外資持股。
- 美股：資金流、ETF flow、選擇權或機構持股，視資料來源可得性。
- 加密貨幣：資金費率、未平倉量、鏈上大額轉帳。

5. Risk Officer Agent

負責風控，不產生買賣衝動：

- 單一標的曝險。
- 相關性與集中度。
- 最大可承受回撤。
- 波動率是否超過使用者設定。
- 目前建議是否違反使用者風險偏好。

6. Chief Strategist Agent

整合前述報告，做交叉驗證：

- 技術面與新聞面是否一致。
- 波動是否已有合理原因。
- 看多與看空論點各自強度。
- 建議行動、權重、停損、觀察條件。
- 明確列出不確定性與需要人工覆核的資料。

## 三、決策層：PM 工作流

系統不自動交易。建議工作流如下：

1. 定時喚醒

- 每日台股收盤後。
- 每日美股收盤後。
- 每週五產生週報。
- 波動超標時產生事件報告。

2. 自動研究

- 更新價格與新聞。
- 儲存 snapshot。
- 觸發 Agent 分析。
- 生成日報、週報或事件報告。

3. 使用者覆核

- 使用者打開 Dashboard。
- 檢查數據、新聞來源、Agent 引用與限制。
- 在系統中記錄最後決策：買進、賣出、觀望、加碼、減碼。

4. 人工下單

- 使用者自行前往券商或交易所下單。
- 系統只保存決策紀錄與後續追蹤結果。

## 四、落地順序

### Phase 1：資料層與事件監控

- 建立 SQLite。
- 建立 universe。
- 將目前 `/api/analyze` 的結果保存成 snapshots。
- 保存新聞與 alerts。
- Dashboard 顯示最近一次與歷史趨勢。

### Phase 2：定時任務與日報

- 建立每日收盤後更新腳本。
- 產生 markdown/html 投研日報。
- 支援事件觸發報告。

### Phase 3：Agent 報告

- 先用 deterministic code 產生技術面、新聞面、風險面結構化報告。
- 再接 OpenAI API，讓 LLM 只負責摘要、推理與交叉驗證。
- 每份 LLM 報告必須附引用資料與資料缺口。

### Phase 4：投資組合與 PM 決策紀錄

- 建立持倉與現金設定。
- 計算資產類別曝險、集中度、相關性。
- 記錄使用者決策與後續績效回顧。

## 五、目前專案定位

目前已完成 Phase 1 基礎層與 Phase 2/3 大部分功能：

- 本機 Dashboard：整合最新進度、市場雷達、手動投研、ETF 異動、資料健康、績效回測。
- 台灣全市場 universe（TWSE + TPEx 上市上櫃 + ETF + 興櫃），背景定時掃描。
- 主動式 ETF 每日持股快照與個股線索，接入 ZDS 第三方資料源。
- AegisTrader snapshot 覆蓋率監控與財務資料稽核（pass/warn/block/not_applicable）。
- 前置交易資料就緒檢查（pre-trade checklist）。
- 量化指標（ADX、RS 百分位、波動 regime）與績效回測歸因。

**Python 後端模組結構（截至 2026-06-30，Phase 2 完成）**：

| 模組 | 行數 | 職責 |
|------|------|------|
| `common.py` | 436 | 常數、全域設定、共用工具函式 |
| `outcomes.py` | 385 | 推薦績效追蹤（recommendation_outcomes） |
| `tw_universe.py` | 726 | 台灣全市場 universe 管理與覆蓋率分析 |
| `scheduler.py` | 475 | 背景排程狀態、持久化、start/stop |
| `active_etf.py` | 1,485 | 主動式 ETF 匯入、同步、查詢與異動分析 |
| `data_health.py` | 1,026 | Aegis snapshot、DB 健康、財務稽核、前置交易檢查 |
| `technical_indicators.py` | 344 | 技術指標計算（MA、RSI、MACD、ADX、Bollinger 等） |
| `scoring.py` | 536 | 評分邏輯、因子品質、等級判定 |
| `models.py` | 59 | Quote + AegisFundamentals dataclasses |
| `data_sources.py` | 588 | 資料擷取/新聞（Yahoo、TPEX、Aegis snapshot、get_quote、get_chart、get_news 等） |
| `scan.py` | 1,134 | 掃描邏輯、市場機會分析、推薦日誌、Agent 報告、回測執行 |
| `app.py` | 3,069 | 資料庫初始化、推薦引擎、HTTP server、API endpoints、watchlist monitor |

模組依賴方向：`app.py` → scan, data_sources, scoring, technical_indicators, data_health, active_etf, scheduler, tw_universe, outcomes, models, common。循環依賴以 late import 打破：data_health + scan + scheduler 各有後向延遲載入。

**累計縮減**：app.py 9,918 → 3,069（-69.1%）

**資料庫**：`runtime_db/investment_live.db`（SQLite）。

**下一步優先項**：

- 主動式 ETF 接官方資料源（TWSE/TPEx/投信揭露）。
- 用累積推薦日誌做歷史回測統計（`scan.py` 已有 `get_daily_recommendation_performance` + `compute_recommendation_persistence`）。
- UI 標籤清理（`static/index.html` 源碼層面）。
