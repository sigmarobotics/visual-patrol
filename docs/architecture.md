# System Architecture

## 1. System Overview

Visual Patrol is a multi-robot autonomous inspection system. A web-based SPA connects through an nginx reverse proxy to per-robot Flask backend instances, all sharing a common SQLite database (WAL mode). Each backend communicates with its assigned Kachaka robot via gRPC and can leverage both cloud AI (Google Gemini) and edge AI (VILA JPS on Jetson) for visual analysis.

```mermaid
graph TD
    subgraph Browser
        SPA["SPA (6 tabs)<br/>Patrol | Control | History<br/>Reports | Tokens | Settings"]
    end

    subgraph "nginx (port 5000)"
        PROXY["Reverse Proxy<br/>Static file serving<br/>Robot-ID routing"]
    end

    subgraph "Flask Backends (one per robot)"
        FA["robot-a<br/>Flask + frame_hub<br/>+ patrol_service"]
        FB["robot-b<br/>Flask + frame_hub<br/>+ patrol_service"]
        FC["robot-c<br/>Flask + frame_hub<br/>+ patrol_service"]
    end

    subgraph "Kachaka Robots (gRPC)"
        KA["Kachaka A"]
        KB["Kachaka B"]
        KC["Kachaka C"]
    end

    subgraph "Shared Storage"
        DB[("SQLite DB (WAL)<br/>data/report/report.db")]
    end

    subgraph "Jetson Orin NX"
        MTX["mediamtx<br/>RTSP server<br/>port 8555"]
        JPS["VILA JPS<br/>API :5010<br/>WS :5016<br/>Metrics :5012"]
        RELAY["relay_service<br/>ffmpeg transcode<br/>port 5020"]
    end

    subgraph "External Services"
        GEMINI["Google Gemini API"]
        TG["Telegram Bot API"]
    end

    SPA --> PROXY
    PROXY --> FA
    PROXY --> FB
    PROXY --> FC
    FA --> KA
    FB --> KB
    FC --> KC
    FA --> DB
    FB --> DB
    FC --> DB
    FA -. "RTSP push (2fps)" .-> MTX
    FB -. "RTSP push (2fps)" .-> MTX
    MTX --> JPS
    RELAY --> MTX
    FA -. "Cloud AI" .-> GEMINI
    FB -. "Cloud AI" .-> GEMINI
    FA -. "Notifications" .-> TG
    JPS -. "WS alerts" .-> FA
    JPS -. "WS alerts" .-> FB
```

## 2. Image Inspection Architecture

### 2a. Cloud AI (Gemini)

Each patrol point inspection follows a synchronous (or turbo-mode async) pipeline: capture a frame from the gRPC cache, send it to the Gemini API with a structured output schema, parse the `is_NG`/`Description` response, and store the result in the database.

```mermaid
sequenceDiagram
    participant PS as patrol_service
    participant FH as frame_hub
    participant Robot as Kachaka (gRPC)
    participant AI as cloud_ai_service
    participant Gemini as Gemini API
    participant DB as SQLite

    PS->>FH: wait_for_fresh_frame()
    FH-->>PS: frame (from cache)
    Note over FH,Robot: frame_hub polls gRPC at 10fps,<br/>cache always has latest frame

    PS->>PS: Save JPEG to disk
    PS->>AI: generate_inspection(image, prompt, system_prompt)
    AI->>Gemini: generate_content()<br/>response_schema=InspectionResult<br/>response_mime_type=application/json
    Gemini-->>AI: {"is_NG": bool, "Description": "..."}
    AI-->>PS: {result: {is_NG, Description}, usage: {tokens}}

    PS->>PS: parse_ai_response() -> is_ng, description, tokens
    PS->>PS: Rename image file (point_OK/NG_uuid.jpg)
    PS->>DB: INSERT INTO inspection_results<br/>(run_id, point_name, ai_response, is_ng, image_path, tokens)
```

