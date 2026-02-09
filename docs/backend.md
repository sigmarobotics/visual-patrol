# Backend Documentation

## Overview

The backend is a Python Flask application that provides the REST API, manages robot communication via gRPC, orchestrates patrol missions, runs AI inference, pushes RTSP camera streams, manages camera relay transcoding, performs live monitoring via VILA JPS, and generates PDF reports.

## File Structure

```
src/backend/
├── app.py               # Flask application, route definitions, startup
├── config.py            # Environment variables, paths, defaults
├── database.py          # SQLite schema, migrations, DB helpers
├── settings_service.py  # Global settings CRUD (wraps DB table)
├── robot_service.py     # Kachaka robot gRPC interface
├── frame_hub.py         # Centralized camera frame cache + ffmpeg RTSP push
├── patrol_service.py    # Patrol orchestration, scheduling
├── cloud_ai_service.py  # Google Gemini AI integration
├── edge_ai_service.py   # VILA JPS live monitoring (WebSocket alerts)
├── relay_manager.py     # HTTP client for Jetson-side relay service
├── pdf_service.py       # PDF report generation (ReportLab)
├── video_recorder.py    # Video recording during patrols (OpenCV)
├── utils.py             # JSON I/O, timezone helpers
├── logger.py            # Timezone-aware logging setup
└── requirements.txt     # Python dependencies
```

## Service Architecture

Services are instantiated as module-level singletons. Import order matters because services read settings from the database at module load time.

```
config.py           -- Loaded first (env vars, paths)
    |
database.py         -- Schema init (init_db called before service imports)
    |
settings_service.py -- Reads global_settings table
    |
robot_service.py    -- Connects to Kachaka (reads ROBOT_IP from env)
frame_hub.py        -- gRPC polling + frame cache + RTSP push (depends on robot_service, settings_service)
cloud_ai_service.py -- Configures Gemini client (reads API key from settings)
relay_manager.py    -- Relay service client (HTTP client to Jetson relay service)
patrol_service.py   -- Imports robot_service, frame_hub, cloud_ai_service, relay_manager, edge_ai_service
edge_ai_service.py  -- Used by patrol_service (VILA JPS API + WebSocket)
pdf_service.py      -- Reads from database for report data
video_recorder.py   -- Used by patrol_service (frame source from frame_hub)
utils.py            -- Used by patrol_service, app.py
logger.py           -- Used by cloud_ai_service, patrol_service, video_recorder, relay_manager, edge_ai_service, frame_hub
```

## Modules

### `config.py`

Reads environment variables and defines filesystem paths.

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOT_ID` | `"default"` | Unique robot identifier |
| `ROBOT_NAME` | `"Robot"` | Display name |
| `ROBOT_IP` | `"192.168.50.133:26400"` | Kachaka gRPC address |
| `DATA_DIR` | `{project}/data` | Shared data directory |
| `LOG_DIR` | `{project}/logs` | Log file directory |
| `PORT` | `5000` | Flask listen port |
| `TZ` | (system) | System timezone (Docker) |
| `RELAY_SERVICE_URL` | `""` | Jetson relay service URL (empty = relay not available) |

**Constants (not environment variables):**

| Constant | Value | Description |
|----------|-------|-------------|
| `JETSON_JPS_API_PORT` | `5010` | VILA JPS API port (fixed, co-located on Jetson) |
| `JETSON_MEDIAMTX_PORT` | `8555` | mediamtx RTSP port (fixed, co-located on Jetson) |

**Derived Paths:**

| Path | Value | Description |
|------|-------|-------------|
| `REPORT_DIR` | `{DATA_DIR}/report` | Shared report directory |
| `DB_FILE` | `{REPORT_DIR}/report.db` | SQLite database |
| `ROBOT_DATA_DIR` | `{DATA_DIR}/{ROBOT_ID}` | Per-robot data |
| `ROBOT_CONFIG_DIR` | `{ROBOT_DATA_DIR}/config` | Per-robot config |
| `ROBOT_IMAGES_DIR` | `{ROBOT_DATA_DIR}/report/images` | Per-robot images |
| `POINTS_FILE` | `{ROBOT_CONFIG_DIR}/points.json` | Waypoints file |
| `SCHEDULE_FILE` | `{ROBOT_CONFIG_DIR}/patrol_schedule.json` | Schedule file |

**Evidence path:**

| Path | Value | Description |
|------|-------|-------------|
| `edge_ai_alerts` dir | `{ROBOT_DATA_DIR}/report/edge_ai_alerts` | Live monitor evidence images (created at runtime) |

Also defines `DEFAULT_SETTINGS` dict with default values for all global settings, and `ensure_dirs()` / `migrate_legacy_files()` functions.

### `database.py`

SQLite database management with schema initialization and migrations.

**Connection settings:**
- WAL journal mode for concurrent access
- 5000ms busy timeout
- Row factory for dict-like access

**Context manager:**
```python
with db_context() as (conn, cursor):
    cursor.execute("SELECT ...")
    # Auto-commits on success, rolls back on error
