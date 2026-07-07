# 投資建議小幫手

一個本機可跑的個人 AI 投研團隊 MVP。主軸是「全市場/候選池掃描」，讓系統主動找出異常波動、技術轉強、風險警示與值得覆核的投資機會；手動查詢仍保留作為深入研究入口。Dashboard 扮演資料中樞，Agent 分工擔任資料研究員、技術分析師、新聞/總經分析師、風控官與首席策略分析師，使用者本人保留最終投資決策權。

使用說明請看 `USER_GUIDE.md`，完整架構請看 `ARCHITECTURE.md`。

## 執行

雙擊或執行：

```powershell
.\start_backend_and_scheduler.bat
```

這會檢查後端是否已啟動，未啟動就開 `python app.py`，並啟動背景輪巡。

檢查背景輪巡：

```powershell
.\check_scheduler_status.bat
```

若要在命令列或排程中執行且不暫停視窗：

```powershell
.\start_backend_and_scheduler.bat nopause
.\check_scheduler_status.bat nopause
```

手動啟動後端：

```powershell
cd Investment
python app.py
```

開啟：

```text
http://127.0.0.1:8765
```

若 8765 被占用：

```powershell
netstat -ano | Select-String ':8765'
Stop-Process -Id <PID> -Force
```

啟動時會自動使用本機 SQLite：

```text
runtime_db/investment_live.db
```

掃描與 Agent 報告也會同步輸出 Markdown/JSONL：

```text
research_exports/
```

## API

- `GET /api/analyze?symbols=AAPL,2330.TW`：分析標的並自動保存 snapshot。
- `GET /api/analyze?symbols=AAPL&threshold=1`：使用手動波動門檻。
- `GET /api/universe?limit=100`：查看目前啟用候選池。
- `GET /api/universe/expand?profile=starter`：加入 starter universe，擴大候選池。
- `GET /api/universe/import?file=universe_import.csv`：匯入專案目錄內的 CSV 水池清單，upsert 到 `universe`。
- `GET /api/universe/maximize?count=250`：低頻連 Yahoo Finance screener 擴大水池，適合久久跑一次。
- `GET /api/active-etf/holdings/import?file=active_etf_holdings.csv`：匯入主動式 ETF 每日完整成分股快照，原始資料會保存。
- `GET /api/active-etf/import-zdsetf`：從 ZDS ETF Tracker 抓取主動式 ETF 真實公開資料，寫入 holdings、changes 與資料稽核紀錄。
- `GET /api/active-etf/sync`：依序嘗試官方來源占位、ZDS 第三方來源、手動 CSV 後備，並保留既有快取資料狀態。
- `GET /api/active-etf/source-status`：查看 ETF 資料來源是否可用、是否 stale、最近匯入 audit。
- `GET /api/active-etf/holdings?limit=500`：查看已保存的每日完整成分股快照。
- `GET /api/active-etf/import?file=active_etf_changes.csv`：舊版手動異動匯入，僅保留相容，不建議作為正式資料流程。
- `GET /api/active-etf/changes?limit=100`：查看主動式 ETF 異動與個股線索彙總。
- `GET /api/active-etf/audit`：稽核主動式 ETF 異動資料來源，區分官方/第三方/未驗證。
- `GET /api/data/audits?limit=50`：查看資料匯入稽核紀錄，包含來源、欄位、筆數、警告數與 Markdown 路徑。
- `GET /api/scan?limit=25&min_priority=25&refresh_minutes=60&offset=0`：掃描候選池並產生 opportunity queue；60 分鐘內已有 snapshot 的標的會走快取。
- `GET /api/scheduler/start?interval_minutes=30&batch_size=25&refresh_minutes=60`：啟動背景自動掃描。
- `GET /api/scheduler/status`：查看背景掃描狀態。
- `GET /api/scheduler/stop`：停止背景掃描。
- `GET /api/research?symbols=AAPL,2330.TW`：啟動 Agent 投研團隊，產生資料、技術、新聞、籌碼/鏈上、風控、首席策略報告。
- `GET /api/backtest?symbols=AAPL,SPY&days=180&cost_bps=5`：用歷史日線回測技術分數策略。
- `GET /api/db/status`：查看資料庫表格筆數與最近 snapshots。
- `GET /api/history?symbol=AAPL&limit=30`：查看單一標的歷史 snapshots 與 alerts。
- `GET /api/data/health`：查看整體資料健康狀態，含排程狀態、Aegis snapshot 覆蓋率、財務稽核與推薦就緒。
- `GET /api/data/pre-trade-checklist?symbol=2330.TW`：查看指定標的的交易前資料就緒狀況。
- `GET /api/data/financial-audit?limit=80`：查看財務資料稽核結果（pass/warn/block/not_applicable）。
- `GET /api/data/quality-history?symbol=2330.TW&limit=10`：查看標的資料品質歷史。
- `GET /api/universe/unsnapped?limit=500`：查看啟用中但尚未有 Aegis snapshot 的全市場個股；加 `&export=1` 也會輸出 Markdown 與 CSV 到 `research_exports/data_audits/`。
- `GET /api/universe/scopes`：查看全市場範疇定義與掃描策略。

