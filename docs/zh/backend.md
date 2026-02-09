# 後端文件

## 概述

後端是 Python Flask 應用程式，提供 REST API、透過 gRPC 管理機器人通訊、以 frame_hub 集中管理攝影機畫面、調度巡檢任務、執行 AI 推論、管理 RTSP 攝影機中繼、透過 VILA JPS 進行即時監控，並產生 PDF 報告。

## 檔案結構

```
src/backend/
|-- app.py               # Flask 應用程式、路由定義、啟動流程
|-- config.py            # 環境變數、路徑、預設值
|-- database.py          # SQLite schema、遷移、DB 輔助函式
|-- settings_service.py  # 全域設定 CRUD (包裝 DB 資料表)
|-- robot_service.py     # Kachaka 機器人 gRPC 介面
|-- frame_hub.py         # 集中式攝影機畫面管理 (gRPC 輪詢 + 快取 + RTSP 推送)
|-- patrol_service.py    # 巡檢調度、排程
|-- cloud_ai_service.py  # Google Gemini AI 整合
|-- edge_ai_service.py   # VILA JPS 即時監控 (WebSocket 警報) + 測試監控器
|-- relay_manager.py     # Jetson relay 服務 HTTP 客戶端
|-- pdf_service.py       # PDF 報告生成 (ReportLab)
|-- video_recorder.py    # 巡檢期間錄影 (OpenCV)
|-- utils.py             # JSON I/O、時區輔助工具
|-- logger.py            # 時區感知日誌設定
+-- requirements.txt     # Python 相依套件
```

## 服務架構

服務以模組層級 singleton 方式實例化。匯入順序重要，因為服務在模組載入時從資料庫讀取設定。

```
config.py           -- 最先載入 (環境變數、路徑)
    |
database.py         -- Schema 初始化 (init_db 在服務匯入前呼叫)
    |
settings_service.py -- 讀取 global_settings 資料表
    |
robot_service.py    -- 連線 Kachaka (從環境變數讀取 ROBOT_IP)
frame_hub.py        -- 集中式畫面管理 (依賴 robot_service)
cloud_ai_service.py -- 設定 Gemini 客戶端 (從設定讀取 API key)
relay_manager.py    -- Relay 服務客戶端 (Jetson relay HTTP 客戶端)
patrol_service.py   -- 匯入 robot_service, frame_hub, cloud_ai_service, relay_manager, edge_ai_service
edge_ai_service.py  -- patrol_service 使用 (VILA JPS API + WebSocket)
pdf_service.py      -- 從資料庫讀取報告資料
video_recorder.py   -- patrol_service 使用
utils.py            -- patrol_service, app.py 使用
logger.py           -- cloud_ai_service, patrol_service, video_recorder, relay_manager, edge_ai_service, frame_hub 使用
```

## 模組

### `config.py`

讀取環境變數並定義檔案系統路徑。

