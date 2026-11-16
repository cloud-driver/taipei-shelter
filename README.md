# 安行 ANXING — 臺北官方避難名冊與 OSM 步行導航

依上層目錄的兩份 PDF 建立的 **macOS / Debian 後端與瀏覽器測試原型**。FastAPI 提供 API，SQLite 保存帳號、版本、災情與原子容量配置，原生 HTML/CSS/JavaScript 提供操作與管理介面。

主頁已接入全市官方避難公開資料與真實 OpenStreetMap 步行路網，提供道路路線參考。官方名冊不代表現場已開設，入口與震後道路未查核，沒有空位或安全保證。另保留「容量演練」：25 個節點、40 條路段、6 個場所與 9 個需求區是合成資料，帳號、容量分流、災情與供需實驗都屬演練。

## 快速開始（macOS）

需要 Python 3.11 以上及網路下載套件。建議 Python 3.12。

```bash
git clone https://github.com/cloud-driver/taipei-shelter.git
cd taipei-shelter
./start.sh
```

也可在 Finder 雙擊 `啟動安行.command`。首次建立 `.venv` 並安裝固定版本套件；之後直接啟動。

| 網頁 | 本機網址 |
|---|---|
| 主操作介面 | http://127.0.0.1:8765/ |
| 獨立 API 整合測試頁 | http://127.0.0.1:8765/test |
| 管理介面 | http://127.0.0.1:8765/admin |
| 可操作 API 文件 | http://127.0.0.1:8765/docs |
| OpenAPI 規格 | http://127.0.0.1:8765/openapi.json |

在啟動的終端按 Ctrl-C 停止。若 8765 已有人使用，可執行 `PORT=8766 ./start.sh`。

預設僅監聽本機。不要直接用多台機器共用 iCloud 同步中的 SQLite 檔；同一個資料庫只由一個主機程序管理。Debian 建議將資料放在本機磁碟 `/var/lib/taipei-shelter`。

## 管理金鑰

首次啟動自動產生隨機金鑰，存在 `.local/admin-token`，權限 600。

```bash
.venv/bin/python -m app.cli admin-token
```

將終端顯示的值貼到管理頁。本頁只將管理金鑰留在記憶體，重新整理後清除。也可由環境變數 `SHELTER_ADMIN_TOKEN` 指定至少 24 字元的值；不要放在網址、公開截圖或版本庫。

## 建議展示順序

先在預設「公開資料導航」頁查詢行政區、場所、位置來源；設定戶外起點，確認需求與出發前條件，計算 OSM 路線。範例測試起點：25.0422, 121.5354。名冊容納人數不能當作即時剩餘空位。需要無階梯或輪椅條件時，目前資料不足，會拒絕推薦。

以下切換「容量演練」，展示合成資料流程：

1. 選「使用輪椅」、同行 2 人、需無障礙 1 人，完成三項出發前確認並規劃。
2. 接受配置；記下路線經過的路段（預設由 `n22` 往 `sB` 時為 `e-n22-n23`）。
3. 下方回報該路段阻斷，原路線會失效，並釋放在途配置。
4. 管理頁確認封閉，填寫查核理由。主頁重新規劃會避開阻斷，輪椅不走樓梯，家庭不拆分。
5. 接受新路線，按「已到達」。到達者繼續計入負載；按「已離開」才釋出。取消及逾時也會釋出在途配置。
6. 管理頁明確解除災情；確認封閉不會因時間流逝自動恢復通行。
7. 分流實驗比較 A 最近場所、B 容量分流、C 容量與災情；匯出 CSV。
8. 到 `/test` 執行整合測試。完整測試另外執行 `./test.sh`。

## 公開資料導航

已收錄419筆官方收容名冊、13筆防災公園及22443筆防空設施；名冊查詢覆蓋12行政區。321筆震災適用／備用與公園紀錄中290筆可定位，31筆待核對仍保留清單。圖層、行政區、名稱與OSM輪椅評級可篩選，完整名冊可下載CSV。161478節點／221520路段的本機OSM圖可計算道路轉折、距離、時間與替代場所。OSM資料實際時間為2026-07-15，不代表當日災情。

已加入Wheelmap相同的OSM無障礙標籤：73筆部分標記、13筆匹配避難名冊，綠色場所不等於全程可通行。詳見 `docs/PUBLIC_DATA.md` 的來源、授權、匹配、限制、API與更新指令。

## 演練已實作功能