**Turbo mode**: When enabled, `_inspect_point` enqueues tasks to `inspection_queue` instead of blocking. The `_inspection_worker` background thread processes them asynchronously, allowing the robot to move to the next point immediately. The queue is drained before the patrol finishes via `inspection_queue.join()`.

### 2b. Edge AI (VILA JPS on Jetson)

Edge AI uses VILA JPS running on a Jetson Orin NX for continuous, real-time VLM monitoring of RTSP streams. The system registers streams, sets alert rules, and listens for WebSocket events.

```mermaid
sequenceDiagram
    participant PS as patrol_service
    participant FH as frame_hub
    participant MTX as mediamtx (Jetson)
    participant JPS as VILA JPS
    participant WS as WebSocket (5016)
    participant EAI as edge_ai_service
    participant DB as SQLite
    participant TG as Telegram

    PS->>FH: start_rtsp_push(jetson:8555, /robot-a/camera)
    FH->>FH: Start ffmpeg (image2pipe -> libx264 -> RTSP TCP)
    FH->>MTX: Push JPEG frames at 2fps

    PS->>FH: wait_for_push_ready()
    FH-->>PS: ready (frames_fed > 0)

    PS->>EAI: edge_ai_monitor.start(run_id, config)
    EAI->>JPS: GET /api/v1/live-stream (cleanup stale)
    EAI->>JPS: POST /api/v1/live-stream<br/>{liveStreamUrl, name}
    Note over EAI,JPS: Retry 3x with 5s delay<br/>(gstDecoder cold start ~30s)
    JPS-->>EAI: {id: stream_id}
    EAI->>JPS: POST /api/v1/alerts<br/>{alerts: [...rules], id: stream_id}
    Note over EAI,JPS: Timeout 60s (VILA model warm-up)

    EAI->>WS: Connect ws://jetson:5016/api/v1/alerts/ws
    Note over EAI,WS: Reconnects up to 10x<br/>with 5s delay

    loop Continuous monitoring
        JPS->>MTX: Pull RTSP stream
        JPS->>JPS: VLM evaluates alert rules
        JPS->>WS: Alert event (rule triggered)
        WS->>EAI: {rule_string, stream_id, alert_id}
        EAI->>EAI: Cooldown check (60s per rule+stream)
        EAI->>FH: get_latest_frame() (evidence capture)
        EAI->>DB: INSERT INTO edge_ai_alerts
        EAI->>TG: sendPhoto (evidence + caption)
    end

    PS->>EAI: edge_ai_monitor.stop()
    EAI->>WS: Close connection
    EAI->>JPS: DELETE /api/v1/live-stream/{id}
    PS->>FH: stop_rtsp_push()
```

## 3. Robot Disconnection Handling

The system operates over mesh Wi-Fi networks where brief gRPC disconnections are expected. `robot_service.py` is designed for resilience with automatic reconnection at every layer.

```mermaid
stateDiagram-v2
    [*] --> Polling: RobotService.__init__()

    state Polling {
        [*] --> Connected
        Connected --> Connected: Poll pose + battery (100ms)
        Connected --> Disconnected: gRPC exception
        Disconnected --> Connected: gRPC success
        Disconnected --> Disconnected: Wait 2s, retry
    }

    state "connected flag" as CF {
        [*] --> Online: gRPC success
        Online --> Offline: gRPC exception
        Offline --> Online: gRPC success
    }

    note right of CF
        Drives heartbeat (30s)
        which updates robots table
        online/offline status
    end note
```

### 3-Phase Move Command

`move_to()` and `return_home()` use a 3-phase pattern to handle transient gRPC failures without accidentally re-sending movement commands.

```mermaid
flowchart TD
    A["Phase 1: Send Command"] --> B{"gRPC success?"}
    B -- No --> C["Log warning, sleep 2s"]
    C --> A
    B -- Yes --> D["Phase 2: Wait for Acceptance"]

    D --> E{"is_command_running()?"}
    E -- No --> F{"Retries < 20?<br/>(up to 10s)"}
    F -- Yes --> G["Sleep 0.5s"] --> E
    F -- No --> H["Phase 3: Get Result"]
    E -- Yes --> I["Command accepted"]

    I --> J{"is_command_running()?"}
    J -- Yes --> K["Sleep 0.5s"] --> J
    J -- No --> H

    H --> L{"get_last_command_result()"}
    L -- Success --> M["Return result"]
    L -- Exception --> N["Return gracefully<br/>(use Phase 1 result)"]

    style A fill:#e1f5fe
    style D fill:#fff3e0
    style H fill:#e8f5e9
```

