# System Architecture

## Overview

Visual Patrol is a multi-robot autonomous inspection system. A web-based single-page application (SPA) connects through an nginx reverse proxy to per-robot Flask backend instances, all sharing a common SQLite database. An RTSP relay layer (mediamtx + ffmpeg) enables continuous camera streaming to the VILA JPS Alert API for real-time monitoring.

```
                           Browser (SPA)
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

              mediamtx (RTSP relay, port 8555)
              /{robot-id}/camera   ← ffmpeg transcode (gRPC JPEG)
              /{robot-id}/external ← ffmpeg transcode (RTSP source)
                        |
                   VILA JPS (h264parse + nvv4l2decoder)
                   Streams → Alert Rules → WebSocket events
```

## Component Breakdown

### Frontend (SPA)

- **Location**: `src/frontend/`
- **Served by**: nginx (static files) or Flask (fallback in dev)
- **Technology**: Vanilla JavaScript ES modules, no framework
- **Entry point**: `src/frontend/templates/index.html`
- **JS entry**: `src/frontend/static/js/app.js`

The frontend is a single HTML page with tab-based navigation. All views (Control, Patrol, History, Stats, Settings) exist in the DOM and are shown/hidden via `switchTab()`. The map canvas is physically reparented between the Control and Patrol tabs to avoid maintaining duplicate canvas state.

### Backend (Flask)

- **Location**: `src/backend/`
- **Entry point**: `src/backend/app.py`
- **Runtime**: Python 3.10+, Flask 3.x

Each robot runs its own Flask process. The backend handles:
- REST API for the frontend
- gRPC communication with Kachaka robots via `kachaka-api`
- AI inference through Google Gemini API
- Patrol orchestration (movement, image capture, AI analysis)
- RTSP relay management (ffmpeg subprocesses pushing to mediamtx)
- Live monitoring via VILA JPS API (stream registration, alert rules, WebSocket)
- PDF report generation
- Telegram notifications (patrol reports + live alert photos)
- Video recording during patrols

### RTSP Relay (mediamtx + ffmpeg)

- **mediamtx**: Lightweight RTSP server (`bluenviron/mediamtx` Docker image, port 8555 on Jetson)
- **ffmpeg**: Managed by `relay_service.py` (Jetson-side relay service)

Two relay types, both **transcoded** to H264 Baseline profile for NvMMLite hardware decoder compatibility:
1. **Robot camera relay**: gRPC JPEG frames → HTTP POST to relay service → ffmpeg `image2pipe` → libx264/NVENC → RTSP push to `/{robot-id}/camera`
2. **External RTSP relay**: Source RTSP → ffmpeg transcode (libx264/NVENC) → RTSP push to `/{robot-id}/external`

The relay service (`relay_service.py`, port 5020) is a standalone Flask app on Jetson managing ffmpeg processes. VP sends robot camera frames via HTTP POST through `RelayServiceClient`. CI-built image at `ghcr.io/sigma-snaken/visual-patrol-relay:latest`. When `RELAY_SERVICE_URL` is not set, relay functionality is unavailable.

VILA JPS pulls these RTSP streams from mediamtx for continuous VLM analysis.

### VILA JPS Integration

- **Stream API**: `POST /api/v1/live-stream` registers RTSP streams with VILA
- **Alert API**: `POST /api/v1/alerts` sets yes/no alert rules per stream
- **WebSocket**: `ws://{host}:5016/api/v1/alerts/ws` delivers real-time alert events
- **Deregister**: `DELETE /api/v1/live-stream/{id}` cleans up on patrol stop
- **streaming.py patch**: JPS requires a patched `streaming.py` (`deploy/vila-jps/streaming_patched.py`) that adds `h264parse` to the GStreamer pipeline for NvMMLite decoder compatibility

When an alert fires, the backend captures an evidence frame from mediamtx via OpenCV RTSP, saves it to disk and DB, and sends it to Telegram if configured.

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
   ├── relay_service_client starts relay on Jetson
   │   ├── Robot camera: gRPC frames → HTTP POST → relay service → ffmpeg → mediamtx RTSP
   │   └── External RTSP: relay service → ffmpeg transcode → mediamtx RTSP
   └── Wait for stream ready on relay service

