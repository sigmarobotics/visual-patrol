# Supabase Cloud Integration Design

**Date**: 2026-02-20
**Status**: Approved

## Goals

1. **Remote monitoring** — View patrol history, reports, token usage from external network
2. **Multi-user access** — Password-protected share links for external users
3. **Data centralization** — Aggregate patrol data from multiple Jetson sites to cloud
4. **Real-time push** — Live updates when patrols are in progress

## Architecture: Cloud + Edge Hybrid

```
┌─── Jetson Edge (LAN) ─────────────────────────┐
│                                                │
│  [Flask Backend] ←→ [SQLite]                   │
│       │                  │                     │
│  robot control,     local data                 │
│  patrol execution   (offline-first)            │
│       │                                        │
│  [nginx :5000] → Local SPA (full control)      │
│       │                                        │
│  ── sync_service.py (new) ──────────────┐      │
│       after each patrol run             │      │
└─────────────────────────────────────────│──────┘
                                          │
                                          ▼
                    ┌─── Supabase Cloud ───────────┐
                    │  PostgreSQL (data)            │
                    │  Storage (images)             │
                    │  Edge Function (auth)         │
                    │  Realtime (WebSocket)         │
                    │  RLS (row-level security)     │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌─── Vercel ───────────────────┐
                    │  Cloud Dashboard SPA          │
                    │  /share/{token}?pwd=xxx       │
                    │  History / Reports / Tokens   │
                    │  Read-only, real-time updates  │
                    └──────────────────────────────┘
```

**Key principle**: Jetson keeps SQLite and operates offline-first. Supabase is the cloud sync target. If SUPABASE_URL is empty, sync is silently skipped — zero impact on existing functionality.

## Role Division

| Layer | Responsibility | Technology |
|-------|---------------|------------|
| Jetson Edge | Patrol execution, robot control, AI analysis, local UI | Flask + SQLite (unchanged) |
| Sync Service | Post-patrol sync to cloud | New `sync_service.py`, Supabase Python SDK |
| Supabase Cloud | Data centralization, access control, real-time push | PostgreSQL + RLS + Realtime + Storage |
| Cloud Dashboard | External read-only view | New static SPA on Vercel, Supabase JS SDK |

## Section 1: Supabase Cloud Schema

### Data to Sync

| SQLite Table | Sync? | Reason |
|-------------|-------|--------|
| `patrol_runs` | Yes | Core data for History tab |
| `inspection_results` | Yes | Per-point AI analysis + images |
| `generated_reports` | Yes | Reports tab |
| `edge_ai_alerts` | Yes | Real-time alert records |
| `robots` | Yes | Robot list/status for dashboard |
| `global_settings` | No | Local config, not needed in cloud |

### PostgreSQL Schema

```sql
-- Tenant
CREATE TABLE sites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Share links
CREATE TABLE share_links (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token TEXT UNIQUE NOT NULL,
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  password_hash TEXT NOT NULL,
  label TEXT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Synced data (mirrors SQLite schema + site_id)
CREATE TABLE robots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT NOT NULL,
  robot_name TEXT NOT NULL,
  last_seen TIMESTAMPTZ,
  status TEXT DEFAULT 'offline',
  UNIQUE(site_id, robot_id)
);

CREATE TABLE patrol_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  local_id INTEGER NOT NULL,
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT NOT NULL,
  start_time TIMESTAMPTZ,
  end_time TIMESTAMPTZ,
  status TEXT,
  report_content TEXT,
  model_id TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  report_input_tokens INTEGER DEFAULT 0,
  report_output_tokens INTEGER DEFAULT 0,
  report_total_tokens INTEGER DEFAULT 0,
  telegram_input_tokens INTEGER DEFAULT 0,
  telegram_output_tokens INTEGER DEFAULT 0,
  telegram_total_tokens INTEGER DEFAULT 0,
  video_input_tokens INTEGER DEFAULT 0,
  video_output_tokens INTEGER DEFAULT 0,
  video_total_tokens INTEGER DEFAULT 0,
  synced_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(site_id, local_id)
);

CREATE TABLE inspection_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  local_id INTEGER NOT NULL,
  run_id UUID REFERENCES patrol_runs(id) ON DELETE CASCADE,
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT,
  point_name TEXT,
  coordinate_x REAL,
  coordinate_y REAL,
  prompt TEXT,
  ai_response TEXT,
  is_ng INTEGER,
  image_url TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  timestamp TIMESTAMPTZ,
  UNIQUE(site_id, local_id)
);

CREATE TABLE generated_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  local_id INTEGER NOT NULL,
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT,
  start_date TEXT,
  end_date TEXT,
  report_content TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  timestamp TIMESTAMPTZ,
  UNIQUE(site_id, local_id)
);

CREATE TABLE edge_ai_alerts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  local_id INTEGER NOT NULL,
  run_id UUID REFERENCES patrol_runs(id) ON DELETE CASCADE,
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT,
  rule TEXT,
  response TEXT,
  image_url TEXT,
  stream_source TEXT,
  timestamp TIMESTAMPTZ,
  UNIQUE(site_id, local_id)
);
```

