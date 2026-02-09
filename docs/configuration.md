# Configuration

## Overview

Visual Patrol has two layers of configuration:

1. **Environment variables** -- Per-robot identity, paths, and infrastructure addresses, set in `docker-compose.yml`
2. **Global settings** -- Shared across all robots, stored in the SQLite database and managed via the web UI Settings page

## Environment Variables

Set per-service in `docker-compose.yml` (dev) or `deploy/docker-compose.prod.yaml` (prod).

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOT_ID` | `"default"` | Unique robot identifier (must match Docker service name in dev) |
| `ROBOT_NAME` | `"Robot"` | Display name shown in the web UI |
| `ROBOT_IP` | `"192.168.50.133:26400"` | Kachaka robot gRPC address (`ip:port`) |
| `DATA_DIR` | `{project}/data` | Base directory for shared data and per-robot data |
| `LOG_DIR` | `{project}/logs` | Base directory for log files |
| `PORT` | `5000` | Flask HTTP listen port (must be unique per robot in prod) |
| `TZ` | (system default) | System timezone for Docker container |
| `MEDIAMTX_INTERNAL` | `"localhost:8554"` | mediamtx host:port for ffmpeg to push to (from inside the container) |
| `MEDIAMTX_EXTERNAL` | `"localhost:8554"` | mediamtx host:port for VILA JPS to pull from (from outside the container) |

**Important:** `ROBOT_ID` must follow the pattern `robot-{name}` (e.g., `robot-a`, `robot-b`). In dev mode, the Docker service name must match the `ROBOT_ID` because nginx resolves backends by service name.

### mediamtx Address Configuration

mediamtx is deployed externally as part of the VILA JPS stack (not included in visual-patrol's docker-compose files). The two `MEDIAMTX_*` variables exist because ffmpeg (inside the Flask container) and VILA JPS (external) may use different addresses to reach mediamtx:

| Mode | `MEDIAMTX_INTERNAL` | `MEDIAMTX_EXTERNAL` | Reason |
|------|---------------------|---------------------|--------|
| Dev (bridge) | `mediamtx:8554` | `localhost:8554` | ffmpeg uses Docker DNS; VILA JPS accesses localhost port mapping |
| Prod (host) | `localhost:8555` | `localhost:8555` | All on host network, same address |

### Example (Dev)

```yaml
environment:
  - DATA_DIR=/app/data
  - LOG_DIR=/app/logs
  - TZ=Asia/Taipei
  - ROBOT_ID=robot-a
  - ROBOT_NAME=Robot A
  - ROBOT_IP=192.168.50.133:26400
  - MEDIAMTX_INTERNAL=mediamtx:8554
  - MEDIAMTX_EXTERNAL=localhost:8554
```

### Example (Prod)

```yaml
environment:
  - DATA_DIR=/app/data
  - LOG_DIR=/app/logs
  - TZ=Asia/Taipei
  - PORT=5001
  - ROBOT_ID=robot-a
  - ROBOT_NAME=Robot A
  - ROBOT_IP=192.168.50.133:26400
  - MEDIAMTX_INTERNAL=localhost:8555
  - MEDIAMTX_EXTERNAL=localhost:8555
```

## Global Settings (Web UI)

Global settings are stored in the `global_settings` SQLite table as key-value pairs. They are shared across all robot backends because all access the same database.

Manage settings through the **Settings** tab in the web UI, or via the API:
- `GET /api/settings` -- Read all settings (sensitive fields masked)
- `POST /api/settings` -- Save settings

### AI Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `gemini_api_key` | `""` | Google Gemini API key (sensitive, masked in GET) |
| `gemini_model` | `"gemini-3-flash-preview"` | Gemini model identifier |
| `system_prompt` | `"You are a helpful robot assistant..."` | System role prompt for AI inspection |

### Patrol Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `turbo_mode` | `false` | Async AI analysis -- robot moves to next point while previous image is analyzed |
| `enable_video_recording` | `false` | Record video during patrol (uses OpenCV) |
| `video_prompt` | `"Analyze this video..."` | Prompt for AI video analysis after patrol |
| `enable_idle_stream` | `true` | Show camera stream when not patrolling |

### Live Monitor (VILA JPS)

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_edge_ai` | `false` | Enable live monitoring during patrol |
| `jetson_host` | `""` | Jetson device IP address (auto-derives JPS, mediamtx, relay URLs) |
| `enable_robot_camera_relay` | `false` | Relay robot camera (gRPC) to mediamtx via ffmpeg |
| `enable_external_rtsp` | `false` | Relay an external RTSP camera to mediamtx |
| `external_rtsp_url` | `""` | External RTSP source URL (e.g., `rtsp://admin:pass@192.168.50.45:554/live`) |
| `edge_ai_rules` | `[]` | List of yes/no alert rule strings, max 10 (e.g., `["Is there a person?", "Is there fire?"]`) |

The live monitor requires at least one stream source (`enable_robot_camera_relay` or `enable_external_rtsp`) and `jetson_host` to be set. When enabled, the system:

1. Starts ffmpeg relay processes to push camera streams to mediamtx
2. Registers streams with VILA JPS
3. Sets alert rules per stream
4. Listens for WebSocket alert events
5. Captures evidence frames and saves to DB + disk on triggered alerts
6. Sends alert photos to Telegram if configured

Each rule has a 60-second cooldown to prevent repeated alerts for the same condition.

### Report Prompts