**環境變數：**

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ROBOT_ID` | `"default"` | 唯一機器人識別碼 |
| `ROBOT_NAME` | `"Robot"` | 顯示名稱 |
| `ROBOT_IP` | `"192.168.50.133:26400"` | Kachaka gRPC 位址 |
| `DATA_DIR` | `{project}/data` | 共用資料目錄 |
| `LOG_DIR` | `{project}/logs` | 日誌檔案目錄 |
| `PORT` | `5000` | Flask 監聽連接埠 |
| `TZ` | (系統預設) | 系統時區 (Docker) |
| `RELAY_SERVICE_URL` | `""` | Jetson relay 服務 URL (空白 = relay 不可用) |

**Jetson 服務連接埠常數：**

| 常數 | 值 | 說明 |
|------|------|------|
| `JETSON_JPS_API_PORT` | `5010` | VILA JPS API 連接埠 |
| `JETSON_MEDIAMTX_PORT` | `8555` | mediamtx RTSP 連接埠 |

**衍生路徑：**

| 路徑 | 值 | 說明 |
|------|------|------|
| `REPORT_DIR` | `{DATA_DIR}/report` | 共用報告目錄 |
| `DB_FILE` | `{REPORT_DIR}/report.db` | SQLite 資料庫 |
| `ROBOT_DATA_DIR` | `{DATA_DIR}/{ROBOT_ID}` | 每台機器人資料 |
| `ROBOT_CONFIG_DIR` | `{ROBOT_DATA_DIR}/config` | 每台機器人設定 |
| `ROBOT_IMAGES_DIR` | `{ROBOT_DATA_DIR}/report/images` | 每台機器人圖片 |
| `POINTS_FILE` | `{ROBOT_CONFIG_DIR}/points.json` | 巡檢點位檔案 |
| `SCHEDULE_FILE` | `{ROBOT_CONFIG_DIR}/patrol_schedule.json` | 排程檔案 |

**證據路徑：**

| 路徑 | 值 | 說明 |
|------|------|------|
| `edge_ai_alerts` 目錄 | `{ROBOT_DATA_DIR}/report/edge_ai_alerts` | 即時監控證據圖片 (執行時建立) |

同時定義 `DEFAULT_SETTINGS` 字典 (所有全域設定的預設值) 以及 `ensure_dirs()` / `migrate_legacy_files()` 函式。

### `database.py`

SQLite 資料庫管理，含 schema 初始化與遷移。

**連線設定：**
- WAL 日誌模式以支援並行存取
- 5000ms 忙碌逾時
- Row factory 用於字典式存取

**Context Manager：**
```python
with db_context() as (conn, cursor):
    cursor.execute("SELECT ...")
    # 成功自動提交，錯誤時回滾
