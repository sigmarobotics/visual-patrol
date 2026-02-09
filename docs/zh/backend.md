# 後端文件

## 概述

後端為 Python Flask 應用程式，提供 REST API、透過 gRPC 管理機器人通訊、調度巡檢任務、執行 AI 推論、並產生 PDF 報告。

## 檔案結構

```
src/backend/
├── app.py               # Flask 應用程式、路由定義、啟動流程
├── config.py            # 環境變數、路徑、預設值
├── database.py          # SQLite schema、遷移、DB 輔助函式
├── settings_service.py  # 全域設定 CRUD (包裝 DB 資料表)
├── robot_service.py     # Kachaka 機器人 gRPC 介面
├── patrol_service.py    # 巡檢調度、排程
├── cloud_ai_service.py        # Google Gemini AI 整合
├── pdf_service.py       # PDF 報告生成 (ReportLab)
├── edge_ai_service.py      # 巡檢期間透過 VILA Alert API 的背景鏡頭監控
├── video_recorder.py    # 巡檢錄影 (OpenCV)
├── utils.py             # JSON I/O、時區輔助函式
├── logger.py            # 時區感知的日誌設定
└── requirements.txt     # Python 相依套件
```

## 服務架構

各服務以模組層級的單例 (singleton) 實例化。匯入順序很重要，因為服務在模組載入時就會從資料庫讀取設定。

```
config.py           -- 最先載入 (環境變數、路徑)
    |
database.py         -- Schema 初始化 (在服務匯入前呼叫 init_db)
    |
settings_service.py -- 讀取 global_settings 資料表
    |
robot_service.py    -- 連線至 Kachaka (從環境變數讀取 ROBOT_IP)
cloud_ai_service.py       -- 設定 Gemini 客戶端 (從設定讀取 API 金鑰)
patrol_service.py   -- 匯入 robot_service、ai_service、settings_service
edge_ai_service.py     -- 由 patrol_service 使用 (延遲匯入)
pdf_service.py      -- 從資料庫讀取報告資料
video_recorder.py   -- 由 patrol_service 使用
utils.py            -- 由 patrol_service、app.py 使用
logger.py           -- 由 ai_service、patrol_service、video_recorder 使用
```

## 模組

### `config.py`

讀取環境變數並定義檔案系統路徑。

**環境變數：**

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ROBOT_ID` | `"default"` | 機器人唯一識別碼 |
| `ROBOT_NAME` | `"Robot"` | 顯示名稱 |
| `ROBOT_IP` | `"192.168.50.133:26400"` | Kachaka gRPC 位址 |
| `DATA_DIR` | `{project}/data` | 共用資料目錄 |
| `LOG_DIR` | `{project}/logs` | 日誌檔案目錄 |
| `PORT` | `5000` | Flask 監聽連接埠 |
| `TZ` | (系統預設) | 系統時區 (Docker) |

**衍生路徑：**

| 路徑 | 值 | 說明 |
|------|------|------|
| `REPORT_DIR` | `{DATA_DIR}/report` | 共用報告目錄 |
| `DB_FILE` | `{REPORT_DIR}/report.db` | SQLite 資料庫 |
| `ROBOT_DATA_DIR` | `{DATA_DIR}/{ROBOT_ID}` | 機器人專屬資料 |
| `ROBOT_CONFIG_DIR` | `{ROBOT_DATA_DIR}/config` | 機器人專屬設定 |
| `ROBOT_IMAGES_DIR` | `{ROBOT_DATA_DIR}/report/images` | 機器人專屬圖片 |
| `POINTS_FILE` | `{ROBOT_CONFIG_DIR}/points.json` | 巡檢點位檔案 |
| `SCHEDULE_FILE` | `{ROBOT_CONFIG_DIR}/patrol_schedule.json` | 排程檔案 |

**證據路徑：**

| 路徑 | 值 | 說明 |
|------|------|------|
| `edge_ai_alerts` 目錄 | `{ROBOT_DATA_DIR}/report/edge_ai_alerts` | 即時監控證據圖片（執行時建立） |

同時定義 `DEFAULT_SETTINGS` 字典，包含所有全域設定的預設值，以及 `ensure_dirs()` / `migrate_legacy_files()` 函式。

### `database.py`

SQLite 資料庫管理，包含 schema 初始化與遷移。

**連線設定：**
- WAL 日誌模式，支援並行存取
- 5000ms 忙碌逾時
- Row factory 以字典方式存取

**Context manager：**
```python
with db_context() as (conn, cursor):
    cursor.execute("SELECT ...")
    # 成功時自動提交，錯誤時回滾
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
| `total_tokens` | INTEGER | 彙總總計 token |
| `video_path` | TEXT | 錄影檔路徑 |
| `video_analysis` | TEXT | AI 影片分析結果 |
| `robot_id` | TEXT | 機器人識別碼 |

