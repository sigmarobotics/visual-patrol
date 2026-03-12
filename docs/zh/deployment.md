# 部署指南

## 概述

Visual Patrol 支援兩種部署模式：

| 模式 | 網路 | 使用場景 | 設定檔 |
|------|------|----------|--------|
| **開發** | Docker bridge | WSL2、Docker Desktop、macOS | `docker-compose.yml`、`nginx.conf` |
| **正式** | Host 網路 | Jetson、裸機 Linux | `deploy/docker-compose.prod.yaml`、`deploy/nginx.conf` |

## 前置需求

- Docker Engine 24+ 及 Docker Compose v2
- 與 Kachaka 機器人的網路連線
- (正式環境) 對 `ghcr.io` 的網路存取以拉取映像
- (即時監控) Jetson 上需執行 VILA JPS 伺服器、mediamtx 及 relay service

## 開發環境設定

### 快速開始

```bash
git clone https://github.com/sigmarobotics/visual-patrol.git
cd visual-patrol

# 在 docker-compose.yml 中編輯機器人 IP
vim docker-compose.yml

docker compose up -d
```

開啟 [http://localhost:5000](http://localhost:5000)。

### 運作原理

- nginx 在主機上綁定 port `5000`
- 每個機器人服務在內部 port `5000` 執行 Flask (Docker bridge 網路隔離各服務)
- nginx 透過 Docker 內部 DNS (`resolver 127.0.0.11`) 解析服務名稱
- Docker 服務名稱**必須**與 `ROBOT_ID` 值一致 (例：服務 `robot-a` = 環境變數 `ROBOT_ID=robot-a`)
- 所有服務掛載 `./src` 以支援即時程式碼重載，以及 `./data` + `./logs` 以持久化儲存
- RTSP relay 在 Jetson 上執行；`RELAY_SERVICE_URL` 環境變數將機器人服務指向 relay service

### 新增機器人 (開發)

1. 在 `docker-compose.yml` 中新增服務區塊：

```yaml
  robot-d:
    container_name: visual_patrol_robot_d
    build: .
    volumes:
      - ./src:/app/src
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - DATA_DIR=/app/data
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - ROBOT_ID=robot-d
      - ROBOT_NAME=Robot D
      - ROBOT_IP=192.168.50.135:26400
      - RELAY_SERVICE_URL=http://192.168.50.35:5020
    restart: unless-stopped
```

2. 將 `robot-d` 加入 nginx 服務的 `depends_on` 清單。

3. 重啟：
```bash
docker compose up -d
```

無需修改 nginx 設定 -- 正規表示式 `^/api/(robot-[^/]+)/(.*)$` 會自動根據 Docker 服務名稱路由。

### 本機開發 (無 Docker)

```bash
# 安裝相依套件
uv pip install --system -r src/backend/requirements.txt

# 設定環境變數
export DATA_DIR=$(pwd)/data
export LOG_DIR=$(pwd)/logs
export ROBOT_ID=robot-a
export ROBOT_NAME="Robot A"
export ROBOT_IP=192.168.50.133:26400

# 執行
python src/backend/app.py
```

Flask 在 `http://localhost:5000` 同時提供 API 和前端。單機器人本機開發不需要 nginx。

若要在本機開發環境啟用 relay 功能，設定 `RELAY_SERVICE_URL` 指向 Jetson relay service：
```bash
export RELAY_SERVICE_URL=http://192.168.50.35:5020
```

未設定 `RELAY_SERVICE_URL` (空白) 時，relay 功能不可用，即時監控無法啟動。

## 正式環境設定 (Jetson / Linux)

### 全新安裝

無需 clone 儲存庫。只需要兩個設定檔：

```bash
mkdir -p ~/visual-patrol && cd ~/visual-patrol

# 下載設定檔
curl -LO https://raw.githubusercontent.com/sigmarobotics/visual-patrol/main/deploy/docker-compose.prod.yaml
curl -LO https://raw.githubusercontent.com/sigmarobotics/visual-patrol/main/deploy/nginx.conf

# 編輯機器人 IP 及其他設定
vim docker-compose.prod.yaml

# 拉取並啟動
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

`data/` 和 `logs/` 目錄在首次啟動時自動建立。

### 運作原理

- 所有容器使用 `network_mode: host` (Jetson 的 `iptables: false` 設定要求)
- nginx 在主機 port 5000 監聽
- 每個 Flask 後端透過 `PORT` 環境變數監聽不同連接埠 (5001, 5002, ...)
- nginx 透過比對 URL 中的 robot ID 路由至對應的連接埠
- 映像從 `ghcr.io/sigmarobotics/visual-patrol:latest` 拉取
- 所有服務使用 `RELAY_SERVICE_URL=http://localhost:5020` (relay service 在同一台主機)
- relay service (`rtsp-relay`) 已包含在 prod compose 檔中

### mediamtx (外部相依)

mediamtx 是即時監控管線使用的 RTSP relay 伺服器。它**未包含**在 visual-patrol 的 docker-compose 檔案中 -- 在 Jetson 上以獨立 compose 部署。

典型部署位置：`/home/nvidia/mediamtx/` (或 `/code/mediamtx/`)。

```bash
# 啟動 mediamtx
cd /home/nvidia/mediamtx && docker compose up -d

# 檢查狀態
docker compose ps
```

mediamtx 在 port `8555` 監聽 RTSP 連線。frame_hub 的 ffmpeg 推送和 relay service 的轉碼串流都推送至此。VILA JPS 從 mediamtx 拉取串流進行分析。

**連接埠衝突：** 若預設 RTSP 連接埠與其他服務衝突 (例：VILA JPS VST 使用 8554)，將 mediamtx 設定在 port `8555`，並確保 relay service 的 `MEDIAMTX_HOST` 相符 (例：`localhost:8555`)。`config.py` 中的 `JETSON_MEDIAMTX_PORT` 常數設為 `8555`。

### RTSP Relay Service (Jetson)

Relay service 是 Jetson 端的元件，處理所有 ffmpeg 影像轉碼。與 mediamtx 和 VILA JPS 一起在 Jetson 上執行。CI 自動建置多架構映像至 `ghcr.io/sigmarobotics/visual-patrol-relay:latest`。

**為什麼需要？** 在 Jetson 上執行 ffmpeg 轉碼，而非在 Flask 容器中：
- 所有串流轉碼為乾淨的 H264 Baseline profile (NvMMLite 硬體解碼器要求)
- 消除跨網路 RTSP 串流不穩定問題
- 機器人攝影機 (來自 frame_hub raw 推送) 和外部 RTSP (重新編碼) 走相同管線

**架構：**
```
VP Flask (開發/Jetson)             Jetson (host networking)
+----------------------+          +-----------------------------+
| frame_hub.py         |  RTSP    | mediamtx (:8555)            |
|  ffmpeg push (2fps)  |  push    |  /{robot-id}/camera         |
|  -> /{robot-id}/cam  | -------> |                             |
| relay_manager.py     |  HTTP    | relay_service.py (:5020)    |
|  (僅外部 RTSP)       | -------> |  ffmpeg 轉碼 (libx264)      |
+----------------------+          | VILA JPS (:5010/:5016)      |
                                  +-----------------------------+
```

機器人攝影機：frame_hub 以 2fps 將 JPEG-over-RTSP (H264 Baseline) 直接推送至 mediamtx 的 `/{robot_id}/camera`，不經過 relay。外部 RTSP 攝影機：relay service 讀取來源 URL 並轉碼至 `/{robot_id}/external`。

**設定 (透過 prod compose)：**

`rtsp-relay` 服務已包含在 `deploy/docker-compose.prod.yaml` 中：

```yaml
  rtsp-relay:
    container_name: visual_patrol_rtsp_relay
    image: ghcr.io/sigmarobotics/visual-patrol-relay:latest
    network_mode: host
    runtime: nvidia
    volumes:
      - ./logs:/app/logs
    environment:
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - RELAY_SERVICE_PORT=5020
      - MEDIAMTX_HOST=localhost:8555
      - USE_NVENC=false
      - RELAY_FPS=2
    restart: unless-stopped
```

**獨立設定 (手動)：**

```bash
# 拉取 CI 建置的映像
docker pull ghcr.io/sigmarobotics/visual-patrol-relay:latest

# 執行
docker rm -f visual_patrol_rtsp_relay 2>/dev/null
docker run -d --name visual_patrol_rtsp_relay \
  --network=host \
  -e TZ=Asia/Taipei \
  -e RELAY_SERVICE_PORT=5020 \
  -e MEDIAMTX_HOST=localhost:8555 \
  -e USE_NVENC=false \
  -e RELAY_FPS=2 \
  --restart=unless-stopped \
  ghcr.io/sigmarobotics/visual-patrol-relay:latest
```

**環境變數：**

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `RELAY_SERVICE_PORT` | `5020` | HTTP API 監聽連接埠 |
| `MEDIAMTX_HOST` | `localhost:8555` | mediamtx RTSP 推送目標 (`host:port`) |
| `USE_NVENC` | `false` | 使用 NVENC 硬體編碼器 (`h264_nvmpi`)。需 L4T 基礎映像及 `--runtime=nvidia`。 |
| `RELAY_FPS` | `2` | 轉碼輸出幀率 |
| `LOG_DIR` | `./logs` | 日誌檔目錄 |

**VP 連線設定：** 在每個機器人服務上設定 `RELAY_SERVICE_URL`：
- 正式環境 (Jetson, host networking)：`RELAY_SERVICE_URL=http://localhost:5020`
- 開發環境 (WSL2, bridge networking)：`RELAY_SERVICE_URL=http://192.168.50.35:5020` (Jetson IP)

未設定 `RELAY_SERVICE_URL` (空白) 時，`relay_service_client` 為 `None`，所有 relay 功能不可用。即時監控在沒有 relay service 的情況下無法啟動。

**驗證：**

```bash
# 健康檢查
curl http://localhost:5020/health

# 列出活躍的 relay
curl http://localhost:5020/relays

# 測試外部 RTSP relay
curl -X POST http://localhost:5020/relays \
  -H 'Content-Type: application/json' \
  -d '{"key":"test/external","source_url":"rtsp://admin:pass@192.168.50.45:554/live/profile.1"}'

# 檢查串流就緒狀態
curl "http://localhost:5020/relays/test%2Fexternal/ready?timeout=15"

# 停止所有 relay
curl -X POST http://localhost:5020/relays/stop_all
```

### JPS VLM streaming.py Patch

VILA JPS 內建的 `jetson_utils.videoSource` 建立的 GStreamer pipeline 缺少 `h264parse`，導致 `nvv4l2decoder` 在讀取 mediamtx relay 串流時失敗 (錯誤：`Stream format not found`)。

`deploy/vila-jps/streaming_patched.py` 以 GStreamer Python bindings 取代 `jetson_utils.videoSource`，建立自訂 pipeline：

```
rtspsrc (TCP) -> rtph264depay -> h264parse -> nvv4l2decoder -> nvvidconv -> appsink
```

**設定：**

```bash
# 複製 patch 至 JPS 目錄
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py

# 確認 JPS compose.yaml 有 volume mount：
# volumes:
#   - ./streaming_patched.py:/jetson-services/inference/vlm/src/mmj_utils/mmj_utils/streaming.py

# 重啟 JPS
cd /code/vila-jps && docker compose restart jps_vlm
```

### 新增機器人 (正式)

1. 在 `docker-compose.prod.yaml` 中新增服務，使用**唯一**的 `PORT`：

```yaml
  robot-b:
    container_name: visual_patrol_robot_b
    image: ghcr.io/sigmarobotics/visual-patrol:latest
    network_mode: host
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - DATA_DIR=/app/data
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - PORT=5002
      - ROBOT_ID=robot-b
      - ROBOT_NAME=Robot B
      - ROBOT_IP=192.168.50.134:26400
      - RELAY_SERVICE_URL=http://localhost:5020
    restart: unless-stopped
```

2. 在 `deploy/nginx.conf` 中新增路由。由於 host 網路無法使用 Docker DNS，需要明確的連接埠路由：

```nginx
location ~ ^/api/(robot-[^/]+)/(.*)$ {
    set $robot_id $1;
    set $api_path $2;

    # 根據 robot ID 路由至正確的後端連接埠
    set $backend "127.0.0.1:5001";
    if ($robot_id = "robot-b") {
        set $backend "127.0.0.1:5002";
    }

    proxy_pass http://$backend/api/$api_path$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

3. 重啟：
```bash
docker compose -f docker-compose.prod.yaml up -d
```

### 更新

```bash
cd ~/visual-patrol
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

### 常用指令

```bash
# 檢視日誌
docker compose -f docker-compose.prod.yaml logs -f

# 檢視特定服務日誌
docker compose -f docker-compose.prod.yaml logs -f robot-a

# 停止所有服務
docker compose -f docker-compose.prod.yaml down

# 重啟特定服務
docker compose -f docker-compose.prod.yaml restart robot-a

# 檢查服務狀態
docker compose -f docker-compose.prod.yaml ps

# 檢查 relay 狀態
curl http://localhost:5000/api/relay/status

# 檢查 VILA JPS 健康狀態
curl http://localhost:5000/api/edge_ai/health
```

## Docker 映像

### 建置

CI pipeline (`.github/workflows/docker-publish.yaml`) 在每次推送至 `main` 時自動建置多架構映像：

- **平台：** `linux/amd64`、`linux/arm64`
- **Registry：** `ghcr.io/sigmarobotics/visual-patrol`
- **標籤：** `latest` (main 分支)、`main`、`v1.0.0` (semver 標籤)
- **快取：** GitHub Actions cache (`type=gha`)

建置兩個映像：
- `ghcr.io/sigmarobotics/visual-patrol:latest` -- 主應用程式 (Flask + 前端)
- `ghcr.io/sigmarobotics/visual-patrol-relay:latest` -- Relay service (ffmpeg 轉碼)

### 手動建置

```bash
# 主應用程式
docker build -t visual-patrol .

# Relay service (從專案根目錄建置)
docker build -f deploy/relay-service/Dockerfile -t visual-patrol-relay .
```

### Dockerfile 細節

**主應用程式 (`Dockerfile`)：**

1. 基礎映像：`python:3.10-slim`
2. 系統相依：gcc、g++、cmake、ffmpeg、gosu、OpenCV 相依
3. Python 相依：透過 `uv pip` 安裝 (快速解析器)
4. 原始碼：複製 `src/` 目錄
5. 前端函式庫：建置時從 CDN 下載 Chart.js 和 marked.js
6. CJK 字型：下載 Noto Sans CJK TC 供 PDF 報告生成使用
7. 使用者：建立 `appuser` (UID 1000) 以非 root 身份執行
8. 進入點：`entrypoint.sh` 修正磁碟區權限後透過 `gosu` 切換至 `appuser`

**Relay service (`deploy/relay-service/Dockerfile`)：**

1. 基礎映像：`python:3.10-slim`
2. 系統相依：ffmpeg
3. Python 相依：Flask
4. 原始碼：複製 `src/backend/relay_service.py`
5. 預設環境：`RELAY_SERVICE_PORT=5020`、`MEDIAMTX_HOST=localhost:8555`、`USE_NVENC=false`、`RELAY_FPS=2`

## 目錄結構 (執行時)

```
~/visual-patrol/               # 或任何部署位置
├── docker-compose.prod.yaml   # 服務定義
├── nginx.conf                 # 反向代理設定
├── data/                      # 持久化資料 (自動建立)
│   ├── report/
│   │   └── report.db          # 共用 SQLite 資料庫
│   ├── robot-a/
│   │   ├── config/
│   │   │   ├── points.json
│   │   │   └── patrol_schedule.json
│   │   └── report/
│   │       ├── images/        # 巡檢照片
│   │       ├── edge_ai_alerts/   # 即時監控證據圖片
│   │       └── video/         # 巡檢影片 (若啟用)
│   └── robot-b/
│       └── ...
└── logs/                      # 應用程式日誌 (自動建立)
    ├── robot-a_app.log
    ├── robot-a_cloud_ai_service.log
    ├── robot-a_patrol_service.log
    ├── robot-a_video_recorder.log
    ├── robot-a_edge_ai_service.log
    ├── robot-a_frame_hub.log
    ├── robot-a_relay_manager.log
    └── relay_service.log       # 來自 relay service 容器
```

## 網路比較

| 面向 | 開發 (Bridge) | 正式 (Host) |
|------|--------------|-------------|
| `network_mode` | (預設 bridge) | `host` |
| nginx 連接埠 | `ports: 5000:5000` | 監聽 host:5000 |
| Flask 連接埠 | 全部內部 5000 | 每台機器人唯一 (5001, 5002...) |
| 服務發現 | Docker DNS | 明確 `127.0.0.1:PORT` |
| `RELAY_SERVICE_URL` | `http://192.168.50.35:5020` (Jetson IP) | `http://localhost:5020` |
| 新增機器人 | 只需新增服務 | 新增服務 + nginx `if` 區塊 |
| 前端服務 | nginx 提供 `/app/frontend` | Flask 提供 (透過 nginx 代理) |
| 原因 | Docker Desktop + WSL2 不支援 `network_mode: host` | Jetson `iptables: false` 不支援 bridge |

## 健康檢查

正式環境服務包含 Docker 健康檢查：

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/state')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

## 疑難排解

| 問題 | 解決方案 |
|------|----------|
| 機器人顯示「離線」 | 檢查 compose 檔中的 `ROBOT_IP`；確認機器人在網路上可連線 |
| 機器人下拉選單空白 | 確認後端正在執行：`docker compose ps` |
| AI 分析失敗 | 檢查設定中的 Gemini API 金鑰；查看 `logs/{robot-id}_cloud_ai_service.log` |
| PDF 生成失敗 | 檢查 `logs/{robot-id}_app.log` 的錯誤訊息 |
| 鏡頭串流無法載入 | 在設定中啟用「Continuous Camera Stream」；確認機器人連線 |
| 地圖無法載入 | 機器人可能還在連線中；檢查容器日誌的 gRPC 錯誤 |
| 連接埠衝突 (正式) | 確保每台機器人有唯一的 `PORT` 值 |
| mediamtx 連接埠衝突 | 修改 mediamtx 設定中的 `MTX_RTSPADDRESS`，並更新 relay service 的 `MEDIAMTX_HOST` 以匹配 |
| 即時監控無法啟動 | 檢查 `logs/{robot-id}_edge_ai_service.log`；確認 VILA JPS 執行中 (`/api/edge_ai/health`)；確認 mediamtx 和 relay service 都在執行 |
| Relay service 不可達 | 檢查 `RELAY_SERVICE_URL` 環境變數已設定；確認 relay service 執行中：`curl http://localhost:5020/health` |
| `RELAY_SERVICE_URL` 為空 | Relay 功能完全停用。設定 `RELAY_SERVICE_URL` 以啟用即時監控。 |
| ffmpeg relay 崩潰 | 檢查 Jetson 上的 `logs/relay_service.log`；確認 mediamtx 執行中並接受連線 |
| Relay 停滯偵測 | Relay service 在 30 秒無新畫面時自動重啟 ffmpeg；檢查來源 RTSP 是否可用 |
| NVENC 編碼器無法使用 | 檢查 `USE_NVENC` 環境變數；確認 `--runtime=nvidia`；查看 relay service 日誌；設 `USE_NVENC=false` 退回 libx264 |
| JPS 串流註冊失敗 | JPS 最多重試 5 次，間隔 10 秒；檢查 JPS 日誌，確認 RTSP 串流在 mediamtx 上可用 |
| WebSocket 最大重連次數 | Edge AI service 在 10 次重連嘗試後放棄；檢查 JPS WebSocket port 5016 是否可存取 |
| data/logs 權限被拒 | 進入點腳本會自動執行 `chown`；檢查 `gosu` 是否已安裝 |
| 資料庫中出現過期的機器人記錄 | 可能因 `ROBOT_ID` 環境變數缺失所致 (預設為 `"default"`) |
| frame_hub 推送未啟動 | 檢查 `logs/{robot-id}_frame_hub.log`；確認攝影機已連線且 `enable_idle_stream` 為 true |
