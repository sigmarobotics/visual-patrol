# Visual Patrol — Architecture

## Overview

Visual Patrol is a multi-robot autonomous patrol system that combines **Kachaka mobile robots** with **Google Gemini Vision AI** and optional **VILA JPS edge AI** for environment monitoring and anomaly detection.

The system follows a **per-robot backend** architecture: each robot runs its own Flask process, sharing a common SQLite database via WAL mode. An nginx reverse proxy multiplexes all robots behind a single port, routing requests by robot ID extracted from the URL path.

## System Architecture

```mermaid
graph TB
    subgraph Browser["Browser"]
        UI["SPA Dashboard<br/>Robot selector + 6 tabs"]
    end

    subgraph Nginx["nginx (:5000)"]
        Router["URL Router<br/>/api/{robot-id}/... → backend<br/>/api/... → robot-a (global)"]
    end

    subgraph Docker["Docker Services"]
        RobotA["robot-a<br/>Flask :5000"]
        RobotB["robot-b<br/>Flask :5000"]
        RobotC["robot-c<br/>Flask :5000"]
    end

    subgraph PerRobot["Per-Robot Components (inside each Flask)"]
        RS["RobotService<br/>gRPC polling thread"]
        FH["FrameHub<br/>Camera frame cache"]
        PS["PatrolService<br/>Waypoint execution"]
        Cloud["CloudAIService<br/>Gemini API"]
        Edge["EdgeAIService<br/>VILA JPS WebSocket"]
        RM["RelayManager<br/>Jetson HTTP client"]
        VR["VideoRecorder<br/>MP4 recording"]
    end

    subgraph Shared["Shared Resources"]
        DB[(SQLite DB<br/>WAL mode)]
        Settings["global_settings<br/>(DB table)"]
        Images["data/images/<br/>Patrol snapshots"]
    end

    subgraph Jetson["Jetson Host"]
        Relay["relay_service<br/>ffmpeg transcode"]
        MTX["mediamtx<br/>RTSP server"]
        VILA["VILA JPS<br/>Edge VLM"]
    end

    subgraph CloudSvc["Cloud"]
        Gemini["Google Gemini API"]
        Supabase["Supabase"]
    end

    UI --> Nginx --> Docker
    RobotA --- PerRobot
    PerRobot --> DB
    PerRobot --> Settings
    FH -->|"RTSP push"| MTX
    RM -->|"HTTP"| Relay --> MTX --> VILA
    Cloud --> Gemini
    PS -->|"sync"| Supabase
```

## Request Routing

nginx uses a regex pattern to extract the robot ID from the URL and proxy to the matching Docker service:

```
^/api/(robot-[^/]+)/(.*)$  →  http://$robot_svc:5000/api/$api_path
```

- Docker service names **must** match robot IDs (`robot-a`, `robot-b`, etc.)
- Global endpoints (`/api/settings`, `/api/robots`, `/api/history`) proxy to `robot-a` since all backends share the same database
- Adding a robot = add a Docker service + restart

## Per-Robot Backend Components

Each Flask backend instantiates these services at startup:

```mermaid
graph LR
    subgraph Flask["Flask Process"]
        App["app.py<br/>REST API routes"]
        RS["RobotService<br/>Background gRPC poller"]
        FH["FrameHub<br/>Camera frame cache<br/>+ ffmpeg RTSP push"]
        PS["PatrolService<br/>Waypoint executor"]
        AI["CloudAIService<br/>Gemini structured output"]
        Edge["EdgeAIService<br/>VILA JPS client"]
        RM["RelayManager<br/>Jetson HTTP"]
        VR["VideoRecorder"]
        SS["SettingsService<br/>DB-backed config"]
        PDF["PDFService"]
        Sync["SyncService<br/>Supabase"]
    end

    App --> RS
    App --> FH
    App --> PS
    PS --> RS
    PS --> FH
    PS --> AI
    PS --> Edge
    PS --> VR
    PS --> Sync
    App --> SS
    App --> PDF
    FH --> RM
```

### RobotService

- Background thread polls Kachaka robot via gRPC every 100ms
- Maintains cached state: battery, pose, map image, map metadata
- Provides blocking command methods: `move_to()`, `return_home()`, `cancel_command()`
- Auto-reconnects on gRPC failure with 2s backoff
- **Uses `kachaka_api.KachakaApiClient` directly** (not `kachaka_core`)

### FrameHub

- Single gRPC polling thread captures camera frames into an in-memory cache
- Serves MJPEG streams for the web UI (`/api/{id}/camera/front`)
- On-demand ffmpeg subprocess pushes RTSP stream to Jetson mediamtx (2 fps)
- Thread-safe frame access via lock
- All consumers (MJPEG, Gemini snapshot, video recorder, RTSP push) read from the same cache

### PatrolService

- Executes patrol as a background thread
- For each waypoint: move robot → capture frame → Gemini analysis → save result
- Supports turbo mode (async AI analysis — robot moves while images process)
- Manages edge AI lifecycle (register/deregister streams, WebSocket connection)
- Handles video recording start/stop around patrol

### CloudAIService

- Wraps Google Gemini API for structured image analysis
- Returns `{is_ng: bool, description: str}` per waypoint
- Generates patrol reports, Telegram messages, multi-day aggregated reports
- Handles video analysis (upload MP4 → Gemini Files API → analyze)
- Token usage tracking per call

### EdgeAIService

- Manages VILA JPS integration for real-time monitoring during patrol
- Registers/deregisters RTSP streams with JPS API
- Connects WebSocket for alert events
- Stores alerts in `edge_ai_alerts` table
- Also provides standalone test mode via `/api/{id}/test_edge_ai/*`