| Setting | Default | Description |
|---------|---------|-------------|
| `report_prompt` | (Chinese inspection table template) | Single patrol run report generation prompt |
| `multiday_report_prompt` | `"Generate a comprehensive summary..."` | Multi-day aggregated report prompt |

The default `report_prompt` is a Chinese-language template that generates a structured inspection checklist table covering electrical safety, indoor environment, fire safety, and other categories.

### Timezone

| Setting | Default | Description |
|---------|---------|-------------|
| `timezone` | `"UTC"` | Timezone for timestamps and scheduling |

Available options in the web UI: UTC, Asia/Taipei, Asia/Tokyo, America/New_York, America/Los_Angeles, Europe/London. The backend uses Python's `zoneinfo` module, so any valid IANA timezone name works if set via API.

This setting affects:
- All timestamps in the database (`get_current_time_str()`)
- Log file timestamps
- Schedule checker (determines "current time" for triggering)
- Header clock display in the web UI

### Telegram Notifications

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_telegram` | `false` | Enable Telegram notifications on patrol completion |
| `telegram_bot_token` | `""` | Telegram Bot API token (sensitive, masked in GET) |
| `telegram_user_id` | `""` | Telegram chat/user ID to send notifications to |
| `telegram_message_prompt` | `"Based on the patrol inspection results below, generate a concise Telegram notification message in Traditional Chinese..."` | Prompt used to generate AI-summarized Telegram notification messages |

When enabled, the system sends notifications in two scenarios:
1. **Patrol completion**: AI-generated summary text + PDF report document
2. **Live monitor alerts**: Photo with caption (rule, source, robot, timestamp) -- sent immediately on alert trigger

### Sensitive Fields

The following fields are masked in GET responses to prevent accidental exposure:
- `gemini_api_key`
- `telegram_bot_token`
- `telegram_user_id`

Masked format: `****{last 4 chars}` (e.g., `****abcd`).

When saving settings via POST, values starting with `****` are ignored so they don't overwrite the real stored values.

## Default Settings

Defined in `src/backend/config.py` as `DEFAULT_SETTINGS`:

```python
DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "gemini_model": "gemini-3-flash-preview",
    "system_prompt": "You are a helpful robot assistant...",
    "timezone": "UTC",
    "enable_video_recording": False,
    "video_prompt": "Analyze this video...",
    "enable_idle_stream": True,
    "report_prompt": "...",  # Chinese inspection table template
    "multiday_report_prompt": "Generate a comprehensive summary...",
    "telegram_message_prompt": "Based on the patrol inspection results below...",
    "enable_edge_ai": False,
    "edge_ai_rules": [],
    "enable_robot_camera_relay": False,
    "enable_external_rtsp": False,
    "external_rtsp_url": "",
}
```

When `settings_service.get_all()` is called, stored settings are merged on top of these defaults. Missing keys fall back to the default value.

## Legacy Migration

### Settings Migration

Before the database-backed settings, settings were stored in `data/config/settings.json`. On first boot, `settings_service.migrate_from_json()` automatically imports this file into the `global_settings` table if no custom settings have been saved yet.

### Per-Robot File Migration

Legacy per-robot files were stored in a shared `data/config/` directory. On first boot, `config.migrate_legacy_files()` copies them to per-robot directories:

- `data/config/points.json` -> `data/{robot_id}/config/points.json`
- `data/config/patrol_schedule.json` -> `data/{robot_id}/config/patrol_schedule.json`

### Data Migration

`database.backfill_robot_id()` sets `robot_id` on existing rows where it's NULL, ensuring pre-multi-robot data is attributed to the current robot.

## Per-Robot Configuration Files

Each robot stores its own configuration in `data/{robot_id}/config/`:

### `points.json` -- Patrol Waypoints

```json
[
  {
    "id": "1706000000000",
    "name": "Lobby Entrance",
    "x": 1.5,
    "y": 2.0,
    "theta": 0.0,
    "prompt": "Check for obstacles in the hallway",
    "enabled": true,
    "source": "robot"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique ID (timestamp-based) |
| `name` | string | Display name |
| `x`, `y` | float | World coordinates (meters) |
| `theta` | float | Orientation (radians) |
| `prompt` | string | AI inspection prompt for this point |
| `enabled` | boolean | Whether to include in patrol |
| `source` | string | Optional. `"robot"` if imported from Kachaka |

### `patrol_schedule.json` -- Scheduled Patrols

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

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID-based identifier |
| `time` | string | Time in `HH:MM` format |
| `days` | int[] | Days of week (0=Monday, 6=Sunday) |
| `enabled` | boolean | Whether this schedule is active |

## Logging Configuration

Log files are written to `LOG_DIR` with robot-ID prefixes:

| Log File | Source | Content |
|----------|--------|---------|
| `{robot_id}_app.log` | `app.py` | Flask application logs |
| `{robot_id}_cloud_ai_service.log` | `cloud_ai_service.py` | AI inference logs, token usage |
| `{robot_id}_patrol_service.log` | `patrol_service.py` | Patrol execution logs |
| `{robot_id}_video_recorder.log` | `video_recorder.py` | Video recording logs |
| `{robot_id}_edge_ai_service.log` | `edge_ai_service.py` | Live monitor alert logs, WebSocket status |
| `{robot_id}_relay_manager.log` | `relay_manager.py` | ffmpeg relay process logs |

All loggers use `TimezoneFormatter` which formats timestamps in the configured timezone. Flask/Werkzeug request logging is suppressed (set to ERROR level).

Log output goes to both the log file and stdout (for Docker `docker compose logs`).
