# API 參考文件

## URL 慣例

所有 API 端點以 `/api/` 開頭。

- **機器人專屬**: `/api/{robot-id}/endpoint` -- nginx 移除 `{robot-id}` 前綴後代理至對應的後端。後端收到的是 `/api/endpoint`。
- **全域**: `/api/endpoint` -- 代理至任一後端 (所有後端共用同一個資料庫)。

`{robot-id}` 必須符合 `robot-[a-z0-9-]+` 格式 (例：`robot-a`、`robot-b`)。

---

## 機器人控制 (機器人專屬)

### GET `/api/{id}/state`

回傳機器人目前狀態。

**回應：**
```json
{
  "battery": 85,
  "pose": { "x": 1.23, "y": 4.56, "theta": 0.78 },
  "map_info": {
    "resolution": 0.05,
    "width": 800,
    "height": 600,
    "origin_x": -5.0,
    "origin_y": -3.0
  },
  "robot_id": "robot-a",
  "robot_name": "Robot A"
}
```

### GET `/api/{id}/robot_info`

僅回傳機器人識別資訊。

**回應：**
```json
{
  "robot_id": "robot-a",
  "robot_name": "Robot A"
}
```

### GET `/api/{id}/map`

回傳機器人的 PNG 地圖圖片。

**回應：** `image/png` 二進位資料，若地圖不可用則回傳 `404`。

### POST `/api/{id}/move`

移動機器人至目標位姿。

**請求：**
```json
{
  "x": 1.5,
  "y": 2.0,
  "theta": 0.0
}
```

- `x`, `y`：必填 (float)。世界座標。
- `theta`：選填 (float, 預設 `0.0`)。方向角 (弧度)，範圍 -2pi 至 2pi。

**回應：**
```json
{ "status": "Moving", "target": { "x": 1.5, "y": 2.0, "theta": 0.0 } }
```

**錯誤：** `400` (參數缺少/無效)、`503` (機器人斷線)。

### POST `/api/{id}/manual_control`

發送手動方向鍵控制指令。

**請求：**
```json
{ "action": "forward" }
```

有效動作：`forward` (0.1m)、`backward` (-0.1m)、`left` (+10 度)、`right` (-10 度)。

**回應：**
```json
{ "status": "Command sent", "action": "forward" }
```

### POST `/api/{id}/return_home`

命令機器人返回充電站。

**回應：**
```json
{ "status": "Returning home" }
```

### POST `/api/{id}/cancel_command`

取消機器人目前的移動指令。

**回應：**
```json
{ "status": "Command cancelled" }
```

### GET `/api/{id}/camera/front`

回傳機器人前方攝影機的 MJPEG 串流。畫面來自 frame_hub 快取。

**回應：** `multipart/x-mixed-replace; boundary=frame` (連續 JPEG 串流，約 10fps)。

### GET `/api/{id}/camera/back`

回傳機器人後方攝影機的 MJPEG 串流。

**回應：** 格式同前方攝影機。

---

## AI 測試 (機器人專屬)

### POST `/api/{id}/test_ai`

從前方攝影機擷取畫面 (透過 frame_hub) 並執行 AI 分析。

**請求：**
```json
{
  "prompt": "Is there a fire hazard?"
}
```

- `prompt`：選填。預設為 `"Describe what you see and check if everything is normal."`

**回應：**
```json
{
  "result": { "is_NG": false, "Description": "Everything appears normal." },
  "prompt": "Is there a fire hazard?",
  "usage": {
    "prompt_token_count": 258,
    "candidates_token_count": 45,
    "total_token_count": 303
  }
}
```

**錯誤：** `503` (攝影機不可用)、`500` (AI 錯誤)。

---

## 即時監控測試 (機器人專屬)

使用 VILA JPS 串流管線 (relay --> mediamtx --> JPS --> WebSocket 警報) 進行設定頁面的快速測試。

### POST `/api/{id}/test_edge_ai/start`

啟動即時監控測試工作階段。啟動 relay、向 VILA JPS 註冊串流、設定警報規則，並監聽 WebSocket 警報。

**請求：**
```json
{
  "jetson_host": "192.168.50.35",
  "rules": ["Is there a person?", "Is there fire?"],
  "stream_source": "robot_camera",
  "external_rtsp_url": ""
}
```