### RLS Policies

```sql
-- Share link users can only read data for their site
-- JWT contains site_id claim (issued by Edge Function)

ALTER TABLE patrol_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "share_read" ON patrol_runs
  FOR SELECT USING (site_id = auth.jwt()->>'site_id'::uuid);

-- Same policy applied to all synced tables
-- Write access restricted to service_role key (Jetson sync only)
```

### Design Decisions

- **UUID PK** in cloud, `local_id` maps back to SQLite INTEGER ID
- **`UNIQUE(site_id, local_id)`** prevents duplicate syncs, enables upsert
- **`image_url`** replaces `image_path` — images uploaded to Supabase Storage
- **All token columns preserved** — needed for Cloud Dashboard Tokens tab

## Section 2: Sync Service (Jetson → Supabase)

### Trigger Points

| Trigger | Description |
|---------|-------------|
| Post-patrol | `patrol_service.py` finishes → calls `sync_service.sync_run(run_id)` |
| Post-report | `app.py` generates report → calls `sync_service.sync_report(report_id)` |
| Background timer | Daemon thread every 5 min scans `sync_status IS NULL` |
| Startup | Flask boot runs `sync_pending()` to catch up offline data |

### SQLite Change

Single column added to patrol_runs, inspection_results, generated_reports, edge_ai_alerts:

```sql
ALTER TABLE patrol_runs ADD COLUMN sync_status TEXT;
-- NULL = unsynced, 'synced', 'error'
```

### sync_service.py Core Logic (~150 LOC)

```python
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, SITE_ID

_client = None

def get_client():
    global _client
    if _client is None and SUPABASE_URL:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client

def sync_run(run_id):
    """Sync a patrol run (+ inspection_results + images + alerts)"""
    client = get_client()
    if not client:
        return  # Supabase not configured, silently skip

    # 1. Read SQLite patrol_run
    # 2. Upload inspection images → Storage
    # 3. Upsert patrol_run → Supabase
    # 4. Upsert inspection_results (with image_url)
    # 5. Upsert edge_ai_alerts
    # 6. Mark SQLite sync_status = 'synced'
    # Any failure → sync_status = 'error', log warning

def sync_report(report_id):
    """Sync a generated report"""

def sync_pending():
    """Catch-up sync for all sync_status IS NULL or 'error'"""

def start_background_sync(interval=300):
    """Start daemon thread running sync_pending every interval seconds"""
```

### Image Upload

```python
def upload_image(local_path, run_id, filename):
    bucket = "inspection-images"
    remote_path = f"{SITE_ID}/{run_id}/{filename}"
    with open(local_path, "rb") as f:
        client.storage.from_(bucket).upload(remote_path, f)
    return client.storage.from_(bucket).get_public_url(remote_path)
```

Storage structure: `inspection-images/{site_id}/{run_local_id}/point1.jpg`

### Config Additions

```python
SUPABASE_URL = os.getenv("SUPABASE_URL", "")   # empty = sync disabled
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")    # service_role key
SITE_ID = os.getenv("SITE_ID", "")              # UUID of this Jetson's site
```

### Offline Tolerance

- `SUPABASE_URL` empty → all sync silently skipped, zero impact
- Network down → sync fails, `sync_status = 'error'`, retried on next timer
- Jetson reboot → `sync_pending()` on startup catches up
- Duplicate sync safe → `UNIQUE(site_id, local_id)` + upsert = idempotent

## Section 3: Cloud Dashboard + Share Links

### Share Link Flow

