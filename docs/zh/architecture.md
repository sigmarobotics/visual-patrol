# 系統架構

## 概述

Visual Patrol 是一套多機器人自主巡檢系統。透過網頁單頁應用程式 (SPA) 經由 nginx 反向代理連線至各機器人專屬的 Flask 後端實例，所有後端共用同一個 SQLite 資料庫。RTSP 中繼層 (mediamtx + ffmpeg) 提供持續的攝影機串流至 VILA JPS Alert API，實現即時監控。

```
                           瀏覽器 (SPA)
                               |
                       nginx (port 5000)
                      /        |        \
               robot-a     robot-b     robot-c
              Flask:5000  Flask:5000  Flask:5000
                 |            |           |
              Kachaka A    Kachaka B   Kachaka C
                 \            |          /
                  \           |         /
                   共用 SQLite DB (WAL)
                     data/report/report.db

              mediamtx (RTSP 中繼, port 8555)
              /{robot-id}/camera   <-- ffmpeg 轉碼 (gRPC JPEG)
              /{robot-id}/external <-- ffmpeg 轉碼 (RTSP 來源)
                        |
                   VILA JPS (h264parse + nvv4l2decoder)
                   串流 --> 警報規則 --> WebSocket 事件
```

## 元件說明

### 前端 (SPA)

- **位置**: `src/frontend/`
- **服務方式**: nginx (靜態檔案) 或 Flask (開發環境備援)
- **技術**: 原生 JavaScript ES modules，無框架
- **進入點**: `src/frontend/templates/index.html`
- **JS 進入點**: `src/frontend/static/js/app.js`

前端為單一 HTML 頁面，採用分頁式導覽。共有 6 個分頁：Patrol、Control (預設)、History、Reports、Tokens、Settings。所有視圖同時存在於 DOM 中，透過 `switchTab()` 切換顯示/隱藏。地圖 canvas 會在 Control 和 Patrol 分頁之間實體搬移，避免維護重複的 canvas 狀態。

### 後端 (Flask)

- **位置**: `src/backend/`
- **進入點**: `src/backend/app.py`
- **執行環境**: Python 3.10+, Flask 3.x

每台機器人執行各自的 Flask 程序。後端負責：
- 提供前端 REST API
- 透過 `kachaka-api` 以 gRPC 與 Kachaka 機器人通訊
- 透過 Google Gemini API 進行 AI 推論
- 巡檢任務調度 (移動、拍照、AI 分析)
- 透過 frame_hub 集中管理攝影機畫面
- 管理 RTSP 中繼 (ffmpeg 子程序推送至 mediamtx)
- 透過 VILA JPS API 進行即時監控 (串流註冊、警報規則、WebSocket)
- PDF 報告生成
- Telegram 通知 (巡檢報告 + 即時警報照片)
- 巡檢期間錄影

### Frame Hub (集中式畫面管理)

- **位置**: `src/backend/frame_hub.py` (280 行)
- **類別**: `FrameHub`
- **實例**: `frame_hub` (模組層級 singleton)

集中式攝影機畫面管理器，取代各模組獨立的 gRPC 呼叫：

1. **單一 gRPC 輪詢執行緒**：以 ~10fps 輪詢機器人前方攝影機，將最新畫面快取於記憶體
2. **畫面快取 API**：所有消費者 (MJPEG 串流、Gemini 巡檢、錄影、證據擷取) 透過 `get_latest_frame()` 讀取快取
3. **按需 RTSP 推送**：啟動 ffmpeg 將快取畫面以 2fps 直接推送至 Jetson mediamtx (`/{robot_id}/camera`)，供 VILA JPS 分析
4. **生命週期管理**：根據巡檢狀態和 `enable_idle_stream` 設定自動啟停輪詢

### RTSP Relay (mediamtx + ffmpeg)

- **mediamtx**: 輕量級 RTSP 伺服器 (`bluenviron/mediamtx` Docker 映像，Jetson 上 port 8555)
- **ffmpeg**: 由 `relay_service.py` 管理 (Jetson 端 relay 服務)