**Key design decisions:**
- Phase 1 retries indefinitely (mesh may be down for minutes)
- Phase 2 waits for Kachaka to acknowledge the command before polling completion (prevents reading stale state from the previous command)
- Phase 3 fails gracefully -- the robot has already moved, so losing the result is acceptable
- `return_home()` uses the identical 3-phase pattern

## 4. Frame Hub Architecture

The frame hub (`frame_hub.py`) centralizes all camera access through a single gRPC polling thread and shared frame cache. This eliminates redundant gRPC calls that previously caused contention.

```mermaid
graph TD
    subgraph "gRPC Source"
        ROBOT["Kachaka Robot<br/>get_front_camera_ros_compressed_image()"]
    end

    subgraph "frame_hub"
        POLL["_poll_loop<br/>~10fps (100ms interval)"]
        CACHE["Frame Cache<br/>_latest_frame + _frame_lock"]
        FEEDER["_feeder_loop<br/>2fps (500ms interval)"]
        MONITOR["_monitor_push<br/>Health check every 5s"]
        FFMPEG["ffmpeg subprocess<br/>image2pipe -> libx264 ultrafast<br/>-> RTSP TCP"]
    end

    subgraph "Consumers (read from cache)"
        MJPEG["MJPEG Stream<br/>Frontend live feed"]
        GEMINI["Gemini Inspection<br/>cloud_ai_service"]
        VIDEO["Video Recorder<br/>OpenCV capture"]
        EVIDENCE["Evidence Capture<br/>edge_ai_service"]
    end

    subgraph "Jetson"
        MTX["mediamtx<br/>/{robot-id}/camera"]
    end

    ROBOT --> POLL
    POLL --> CACHE
    CACHE --> MJPEG
    CACHE --> GEMINI
    CACHE --> VIDEO
    CACHE --> EVIDENCE
    CACHE --> FEEDER
    FEEDER --> FFMPEG
    FFMPEG --> MTX
    MONITOR -. "restart if dead" .-> FFMPEG
```

**Polling lifecycle** -- determined by `_evaluate()`:

```mermaid
flowchart LR
    A{"Patrol active?"} -- Yes --> POLL["Polling ON"]
    A -- No --> B{"enable_idle_stream?"}
    B -- Yes --> POLL
    B -- No --> STOP["Polling OFF<br/>(zero gRPC bandwidth)"]
```

**ffmpeg push command:**
```
ffmpeg -y -f image2pipe -framerate 2 -i pipe:0
  -vf scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2
  -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -level 3.1
  -pix_fmt yuv420p -x264-params keyint=1:min-keyint=1:repeat-headers=1
  -bsf:v dump_extra -f rtsp -rtsp_transport tcp rtsp://{host}:{port}/{path}
```

## 5. RTSP Relay Layer

Two distinct pipelines feed RTSP streams to mediamtx for VILA JPS consumption.

```mermaid
graph LR
    subgraph "Pipeline 1: Robot Camera (direct push)"
        FH["frame_hub<br/>(in Flask backend)"]
        FF1["ffmpeg<br/>image2pipe -> libx264<br/>ultrafast, baseline"]
    end

    subgraph "Pipeline 2: External RTSP (via relay)"
        EXT["External RTSP Source"]
        RS["relay_service.py<br/>(port 5020, on Jetson)"]
        FF2["ffmpeg<br/>RTSP in -> h264_nvmpi<br/>(or libx264 fallback)"]
    end

    subgraph "Jetson"
        MTX["mediamtx<br/>port 8555"]
        JPS["VILA JPS"]
    end

    FH --> FF1
    FF1 -- "/{robot-id}/camera" --> MTX
    EXT --> RS
    RS --> FF2
    FF2 -- "/{robot-id}/external" --> MTX
    MTX --> JPS
```