```

#### 資料庫 Schema

**`patrol_runs`** -- 每次巡檢任務一筆記錄

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `start_time` | TEXT | 巡檢開始時間 |
| `end_time` | TEXT | 巡檢結束時間 |
| `status` | TEXT | `Running`、`Completed`、`Patrol Stopped` |
| `robot_serial` | TEXT | Kachaka 序號 |
| `report_content` | TEXT | AI 生成的摘要報告 (Markdown) |
| `model_id` | TEXT | Gemini 模型名稱 |
| `token_usage` | TEXT | Token 使用量 JSON 字串 |
| `prompt_tokens` | INTEGER | 彙總輸入 token |
| `candidate_tokens` | INTEGER | 彙總輸出 token |
| `total_tokens` | INTEGER | 彙總總 token |
| `video_path` | TEXT | 錄影路徑 |
| `video_analysis` | TEXT | AI 影片分析結果 |
| `robot_id` | TEXT | 機器人識別碼 |

**`inspection_results`** -- 每個巡檢點一筆記錄

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `run_id` | INTEGER FK | 參照 `patrol_runs.id` |
| `point_name` | TEXT | 巡檢點名稱 |
| `coordinate_x` | REAL | 世界 X 座標 |
| `coordinate_y` | REAL | 世界 Y 座標 |
| `prompt` | TEXT | 使用的 AI 提示詞 |
| `ai_response` | TEXT | 原始 AI 回應 (JSON 或文字) |
| `is_ng` | INTEGER | 1 為異常，0 為正常 |
| `ai_description` | TEXT | 解析後的描述 |
| `token_usage` | TEXT | Token 使用量 JSON 字串 |
| `prompt_tokens` | INTEGER | 輸入 token |
| `candidate_tokens` | INTEGER | 輸出 token |
| `total_tokens` | INTEGER | 總 token |
| `image_path` | TEXT | 巡檢圖片相對路徑 |
| `timestamp` | TEXT | 巡檢時間戳記 |
| `robot_moving_status` | TEXT | 移動結果 (`Success`、`Error: ...`) |
| `robot_id` | TEXT | 機器人識別碼 |

**`generated_reports`** -- AI 生成的多日分析報告

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `start_date` | TEXT | 報告期間開始日期 |
| `end_date` | TEXT | 報告期間結束日期 |
| `report_content` | TEXT | AI 報告內容 (Markdown) |
| `prompt_tokens` | INTEGER | 輸入 token |
| `candidate_tokens` | INTEGER | 輸出 token |
| `total_tokens` | INTEGER | 總 token |
| `timestamp` | TEXT | 生成時間戳記 |
| `robot_id` | TEXT | 使用的機器人篩選器 |

**`robots`** -- 已註冊的機器人實例

| 欄位 | 型別 | 說明 |
|------|------|------|
| `robot_id` | TEXT PK | 唯一識別碼 |
| `robot_name` | TEXT | 顯示名稱 |
| `robot_ip` | TEXT | gRPC 位址 |
| `last_seen` | TEXT | 最後心跳時間 |
| `status` | TEXT | `online` 或 `offline` |

**`edge_ai_alerts`** -- 巡檢期間觸發的即時監控警報

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `run_id` | INTEGER FK | 參照 `patrol_runs.id` |
| `rule` | TEXT | 觸發的警報規則 |
| `response` | TEXT | `"triggered"` (JPS 警報) |
| `image_path` | TEXT | 證據圖片相對路徑 |
| `timestamp` | TEXT | 警報時間戳記 |
| `robot_id` | TEXT | 機器人識別碼 |
| `stream_source` | TEXT | 串流類型：`"robot_camera"`、`"external_rtsp"` 或 `"unknown"` |

**`global_settings`** -- 鍵值對設定儲存

| 欄位 | 型別 | 說明 |
|------|------|------|
| `key` | TEXT PK | 設定名稱 |
| `value` | TEXT | JSON 編碼的值 |

**Schema 遷移：**

`_run_migrations()` 函式為既有資料表新增欄位以維持向後相容性。透過嘗試 SELECT 檢查欄位是否存在，若檢查失敗則透過 ALTER TABLE 新增缺少的欄位。

### `settings_service.py`

`database.get_global_settings()` 和 `database.save_global_settings()` 的薄層包裝。

- `get_all()` -- 回傳與 `DEFAULT_SETTINGS` 合併後的設定
- `get(key, default)` -- 取得單一設定
- `save(dict)` -- UPSERT 所有鍵值對
- `migrate_from_json(path)` -- 從舊版 `settings.json` 一次性匯入

**重要：** `settings_service` 為模組層級函式 (非類別) -- 使用 `import settings_service` 然後 `settings_service.get(...)`，而非 `from settings_service import settings_service`。

### `robot_service.py`

管理與 Kachaka 機器人的 gRPC 連線。

**Singleton:** `robot_service = RobotService()`

**主要方法：**

| 方法 | 說明 |
|------|------|
| `connect()` | 建立至 `ROBOT_IP` 的 gRPC 連線 |
| `get_client()` | 回傳 gRPC 客戶端 (斷線時為 `None`) |
| `get_state()` | 回傳 `{battery, pose, map_info}` |
| `get_map_bytes()` | 回傳 PNG 地圖位元組 |
| `move_to(x, y, theta)` | 移動機器人至指定位姿 |
| `move_forward(distance, speed)` | 前進/後退 |
| `rotate(angle)` | 原地旋轉 |
| `return_home()` | 返回充電站 |
| `cancel_command()` | 取消目前指令 |
| `get_front_camera_image()` | 取得前方攝影機 JPEG (同時作為 frame_hub 的 frame_func) |
| `get_back_camera_image()` | 取得後方攝影機 JPEG |
| `get_serial()` | 取得機器人序號 |
| `get_locations()` | 取得機器人已儲存的位置 |

**執行緒安全：** 使用 `client_lock` 保護 gRPC 客戶端存取，`state_lock` 保護狀態讀寫。

**自動重連：** 輪詢迴圈在持續錯誤時將 `self.client = None`，於下次輪詢週期觸發重新連線。

### `frame_hub.py`

集中式攝影機畫面管理 (280 行) -- 單一 gRPC 輪詢執行緒供應記憶體畫面快取，所有本地消費者 (前端 MJPEG、Gemini 巡檢、錄影、證據擷取) 皆讀取快取，不再獨立呼叫 gRPC。

**Singleton:** `frame_hub = FrameHub(robot_service.get_front_camera_image)`

**常數：**

| 常數 | 值 | 說明 |
|------|------|------|
| `POLL_INTERVAL` | `0.1` | ~10fps gRPC 輪詢 |
| `FEEDER_INTERVAL` | `0.5` | 2fps ffmpeg 推送 |
| `PUSH_MONITOR_INTERVAL` | `5.0` | 每 5 秒檢查 ffmpeg 健康狀態 |

**主要方法：**

| 方法 | 說明 |
|------|------|
| `start_polling()` | 啟動 gRPC 輪詢，以 ~10fps 更新畫面快取 (冪等) |
| `stop_polling()` | 停止 gRPC 輪詢，畫面快取設為 None (冪等) |
| `get_latest_frame()` | 回傳最新快取的 gRPC 畫面回應 (含 `.data` 屬性) |
| `set_patrol_active(active)` | patrol_service 呼叫，巡檢啟停時 |
| `on_idle_stream_changed(enabled)` | `enable_idle_stream` 設定變更時呼叫 |
| `start_rtsp_push(target, path)` | 啟動 ffmpeg 將快取畫面推送至 mediamtx RTSP |
| `stop_rtsp_push()` | 停止 ffmpeg RTSP 推送 |
| `wait_for_push_ready(timeout)` | 等待至少一個畫面已寫入 ffmpeg |

**輪詢生命週期：**
- 巡檢中：一律輪詢
- 閒置 + `enable_idle_stream=true`：輪詢 (前端顯示即時畫面)
- 閒置 + `enable_idle_stream=false`：不輪詢 (零 gRPC 頻寬)

**RTSP 推送：** 啟動 ffmpeg 子程序，以 `image2pipe` 模式接收 JPEG 畫面，轉碼為 H264 Baseline profile，推送至 mediamtx RTSP。含 monitor 執行緒自動重啟 ffmpeg。

### `cloud_ai_service.py`

視覺巡檢與報告生成的 AI 整合。使用 Google Gemini 作為 VLM 供應商。

**Singleton:** `ai_service = AIService()`

使用 `google-genai` SDK (非已棄用的 `google-generativeai`)。

**主要方法：**

| 方法 | 說明 |
|------|------|
| `generate_inspection(image, prompt, sys_prompt)` | 以結構化 JSON 回應分析圖片 |
| `generate_report(prompt)` | 從巡檢資料產生文字報告 |
| `analyze_video(path, prompt)` | 分析巡檢影片 |
| `is_configured()` | 檢查 API key 是否已設定 |
| `get_model_name()` | 取得目前模型名稱 |

**結構化輸出：** `generate_inspection()` 使用 Pydantic `InspectionResult` schema 強制 JSON 回應格式：
```python
class InspectionResult(BaseModel):
    is_NG: bool   # True 為異常
    Description: str
