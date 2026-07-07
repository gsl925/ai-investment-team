# AI 投研團隊使用說明書

本系統是一個個人用的 Agentic AI Investment Team MVP。它的主軸不是讓使用者手動輸入個股才開始研究，而是先由系統掃描候選池，主動找出值得注意的標的，再由 Agent 團隊產生投研提報，最後由使用者本人作為 PM 做決策。

系統不會自動下單，也不應被視為投資顧問或保證獲利模型。

## 1. 啟動與維運

最簡單方式是直接執行：

```powershell
cd Investment
.\start_backend_and_scheduler.bat
```

這會：

- 檢查 `http://127.0.0.1:8765` 後端是否已存在。
- 若不存在，啟動 `python app.py`。
- 呼叫 `/api/scheduler/start` 開啟背景輪巡。
- 顯示最新 scan、水池覆蓋率與下次輪巡時間。

檢查背景輪巡狀態：

```powershell
.\check_scheduler_status.bat
```

若用排程或命令列不想停在 `pause`：

```powershell
.\start_backend_and_scheduler.bat nopause
.\check_scheduler_status.bat nopause
```

手動啟動後端：

在專案目錄執行：

```powershell
cd Investment
python app.py
```

開啟：

```text
http://127.0.0.1:8765
```

啟動後會使用本機 SQLite：

```text
runtime_db/investment_live.db
```

### 確認服務是否啟動

看到終端機顯示類似訊息：

```text
Investment helper running at http://127.0.0.1:8765
```

或在 PowerShell 檢查：

```powershell
netstat -ano | Select-String ':8765'
```

若看到 `LISTENING`，表示服務正在跑。

### 停止服務

如果是在目前終端機直接啟動，可以按：

```text
Ctrl + C
```

如果服務在背景執行，先查 PID：

```powershell
netstat -ano | Select-String ':8765'
```

找到最後一欄 PID 後停止：

```powershell
Stop-Process -Id <PID> -Force
```

範例：

```powershell
Stop-Process -Id 29516 -Force
```

### 重啟服務

先停止舊服務，再執行：

```powershell
cd Investment
python app.py
```

### 8765 port 被占用

如果啟動時失敗，可能是舊服務還在佔用 port。

查詢：

```powershell
netstat -ano | Select-String ':8765'
```

停止對應 PID：

```powershell
Stop-Process -Id <PID> -Force
```

### 檢查 Python 語法

修改程式後可先檢查：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "
import ast, pathlib
for f in ['common.py','outcomes.py','tw_universe.py','scheduler.py','active_etf.py','data_health.py','app.py']:
    ast.parse(pathlib.Path(f).read_text(encoding='utf-8'))
    print(f, 'syntax ok')
"
```

### 檢查資料庫狀態

啟動服務後打開：

```text
http://127.0.0.1:8765/api/db/status
```

或用 PowerShell：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/db/status' | ConvertTo-Json -Depth 3
```

### 常見啟動問題

#### 瀏覽器打不開頁面

先確認服務是否在跑：

```powershell
netstat -ano | Select-String ':8765'
```

如果沒有 `LISTENING`，重新執行：

```powershell
python app.py
```

#### 分析或掃描失敗

可能原因：

- Yahoo Finance 暫時連不上。
- 網路或 proxy 問題。
- 代號格式錯誤。
- API rate limit。

可先用少量標的測試：

```text
http://127.0.0.1:8765/api/analyze?symbols=AAPL
```

#### 中文變亂碼

PowerShell 讀 Markdown 時請指定 UTF-8：

```powershell
Get-Content -Path USER_GUIDE.md -Encoding UTF8
```

## 2. 頁面結構

目前 Dashboard 分成兩個主要頁面：

```text
最新進度
市場雷達
主動式 ETF 異動
手動投研
```

### 最新進度

這是預設首頁。打開 UI 後會自動讀取 `/api/dashboard/latest`，每 30 秒刷新一次。

它會顯示：

- 背景輪巡是否健康、thread 是否存活、是否 overdue。
- 下次輪巡時間與最新 scan 編號。
- 水池總數、已有 snapshot 數、近 24 小時更新數。
- 最新推薦 / 風險候選。
- 主動式 ETF 個股線索。

這一頁的目的，是讓 PM 打開 UI 就先看到「系統目前推薦你注意什麼」與「輪巡有沒有正常跑」。

### 市場雷達

這是主頁，也是未來主軸。

用途：