## 代號範例

- 美股：`AAPL`, `MSFT`, `NVDA`, `SPY`
- 台股：`2330.TW`, `2317.TW`, `0050.TW`
- 原物料：`GC=F`, `CL=F`, `HG=F`
- 虛擬貨幣：`BTC-USD`, `ETH-USD`, `SOL-USD`

## 資料與判斷邏輯

- 行情與部分基本面：Yahoo Finance quote/chart API
- 即時新聞：Google News RSS
- 評分面向：技術面、基本面、新聞語氣、流動性與波動風險
- Dashboard 監控：
  - 依資產類別設定波動門檻，美股/台股約 3%、原物料約 2.5%、虛擬貨幣約 5%。
  - 若近期日波動較高，門檻會用「近 21 日日波動 * 1.8」動態上調。
  - 前端可自訂全域波動門檻；未填時使用資產類別預設值。
  - 可開啟 5 分鐘自動刷新，作為簡易動態監控。
  - 單日或 5 日波動超過門檻時，會主動抓更聚焦的 Yahoo Finance 新聞，列為可能催化原因。

分數只是一個研究排序工具，不是保證報酬的模型。新聞語氣目前用關鍵字粗略判斷，適合快速篩選，正式下單前仍需人工閱讀來源、確認財報、產業趨勢、總經與個人風險承受度。

## 回測規則

第一版回測只使用歷史收盤價與技術規則，不使用新聞與基本面，避免歷史新聞資料不足造成假精準。

- 第 N 日收盤後用過去資料計算技術分數。
- 第 N+1 日依分數持倉：
  - `score >= 72`：100% 部位。
  - `score >= 58`：50% 部位。
  - `score <= 42`：0% 部位。
  - 其他：25% 部位。
- 預設交易成本為 5 bps。
- 回測結果會保存到 `backtest_runs`。

## Agent 投研工作流

`/api/research` 會產生 6 份分工報告並保存到 `agent_reports`：

- Data Retrieval Agent：檢查資料完整性與本機 snapshot。
- Technical Analyst Agent：分析技術面、均線、波動與分數。
- Macro & News Analyst Agent：整理新聞語氣與可能事件。
- Flow / On-chain Agent：依資產類別列出籌碼、資金流或鏈上資料缺口。
- Risk Officer Agent：檢查風險、警示與部位限制。
- Chief Strategist Agent：交叉驗證前面報告，提出給 PM 覆核的行動建議。

## 市場雷達工作流

主軸流程：

```text
Universe 候選池
-> Market Scanner 掃描行情、波動、分數與新聞
-> Opportunity Queue 排出優先清單
-> 對高優先標的觸發 Agent 投研
-> PM 在 Dashboard 覆核後手動決策
```

