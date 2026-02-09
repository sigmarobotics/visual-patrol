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
- (Live monitor) VILA JPS server and mediamtx accessible from the deployment machine (mediamtx deployed standalone at `/home/nvidia/mediamtx/`)

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
- mediamtx runs externally (deployed with VILA JPS stack); `MEDIAMTX_INTERNAL` / `MEDIAMTX_EXTERNAL` env vars point robot services to it

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
      - MEDIAMTX_INTERNAL=mediamtx:8554
      - MEDIAMTX_EXTERNAL=localhost:8554
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

For RTSP relay functionality, run mediamtx separately:
```bash
docker run -d --name mediamtx -p 8554:8554 bluenviron/mediamtx:latest
```

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
- All services use `MEDIAMTX_INTERNAL` and `MEDIAMTX_EXTERNAL` pointing to `localhost:{port}`

### mediamtx (External Dependency)

mediamtx is the RTSP relay server used for live monitoring. It is **not included** in visual-patrol's docker-compose files -- it is deployed as a standalone compose at `/code/mediamtx/compose.yaml` on the Jetson.

```bash
# Start mediamtx
cd /code/mediamtx && docker compose up -d

# Check status
docker compose -f /code/mediamtx/compose.yaml ps
```

Visual Patrol connects to mediamtx via the `MEDIAMTX_INTERNAL` and `MEDIAMTX_EXTERNAL` environment variables on each robot service. Ensure mediamtx is running and reachable at the configured addresses before enabling live monitoring.

**Port conflicts:** If the default RTSP port (8554) is already in use (e.g., by VILA JPS VST), configure mediamtx on another port like `8555` and update `MEDIAMTX_INTERNAL`/`MEDIAMTX_EXTERNAL` on all robot services to match.

### RTSP Relay Service (Jetson)

The relay service is a Jetson-side component that handles all ffmpeg video transcoding. It runs alongside mediamtx and VILA JPS on the Jetson. CI automatically builds multi-arch images to `ghcr.io/sigma-snaken/visual-patrol-relay:latest`.

**Why?** Running ffmpeg on Jetson instead of in the Flask container provides:
- All streams transcoded to clean H264 Baseline profile (required for NvMMLite hardware decoder)
- Eliminates cross-network RTSP stream instability
- Both robot camera (JPEG→H264) and external RTSP (re-encode) go through the same pipeline

**Architecture:**
```
VP Flask (dev/Jetson)              Jetson (host networking)
┌──────────────────────┐          ┌─────────────────────────────┐
│ relay_manager.py     │  HTTP    │ relay_service.py (:5020)    │
│  RelayServiceClient  │ ──────> │  ffmpeg transcode (libx264) │
│  FrameFeederThread   │         │  → mediamtx (:8555)         │
│                      │         │  → VILA JPS (:5010/:5016)   │
└──────────────────────┘         └─────────────────────────────┘
```

VP grabs robot camera frames via gRPC and POSTs them to the relay service over HTTP. For external cameras, the relay fetches the RTSP source directly. Both types are transcoded and pushed to mediamtx.

**Setup (Jetson):**

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
  --restart=unless-stopped \
  ghcr.io/sigma-snaken/visual-patrol-relay:latest
```

Or use the prod compose file which includes the `rtsp-relay` service.

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_SERVICE_PORT` | `5020` | HTTP API listen port |
| `MEDIAMTX_HOST` | `localhost:8555` | mediamtx RTSP push target |
| `USE_NVENC` | `false` | Use NVENC hardware encoder (`h264_nvmpi`). Requires L4T base image. |
| `LOG_DIR` | `./logs` | Log file directory |

**VP Connection:** Set `RELAY_SERVICE_URL` on each robot service to point to the relay service:
- Production (Jetson, host networking): `RELAY_SERVICE_URL=http://localhost:5020`
- Development (WSL2, bridge networking): `RELAY_SERVICE_URL=http://192.168.50.35:5020` (Jetson IP)

When `RELAY_SERVICE_URL` is not set or the service is unreachable, VP falls back to local ffmpeg (software encoding in the Flask container).

**Verify:**

