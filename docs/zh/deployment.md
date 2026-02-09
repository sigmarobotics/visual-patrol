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

## 開發環境設定

### 快速開始

```bash
git clone https://github.com/sigma-snaken/visual-patrol.git
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

## 正式環境設定 (Jetson / Linux)

### 全新安裝

無需 clone 儲存庫。只需要兩個設定檔：

```bash
mkdir -p ~/visual-patrol && cd ~/visual-patrol

# 下載設定檔
curl -LO https://raw.githubusercontent.com/sigma-snaken/visual-patrol/main/deploy/docker-compose.prod.yaml
curl -LO https://raw.githubusercontent.com/sigma-snaken/visual-patrol/main/deploy/nginx.conf

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
- 映像從 `ghcr.io/sigma-snaken/visual-patrol:latest` 拉取

### 新增機器人 (正式)

1. 在 `docker-compose.prod.yaml` 中新增服務，使用**唯一**的 `PORT`：

```yaml
  robot-b:
    container_name: visual_patrol_robot_b
    image: ghcr.io/sigma-snaken/visual-patrol:latest
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
```

### RTSP Relay Service (Jetson)

Relay Service 是 Jetson 端元件，處理所有 ffmpeg 影像轉碼。與 mediamtx、VILA JPS 一起在 Jetson 上執行。CI 自動建置多架構映像至 `ghcr.io/sigma-snaken/visual-patrol-relay:latest`。

**為什麼需要？** 在 Jetson 上執行 ffmpeg 轉碼，而非在 Flask 容器中：
- 所有串流轉碼為乾淨的 H264 Baseline profile（NvMMLite 硬體解碼器要求）
- 消除跨網路 RTSP 串流不穩定問題
- 機器人相機 (JPEG→H264) 與外部相機 (重新編碼) 走相同 pipeline

**架構：**
```
VP Flask (開發/Jetson)             Jetson (host networking)
┌──────────────────────┐          ┌─────────────────────────────┐
│ relay_manager.py     │  HTTP    │ relay_service.py (:5020)    │
│  RelayServiceClient  │ ──────> │  ffmpeg 轉碼 (libx264)      │
│  FrameFeederThread   │         │  → mediamtx (:8555)         │
│                      │         │  → VILA JPS (:5010/:5016)   │
└──────────────────────┘         └─────────────────────────────┘
```

VP 透過 gRPC 擷取機器人相機畫面，以 HTTP POST 送至 Relay Service。外部相機則由 Relay Service 直接拉取 RTSP 來源。兩種類型都經過轉碼後推送至 mediamtx。

**設定 (Jetson)：**

```bash
# 拉取 CI 建置的映像
docker pull ghcr.io/sigma-snaken/visual-patrol-relay:latest

# 執行
docker rm -f visual_patrol_rtsp_relay 2>/dev/null
docker run -d --name visual_patrol_rtsp_relay \
  --network=host \
  -e TZ=Asia/Taipei \
  -e RELAY_SERVICE_PORT=5020 \
  -e MEDIAMTX_HOST=localhost:8555 \
  -e USE_NVENC=false \
  --restart=unless-stopped \
  ghcr.io/sigma-snaken/visual-patrol-relay:latest
```

或使用 prod compose 檔，其中已包含 `rtsp-relay` 服務。

**環境變數：**

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `RELAY_SERVICE_PORT` | `5020` | HTTP API 監聽埠 |
| `MEDIAMTX_HOST` | `localhost:8555` | mediamtx RTSP 推送目標 |
| `USE_NVENC` | `false` | 使用 NVENC 硬體編碼 (`h264_nvmpi`)。需 L4T 基礎映像。 |
| `LOG_DIR` | `./logs` | 日誌檔目錄 |

**VP 連線設定：** 在每個 robot 服務上設定 `RELAY_SERVICE_URL`：
- 正式環境 (Jetson, host networking)：`RELAY_SERVICE_URL=http://localhost:5020`
- 開發環境 (WSL2, bridge networking)：`RELAY_SERVICE_URL=http://192.168.50.35:5020` (Jetson IP)

