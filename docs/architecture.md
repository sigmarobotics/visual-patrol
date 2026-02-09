# System Architecture

## Overview

Visual Patrol is a multi-robot autonomous inspection system. A web-based single-page application (SPA) connects through an nginx reverse proxy to per-robot Flask backend instances, all sharing a common SQLite database. An RTSP relay layer on the Jetson host -- consisting of a relay service (`relay_service.py`), mediamtx, and VILA JPS -- enables continuous camera streaming and real-time VLM-based alert monitoring.

Each backend instance runs a centralized `frame_hub` module that provides a single gRPC polling thread feeding an in-memory frame cache. All local consumers (frontend MJPEG streams, Gemini inspection, video recording, evidence capture) read from this cache. When edge AI monitoring is active, frame_hub also drives an on-demand ffmpeg process that pushes JPEG frames as RTSP to the Jetson mediamtx server.

```
                           Browser (SPA)
                           6 tabs: Patrol, Control (default),
                           History, Reports, Tokens, Settings
                               |
                       nginx (port 5000)
                      /        |        \
               robot-a     robot-b     robot-c
              Flask:5000  Flask:5000  Flask:5000
                 |            |           |
              Kachaka A    Kachaka B   Kachaka C
                 \            |          /
                  \           |         /
                   Shared SQLite DB (WAL)
                     data/report/report.db

          Each Flask backend contains:
            frame_hub:  gRPC poll -> frame cache -> ffmpeg RTSP push
            relay_manager: HTTP client -> Jetson relay service

          Jetson Host:
            mediamtx (port 8555)
              /{robot-id}/camera       <- frame_hub direct push (2fps)
              /{robot-id}/external     <- relay transcode output
            relay_service.py (port 5020)
              External RTSP in -> ffmpeg transcode H264 Baseline -> RTSP out
            VILA JPS (port 5010 API, port 5016 WS, port 5012 metrics)
              Pulls from mediamtx -> VLM analysis -> WebSocket alerts
```

## Component Breakdown

### Frontend (SPA)

- **Location**: `src/frontend/`
- **Served by**: nginx (static files) or Flask (fallback in dev)
- **Technology**: Vanilla JavaScript ES modules, no framework
- **Entry point**: `src/frontend/templates/index.html`
- **JS entry**: `src/frontend/static/js/app.js`

The frontend is a single HTML page with tab-based navigation across six tabs: **Patrol**, **Control** (default), **History**, **Reports**, **Tokens**, and **Settings**. All views exist in the DOM and are shown/hidden via `switchTab()`. The map canvas is physically reparented between the Control and Patrol tabs to avoid maintaining duplicate canvas state.

Key frontend modules:

| Module | Lines | Purpose |
|--------|-------|---------|
| `app.js` | 214 | Entry point, tab switching (6 tabs), robot selector polling (5s) |
| `state.js` | 45 | Shared mutable state object (single source of truth) |
| `map.js` | 275 | Canvas rendering, world-to-pixel transforms, mouse drag-to-move |
| `controls.js` | 52 | Manual D-pad control, return home, cancel command |
| `patrol.js` | 193 | Start/stop patrol, status polling (1s), camera stream, edge AI alerts |
| `points.js` | 327 | Patrol points CRUD, import/export, reordering, robot location import |
| `schedule.js` | 198 | Scheduled patrol CRUD with day-of-week checkboxes |
| `ai.js` | 108 | AI test UI, JSON result parsing, OK/NG color display |
| `history.js` | 247 | Patrol history browser, robot filtering, detail modal, PDF export |
| `reports.js` | 146 | Multi-run report generation with date range picker, collapsible cards |
| `settings.js` | 303 | 3 sub-tab settings UI (General, Gemini AI, VILA/Edge AI), edge AI test |
| `stats.js` | 272 | Token usage Chart.js line chart, millions display, pricing estimates |