- 訪客免登入；帳號註冊、登入、登出、本人需求修改及刪除。至少年齡或行動需求一項，需同意保存。年齡不推定失能或速度。
- 行動模式、速度、休息、樓梯、坡度、寬度、無階梯入口、廁所、同行與協助者確認。輪椅／輔具／需協助預設無障礙。
- Dijkstra 計算可通行路網；先排除封閉、需求不符與未知無障礙條件，再用正規化時間、風險、負載排序。
- 群組容量貪婪配置；一般人數不能占用保留的無障礙適用容量。未知容量不任意填值。
- `BEGIN IMMEDIATE` 將檢查、釋放舊配置與建立新配置包在同一交易內；唯一請求識別防止重送重複扣容量。重規劃失敗時保留原有有效配置。
- 提案 10 分鐘、接受後 20 分鐘的配置期限；到達者直到離開才釋出。登出／刪除後到達人數轉為匿名背景負載，不製造空位。
- 需求版本更新、場所狀態變更與可信災情更新，使受影響在途路線失效；輪詢只更新資訊，不自動切換使用者路線。
- 回報指定完整路段或場所、時間、分類、說明及可選照片；去重、頻率限制、待確認／暫時避開／確認封閉／解除與審核紀錄。
- JPEG／PNG 最多 2MB、1200 萬像素，重新編碼並移除 EXIF、GPS 與原檔名；照片只限回報本人與管理端讀取。沒有自動辨識／模糊人臉功能，請勿上傳個資。
- 演練地震事件版本控制；舊版和重複版本拒絕覆寫；只允許明確標示的演練訊息。
- 供需共享容量、合成人口密度、缺口圖、5／10／15 分鐘可達節點圖、三策略到達時間／負載／缺口比較、JSON／CSV 結果保存。
- 相同種子、需求及障礙真值；App 使用／接受與非使用人群不重複；真實障礙與收到的真假回報分開；共同安置者時間逐人加權，分開報無障礙群組成功率。
- 離線預存示意圖與本機路徑參考、手動上傳文字回報佇列；離線停止雲端容量協調，不宣稱有即時狀態。
- 標準資料匯入與品質報告、備份、30 天保存期限清理工具。

## 離線操作

第一次連網開主頁，切到「容量演練」，按「預存離線示意圖」，再停止後端／中斷網路。重新整理後可載入快取頁面，顯示資料取得時間與離線標示。離線規劃沿最近一次示意路網計算，不建立容量配置。

帳號需求另需按「同意預存需求至本機」，不會自動存年齡或帳號。文字回報必須勾選暫存同意，復網後由使用者手動上傳；離線不暫存照片。回報逾 24 小時會被後端拒絕，需要重新查核與建立新回報。

憑證存在分頁 `sessionStorage`；回報佇列綁定分頁範圍，換帳號時不會將上一身分的待上傳內容送出。離線圖與同意預存需求存在 `localStorage`，可用帳號頁按鈕清除。公用電腦請清除後再離開。

## 專案結構

| 路徑 | 內容 |
|---|---|
| `app/main.py` | FastAPI 應用：中介層（同源寫入、大小限制、安全標頭）、帳號、導航配置、災情回報、管理、分析 |
| `app/security.py` | 密碼雜湊（scrypt）、token 摘要、常數時間比較、安全標頭 |
| `app/routing.py` | 演練路網：硬性條件篩選 → Dijkstra → 容量限制群組分配與排序 |
| `app/simulation.py` | A／B／C 三策略分流實驗 |
| `app/public_map.py`、`app/osm_graph.py` | 官方名冊查詢與 OSM 步行路網路線參考（`/api/public`） |
| `app/public_data.py`、`app/accessibility.py` | 官方資料正規化、OSM 無障礙標記匹配 |
| `app/db.py`、`app/models.py`、`app/datasets.py` | SQLite 結構與交易、API 輸入模型、匯入資料驗證 |
| `app/cli.py` | 管理指令：金鑰、匯出／驗證／匯入資料、備份、保存期限清理 |
| `static/` | 原生 HTML/CSS/JS 前端（`vendor/` 為 Leaflet） |
| `scripts/` | 公開資料同步與快照建置、HTTP smoke 測試 |
| `data/public/` | 官方名冊與 OSM 路網快照；`data/demo.json` 為合成演練資料 |
| `tests/` | pytest 測試 |
| `deploy/`、`Dockerfile` | systemd、Nginx、容器部署範例 |

## 測試與驗證

```bash
./test.sh
# 或已安裝測試依賴時
.venv/bin/python -m pytest -q
# 執行中的實際 HTTP 服務
.venv/bin/python scripts/http_smoke.py --url http://127.0.0.1:8765
```