未設定 `RELAY_SERVICE_URL` 或服務不可達時，VP 自動退回本地 ffmpeg (Flask 容器內軟體編碼)。

**驗證：**

```bash
# 健康檢查
curl http://localhost:5020/health

# 列出活躍的 relay
curl http://localhost:5020/relays

# 停止所有 relay
curl -X POST http://localhost:5020/relays/stop_all
```

### JPS VLM streaming.py Patch

VILA JPS 內建的 `jetson_utils.videoSource` 建立的 GStreamer pipeline 缺少 `h264parse`，
導致 `nvv4l2decoder` 無法解析從 mediamtx relay 過來的 H264 串流（錯誤：`Stream format not found`）。

`deploy/vila-jps/streaming_patched.py` 以 GStreamer Python bindings 取代 `jetson_utils.videoSource`，
建立含 `h264parse` 的自訂 pipeline：

```
rtspsrc (TCP) → rtph264depay → h264parse → nvv4l2decoder → nvvidconv → appsink
```

**設定：**

```bash
# 複製 patch 檔案至 JPS 目錄
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py

# 確認 JPS compose.yaml 有 volume mount：
# volumes:
#   - ./streaming_patched.py:/jetson-services/inference/vlm/src/mmj_utils/mmj_utils/streaming.py

# 重啟 JPS
cd /code/vila-jps && docker compose restart jps_vlm
```

詳見 [Relay Service 部署說明](../deploy/relay-service/JETSON_SETUP.md)。

## Docker 映像

### 建置

CI pipeline (`.github/workflows/docker-publish.yaml`) 在每次推送至 `main` 時自動建置多架構映像：

- **平台：** `linux/amd64`、`linux/arm64`
- **Registry：** `ghcr.io/sigma-snaken/visual-patrol`
- **標籤：** `latest` (main 分支)、`main`、`v1.0.0` (semver 標籤)
- **快取：** GitHub Actions cache (`type=gha`)

### 手動建置

```bash
docker build -t visual-patrol .
```

### Dockerfile 細節

1. 基礎映像：`python:3.10-slim`
2. 系統相依：gcc、g++、cmake、ffmpeg、gosu、OpenCV 相依
3. Python 相依：透過 `uv pip` 安裝 (快速解析器)
4. 原始碼：複製 `src/` 目錄
5. 前端函式庫：建置時從 CDN 下載 Chart.js 和 marked.js
6. 使用者：建立 `appuser` (UID 1000) 以非 root 身份執行
7. 進入點：`entrypoint.sh` 修正磁碟區權限後透過 `gosu` 切換至 `appuser`

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
│   │       └── video/         # 巡檢影片 (若啟用)
│   └── robot-b/
│       └── ...
└── logs/                      # 應用程式日誌 (自動建立)
    ├── robot-a_app.log
    ├── robot-a_cloud_ai_service.log
    ├── robot-a_patrol_service.log
    └── robot-a_video_recorder.log
```

## 網路比較

| 面向 | 開發 (Bridge) | 正式 (Host) |
|------|--------------|-------------|
| `network_mode` | (預設 bridge) | `host` |
| nginx 連接埠 | `ports: 5000:5000` | 監聽 host:5000 |
| Flask 連接埠 | 全部內部 5000 | 每台機器人唯一 (5001, 5002...) |
| 服務發現 | Docker DNS | 明確 `127.0.0.1:PORT` |
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
| data/logs 權限被拒 | 進入點腳本會自動執行 `chown`；檢查 `gosu` 是否已安裝 |
| 資料庫中出現過期的機器人記錄 | 可能因 `ROBOT_ID` 環境變數缺失所致 (預設為 `"default"`) |
| Relay Service 不可達 | 檢查 `RELAY_SERVICE_URL` 環境變數；確認 relay service 執行中：`curl http://localhost:5020/health`；VP 會自動退回本地 ffmpeg |
| NVENC 編碼器無法使用 | 檢查 `USE_NVENC` 環境變數；確認 `--runtime=nvidia`；查看 relay service 日誌；設 `USE_NVENC=false` 退回 libx264 |