## Image Intelligence Pipeline

Three parallel AI processing paths from a single camera source:

```mermaid
graph TD
    CAM["Robot Camera (gRPC)"] --> FH["FrameHub Cache"]

    FH --> PATH1["① Waypoint Snapshot"]
    FH --> PATH2["② Video Recorder"]
    FH --> PATH3["③ RTSP Push (2fps)"]

    PATH1 -->|"JPEG"| GEMINI1["Gemini: Structured Inspection<br/>~3-5s per point"]
    PATH2 -->|"MP4"| GEMINI2["Gemini: Video Analysis<br/>~5-30min"]
    PATH3 --> MTX["mediamtx"] --> VILA["VILA JPS: Real-time Alert<br/>~1-2s"]

    GEMINI1 --> DB[(SQLite)]
    GEMINI2 --> DB
    VILA -->|"WebSocket"| DB
```

| # | Mode | Trigger | AI | Latency | Output |
|---|------|---------|----|---------|--------|
| ① | Waypoint Inspection | Robot arrives at point | Gemini (Cloud) | ~3-5s | Structured JSON (OK/NG) |
| ② | Video Analysis | Patrol completes | Gemini (Cloud) | ~5-30min | Narrative summary |
| ③ | Real-time Alert | Continuous | VILA JPS (Edge) | ~1-2s | WebSocket alert + photo |

## Database Schema

```mermaid
erDiagram
    patrol_runs {
        INTEGER id PK
        TEXT start_time
        TEXT end_time
        TEXT status
        TEXT robot_id
        TEXT report_content
        TEXT model_id
        INTEGER total_tokens
    }

    inspection_results {
        INTEGER id PK
        INTEGER run_id FK
        TEXT robot_id
        TEXT point_name
        TEXT ai_response
        INTEGER is_ng
        TEXT image_path
        INTEGER total_tokens
    }

    generated_reports {
        INTEGER id PK
        TEXT start_date
        TEXT end_date
        TEXT report_content
        TEXT robot_id
    }

    edge_ai_alerts {
        INTEGER id PK
        INTEGER run_id FK
        TEXT robot_id
        TEXT rule
        TEXT response
        TEXT image_path
    }

    robots {
        TEXT robot_id PK
        TEXT robot_name
        TEXT robot_ip
        TEXT last_seen
        TEXT status
    }

    global_settings {
        TEXT key PK
        TEXT value
    }

    patrol_runs ||--o{ inspection_results : "has"
    patrol_runs ||--o{ edge_ai_alerts : "has"
```

## Threading Model

```mermaid
graph TD
    Main["Main Thread<br/>Flask WSGI"]
    Poll["RobotService Thread<br/>gRPC poll (100ms)"]
    FHThread["FrameHub Thread<br/>Camera poll"]
    FFmpeg["ffmpeg Subprocess<br/>RTSP push"]
    Patrol["Patrol Thread<br/>Waypoint execution"]
    VRThread["VideoRecorder Thread<br/>Frame → MP4"]
    EdgeWS["EdgeAI Thread<br/>WebSocket listener"]

    Main -.->|"spawn"| Poll
    Main -.->|"spawn"| FHThread
    FHThread -.->|"spawn"| FFmpeg
    Main -.->|"spawn on start"| Patrol
    Patrol -.->|"spawn"| VRThread
    Patrol -.->|"spawn"| EdgeWS
```

All threads are daemon threads — they auto-exit when the Flask process stops.

## Networking Modes

```mermaid
graph TB
    subgraph Dev["Development (Docker Bridge)"]
        DN["nginx :5000"] --> DA["robot-a :5000<br/>(internal)"]
        DN --> DB2["robot-b :5000<br/>(internal)"]
    end

    subgraph Prod["Production (Host Network)"]
        PN["nginx :5000"] --> PA["robot-a :5001"]
        PN --> PB["robot-b :5002"]
        PN --> PC["robot-c :5003"]
        PN --> PR["rtsp-relay :5020"]
    end
```

- **Development**: Docker bridge network, DNS resolves service names, all backends on port 5000
- **Production**: Host networking, each backend on a unique `PORT` env var, direct localhost access

## Configuration

Settings are stored in SQLite (`global_settings` table) and managed via the web UI:

| Category | Settings |
|----------|----------|
| General | timezone, turbo_mode, idle_stream, telegram config |
| Gemini AI | API key, model, system prompt, report prompts, video prompt |
| VILA / Edge AI | enable, stream source, jetson_host, RTSP URL, alert rules |

Per-robot config is set via environment variables in `docker-compose.yml`:

| Variable | Purpose |
|----------|---------|
| `ROBOT_ID` | Robot identifier (must match Docker service name) |
| `ROBOT_NAME` | Display name |
| `ROBOT_IP` | Kachaka robot IP:port |
| `PORT` | Flask port (default 5000, unique per robot in production) |
| `RELAY_SERVICE_URL` | Jetson relay service URL |

## Cloud Sync (Supabase)

Optional integration syncs patrol data to Supabase for cross-device access:

- Patrol runs, inspection results, and robot status synced after each patrol
- Cloud dashboard deployed on Vercel (`cloud-dashboard/`)
- Configured via `SUPABASE_URL` and `SUPABASE_KEY` environment variables

## CI/CD

GitHub Actions builds multi-arch Docker images on every push to `main`:

- Platforms: `linux/amd64`, `linux/arm64`
- Registry: `ghcr.io/sigmarobotics/visual-patrol`
- Separate workflow for relay service image
