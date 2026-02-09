# Frontend Documentation

## Overview

The frontend is a single-page application (SPA) built with vanilla JavaScript ES modules. There is no build step or bundler -- the browser loads modules directly via `<script type="module">`.

## File Structure

```
src/frontend/
├── templates/
│   └── index.html              # Main SPA page (all views in one file)
└── static/
    ├── favicon.png              # Application logo
    ├── css/
    │   └── style.css            # All styles (~48KB)
    └── js/
        ├── app.js               # Entry point, tab switching, robot selector
        ├── state.js             # Shared mutable state (singleton)
        ├── map.js               # Canvas rendering, coordinate transforms
        ├── controls.js          # D-pad manual control
        ├── ai.js                # AI test panel, result parsing
        ├── points.js            # Waypoint CRUD, table rendering
        ├── patrol.js            # Patrol start/stop, status polling
        ├── schedule.js          # Scheduled patrol management
        ├── history.js           # Patrol history, detail modal, PDF export
        ├── reports.js           # Multi-run report generation, list, PDF save
        ├── settings.js          # Settings load/save, clock, live monitor test
        ├── stats.js             # Token usage chart (Chart.js)
        ├── chart.min.js         # Chart.js (vendored, downloaded at build)
        └── marked.min.js        # Marked.js (vendored, downloaded at build)
```

## Module Dependency Graph

```
state.js  (imports nothing)
    ^
    |--- map.js
    |--- controls.js
    |--- ai.js
    |--- points.js  ---> map.js, ai.js
    |--- patrol.js  ---> ai.js
    |--- schedule.js
    |--- history.js ---> ai.js
    |--- reports.js
    |--- settings.js
    |--- stats.js   (no state import, uses DOM directly)
    |
app.js  (imports all of the above, entry point)
```

## Shared State (`state.js`)

All cross-module state lives in a single exported `state` object:

```javascript
const state = {
    robotPose: { x, y, theta },     // Current robot position
    mapInfo: null,                   // { resolution, width, height, origin_x, origin_y }
    isMapLoaded: false,
    mapImage: new Image(),           // Loaded PNG map
    canvasScale: 1,                  // CSS scale factor for canvas

    isDragging: false,               // Click-to-move drag state
    dragStart: null,
    dragCurrent: null,

    currentPatrolPoints: [],         // Loaded from /api/{id}/points
    highlightedPoint: null,          // For map hover highlight

    currentSettingsTimezone: 'UTC',
    currentIdleStreamEnabled: true,

    selectedRobotId: null,           // Currently selected robot
    availableRobots: [],             // All registered robots

    _intervals: { ... },            // SetInterval IDs for cleanup
};
```

Also exports `escapeHtml()` utility for XSS prevention.

## Tab System

The app has 6 tabs: **Patrol**, **Control** (default), **History**, **Reports**, **Tokens**, **Settings**.

All tab views exist in the DOM simultaneously. `switchTab(name)` shows/hides them:

```javascript
window.switchTab = function(tabName) {
    // Hide all views
    // Show target view
    // Load data for data-heavy tabs (history, reports, stats, settings)
    // Reparent map canvas between Control and Patrol views
};
```

The map canvas (`#map-canvas`) is physically moved between the Control and Patrol panels using `prepend()` / `appendChild()` to avoid maintaining two separate canvases.

## Key Patterns

### API Calls

All API calls use the `state.selectedRobotId` for robot-specific endpoints:

```javascript
// Robot-specific
fetch(`/api/${state.selectedRobotId}/state`)

// Global (no robot prefix)
fetch('/api/settings')
```

### Window Function Exposure

Inline `onclick` handlers in HTML reference functions via `window`:

```javascript
// In initPoints()
window.updatePoint = updatePoint;
window.deletePoint = deletePoint;
```

```html
<button onclick="deletePoint('${id}')">Delete</button>
```

### Robot Selector

`fetchRobots()` runs every 5 seconds, updating the robot dropdown and connection indicator. When the selected robot changes, `onRobotChanged()` triggers:

1. `resetMap()` -- Clears and reloads the map
2. `loadPoints()` -- Fetches waypoints for the new robot
3. `loadSchedule()` -- Fetches scheduled patrols
4. `refreshCameraStreams()` -- Points camera `<img>` tags to new robot's stream

### Polling Intervals

| Interval | Target | Frequency |
|----------|--------|-----------|
| `statePolling` | Robot pose, battery, map info | 100ms |
| `patrolPolling` | Patrol status, inspection results | 1s |
| `robotFetch` | Robot list (dropdown refresh) | 5s |
| `clock` | Header clock display | 1s |
| `scheduleDisplay` | Next patrol time display | 60s |

All intervals are stored in `state._intervals` to prevent duplicate registration.

## Module Details

### `map.js` -- Map Canvas

Renders the robot's environment map on an HTML5 canvas:

- Loads map PNG from `/api/{id}/map`
- Draws robot position as a directional arrow
- Shows patrol point highlights on hover
- Handles click-to-move (click) and pose-to-move (drag for direction)
- Coordinate transforms between world (meters) and pixel space:
  - `worldToPixelX/Y()`: World coordinates to canvas pixels
  - `pixelToWorldX/Y()`: Canvas pixels to world coordinates
