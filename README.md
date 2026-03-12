# Visual Patrol

> **Note:** This project uses `kachaka_api.KachakaApiClient` directly instead of the [`kachaka-sdk-toolkit`](https://github.com/sigmarobotics/kachaka-sdk-toolkit) (`kachaka_core`) best practices — no connection pooling, no `@with_retry`, no `CameraStreamer`, no `RobotController`. A migration to `kachaka_core` is planned.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-green)
![Docker](https://img.shields.io/badge/Docker-Enabled-blue)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-orange)
![Platform](https://img.shields.io/badge/Platform-amd64%20%7C%20arm64-lightgrey)

Autonomous multi-robot visual patrol system integrating **Kachaka Robot** with **Google Gemini Vision AI** for intelligent environment monitoring and anomaly detection. A single web dashboard controls multiple robots through an nginx reverse proxy, with each robot running an isolated Flask backend sharing a common SQLite database.

## Features

- **Multi-Robot Support** — Single dashboard controls multiple robots via dropdown selector
- **Autonomous Patrol** — Define waypoints per robot and navigate automatically
- **AI-Powered Inspection** — Gemini Vision analyzes camera images at each waypoint (structured JSON output)
- **Live Monitoring (VILA JPS)** — Continuous camera monitoring via RTSP relay + VILA JPS with WebSocket alerts
- **Centralized Frame Hub** — Single gRPC polling thread feeds an in-memory frame cache for all consumers
- **RTSP Camera Relay** — Robot camera and external RTSP cameras relayed through Jetson relay service + mediamtx
- **Video Recording** — Record patrol footage with codec auto-detection (H.264 / XVID / MJPEG)
- **Real-time Dashboard** — Live map, robot position, battery, camera streams across 6 tabs
- **Scheduled Patrols** — Recurring patrol times with day-of-week filtering
- **Multi-run Analysis Reports** — AI-powered aggregated reports across date ranges
- **PDF Reports** — Server-side PDF generation with Markdown and CJK support
- **Cloud Sync** — Supabase integration for cross-device patrol data synchronization
- **Telegram Notifications** — Send patrol reports, PDFs, and live alert photos
- **Manual Control** — Web-based remote control with D-pad navigation
- **History & Token Analytics** — Browse past patrols with token usage statistics and pricing estimates

## Architecture

```mermaid
graph TB
    subgraph Browser["Browser (http://localhost:5000)"]
        UI["SPA Dashboard<br/>Robot selector dropdown<br/>6 tabs: Patrol, Control, History,<br/>Reports, Tokens, Settings"]
    end

    subgraph Nginx["nginx (port 5000)"]
        Static["/ → index.html<br/>/static/ → CSS/JS"]
        RobotProxy["/api/{robot-id}/... → proxy"]
        GlobalProxy["/api/... → robot-a"]
    end

    subgraph Backends["Docker Services (shared SQLite WAL)"]
        RobotA["robot-a (Flask:5000)<br/>↔ Kachaka Robot A"]
        RobotB["robot-b (Flask:5000)<br/>↔ Kachaka Robot B"]
        FrameHub["frame_hub<br/>gRPC → frame cache<br/>→ ffmpeg RTSP push"]
    end

    subgraph Jetson["Jetson Host"]
        Relay["relay_service (port 5020)<br/>ffmpeg transcode"]
        MediaMTX["mediamtx (port 8555)<br/>RTSP server"]
        VILA["VILA JPS<br/>API :5010 / WS :5016"]
    end

    subgraph Cloud["Cloud Services"]
        Gemini["Google Gemini<br/>Vision AI"]
        Supabase["Supabase<br/>Cloud DB"]
    end

    UI --> Nginx
    Nginx --> RobotA
    Nginx --> RobotB
    RobotA --- FrameHub
    FrameHub -->|"RTSP push"| MediaMTX
    Relay -->|"transcode"| MediaMTX
    MediaMTX --> VILA
    RobotA -->|"image analysis"| Gemini
    RobotA -->|"sync"| Supabase
```

## Patrol Flow

```mermaid
sequenceDiagram
    participant User
    participant Flask as Flask Backend
    participant Robot as Kachaka Robot
    participant FH as frame_hub
    participant AI as Gemini AI
    participant DB as SQLite

    User->>Flask: POST /patrol/start
    Flask->>DB: Create patrol_run
    Flask->>FH: start_rtsp_push()

    loop Each Waypoint
        Flask->>Robot: move_to(x, y, theta)
        Robot-->>Flask: Arrival confirmed
        FH-->>Flask: Latest frame
        Flask->>AI: Analyze image
        AI-->>Flask: {is_NG, description}
        Flask->>DB: Save inspection_result
    end

    Flask->>AI: Generate patrol report
    Flask->>DB: Save report

    opt Telegram enabled
        Flask-->>User: Telegram notification + PDF
    end

    Flask->>FH: stop_rtsp_push()
    Flask->>DB: Update patrol_run (completed)
```

## Image Intelligence Pipeline

```mermaid
graph LR
    CAM["Robot Camera<br/>(gRPC)"] --> FH["frame_hub<br/>Frame Cache"]

    FH --> SNAP["Snapshot<br/>(per waypoint)"]
    FH --> VR["Video Recorder<br/>(full patrol)"]
    FH --> RTSP["RTSP Push<br/>(2 fps)"]

    SNAP -->|"JPEG"| IMG["① Cloud VLM<br/>Waypoint Inspection<br/>~3-5s"]
    VR -->|"MP4"| VID["② Cloud VLM<br/>Video Analysis<br/>~5-30min"]
    RTSP --> MTX["mediamtx"] --> EDGE["③ Edge VLM<br/>Real-time Alert<br/>~1-2s"]
```

| # | Mode | Trigger | AI | Latency | Output |
|---|------|---------|----|---------|--------|
| ① | Waypoint Inspection | Robot arrives at point | Gemini (Cloud) | ~3-5s | Structured JSON (OK/NG) |
| ② | Video Analysis | Patrol completes | Gemini (Cloud) | ~5-30min | Narrative summary |
| ③ | Real-time Alert | Continuous | VILA JPS (Edge) | ~1-2s | WebSocket alert + photo |

## Quick Start

```bash
docker compose up -d
```

Open [http://localhost:5000](http://localhost:5000), go to **Settings** and configure:

1. **Google Gemini API Key** (Gemini AI tab)
2. **Timezone** (General tab)
3. **Live Monitor** (optional): Select stream source, set Jetson Host IP, define alert rules

Robot IPs are set per-service in `docker-compose.yml` via the `ROBOT_IP` environment variable.

### Adding a New Robot

Add a service to `docker-compose.yml`:

```yaml
  robot-d:
    container_name: visual_patrol_robot_d
    build: .
    volumes:
      - ./src:/app/src
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - ROBOT_ID=robot-d
      - ROBOT_NAME=Robot D
      - ROBOT_IP=<robot-ip>:26400
    restart: unless-stopped
```

Add `robot-d` to nginx `depends_on`, then `docker compose up -d`.

## Project Structure

```
visual-patrol/
├── nginx.conf                  # Dev reverse proxy
├── docker-compose.yml          # Dev: nginx + per-robot services
├── Dockerfile                  # Python 3.10, non-root
├── src/
│   ├── backend/
│   │   ├── app.py              # Flask REST API
│   │   ├── robot_service.py    # Kachaka gRPC interface
│   │   ├── patrol_service.py   # Patrol orchestration
│   │   ├── cloud_ai_service.py # Gemini AI integration
│   │   ├── edge_ai_service.py  # VILA JPS live monitoring
│   │   ├── frame_hub.py        # gRPC poll → frame cache → RTSP push
│   │   ├── relay_manager.py    # Jetson relay HTTP client
│   │   ├── sync_service.py     # Supabase cloud sync
│   │   ├── settings_service.py # DB-backed settings
│   │   ├── pdf_service.py      # PDF report generation
│   │   ├── database.py         # SQLite + migrations
│   │   ├── video_recorder.py   # Patrol video recording
│   │   ├── config.py           # Per-robot env config
│   │   ├── logger.py           # Timezone-aware logging
│   │   └── utils.py
│   └── frontend/
│       ├── templates/index.html  # SPA (no framework)
│       └── static/
│           ├── css/style.css
│           └── js/              # app, state, map, patrol, etc.
├── deploy/                     # Production configs
│   ├── docker-compose.prod.yaml
│   ├── nginx.conf
│   └── relay-service/          # Jetson ffmpeg relay
├── cloud-dashboard/            # Supabase cloud dashboard (Vercel)
└── .github/workflows/          # CI: multi-arch Docker → GHCR
```

## Deployment

```mermaid
graph TB
    subgraph Dev["Development (Docker Bridge)"]
        DN["nginx :5000"] --> DA["robot-a :5000"]
        DN --> DB2["robot-b :5000"]
    end

    subgraph Prod["Production (Host Network)"]
        PN["nginx :5000"] --> PA["robot-a :5001"]
        PN --> PB["robot-b :5002"]
        PN --> PR["rtsp-relay :5020"]
    end
```

```bash
# Production (host networking)
docker compose -f deploy/docker-compose.prod.yaml up -d
```

Docker images are built for **linux/amd64** and **linux/arm64** on every push to `main`.

See [docs/deployment.md](docs/deployment.md) for full production setup.

## Local Development

```bash
uv pip install --system -r src/backend/requirements.txt

export DATA_DIR=$(pwd)/data LOG_DIR=$(pwd)/logs
export ROBOT_ID=robot-a ROBOT_NAME="Robot A"
export ROBOT_IP=<robot-ip>:26400

python src/backend/app.py
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, request flow, threading model |
| [架構文件](docs/zh/architecture.md) | 系統架構（中文） |
| [API Reference](docs/api-reference.md) | All REST endpoints |
| [Frontend Guide](docs/frontend.md) | Module structure, state management |
| [Backend Guide](docs/backend.md) | Services, database schema |
| [Deployment](docs/deployment.md) | Dev and production setup |
| [Configuration](docs/configuration.md) | Environment variables, settings |
| [Jetson Debug](docs/jetson-debug-guide.md) | RTSP relay + VILA JPS debugging |

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Copyright 2026 Sigma Robotics