Dashboard 預設進入「最新進度」頁；它會整合背景輪巡健康狀態、最新 scan、推薦/風險候選、水池覆蓋率與主動式 ETF 線索，並每 30 秒自動刷新。「市場雷達」可手動掃描候選池；「手動投研」保留給指定標的深入分析。

目前已保存歷史 snapshots，市場雷達會顯示歷史筆數與分數變化；資料越累積，趨勢判斷會越有依據。

水池擴大方式：

- 內建 starter：按 Dashboard 的「擴大候選池」。
- CSV 匯入：維護 `universe_import.csv`，按「匯入水池 CSV」或呼叫 `/api/universe/import?file=universe_import.csv`。
- 最大化同步：按「最大化水池」或呼叫 `/api/universe/maximize?count=250`，會從 Yahoo Finance screener 匯入活躍股、漲跌幅榜、成長/價值/基金/ETF 等來源。
- CSV 欄位：`symbol,name,asset_type,currency,sector,industry,enabled`；只有 `symbol` 必填，其餘可空白，系統會依 symbol 分類資產。

市場雷達預設 `refresh_minutes=60`，同一標的 60 分鐘內已有 snapshot 時會 bypass 重新抓資料，以減少 API 呼叫與資料量；設為 `0` 可強制刷新。

市場雷達也支援背景自動掃描。這是本機 app 內的背景 thread，app 關閉後會停止，重啟 app 後需重新啟動。背景掃描採混合批次：先更新一小批已有 snapshot/opportunity 的核心候選，再用剩餘名額輪巡大水池。這樣水池可以很大，但原有候選仍能短週期追蹤。

每次掃描會同步輸出 `research_exports/scans/*.md` 與 `research_exports/jsonl/opportunities.jsonl`；每次 Agent 投研會同步輸出 `research_exports/reports/*.md` 與 `research_exports/jsonl/agent_reports.jsonl`。

## 主動式 ETF 異動

台股主動式 ETF 的實際投資組合具每日揭露特性，因此持股新增、加碼、減碼、刪除可作為投資研究線索。正式流程以「每日完整成分股快照」為主：先保存原始 holdings 到 `active_etf_holdings`，再由系統比對同一檔 ETF 的前一個可用快照，產生 `active_etf_changes` 摘要。

這樣做是因為只匯入「新增/刪減」容易錯：如果來源 CSV 欄位錯位、中文編碼壞掉、或人工判斷漏掉刪除，系統無法證明異動是真的。完整快照可以保留原始證據，也能重跑比對邏輯。

每次 holdings 匯入也會寫入 `data_import_audits`，並輸出 Markdown 到 `research_exports/data_audits/`。這層紀錄的目的不是判斷推薦準不準，而是先確認數字來源、欄位、筆數與可疑值都可追溯。

目前已接入第三方真實資料源 `https://www.zdsetf.com`。它會抓取每檔主動式 ETF 詳細頁，保存「全部持股」到 `active_etf_holdings`，保存「新增/加碼/減碼/刪除」到 `active_etf_changes`，並在每筆資料保存來源 URL。此來源屬於 `third_party`，不是官方 `verified`，但可作為外部佐證資料。

每日背景 ETF 更新策略：

1. 官方來源 adapter：目前保留占位，未接 TWSE/TPEx/投信官網前會跳過。
2. ZDS ETF Tracker：主要第三方來源。
3. `active_etf_holdings.csv`：最後人工 CSV 後備。
4. 若所有線上來源失敗，保留既有資料並標記 cached/stale/unavailable。

排程仍使用台北時間 `16:40` 與 `20:40`，但現在會呼叫統一 sync 策略，而不是只匯入本機 CSV。

完整快照匯入檔案為 `active_etf_holdings.csv`，欄位包含：