2. LiveMonitor.start()
   ├── POST /api/v1/live-stream → register each stream → get stream_id
   ├── POST /api/v1/alerts → set alert rules per stream
   └── Start WebSocket listener thread

3. During patrol
   ├── VILA JPS pulls RTSP streams, evaluates rules continuously
   ├── On alert → WebSocket event delivered to backend
   │   ├── Cooldown check (60s per rule+stream)
   │   ├── Capture evidence frame from mediamtx (cv2 RTSP)
   │   ├── Save JPEG to data/{robot_id}/report/edge_ai_alerts/
   │   ├── INSERT INTO edge_ai_alerts
   │   └── Send Telegram photo if configured
   └── Alerts visible in patrol dashboard via /api/{id}/patrol/edge_ai_alerts

4. Patrol ends
   ├── LiveMonitor.stop()
   │   ├── Close WebSocket
   │   └── DELETE /api/v1/live-stream/{id} for each stream
   ├── relay_service_client.stop_all() → stops ffmpeg on relay service
   └── Alerts included in AI summary report
```

## Data Model

### Per-Robot Data (filesystem)

Each robot stores its own configuration and images:

```
data/
├── report/
│   └── report.db              # Shared database
├── robot-a/
│   ├── config/
│   │   ├── points.json        # Patrol waypoints
│   │   └── patrol_schedule.json
│   └── report/
│       ├── images/            # Inspection photos
│       │   └── {run_id}_{timestamp}/
│       └── edge_ai_alerts/       # Live monitor evidence images
├── robot-b/
│   └── ...
```

### Shared Data (database)

See [backend.md](backend.md) for the full database schema.

## Threading Model

Each Flask backend runs several background threads:

| Thread | Purpose | Interval |
|--------|---------|----------|
| `_polling_loop` (robot_service) | Polls robot pose, battery, map via gRPC | 100ms |
| `_heartbeat_loop` (app.py) | Updates robot online status in DB | 30s |
| `_schedule_checker` (patrol_service) | Checks for scheduled patrol times | 30s |
| `_inspection_worker` (patrol_service) | Processes AI inspection queue | Event-driven |
| `_record_loop` (video_recorder) | Captures video frames during patrol | 1/fps |
| `_feeder_loop` (relay_manager) | Feeds gRPC JPEG frames to relay service via HTTP POST | 2s (0.5 fps) |
| `_ws_listener` (edge_ai_service) | Listens for VILA JPS WebSocket alert events | Continuous |

## Networking Modes

### Development (WSL2 / Docker Desktop)

Uses Docker bridge networking:

- nginx binds `ports: 5000:5000`
- mediamtx binds `ports: 8554:8554`
- All Flask backends listen on port 5000 internally
- nginx resolves backend hostnames via Docker DNS (`resolver 127.0.0.11`)
- Docker service names must match `ROBOT_ID` values (e.g., service `robot-a` = `ROBOT_ID=robot-a`)
- `RELAY_SERVICE_URL` points to the Jetson relay service (e.g., `http://192.168.50.35:5020`)

### Production (Jetson / Linux host)

Uses host networking (`network_mode: host`):

- All containers share the host network stack
- nginx listens on port 5000
- Each Flask backend uses a unique port via `PORT` env var (5001, 5002, ...)
- mediamtx RTSP port configurable via `MTX_RTSPADDRESS` (default 8554, use 8555 if port conflicts)
- nginx routes by robot ID using explicit proxy rules to `127.0.0.1:PORT`
- `RELAY_SERVICE_URL=http://localhost:5020` (relay service on same Jetson host)

See [deployment.md](deployment.md) for details.

## Security

- nginx adds security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
- Sensitive settings (API keys, Telegram tokens) are masked in GET responses (`****` prefix)
- Robot ID path parameters are validated against `^robot-[a-z0-9-]+$`
- Image serving validates robot ID format before constructing filesystem paths
- Docker runs Flask as non-root user (`appuser`, UID 1000)
- `entrypoint.sh` uses `gosu` to drop privileges after fixing volume permissions