- 從 `universe` 候選池主動掃描標的。
- 找出異常波動、技術轉強、趨勢事件與風險事件。
- 產生 opportunity queue，讓使用者不用自己先想到股票代號。
- 點選「進入手動投研」後，可對該標的產生 Agent 投研報告。

重要：市場雷達不是只找看多機會，也會找風險事件。大跌、波動飆高、分數偏弱的標的，也可能被排到很前面，因為它值得 PM 注意。

目前版本是「候選池掃描」，不是真正全市場掃描。掃描數量只能控制最多掃幾檔，但不能超過 `universe` 裡已啟用的候選池總數。舉例來說，如果候選池只有 25 檔，掃描數量填 50 或 200，實際仍只會掃 25 檔。

目前預載候選池約 25 檔，包含：

- 美股/ETF。
- 台股。
- 原物料。
- 虛擬貨幣。

未來要接近全市場，需要先擴大 universe，例如 S&P 500、Nasdaq 100、台股 0050/006208 成分股、上市櫃大型股、crypto top N。

市場雷達頁有「擴大候選池」按鈕，會加入 starter universe。這不是完整全市場，但會把候選池從最小 MVP 擴大到較完整的第一批觀察名單。

市場雷達頁也有「匯入水池 CSV」按鈕。這會讀取專案目錄內的：

```text
universe_import.csv
```

並 upsert 到 `universe` 表。CSV 欄位如下：

```csv
symbol,name,asset_type,currency,sector,industry,enabled
AAPL,Apple Inc.,美股/ETF,USD,Technology,Consumer Electronics,1
2330.TW,台積電,台股,TWD,半導體,晶圓代工,1
```

只有 `symbol` 必填；其他欄位可空白。`enabled=0` 可以保留在水池文件中但暫停掃描。這是未來擴大到 500、1000 檔以上時的主要維護方式。

市場雷達頁的「最大化水池」按鈕是低頻擴建用。它會連 Yahoo Finance screener，從活躍股、漲跌幅榜、成長/價值、基金/ETF 等來源匯入標的。這類任務不需要每小時跑；久久跑一次即可。

目前策略是：

```text
低頻：最大化水池，盡量把可投資標的放進 universe
高頻：背景掃描優先更新核心候選，再用剩餘批次探索大水池
```

背景掃描每輪會拆成兩部分：

- 核心候選：已有 snapshot 或曾進 opportunity queue 的標的，短週期追蹤。
- 大水池探索：從 `cursor_offset` 繼續往後掃，慢慢覆蓋整個水池。

### 手動投研

這是保留給指定標的深入研究的頁面。

用途：

- 手動輸入代號分析。
- 產生 Agent 投研報告。
- 查看單一標的即時新聞、風險、技術訊號。
- 執行輔助性回測。

這一頁不是主軸，但會保留，因為 PM 有時仍會想指定某個標的深入研究。

### 主動式 ETF 異動

這是針對台股主動式 ETF 的新資料源。因主動式 ETF 會每日揭露實際投資組合，持股新增、加碼、減碼、刪除可以視為法人級選股行為的線索。

頁面用途：

- 匯入每日主動式 ETF 持股異動。
- 保存 ETF、個股、異動類型、股數變化、權重、來源與時間戳記。
- 彙總同一個股被幾檔 ETF 新增/加碼或減碼/刪除。
- 點進「手動投研」做完整價格、新聞與風險覆核。

匯入檔案：

```text
active_etf_changes.csv
```

欄位：

```csv
trade_date,etf_symbol,etf_name,issuer,stock_symbol,stock_name,change_type,previous_shares,current_shares,share_delta,weight,estimated_price,estimated_value,source
```

新增/加碼會被列為偏正向研究線索；減碼/刪除會被列為風險觀察。系統會把異動個股自動加入 `universe`，讓市場雷達後續可以追蹤。

重要：這不是下單推薦。主動式 ETF 的經理人行為可以提供線索，但仍需要確認價格位置、成交量、產業新聞、ETF 規模、是否只是再平衡，以及是否多檔 ETF 同向行動。

資料可信度規則：

- `verified`：來源是官方 URL，例如 TWSE、TPEx 或投信公司揭露頁。
- `third_party`：來源是第三方整理站或非官方來源，需人工覆核。
- `unverified`：手動或示範資料，不能作為可靠投研證據。

目前內建 `active_etf_changes.csv` 是示範格式，source 為 `demo_manual_unverified`。它只能驗證系統流程，不代表真實 ETF 異動。

