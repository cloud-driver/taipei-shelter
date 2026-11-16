# Debian 部署與驗證

適用 Debian 12／13 的單機 Python 後端；建議 Python 3.11 以上。本次沒有連線 Debian 主機，以下設定尚未在 Debian 實機執行。

## 安裝

將專案原始碼複製到 `/opt/taipei-shelter`，**不要複製 macOS 的 `.venv`、`.local`、`__pycache__` 或測試憑證**。用新資料目錄啟動。

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip nginx curl
sudo useradd --system --home /var/lib/taipei-shelter --shell /usr/sbin/nologin anxing
sudo install -d -o root -g root /opt/taipei-shelter
sudo install -d -o anxing -g anxing -m 700 /var/lib/taipei-shelter
# 將原始碼複製到 /opt/taipei-shelter 後：
cd /opt/taipei-shelter
sudo python3 -m venv .venv
sudo .venv/bin/python -m pip install -r requirements.txt
sudo cp deploy/taipei-shelter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now taipei-shelter
sudo systemctl status taipei-shelter --no-pager
curl --fail http://127.0.0.1:8765/api/health
```

**必須完整複製 `data/public/`**，內含官方名冊、無障礙標記與壓縮 OSM 路網；不需要在 Debian 首次啟動時下載圖磚或 Overpass。首次導航會延遲載入約161478節點／221520路段，請為 Python 程序預留至少1GB RAM，實際用量依執行環境而異。圖磚由瀏覽器以HTTPS向OSM載入。

驗證公開資料：

```bash
curl --fail http://127.0.0.1:8765/api/public/sources
curl --fail 'http://127.0.0.1:8765/api/public/catalog?kind=earthquake&district=%E5%A4%A7%E5%AE%89%E5%8D%80'
```

資料更新請在副本執行 `scripts/sync_public_data.py` 與建置、測試後再替換整個快照；替換後重新啟動服務，避免混用不同版本的名冊與路網。詳見 `PUBLIC_DATA.md`。

`StateDirectory` 與 `ReadWritePaths` 只允許服務寫入 `/var/lib/taipei-shelter`。服務以 `anxing` 執行，程式碼由 root 管理，金鑰自動產生於資料目錄，不需硬編碼。

管理金鑰：

```bash
sudo -u anxing env SHELTER_DATA_DIR=/var/lib/taipei-shelter \
  /opt/taipei-shelter/.venv/bin/python -m app.cli admin-token
```

## 先在本機做測試

在伺服器另建測試虛擬環境（測試不用寫正式資料目錄）：

```bash
cd /opt/taipei-shelter
python3 -m venv /tmp/anxing-test-venv
/tmp/anxing-test-venv/bin/python -m pip install -r requirements-dev.txt
/tmp/anxing-test-venv/bin/python -m pytest -q
/tmp/anxing-test-venv/bin/python scripts/http_smoke.py --url http://127.0.0.1:8765
```

不要以 `pytest` 通過取代實際 `curl`、瀏覽器與 systemd 的健康狀態驗證。

## 從 Mac 經 SSH 開啟 Debian 網頁

不必對外暴露 8765：

```bash
ssh -L 8766:127.0.0.1:8765 YOUR_DEBIAN_SSH_ALIAS
```

Mac Chrome 開 `http://127.0.0.1:8766/`。因為 Host 是 `127.0.0.1:8766`，預設 Host 名單允許；同源 API 與離線 Service Worker 可以運作。

## 有網域時的反向代理

修改 `deploy/nginx.conf.example` 的網域，並在 `/etc/taipei-shelter.env` 設定：

```ini
SHELTER_ALLOWED_HOSTS=localhost,127.0.0.1,drill.example.com
```

檔案權限設為 `root:root 600`。Nginx 範例只是 HTTP 轉發起點，對外使用請另配置可信 HTTPS 憑證；Service Worker、登入憑證與個人資料不應透過公開 HTTP 傳輸。啟用後執行 `sudo nginx -t`、重新載入 Nginx 並重啟服務。

僅一個可信反向代理直接連接本機 Uvicorn；若改變代理層數，需重新設定可信 forwarded IP 範圍。沒有啟用公開跨來源 CORS。不要把管理金鑰放在 query string。

## 備份與清理

```bash
sudo install -d -o anxing -g anxing -m 700 /var/backups/taipei-shelter
sudo -u anxing env SHELTER_DATA_DIR=/var/lib/taipei-shelter \
  /opt/taipei-shelter/.venv/bin/python -m app.cli backup \
  /var/backups/taipei-shelter/backup-2026-10-21.sqlite3
sudo -u anxing env SHELTER_DATA_DIR=/var/lib/taipei-shelter \
  /opt/taipei-shelter/.venv/bin/python -m app.cli prune
```

不要在服務執行時只複製 `.sqlite3`、漏掉 WAL；使用 `backup` 產生一致快照。還原時先停止服務，備份現有資料，替換資料庫並確保 `anxing` 擁有、權限600；舊的 WAL／SHM 必須與舊資料庫一起移出，不能與新備份混用。重新啟動後檢查健康、資料版本及登入。

可選擇安裝每日清理 timer（這些檔案僅隨附，未自動安裝）：

```bash
sudo cp deploy/taipei-shelter-prune.service deploy/taipei-shelter-prune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now taipei-shelter-prune.timer
```

## 故障檢查

```bash
sudo journalctl -u taipei-shelter -n 80 --no-pager
curl -i http://127.0.0.1:8765/api/health
sudo systemctl restart taipei-shelter
```

- 400 Host 錯誤：將實際網域加入 `SHELTER_ALLOWED_HOSTS` 後重啟。
- 403 管理錯誤：確認使用同一資料目錄產生的金鑰。
- 413：JSON 上限32KB、照片上限2MB；代理與後端都會限制。
- 409 無路線：核對開設、容量、保留無障礙名額、入口未知、必要需求與封路；不要直接放寬限制。
- 登出後仍有負載：已到達人數已轉為匿名背景統計，需管理端校正，不能當作空位。

## 選用容器

`Dockerfile` 使用 Debian bookworm 基底，資料掛載於 `/data`。此交付環境沒有 Docker，未進行容器建置驗證。

```bash
docker build -t anxing-drill .
docker volume create anxing-data
docker run --rm --name anxing -p 127.0.0.1:8765:8765 \
  -v anxing-data:/data anxing-drill
```