```csv
trade_date,etf_symbol,etf_name,issuer,stock_symbol,stock_name,shares,weight,market_value,source,source_url
```

匯入檔案為 `active_etf_changes.csv`，欄位包含：

```csv
trade_date,etf_symbol,etf_name,issuer,stock_symbol,stock_name,change_type,previous_shares,current_shares,share_delta,weight,estimated_price,estimated_value,source
```

新增/加碼會被標成偏正向研究線索；減碼/刪除會被標成風險觀察。這些線索會自動把個股加入 `universe`，但不構成買賣指令。

目前內建 `active_etf_changes.csv` 是示範資料，來源標示為 `demo_manual_unverified`，UI 會顯示未驗證。只有 source 欄位帶官方 URL 或可信第三方來源時，才會在稽核中標成有外部來源佐證。

背景輪巡會在台北時間每日 `16:40` 與 `20:40` 檢查並匯入 `active_etf_changes.csv`，同一天同一時段只會成功執行一次。手動補跑可呼叫：

```text
/api/active-etf/schedule/run-due?force=1
```

## 程式模組架構

後端拆分為 12 個 Python 模組（Phase 2 完成，2026-06-30）：

| 模組 | 行數 | 職責 |
|------|------|------|
| `common.py` | 436 | 常數、全域設定、共用工具 |
| `models.py` | 59 | Quote、AegisFundamentals dataclass |
| `outcomes.py` | 385 | 推薦績效追蹤（recommendation_outcomes） |
| `tw_universe.py` | 726 | 台灣全市場 universe 管理與覆蓋率 |
| `scheduler.py` | 475 | 背景排程狀態、持久化、start/stop |
| `active_etf.py` | 1,485 | 主動式 ETF 匯入、同步、查詢、異動分析 |
| `data_health.py` | 1,026 | Aegis snapshot、DB 健康、財務稽核、pre-trade 檢查 |
| `technical_indicators.py` | 344 | 技術指標計算（MA、RSI、MACD、ADX、Bollinger 等） |
| `scoring.py` | 536 | 評分邏輯、因子品質、等級判定（cap_grade、opportunity_grade 等） |
| `data_sources.py` | 588 | 資料擷取、新聞（Yahoo Finance、TPEx fallback） |
| `scan.py` | 1,227 | 掃描、推薦日誌、回測、Agent 投研報告 |
| `app.py` | 3,069 | 推薦引擎、HTTP server、universe queries、watchlist |

依賴方向：`app.py` → scan, data_sources, scoring, technical_indicators, data_health, active_etf, scheduler, tw_universe, outcomes, models, common。
循環依賴透過 late import 處理（data_health → scan、scan → app、scheduler → app + scan）。

## 後續可補強

優先順序（2026-06-30 更新）：

1. **資料品質清理**：部分 2026-06-10 的 price_snapshot 存入錯誤縮放值（10x），導致 outcome 計算出不真實的高報酬率。計劃在 `update_recommendation_outcomes` 加入合理性檢查，|return_percent| > 500% 自動標記為 `suspicious`，不納入勝率統計。
2. **每日推薦持續執行**：目前僅累積 5 天 JSONL 日誌（Jun 11, 12, 24, 25, 29）。每日執行 `run_daily_recommendation.bat` 是讓 attribution 分析有意義的前提，30 天以上才能做有效因子歸因。
3. **大盤 regime 濾波**：掃描前先看 0050.TW 均線狀態（MA20/MA60 位階與方向）。弱市環境只保留 `tier=core_watch` 或 `financial_audit=pass` 的標的，避免在下跌趨勢中持續輸出偏多推薦。
4. **open-outcome 缺口補足**：目前有 2,272 筆 `missing_price` outcome（無法補算）。排程中加入「每日補存有 pending outcome 標的的 snapshot」，確保到期時有價格可計算。
5. **多信號確認機制**：`build_recommendation` 加入 `bullish_confirmation_count`，技術、基本面、新聞情緒三者衝突時自動降級，減少單一信號誤報。
6. **因子權重校準**：累積 30 天以上 outcome 後，用 `/api/recommendations/daily-log/performance` 的 attribution 資料調整 `FACTOR_WEIGHTS`，讓高勝率因子得到更高權重。
7. **主動式 ETF 官方資料源**：目前以 ZDS 第三方為主，擱置中，等待確認 TWSE/TPEx/投信揭露可用 endpoint。
8. **MOPS 重大訊息接入**：公開資訊觀測站重大訊息，補充 Yahoo Finance 新聞在台股的覆蓋不足。
9. 接 OpenAI API，讓模型把新聞、財報與產業資料整理成完整投資論述。
10. 增加投資人設定：投資期限、風險承受度、幣別、最大回撤。
## 手動執行每日推薦

