# 設定文件

## 概述

Visual Patrol 有兩層設定：

1. **環境變數** -- 每台機器人的身份識別、路徑及基礎設施位址，在 `docker-compose.yml` 中設定
2. **全域設定** -- 所有機器人共用，儲存在 SQLite 資料庫中，透過 Web UI 設定頁面管理

## 環境變數

在 `docker-compose.yml` (開發) 或 `deploy/docker-compose.prod.yaml` (正式) 中按服務設定。

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ROBOT_ID` | `"default"` | 機器人唯一識別碼 (開發環境中須與 Docker 服務名稱一致) |
| `ROBOT_NAME` | `"Robot"` | Web UI 中顯示的名稱 |
| `ROBOT_IP` | `"192.168.50.133:26400"` | Kachaka 機器人 gRPC 位址 (`ip:port`) |
| `DATA_DIR` | `{project}/data` | 共用資料與機器人專屬資料的基礎目錄 |
| `LOG_DIR` | `{project}/logs` | 日誌檔案基礎目錄 |
| `PORT` | `5000` | Flask HTTP 監聽連接埠 (正式環境中每台機器人須唯一) |
| `TZ` | (系統預設) | Docker 容器的系統時區 |
| `RELAY_SERVICE_URL` | `""` | Jetson relay 服務 URL (空白 = relay 不可用，即時監控無法啟動) |

**重要：** `ROBOT_ID` 須遵循 `robot-{name}` 格式 (例：`robot-a`、`robot-b`)。開發模式中 Docker 服務名稱必須與 `ROBOT_ID` 一致，因為 nginx 透過服務名稱解析後端。

### 範例 (開發)

```yaml
environment:
  - DATA_DIR=/app/data
  - LOG_DIR=/app/logs
  - TZ=Asia/Taipei
  - ROBOT_ID=robot-a
  - ROBOT_NAME=Robot A
  - ROBOT_IP=192.168.50.133:26400
  - RELAY_SERVICE_URL=http://192.168.50.35:5020
```

### 範例 (正式)

```yaml
environment:
  - DATA_DIR=/app/data
  - LOG_DIR=/app/logs
  - TZ=Asia/Taipei
  - PORT=5001
  - ROBOT_ID=robot-a
  - ROBOT_NAME=Robot A
  - ROBOT_IP=192.168.50.133:26400
  - RELAY_SERVICE_URL=http://localhost:5020
```

## 全域設定 (Web UI)

全域設定儲存在 `global_settings` SQLite 資料表中，以鍵值對形式保存。由於所有後端存取同一個資料庫，設定在所有機器人後端間共享。

透過 Web UI 的 **Settings** 分頁管理設定，或透過 API：
- `GET /api/settings` -- 讀取所有設定 (敏感欄位遮罩)
- `POST /api/settings` -- 儲存設定

Settings 分頁包含 3 個子分頁：**General**、**Gemini AI**、**VILA/Edge AI**。

### AI 設定

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `gemini_api_key` | `""` | Google Gemini API 金鑰 (敏感，GET 時遮罩) |
| `gemini_model` | `"gemini-3-flash-preview"` | Gemini 模型識別碼 |
| `system_prompt` | `"你是一個巡檢機器人..."` | AI 巡檢的系統角色提示詞 |

### 巡檢設定

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `turbo_mode` | `false` | 非同步 AI 分析 -- 機器人在前一張影像分析時移往下一個點位 |
| `enable_video_recording` | `false` | 巡檢期間錄影 (使用 OpenCV) |
| `video_prompt` | `"你是一個巡檢機器人..."` | 巡檢後 AI 影片分析的提示詞 |
| `enable_idle_stream` | `true` | 未巡檢時顯示鏡頭串流 |

### 即時監控 (VILA JPS)

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `enable_edge_ai` | `false` | 巡檢期間啟用即時監控 |
| `jetson_host` | `""` | Jetson 裝置 IP 位址 (自動衍生 JPS、mediamtx、relay、WS URL) |
| `enable_robot_camera_relay` | `false` | 將機器人攝影機 (gRPC) 中繼至 mediamtx (透過 ffmpeg) |
| `enable_external_rtsp` | `false` | 將外部 RTSP 攝影機中繼至 mediamtx |
| `external_rtsp_url` | `""` | 外部 RTSP 來源 URL (例：`rtsp://admin:pass@192.168.50.45:554/live`) |
| `edge_ai_rules` | `[]` | yes/no 警報規則字串列表，最多 10 條 (例：`["Is there a person?", "Is there fire?"]`) |