稽核 API：

```text
http://127.0.0.1:8765/api/active-etf/audit
```

背景輪巡會在台北時間每日 `16:40` 與 `20:40` 檢查並匯入 `active_etf_changes.csv`。同一天同一時段只會成功執行一次，執行紀錄保存在 `active_etf_import_runs`。若要手動補跑，可呼叫：

```text
http://127.0.0.1:8765/api/active-etf/schedule/run-due?force=1
```

## 3. 兩個分數的差異

目前最容易混淆的是：

```text
雷達優先度
投研分數
```

### 雷達優先度

雷達優先度是市場雷達右上角的數字，範圍約為 `0~100`。

它代表：

```text
這個標的值不值得你注意
```

它不是看多分數，也不是買進分數。

高雷達優先度可能代表：

- 單日大漲。
- 單日大跌。
- 5 日變化很大。
- 波動率異常。
- 有新聞或風險警示。
- 技術分數很高，值得研究。
- 技術分數很低，可能有風險，需要注意。

所以可能出現：

```text
雷達優先度 100
投研分數 29
動作：偏空/暫避
```

這代表「高度值得注意，但原因是風險很高」，不是看多。

### 投研分數

投研分數是方向性研究分數，範圍是 `0~100`。

大致解讀：

```text
72-100：偏多觀察 / 可進候選清單
58-71 ：中性偏多 / 追蹤等待確認
43-57 ：中性觀望
0-42  ：偏空 / 暫避
```

投研分數高，表示目前資料比較偏向看多或值得進一步研究；投研分數低，表示目前資料偏弱或風險較高。

但它仍然不是下單指令，只是研究排序工具。

## 4. 分數依據

目前分數是 deterministic heuristic，也就是明確規則加減分，不是黑箱 AI。

投研分數主要依據：

- 產業分類是否取得。
- 近 1 個月價格動能。
- 近 3 個月價格變化。
- 20 日與 60 日均線排列。
- 近 3 個月年化波動。
- 近期日波動。
- 新聞標題語氣。
- 資料缺口與風險提示。

雷達優先度主要依據：

- 投研分數是否進入偏多或偏空區間。
- 單日變化是否超過波動門檻。
- 5 日變化是否明顯。
- 是否有高優先或中優先 alert。
- 是否找到可能佐證新聞。

## 5. Agent 投研報告

按下「投研報告」或從市場雷達點「進入手動投研」後，系統會產生 6 份報告：

- Data Retrieval Agent：檢查資料完整性、snapshot 與資料缺口。
- Technical Analyst Agent：分析技術面、均線、波動與分數。
- Macro & News Analyst Agent：整理新聞語氣與可能事件。
- Flow / On-chain Agent：依資產類別列出籌碼、資金流或鏈上資料缺口。
- Risk Officer Agent：檢查警示、風控與部位限制。
- Chief Strategist Agent：交叉驗證前面報告，提出給 PM 覆核的行動提案。

目前 Agent 是 deterministic 版本，目的是先建立「分工、格式、資料保存與可追溯性」。未來才會接 OpenAI、LangGraph 或 CrewAI，讓 LLM 負責摘要、推理與交叉驗證。

## 6. 資料保存

所有重要結果都會保存到：

```text
runtime_db/investment_live.db
```

主要資料表：

```text
universe             候選池
price_snapshots      每次分析的價格、分數、波動、建議
technical_signals    加減分訊號
news_items           新聞標題、來源、連結、情緒
event_alerts         波動或事件警示
recommendations      投研分數、動作、風險與 metrics
agent_reports        Agent 投研報告
scan_runs            每次市場掃描紀錄
opportunities        市場雷達產生的機會清單
backtest_runs        輔助回測紀錄
user_decisions       未來保存 PM 最終決策
```

除了 SQLite，系統也會輸出跨平台文字檔：

```text
research_exports/
  scans/
    scan_<run_id>_<timestamp>.md
    latest_scan.md
  reports/
    research_<symbol>_<timestamp>.md
    latest_<symbol>.md
  jsonl/
    opportunities.jsonl
    agent_reports.jsonl
```

設計目的：

- SQLite 負責查詢、排序與結構化儲存。
- Markdown 負責人類閱讀、跨平台、跨模型上下文交換。
- JSONL 負責給其他程式、模型或資料管線增量讀取。
- 檔名包含時間戳，方便追蹤每次掃描或報告的版本。