```

**自動重設：** 每次方法呼叫執行 `_configure()`，檢查設定是否變更並在需要時重新設定客戶端。

**`parse_ai_response()`** 是獨立工具函式，將 AI 回應正規化為 patrol_service 使用的標準字典格式。

### `relay_manager.py`

Jetson 端 relay 服務的 HTTP 客戶端。

**模組層級：** `relay_service_client = RelayServiceClient(URL) if RELAY_SERVICE_URL else None`

**類別：**

- **`RelayServiceClient`** -- Jetson relay 服務 REST API 的 HTTP 客戶端

| 方法 | 說明 |
|------|------|
| `is_available()` | 檢查 relay 服務是否可達 |
| `start_relay(key, source_url)` | 在服務上啟動 relay (source_url 為 RTSP URL) |
| `stop_relay(key)` | 停止特定 relay |
| `stop_all()` | 停止所有啟用中的 relay |
| `wait_for_stream(key, timeout)` | 等待 relay 服務上的串流就緒 |
| `get_status()` | 從服務取得所有 relay 的狀態 |

當 `RELAY_SERVICE_URL` 未設定時，`relay_service_client` 為 `None`，relay 功能不可用，即時監控無法啟動。

### `patrol_service.py`

調度自主巡檢任務。

**Singleton:** `patrol_service = PatrolService()`

**巡檢流程：**

1. 從 `points.json` 載入已啟用的巡檢點
2. 驗證 AI 已設定
3. 建立 `patrol_runs` DB 記錄
4. 選擇性啟動錄影
5. 啟動 RTSP relay (若 `enable_robot_camera_relay` 或 `enable_external_rtsp` 已啟用)
6. 等待 3 秒讓串流建立
7. 啟動即時監控 (若串流、規則和 `jetson_host` 已設定)
8. 對每個巡檢點：
   a. 移動機器人至點位 (`_move_to_point`)
   b. 等待 2 秒穩定
   c. 擷取前方攝影機畫面 (透過 frame_hub 快取)
   d. 執行 AI 巡檢 (同步或透過 turbo 模式非同步)
   e. 儲存結果至 `inspection_results` 資料表
9. 停止即時監控
10. 停止 RTSP relay
11. 返回基地
12. 等待非同步佇列 (turbo 模式)
13. 選擇性分析影片
14. 產生 AI 摘要報告 (若有即時監控警報，一併納入)
15. 產生 AI 摘要 Telegram 訊息並傳送通知 (若已啟用)
16. 更新巡檢狀態與 token 用量

**Turbo 模式：** 啟用時，圖片排入佇列進行 AI 分析，機器人同時移動至下個巡檢點。`_inspection_worker` 執行緒在背景處理佇列。

**排程檢查器：** 背景執行緒每 30 秒執行一次，比對目前時間與已啟用的排程。每個排程每天只能觸發一次 (以 `trigger_key` 追蹤)。

**圖片命名：** 擷取時儲存為 `{point_name}_processing_{uuid}.jpg`，AI 分析後重新命名為 `{point_name}_{OK|NG}_{uuid}.jpg`。

### `edge_ai_service.py`

巡檢期間透過 VILA JPS Alert API 進行背景攝影機監控，以及設定頁面的測試監控器。

**Singleton:** `edge_ai_monitor = LiveMonitor()`、`test_edge_ai = TestLiveMonitor()`

#### VILA JPS API 整合 (LiveMonitor)

主要的 `LiveMonitor` 類別使用 VILA JPS Stream API + Alert API + WebSocket 進行高效持續監控。VILA 在內部處理持續畫面擷取和規則評估 -- 後端僅在警報觸發時接收 WebSocket 事件。

**生命週期：**

1. **註冊串流**: `POST {vila_jps_url}/api/v1/live-stream` (含 `{liveStreamUrl, name}`) --> 回傳 `stream_id`
2. **設定警報規則**: `POST {vila_jps_url}/api/v1/alerts` (含 `{alerts, id}`) 對每個串流
3. **WebSocket 監聽器**: 連線至 `ws://{host}:5016/api/v1/alerts/ws`，監聽警報事件
4. **警報事件處理**: 冷卻檢查 --> 擷取證據畫面 --> 儲存至 DB + 磁碟 --> 傳送 Telegram
5. **停止**: 關閉 WebSocket --> `DELETE {vila_jps_url}/api/v1/live-stream/{id}` 取消註冊每個串流