| Property | Robot Camera (direct push) | External RTSP (via relay) |
|----------|---------------------------|---------------------------|
| Source | gRPC frame cache (JPEG) | RTSP URL |
| Encoder | libx264 (CPU, ultrafast) | h264_nvmpi (NVENC) or libx264 fallback |
| Frame rate | 2fps (from feeder loop) | Source native |
| Profile | H.264 Baseline | H.264 Baseline |
| mediamtx path | `/{robot-id}/camera` | `/{robot-id}/external` |
| Managed by | frame_hub (in Flask) | relay_service (on Jetson) |
| Evidence capture | gRPC `get_latest_frame()` | OpenCV RTSP from mediamtx |

## 6. Request Flow

### Robot-Specific vs Global Requests

```mermaid
flowchart TD
    subgraph "Browser"
        REQ1["GET /api/robot-a/state"]
        REQ2["GET /api/settings"]
    end

    subgraph "nginx"
        R1["Regex: ^/api/(robot-a)/(.*)$<br/>Strip prefix, proxy to robot-a"]
        R2["Catch-all: /api/<br/>Proxy to robot-a (any backend)"]
    end

    subgraph "Flask Backends"
        FA["robot-a Flask<br/>Handles /api/state"]
        FB["robot-b Flask"]
    end

    subgraph "Shared DB"
        DB[("SQLite WAL")]
    end

    REQ1 --> R1
    R1 --> FA
    FA --> DB

    REQ2 --> R2
    R2 --> FA
    FA --> DB

    style R1 fill:#e1f5fe
    style R2 fill:#fff3e0
```

**URL convention:**
- **Robot-specific**: `fetch(/api/${state.selectedRobotId}/endpoint)` -- nginx strips robot-id prefix, routes to the matching backend
- **Global**: `fetch('/api/endpoint')` -- proxied to robot-a (any backend works, shared DB)

**Global endpoints**: `/api/settings`, `/api/robots`, `/api/history`, `/api/stats`, `/api/reports`

**Robot-specific endpoints**: `/api/state`, `/api/map`, `/api/move`, `/api/patrol/*`, `/api/points/*`, `/api/camera/*`, `/api/test_ai`, `/api/images/*`

## 7. Database Schema

```mermaid
erDiagram
    patrol_runs {
        INTEGER id PK
        TEXT start_time
        TEXT end_time
        TEXT status
        TEXT robot_serial
        TEXT report_content
        TEXT model_id
        TEXT video_path
        TEXT video_analysis
        TEXT robot_id
        INTEGER input_tokens
        INTEGER output_tokens
        INTEGER total_tokens
        INTEGER report_input_tokens
        INTEGER report_output_tokens
        INTEGER report_total_tokens
        INTEGER telegram_input_tokens
        INTEGER telegram_output_tokens
        INTEGER telegram_total_tokens
        INTEGER video_input_tokens
        INTEGER video_output_tokens
        INTEGER video_total_tokens
    }

    inspection_results {
        INTEGER id PK
        INTEGER run_id FK
        TEXT point_name
        REAL coordinate_x
        REAL coordinate_y
        TEXT prompt
        TEXT ai_response
        INTEGER is_ng
        TEXT ai_description
        INTEGER input_tokens
        INTEGER output_tokens
        INTEGER total_tokens
        TEXT image_path
        TEXT timestamp
        TEXT robot_moving_status
        TEXT robot_id
    }

    edge_ai_alerts {
        INTEGER id PK
        INTEGER run_id FK
        TEXT rule
        TEXT response
        TEXT image_path
        TEXT timestamp
        TEXT robot_id
        TEXT stream_source
    }

    generated_reports {
        INTEGER id PK
        TEXT start_date
        TEXT end_date
        TEXT report_content
        INTEGER input_tokens
        INTEGER output_tokens
        INTEGER total_tokens
        TEXT timestamp
        TEXT robot_id
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

    patrol_runs ||--o{ inspection_results : "run_id"
    patrol_runs ||--o{ edge_ai_alerts : "run_id"
```