因此未來就算換模型、換 UI 或搬到其他系統，也可以直接讀 Markdown/JSONL，不必只依賴原本的 app。

## 7. API 速查

```text
GET /api/universe?limit=100
GET /api/scan?limit=25&min_priority=25&refresh_minutes=60
GET /api/analyze?symbols=AAPL,2330.TW
GET /api/research?symbols=AAPL,2330.TW
GET /api/history?symbol=AAPL&limit=30
GET /api/db/status
GET /api/backtest?symbols=AAPL,SPY&days=180&cost_bps=5
```

## 8. 設計決策與曾經的疑惑

### 疑惑 1：為什麼右上角 100，但標的是偏空？

一開始市場雷達卡片右上角只顯示一個 `0~100` 數字，容易被誤解為看多分數。

實際上那個數字是雷達優先度，不是投研分數。它代表「值得注意」，可能是因為機會，也可能是因為風險。

後來 UI 改成：

```text
雷達優先
投研分數
```

原因是要區分：

```text
雷達優先度 = 注意力排序
投研分數 = 方向性研究分數
```

### 疑惑 2：系統是不是需要我指定個股才會蒐集資料？

最早版本偏向手動查詢，使用者輸入代號後才抓資料。這會漏掉使用者沒有想到的投資機會。

後來方向改成：

```text
Universe 候選池
-> Market Scanner
-> Opportunity Queue
-> Agent Research Trigger
-> PM Dashboard
```

也就是系統主動掃描候選池，把值得看的標的推給 PM。

手動查詢仍保留，但不再是主軸。

### 疑惑 3：為什麼偏空標的也會出現在機會清單？

市場雷達不是只找買進機會，它也要找風險事件。

如果某個標的大跌、波動飆高、新聞偏負或技術分數很低，它也應該被放到 PM 面前。這比較接近法人風控流程：重要風險和重要機會都要被看見。

### 疑惑 4：為什麼沒有直接做全市場？

真正全市場需要處理：

- 資料源限制。
- API rate limit。
- 台股、美股、ETF、crypto、原物料格式不同。
- 新聞與價格時間序列對齊。
- 快取、排程、錯誤重試。

所以目前先做「候選池掃描」：

- 先用預載 universe。
- 跑出 opportunity queue。
- 驗證資料結構與 UI。
- 未來再擴大到 S&P 500、Nasdaq 100、0050 成分股、上市櫃大型股、crypto top N。

一次到位做全市場會遇到：

- Yahoo Finance / 其他 API rate limit。
- 每檔都抓新聞會非常慢。
- 台股、美股、ETF、crypto、原物料資料格式不同。
- 成分股名單需要定期更新。
- 掃描時需要分批、重試、快取與排程，不能每次開頁面就全量重抓。
- 真正全市場會產生大量 snapshot，需要資料庫索引、清理策略與背景任務。

建議分批路線：

```text
Phase 1: starter universe 100~150 檔
Phase 2: 美股大型股/ETF 300~500 檔
Phase 3: 台股大型股/ETF 200~500 檔
Phase 4: crypto top 50~100 + 原物料
Phase 5: 定時背景掃描、增量更新、只對異常標的抓新聞與觸發 Agent
```

### 疑惑 6：為什麼我增加掃描數量，結果沒有增加？

因為掃描數量只是上限，不會自動增加候選池。

實際掃描數量由兩個因素決定：

```text
實際掃描數量 = min(你設定的掃描數量, universe 裡已啟用的候選池數量)
```

如果 universe 目前只有 25 檔：

```text
掃描數量 25 -> 掃 25 檔
掃描數量 50 -> 還是掃 25 檔
掃描數量 200 -> 還是掃 25 檔
```

要增加實際掃描數量，必須擴大 `universe`，不是只調高掃描數量。

### 疑惑 7：系統有保存歷史紀錄嗎？

有。

每次分析、投研或掃描成功時，系統會把資料寫入 `runtime_db/investment_live.db`：

- `price_snapshots`：每次價格、分數、波動、動作。
- `technical_signals`：每次分數的加減分依據。
- `news_items`：新聞標題、來源、連結、情緒。
- `event_alerts`：波動或事件警示。
- `recommendations`：投研分數、建議與風險。
- `opportunities`：市場雷達機會清單。
- `agent_reports`：Agent 投研報告。

所以不需要每次從零開始判斷。現在市場雷達已會讀取本機歷史快照，顯示：