```

#### Database Schema

**`patrol_runs`** -- One row per patrol mission

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `start_time` | TEXT | Patrol start timestamp |
| `end_time` | TEXT | Patrol end timestamp |
| `status` | TEXT | `Running`, `Completed`, `Patrol Stopped` |
| `robot_serial` | TEXT | Kachaka serial number |
| `report_content` | TEXT | AI-generated summary report (Markdown) |
| `model_id` | TEXT | Gemini model name |
| `token_usage` | TEXT | JSON string of token usage |
| `input_tokens` | INTEGER | Aggregated input tokens (grand total across all categories) |
| `output_tokens` | INTEGER | Aggregated output tokens |
| `total_tokens` | INTEGER | Aggregated total tokens |
| `video_path` | TEXT | Path to recorded video |
| `video_analysis` | TEXT | AI video analysis result |
| `robot_id` | TEXT | Robot identifier |
| `report_input_tokens` | INTEGER | Report generation input tokens |
| `report_output_tokens` | INTEGER | Report generation output tokens |
| `report_total_tokens` | INTEGER | Report generation total tokens |
| `telegram_input_tokens` | INTEGER | Telegram message input tokens |
| `telegram_output_tokens` | INTEGER | Telegram message output tokens |
| `telegram_total_tokens` | INTEGER | Telegram message total tokens |
| `video_input_tokens` | INTEGER | Video analysis input tokens |
| `video_output_tokens` | INTEGER | Video analysis output tokens |
| `video_total_tokens` | INTEGER | Video analysis total tokens |

**`inspection_results`** -- One row per waypoint inspection

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `run_id` | INTEGER FK | References `patrol_runs.id` |
| `point_name` | TEXT | Waypoint name |
| `coordinate_x` | REAL | World X coordinate |
| `coordinate_y` | REAL | World Y coordinate |
| `prompt` | TEXT | AI prompt used |
| `ai_response` | TEXT | Raw AI response (JSON or text) |
| `is_ng` | INTEGER | 1 if abnormal, 0 if normal |
| `ai_description` | TEXT | Parsed description |
| `token_usage` | TEXT | JSON string of token usage |
| `input_tokens` | INTEGER | Input tokens |
| `output_tokens` | INTEGER | Output tokens |
| `total_tokens` | INTEGER | Total tokens |
| `image_path` | TEXT | Relative path to inspection image |
| `timestamp` | TEXT | Inspection timestamp |
| `robot_moving_status` | TEXT | Movement result (`Success`, `Error: ...`) |
| `robot_id` | TEXT | Robot identifier |

**`generated_reports`** -- AI-generated multi-day analysis reports

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `start_date` | TEXT | Report period start |
| `end_date` | TEXT | Report period end |
| `report_content` | TEXT | AI report content (Markdown) |
| `input_tokens` | INTEGER | Input tokens |
| `output_tokens` | INTEGER | Output tokens |
| `total_tokens` | INTEGER | Total tokens |
| `timestamp` | TEXT | Generation timestamp |
| `robot_id` | TEXT | Robot filter used |

**`robots`** -- Registered robot instances

| Column | Type | Description |
|--------|------|-------------|
| `robot_id` | TEXT PK | Unique identifier |
| `robot_name` | TEXT | Display name |
| `robot_ip` | TEXT | gRPC address |
| `last_seen` | TEXT | Last heartbeat time |
| `status` | TEXT | `online` or `offline` |

**`edge_ai_alerts`** -- Live monitor alerts triggered during patrol

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `run_id` | INTEGER FK | References `patrol_runs.id` |
| `rule` | TEXT | Alert rule that triggered |
| `response` | TEXT | `"triggered"` (JPS WebSocket event) |
| `image_path` | TEXT | Relative path to evidence image |
| `timestamp` | TEXT | Alert timestamp |
| `robot_id` | TEXT | Robot identifier |
| `stream_source` | TEXT | Stream type: `"robot_camera"`, `"external_rtsp"`, or `"unknown"` |

**`global_settings`** -- Key-value settings store

| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT PK | Setting name |
| `value` | TEXT | JSON-encoded value |

**Schema Migrations:**

The `_run_migrations()` function adds columns to existing tables for backward compatibility. It checks if a column exists by attempting a SELECT, and adds missing columns via ALTER TABLE if the check fails. Additional migration functions handle renaming token columns (`prompt_tokens` to `input_tokens`, `candidate_tokens` to `output_tokens`), adding per-category token columns to `patrol_runs`, and renaming the legacy `live_alerts` table to `edge_ai_alerts`.

### `settings_service.py`

Thin wrapper around `database.get_global_settings()` and `database.save_global_settings()`.

- `get_all()` -- Returns settings merged with `DEFAULT_SETTINGS`
- `get(key, default)` -- Get single setting
- `save(dict)` -- UPSERT all key-value pairs
- `migrate_from_json(path)` -- One-time import from legacy `settings.json`

### `robot_service.py`

Manages the gRPC connection to a Kachaka robot.

**Singleton:** `robot_service = RobotService()`

**Key methods:**

| Method | Description |
|--------|-------------|
| `connect()` | Establish gRPC connection to `ROBOT_IP` |
| `get_client()` | Returns gRPC client (or `None` if disconnected) |
| `get_state()` | Returns `{battery, pose, map_info}` |
| `get_map_bytes()` | Returns PNG map as bytes |
| `move_to(x, y, theta)` | Move robot to pose |
| `move_forward(distance, speed)` | Move forward/backward |
| `rotate(angle)` | Rotate in place |
| `return_home()` | Return to charging station |
| `cancel_command()` | Cancel current command |
| `get_front_camera_image()` | Get front camera JPEG (also used as frame_hub frame source) |
| `get_back_camera_image()` | Get back camera JPEG |
| `get_serial()` | Get robot serial number |
| `get_locations()` | Get saved locations from robot |

**Thread safety:** Uses `client_lock` for gRPC client access and `state_lock` for state reads/writes.

**Auto-reconnect:** The polling loop resets `self.client = None` on persistent errors, triggering reconnection on the next poll cycle.

### `frame_hub.py`

Centralized camera frame management. A single gRPC polling thread feeds an in-memory frame cache. All local consumers -- frontend MJPEG, Gemini inspection, video recording, evidence capture -- read from the cache instead of making independent gRPC calls. An on-demand ffmpeg subprocess pushes frames from the cache to Jetson mediamtx as RTSP for Edge AI relay.

**Singleton:** `frame_hub = FrameHub(robot_service.get_front_camera_image)`

At module load time, reads `enable_idle_stream` from settings and calls `on_idle_stream_changed()` to initialize polling state.

**Polling lifecycle:** Controlled by patrol state + `enable_idle_stream` setting:
- Patrolling: always polling
- Idle + `enable_idle_stream=true`: polling (frontend shows live feed)
- Idle + `enable_idle_stream=false`: NOT polling (zero gRPC bandwidth)

**Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `POLL_INTERVAL` | `0.1` | ~10fps gRPC polling |
| `FEEDER_INTERVAL` | `0.5` | 2fps for ffmpeg push |
| `PUSH_MONITOR_INTERVAL` | `5.0` | Check ffmpeg health every 5s |

**Key methods:**

| Method | Description |
|--------|-------------|
| `start_polling()` | Start gRPC polling, updating frame cache at ~10fps. Idempotent. |
| `stop_polling()` | Stop gRPC polling. Frame cache set to None. Idempotent. |
| `set_patrol_active(active)` | Called by patrol_service when patrol starts/stops. |
| `on_idle_stream_changed(enabled)` | Called when `enable_idle_stream` setting changes. |
| `get_latest_frame()` | Return latest cached gRPC image response (has `.data` attribute). |
| `start_rtsp_push(target_mediamtx, rtsp_path)` | Start ffmpeg pushing frames to Jetson mediamtx as RTSP. |
| `stop_rtsp_push()` | Stop ffmpeg RTSP push. Does not stop polling (controlled by `_evaluate`). |
| `wait_for_push_ready(timeout)` | Wait until at least one frame has been fed to ffmpeg. |

**RTSP push pipeline:** When `start_rtsp_push()` is called, an ffmpeg subprocess is spawned with `image2pipe` input and `libx264` baseline profile output. A feeder thread reads JPEG frames from the cache at 2fps and writes them to ffmpeg stdin. A monitor thread auto-restarts ffmpeg if it dies. The ffmpeg command scales frames to 1280x720, uses `ultrafast` preset, `zerolatency` tune, and outputs via RTSP/TCP to the target mediamtx.

### `cloud_ai_service.py`

AI integration for visual inspection and report generation. Uses Google Gemini as the VLM provider.

**Singleton:** `ai_service = AIService()`

Uses the `google-genai` SDK (not the deprecated `google-generativeai`).

**Key methods:**

| Method | Description |
|--------|-------------|
| `generate_inspection(image, prompt, sys_prompt)` | Analyze image with structured JSON response |
| `generate_report(prompt)` | Generate text report from patrol data |
| `analyze_video(path, prompt)` | Analyze patrol video |
| `is_configured()` | Check if API key is set |
| `get_model_name()` | Get current model name |

**Structured output:** `generate_inspection()` uses a Pydantic `InspectionResult` schema to enforce JSON response format:
```python
class InspectionResult(BaseModel):
    is_NG: bool   # True if abnormal
    Description: str