**Connection settings:** WAL mode, 5000ms busy timeout. All backends share one DB file at `data/report/report.db`. The `robot_id` column on `patrol_runs`, `inspection_results`, `generated_reports`, and `edge_ai_alerts` distinguishes data per robot.

## 8. Threading Model

Each Flask backend runs several background daemon threads.

```mermaid
graph TD
    subgraph "Always Running"
        T1["_polling_loop<br/>robot_service<br/>100ms (pose, battery, map)"]
        T2["_heartbeat_loop<br/>app.py<br/>30s (updates robots table)"]
        T3["_schedule_checker<br/>patrol_service<br/>30s (checks scheduled times)"]
        T4["_inspection_worker<br/>patrol_service<br/>Event-driven (queue.get)"]
    end

    subgraph "During Patrol (frame_hub polling)"
        T5["_poll_loop<br/>frame_hub<br/>100ms (~10fps gRPC)"]
    end

    subgraph "During RTSP Push (edge AI active)"
        T6["_feeder_loop<br/>frame_hub<br/>500ms (2fps to ffmpeg)"]
        T7["_monitor_push<br/>frame_hub<br/>5s (ffmpeg health)"]
        T8["_stderr_reader<br/>frame_hub<br/>Continuous (ffmpeg logs)"]
    end

    subgraph "During Edge AI Monitoring"
        T9["_ws_listener<br/>edge_ai_service<br/>Continuous (VILA WS events)"]
    end

    subgraph "During Video Recording"
        T10["_record_loop<br/>video_recorder<br/>1/fps (OpenCV capture)"]
    end

    subgraph "During Edge AI Test (settings page)"
        T11["_jps_setup<br/>edge_ai_service<br/>One-shot (background setup)"]
        T12["_snapshot_loop<br/>edge_ai_service<br/>500ms (mediamtx RTSP grabs)"]
    end
```

| Thread | Module | Interval | Lifecycle |
|--------|--------|----------|-----------|
| `_polling_loop` | robot_service | 100ms | Always (from init) |
| `_heartbeat_loop` | app.py | 30s | Always (from startup) |
| `_schedule_checker` | patrol_service | 30s | Always (from init) |
| `_inspection_worker` | patrol_service | Event-driven | Always (from init) |
| `_poll_loop` | frame_hub | 100ms | Patrol active OR idle stream enabled |
| `_feeder_loop` | frame_hub | 500ms | RTSP push active |
| `_monitor_push` | frame_hub | 5s | RTSP push active |
| `_stderr_reader` | frame_hub | Continuous | RTSP push active |
| `_ws_listener` | edge_ai_service | Continuous | Edge AI monitoring active |
| `_record_loop` | video_recorder | 1/fps | Video recording active |
| `_jps_setup` | edge_ai_service | One-shot | Edge AI test only |
| `_snapshot_loop` | edge_ai_service | 500ms | Edge AI test only |

## 9. Networking Modes

### Development (WSL2 / Docker Desktop)

```mermaid
graph TD
    subgraph "Host (WSL2)"
        BROWSER["Browser<br/>localhost:5000"]
    end

    subgraph "Docker Bridge Network"
        NGINX["nginx<br/>ports: 5000:5000"]
        RA["robot-a<br/>internal :5000"]
        RB["robot-b<br/>internal :5000"]
        DNS["Docker DNS<br/>resolver 127.0.0.11"]
    end

    subgraph "Physical Network"
        KA["Kachaka A<br/>192.168.x.x:26400"]
        JETSON["Jetson<br/>192.168.x.x"]
    end

    BROWSER --> NGINX
    NGINX -- "Docker DNS lookup" --> DNS
    DNS --> RA
    DNS --> RB
    RA --> KA
    RB --> KA
    RA -. "RTSP push" .-> JETSON
```