The Settings page is organized into three sub-tabs:
- **General**: Timezone, turbo mode, idle stream toggle, Telegram configuration
- **Gemini AI**: API key, model, system prompt, report prompts, video recording
- **VILA / Edge AI**: Enable toggle, stream source radio buttons (robot camera OR external RTSP -- max 1 due to JPS limit), Jetson host IP, alert rules, test button

History cards display run titles as "Run #N" with a video icon for runs that include video recordings. The Tokens tab (formerly Stats) shows token usage in millions with pricing estimates ($0.50/1M input, $3.00/1M output).

### Backend (Flask)

- **Location**: `src/backend/`
- **Entry point**: `src/backend/app.py`
- **Runtime**: Python 3.10+, Flask 3.x

Each robot runs its own Flask process. The backend handles:
- REST API for the frontend
- gRPC communication with Kachaka robots via `kachaka-api`
- AI inference through Google Gemini API
- Patrol orchestration (movement, image capture, AI analysis)
- Centralized frame management via `frame_hub` (gRPC poll, frame cache, RTSP push)
- Relay service communication via `relay_manager` (HTTP client to Jetson relay)
- Live monitoring via VILA JPS API (stream registration, alert rules, WebSocket)
- PDF report generation
- Telegram notifications (patrol reports + live alert photos)
- Video recording during patrols

Key backend modules:

| Module | Lines | Purpose |
|--------|-------|---------|
| `app.py` | ~1000 | Flask REST API routes |
| `patrol_service.py` | ~790 | Patrol orchestration, scheduling, async inspection queue |
| `edge_ai_service.py` | ~850 | LiveMonitor + TestLiveMonitor (JPS flow), WebSocket alerts |
| `cloud_ai_service.py` | ~280 | Gemini VLM provider, structured outputs, token tracking |
| `pdf_service.py` | ~1030 | ReportLab PDF generation with CJK fonts (Noto Sans CJK TC) |
| `database.py` | ~440 | Schema, migrations (idempotent), multi-robot queries |
| `frame_hub.py` | ~300 | gRPC poll -> frame cache -> on-demand ffmpeg RTSP push |
| `robot_service.py` | ~190 | Kachaka gRPC interface, 100ms polling loop, thread-safe state |
| `relay_manager.py` | ~100 | RelayServiceClient HTTP client for Jetson relay service |
| `video_recorder.py` | ~110 | OpenCV video capture with codec auto-detection |
| `config.py` | ~125 | Env vars, paths, DEFAULT_SETTINGS, derived Jetson ports |
| `utils.py` | ~105 | JSON I/O (atomic save), timezone utilities |
| `settings_service.py` | ~60 | DB-backed settings wrapper (get_all, get, save) |
| `logger.py` | ~38 | Timezone-aware logging with robot_id prefix |

### Frame Hub (`frame_hub.py`)

The frame hub is the centralized camera frame management module. It replaces the pattern of multiple independent gRPC calls with a single polling thread and shared cache.

**Architecture:**

```
                    gRPC (Kachaka robot)
                           |
                    _poll_loop (~10fps)
                           |
                    frame cache (in-memory)
                   /       |       \        \
            MJPEG     Gemini AI   Video    Evidence
           stream    inspection  recorder  capture
                           |
                  (on-demand, during patrol)
                    _feeder_loop (2fps)
                           |
                    ffmpeg stdin (image2pipe)
                           |
                    RTSP push to mediamtx /{robot-id}/camera
```

**Key features:**
- Single gRPC polling thread at ~10fps feeds `_latest_frame` cache
- All consumers call `get_latest_frame()` -- zero additional gRPC overhead
- Polling lifecycle controlled by patrol state + `enable_idle_stream` setting
- On-demand ffmpeg RTSP push via `start_rtsp_push()` / `stop_rtsp_push()`
- Auto-restart monitor thread (`_monitor_push`) restarts ffmpeg if it dies
- `wait_for_push_ready()` blocks until first frame is fed (used by relay setup)