每天手動生成當日推薦日誌（寫入 JSONL + Markdown）：

```powershell
.\run_daily_recommendation.bat
```

若今日已生成過，會自動跳過。若要強制重新生成（預設即 force=1）：

```powershell
.\run_daily_recommendation.bat 1
```

限制推薦數量：

```powershell
.\run_daily_recommendation.bat 1 30
```

bat 會自動在背景啟動 server（若未運行），呼叫完畢後 server 持續在後台運行。

## 開機後一次性補更新

如果每天會關機，開機後可執行：

```powershell
.\sync_all_now.bat
```

這個批次會做：

- 確認後端是否開著，沒開就啟動 `python app.py`。
- 呼叫 `/api/active-etf/sync` 更新主動式 ETF 資料。
- 呼叫 `/api/scan` 更新候選池行情、新聞、snapshot 與 opportunity queue。
- 不啟動背景 scheduler。

可指定候選池掃描數量、快取分鐘與 offset：

```powershell
.\sync_all_now.bat 100 240 0
```

背景輪巡只有執行 `start_backend_and_scheduler.bat` 或呼叫 `/api/scheduler/start` 才會開始。

## 低頻集中擴大候選池

只建立候選池名冊，不掃價格/新聞/snapshot：

```powershell
.\build_universe_only.bat
```

久久跑一次：

```powershell
.\expand_pool_now.bat
```

可指定 Yahoo 每個 screener 抓取數量、每輪 scan 數量、scan 輪數、快取分鐘：

```powershell
.\expand_pool_now.bat 250 100 10 1440
```

流程：

- 同步 ETF 真實資料。
- 將 ETF holdings 裡出現的股票併入 universe。
- 匯入 `universe_import.csv`。
- 呼叫 Yahoo Finance screeners 擴池。
- 分批 scan 擴大後的 universe，補 snapshot 與 opportunity。
- 不啟動背景 scheduler。

`build_universe_only.bat` 和 `expand_pool_now.bat` 的差異：

- `build_universe_only.bat`：只擴 universe 名冊，目標是接近全市場候選池。
- `expand_pool_now.bat`：擴 universe 後再分批 scan，會抓行情、新聞與 snapshot。

## 財務資料稽核規則

財務資料正確性優先於勝率回測。股票類標的必須清楚標示基本面資料來源、期間、新鮮度與缺口；ETF、商品、加密貨幣不強制套用單一公司 EPS/PE 規則。

分類：

- `pass`：需要公司財務資料，且已取得可信來源；月營收若存在不得超過 4 個月；季度 EPS 若存在不得超過 3 季；重要缺口不會讓 value 或 quality 信心降到 none/low。
- `warn`：有財務來源但資料明顯不完整；缺 `pe`、`eps`、`eps_ttm`、`revenue_yoy`、`revenue_mom` 等重要欄位，且 value/quality 信心為 none/low；或月營收超過 4 個月、EPS 超過 3 季。`warn` 會限制基本面敏感推薦，不給 A 級。
- `block`：股票類標的需要公司財務資料，但沒有可用來源；或 value 與 quality 信心同時為 none。`block` 禁止列為可買候選。
- `not_applicable`：ETF、基金、商品、加密貨幣等不適用單一公司 EPS/PE 財報規則，必須用資產類別對應資料評估。