所有欄位皆為選填 -- 未提供時使用已儲存的設定值。

**回應：**
```json
{ "status": "started" }
```

**錯誤：** `400` (缺少 URL 或規則)、`409` (測試已在執行中)。

### POST `/api/{id}/test_edge_ai/stop`

停止執行中的測試工作階段。

**回應：**
```json
{ "status": "stopped" }
```

### GET `/api/{id}/test_edge_ai/status`

回傳目前測試工作階段狀態及結果。

**回應：**
```json
{
  "active": true,
  "ws_connected": true,
  "alert_count": 1,
  "alerts": [
    {
      "rule": "Is there a person?",
      "timestamp": "2026-02-06 23:05:58",
      "image": "data:image/jpeg;base64,..."
    }
  ],
  "ws_messages": [
    { "timestamp": "2026-02-06 23:05:58", "event": { "rule_string": "Is there a person?", "..." : "..." } }
  ],
  "error": null
}
```

---

## 巡檢管理 (機器人專屬)

### GET `/api/{id}/patrol/status`

回傳目前巡檢狀態。

**回應：**
```json
{
  "is_patrolling": true,
  "status": "Moving to Point 1...",
  "current_index": 0
}
```

### POST `/api/{id}/patrol/start`

啟動巡檢任務。機器人將依序造訪所有已啟用的巡檢點位。

**回應：**
```json
{ "status": "started" }
```

**錯誤：** `400` 若已在巡檢中。

### POST `/api/{id}/patrol/stop`

停止目前巡檢。機器人取消目前指令並返回基地。

**回應：**
```json
{ "status": "stopping" }
```

### GET `/api/{id}/patrol/edge_ai_alerts`

回傳目前進行中巡檢的即時監控警報。若無進行中的巡檢或即時監控未啟用，則回傳空列表。

**回應：**
```json
[
  {
    "id": 1,
    "rule": "Is there a person lying on the floor?",
    "response": "triggered",
    "image_path": "report/edge_ai_alerts/42_1707200000_Is_there_a_person_lying_on_the_floor_.jpg",
    "timestamp": "2026-02-06 14:05:00",
    "stream_source": "robot_camera"
  }
]
```

結果按最新在前排列 (`ORDER BY id DESC`)。

### GET `/api/{id}/patrol/results`

回傳目前進行中巡檢的檢查結果。若無進行中的巡檢則回傳空列表。

**回應：**
```json
[
  {
    "point_name": "Lobby",
    "result": "{\"is_NG\": false, \"Description\": \"Normal\"}",
    "timestamp": "2026-02-06 14:30:00"
  }
]
```

### GET `/api/{id}/patrol/schedule`

回傳此機器人的所有排程巡檢。

**回應：**
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

星期：`0` = 週一 至 `6` = 週日。

### POST `/api/{id}/patrol/schedule`

新增排程巡檢。

**請求：**
```json
{
  "time": "08:00",
  "days": [0, 1, 2, 3, 4],
  "enabled": true
}
```

- `time`：必填。格式 `HH:MM`。
- `days`：選填。整數列表 0-6。預設為每天。
- `enabled`：選填。預設 `true`。

**回應：**
```json
{
  "status": "added",
  "schedule": { "id": "a1b2c3d4", "time": "08:00", "days": [0,1,2,3,4], "enabled": true }
}
```

### PUT `/api/{id}/patrol/schedule/{schedule_id}`

更新排程巡檢。

**請求：**
```json
{
  "time": "09:00",
  "days": [0, 1, 2, 3, 4, 5],
  "enabled": false
}
```

所有欄位皆為選填。

### DELETE `/api/{id}/patrol/schedule/{schedule_id}`

刪除排程巡檢。

**回應：**
```json
{ "status": "deleted" }
```

---

## 巡檢點位 (機器人專屬)

### GET `/api/{id}/points`

回傳此機器人的所有巡檢點位。

**回應：**
```json
[
  {
    "id": "1706000000000",
    "name": "Lobby",
    "x": 1.5,
    "y": 2.0,
    "theta": 0.0,
    "prompt": "Is everything normal?",
    "enabled": true
  }
]
```