1. Admin creates share link in Local SPA
   - `POST /api/share-links { label, password, expires_days }`
   - Generates token, hashes password, syncs to Supabase
   - Returns URL: `https://xxx.vercel.app/share/{token}`

2. External user opens link
   - Enters password → Supabase Edge Function verifies
   - Returns short-lived JWT (24h, contains site_id, role: viewer)
   - Dashboard uses JWT to read data via RLS

3. RLS enforces: share token can only read matching site_id, cannot write

### Edge Function: verify-share (~40 LOC)

```typescript
// supabase/functions/verify-share/index.ts
// Verifies token + password → returns JWT with site_id claim
```

### Cloud Dashboard Structure

```
cloud-dashboard/
  index.html          -- Password entry page (/share/{token})
  app.js              -- Entry: verify → load data
  history.js          -- Patrol history (adapted from existing)
  reports.js          -- Report viewer
  stats.js            -- Token usage charts
  style.css           -- Styles (reuse from existing)
```

### Dashboard vs Local SPA

| | Local SPA (Jetson) | Cloud Dashboard (Vercel) |
|---|---|---|
| Tabs | Patrol, Control, History, Reports, Tokens, Settings | History, Reports, Tokens (3 only) |
| Data source | SQLite via Flask API | Supabase via JS SDK |
| Write | Full robot control | Read-only |
| Real-time | HTTP polling | Supabase Realtime subscription |
| Access | LAN :5000 | `xxx.vercel.app/share/{token}` |

### Real-time Subscription

```javascript
const channel = supabase
  .channel('live-patrol')
  .on('postgres_changes',
    { event: 'INSERT', schema: 'public', table: 'inspection_results',
      filter: `site_id=eq.${siteId}` },
    (payload) => appendInspectionResult(payload.new)
  )
  .subscribe()
```

### Admin API (Local SPA additions)

```
POST   /api/share-links   — Create share link
GET    /api/share-links    — List all share links
DELETE /api/share-links/id — Delete share link
```

## Section 4: Implementation Phases

### Phase 1 — Foundation (Jetson side)
- Supabase project setup
- PostgreSQL schema + migrations
- Storage bucket
- Edge Function
- RLS policies
- `sync_service.py`
- `database.py` migration (sync_status)
- `config.py` new env vars
- Share-links API
- `patrol_service` calls sync

### Phase 2 — Cloud Dashboard (new project)
- Password entry page
- History tab
- Reports tab
- Tokens tab
- Real-time subscription
- Vercel project setup

### Phase 3 — Go Live
- Register first site
- End-to-end sync verification
- Share link end-to-end test
- Vercel deployment

## File Changes Summary

| File | Action | Est. LOC |
|------|--------|---------|
| `src/backend/sync_service.py` | New | ~150 |
| `src/backend/config.py` | Modify | +5 |
| `src/backend/database.py` | Modify | +15 |
| `src/backend/patrol_service.py` | Modify | +3 |
| `src/backend/app.py` | Modify | +40 |
| `supabase/migrations/001_init.sql` | New | ~80 |
| `supabase/functions/verify-share/` | New | ~40 |
| `cloud-dashboard/index.html` | New | ~200 |
| `cloud-dashboard/app.js` | New | ~80 |
| `cloud-dashboard/history.js` | New | ~150 |
| `cloud-dashboard/reports.js` | New | ~100 |
| `cloud-dashboard/stats.js` | New | ~120 |
| `cloud-dashboard/style.css` | New | ~100 |
| `docker-compose.yml` | Modify | +3 |
| `deploy/docker-compose.prod.yaml` | Modify | +3 |

**Total**: ~1,100 new LOC, ~70 modified LOC

## New Dependencies

| Layer | Addition |
|-------|---------|
| Jetson Python | `supabase` (pip) |
| Cloud Dashboard | `supabase-js` (CDN) |
| External Services | Supabase free account + Vercel free account |

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Supabase free tier limits | Free: 500MB DB + 1GB Storage + 50K Auth — sufficient for initial 10 Jetsons |
| Image sync bandwidth | JPEG ~100KB each, ~10 per patrol = ~1MB per run |
| Sync latency | Triggered immediately post-patrol, typically 1-3s |
| Supabase outage | Jetson unaffected (offline-first), cloud view temporarily unavailable |
| Env vars not set | `sync_service` silently skips, zero side effects |