目前 API：

```text
/api/data/financial-audit?limit=80
```

Dashboard 的「財務資料稽核」會顯示分類計數、規則摘要，以及最新 warn/block 標的缺口。

## 量化指標規則

量化指標用來提高排序品質與勝率估計，但不覆蓋財務資料稽核。若股票類標的的財務資料為 `warn` 或 `block`，推薦等級仍會被限制，即使技術與量化指標偏多。

第一批新增指標：

- `adx_14` / `plus_di` / `minus_di`：使用 high/low/close 計算 14 日 Directional Movement。`ADX >= 25` 且 `+DI > -DI` 視為強趨勢偏多，trend 加分；`ADX >= 25` 且 `-DI > +DI` 視為強趨勢偏空，trend 扣分；`ADX < 15` 視為趨勢強度不足，trend 小幅扣分。
- `rs_1m_percentile` / `rs_3m_percentile`：同資產類別最新 snapshot 的 1 個月與 3 個月漲跌幅百分位。`rs_3m_percentile >= 75` 視為相對強勢，momentum 加分；`<= 25` 視為相對弱勢，momentum 扣分。`rs_1m_percentile >= 80` 或 `<= 20` 會做較小幅度的短線加減分。
- `volatility_regime_percentile`：近 20 日年化波動在最近約 120 個交易日 rolling volatility 中的百分位。`>= 85` 視為高波動 regime，risk 扣分並加入風險提示；`<= 35` 視為波動較平穩，risk 小幅加分。

資料品質規則：

- trend 必須追蹤 `adx_14`、`plus_di`、`minus_di`，缺值會反映在 factor data quality coverage。
- momentum 必須追蹤 `rs_1m_percentile`、`rs_3m_percentile`；peer 數量會回傳在 `rs_peer_count`，peer universe 來自同資產類別最新 `price_snapshots`。
- risk 必須追蹤 `volatility_regime_percentile`。
- 百分位指標依目前資料庫 snapshot 覆蓋率而變動；若 peer snapshot 不足，該欄位會是 `null`，不做加減分。

量化指標成效歸因 API：

```text
GET /api/recommendation/outcomes/quant-attribution?horizon_days=5&min_count=3
```

回傳內容會用已完成的 `recommendation_outcomes`，把當時 snapshot 的量化 metrics 分桶後計算勝率與報酬：

- `adx_direction`：`strong_bullish`、`strong_bearish`、`weak_trend`、`neutral_trend`
- `adx_strength`：`adx_35_plus`、`adx_25_35`、`adx_15_25`、`adx_below_15`
- `rs_3m`：`top_quartile`、`middle_50`、`bottom_quartile`
- `rs_1m`：`top_20`、`middle_60`、`bottom_20`
- `volatility_regime`：`high_85_plus`、`normal_35_85`、`calm_35_minus`

每個 bucket 會回傳 `count`、`win_count`、`win_rate`、`avg_return_percent`、`median_return_percent`、`worst_return_percent`、`best_return_percent`。樣本數低於 `min_count` 的 bucket 不列入，避免小樣本誤判。
## 2026-06-11 Unsnapped Universe Report

- `GET /api/universe/unsnapped?limit=500` lists enabled universe symbols that still have no saved snapshot.
- `GET /api/universe/unsnapped?limit=500&export=1` also writes Markdown and CSV reports under `research_exports/data_audits/`.
- The report classifies gaps using local scan errors and AegisTrader snapshot coverage; it does not call external data sources.

## 2026-06-11 Taiwan Full-Market Scope