```

**Auto-reconfigure:** Each method call runs `_configure()` which checks if settings have changed and reconfigures the client if needed.

**`parse_ai_response()`** is a standalone utility function that normalizes AI responses into a standard dict format used by patrol_service.

### `relay_manager.py`

HTTP client for the Jetson-side relay service. Delegates all relay operations (ffmpeg subprocess management, RTSP transcode) to the remote Jetson relay_service.py via REST API. All relays are unified: RTSP input -> transcode -> RTSP output.

**Module-level:** `relay_service_client = RelayServiceClient(URL) if RELAY_SERVICE_URL else None`

When `RELAY_SERVICE_URL` is not set, `relay_service_client` is `None` and relay functionality is unavailable.

**`RelayServiceClient` methods:**

| Method | Description |
|--------|-------------|
| `is_available()` | Check if the relay service is reachable (GET /health) |
| `start_relay(key, source_url)` | Start a relay on the service (POST /relays) |
| `stop_relay(key)` | Stop a specific relay (DELETE /relays/{key}) |
| `stop_all()` | Stop all active relays (POST /relays/stop_all) |
| `wait_for_stream(key, timeout)` | Blocking readiness check on the relay service (GET /relays/{key}/ready) |
| `get_status()` | Get status of all relays from the service (GET /relays) |

### `edge_ai_service.py`

Background camera monitoring via VILA JPS during patrol, plus a test monitor for the settings page.

**Singletons:** `edge_ai_monitor = LiveMonitor()`, `test_edge_ai = TestLiveMonitor()`

#### VILA JPS API Integration (LiveMonitor)

The primary `LiveMonitor` class uses the VILA JPS Stream API + Alert API + WebSocket for efficient continuous monitoring. VILA handles continuous frame capture and rule evaluation internally -- the backend only receives WebSocket events when alerts trigger.

**Lifecycle:**

1. **Cleanup stale streams**: `GET /api/v1/live-stream` + `DELETE` each existing stream
2. **Register streams**: `POST /api/v1/live-stream` with `{liveStreamUrl, name}` for each stream (up to 3 retries)
3. **Set alert rules**: `POST /api/v1/alerts` with `{alerts, id}` per stream
4. **WebSocket listener**: Connects to `ws://{host}:5016/api/v1/alerts/ws`, listens for alert events
5. **On alert event**: Cooldown check -> capture evidence frame -> save to DB + disk -> send Telegram
6. **Stop**: Close WebSocket -> `DELETE /api/v1/live-stream/{id}` per stream