**`jetson_host` 自動衍生的 URL：**

| 服務 | 衍生 URL | 說明 |
|------|----------|------|
| VILA JPS API | `http://{jetson_host}:5010` | JPS 串流/警報 REST API |
| mediamtx (內部) | `{jetson_host}:8555` | ffmpeg 推送目標 (RTSP) |
| mediamtx (外部) | `{jetson_host}:8555` | VILA JPS 拉取來源 (RTSP) |
| Relay Service | `http://{jetson_host}:5020` | ffmpeg 轉碼服務 (由 `RELAY_SERVICE_URL` 環境變數設定) |
| WebSocket | `ws://{jetson_host}:5016` | JPS 警報事件 |

**JPS 限制：** 最多 1 個串流 -- 前端使用單選按鈕 (非核取方塊) 選擇串流來源。

即時監控需要至少一個串流來源 (`enable_robot_camera_relay` 或 `enable_external_rtsp`) 及已設定的 `jetson_host`。啟用後，系統：

1. 啟動 ffmpeg relay 程序將攝影機串流推送至 mediamtx
2. 向 VILA JPS 註冊串流
3. 為每個串流設定警報規則
4. 監聽 WebSocket 警報事件
5. 在觸發警報時擷取證據畫面並儲存至 DB + 磁碟
6. 若已設定 Telegram，傳送警報照片

每條規則有 60 秒冷卻時間以防止同一狀況重複觸發警報。

**無本地退回：** 當 `RELAY_SERVICE_URL` 為空時，relay 功能不可用，即時監控無法啟動。

### 報告提示詞

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `report_prompt` | (中文巡檢表範本) | 單次巡檢報告生成提示詞 |
| `multiday_report_prompt` | (中文多日巡檢表範本) | 多日彙總報告提示詞 |

預設的 `report_prompt` 為中文範本，生成涵蓋電氣安全、室內環境、消防安全等類別的結構化巡檢清單表格。

### 時區

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `timezone` | `"Asia/Taipei"` | 時間戳記與排程使用的時區 |

Web UI 中可選：UTC、Asia/Taipei、Asia/Tokyo、America/New_York、America/Los_Angeles、Europe/London。後端使用 Python 的 `zoneinfo` 模組，因此任何有效的 IANA 時區名稱都可透過 API 設定。

此設定影響：
- 資料庫中的所有時間戳記 (`get_current_time_str()`)
- 日誌檔時間戳記
- 排程檢查器 (決定觸發的「目前時間」)
- Web UI 標頭時鐘顯示

### Telegram 通知

| 設定 | 預設值 | 說明 |
|------|--------|------|
| `enable_telegram` | `false` | 巡檢完成後啟用 Telegram 通知 |
| `telegram_bot_token` | `""` | Telegram Bot API token (敏感，GET 時遮罩) |
| `telegram_user_id` | `""` | 接收通知的 Telegram chat/user ID |
| `telegram_message_prompt` | `"Based on the patrol inspection results below..."` | 用於生成 AI 摘要 Telegram 通知訊息的提示詞 |

啟用後，系統在兩種情境傳送通知：
1. **巡檢完成**：AI 生成的摘要文字 + PDF 報告文件
2. **即時監控警報**：照片 + 標題 (規則、來源、機器人、時間戳記) -- 警報觸發時立即傳送

### 敏感欄位

以下欄位在 GET 回應中遮罩，避免意外曝露：
- `gemini_api_key`
- `telegram_bot_token`
- `telegram_user_id`

遮罩格式：`****{後 4 碼}` (例：`****abcd`)。

透過 POST 儲存設定時，以 `****` 開頭的值會被忽略，確保不會覆寫實際儲存的值。

## 預設設定

定義在 `src/backend/config.py` 的 `DEFAULT_SETTINGS`：