- Polls robot state at 100ms and redraws

### `controls.js` -- Manual Control

Simple D-pad controls for manual robot movement:

- Forward/backward: 0.1m increments
- Left/right: ~10 degree rotation
- Return home button
- Emergency stop (cancel command)

### `ai.js` -- AI Test

Test AI recognition on the current camera frame:

- Sends prompt to `/api/{id}/test_ai`
- Parses structured JSON response (`is_NG`, `Description`)
- Exports `parseAIResponse()` and `renderAIResultHTML()` used by other modules

### `points.js` -- Waypoint Management

Full CRUD for patrol waypoints:

- Add point at current robot position
- Edit name, prompt, enabled status inline
- Delete points
- Reorder via up/down buttons in Patrol view
- Import/export as JSON files
- Import saved locations from the Kachaka robot
- Test a point: move robot there, then run AI test

Renders three separate table views:
1. Quick table (Control view) -- name, prompt, test, delete
2. Patrol view table -- name, reorder buttons, enabled checkbox
3. Detailed table (unused in current UI) -- full info with coordinates

### `patrol.js` -- Patrol Control

- Start/stop patrol buttons
- Polls patrol status every 1s
- Displays latest AI analysis result
- Shows scrollable history of current patrol results
- Manages camera stream (active during patrol, optionally during idle)
- **Live Alerts panel**: When live monitoring is active, polls `GET /api/{id}/patrol/edge_ai_alerts` each second and renders triggered alerts in a red-themed collapsible panel with a badge counter. Alerts show rule, stream source, timestamp, and evidence image.

### `schedule.js` -- Scheduled Patrols

- Add scheduled patrols with time picker
- Toggle enable/disable
- Delete schedules
- Displays "Next patrol" countdown in the Patrol view header

### `history.js` -- Patrol History

- Lists all past patrol runs as clickable cards with status, timing, and token usage
- Card title format: "Run #N" with robot name tag
- Video icon displayed for runs that have a video recording
- Robot filter dropdown
- Click to view detail modal with AI summary and inspection images
- Live alerts section in detail modal (with stream source labels)
- Download patrol PDFs
- Uses `marked.js` to render Markdown report content

### `reports.js` -- Multi-Run Reports

Separate from History -- dedicated to generating and managing multi-run analysis reports.

- Date range picker (defaults to last 7 days)
- Generate button that calls `POST /api/reports/generate` with start/end dates
- Lists all previously generated reports as collapsible cards
- Each card header shows date range, timestamp, and token usage (input/output/total)
- Card body renders report Markdown content via `marked.js`
- Save PDF button per report -- downloads via `/api/reports/generate/pdf?start_date=...&end_date=...`

### `settings.js` -- Settings Panel

Loads and saves all system settings via `/api/settings`. Organized into 3 sub-tabs:

**General:**
- Timezone selector
- Turbo mode checkbox (parallel inspection)
- Enable idle stream checkbox (camera feed when not patrolling)
- Enable Telegram notifications (bot token, user ID)
- Telegram message prompt textarea
- Registered robots list display

**Gemini AI:**
- API key input (with masking for saved keys)
- Model selector
- System prompt textarea
- Report prompt textarea
- Multi-day report prompt textarea
- Enable video recording checkbox
- Video analysis prompt textarea

**VILA/Edge AI:**
- Enable live monitoring checkbox
- Stream source radio buttons (Robot Camera / External RTSP -- mutually exclusive, JPS supports max 1 stream)
- Jetson Host IP input (auto-derives JPS, mediamtx, relay URLs)
- External RTSP URL input
- Alert rules textarea (one rule per line, max 10)
- Test Edge AI button

**Test Edge AI button:** Starts a test session using the full VILA JPS flow -- starts relay/push, registers stream with JPS, sets alert rules, connects WebSocket. Displays an alert rules table with per-rule trigger count and last-triggered timestamp. Shows VLC RTSP URL for stream preview. Polls `/api/{id}/test_edge_ai/status` every 2 seconds for status updates. Toggle button switches between "Test Edge AI" and "Stop Test".

**Additional features:**
- Manages the header clock (uses configured timezone)
- Sub-tab switching via `switchSettingsTab()` function

### `stats.js` -- Tokens (Token Usage Statistics)

- Tab labeled "Tokens" in the header navigation
- Fetches daily token usage from `/api/stats/token_usage`
- Renders a Chart.js line chart with three datasets: Input Tokens, Output Tokens, Total Tokens
- Y-axis displays values in millions (e.g., "0.50 M") with "Million Tokens" axis label
- Date range picker with robot filter dropdown
- Summary cards showing totals for the selected period:
  - Input Tokens displayed in millions with pricing annotation ($0.50 / 1M)
  - Output Tokens displayed in millions with pricing annotation ($3.00 / 1M)
  - Total Tokens displayed in millions with estimated total cost
- Tooltips show precise values in millions format (e.g., "0.123 M")

## Third-Party Libraries

| Library | Version | Purpose |
|---------|---------|---------|
| Chart.js | Latest (CDN) | Token usage statistics chart |
| marked.js | Latest (CDN) | Markdown rendering for AI reports |

Both are downloaded at Docker build time and bundled as static files. They are loaded as traditional scripts (not ES modules) via `<script>` tags before `app.js`.
