# 系統架構

## 概述

Visual Patrol 是一套多機器人自主巡檢系統。透過網頁單頁應用程式 (SPA) 經由 nginx 反向代理連線至各機器人專屬的 Flask 後端實例，所有後端共用同一個 SQLite 資料庫。

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
```

## 元件說明

### 前端 (SPA)

- **位置**: `src/frontend/`
- **服務方式**: nginx (靜態檔案) 或 Flask (開發環境備援)
- **技術**: 原生 JavaScript ES modules，無框架
- **進入點**: `src/frontend/templates/index.html`
- **JS 進入點**: `src/frontend/static/js/app.js`

前端為單一 HTML 頁面，採用分頁式導覽。所有視圖 (Control、Patrol、History、Stats、Settings) 同時存在於 DOM 中，透過 `switchTab()` 切換顯示/隱藏。地圖 canvas 會在 Control 和 Patrol 分頁之間實體搬移，避免維護重複的 canvas 狀態。

### 後端 (Flask)

- **位置**: `src/backend/`
- **進入點**: `src/backend/app.py`
- **執行環境**: Python 3.10+, Flask 3.x

每台機器人執行各自的 Flask 程序。後端負責：
- 提供前端 REST API
- 透過 `kachaka-api` 以 gRPC 與 Kachaka 機器人通訊
- 透過 Google Gemini API 進行 AI 推論
- 巡檢任務調度 (移動、拍照、AI 分析)
- PDF 報告生成
- Telegram 通知
- 巡檢錄影
- 巡檢期間透過 VILA Alert API 的即時鏡頭監控

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

## 資料模型

### 每台機器人的資料 (檔案系統)

每台機器人儲存各自的設定與圖片：

```
data/
├── report/
│   └── report.db              # 共用資料庫
├── robot-a/
│   ├── config/
│   │   ├── points.json        # 巡檢點位
│   │   └── patrol_schedule.json
│   └── report/
│       ├── images/            # 巡檢照片
│       │   └── {run_id}_{timestamp}/
│       └── edge_ai_alerts/       # 即時監控證據圖片
├── robot-b/
│   └── ...
```

### 共用資料 (資料庫)

詳見 [backend.md](backend.md) 的完整資料庫 schema。

## 執行緒模型

每個 Flask 後端執行數個背景執行緒：

| 執行緒 | 用途 | 間隔 |
|--------|------|------|
| `_polling_loop` (robot_service) | 透過 gRPC 輪詢機器人位置、電量、地圖 | 100ms |
| `_heartbeat_loop` (app.py) | 更新資料庫中的機器人上線狀態 | 30s |
| `_schedule_checker` (patrol_service) | 檢查排程巡檢時間 | 30s |
| `_inspection_worker` (patrol_service) | 處理 AI 巡檢佇列 | 事件驅動 |
| `_record_loop` (video_recorder) | 巡檢期間擷取影片畫面 | 1/fps |
| `_monitor_loop` (live_monitor) | 巡檢期間將畫面發送至 VILA Alert API | 可設定（預設 5s） |

## 網路模式

### 開發環境 (WSL2 / Docker Desktop)

使用 Docker bridge 網路：

- nginx 綁定 `ports: 5000:5000`
- 所有 Flask 後端在內部監聽 port 5000
- nginx 透過 Docker DNS (`resolver 127.0.0.11`) 解析後端主機名稱
- Docker 服務名稱必須與 `ROBOT_ID` 值一致 (例：服務 `robot-a` = `ROBOT_ID=robot-a`)

### 正式環境 (Jetson / Linux 主機)

使用 host 網路 (`network_mode: host`)：

- 所有容器共用主機網路堆疊
- nginx 監聽 port 5000
- 每個 Flask 後端透過 `PORT` 環境變數使用不同的連接埠 (5001, 5002, ...)
- nginx 透過明確的代理規則將 robot ID 路由至 `127.0.0.1:PORT`

詳見 [deployment.md](deployment.md)。

## 安全性

- nginx 加入安全標頭：`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`
- 敏感設定 (API 金鑰、Telegram token) 在 GET 回應中遮罩 (`****` 前綴)
- Robot ID 路徑參數驗證格式 `^robot-[a-z0-9-]+$`
- 圖片服務在建構檔案路徑前驗證 robot ID 格式
- Docker 以非 root 使用者執行 Flask (`appuser`, UID 1000)
- `entrypoint.sh` 使用 `gosu` 在修正磁碟區權限後降低權限