**主要方法：**

| 方法 | 說明 |
|------|------|
| `start(run_id, config)` | 使用 VILA JPS 設定字典啟動監控 |
| `stop()` | 停止監控，取消註冊串流 |
| `get_alerts()` | 回傳本次收集的警報列表 |

**設定字典：**
```python
{
    "vila_jps_url": "http://localhost:5010",
    "streams": [
        {"rtsp_url": "rtsp://...", "name": "Robot Camera",
         "type": "robot_camera", "evidence_func": callable},
        {"rtsp_url": "rtsp://...", "name": "External Camera",
         "type": "external_rtsp"},
    ],
    "rules": ["Is there a person?", "Is there fire?"],
    "telegram_config": {"bot_token": "...", "user_id": "..."},
    "mediamtx_external": "localhost:8555",
}
```

**證據擷取：** 機器人攝影機警報使用 `evidence_func()` (gRPC via frame_hub) 取得最佳畫質。外部 RTSP 警報使用 `cv2.VideoCapture()` 從 relay URL 擷取畫面。

**WebSocket 重連：** 斷線時以 5 秒延遲重試，最多 10 次重連嘗試。成功連線時重置重連計數器。

**限制：** 每個串流最多 10 條警報規則 (VILA JPS 限制)。每條規則冷卻 60 秒以防重複警報 (與 VILA 自身冷卻機制形成雙重防護)。

#### 測試監控器 (TestLiveMonitor)

`TestLiveMonitor` 使用相同的 JPS 流程 (relay --> mediamtx --> JPS --> WebSocket 警報) 進行設定頁面的快速測試，不寫入 DB。