- 歷史筆數。
- 投研分數變化。
- 本機快照期間的價格變化。

目前歷史資料還是早期資料，筆數不多；等每天或每次掃描持續累積後，雷達分數和投研分數會更有趨勢依據。

市場雷達現在有快取 TTL：

```text
refresh_minutes=60
```

意思是：如果某標的最近 60 分鐘內已有 `price_snapshots`，掃描時會直接用本機資料，不重新抓 Yahoo Finance，也不新增重複 snapshot。若超過 60 分鐘，才重新抓資料並保存新 snapshot。

如果想強制刷新，可把快取分鐘設為：

```text
0
```

目前已有的時間戳記：

- `price_snapshots.captured_at`：每次行情/分數 snapshot 時間。
- `news_items.captured_at`：新聞保存時間。
- `event_alerts.captured_at`：警示產生時間。
- `agent_reports.generated_at`：Agent 報告產生時間。
- `scan_runs.created_at`：市場掃描時間。
- `opportunities.created_at`：機會清單產生時間。
- Markdown 檔名與內容也都有 timestamp。

### 背景自動掃描

市場雷達頁可以啟動背景自動掃描。

參數：

- `間隔分鐘`：每隔幾分鐘掃一次。
- `每批檔數`：每次最多掃幾檔 universe。
- `快取分鐘`：多久內已抓過的標的不重抓。
- `最低優先`：沿用市場雷達的最低雷達優先度。
- `資產類別`：沿用市場雷達的資產類別篩選。

啟動後，系統會在 app 背景執行：

```text
每 N 分鐘
-> 依批次游標掃描下一批 universe
-> 使用快取 TTL 避免重複抓資料
-> 保存 scan_runs / opportunities / snapshots
-> 輸出 Markdown / JSONL
```

批次游標的意思是：如果 universe 有 148 檔，每批 25 檔，第一次會掃第 1-25 檔，下一次掃第 26-50 檔，持續往後輪巡；掃到尾端後再回到第 1 檔。Dashboard 會顯示上次掃描的區間與下次起始位置。

快取分鐘用來降低重複抓資料。若某檔標的在快取時間內已經有 snapshot，背景掃描會直接讀 SQLite 裡的舊分析結果，不會重新連 Yahoo/新聞來源。若設為 `0`，代表每次都強制刷新。

目前背景掃描是本機 app 內的 daemon thread。也就是說：

- app 關掉後，背景掃描會停止。
- 重啟 app 後，需要重新按「啟動背景掃描」。
- 未來可再改成 Windows Task Scheduler 或獨立 worker。

### 疑惑 8：只有 SQL 會不會不利於跨平台、跨模型？

會，所以系統增加了文件層。

每次市場掃描會同步產生：

```text
research_exports/scans/*.md
research_exports/jsonl/opportunities.jsonl
```

每次 Agent 投研會同步產生：

```text
research_exports/reports/*.md
research_exports/jsonl/agent_reports.jsonl
```

這樣可以同時滿足：

- 資料庫查詢。
- 人類閱讀。
- 其他模型讀取。
- 其他程式增量處理。
- 長期研究紀錄保存。

### 疑惑 5：回測是不是主線？

不是。

回測是輔助工具，用來檢查某些技術規則的歷史表現。但本專案主線是：

```text
主動掃描
-> Agent 投研提報
-> PM 覆核
-> 人工決策
```

回測可以保留，但不應蓋過市場雷達與 Agent 投研團隊。

## 9. 未來方向

接下來優先順序：

1. 擴大 universe。
2. 加入排程，定時掃描市場。
3. 改善 opportunity priority 規則。
4. 接入更可靠的新聞與公告來源。
5. 接入台股籌碼、加密貨幣鏈上、總經資料。
6. 增加使用者投資設定與持倉。
7. 接入 OpenAI / LangGraph / CrewAI。
8. 產生日報、週報與事件報告。
## 主動式 ETF：每日成分股快照流程

目前正式流程改成保存「每日完整成分股快照」，再由系統自動比對新增、刪除、加碼、減碼。

為什麼改：

- 只匯入異動清單時，使用者無法判斷資料是真是假。
- 舊的 `active_etf_changes.csv` 曾出現欄位錯位與中文編碼錯誤，導致畫面看到的異動是錯的。
- 完整 holdings 快照可以保留原始資料；未來即使修改比對規則，也可以重新從原始快照產生異動摘要。

