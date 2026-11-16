# 官方名冊與 OpenStreetMap

本次2026-10-21下載的公開來源全部保留紀錄，不以下載日冒充逐筆觀測日期。來源連結、來源版本、原始檔SHA-256與筆數見 `data/public/sources.json`。

| 公開來源 | 筆數 | 用途 |
|---|---:|---|
| [臺北市教育局可供避難收容處所](https://data.taipei/dataset/detail?id=aaf97773-3631-40e2-b3cc-da87bf2ce1d5) | 419 | 全市12區主名冊；震災是62、備用246、否111 |
| [臺北市防災公園](https://data.taipei/dataset/detail?id=5800ed81-4aa8-48c9-ae6b-e1e23a91a695) | 13 | 官方位置與規劃容量 |
| [警察局防空疏散避難設施](https://data.taipei/dataset/detail?id=70a6216e-3730-4d1d-b334-62fca2dd71cd) | 22443 | 獨立圖層，不列為震災目的地 |
| [消防署避難收容點位](https://data.gov.tw/dataset/73242) | 272筆臺北資料 | 輔助核對收容名冊位置，不另加總 |

三類共22875筆來源紀錄，可能同一公園跨來源重複，不能解讀成22875個獨立場地。這是上述官方資料集的完整收錄，不保證涵蓋所有臨時開設或未公開場地。

震災適用／備用及公園合計321筆，290筆有可核對座標、31筆待核對。所有來源合計81筆尚無可靠座標，仍保留於清單與 `unlocated.json`，不放置虛構地圖點或導航。名稱／備考有「暫停用」者不推薦。官方欄位容量未知即保留null；未取得即時開設狀態，不建立容量配置。

## 座標品質

優先同區同門牌匹配消防署點位，之後同名；再核對警察局相同門牌建物、公園官方位置及同名OSM設施。只接受臺北及鄰接緩衝範圍內座標；例如來源中錯落南部的學校座標已拒絕。相同地址位置分歧超過約100m時不直接取平均作為確定位置。OSM同名只接受集中在約300m內的點，仍標示位置匹配，非已查核入口。未使用付費地理編碼，沒有把模糊名稱猜成確定座標。

## OSM路網

OSM資料經一次有限範圍的Overpass查詢取得，範圍24.94–25.23N、121.44–121.69E，包含鄰接道路以維持連通。實際回傳資料時間 **2026-07-15T15:22:01Z**，不是2026-10-21即時道路。原始 `osm-overpass.json.gz` 與衍生 `walking-graph.json.gz` 隨程式提供；© OpenStreetMap contributors，依[ODbL 1.0](https://www.openstreetmap.org/copyright)授權。政府資料依各來源標示的政府資料開放授權條款第1版。

路網161478節點、221520路段。壓縮一般中繼節點但保留轉折幾何；排除禁止步行、私人、施工及一般禁止步行的高速道路等不可通行路段，保留步行單行限制與節點障礙；嚴格無障礙條件排除未知路段。使用Dijkstra，以路網距離選最近符合條件場所。

起點150m內、目的地300m內需有公共步行路網節點；接駁段未查核且不畫成已知可走道路，距離與時間只計OSM路網，畫面另列兩端偏移距離。離散元件或找不到路時拒絕，不以直線代替。這是步行路線與逐段道路提示，尚無方向感測、語音或行走中自動重新規劃。

OSM圖磚使用[官方政策](https://operations.osmfoundation.org/policies/tiles/)允許的正常互動檢視，保留歸屬、Referer與瀏覽器快取；不批次下載、不離線預存公共圖磚。規劃在本機後端完成，不將起點送至第三方路由服務；瀏覽器載入圖磚會向OSM傳送顯示區域的圖磚請求。

官方無障礙設施欄位不足以確認入口、廁所與全程通行；目前嚴格輪椅／無階梯需求會拒絕推薦，不放寬未知條件。公開頁的路線未接入官方即時封路與人工實勘；「容量演練」回報與管理仍操作合成資料。

## Wheelmap 與輪椅資訊

依 [Wheelmap官方FAQ](https://news.wheelmap.org/en/FAQ/)，其主要資料來自OSM，另有不同授權的合作資料；[accessibility.cloud官方API](https://github.com/sozialhelden/accessibility-cloud/blob/main/app/docs/json-api.md)需要應用token並依回應逐項處理授權。本版接入OSM的 `wheelchair=yes/limited/no`、`toilets:wheelchair` 與說明，使用Wheelmap同一套標記；沒有擷取Wheelmap專屬或合作夥伴資料，也不聲稱與完整Wheelmap畫面一致。

完整無障礙專題Overpass請求曾遇502／逾時，因此交付使用已取得的路網／學校快照中73筆具名OSM標記（53可使用、5部分、15不可使用），範圍含鄰近緩衝區，並非全市完整無障礙清單。13筆與避難名冊同名且距離400m內匹配，10筆標記可使用、2筆部分、1筆不可使用。其他保持未知。圖層包含一般設施，不能直接當作避難目的地。

評級、廁所評級、OSM原始連結、快照時間、查核日期標籤分別呈現；沒有查核日期就顯示未提供。來源衝突列未知，同名但距離太遠不匹配。綠色設施不會自動設定 `entrance_verified` 或 `route_verified`，不繞過輪椅路網的坡度、寬度、路面及銜接段檢查。選輪椅模式會自動顯示標記圖層，也可篩選評級與對應的官方避難名冊。

`data/public/accessibility.json` 提供精簡觀測資料；catalog的 `wheelmap_osm` 保存匹配證據。`GET /api/public/accessibility?rating=yes&q=...` 查圖層，catalog與points可用 `wheelchair=yes|limited|no|unknown|all` 篩選。公開圖層正常顯示不需帳號或token。

需要擴充專題快照時，可手動執行 `scripts/sync_public_data.py --accessibility`，完整驗證回應後再 `scripts/build_public_snapshot.py --catalog-only`。公共端點不可用時腳本會失敗並保留既有無障礙檔；不自動密集重試。更完整的Wheelmap合作資料仍需自己的accessibility.cloud應用token與相應來源授權。

## 重新下載與建置

交付快照可直接啟動，不必下載。若要更新，請在專案副本執行，檢查官方網址與版本是否變更：

```bash
.venv/bin/python scripts/sync_public_data.py
.venv/bin/python scripts/build_public_snapshot.py --catalog-only
# 額外重新取得大型OSM快照（一次查詢，勿反覆呼叫公共端點）：
.venv/bin/python scripts/sync_public_data.py --osm
.venv/bin/python scripts/build_public_snapshot.py
.venv/bin/python -m pytest -q
```

原始ODS／CSV快取在 `.local/public-raw`，不納入原始碼交付，以免帶入未用到的公開聯絡人欄位。正規化輸出不含聯絡電話／聯絡人。下載腳本先驗證完整內容再替換單檔；公共服務無回應時會失敗，不以不完整回應重建圖。下載、建置全流程完成且檢查數量、未定位、來源版本、授權及路線後，才替換部署快照並重新啟動服務。HTTP路由程序不自動輪詢政府或Overpass。

## API與檔案

- `GET /api/public/sources`：版本、授權、筆數、品質與OSM時間。
- `GET /api/public/catalog`：kind、district、q、offset、limit篩選分頁，包含未定位紀錄。
- `GET /api/public/points`：相同篩選的精簡地圖點位，未定位數量另列。
- `GET /api/public/catalog/{id}`：位置來源、適災類型、未知開設與名冊容量。
- `GET /api/public/download.csv`：全部22875筆紀錄。
- `POST /api/public/route`：人工確認後的OSM路線參考；每IP每分鐘30次。

`id`保留官方識別（防空加air-前綴），公園使用來源列號park-；`earthquake_role`為primary/backup/not_applicable/disaster_park，`status`為unknown或closed，`coordinate_source`與`coordinate_method`分開記錄位置證據。`entrance_verified`目前全為false。`lat/lon`為WGS84，`capacity`為規劃名冊容量，不是剩餘空位。`allocation_created`在公開路線永遠false。

離線演練仍可按原功能預存合成示意圖；公開名冊查詢與道路路由需要本機後端運作，線上OSM底圖需要網路。並未實作可斷開後端的全市離線路由。