**流程：** 啟動 relay --> 等待 mediamtx 串流就緒 --> 向 JPS 註冊串流 --> 設定規則 --> WebSocket 監聽。同時從 mediamtx RTSP 擷取快照畫面以驗證 relay 管線。

**主要方法：**

| 方法 | 說明 |
|------|------|
| `start(config)` | 啟動測試工作階段 |
| `stop()` | 停止測試工作階段 |
| `get_status()` | 回傳 `{active, ws_connected, alert_count, alerts, ws_messages, error}` |
| `get_snapshot()` | 回傳最新 JPEG 位元組 (從 mediamtx 擷取) |

### `pdf_service.py`

使用 ReportLab 進行伺服器端 PDF 生成。

**主要函式：**

| 函式 | 說明 |
|------|------|
| `generate_patrol_report(run_id)` | 單次巡檢 PDF |
| `generate_analysis_report(content, start, end)` | 多日分析 PDF |

**功能：**
- CJK 字型支援 (`STSong-Light` 用於中文字元)
- Markdown 轉 PDF 轉換 (標題、粗體、斜體、程式碼區塊、表格、列表、引用)
- 巡檢圖片嵌入 PDF
- OK/NG 顏色編碼 (綠色/紅色)
- 頁碼與頁尾

### `video_recorder.py`

使用 OpenCV 錄製巡檢影片。

- 依序嘗試編碼器：H.264 (`avc1`)、XVID、MJPEG
- 以設定的 FPS (預設 5) 從機器人前方攝影機擷取畫面
- 畫面縮放至 640x480
- 在背景執行緒中執行

### `utils.py`

共用工具函式：

- `load_json(path, default)` -- 安全的 JSON 檔案載入，含備援值
- `save_json(path, data)` -- 原子式 JSON 儲存 (暫存檔 + 重新命名)
- `get_current_time_str()` -- 時區感知的時間戳記字串
- `get_current_datetime()` -- 時區感知的 datetime 物件
- `get_filename_timestamp()` -- 檔名用時間戳記 (`YYYYMMDD_HHMMSS`)

### `logger.py`

含時區支援的日誌設定。

- `TimezoneFormatter` -- 使用已設定時區的自訂格式器
- `get_logger(name, file)` -- 建立含檔案 + 主控台處理器的 logger
- 日誌檔案以 robot ID 為前綴 (例：`robot-a_app.log`)
- Flask/Werkzeug 請求日誌已抑制 (`logging.ERROR` 等級)

## 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `flask` | >=3.0, <4.0 | 網頁框架 |
| `kachaka-api` | >=3.14, <4.0 | Kachaka 機器人 gRPC 客戶端 |
| `numpy` | >=2.2, <3.0 | 陣列運算 (影片畫面) |
| `pillow` | >=10.0, <11.0 | 圖片處理 |
| `google-genai` | >=1.0, <2.0 | Google Gemini AI SDK |
| `reportlab` | >=4.0, <5.0 | PDF 生成 |
| `opencv-python-headless` | >=4.9, <5.0 | 錄影、RTSP 證據擷取 |
| `requests` | >=2.31, <3.0 | HTTP API 呼叫 (Telegram、VILA JPS、relay 服務) |
| `websocket-client` | >=1.6, <2.0 | VILA JPS WebSocket 連線 |

## 啟動流程 (`app.py`)

1. 匯入 `config` (讀取環境變數)
2. 呼叫 `ensure_dirs()` (建立資料目錄)
3. 呼叫 `init_db()` (建立/遷移 DB schema)
4. 匯入服務 (它們在模組層級讀取 DB)
5. 建立 Flask app
6. 設定日誌
7. 註冊路由
8. **在 `__main__` 時：**
   a. `init_db()` 再次呼叫 (冪等)
   b. `migrate_from_json()` (舊版設定遷移)
   c. `migrate_legacy_files()` (舊版每台機器人檔案遷移)
   d. `register_robot()` (將此實例註冊至 DB)
   e. `backfill_robot_id()` (為 NULL 列設定 robot_id)
   f. 啟動心跳執行緒
   g. `app.run()` 在設定的連接埠上執行