**Key methods:**

| Method | Description |
|--------|-------------|
| `start(run_id, config)` | Start monitoring with VILA JPS config dict |
| `stop()` | Stop monitoring, deregister streams |
| `get_alerts()` | Return collected alerts list |

**Config dict:**
```python
{
    "vila_jps_url": "http://192.168.50.35:5010",
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

**Evidence capture:** Robot camera alerts use `evidence_func()` (reads from frame_hub cache) for best quality. External RTSP alerts use `cv2.VideoCapture()` to grab a frame from the relay URL.

**WebSocket reconnection:** On disconnect, retries with 5s delay, up to 10 reconnection attempts. Reconnect counter resets on successful connection.

**Constraints:** Maximum 10 alert rules per stream (VILA JPS limit). Per-rule cooldown of 60 seconds prevents duplicate alerts (defense-in-depth alongside VILA's own cooldown).

#### Test Monitor (TestLiveMonitor)

The `TestLiveMonitor` provides a settings-page test using the same VILA JPS flow -- relay -> mediamtx -> JPS stream registration -> alert rules -> WebSocket alerts. No DB writes; alerts are kept in memory only.

**Test flow:**

1. **Validate source**: Check frame_hub cache (robot camera) or external RTSP URL availability
2. **Start stream**: Robot camera uses `frame_hub.start_rtsp_push()` directly; external RTSP uses `relay_service_client.start_relay()`
3. **Wait for stream**: `frame_hub.wait_for_push_ready()` or `relay_service_client.wait_for_stream()`
4. **Start snapshot thread**: Captures frames from mediamtx RTSP for live preview
5. **Cleanup stale JPS streams**: Remove any existing JPS registrations
6. **Register stream with JPS**: `POST /api/v1/live-stream` with retries (up to 5 attempts)
7. **Set alert rules**: `POST /api/v1/alerts`
8. **Start WebSocket listener**: Same as LiveMonitor but without DB writes
9. **On stop**: Close WebSocket -> deregister JPS stream -> stop relay/push -> release snapshot capture

**Key methods:**

| Method | Description |
|--------|-------------|
| `start(config)` | Start test session (relay -> JPS -> WebSocket) |
| `stop()` | Stop test session, clean up all resources |
| `get_status()` | Return `{active, ws_connected, alert_count, alerts, ws_messages, error}` |
| `get_snapshot()` | Return latest JPEG bytes captured from mediamtx |

### `patrol_service.py`

Orchestrates autonomous patrol missions.

**Singleton:** `patrol_service = PatrolService()`

**Patrol flow:**

1. Set `frame_hub.set_patrol_active(True)` -- ensures polling is running
2. Load enabled waypoints from `points.json`
3. Validate AI is configured
4. Create `patrol_runs` DB record
5. Optionally start video recording (uses `frame_hub.get_latest_frame` as frame source)
6. Start Edge AI setup:
   a. Derive JPS URL and mediamtx address from `jetson_host` setting
   b. If robot camera relay enabled: `frame_hub.start_rtsp_push()` to `{jetson_host}:{JETSON_MEDIAMTX_PORT}`
   c. If external RTSP enabled: `relay_service_client.start_relay()` for transcode
   d. Wait for streams to be live on mediamtx (`frame_hub.wait_for_push_ready()` / `relay_service_client.wait_for_stream()`)
   e. Start `edge_ai_monitor.start()` with JPS config (stream registration + alert rules + WebSocket)
7. For each waypoint:
   a. Move robot to point (`_move_to_point`)
   b. Wait 2 seconds for stability
   c. Capture front camera image from `frame_hub.get_latest_frame()`
   d. Run AI inspection (sync or async via turbo mode)
   e. Save result to `inspection_results` table
8. Return home (edge AI continues monitoring during return)
9. Wait for async queue (turbo mode)
10. Cleanup (always runs in `finally` block):
    a. `edge_ai_monitor.stop()` -- close WebSocket, deregister JPS streams
    b. `frame_hub.stop_rtsp_push()` -- stop ffmpeg push
    c. `relay_service_client.stop_all()` -- stop all relay transcodes
    d. Stop video recorder if running
    e. `frame_hub.set_patrol_active(False)` -- re-evaluate polling
11. Optionally analyze video
12. Generate AI summary report (includes live monitor alerts if any)
13. Generate AI-summarized Telegram message and send notification (if enabled)
14. Update run status and aggregated token totals

**Turbo mode:** When enabled, images are queued for AI analysis while the robot continues moving to the next waypoint. The `_inspection_worker` thread processes the queue in the background.

**Schedule checker:** A background thread runs every 30 seconds, comparing the current time against enabled schedules. Each schedule can only trigger once per day (tracked by `trigger_key`).

**Image naming:** Images are saved as `{point_name}_processing_{uuid}.jpg` during capture, then renamed to `{point_name}_{OK|NG}_{uuid}.jpg` after AI analysis.

### `pdf_service.py`

Server-side PDF generation using ReportLab.

**Key functions:**

| Function | Description |
|----------|-------------|
| `generate_patrol_report(run_id)` | Single patrol run PDF |
| `generate_analysis_report(content, start, end)` | Multi-day analysis PDF |

**Features:**
- CJK font support (`STSong-Light` for Chinese characters)
- Markdown-to-PDF conversion (headers, bold, italic, code blocks, tables, lists, blockquotes)
- Inspection images embedded in PDF
- OK/NG color coding (green/red)
- Page numbers and footer

### `video_recorder.py`

Records patrol video using OpenCV.

- Tries codecs in order: H.264 (`avc1`), XVID, MJPEG
- Captures frames from `frame_hub.get_latest_frame()` at configured FPS (default 5)
- Resizes frames to 640x480
- Runs in a background thread

### `utils.py`

Shared utility functions:

- `load_json(path, default)` -- Safe JSON file loading with fallback
- `save_json(path, data)` -- Atomic JSON save (temp file + rename)
- `get_current_time_str()` -- Timezone-aware timestamp string
- `get_current_datetime()` -- Timezone-aware datetime object
- `get_filename_timestamp()` -- Timestamp for filenames (`YYYYMMDD_HHMMSS`)

### `logger.py`

Logging configuration with timezone support.

- `TimezoneFormatter` -- Custom formatter using configured timezone
- `get_logger(name, file)` -- Creates logger with file + console handlers
- Log files are prefixed with robot ID (e.g., `robot-a_app.log`)
- Flask/Werkzeug request logging is suppressed (`logging.ERROR` level)

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | >=3.0, <4.0 | Web framework |
| `kachaka-api` | >=3.14, <4.0 | Kachaka robot gRPC client |
| `numpy` | >=2.2, <3.0 | Array operations (video frames) |
| `pillow` | >=10.0, <11.0 | Image processing |
| `google-genai` | >=1.0, <2.0 | Google Gemini AI SDK |
| `reportlab` | >=4.0, <5.0 | PDF generation |
| `opencv-python-headless` | >=4.9, <5.0 | Video recording, RTSP evidence capture |
| `requests` | >=2.31, <3.0 | HTTP API calls (Telegram, VILA JPS, relay service) |
| `websocket-client` | >=1.6, <2.0 | VILA JPS WebSocket connection |

## Startup Sequence (`app.py`)

1. Import `config` (reads env vars)
2. Call `ensure_dirs()` (create data directories)
3. Call `init_db()` (create/migrate DB schema)
4. Import services (they read DB at module level):
   - `settings_service`
   - `robot_service` (starts gRPC polling thread)
   - `frame_hub` (initializes polling based on `enable_idle_stream` setting)
   - `patrol_service` (starts inspection worker + schedule checker threads)
   - `cloud_ai_service`, `edge_ai_service`, `relay_manager`
   - `pdf_service`
5. Create Flask app
6. Configure logging
7. Register routes
8. **On `__main__`:**
   a. `init_db()` again (idempotent)
   b. `migrate_from_json()` (legacy settings migration)
   c. `migrate_legacy_files()` (legacy per-robot file migration)
   d. `register_robot()` (register this instance in DB)
   e. `backfill_robot_id()` (set robot_id on NULL rows)
   f. Start heartbeat thread
   g. `app.run()` on configured port
