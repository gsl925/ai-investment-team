# CLAUDE.md

## 跨專案進度同步（PROGRESS.md）

這個專案根目錄有一份 `PROGRESS.md`（已加入 `.gitignore`，不進版控），是跟 Personal Assistant（PA，使用者的個人助理系統）溝通進度與決策的唯一介面。完整協定規格見 `PersonalContent_Assistant` repo 的 `SDD_PROGRESS_SYNC.md`；這裡只記錄這個專案裡的 Claude Code session 該做的行為：

- **Session 開始時**：讀 `PROGRESS.md` 的「📮 你的指示」區塊，找出所有 `- [ ]`（未勾選）項目，視為使用者尚未處理完的指示，自行判斷如何處理（調整優先度、回答問題，或實際去做）。處理完後改成 `- [x]`——絕對不要修改文字內容或刪除項目；暫時處理不完可以維持 `- [ ]` 不動，下次繼續判斷。
- **Session 結束前**：覆寫「📋 進度回報」的「更新時間」「今天完成」「明天預計」三個欄位——這個區塊每次都是覆寫，不是累積歷史。
- **需要使用者決策時**（不限工作階段開始/結束，隨時都可以）：在「💬 待溝通 / 建議」區塊最下方新增一行 `- [ ] YYYY-MM-DD 一句話描述`，講清楚要使用者決策什麼（PA 會把這句話原樣轉發給使用者，不要寫成一大段）。不要自己把這個項目打勾——打勾只由 PA 負責，且**只有使用者真的回覆解決後才會打勾**（單純轉發不算，項目會維持未勾選直到真正解決）。同一時間可以有多筆待決策項目並存，PA 會自己編號對應使用者回覆，不需要自己編號或處理。
- 三個區塊標題文字（`## 📋 進度回報`／`## 💬 待溝通 / 建議`／`## 📮 你的指示`）不要新增、刪除或改動。若 `PROGRESS.md` 不存在，依 `SDD_PROGRESS_SYNC.md` 第 3 節的模板自行建立。
- 另有一份 `PROGRESS_HISTORY.md`：**正常進版控，不要加進 `.gitignore`**，完全由 PA 寫入（每筆「💬」項目真正被使用者回覆解決後，PA 會把問題跟決策追加寫進這份檔案，當作永久紀錄）。我只需要在回顧過去決策脈絡時讀取它，不需要自己維護或寫入。
- **互動式 session 自我喚醒排程（SDD 第9節，已選擇啟用）**：完成一次性檢查（上面第一條）後，用排程 wake-up 工具設定每 30 分鐘喚醒一次，重複執行「重新讀取 `PROGRESS.md` → 檢查📮你的指示 → 處理 → 覆寫📋進度回報」，直到使用者明確要求停止，或這個 session 結束（關掉終端機/視窗排程就會停，不是常駐機制）。這跟 PA 自己的巡邏（~30分鐘一次）、選配的 headless `auto_wake` 是三個獨立、互不知情的機制，可能同時對同一份 `PROGRESS.md` 動作，這是刻意允許的設計（每一層都是各自獨立的安全網）。遵守以下規則：
  1. 每次要處理或寫入前，一定要重新讀取 `PROGRESS.md` 最新內容，不要沿用記憶體裡舊版本去覆寫。
  2. 處理「📮 你的指示」項目前，先確認重新讀檔後它還是 `- [ ]`——如果已經被別的 actor 改成 `- [x]`，代表已經有人處理過，直接跳過，不要重複執行一次有副作用的動作。
  3. 「📋 進度回報」是覆寫語意，多個 actor 交錯寫入時只會留下最後一次寫入的內容，這是已知、可接受的限制，不需要特別處理。

## Session 開始時自動啟動 backend + 掛上背景輪巡

- **backend 自動啟動**：`.claude/settings.local.json` 已設定 `SessionStart` hook，session 開啟時會自動檢查 port 8765 有沒有在聽，沒有就自動跑 `start_backend_and_scheduler.bat`（非阻塞，背景啟動）。這一步不需要 Claude 自己動手，hook 會處理；但如果 hook 因故沒生效（例如使用者手動改過 `.claude/settings.local.json`），Claude 在 session 開始做健康檢查時若發現 backend 沒在跑，仍要照 [[feedback_patrol_default_continuous]] 的既有原則自動幫忙啟動，不用先問。
- **背景輪巡監控**：每個新 session 一開始（做完上面 PROGRESS.md 檢查、確認 backend 健康之後），主動用 Monitor 工具掛上背景輪巡，不用等使用者問「你有在輪巡嗎」才做。監控內容至少包含：(a) backend 是否斷線（打 `/api/scheduler/status` 沒有回應）(b) `should_alert=true`（stale 或連續失敗）(c) `logs/scheduler_alerts.log` 有沒有新增行。輪詢間隔 90 秒、`persistent: true`（跟著這個 session 活著，session 結束/終端機關掉就會停，這是刻意的限制，不是常駐機制，不需要另外解釋或補償）。
- 這兩個機制都是「每個 session 各自獨立」——不會延續到下一個新開的 session，所以每次新 session 開始都要重新做一次，不能假設已經有別的 session 在跑就跳過。