**Polling lifecycle:**
- Patrolling: always polling
- Idle + `enable_idle_stream=true`: polling (frontend shows live feed)
- Idle + `enable_idle_stream=false`: NOT polling (zero gRPC bandwidth)

### Relay Manager (`relay_manager.py`)

The relay manager is an HTTP client (`RelayServiceClient`) that communicates with the Jetson-side relay service via REST API. It delegates all ffmpeg subprocess management to the remote relay service -- there is no local ffmpeg for relay purposes.

**API methods:**
- `start_relay(key, source_url)` -- Start a relay on Jetson (RTSP input -> transcode -> RTSP output)
- `stop_relay(key)` -- Stop a specific relay
- `wait_for_stream(key, timeout)` -- Blocking readiness check
- `get_status()` -- Status of all relays
- `stop_all()` -- Stop all relays
- `is_available()` -- Health check

The module-level instance `relay_service_client` is `None` when `RELAY_SERVICE_URL` is empty, meaning relay functionality is completely unavailable. There is no local fallback -- live monitoring cannot start without the relay service.

### RTSP Relay Layer

The relay layer bridges robot cameras and external RTSP sources to VILA JPS for continuous VLM analysis. All streams are transcoded to H264 Baseline profile for compatibility with NvMMLite hardware decoder on Jetson.

**Components:**
- **frame_hub** (in VP backend): Pushes gRPC JPEG frames to mediamtx as raw RTSP via ffmpeg
- **relay_service.py** (port 5020, on Jetson): Standalone Flask app managing ffmpeg transcode processes
- **mediamtx** (port 8555, on Jetson): Lightweight RTSP server (`bluenviron/mediamtx` Docker image)

**Two relay pipelines:**

1. **Robot camera (direct push)**:
   ```
   frame_hub gRPC poll -> frame cache -> ffmpeg push (2fps)
     -> mediamtx /{robot-id}/camera
     -> VILA JPS
   ```

2. **External RTSP (via relay)**:
   ```
   External RTSP source URL
     -> relay service transcode (H264 Baseline)
     -> mediamtx /{robot-id}/external
     -> VILA JPS
   ```

The relay service CI-built image is at `ghcr.io/sigma-snaken/visual-patrol-relay:latest`.

### VILA JPS Integration

VILA JPS (Jetson Platform Services) provides continuous VLM analysis of RTSP streams with alert-based monitoring.

**API endpoints (on Jetson, port 5010):**
- `POST /api/v1/live-stream` -- Register RTSP streams with VILA (field: `liveStreamUrl`)
- `POST /api/v1/alerts` -- Set yes/no alert rules per stream (max 10 rules)
- `DELETE /api/v1/live-stream/{id}` -- Deregister stream on patrol stop
- `GET /api/v1/live-stream` -- List registered streams (used for stale stream cleanup)

**WebSocket (port 5016):**
- `ws://{host}:5016/api/v1/alerts/ws` -- Delivers real-time alert events

**JPS limit:** Maximum 1 stream at a time. The frontend enforces this with radio buttons (robot camera OR external RTSP, not both).

**streaming.py patch:** JPS requires a patched `streaming.py` (`deploy/vila-jps/streaming_patched.py`) that adds `h264parse` to the GStreamer pipeline for NvMMLite decoder compatibility. Pipeline: `rtspsrc (TCP) -> rtph264depay -> h264parse -> nvv4l2decoder -> nvvidconv -> BGRx appsink`.

**Alert handling:** When an alert fires, the backend captures an evidence frame (gRPC for robot camera, OpenCV RTSP for external), saves it to disk and DB (`edge_ai_alerts` table), and sends it to Telegram if configured. Alerts have a 60-second cooldown per rule+stream combination.

**TestLiveMonitor:** The settings page includes a test button that uses the same JPS flow (relay -> mediamtx -> JPS stream registration -> alert rules -> WebSocket listener). It runs in memory only (no DB writes) and includes a snapshot thread that pulls frames from mediamtx for live preview. The test also displays an RTSP URL for viewing in VLC.