兩種串流路徑：
1. **機器人攝影機 (直推)**: frame_hub gRPC 輪詢 --> 畫面快取 --> ffmpeg 推送 (2fps) --> mediamtx `/{robot_id}/camera` --> VILA JPS（不經過 relay）
2. **外部 RTSP (經 relay 轉碼)**: 來源 RTSP --> relay 服務 ffmpeg 轉碼 (H264 Baseline) --> RTSP 推送至 mediamtx `/{robot_id}/external` --> VILA JPS

Relay 服務 (`relay_service.py`, port 5020) 是 Jetson 上的獨立 Flask 應用程式，僅用於外部 RTSP 轉碼。CI 建置映像至 `ghcr.io/sigmarobotics/visual-patrol-relay:latest`。

VILA JPS 從 mediamtx 拉取 RTSP 串流進行持續 VLM 分析。

### VILA JPS 整合

- **Stream API**: `POST /api/v1/live-stream` 向 VILA 註冊 RTSP 串流
- **Alert API**: `POST /api/v1/alerts` 為每個串流設定 yes/no 警報規則
- **WebSocket**: `ws://{host}:5016/api/v1/alerts/ws` 傳遞即時警報事件
- **取消註冊**: `DELETE /api/v1/live-stream/{id}` 在巡檢停止時清除
- **streaming.py 修補**: JPS 需要修補的 `streaming.py` (`deploy/vila-jps/streaming_patched.py`)，在 GStreamer 管線中加入 `h264parse` 以相容 NvMMLite 解碼器

**JPS 限制**：最多 1 個串流 -- 前端使用單選按鈕 (非核取方塊)。

當警報觸發時，後端透過 OpenCV RTSP 從 mediamtx 擷取證據畫面，儲存至磁碟與資料庫，並在設定 Telegram 時傳送通知。

### 反向代理 (nginx)

- **開發環境設定檔**: `nginx.conf` (根目錄)
- **正式環境設定檔**: `deploy/nginx.conf`

nginx 負責兩項功能：
1. 直接提供靜態前端資源 (比 Flask 更快)
2. 根據 URL 路徑將 API 請求路由至對應的後端

### 資料庫 (SQLite)

- **檔案**: `data/report/report.db`
- **模式**: WAL (Write-Ahead Logging)，支援多程序同時讀寫
- **忙碌逾時**: 5000ms

所有機器人後端共用同一個資料庫檔案。各資料表中的 `robot_id` 欄位用於區分不同機器人的資料。

## 請求流程

### 機器人專屬請求

```
瀏覽器:  GET /api/robot-a/state
    |
nginx:    正規匹配 ^/api/(robot-a)/(.*)$
          移除前綴，代理至 http://robot-a:5000/api/state
    |
Flask:    處理 /api/state，回傳機器人專屬資料
```

### 全域請求

```
瀏覽器:  GET /api/settings
    |
nginx:    落入 /api/ 的通用規則
          代理至 http://robot-a:5000/api/settings
    |
Flask:    從共用 SQLite DB 讀取，回傳設定
```

任何後端都能處理全域請求，因為它們共用同一個資料庫。

## 即時監控資料流

```
1. 巡檢啟動
   |-- 機器人攝影機: frame_hub.start_rtsp_push() 以 2fps 直推至 mediamtx /{robot_id}/camera
   |-- 外部 RTSP: relay_service_client 啟動 Jetson relay 轉碼至 /{robot_id}/external
   +-- 等待串流就緒 (frame_hub.wait_for_push_ready 或 relay wait_for_stream)

2. LiveMonitor.start()
   |-- POST /api/v1/live-stream --> 註冊每個串流 --> 取得 stream_id
   |-- POST /api/v1/alerts --> 為每個串流設定警報規則
   +-- 啟動 WebSocket 監聽執行緒

3. 巡檢期間
   |-- VILA JPS 拉取 RTSP 串流，持續評估規則
   |-- 警報觸發 --> WebSocket 事件傳至後端
   |   |-- 冷卻檢查 (每規則+串流 60 秒)
   |   |-- 從 mediamtx 擷取證據畫面 (cv2 RTSP)
   |   |-- 儲存 JPEG 至 data/{robot_id}/report/edge_ai_alerts/
   |   |-- INSERT INTO edge_ai_alerts (含 stream_source)
   |   +-- 若已設定 Telegram，傳送照片
   +-- 警報可透過 /api/{id}/patrol/edge_ai_alerts 在巡檢儀表板查看

4. 巡檢結束
   |-- LiveMonitor.stop()
   |   |-- 關閉 WebSocket
   |   +-- DELETE /api/v1/live-stream/{id} 取消註冊每個串流
   |-- relay_service_client.stop_all() --> 停止 Jetson relay ffmpeg
   |-- frame_hub.stop_rtsp_push()
   +-- 警報納入 AI 摘要報告
```

