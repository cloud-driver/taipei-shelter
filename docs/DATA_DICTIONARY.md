# 資料字典與演算法合約

`data/demo.json` 可直接驗證與作為正規化格式範本；以下欄位以 `app/datasets.py` 的 Pydantic 模型為準。內部 API 資料型態見 `/docs`、`/openapi.json`。

## 標準路網資料

| 群組 | 欄位 | 說明 |
|---|---|---|
| dataset | version / source / drill / crs | 來源版本、說明、必須 true 的演練標記、必須 EPSG:4326 |
| nodes | id / name / lat / lon | 唯一入口或路口代碼、名稱、WGS84；原型限制臺北附近座標 |
| edges | id / a / b / length | 唯一路段代碼、兩端節點、沿路網長度 m；目前為雙向可步行路段 |
| edges | stairs / slope / width / surface_ok / risk | 樓梯、坡度 %、有效寬度 m、鋪面確認、0–1情境風險 |
| shelters | id / name / node_id / disaster | 唯一場所、名稱、實際查核入口節點、earthquake／airraid／other |
| shelters | status | open / closed / unknown；不是名冊存在就視為開設 |
| shelters | capacity / accessible_capacity | 人數上限與已查核適用容量；未知用 null，不能自行估計輪椅名額 |
| shelters | entrance_step_free / space_accessible / accessible_toilet | true / false / null；需要無障礙時 null 不視為符合 |
| shelters | background / background_accessible | 已知情境非 App 背景總人數及其中需無障礙人數；不可與 App 到達重複相加 |
| shelters | source / version | 各場所的來源與查核版本 |
| areas | id / name / node_id | 人口區域代碼、名稱、演練需求起點；不可僅依可能重複的里名串接 |
| areas | population / elderly / area_km2 | 同期人口、65歲以上人口、以正確投影求得的面積 km² |
| areas | stat_period / boundary_version / bounds | YYYY-MM 或 synthetic、界線版本、WGS84 [西,南,東,北]；目前圖形是合成區塊示意 |

來源格式不一致時先正規化，保留未匹配紀錄與原始檔，不猜測欄位。已知完整里界多邊形與道路曲線的正式 GIS 匯入還需擴充；目前模型使用節點、雙向邊及示意區塊，不能稱為全市 PostGIS 完成品。

## 導航與容量

使用者年齡、需求、同意及版本在 `users`／`profile_history`；訪客沒有個人需求持久化。憑證雜湊與期限在 `sessions`。

`allocations` 保存 owner session、唯一 request_id、請求內容雜湊、目的地、整組人數、需無障礙人數、需求版本、資料版本、路徑與狀態。一般配置不能超過「總容量－保留無障礙容量」；需無障礙名額另外限制。全組路線都採最嚴格必要條件。

狀態：`proposed → accepted → arrived → left`；在途可 `cancelled`、`expired` 或 `invalidated`。已到達的人直到離開才停止計數。相同 request_id、相同內容回傳原紀錄；改內容回409。重新規劃在同一交易排除舊配置計算新負載，失敗會 rollback。

硬限制通過後，候選成本 `0.55 × (分鐘 / 30) + 0.20 × 風險 + 0.25 × 預估負載比`；最短路徑使用路網距離，並且限制相對可行最短時間的額外步行上限。權重版本 `group-greedy-v1`；這些是待校準設計參數，不是官方安全標準。此版本不是最小成本流／整數規劃的全域最適解。

## 回報與照片

`reports` 指向 edge 或 shelter、紀錄種類、發現時間、說明與狀態。高嚴重度先 `avoiding`；低嚴重度 `pending`；管理者以 `confirmed`／`resolved` 更新。審核歷史在 `report_audit`。多筆回報不自動當成真實；有任一仍避開／封閉的回報即排除對象。封閉不因逾時解除。

`photos` 存重新編碼 JPEG，不保留原始檔名與 EXIF。讀取需要本人 bearer 或管理金鑰。HTTP API 不提供匿名照片 URL。照片與自由文字保存30天需排程執行清理。

## 實驗結果

`experiments` 保存參數、種子、資料與演算法版本、每種子每策略結果及摘要。JSON 完整結果含障礙真值與觀測回報，CSV 輸出純量指標。

需求＝round(人口×日間情境倍率×避難比例)。隨機家庭1–4人，含無障礙成員者有1人使用無障礙適用容量，全組不拆分。App使用／接受率以乘積決定採用建議者；非使用者先依 A 分配，避免將同一人重複計入。

所有策略的平均／P95都有各自成功者資料；公平時間比較另取三策略皆成功的相同家庭，依人數加權。摘要的 min–max 只是多種子結果範圍，不是統計信賴區間。全體無人共同安置時時間指標為 null，不填0。

危險暴露計算路徑穿越模擬真實障礙的人數，每人最多一次；約束違反只計 C 實際管理並推薦的路線，不把非使用者自行走過阻斷路段算成推薦錯誤。無障礙成功率分母為含無障礙成員的整組人數，非僅輪椅成員數；欄位名稱明確保留 group。

## 保存期限

`prune` 清理30天前已結束的導航、舊需求版本、照片與回報自由文字；清理 rate limit。確認封閉狀態、審核理由與聚合實驗保留供查核；任何紀錄內都不應寫入與導航無關的病名、診斷、身分證或聯絡地址。已到達人數及其必要持有紀錄不會被任意清空。


## 全市公開資料與 OSM

此文件先前欄位用於合成演練。新增獨立資料：`data/public/catalog.json`、`sources.json`、`walking-graph.json.gz`。完整欄位、來源與導航條件見 `PUBLIC_DATA.md`；官方容量不寫入演練配置，也不當成即時剩餘空位。
