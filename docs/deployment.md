# Deployment Guide

## Overview

Visual Patrol supports two deployment modes:

| Mode | Networking | Use Case | Config Files |
|------|-----------|----------|-------------|
| **Development** | Docker bridge | WSL2, Docker Desktop, macOS | `docker-compose.yml`, `nginx.conf` |
| **Production** | Host networking | Jetson, bare-metal Linux | `deploy/docker-compose.prod.yaml`, `deploy/nginx.conf` |

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Network access to the Kachaka robot(s)
- (Production) Network access to `ghcr.io` for pulling images
- (Live monitor) VILA JPS server, mediamtx, and relay service running on the Jetson

## Development Setup

### Quick Start

```bash
git clone https://github.com/sigma-snaken/visual-patrol.git
cd visual-patrol

# Edit robot IPs in docker-compose.yml
vim docker-compose.yml

docker compose up -d
```

Open [http://localhost:5000](http://localhost:5000).

### How It Works

- nginx binds port `5000` on the host
- Each robot service runs Flask on port `5000` internally (Docker bridge networking isolates them)
- nginx resolves service names via Docker's internal DNS (`resolver 127.0.0.11`)
- Docker service names **must** match `ROBOT_ID` values (e.g., service `robot-a` = env `ROBOT_ID=robot-a`)
- All services mount `./src` for live code reloading and `./data` + `./logs` for persistent storage
- RTSP relay runs on Jetson; `RELAY_SERVICE_URL` env var points robot services to it

### Adding a Robot (Dev)

1. Add a new service block to `docker-compose.yml`:

```yaml
  robot-d:
    container_name: visual_patrol_robot_d
    build: .
    volumes:
      - ./src:/app/src
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - DATA_DIR=/app/data
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - ROBOT_ID=robot-d
      - ROBOT_NAME=Robot D
      - ROBOT_IP=192.168.50.135:26400
      - RELAY_SERVICE_URL=http://192.168.50.35:5020
    restart: unless-stopped
```

2. Add `robot-d` to the nginx service's `depends_on` list.

3. Restart:
```bash
docker compose up -d
```

No nginx config changes needed -- the regex `^/api/(robot-[^/]+)/(.*)$` auto-routes based on Docker service name.

### Local Development (No Docker)

```bash
# Install dependencies
uv pip install --system -r src/backend/requirements.txt

# Set environment
export DATA_DIR=$(pwd)/data
export LOG_DIR=$(pwd)/logs
export ROBOT_ID=robot-a
export ROBOT_NAME="Robot A"
export ROBOT_IP=192.168.50.133:26400

# Run
python src/backend/app.py
```

Flask serves both the API and frontend at `http://localhost:5000`. No nginx needed for single-robot local dev.

To enable relay functionality in local dev, set `RELAY_SERVICE_URL` to point at the Jetson relay service:
```bash
export RELAY_SERVICE_URL=http://192.168.50.35:5020
```

When `RELAY_SERVICE_URL` is not set (empty), relay functionality is unavailable and live monitoring cannot start.

## Production Setup (Jetson / Linux)

### Fresh Install

No need to clone the repository. Only two config files are needed:

```bash
mkdir -p ~/visual-patrol && cd ~/visual-patrol

# Download config files
curl -LO https://raw.githubusercontent.com/sigma-snaken/visual-patrol/main/deploy/docker-compose.prod.yaml
curl -LO https://raw.githubusercontent.com/sigma-snaken/visual-patrol/main/deploy/nginx.conf

# Edit robot IP and other settings
vim docker-compose.prod.yaml

# Pull and start
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

The `data/` and `logs/` directories are created automatically on first start.

### How It Works

- All containers use `network_mode: host` (required for Jetson with `iptables: false`)
- nginx listens on port 5000 on the host
- Each Flask backend listens on a unique port via `PORT` env var (5001, 5002, ...)
- nginx routes by matching robot IDs in the URL to specific ports
- Images are pulled from `ghcr.io/sigma-snaken/visual-patrol:latest`
- All services use `RELAY_SERVICE_URL=http://localhost:5020` (relay service on same host)
- The relay service (`rtsp-relay`) is included in the prod compose file alongside robot services

### mediamtx (External Dependency)

mediamtx is the RTSP relay server used by the live monitoring pipeline. It is **not included** in visual-patrol's docker-compose files -- it is deployed as a standalone compose on the Jetson.

Typical deployment location: `/home/nvidia/mediamtx/` (or `/code/mediamtx/`).

```bash
# Start mediamtx
cd /home/nvidia/mediamtx && docker compose up -d

# Check status
docker compose ps
```

mediamtx listens on port `8555` for RTSP connections. Both the frame_hub ffmpeg push and the relay service push transcoded streams here. VILA JPS pulls streams from mediamtx for analysis.

**Port conflicts:** If the default RTSP port conflicts with another service (e.g., VILA JPS VST uses 8554), configure mediamtx on port `8555` and ensure `MEDIAMTX_HOST` on the relay service matches (e.g., `localhost:8555`). The `JETSON_MEDIAMTX_PORT` constant in `config.py` is set to `8555`.

### RTSP Relay Service (Jetson)

The relay service is a Jetson-side component that handles all ffmpeg video transcoding. It runs alongside mediamtx and VILA JPS on the Jetson. CI automatically builds multi-arch images to `ghcr.io/sigma-snaken/visual-patrol-relay:latest`.

**Why?** Running ffmpeg on Jetson instead of in the Flask container provides:
- All streams transcoded to clean H264 Baseline profile (required for NvMMLite hardware decoder)
- Eliminates cross-network RTSP stream instability
- Both robot camera (from frame_hub raw push) and external RTSP (re-encode) go through the same pipeline

**Architecture:**
```
VP Flask (dev/Jetson)              Jetson (host networking)
+----------------------+          +-----------------------------+
| frame_hub.py         |  RTSP    | mediamtx (:8555)            |
|  ffmpeg push (2fps)  |  push    |  /{robot-id}/camera         |
|  -> /{robot-id}/cam  | -------> |                             |
| relay_manager.py     |  HTTP    | relay_service.py (:5020)    |
|  (external RTSP only)| -------> |  ffmpeg transcode (libx264) |
+----------------------+          | VILA JPS (:5010/:5016)      |
                                  +-----------------------------+
```

For robot cameras: frame_hub pushes JPEG-over-RTSP (H264 Baseline, 2fps) directly to mediamtx at `/{robot_id}/camera` -- no relay needed. For external RTSP cameras: the relay service reads the source URL and transcodes to `/{robot_id}/external`.

**Setup (via prod compose):**

The `rtsp-relay` service is included in `deploy/docker-compose.prod.yaml`:

```yaml
  rtsp-relay:
    container_name: visual_patrol_rtsp_relay
    image: ghcr.io/sigma-snaken/visual-patrol-relay:latest
    network_mode: host
    runtime: nvidia
    volumes:
      - ./logs:/app/logs
    environment:
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - RELAY_SERVICE_PORT=5020
      - MEDIAMTX_HOST=localhost:8555
      - USE_NVENC=false
      - RELAY_FPS=2
    restart: unless-stopped
```

**Standalone setup (manual):**

```bash
# Pull CI-built image
docker pull ghcr.io/sigma-snaken/visual-patrol-relay:latest

# Run
docker rm -f visual_patrol_rtsp_relay 2>/dev/null
docker run -d --name visual_patrol_rtsp_relay \
  --network=host \
  -e TZ=Asia/Taipei \
  -e RELAY_SERVICE_PORT=5020 \
  -e MEDIAMTX_HOST=localhost:8555 \
  -e USE_NVENC=false \
  -e RELAY_FPS=2 \
  --restart=unless-stopped \
  ghcr.io/sigma-snaken/visual-patrol-relay:latest
```

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_SERVICE_PORT` | `5020` | HTTP API listen port |
| `MEDIAMTX_HOST` | `localhost:8555` | mediamtx RTSP push target (`host:port`) |
| `USE_NVENC` | `false` | Use NVENC hardware encoder (`h264_nvmpi`). Requires L4T base image and `--runtime=nvidia`. |
| `RELAY_FPS` | `2` | Output framerate for transcode |
| `LOG_DIR` | `./logs` | Log file directory |

**VP Connection:** Set `RELAY_SERVICE_URL` on each robot service to point to the relay service:
- Production (Jetson, host networking): `RELAY_SERVICE_URL=http://localhost:5020`
- Development (WSL2, bridge networking): `RELAY_SERVICE_URL=http://192.168.50.35:5020` (Jetson IP)

When `RELAY_SERVICE_URL` is not set (empty), `relay_service_client` is `None` and all relay functionality is unavailable. Live monitoring cannot start without a relay service.

**Verify:**

```bash
# Health check
curl http://localhost:5020/health

# List active relays
curl http://localhost:5020/relays

# Test external RTSP relay
curl -X POST http://localhost:5020/relays \
  -H 'Content-Type: application/json' \
  -d '{"key":"test/external","source_url":"rtsp://admin:pass@192.168.50.45:554/live/profile.1"}'

# Check stream readiness
curl "http://localhost:5020/relays/test%2Fexternal/ready?timeout=15"

# Stop all relays
curl -X POST http://localhost:5020/relays/stop_all
```

### JPS VLM streaming.py Patch

VILA JPS's built-in `jetson_utils.videoSource` creates a GStreamer pipeline without `h264parse`, causing `nvv4l2decoder` to fail with "Stream format not found" when reading from mediamtx relay streams.

A patched `streaming.py` is provided at `deploy/vila-jps/streaming_patched.py`. It replaces `jetson_utils.videoSource` with a custom GStreamer Python pipeline:

```
rtspsrc (TCP) -> rtph264depay -> h264parse -> nvv4l2decoder -> nvvidconv -> appsink
```

**Setup:**

```bash
# Copy patch to JPS directory
cp deploy/vila-jps/streaming_patched.py /code/vila-jps/streaming_patched.py

# Ensure JPS compose.yaml has the volume mount:
# volumes:
#   - ./streaming_patched.py:/jetson-services/inference/vlm/src/mmj_utils/mmj_utils/streaming.py

# Restart JPS
cd /code/vila-jps && docker compose restart jps_vlm
```

### Adding a Robot (Prod)

1. Add a new service to `docker-compose.prod.yaml` with a **unique** `PORT`:

```yaml
  robot-b:
    container_name: visual_patrol_robot_b
    image: ghcr.io/sigma-snaken/visual-patrol:latest
    network_mode: host
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - DATA_DIR=/app/data
      - LOG_DIR=/app/logs
      - TZ=Asia/Taipei
      - PORT=5002
      - ROBOT_ID=robot-b
      - ROBOT_NAME=Robot B
      - ROBOT_IP=192.168.50.134:26400
      - RELAY_SERVICE_URL=http://localhost:5020
    restart: unless-stopped
```

2. Add an `if` block for the new robot in `deploy/nginx.conf`:

```nginx
if ($robot_id = "robot-b") { set $backend_port 5002; }
```

The full location block should look like:

```nginx
location ~ ^/api/(robot-[^/]+)/(.*)$ {
    set $robot_id $1;
    set $api_path $2;

    set $backend_port 5001;
    if ($robot_id = "robot-b") { set $backend_port 5002; }
    # Add more robots here...

    proxy_pass http://127.0.0.1:$backend_port/api/$api_path$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

3. Add a healthcheck with the **correct port** (must match `PORT` env var):

```yaml
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5002/api/state')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

4. Restart:
```bash
docker compose -f docker-compose.prod.yaml up -d
docker exec visual_patrol_nginx nginx -s reload
```

### Updating

```bash
cd ~/visual-patrol
docker compose -f docker-compose.prod.yaml pull
docker compose -f docker-compose.prod.yaml up -d
```

### Common Commands

```bash
# View logs
docker compose -f docker-compose.prod.yaml logs -f

# View specific service logs
docker compose -f docker-compose.prod.yaml logs -f robot-a

# Stop all services
docker compose -f docker-compose.prod.yaml down

# Restart a specific service
docker compose -f docker-compose.prod.yaml restart robot-a

# Check service status
docker compose -f docker-compose.prod.yaml ps

# Check relay status
curl http://localhost:5000/api/relay/status

# Check VILA JPS health
curl http://localhost:5000/api/edge_ai/health
```

## Docker Image

### Build

The CI pipeline (`.github/workflows/docker-publish.yaml`) automatically builds multi-architecture images on every push to `main`:

- **Platforms:** `linux/amd64`, `linux/arm64`
- **Registry:** `ghcr.io/sigma-snaken/visual-patrol`
- **Tags:** `latest` (main branch), `main`, `v1.0.0` (semver tags)
- **Cache:** GitHub Actions cache (`type=gha`)

Two images are built:
- `ghcr.io/sigma-snaken/visual-patrol:latest` -- Main application (Flask + frontend)
- `ghcr.io/sigma-snaken/visual-patrol-relay:latest` -- Relay service (ffmpeg transcoding)

### Manual Build

```bash
# Main application
docker build -t visual-patrol .

# Relay service (build from repo root)
docker build -f deploy/relay-service/Dockerfile -t visual-patrol-relay .
```

### Dockerfile Details

**Main application (`Dockerfile`):**

1. Base: `python:3.10-slim`
2. System deps: gcc, g++, cmake, ffmpeg, gosu, OpenCV deps
3. Python deps: Installed via `uv pip` (fast resolver)
4. Source: Copies `src/` directory
5. Frontend libs: Downloads Chart.js and marked.js from CDN at build time
6. CJK fonts: Downloads Noto Sans CJK TC for PDF generation
7. User: Creates `appuser` (UID 1000) for non-root execution
8. Entrypoint: `entrypoint.sh` fixes volume permissions then drops to `appuser` via `gosu`

**Relay service (`deploy/relay-service/Dockerfile`):**

1. Base: `python:3.10-slim`
2. System deps: ffmpeg
3. Python deps: Flask
4. Source: Copies `src/backend/relay_service.py`
5. Default env: `RELAY_SERVICE_PORT=5020`, `MEDIAMTX_HOST=localhost:8555`, `USE_NVENC=false`, `RELAY_FPS=2`

## Directory Structure (Runtime)

```
~/visual-patrol/               # Or wherever deployed
├── docker-compose.prod.yaml   # Service definitions
├── nginx.conf                 # Reverse proxy config
├── data/                      # Persistent data (auto-created)
│   ├── report/
│   │   └── report.db          # Shared SQLite database
│   ├── robot-a/
│   │   ├── config/
│   │   │   ├── points.json
│   │   │   └── patrol_schedule.json
│   │   └── report/
│   │       ├── images/        # Inspection photos
│   │       ├── edge_ai_alerts/   # Live monitor evidence images
│   │       └── video/         # Patrol videos (if enabled)
│   └── robot-b/
│       └── ...
└── logs/                      # Application logs (auto-created)
    ├── robot-a_app.log
    ├── robot-a_cloud_ai_service.log
    ├── robot-a_patrol_service.log
    ├── robot-a_video_recorder.log
    ├── robot-a_edge_ai_service.log
    ├── robot-a_frame_hub.log
    ├── robot-a_relay_manager.log
    └── relay_service.log       # From the relay service container
```

## Networking Comparison

| Aspect | Dev (Bridge) | Prod (Host) |
|--------|-------------|-------------|
| `network_mode` | (default bridge) | `host` |
| nginx port | `ports: 5000:5000` | Listens on host:5000 |
| Flask ports | All internal 5000 | Unique per robot (5001, 5002...) |
| Service discovery | Docker DNS | Explicit `127.0.0.1:PORT` |
| `RELAY_SERVICE_URL` | `http://192.168.50.35:5020` (Jetson IP) | `http://localhost:5020` |
| Adding a robot | Add service only | Add service + nginx `if` block |
| Frontend serving | nginx serves `/app/frontend` | Flask serves (proxied through nginx) |
| Why | Docker Desktop + WSL2 breaks `network_mode: host` | Jetson `iptables: false` breaks bridge |

## Healthcheck

Production services include a Docker healthcheck:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/state')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Robot shows "offline" | Check `ROBOT_IP` in compose file; verify robot is reachable on the network |
| Robot dropdown empty | Verify backends are running: `docker compose ps` |
| AI analysis failed | Check Gemini API key in Settings; review `logs/{robot-id}_cloud_ai_service.log` |
| PDF generation failed | Check `logs/{robot-id}_app.log` for errors |
| Camera stream not loading | Enable "Continuous Camera Stream" in Settings; verify robot connection |
| Map not loading | Robot may still be connecting; check container logs for gRPC errors |
| Port conflict (prod) | Ensure each robot has a unique `PORT` value |
| mediamtx port conflict | Change `MTX_RTSPADDRESS` in mediamtx config and update `MEDIAMTX_HOST` on relay service to match |
| Live monitor not working | Check `logs/{robot-id}_edge_ai_service.log`; verify VILA JPS is running (`/api/edge_ai/health`); verify mediamtx and relay service are both running |
| Relay service unreachable | Check `RELAY_SERVICE_URL` env var is set; verify relay service is running: `curl http://localhost:5020/health` |
| `RELAY_SERVICE_URL` empty | Relay functionality is completely disabled. Set `RELAY_SERVICE_URL` to enable live monitoring. |
| ffmpeg relay crashing | Check `logs/relay_service.log` on Jetson; verify mediamtx is running and accepting connections |
| Relay stall detection | The relay service auto-restarts ffmpeg if no new frames are produced for 30 seconds; check source RTSP availability |
| NVENC encoder not working | Check `USE_NVENC` env var; verify `--runtime=nvidia`; check relay service logs for encoder errors; set `USE_NVENC=false` to fall back to libx264 |
| JPS stream registration fails | JPS retries up to 5 times with 10s delays; check JPS logs and verify the RTSP stream is available on mediamtx |
| WebSocket max reconnects | Edge AI service gives up after 10 reconnect attempts; check JPS WebSocket port 5016 is accessible |
| Permission denied on data/logs | The entrypoint script runs `chown` automatically; check if `gosu` is installed |
| Stale robot entries in DB | Can happen if `ROBOT_ID` env var is missing (defaults to `"default"`) |
| frame_hub push not starting | Check `logs/{robot-id}_frame_hub.log`; verify camera is connected and `enable_idle_stream` is true |
| All robots show same map/camera | nginx routing broken — see [Multi-Robot Routing Debug](#multi-robot-routing-debug) below |
| Healthcheck always unhealthy | Verify each robot's healthcheck URL matches its own `PORT` (not another robot's port) |

### Multi-Robot Routing Debug

When switching robots in the frontend but always seeing the same map/camera/state data, the problem is nginx routing all robot-specific requests to the same backend. This section walks through a systematic diagnosis.

#### Symptom

Frontend has 3 robots in the dropdown. Switching between them shows identical map images and camera feeds — always from the first robot.

#### Step 1: Verify backends are running on separate ports

Each robot backend must listen on its own port (set via `PORT` env var):

```bash
# Check all containers are running
docker compose -f docker-compose.prod.yaml ps

# Test each backend directly (bypass nginx)
curl -s -o /dev/null -w '%{http_code}' http://localhost:5001/api/state   # robot-a
curl -s -o /dev/null -w '%{http_code}' http://localhost:5002/api/state   # robot-b
curl -s -o /dev/null -w '%{http_code}' http://localhost:5003/api/state   # robot-c
```

All should return `200`. If any returns connection refused, that backend is not running or using a different port.

#### Step 2: Verify nginx routes to different backends

```bash
# These should return different robot_id values
curl -s http://localhost:5000/api/robot-a/state | python3 -c "import json,sys; print(json.load(sys.stdin).get('robot_id'))"
curl -s http://localhost:5000/api/robot-b/state | python3 -c "import json,sys; print(json.load(sys.stdin).get('robot_id'))"
curl -s http://localhost:5000/api/robot-c/state | python3 -c "import json,sys; print(json.load(sys.stdin).get('robot_id'))"
```

If all three return the same `robot_id`, nginx is routing everything to one backend.

#### Step 3: Verify map images are different

```bash
# Compare response sizes — different robots with different maps should have different sizes
curl -s -o /dev/null -w 'robot-a: %{size_download}\n' http://localhost:5000/api/robot-a/map
curl -s -o /dev/null -w 'robot-b: %{size_download}\n' http://localhost:5000/api/robot-b/map
curl -s -o /dev/null -w 'robot-c: %{size_download}\n' http://localhost:5000/api/robot-c/map
```

#### Root Cause: nginx.conf hardcoded port

The most common cause is `nginx.conf` routing all robot-specific requests to the same port:

```nginx
# WRONG — all robots go to port 5001
location ~ ^/api/(robot-[^/]+)/(.*)$ {
    proxy_pass http://127.0.0.1:5001/api/$2$is_args$args;
}
```

The correct config uses `set` + `if` to route by robot ID:

```nginx
# CORRECT — route to backend by robot ID
location ~ ^/api/(robot-[^/]+)/(.*)$ {
    set $robot_id $1;
    set $api_path $2;

    set $backend_port 5001;
    if ($robot_id = "robot-b") { set $backend_port 5002; }
    if ($robot_id = "robot-c") { set $backend_port 5003; }

    proxy_pass http://127.0.0.1:$backend_port/api/$api_path$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

> **Note**: `map $uri` with variable in `proxy_pass` does NOT work reliably in nginx regex locations. Use `set` + `if` instead.

#### Step 4: Verify healthchecks match ports

Each robot's healthcheck must point to its own port. A common copy-paste error:

```yaml
# WRONG — robot-b checking robot-a's port
robot-b:
  environment:
    - PORT=5002
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/state')"]

# CORRECT
robot-b:
  environment:
    - PORT=5002
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5002/api/state')"]
```

#### After Fixing

```bash
# Reload nginx (no container restart needed)
docker exec visual_patrol_nginx nginx -t && docker exec visual_patrol_nginx nginx -s reload

# Re-run Step 2 to verify routing
```