正式匯入檔：

```text
active_etf_holdings.csv
```

欄位：

```csv
trade_date,etf_symbol,etf_name,issuer,stock_symbol,stock_name,shares,weight,market_value,source,source_url
```

匯入：

```text
http://127.0.0.1:8765/api/active-etf/holdings/import?file=active_etf_holdings.csv
```

查詢原始快照：

```text
http://127.0.0.1:8765/api/active-etf/holdings?limit=500
```

查詢系統比對後的異動摘要：

```text
http://127.0.0.1:8765/api/active-etf/changes?limit=100
```

注意：`active_etf_changes` 是摘要，不是原始資料。原始資料以 `active_etf_holdings` 為準。

### 資料稽核紀錄

目前系統的優先順序是：先確保數字可追溯，再談推薦品質。

每次匯入 `active_etf_holdings.csv` 會同時留下兩份紀錄：

- DB：`data_import_audits`
- Markdown：`research_exports/data_audits/`

查詢 API：

```text
http://127.0.0.1:8765/api/data/audits?limit=50
```

稽核內容包含：

- dataset 名稱。
- source file / source url / source label。
- 匯入時間。
- 原始 row count。
- inserted / updated / skipped。
- 欄位清單。
- 警告與錯誤。
- 衍生摘要統計，例如 ETF 數、股票數、日期、比對後新增/刪除/加碼/減碼筆數。

若資料來源是 demo/manual/unverified，系統會保存資料，但 audit 狀態會是 `warning`，不能當成已驗證投資證據。

### 真實第三方 ETF 資料匯入

目前已接入 ZDS ETF Tracker：

```text
https://www.zdsetf.com
```

手動匯入 API：

```text
http://127.0.0.1:8765/api/active-etf/import-zdsetf
```

建議使用的統一同步 API：

```text
http://127.0.0.1:8765/api/active-etf/sync
```

來源狀態 API：

```text
http://127.0.0.1:8765/api/active-etf/source-status
```

匯入內容：

- 23 檔主動式 ETF。
- 每檔 ETF 詳細頁的全部持股。
- 每檔 ETF 詳細頁的新增、加碼、減碼、刪除明細。
- 每筆資料的來源 URL。
- 匯入 audit 與 Markdown 文件。

查詢：

```text
http://127.0.0.1:8765/api/active-etf/holdings?limit=500
http://127.0.0.1:8765/api/active-etf/changes?limit=100
http://127.0.0.1:8765/api/data/audits?dataset=active_etf_zdsetf
```

資料等級：

- `third_party`：ZDS ETF Tracker 公開頁面。
- `verified`：未來若接 TWSE、TPEx、投信官網等官方來源才會標記。

背景排程策略：

- 台北時間 `16:40`：收盤後第一次更新。
- 台北時間 `20:40`：晚間補更新，處理資料源延遲。
- 更新順序：官方來源占位 -> ZDS ETF Tracker -> 手動 CSV -> 保留既有快取並標記 stale。
## 開機後一次性補更新

如果每天或週末會停機，開機後可先執行：

```powershell
.\sync_all_now.bat
```

它會補跑：

- ETF 資料來源同步：`/api/active-etf/sync`。
- 候選池掃描：`/api/scan`。
- Markdown / JSONL 匯出。
- DB snapshot 與 opportunity queue 更新。

預設參數：

```text
scan_limit = 100
refresh_minutes = 240
offset = 0
```

可自行指定：

```powershell
.\sync_all_now.bat 100 240 0
```

這個 batch 不會啟動背景輪巡。若要背景輪巡，才使用 `start_backend_and_scheduler.bat`。

## 低頻集中擴大候選池

擴大水池不需要高頻輪巡，建議久久集中跑一次：

只建立候選池名冊：

```powershell
.\build_universe_only.bat
```

建立候選池後再分批補 snapshot：

```powershell
.\expand_pool_now.bat
```

預設：

```text
Yahoo screener count = 250
scan_limit = 100
rounds = 10
refresh_minutes = 1440
```

指定參數：

```powershell
.\expand_pool_now.bat 250 100 10 1440
```

它會依序：

- 同步 ETF 第三方資料。
- 將 ETF holdings 股票併入 universe。
- 匯入 `universe_import.csv`。
- 從 Yahoo Finance screeners 擴大 universe。
- 用 `/api/scan` 分批補 snapshot。
- 不啟動背景 scheduler。