**`inspection_results`** -- 每個巡檢點位一筆記錄

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `run_id` | INTEGER FK | 參照 `patrol_runs.id` |
| `point_name` | TEXT | 巡檢點名稱 |
| `coordinate_x` | REAL | 世界 X 座標 |
| `coordinate_y` | REAL | 世界 Y 座標 |
| `prompt` | TEXT | 使用的 AI 提示詞 |
| `ai_response` | TEXT | 原始 AI 回應 (JSON 或文字) |
| `is_ng` | INTEGER | 1 表示異常，0 表示正常 |
| `ai_description` | TEXT | 解析後的描述 |
| `token_usage` | TEXT | Token 使用量 JSON 字串 |
| `prompt_tokens` | INTEGER | 輸入 token |
| `candidate_tokens` | INTEGER | 輸出 token |
| `total_tokens` | INTEGER | 總計 token |
| `image_path` | TEXT | 巡檢圖片的相對路徑 |
| `timestamp` | TEXT | 檢查時間戳記 |
| `robot_moving_status` | TEXT | 移動結果 (`Success`、`Error: ...`) |
| `robot_id` | TEXT | 機器人識別碼 |

**`generated_reports`** -- AI 生成的多日分析報告

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `start_date` | TEXT | 報告期間起始日 |
| `end_date` | TEXT | 報告期間結束日 |
| `report_content` | TEXT | AI 報告內容 (Markdown) |
| `prompt_tokens` | INTEGER | 輸入 token |
| `candidate_tokens` | INTEGER | 輸出 token |
| `total_tokens` | INTEGER | 總計 token |
| `timestamp` | TEXT | 生成時間戳記 |
| `robot_id` | TEXT | 使用的機器人篩選條件 |

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
| `rule` | TEXT | 觸發的警報規則問題 |
| `response` | TEXT | VILA Alert API 回應 |
| `image_path` | TEXT | 證據圖片的相對路徑 |
| `timestamp` | TEXT | 警報時間戳記 |
| `robot_id` | TEXT | 機器人識別碼 |

**`global_settings`** -- 鍵值設定儲存

| 欄位 | 型別 | 說明 |
|------|------|------|
| `key` | TEXT PK | 設定名稱 |
| `value` | TEXT | JSON 編碼的值 |

**Schema 遷移：**

`_run_migrations()` 函式為向後相容性對既有資料表新增欄位。透過嘗試 SELECT 來檢查欄位是否存在，若檢查失敗則透過 ALTER TABLE 新增缺失的欄位。

### `settings_service.py`

對 `database.get_global_settings()` 和 `database.save_global_settings()` 的薄層包裝。

- `get_all()` -- 回傳與 `DEFAULT_SETTINGS` 合併後的設定
- `get(key, default)` -- 取得單一設定
- `save(dict)` -- UPSERT 所有鍵值對
- `migrate_from_json(path)` -- 從舊版 `settings.json` 一次性匯入

### `robot_service.py`

管理與 Kachaka 機器人的 gRPC 連線。

**單例：** `robot_service = RobotService()`

**主要方法：**

| 方法 | 說明 |
|------|------|
| `connect()` | 建立與 `ROBOT_IP` 的 gRPC 連線 |
| `get_client()` | 回傳 gRPC 客戶端 (斷線時回傳 `None`) |
| `get_state()` | 回傳 `{battery, pose, map_info}` |
| `get_map_bytes()` | 回傳 PNG 地圖的位元組 |
| `move_to(x, y, theta)` | 移動機器人至指定位姿 |
| `move_forward(distance, speed)` | 前進/後退 |
| `rotate(angle)` | 原地旋轉 |
| `return_home()` | 返回充電座 |
| `cancel_command()` | 取消目前指令 |
| `get_front_camera_image()` | 取得前置鏡頭 JPEG |
| `get_back_camera_image()` | 取得後置鏡頭 JPEG |
| `get_serial()` | 取得機器人序號 |
| `get_locations()` | 從機器人取得已儲存的位置 |

**執行緒安全：** 使用 `client_lock` 保護 gRPC 客戶端存取，`state_lock` 保護狀態讀寫。

**自動重連：** 輪詢迴圈在持續錯誤時重設 `self.client = None`，觸發下一輪輪詢週期時重新連線。

### `cloud_ai_service.py`

AI 整合，用於視覺巡檢和報告生成。使用 Google Gemini 作為 VLM 供應商。