### POST `/api/{id}/points`

新增或更新巡檢點位。

**請求：**
```json
{
  "id": "optional-existing-id",
  "name": "Lobby",
  "x": 1.5,
  "y": 2.0,
  "theta": 0.0,
  "prompt": "Check for hazards",
  "enabled": true
}
```

- `name`, `x`, `y`：必填。
- `id`：若提供且匹配既有點位則更新。否則以自動產生的 ID 建立新點位。

### DELETE `/api/{id}/points?id={point_id}`

依 ID 刪除巡檢點位。

### POST `/api/{id}/points/reorder`

取代整個點位列表 (用於拖曳排序)。

**請求：** 點位物件陣列 (格式同 GET 回應)。

### GET `/api/{id}/points/export`

下載所有點位為 JSON 檔案。

**回應：** `application/json` 檔案下載 (`patrol_points.json`)。

### POST `/api/{id}/points/import`

上傳 JSON 檔案取代所有點位。

**請求：** 含 `file` 欄位的 Multipart 表單，內含 JSON 檔案。

**回應：**
```json
{ "status": "imported", "count": 5 }
```

### GET `/api/{id}/points/from_robot`

從 Kachaka 機器人取得已儲存的位置並合併至現有點位。跳過重複項 (相同名稱和座標)。

**回應：**
```json
{
  "status": "success",
  "added": ["Kitchen", "Hallway"],
  "skipped": ["Lobby"],
  "total_robot_locations": 3,
  "total_points": 5
}
```

---

## 基礎設施端點 (全域)

### GET `/api/relay/status`

回傳所有啟用中 RTSP relay 程序的狀態。

**回應：**
```json
{
  "robot-a/camera": {
    "type": "robot_camera",
    "running": true,
    "uptime": 125.3,
    "restart_count": 0
  },
  "robot-a/external": {
    "type": "external_rtsp",
    "running": true,
    "uptime": 124.8,
    "restart_count": 0
  }
}
```

無 relay 啟用時回傳 `{}`。

### POST `/api/relay/test`

快速測試機器人攝影機 relay。啟動 relay，等待 3 秒，檢查狀態，然後停止。

**回應 (成功)：**
```json
{
  "status": "ok",
  "relay_status": {
    "robot-a/camera": {
      "type": "robot_camera",
      "running": true,
      "uptime": 3.1,
      "restart_count": 0
    }
  }
}
```

**回應 (失敗)：**
```json
{
  "status": "error",
  "error": "Camera not available"
}
```

### GET `/api/edge_ai/health`

透過從 `jetson_host` 衍生 JPS URL 並呼叫 `GET http://{jetson_host}:5010/api/v1/health/ready`，檢查 VILA JPS API 健康狀態。

**回應 (健康)：**
```json
{
  "status": "ok",
  "code": 200
}
```

**回應 (不健康)：**
```json
{
  "status": "error",
  "error": "Connection refused"
}
```

**錯誤：** `400` 若 `jetson_host` 未設定。

---

## 全域端點

### GET/POST `/api/settings`

**GET** 回傳所有設定。敏感欄位 (`gemini_api_key`、`telegram_bot_token`、`telegram_user_id`) 以 `****` 前綴遮罩。

**POST** 儲存設定。以 `****` 開頭的遮罩值會被忽略，避免覆寫實際儲存的值。

**回應 (GET)：**
```json
{
  "gemini_api_key": "****abcd",
  "gemini_model": "gemini-3-flash-preview",
  "timezone": "Asia/Taipei",
  "system_prompt": "You are a helpful robot assistant...",
  "report_prompt": "...",
  "multiday_report_prompt": "...",
  "turbo_mode": false,
  "enable_video_recording": false,
  "video_prompt": "...",
  "enable_idle_stream": true,
  "enable_telegram": false,
  "telegram_bot_token": "",
  "telegram_user_id": "",
  "telegram_message_prompt": "Based on the patrol inspection results below...",
  "enable_edge_ai": false,
  "edge_ai_rules": ["Is there a person?", "Is there fire?"],
  "jetson_host": "192.168.50.35",
  "enable_robot_camera_relay": false,
  "enable_external_rtsp": false,
  "external_rtsp_url": ""
}
```