```bash
# Health check
curl http://localhost:5020/health

# List active relays
curl http://localhost:5020/relays

# Test external RTSP relay
curl -X POST http://localhost:5020/relays \
  -H 'Content-Type: application/json' \
  -d '{"key":"test/external","type":"external_rtsp","source_url":"rtsp://admin:pass@192.168.50.45:554/live/profile.1"}'

# Check stream readiness
curl "http://localhost:5020/relays/test%2Fexternal/ready?timeout=15"

# Stop all relays
curl -X POST http://localhost:5020/relays/stop_all
```

### JPS VLM streaming.py Patch

VILA JPS's built-in `jetson_utils.videoSource` creates a GStreamer pipeline without `h264parse`, causing `nvv4l2decoder` to fail with "Stream format not found" when reading from mediamtx relay streams.

A patched `streaming.py` is provided at `deploy/vila-jps/streaming_patched.py`. It replaces `jetson_utils.videoSource` with a custom GStreamer Python pipeline:

```
rtspsrc (TCP) → rtph264depay → h264parse → nvv4l2decoder → nvvidconv → appsink
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

See [Relay Service Setup](../deploy/relay-service/JETSON_SETUP.md) for full details.

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
      - MEDIAMTX_INTERNAL=localhost:8555
      - MEDIAMTX_EXTERNAL=localhost:8555
    restart: unless-stopped
```

2. Add routing in `deploy/nginx.conf`. Since host networking cannot use Docker DNS, you need explicit port routing:

```nginx
location ~ ^/api/(robot-[^/]+)/(.*)$ {
    set $robot_id $1;
    set $api_path $2;

    # Route to correct backend port based on robot ID
    set $backend "127.0.0.1:5001";
    if ($robot_id = "robot-b") {
        set $backend "127.0.0.1:5002";
    }

    proxy_pass http://$backend/api/$api_path$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

3. Restart:
```bash
docker compose -f docker-compose.prod.yaml up -d
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

### Manual Build

```bash
docker build -t visual-patrol .
```

### Dockerfile Details

1. Base: `python:3.10-slim`
2. System deps: gcc, g++, cmake, ffmpeg, gosu, OpenCV deps
3. Python deps: Installed via `uv pip` (fast resolver)
4. Source: Copies `src/` directory
5. Frontend libs: Downloads Chart.js and marked.js from CDN at build time
6. User: Creates `appuser` (UID 1000) for non-root execution
7. Entrypoint: `entrypoint.sh` fixes volume permissions then drops to `appuser` via `gosu`

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
    └── robot-a_relay_manager.log
```

## Networking Comparison

| Aspect | Dev (Bridge) | Prod (Host) |
|--------|-------------|-------------|
| `network_mode` | (default bridge) | `host` |
| nginx port | `ports: 5000:5000` | Listens on host:5000 |
| Flask ports | All internal 5000 | Unique per robot (5001, 5002...) |
| Service discovery | Docker DNS | Explicit `127.0.0.1:PORT` |
| `MEDIAMTX_INTERNAL` | `mediamtx:8554` (Docker DNS) | `localhost:8555` |
| `MEDIAMTX_EXTERNAL` | `localhost:8554` (host port) | `localhost:8555` |
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
| mediamtx port conflict | Change `MTX_RTSPADDRESS` and update `MEDIAMTX_*` env vars to match |
| Live monitor not working | Check `logs/{robot-id}_edge_ai_service.log`; verify VILA JPS is running (`/api/edge_ai/health`) |
| ffmpeg relay crashing | Check `logs/{robot-id}_relay_manager.log`; verify mediamtx is running |
| Relay service unreachable | Check `RELAY_SERVICE_URL` env var; verify relay service is running: `curl http://localhost:5020/health`; VP falls back to local ffmpeg automatically |
| NVENC encoder not working | Check `USE_NVENC` env var; verify `--runtime=nvidia`; check relay service logs for encoder errors; set `USE_NVENC=false` to fallback to libx264 |
| Permission denied on data/logs | The entrypoint script runs `chown` automatically; check if `gosu` is installed |
| Stale robot entries in DB | Can happen if `ROBOT_ID` env var is missing (defaults to `"default"`) |