程式風格：Python 以 [ruff](https://docs.astral.sh/ruff/) 檢查與格式化（設定見 `pyproject.toml`），前端以 Prettier 格式化（`.prettierrc.json`）。

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
npx prettier@3.3.3 --check "static/*.{js,html,css}"
```

推送到 GitHub 後，`.github/workflows/ci.yml` 會在 Python 3.11／3.12 自動執行 lint 與全部測試。

測試使用暫存資料庫，不刪除目前展示資料。`/test` 與 HTTP smoke 會建立獨立訪客、臨時配置，再取消／登出清理；若現有容量已滿或路網全部封閉，會如實失敗。實測紀錄見 `TEST_REPORT.md`。

## Debian 部署

完整指令、systemd、Nginx、備份與驗證方式見 `docs/DEBIAN.md`。本次只在 macOS 實測，未聲稱已在 Debian 實機執行。

## 資料整備與匯入

`data/demo.json` 是可檢查的標準格式，`docs/DATA_DICTIONARY.md` 說明欄位。

```bash
.venv/bin/python -m app.cli validate-data data/demo.json
# 使用新的資料目錄，避免覆蓋已有導航與災情歷史
SHELTER_DATA_DIR=/path/to/new-store .venv/bin/python -m app.cli import-data data/demo.json
SHELTER_DATA_DIR=/path/to/new-store ./start.sh
.venv/bin/python -m app.cli backup /path/to/new-backup.sqlite3
.venv/bin/python -m app.cli prune
```

匯入會拒絕重複代碼、人口期別不同、孤立的外鍵、無效座標、錯誤容量關係與非 WGS84；不連通元件、未知容量與無障礙資料另列品質報告。匯入資料仍以演練模式運作。

公開名冊與 OSM 已有獨立整備流程（見 `docs/PUBLIC_DATA.md`），不會用合成資料覆蓋官方紀錄。下述標準匯入用於演練；里界與人口仍需另外取得、核對。人口總計／男女／年齡總計不可重複加總；以區域代碼串接並另保留未匹配項目；TWD97 先投影計算面積再轉 EPSG:4326；入口需查核。`source`、`version`、`stat_period` 和 `boundary_version` 不可用下載日期代替。

## 個資、保存與權限

使用 scrypt 加鹽雜湊密碼，資料庫只存 token 雜湊。API 不回傳密碼；驗證錯誤不回顯敏感原文。管理端無帳號、個人位置或需求列表，公開災情不顯示回報人與自由文字；照片不公開。管理者仍能讀取回報自由文字，請勿在描述輸入個資。

帳號資料保存至本人刪除。過去需求版本、照片、回報自由文字、已結束導航保存 30 天，**必須定期執行 `prune`**；範例 systemd timer 見部署說明。確認封閉的路段狀態不因清理解除。備份、瀏覽器預存與 SQLite 檔案級歷史副本需由操作者另外管理；此原型沒有宣稱法證等級安全抹除。

預設同源寫入、Host 白名單、CSP、禁止框架嵌入、API `no-store` 與請求大小限制。對外部署必須配置 HTTPS、自己的 Host 名單與定期備份。資料庫是單機 SQLite，不是跨節點或高可用正式營運服務。

## 明確未包含的部分

| 項目 | 本次狀態 |
|---|---|
| 官方強震即時速報、資格、再發布授權 | 未接入；只做演練事件 |
| 官方震後報告自動同步 | 未接入；事件模型與版本 API 已提供 |
| 全市官方場所與 OSM 步行路網 | 已整備官方419筆收容、13筆防災公園、22443筆防空；161478節點／221520路段；里界人口仍未接入 |
| 現場開設、入口查核、即時空位 | 未取得；公開頁標示未知／名冊暫停用，演練頁另有合成狀態 |
| Flutter／iOS App、實機定位、APNs／Critical Alerts | 不在此後端與測試網頁交付內，尚未實作 |
| 樓內垂直撤離、救援派遣、通知可靠度 | 未提供，不以導航代替救援 |
| PostgreSQL／PostGIS | 尚未整合；此版本用 SQLite 支援 macOS 與 Debian |
| 正式多人高可用、全市災時負載 | 未驗證；本次範圍為單機研究原型 |

## 參考

- [FastAPI 官方測試文件](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI 官方安全文件](https://fastapi.tiangolo.com/tutorial/security/)
- [Python SQLite 交易與備份](https://docs.python.org/3/library/sqlite3.html)
- [臺北市避難收容資料](https://data.taipei/dataset/detail?id=aaf97773-3631-40e2-b3cc-da87bf2ce1d5)
- [中央氣象署震後地震報告資料](https://opendata.cwa.gov.tw/dataset/earthquake/E-A0015-001)

官方名冊的來源、版本、SHA-256與授權見 `data/public/sources.json`。資料下載與完整性不等於現場查核或正式速報資格。
