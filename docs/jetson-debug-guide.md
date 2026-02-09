# Jetson Debug Guide: RTSP Relay + VILA JPS

Reference for debugging the live monitoring pipeline on the Jetson deployment machine (`nvidia@192.168.50.35`).

## Deployment Layout

### Visual Patrol (this project)

```
/code/visual-patrol/deploy/
├── docker-compose.prod.yaml   # nginx + robot-a (host networking)
├── nginx.conf                 # Reverse proxy config
├── data/                      # Persistent runtime data
│   ├── report/report.db       # Shared SQLite DB
│   └── robot-a/
│       ├── config/            # points.json, patrol_schedule.json
│       └── report/
│           ├── images/        # Patrol inspection photos
│           ├── edge_ai_alerts/   # Live monitor evidence images
│           └── video/         # Patrol videos
└── logs/
    ├── robot-a_app.log
    ├── robot-a_patrol_service.log
    ├── robot-a_edge_ai_service.log    # <-- VILA JPS alerts, WebSocket events
    ├── robot-a_relay_manager.log   # <-- ffmpeg relay process status
    ├── robot-a_cloud_ai_service.log
    └── robot-a_video_recorder.log
```

### mediamtx (standalone, `/home/nvidia/mediamtx/`)

mediamtx is NOT part of visual-patrol's docker-compose. It runs as a standalone compose deployment.

- **Compose file**: `/home/nvidia/mediamtx/compose.yaml`
- **Port**: 8555 (changed from default 8554 to avoid conflict with JPS VST)
- **Container name**: `visual_patrol_mediamtx`
- **RTSP URL format**: `rtsp://localhost:8555/{robot-id}/camera` or `rtsp://localhost:8555/{robot-id}/external`
- **Manage**: `cd /home/nvidia/mediamtx && docker compose up -d` / `docker compose down`

### VILA JPS (external)

- **API port**: 5010
- **WebSocket port**: 5016
- **API base**: `http://localhost:5010`
- **WS endpoint**: `ws://localhost:5016/api/v1/alerts/ws`

## Source Code File Map

All source files are in the visual-patrol repo at `src/backend/`:

| File | Purpose | Key Details |
|------|---------|-------------|
| `src/backend/relay_manager.py` | Relay service client | `RelayServiceClient` HTTP client for Jetson relay service; `FrameFeederThread` for robot camera frames; `relay_service_client` module-level instance (None if `RELAY_SERVICE_URL` not set) |
| `src/backend/edge_ai_service.py` | VILA JPS lifecycle | `LiveMonitor` class: stream register → alert rules → WebSocket → deregister; `_cleanup_stale_streams()` on start; 60s alert cooldown; evidence capture + DB save + Telegram |
| `src/backend/patrol_service.py` | Patrol orchestration | Lines ~348-455: starts relay → sleeps 3s → starts live_monitor; `finally` block stops live_monitor then relay_manager |
| `src/backend/config.py` | Environment config | `RELAY_SERVICE_URL` (Jetson relay service URL), `JETSON_JPS_API_PORT` (5010), `JETSON_MEDIAMTX_PORT` (8555) |
| `src/backend/app.py` | Flask API endpoints | `/api/relay/status`, `/api/relay/test`, `/api/edge_ai/health` |

## Data Flow

```
Robot Camera (Kachaka gRPC)
  → relay_manager.py: FrameFeederThread at 0.5fps → HTTP POST to relay_service
    → relay_service.py: ffmpeg (image2pipe → libx264)
      → RTSP push to rtsp://localhost:8555/{robot-id}/camera

External RTSP Camera
  → relay_service.py: ffmpeg transcode (libx264)
    → RTSP push to rtsp://localhost:8555/{robot-id}/external

mediamtx (port 8555)
  ← VILA JPS pulls registered RTSP stream

VILA JPS (port 5010)
  → Analyzes video frames against alert rules
  → Sends alert events via WebSocket (port 5016)

edge_ai_service.py
  ← Receives WS alert events
  → Captures evidence frame (gRPC for robot cam, cv2 for external)
  → Saves to DB (edge_ai_alerts table) + disk (data/{robot-id}/report/edge_ai_alerts/)
  → Sends photo to Telegram (if configured)
```

## Quick Health Checks

Run these from the Jetson:

```bash
# --- Visual Patrol ---
# Check containers
docker compose -f /code/visual-patrol/deploy/docker-compose.prod.yaml ps

# Check relay status (are ffmpeg processes alive?)
curl -s http://localhost:5000/api/relay/status | python3 -m json.tool

# Check VILA JPS connectivity (from visual-patrol's perspective)
curl -s http://localhost:5000/api/edge_ai/health | python3 -m json.tool

# --- mediamtx ---
# Is mediamtx container running?
docker ps | grep mediamtx

# List active RTSP paths (mediamtx API, port 9997 by default)
curl -s http://localhost:9997/v3/paths/list | python3 -m json.tool

# Test RTSP stream playback (if ffplay available)
ffplay -rtsp_transport tcp rtsp://localhost:8555/robot-a/camera

# --- VILA JPS ---
# Health check
curl -s http://localhost:5010/api/v1/health/ready

# List registered streams (should be empty when no patrol running)
curl -s http://localhost:5010/api/v1/live-stream | python3 -m json.tool

# Check what ports are listening
ss -tlnp | grep -E '(5010|5016|8555)'
```

## Log Files to Check

All logs are at `/code/visual-patrol/deploy/logs/`:

```bash
# Live monitor (VILA JPS alerts, WebSocket connection)
tail -f /code/visual-patrol/deploy/logs/robot-a_edge_ai_service.log

# Relay manager (ffmpeg processes start/stop/crash)
tail -f /code/visual-patrol/deploy/logs/robot-a_relay_manager.log

# Patrol service (overall patrol flow, when relay/monitor starts/stops)
tail -f /code/visual-patrol/deploy/logs/robot-a_patrol_service.log

# All visual-patrol container logs
docker compose -f /code/visual-patrol/deploy/docker-compose.prod.yaml logs -f robot-a

# Follow all relevant logs at once
tail -f /code/visual-patrol/deploy/logs/robot-a_{live_monitor,relay_manager,patrol_service}.log
```

## Common Issues

### "Stream Maximum reached" from VILA JPS

**Symptom**: `edge_ai_service.log` shows 422 error when registering stream.

**Cause**: VILA JPS allows max 1 stream. A previous patrol crashed without deregistering.

**Fix**:
```bash
# List stale streams
curl -s http://localhost:5010/api/v1/live-stream | python3 -m json.tool

# Delete each stale stream
curl -X DELETE http://localhost:5010/api/v1/live-stream/{stream-id}
```

The code has `_cleanup_stale_streams()` that runs automatically on each `LiveMonitor.start()`, but if the JPS API is temporarily unreachable during cleanup, stale streams may persist.

### ffmpeg relay dies repeatedly

**Symptom**: `relay_manager.log` shows "Relay {key} died, restarting" followed by "exceeded max retries".

**Cause**: mediamtx is down, or network issue between Flask container and mediamtx.

**Fix**:
```bash
# Check mediamtx is running
docker ps | grep mediamtx

# Check mediamtx is listening on 8555
ss -tlnp | grep 8555

# Check relay service is reachable
curl -s http://localhost:5020/health

# Check env vars on robot-a container
docker exec visual_patrol_robot_a env | grep RELAY
# Expected: RELAY_SERVICE_URL=http://localhost:5020
```

### WebSocket never connects

**Symptom**: `edge_ai_service.log` shows "VILA WS error (attempt N/10)" repeatedly.

**Cause**: VILA JPS WebSocket port 5016 not reachable.

**Fix**:
```bash
# Check port 5016 is listening
ss -tlnp | grep 5016

# Test WebSocket manually (if wscat installed)
wscat -c ws://localhost:5016/api/v1/alerts/ws

# Check VILA JPS container logs for errors
docker logs <vila-jps-container-name> --tail 50
```

### Alerts trigger but no evidence image saved

**Symptom**: `edge_ai_alerts` table has entries with empty `image_path`.

**Cause**: Evidence capture failed. For robot camera: gRPC connection lost. For external: RTSP URL unreachable via cv2.

**Check**:
```bash
# Robot camera: test gRPC
curl -s http://localhost:5000/api/robot-a/state | python3 -m json.tool
# If robot is offline, gRPC evidence capture will fail

# External RTSP: test the relay URL
ffprobe -rtsp_transport tcp rtsp://localhost:8555/robot-a/external 2>&1 | head -5
```

### Patrol starts but live monitor doesn't activate

**Symptom**: `patrol_service.log` shows patrol running but no relay/monitor entries.

**Cause**: Settings not configured. All of these must be set:
1. `enable_edge_ai` = true
2. `jetson_host` is set (e.g. `192.168.50.35`)
3. At least one stream source enabled (`enable_robot_camera_relay` or `enable_external_rtsp`)
4. At least one rule in `edge_ai_rules`

**Check**:
```bash
curl -s http://localhost:5000/api/settings | python3 -m json.tool | grep -E '(enable_edge_ai|jetson_host|enable_robot|enable_external|edge_ai_rules)'
```

## Environment Variables (on robot services)

These are set in `docker-compose.prod.yaml` on each robot service:

| Variable | Jetson Value | Purpose |
|----------|-------------|---------|
| `RELAY_SERVICE_URL` | `http://localhost:5020` | Jetson relay service URL (manages ffmpeg → mediamtx) |

Since Jetson uses host networking, the relay service is at `localhost:5020`. The relay service's `MEDIAMTX_HOST` must match mediamtx's `MTX_RTSPADDRESS` configuration.

## VILA JPS API Quick Reference

```bash
# Health
GET  http://localhost:5010/api/v1/health/ready

# Streams
GET    http://localhost:5010/api/v1/live-stream          # List all
POST   http://localhost:5010/api/v1/live-stream          # Register: {"liveStreamUrl": "rtsp://...", "name": "..."}
DELETE http://localhost:5010/api/v1/live-stream/{id}      # Deregister

# Alerts
POST   http://localhost:5010/api/v1/alerts               # Set rules: {"alerts": ["Is there a person?"], "id": "stream_id"}

# WebSocket
WS     ws://localhost:5016/api/v1/alerts/ws              # Alert events (port 5016, NOT 5010)
```

**Critical**: Stream registration uses field `liveStreamUrl` (NOT `url`). Wrong field name returns 422.

## Restarting Services

```bash
# Restart only visual-patrol (mediamtx and VILA JPS are unaffected)
cd /code/visual-patrol/deploy
docker compose -f docker-compose.prod.yaml restart robot-a

# Full restart
docker compose -f docker-compose.prod.yaml down
docker compose -f docker-compose.prod.yaml up -d

# Update to latest image
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```