### GET `/api/robots`

回傳所有已註冊的機器人。

**回應：**
```json
[
  {
    "robot_id": "robot-a",
    "robot_name": "Robot A",
    "robot_ip": "192.168.50.133:26400",
    "last_seen": "2026-02-06 14:30:00",
    "status": "online"
  }
]
```

機器人狀態根據 Kachaka gRPC 連線健康度判定，由心跳執行緒每 30 秒更新。

### GET `/api/history`

回傳所有巡檢記錄，最新在前。

**查詢參數：**
- `robot_id`：選填。依機器人篩選。

**回應：**
```json
[
  {
    "id": 42,
    "start_time": "2026-02-06 14:00:00",
    "end_time": "2026-02-06 14:15:00",
    "status": "Completed",
    "robot_serial": "KAC-001",
    "report_content": "All points inspected...",
    "model_id": "gemini-3-flash-preview",
    "total_tokens": 1234,
    "video_path": "video/42_20260206_140000.mp4",
    "robot_id": "robot-a"
  }
]
```

`video_path` 欄位：若有錄影則為路徑字串，否則為 `null`。前端可據此顯示影片圖示。

### GET `/api/history/{run_id}`

回傳巡檢詳細資料，含所有檢查結果及即時警報。

**回應：**
```json
{
  "run": { "id": 42, "start_time": "...", "status": "Completed", "video_path": "...", "..." : "..." },
  "inspections": [
    {
      "id": 100,
      "run_id": 42,
      "point_name": "Lobby",
      "coordinate_x": 1.5,
      "coordinate_y": 2.0,
      "prompt": "Is everything normal?",
      "ai_response": "{\"is_NG\": false, \"Description\": \"Normal\"}",
      "is_ng": 0,
      "ai_description": "Normal",
      "image_path": "42_20260206_140000/Lobby_OK_uuid.jpg",
      "timestamp": "2026-02-06 14:02:00",
      "robot_id": "robot-a"
    }
  ],
  "edge_ai_alerts": [
    {
      "id": 1,
      "run_id": 42,
      "rule": "Is there a person lying on the floor?",
      "response": "triggered",
      "image_path": "report/edge_ai_alerts/42_1707200000_Is_there_a_person_lying_on_the_floor_.jpg",
      "timestamp": "2026-02-06 14:05:00",
      "robot_id": "robot-a",
      "stream_source": "robot_camera"
    }
  ]
}
```

### GET `/api/report/{run_id}/pdf`

產生並下載單次巡檢的 PDF 報告。

**回應：** `application/pdf` 檔案下載。

### POST `/api/reports/generate`

產生日期範圍內的 AI 分析報告。

**請求：**
```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-06",
  "prompt": "Summarize trends and anomalies",
  "robot_id": "robot-a"
}
```

- `start_date`, `end_date`：必填。
- `prompt`：選填。未提供時使用已設定的預設值。
- `robot_id`：選填。依機器人篩選。

**回應：**
```json
{
  "id": 5,
  "report": "## Summary Report\n\n...",
  "usage": {
    "prompt_token_count": 2000,
    "candidates_token_count": 500,
    "total_token_count": 2500
  }
}
```

### GET `/api/reports/generate/pdf`

下載最近一次產生的分析報告 PDF。

**查詢參數：**
- `start_date`：必填。
- `end_date`：必填。

### GET `/api/stats/token_usage`

回傳從巡檢記錄及產生的報告彙總的每日 token 使用量。

**查詢參數：**
- `robot_id`：選填。依機器人篩選。

**回應：**
```json
[
  { "date": "2026-02-05", "input": 1000, "output": 200, "total": 1200 },
  { "date": "2026-02-06", "input": 500, "output": 100, "total": 600 }
]
```

---

## 圖片服務 (機器人專屬)

### GET `/api/{id}/images/{filename}`

提供巡檢圖片。優先從機器人的圖片目錄取得，若不存在則回退至舊版目錄。

### GET `/api/robots/{robot_id}/images/{filename}`

從特定機器人的目錄提供圖片。用於歷史記錄視圖中，瀏覽的機器人可能與圖片來源機器人不同的情境。