**單例：** `ai_service = AIService()`

使用 `google-genai` SDK (非已棄用的 `google-generativeai`)。

**主要方法：**

| 方法 | 說明 |
|------|------|
| `generate_inspection(image, prompt, sys_prompt)` | 以結構化 JSON 回應分析影像 |
| `generate_report(prompt)` | 從巡檢資料生成文字報告 |
| `analyze_video(path, prompt)` | 分析巡檢影片 |
| `is_configured()` | 檢查 API 金鑰是否已設定 |
| `get_model_name()` | 取得目前模型名稱 |

**結構化輸出：** `generate_inspection()` 使用 Pydantic `InspectionResult` schema 強制 JSON 回應格式：
```python
class InspectionResult(BaseModel):
    is_NG: bool   # True 表示異常
    Description: str
```

**自動重新設定：** 每次方法呼叫會執行 `_configure()`，檢查設定是否變更並在需要時重新設定客戶端。

**`parse_ai_response()`** 是一個獨立的工具函式，將 AI 回應標準化為 patrol_service 使用的標準字典格式。

### `patrol_service.py`

調度自主巡檢任務。

**單例：** `patrol_service = PatrolService()`

**巡檢流程：**

1. 從 `points.json` 載入已啟用的巡檢點位
2. 驗證 AI 已設定
3. 建立 `patrol_runs` 資料庫記錄
4. 選擇性啟動錄影
5. 選擇性啟動即時監控（若 `enable_edge_ai` 已啟用且 `jetson_host` 已設定）
6. 對每個巡檢點位：
   a. 移動機器人至該點 (`_move_to_point`)
   b. 等待 2 秒穩定
   c. 暫停即時監控
   d. 擷取前置鏡頭影像
   e. 執行 AI 巡檢 (同步或透過 turbo 模式非同步)
   f. 將結果儲存至 `inspection_results` 資料表
   g. 恢復即時監控
7. 停止即時監控
8. 返回基地
9. 等待非同步佇列完成 (turbo 模式)
10. 選擇性分析影片
11. 生成 AI 摘要報告（若有即時監控警報則包含在內）
12. 生成 AI 摘要 Telegram 訊息並發送通知 (若已啟用)
13. 更新巡檢記錄狀態和 token 統計

**Turbo 模式：** 啟用時，影像會在機器人移往下一個點位的同時排入 AI 分析佇列。`_inspection_worker` 執行緒在背景處理佇列。

**排程檢查器：** 背景執行緒每 30 秒執行一次，比對目前時間與已啟用的排程。每個排程每天只會觸發一次 (透過 `trigger_key` 追蹤)。

**圖片命名：** 擷取時圖片儲存為 `{point_name}_processing_{uuid}.jpg`，AI 分析完成後重新命名為 `{point_name}_{OK|NG}_{uuid}.jpg`。

### `edge_ai_service.py`

巡檢期間透過 VILA 的背景鏡頭監控，以及設定頁面的測試監控器。

**單例：** `edge_ai_monitor = LiveMonitor()`、`test_edge_ai = TestLiveMonitor()`

#### VILA API 整合

使用 VILA OpenAI 相容的 chat completions 端點 (`POST /v1/chat/completions`)。每條警報規則以獨立請求發送，附帶鏡頭畫面的 base64 圖片。

**輔助函式：** `_call_vila_chat(vila_url, data_url, rules, system_prompt="", timeout=30)`

- 每條規則發送一個請求，確保小型 VLM 可靠回答是/否
- `system_prompt` 附加在每條規則問題後 (例：`"有沒有人？Answer only yes or no."`)
- 回傳答案字串列表，每條規則一個

**VILA 回應格式：**

VILA 3B (量化版) 以 `0`/`1` 回答而非 `yes`/`no`：

| 回應 | 意義 | 是否觸發？ |
|------|------|-----------|
| `0` | 否 | 否 |
| `1` | 是 | 是 |
| `no` | 否 | 否 |
| `yes` | 是 | 是 |

後端將 `"yes"`、`"true"`、`"1"` 視為觸發警報。不在 `messages` 陣列中使用 system prompt — VILA 3B 對直接問題的回答效果更佳。

#### `LiveMonitor`

以 daemon 執行緒在巡檢期間運行。

**主要方法：**

| 方法 | 說明 |
|------|------|
| `start(run_id, rules, url, frame_func, interval, system_prompt)` | 啟動背景監控 |
| `stop()` | 停止監控並等待執行緒結束 |
| `pause()` | 暫停監控（在巡檢點位檢查期間） |
| `resume()` | 恢復監控（巡檢點位檢查後） |
| `get_alerts()` | 回傳本次巡檢收集的警報列表 |