## 資料模型

### 每台機器人的資料 (檔案系統)

每台機器人儲存各自的設定與圖片：

```
data/
|-- report/
|   +-- report.db              # 共用資料庫
|-- robot-a/
|   |-- config/
|   |   |-- points.json        # 巡檢點位
|   |   +-- patrol_schedule.json
|   +-- report/
|       |-- images/            # 巡檢照片
|       |   +-- {run_id}_{timestamp}/
|       +-- edge_ai_alerts/    # 即時監控證據圖片
|-- robot-b/
|   +-- ...
```

### 共用資料 (資料庫)

詳見 [backend.md](backend.md) 的完整資料庫 schema。

## 執行緒模型

每個 Flask 後端執行數個背景執行緒：

| 執行緒 | 用途 | 間隔 |
|--------|------|------|
| `_poll_loop` (frame_hub) | gRPC 輪詢攝影機畫面，更新記憶體快取 | 100ms (~10fps) |
| `_feeder_loop` (frame_hub) | 將快取畫面寫入 ffmpeg stdin (RTSP 推送) | 500ms (2fps) |
| `_monitor_push` (frame_hub) | 監控 ffmpeg 健康狀態，自動重啟 | 5s |
| `_polling_loop` (robot_service) | 透過 gRPC 輪詢機器人位置、電量、地圖 | 100ms |
| `_heartbeat_loop` (app.py) | 更新資料庫中的機器人上線狀態 | 30s |
| `_schedule_checker` (patrol_service) | 檢查排程巡檢時間 | 30s |
| `_inspection_worker` (patrol_service) | 處理 AI 巡檢佇列 (turbo 模式) | 事件驅動 |
| `_record_loop` (video_recorder) | 巡檢期間擷取影片畫面 | 1/fps |
| `_ws_listener` (edge_ai_service) | 監聽 VILA JPS WebSocket 警報事件 | 持續 |

## 網路模式

### 開發環境 (WSL2 / Docker Desktop)

使用 Docker bridge 網路：

- nginx 綁定 `ports: 5000:5000`
- mediamtx 綁定 `ports: 8554:8554`
- 所有 Flask 後端在內部監聽 port 5000
- nginx 透過 Docker DNS (`resolver 127.0.0.11`) 解析後端主機名稱
- Docker 服務名稱必須與 `ROBOT_ID` 值一致 (例：服務 `robot-a` = `ROBOT_ID=robot-a`)
- `RELAY_SERVICE_URL` 指向 Jetson relay 服務 (例：`http://192.168.50.35:5020`)

### 正式環境 (Jetson / Linux 主機)

使用 host 網路 (`network_mode: host`)：

- 所有容器共用主機網路堆疊
- nginx 監聽 port 5000
- 每個 Flask 後端透過 `PORT` 環境變數使用不同的連接埠 (5001, 5002, ...)
- mediamtx RTSP port 可透過 `MTX_RTSPADDRESS` 設定 (預設 8554，若有衝突使用 8555)
- nginx 透過明確的代理規則將 robot ID 路由至 `127.0.0.1:PORT`
- `RELAY_SERVICE_URL=http://localhost:5020` (relay 服務在同一台 Jetson 上)

詳見 [deployment.md](deployment.md)。

## 安全性

- nginx 加入安全標頭：`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`
- 敏感設定 (API 金鑰、Telegram token) 在 GET 回應中遮罩 (`****` 前綴)
- Robot ID 路徑參數驗證格式 `^robot-[a-z0-9-]+$`
- 圖片服務在建構檔案路徑前驗證 robot ID 格式
- Docker 以非 root 使用者執行 Flask (`appuser`, UID 1000)
- `entrypoint.sh` 使用 `gosu` 在修正磁碟區權限後降低權限
