# API Reference

## URL Convention

All API endpoints are prefixed with `/api/`.

- **Robot-specific**: `/api/{robot-id}/endpoint` -- nginx strips the `{robot-id}` prefix before proxying to the matching backend. The backend sees `/api/endpoint`.
- **Global**: `/api/endpoint` -- proxied to any backend (all share the same database).

`{robot-id}` must match the pattern `robot-[a-z0-9-]+` (e.g., `robot-a`, `robot-b`).

---

## Robot Control (robot-specific)

### GET `/api/{id}/state`

Returns the robot's current status.

**Response:**
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

Returns robot identity only.

**Response:**
```json
{
  "robot_id": "robot-a",
  "robot_name": "Robot A"
}
```

### GET `/api/{id}/map`

Returns the robot's PNG map image.

**Response:** `image/png` binary data or `404` if map not available.

### POST `/api/{id}/move`

Move the robot to a target pose.

**Request:**
```json
{
  "x": 1.5,
  "y": 2.0,
  "theta": 0.0
}
```

- `x`, `y`: Required (float). World coordinates.
- `theta`: Optional (float, default `0.0`). Orientation in radians, must be between -2pi and 2pi.

**Response:**
```json
{ "status": "Moving", "target": { "x": 1.5, "y": 2.0, "theta": 0.0 } }
```

**Errors:** `400` (missing/invalid params), `503` (robot disconnected).

### POST `/api/{id}/manual_control`

Send a manual D-pad control command.

**Request:**
```json
{ "action": "forward" }
```

Valid actions: `forward` (0.1m), `backward` (-0.1m), `left` (+10 deg), `right` (-10 deg).

**Response:**
```json
{ "status": "Command sent", "action": "forward" }
```

### POST `/api/{id}/return_home`

Command the robot to return to its charging station.

**Response:**
```json
{ "status": "Returning home" }
```

### POST `/api/{id}/cancel_command`

Cancel the robot's current movement command.

**Response:**
```json
{ "status": "Command cancelled" }
```

### GET `/api/{id}/camera/front`

Returns an MJPEG stream from the robot's front camera.

**Response:** `multipart/x-mixed-replace; boundary=frame` (continuous JPEG stream at ~20fps).

### GET `/api/{id}/camera/back`

Returns an MJPEG stream from the robot's back camera.

**Response:** Same format as front camera.

---

## AI Test (robot-specific)

### POST `/api/{id}/test_ai`

Capture an image from the front camera and run AI analysis.

**Request:**
```json
{
  "prompt": "Is there a fire hazard?"
}
```

- `prompt`: Optional. Defaults to `"Describe what you see and check if everything is normal."`

**Response:**
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

**Errors:** `503` (camera unavailable), `500` (AI error).

---

## Test Edge AI (robot-specific)

Uses VILA JPS streaming pipeline (relay → mediamtx → JPS → WebSocket alerts) for quick testing from the settings page.

### POST `/api/{id}/test_edge_ai/start`

Start a test live monitor session. Starts relay, registers stream with VILA JPS, sets alert rules, and listens for WebSocket alerts.

**Request:**
```json
{
  "jetson_host": "192.168.50.35",
  "rules": ["Is there a person?", "Is there fire?"],
  "stream_source": "robot_camera",
  "external_rtsp_url": ""
}
```

All fields are optional -- falls back to saved settings if omitted.

**Response:**
```json
{ "status": "started" }
```

**Errors:** `400` (missing URL or rules), `409` (test already running).

### POST `/api/{id}/test_edge_ai/stop`

Stop the running test session.

**Response:**
```json
{ "status": "stopped" }
```

### GET `/api/{id}/test_edge_ai/status`

Returns the current test session state and results.

**Response:**
```json
{
  "active": true,
  "check_count": 3,
  "error": null,
  "results": [
    {
      "check_id": 1,
      "timestamp": "2026-02-06 23:05:58",
      "responses": [
        { "rule": "Is there a person?", "answer": "no" },
        { "rule": "Is there fire?", "answer": "no" }
      ]
    }
  ]
}
```

---

## Patrol Management (robot-specific)

### GET `/api/{id}/patrol/status`

Returns the current patrol status.

**Response:**
```json
{
  "is_patrolling": true,
  "status": "Moving to Point 1...",
  "current_index": 0
}
```

### POST `/api/{id}/patrol/start`

Start a patrol run. The robot will visit all enabled waypoints sequentially.

**Response:**
```json
{ "status": "started" }
```

**Errors:** `400` if already patrolling.

### POST `/api/{id}/patrol/stop`

Stop the current patrol. The robot cancels its current command and returns home.

**Response:**
```json
{ "status": "stopping" }
```

### GET `/api/{id}/patrol/edge_ai_alerts`

Returns live monitor alerts for the currently active patrol run. Returns empty list if no patrol is active or live monitor is not enabled.

**Response:**
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

Results are ordered newest first (`ORDER BY id DESC`).

### GET `/api/{id}/patrol/results`

Returns inspection results for the currently active patrol run only. Returns empty list if no patrol is active.

**Response:**
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

Returns all scheduled patrols for this robot.

**Response:**
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

Days: `0` = Monday through `6` = Sunday.

### POST `/api/{id}/patrol/schedule`

Add a new scheduled patrol.

**Request:**
```json
{
  "time": "08:00",
  "days": [0, 1, 2, 3, 4],
  "enabled": true
}
```