- Docker service names must match `ROBOT_ID` values (e.g., service `robot-a` = `ROBOT_ID=robot-a`)
- nginx resolves backends via Docker DNS (`resolver 127.0.0.11`)
- All Flask backends listen on internal port 5000
- `RELAY_SERVICE_URL` points to Jetson relay service (e.g., `http://192.168.50.35:5020`)

### Production (Jetson / Linux host)

```mermaid
graph TD
    subgraph "Host Network (all containers share)"
        NGINX["nginx :5000"]
        RA["robot-a Flask :5001"]
        RB["robot-b Flask :5002"]
        MTX["mediamtx :8555"]
        JPS["VILA JPS :5010, :5012, :5016"]
        RELAY["relay_service :5020"]
    end

    subgraph "Physical Network"
        KA["Kachaka A"]
        KB["Kachaka B"]
    end

    NGINX -- "if robot-id=robot-a<br/>-> 127.0.0.1:5001" --> RA
    NGINX -- "if robot-id=robot-b<br/>-> 127.0.0.1:5002" --> RB
    RA --> KA
    RB --> KB
    RA -- "RTSP push" --> MTX
    RB -- "RTSP push" --> MTX
    MTX --> JPS
    RELAY --> MTX
```

- All containers use `network_mode: host` (required: Jetson has `iptables: false`)
- Each Flask backend uses a unique `PORT` env var (5001, 5002, ...)
- nginx routes by robot ID using explicit `if ($robot_id = "robot-a")` rules to `127.0.0.1:PORT`
- `jetson_host` setting auto-derives all Jetson service URLs
- Adding a robot = add docker-compose service + add nginx `if` block

## 10. Patrol Lifecycle

Full flow from start to finish, including edge AI setup and teardown.

```mermaid
flowchart TD
    START["start_patrol()"] --> VALIDATE["Validate AI configured<br/>+ points exist"]
    VALIDATE -- Fail --> ABORT["Set error status, return"]
    VALIDATE -- OK --> CREATE_RUN["Create patrol_runs record<br/>status = Running"]
    CREATE_RUN --> SETUP_VIDEO{"Video recording<br/>enabled?"}
    SETUP_VIDEO -- Yes --> START_REC["Start VideoRecorder<br/>OpenCV codec auto-detect"]
    SETUP_VIDEO -- No --> SETUP_EDGE
    START_REC --> SETUP_EDGE

    SETUP_EDGE{"Edge AI enabled<br/>+ jetson_host set?"}
    SETUP_EDGE -- Yes --> RTSP_PUSH["frame_hub.start_rtsp_push()<br/>or relay_service.start_relay()"]
    RTSP_PUSH --> WAIT_STREAM["wait_for_push_ready()<br/>or wait_for_stream()"]
    WAIT_STREAM --> JPS_REG["edge_ai_monitor.start()<br/>Register stream + rules + WS"]
    SETUP_EDGE -- No --> PATROL_LOOP

    JPS_REG --> PATROL_LOOP

    subgraph "Main Patrol Loop"
        PATROL_LOOP["For each enabled point"] --> MOVE["robot_service.move_to(x, y, theta)<br/>3-phase resilient move"]
        MOVE -- Fail --> LOG_FAIL["Save move failure to DB<br/>Continue to next point"]
        MOVE -- Success --> FRESH["frame_hub.wait_for_fresh_frame()<br/>+ 2s settle time"]
        FRESH --> INSPECT{"Turbo mode?"}
        INSPECT -- Yes --> QUEUE["Enqueue to inspection_queue"]
        INSPECT -- No --> SYNC["Synchronous Gemini inspection"]
        LOG_FAIL --> CHECK_STOP{"is_patrolling?"}
        QUEUE --> CHECK_STOP
        SYNC --> CHECK_STOP
        CHECK_STOP -- Yes --> PATROL_LOOP
        CHECK_STOP -- No --> CLEANUP
    end

    PATROL_LOOP -- "All points done" --> RETURN_HOME
    RETURN_HOME["robot_service.return_home()<br/>3-phase resilient"]
    RETURN_HOME --> DRAIN{"Turbo mode?"}
    DRAIN -- Yes --> WAIT_QUEUE["inspection_queue.join()"]
    DRAIN -- No --> CLEANUP
    WAIT_QUEUE --> CLEANUP

    subgraph "Cleanup (finally block)"
        CLEANUP["Always executes"] --> STOP_EDGE["edge_ai_monitor.stop()"]
        STOP_EDGE --> STOP_PUSH["frame_hub.stop_rtsp_push()"]
        STOP_PUSH --> STOP_RELAY["relay_service_client.stop_all()"]
        STOP_RELAY --> STOP_REC["recorder.stop()"]
        STOP_REC --> EVAL["frame_hub.set_patrol_active(false)<br/>Re-evaluate polling"]
    end

    EVAL --> VIDEO_ANALYSIS{"Video recorded<br/>+ completed?"}
    VIDEO_ANALYSIS -- Yes --> ANALYZE_VID["ai_service.analyze_video()<br/>Save video tokens"]
    VIDEO_ANALYSIS -- No --> FINALIZE
    ANALYZE_VID --> FINALIZE

    FINALIZE["Update patrol_runs<br/>end_time + status"] --> REPORT["ai_service.generate_report()<br/>Include edge AI alerts"]
    REPORT --> TELEGRAM{"Telegram enabled?"}
    TELEGRAM -- Yes --> TG_MSG["Generate + send<br/>Telegram message + PDF"]
    TELEGRAM -- No --> TOKENS
    TG_MSG --> TOKENS["update_run_tokens()<br/>Aggregate all categories"]
    TOKENS --> DONE["is_patrolling = false"]
```