```python
DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "gemini_model": "gemini-3-flash-preview",
    "system_prompt": "你是一個巡檢機器人...",
    "timezone": "Asia/Taipei",
    "enable_video_recording": False,
    "video_prompt": "你是一個巡檢機器人...",
    "enable_idle_stream": True,
    "report_prompt": "...",  # 中文巡檢表範本
    "multiday_report_prompt": "...",  # 中文多日巡檢表範本
    "telegram_message_prompt": "Based on the patrol inspection results below...",
    "enable_edge_ai": False,
    "edge_ai_rules": [],
    "jetson_host": "",
    "enable_robot_camera_relay": False,
    "enable_external_rtsp": False,
    "external_rtsp_url": "",
}
```

呼叫 `settings_service.get_all()` 時，已儲存的設定會覆蓋在這些預設值之上。缺失的鍵會回退至預設值。

## 舊版遷移

### 設定遷移

在資料庫支援的設定之前，設定儲存在 `data/config/settings.json`。首次啟動時，`settings_service.migrate_from_json()` 會在尚未儲存任何自訂設定的情況下，自動將此檔案匯入 `global_settings` 資料表。

### 機器人檔案遷移

舊版的機器人檔案儲存在共用的 `data/config/` 目錄。首次啟動時，`config.migrate_legacy_files()` 會將其複製至機器人專屬目錄：

- `data/config/points.json` -> `data/{robot_id}/config/points.json`
- `data/config/patrol_schedule.json` -> `data/{robot_id}/config/patrol_schedule.json`

### 資料遷移

`database.backfill_robot_id()` 會對既有資料中 `robot_id` 為 NULL 的列設定值，確保多機器人功能之前的資料歸屬至目前的機器人。

## 機器人設定檔

每台機器人在 `data/{robot_id}/config/` 儲存各自的設定：

### `points.json` -- 巡檢點位

```json
[
  {
    "id": "1706000000000",
    "name": "Lobby Entrance",
    "x": 1.5,
    "y": 2.0,
    "theta": 0.0,
    "prompt": "Check for obstacles in the hallway",
    "enabled": true,
    "source": "robot"
  }
]
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | string | 唯一 ID (基於時間戳記) |
| `name` | string | 顯示名稱 |
| `x`, `y` | float | 世界座標 (公尺) |
| `theta` | float | 方向角 (弧度) |
| `prompt` | string | 此點位的 AI 巡檢提示詞 |
| `enabled` | boolean | 是否納入巡檢 |
| `source` | string | 選填。從 Kachaka 匯入時為 `"robot"` |

### `patrol_schedule.json` -- 排程巡檢

```json
[
  {
    "id": "a1b2c3d4",
    "time": "08:00",
    "days": [0, 1, 2, 3, 4],
    "enabled": true
  }
]
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | string | UUID 識別碼 |
| `time` | string | 時間，`HH:MM` 格式 |
| `days` | int[] | 星期幾 (0=週一，6=週日) |
| `enabled` | boolean | 此排程是否啟用 |

## 日誌設定

日誌檔寫入 `LOG_DIR`，以機器人 ID 為前綴：

| 日誌檔 | 來源 | 內容 |
|--------|------|------|
| `{robot_id}_app.log` | `app.py` | Flask 應用程式日誌 |
| `{robot_id}_cloud_ai_service.log` | `cloud_ai_service.py` | AI 推論日誌、token 使用量 |
| `{robot_id}_patrol_service.log` | `patrol_service.py` | 巡檢執行日誌 |
| `{robot_id}_video_recorder.log` | `video_recorder.py` | 錄影日誌 |
| `{robot_id}_edge_ai_service.log` | `edge_ai_service.py` | 即時監控警報日誌、WebSocket 狀態 |
| `{robot_id}_relay_manager.log` | `relay_manager.py` | ffmpeg relay 程序日誌 |
| `{robot_id}_frame_hub.log` | `frame_hub.py` | 畫面輪詢及 RTSP 推送日誌 |

所有日誌器使用 `TimezoneFormatter`，以設定的時區格式化時間戳記。Flask/Werkzeug 請求日誌被抑制 (設為 ERROR 層級)。

日誌輸出同時寫入日誌檔和標準輸出 (供 Docker `docker compose logs` 使用)。