### Reverse Proxy (nginx)

- **Dev config**: `nginx.conf` (root)
- **Prod config**: `deploy/nginx.conf`

nginx performs two functions:
1. Serves static frontend assets directly (faster than Flask)
2. Routes API requests to the correct backend based on URL pattern

### Database (SQLite)

- **File**: `data/report/report.db`
- **Mode**: WAL (Write-Ahead Logging) for concurrent read/write from multiple processes
- **Busy timeout**: 5000ms

All robot backends share a single database file. The `robot_id` column in each table distinguishes data per robot.

**Tables:**
- `patrol_runs` -- id, start/end_time, status, robot_id, report_content, model_id, per-category token columns
- `inspection_results` -- id, run_id, robot_id, point_name, coords, prompt, ai_response (JSON), is_ng, image_path, tokens
- `generated_reports` -- id, start/end_date, report_content, tokens, robot_id
- `robots` -- robot_id (PK), robot_name, robot_ip, last_seen, status (online/offline)
- `global_settings` -- key (PK), value (JSON string)
- `edge_ai_alerts` -- id, run_id, robot_id, rule, response, image_path, stream_source

## Request Flow

### Robot-Specific Requests

```
Browser:  GET /api/robot-a/state
    |
nginx:    Regex match ^/api/(robot-a)/(.*)$
          Strips prefix, proxies to http://robot-a:5000/api/state
    |
Flask:    Handles /api/state, returns robot-specific data
```

### Global Requests

```
Browser:  GET /api/settings
    |
nginx:    Falls through to /api/ catch-all
          Proxies to http://robot-a:5000/api/settings
    |
Flask:    Reads from shared SQLite DB, returns settings
```

Any backend can serve global requests because they all share the same database.

## Live Monitor Data Flow

```
1. Patrol starts
   +-- Robot camera: frame_hub.start_rtsp_push(mediamtx_host, /{robot-id}/camera)
   |   +-- ffmpeg stdin push at 2fps from frame cache (direct, no relay)
   |   +-- wait_for_push_ready() blocks until first frame fed
   +-- External RTSP: relay_service_client.start_relay()
   |   +-- transcode source -> /{robot-id}/external
   +-- Wait for stream ready (frame_hub or relay_service_client)

2. LiveMonitor.start()
   +-- Cleanup stale JPS streams (GET + DELETE)
   +-- POST /api/v1/live-stream -> register each stream -> get stream_id
   |   (retry up to 3 attempts with 5s delay -- gstDecoder may need time)
   +-- POST /api/v1/alerts -> set alert rules per stream
   +-- Start WebSocket listener thread

3. During patrol
   +-- VILA JPS pulls RTSP streams from mediamtx, evaluates rules continuously
   +-- On alert -> WebSocket event delivered to backend
   |   +-- Cooldown check (60s per rule+stream)
   |   +-- Capture evidence frame (gRPC for robot camera, cv2 RTSP for external)
   |   +-- Save JPEG to data/{robot_id}/report/edge_ai_alerts/
   |   +-- INSERT INTO edge_ai_alerts
   |   +-- Send Telegram photo if configured
   +-- Alerts visible in patrol dashboard via /api/{id}/patrol/edge_ai_alerts

4. Patrol ends
   +-- LiveMonitor.stop()
   |   +-- Close WebSocket
   |   +-- DELETE /api/v1/live-stream/{id} for each stream
   +-- relay_service_client.stop_all() -> stops ffmpeg on relay service
   +-- frame_hub.stop_rtsp_push() -> kills local ffmpeg, re-evaluates polling
   +-- Alerts included in AI summary report
```

## Data Model

### Per-Robot Data (filesystem)

Each robot stores its own configuration and images:

```
data/
+-- report/
|   +-- report.db              # Shared database
+-- robot-a/
|   +-- config/
|   |   +-- points.json        # Patrol waypoints
|   |   +-- patrol_schedule.json
|   +-- report/
|       +-- images/            # Inspection photos
|       |   +-- {run_id}_{timestamp}/
|       +-- edge_ai_alerts/    # Live monitor evidence images
+-- robot-b/
|   +-- ...
```

### Shared Data (database)

See [backend.md](backend.md) for the full database schema.

## Threading Model

Each Flask backend runs several background threads:

| Thread | Module | Purpose | Interval |
|--------|--------|---------|----------|
| `_poll_loop` | frame_hub | Polls robot camera via gRPC, updates frame cache | 100ms (~10fps) |
| `_feeder_loop` | frame_hub | Feeds JPEG frames from cache to ffmpeg stdin | 500ms (2fps) |
| `_monitor_push` | frame_hub | Monitors ffmpeg health, auto-restarts if dead | 5s |
| `_stderr_reader` | frame_hub | Reads ffmpeg stderr for logging | Continuous |
| `_polling_loop` | robot_service | Polls robot pose, battery, map via gRPC | 100ms |
| `_heartbeat_loop` | app.py | Updates robot online status in DB | 30s |
| `_schedule_checker` | patrol_service | Checks for scheduled patrol times | 30s |
| `_inspection_worker` | patrol_service | Processes AI inspection queue | Event-driven |
| `_record_loop` | video_recorder | Captures video frames during patrol | 1/fps |
| `_ws_listener` | edge_ai_service | Listens for VILA JPS WebSocket alert events | Continuous |
| `_snapshot_loop` | edge_ai_service | Captures mediamtx RTSP frames for test preview | 500ms |
| `_jps_setup` | edge_ai_service | Background JPS registration for test monitor | One-shot |

Note: `_feeder_loop`, `_monitor_push`, and `_stderr_reader` only run when RTSP push is active (during patrol with edge AI or edge AI test). `_ws_listener` only runs during active monitoring. `_snapshot_loop` and `_jps_setup` only run during edge AI test.

## Networking Modes

### Development (WSL2 / Docker Desktop)

Uses Docker bridge networking:

- nginx binds `ports: 5000:5000`
- All Flask backends listen on port 5000 internally
- nginx resolves backend hostnames via Docker DNS (`resolver 127.0.0.11`)
- Docker service names must match `ROBOT_ID` values (e.g., service `robot-a` = `ROBOT_ID=robot-a`)
- `RELAY_SERVICE_URL` points to the Jetson relay service (e.g., `http://192.168.50.35:5020`); when empty, relay is unavailable and live monitoring cannot start

### Production (Jetson / Linux host)

Uses host networking (`network_mode: host`):

- All containers share the host network stack
- nginx listens on port 5000
- Each Flask backend uses a unique port via `PORT` env var (5001, 5002, ...)
- mediamtx RTSP port configurable via `MTX_RTSPADDRESS` (default 8554, use 8555 if port conflicts)
- nginx routes by robot ID using explicit proxy rules to `127.0.0.1:PORT`
- `RELAY_SERVICE_URL=http://localhost:5020` (relay service on same Jetson host)
- `jetson_host` setting auto-derives all Jetson service URLs:
  - JPS API: `http://{jetson_host}:5010`
  - mediamtx: `{jetson_host}:8555`
  - Relay service: `http://{jetson_host}:5020`
  - WebSocket: `ws://{jetson_host}:5016`

See [deployment.md](deployment.md) for details.

## Security

- nginx adds security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
- Sensitive settings (API keys, Telegram tokens) are masked in GET responses (`****` prefix)
- Robot ID path parameters are validated against `^robot-[a-z0-9-]+$`
- Image serving validates robot ID format before constructing filesystem paths
- Docker runs Flask as non-root user (`appuser`, UID 1000)
- `entrypoint.sh` uses `gosu` to drop privileges after fixing volume permissions