## 11. Data Model (Filesystem Layout)

```mermaid
graph TD
    subgraph "data/"
        REPORT_DIR["report/"]
        ROBOT_A["robot-a/"]
        ROBOT_B["robot-b/"]
    end

    subgraph "report/"
        DB[("report.db<br/>Shared SQLite WAL")]
    end

    subgraph "robot-a/"
        CONFIG_A["config/"]
        REPORT_A["report/"]
    end

    subgraph "config/ (per robot)"
        POINTS["points.json<br/>Patrol waypoints"]
        SCHED["patrol_schedule.json"]
    end

    subgraph "report/ (per robot)"
        IMAGES["images/"]
        EDGE_ALERTS["edge_ai_alerts/"]
        VIDEO["video/"]
    end

    subgraph "images/"
        RUN_DIR["{run_id}_{timestamp}/"]
        IMG_FILES["PointName_OK_uuid.jpg<br/>PointName_NG_uuid.jpg"]
    end

    subgraph "edge_ai_alerts/"
        ALERT_IMG["{run_id}_{epoch}_{rule}.jpg"]
    end

    subgraph "video/"
        VID_FILE["{run_id}_{timestamp}.mp4"]
    end

    REPORT_DIR --> DB
    ROBOT_A --> CONFIG_A
    ROBOT_A --> REPORT_A
    CONFIG_A --> POINTS
    CONFIG_A --> SCHED
    REPORT_A --> IMAGES
    REPORT_A --> EDGE_ALERTS
    REPORT_A --> VIDEO
    IMAGES --> RUN_DIR
    RUN_DIR --> IMG_FILES
    EDGE_ALERTS --> ALERT_IMG
    VIDEO --> VID_FILE
```

**Filesystem conventions:**
- Shared DB at `data/report/report.db` -- all backends read/write via WAL
- Per-robot data under `data/{robot_id}/` -- config files and inspection artifacts
- Inspection images organized by run: `data/{robot_id}/report/images/{run_id}_{timestamp}/`
- Image filenames encode inspection result: `{PointName}_{OK|NG}_{uuid}.jpg`
- Edge AI evidence images: `data/{robot_id}/report/edge_ai_alerts/{run_id}_{epoch}_{rule}.jpg`
- Video recordings: `data/{robot_id}/report/video/{run_id}_{timestamp}.mp4`