**巡檢期間生命週期：**

1. 在建立巡檢記錄後啟動（若 `enable_edge_ai` 已啟用且 `jetson_host` 已設定）
2. 在每個巡檢點位檢查前暫停 (`pause()`)
3. 在每個巡檢點位檢查後恢復 (`resume()`)
4. 在巡檢迴圈結束後的 `finally` 區塊中停止
5. 收集的警報會包含在 AI 摘要報告中

**警報處理：**

- 透過 VILA JPS WebSocket 接收串流分析的警報事件
- 每條規則有 60 秒冷卻時間，避免同一規則重複觸發警報
- 證據圖片儲存至 `data/{robot_id}/report/edge_ai_alerts/`
- 警報記錄至 `edge_ai_alerts` 資料庫資料表

#### `TestLiveMonitor`

設定頁面使用的輕量測試監控器 — 不寫入資料庫，在記憶體中保留最多 50 筆結果。

**主要方法：**

| 方法 | 說明 |
|------|------|
| `start(url, rules, frame_func, interval, system_prompt)` | 啟動測試 |
| `stop()` | 停止測試 |
| `get_status()` | 回傳 `{active, check_count, error, results}` |

### `pdf_service.py`

使用 ReportLab 進行伺服器端 PDF 生成。

**主要函式：**

| 函式 | 說明 |
|------|------|
| `generate_patrol_report(run_id)` | 單次巡檢 PDF |
| `generate_analysis_report(content, start, end)` | 多日分析 PDF |

**功能特色：**
- CJK 字型支援 (`STSong-Light` 用於中文字元)
- Markdown 轉 PDF (標題、粗體、斜體、程式碼區塊、表格、列表、引用)
- 巡檢圖片嵌入 PDF
- OK/NG 色彩標示 (綠/紅)
- 頁碼和頁尾

### `video_recorder.py`

使用 OpenCV 錄製巡檢影片。

- 依序嘗試編解碼器：H.264 (`avc1`)、XVID、MJPEG
- 以設定的 FPS (預設 5) 從機器人前置鏡頭擷取畫面
- 將畫面調整為 640x480
- 在背景執行緒中執行

### `utils.py`

共用工具函式：

- `load_json(path, default)` -- 安全的 JSON 檔案載入，含備援值
- `save_json(path, data)` -- 原子性 JSON 儲存 (暫存檔 + 重新命名)
- `get_current_time_str()` -- 時區感知的時間戳記字串
- `get_current_datetime()` -- 時區感知的 datetime 物件
- `get_filename_timestamp()` -- 檔名用時間戳記 (`YYYYMMDD_HHMMSS`)

### `logger.py`

含時區支援的日誌設定。

- `TimezoneFormatter` -- 使用已設定時區的自定義格式化器
- `get_logger(name, file)` -- 建立含檔案和主控台處理器的日誌器
- 日誌檔以機器人 ID 為前綴 (例：`robot-a_app.log`)
- Flask/Werkzeug 請求日誌被抑制 (`logging.ERROR` 層級)

## 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `flask` | >=3.0, <4.0 | Web 框架 |
| `kachaka-api` | >=3.14, <4.0 | Kachaka 機器人 gRPC 客戶端 |
| `numpy` | >=2.2, <3.0 | 陣列運算 (影片畫面) |
| `pillow` | >=10.0, <11.0 | 影像處理 |
| `google-genai` | >=1.0, <2.0 | Google Gemini AI SDK |
| `reportlab` | >=4.0, <5.0 | PDF 生成 |
| `opencv-python-headless` | >=4.9, <5.0 | 影片錄製 |
| `requests` | >=2.31, <3.0 | Telegram API 呼叫 |

## 啟動流程 (`app.py`)

1. 匯入 `config` (讀取環境變數)
2. 呼叫 `ensure_dirs()` (建立資料目錄)
3. 呼叫 `init_db()` (建立/遷移 DB schema)
4. 匯入服務 (它們在模組層級讀取 DB)
5. 建立 Flask 應用程式
6. 設定日誌
7. 註冊路由
8. **在 `__main__` 時：**
   a. 再次 `init_db()` (冪等)
   b. `migrate_from_json()` (舊版設定遷移)
   c. `migrate_legacy_files()` (舊版機器人檔案遷移)
   d. `register_robot()` (在 DB 註冊此實例)
   e. `backfill_robot_id()` (對 NULL 的列設定 robot_id)
   f. 啟動心跳執行緒
   g. `app.run()` 在設定的連接埠上執行