- `time`: Required. Format `HH:MM`.
- `days`: Optional. List of integers 0-6. Defaults to every day.
- `enabled`: Optional. Default `true`.

**Response:**
```json
{
  "status": "added",
  "schedule": { "id": "a1b2c3d4", "time": "08:00", "days": [0,1,2,3,4], "enabled": true }
}
```

### PUT `/api/{id}/patrol/schedule/{schedule_id}`

Update a scheduled patrol.

**Request:**
```json
{
  "time": "09:00",
  "days": [0, 1, 2, 3, 4, 5],
  "enabled": false
}
```

All fields are optional.

### DELETE `/api/{id}/patrol/schedule/{schedule_id}`

Delete a scheduled patrol.

**Response:**
```json
{ "status": "deleted" }
```

---

## Points / Waypoints (robot-specific)

### GET `/api/{id}/points`

Returns all patrol waypoints for this robot.

**Response:**
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

Add or update a patrol waypoint.

**Request:**
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

- `name`, `x`, `y`: Required.
- `id`: If provided and matches an existing point, updates it. Otherwise creates a new point with auto-generated ID.

### DELETE `/api/{id}/points?id={point_id}`

Delete a patrol waypoint by ID.

### POST `/api/{id}/points/reorder`

Replace the entire points list (used for drag-and-drop reordering).

**Request:** Array of point objects (same format as GET response).

### GET `/api/{id}/points/export`

Download all points as a JSON file.

**Response:** `application/json` file download (`patrol_points.json`).

### POST `/api/{id}/points/import`

Upload a JSON file to replace all points.

**Request:** Multipart form with `file` field containing a JSON file.

**Response:**
```json
{ "status": "imported", "count": 5 }
```

### GET `/api/{id}/points/from_robot`

Fetch saved locations from the Kachaka robot and merge with existing points. Skips duplicates (same name and coordinates).

**Response:**
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

## Infrastructure (global)

### GET `/api/relay/status`

Returns the status of all active RTSP relay processes.

**Response:**
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

Returns `{}` when no relays are active (no patrol running with relay sources enabled).

### POST `/api/relay/test`

Quick-test the robot camera relay. Starts a relay, waits 3 seconds, checks status, then stops.

**Response (success):**
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

**Response (failure):**
```json
{
  "status": "error",
  "error": "Camera not available"
}
```

### GET `/api/edge_ai/health`

Check VILA JPS API health by deriving the JPS URL from `jetson_host` and calling `GET http://{jetson_host}:5010/api/v1/health/ready`.

**Response (healthy):**
```json
{
  "status": "ok",
  "code": 200
}
```

**Response (unhealthy):**
```json
{
  "status": "error",
  "error": "Connection refused"
}
```

**Errors:** `400` if `jetson_host` is not configured.

---

## Global Endpoints

### GET/POST `/api/settings`

**GET** returns all settings. Sensitive fields (`gemini_api_key`, `telegram_bot_token`, `telegram_user_id`) are masked with `****` prefix.

**POST** saves settings. Masked values (starting with `****`) are ignored to prevent overwriting real values.

**Response (GET):**
```json
{
  "gemini_api_key": "****abcd",
  "gemini_model": "gemini-2.0-flash",
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

Returns all registered robots.

**Response:**
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

Robot status is based on Kachaka gRPC connection health, updated every 30 seconds by the heartbeat thread.

### GET `/api/history`

Returns all patrol runs, newest first.

**Query params:**
- `robot_id`: Optional. Filter by robot.

**Response:**
```json
[
  {
    "id": 42,
    "start_time": "2026-02-06 14:00:00",
    "end_time": "2026-02-06 14:15:00",
    "status": "Completed",
    "robot_serial": "KAC-001",
    "report_content": "All points inspected...",
    "model_id": "gemini-2.0-flash",
    "total_tokens": 1234,
    "robot_id": "robot-a"
  }
]
```

### GET `/api/history/{run_id}`

Returns detailed patrol run info with all inspection results and live alerts.

**Response:**
```json
{
  "run": { "id": 42, "start_time": "...", "status": "Completed", "..." : "..." },
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

Generate and download a PDF report for a single patrol run.

**Response:** `application/pdf` file download.

### POST `/api/reports/generate`

Generate an AI-powered analysis report for a date range.

**Request:**
```json
{
  "start_date": "2026-02-01",
  "end_date": "2026-02-06",
  "prompt": "Summarize trends and anomalies",
  "robot_id": "robot-a"
}
```

- `start_date`, `end_date`: Required.
- `prompt`: Optional. Uses configured default if not provided.
- `robot_id`: Optional. Filter by robot.

**Response:**
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

Download the most recently generated analysis report as PDF.

**Query params:**
- `start_date`: Required.
- `end_date`: Required.

### GET `/api/stats/token_usage`

Returns daily token usage aggregated from patrol runs and generated reports.

**Query params:**
- `robot_id`: Optional. Filter by robot.

**Response:**
```json
[
  { "date": "2026-02-05", "input": 1000, "output": 200, "total": 1200 },
  { "date": "2026-02-06", "input": 500, "output": 100, "total": 600 }
]
```

---

## Image Serving (robot-specific)

### GET `/api/{id}/images/{filename}`

Serves inspection images. Tries the robot's image directory first, then falls back to the legacy directory.

### GET `/api/robots/{robot_id}/images/{filename}`

Serves images from a specific robot's directory. Used in history views where the viewing robot may differ from the image source robot.