- Decision record: when this project says "full market", it means listed and OTC common stocks, ETFs, and emerging stocks.
- Full market means TWSE listed common stocks, TPEx listed common stocks, ETFs, and emerging stocks.
- Excluded instruments: warrants, bull-bear/callable products, preferred shares, TDR, foreign holdings, bonds, and other non-stock/ETF instruments.
- The full-market scope is the persistent monitoring universe. A low score, low liquidity, or low confidence today does not remove a symbol from future scans.
- Opportunity candidates are only the current research priority shortlist derived from the latest full-market snapshots.
- No-quote symbols stay in membership but are excluded from opportunity ranking until an official quote becomes available; snapshots must not be fabricated.
- `GET /api/universe/scopes` returns the persisted scope definition and scan policy.
- `GET /api/universe/tw-full-market/sync` rebuilds the `tw_full_market` membership from the local AegisTrader snapshot.
- `GET /api/universe/tw-full-market/status` returns the clean full-market count and snapshot coverage.
- `GET /api/scan?scope=tw_full_market&limit=25&offset=0` scans only the clean Taiwan full-market scope.
- `GET /api/opportunities/tw-full-market?limit=50&min_priority=25` returns the current opportunity shortlist; it does not redefine the future scan universe.
- `GET /api/opportunities/tw-full-market/shortlist?limit=25` returns a stricter research shortlist: high-confidence data, non-single-point quote history, sufficient volume, relative strength, and trend filters.
- The latest dashboard UI now shows Full Market Scope and Strict Investment Shortlist on the first page.
- Background updates should run with `scope=tw_full_market`; UI scheduler start now sends this scope so future scans keep cycling through the full market, not only the current shortlist.
- `GET /api/recommendations/daily-log?force=1&limit=50` writes today's recommendation log. Scheduled logs run after 16:10 Asia/Taipei and are saved under `research_exports/daily_recommendations/` as Markdown plus JSONL for future backtesting.
- `GET /api/recommendations/daily-log/history?limit=20` returns past daily log entries from JSONL.
- `GET /api/recommendations/daily-log/performance?limit=100` returns 5/20/60D horizon performance with factor attribution (score / RS-3M / ADX / financial_audit / tier buckets). Meaningful once 10+ days of logs are accumulated.
- Clicking a strict shortlist card's manual research button opens the Manual Research page for that symbol.

## 推薦持續性指標（Recommendation Persistence Indicator）

把「個股是否持續出現在每日推薦」量化成一個獨立投資指標。動機：只出現一天的個股通常是雜訊；真正值得追蹤的是在多天推薦中持續、且分數穩定或上升的個股。

- 來源：`research_exports/daily_recommendations/recommendations.jsonl`（每個日期一筆）。
- API：`GET /api/recommendations/persistence?days=60&min_days=2`。
- 這是**獨立指標**，不改變 `research_score`、評級或財務稽核 gate；定位為加強排序與信心的訊號，未來可單獨回測。
- `persistence_score` 為 0-100，由五個成分加權（權重記在 `RECOMMENDATION_PERSISTENCE_RULES`，可調）：
  - `coverage` 35%：上榜天數 / 視窗內紀錄天數。
  - `recency_streak` 25%：在「已記錄天數」上的連續上榜長度，且需在最新一筆 log 在榜才給滿分。用已記錄天數計算，所以記錄日之間有間隔不會中斷連續性。
  - `score_trend` 15%：研究分數隨上榜的變化方向，上升偏多。
  - `score_level` 15%：歷次上榜的平均研究分數。
  - `tier_quality` 10%：core_watch 上榜次數佔比。
- 只上榜 1 天者標記 `insufficient_history`，不列為合格持續候選（一日尖峰視為雜訊）。
- UI：Dashboard「每日推薦」分頁的「持續上榜（值得追蹤的個股）」面板顯示此指標、依分數排序、可切換最少上榜天數門檻，點個股可看其逐日分數走勢。
- 隨每日 log 累積，指標會越有意義。
