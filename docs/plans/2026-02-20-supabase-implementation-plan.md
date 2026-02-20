# Supabase Cloud Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a cloud sync layer (Supabase) so external users can view patrol history, reports, and token usage via password-protected share links on a Vercel-hosted dashboard.

**Architecture:** Hybrid cloud+edge. Jetson keeps SQLite (offline-first), syncs completed patrol data to Supabase PostgreSQL. A new static SPA on Vercel reads from Supabase via JS SDK. Access is controlled by share links with password protection (Edge Function issues short-lived JWTs, RLS enforces site isolation).

**Tech Stack:** Supabase (PostgreSQL + Storage + Edge Functions + Realtime), Python `supabase` SDK, vanilla JS + `supabase-js` CDN, Vercel hosting.

**Design doc:** `docs/plans/2026-02-20-supabase-cloud-integration-design.md`

---

## Phase 1: Supabase Project Setup (Cloud Console)

### Task 1: Create Supabase Project & Storage Bucket

This is a manual cloud console task. No code changes.

**Step 1: Create Supabase project**
- Go to https://supabase.com/dashboard → New Project
- Name: `visual-patrol`
- Region: Choose closest to Jetson deployment (e.g. `ap-northeast-1` for Asia)
- Note down: `SUPABASE_URL`, `anon key`, `service_role key`

**Step 2: Create Storage bucket**
- Dashboard → Storage → New Bucket
- Name: `inspection-images`
- Public: **Yes** (images need public URLs for dashboard)
- File size limit: 5MB

**Step 3: Record credentials**
- Create a local `.env.supabase` file (gitignored) with:
```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

**Step 4: Commit .gitignore update**
```bash
echo ".env.supabase" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore supabase credentials file"
```

---

### Task 2: Apply PostgreSQL Schema

**Files:**
- Create: `supabase/migrations/001_init.sql`

**Step 1: Create migration file**

```sql
-- supabase/migrations/001_init.sql

-- Tenants
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

-- Robots
CREATE TABLE robots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id UUID REFERENCES sites(id) ON DELETE CASCADE,
  robot_id TEXT NOT NULL,
  robot_name TEXT NOT NULL,
  last_seen TIMESTAMPTZ,
  status TEXT DEFAULT 'offline',
  UNIQUE(site_id, robot_id)
);

-- Patrol runs
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

-- Inspection results
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

-- Generated reports
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

-- Edge AI alerts
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

-- Enable RLS on all synced tables
ALTER TABLE sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE robots ENABLE ROW LEVEL SECURITY;
ALTER TABLE patrol_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspection_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE edge_ai_alerts ENABLE ROW LEVEL SECURITY;

-- RLS: service_role bypasses RLS automatically.
-- For anon/authenticated users with JWT containing site_id claim:
CREATE POLICY "viewer_read_sites" ON sites
  FOR SELECT USING (id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

CREATE POLICY "viewer_read_robots" ON robots
  FOR SELECT USING (site_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

CREATE POLICY "viewer_read_patrol_runs" ON patrol_runs
  FOR SELECT USING (site_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

CREATE POLICY "viewer_read_inspection_results" ON inspection_results
  FOR SELECT USING (site_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

CREATE POLICY "viewer_read_generated_reports" ON generated_reports
  FOR SELECT USING (site_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

CREATE POLICY "viewer_read_edge_ai_alerts" ON edge_ai_alerts
  FOR SELECT USING (site_id::text = coalesce(current_setting('request.jwt.claims', true)::json->>'site_id', ''));

-- share_links: only service_role can read/write (no viewer access)
-- No SELECT policy for anon = denied by default with RLS enabled.
```

**Step 2: Apply migration**
- In Supabase Dashboard → SQL Editor → paste and run the migration
- Or via CLI: `supabase db push` (if using Supabase CLI locally)

**Step 3: Create initial site record**
- In SQL Editor, run:
```sql
INSERT INTO sites (name) VALUES ('My First Site') RETURNING id;
```
- Note down the returned UUID — this is the `SITE_ID` for docker-compose env vars.

**Step 4: Commit migration file**
```bash
git add supabase/migrations/001_init.sql
git commit -m "feat: add Supabase PostgreSQL schema for cloud sync"
```

---

### Task 3: Create Edge Function (verify-share)

**Files:**
- Create: `supabase/functions/verify-share/index.ts`

**Step 1: Write the Edge Function**

```typescript
// supabase/functions/verify-share/index.ts
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { SignJWT } from "https://deno.land/x/jose@v5.2.0/index.ts";
import { compare } from "https://deno.land/x/bcrypt@v0.4.1/mod.ts";

const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const jwtSecret = new TextEncoder().encode(
  Deno.env.get("SHARE_JWT_SECRET") || supabaseServiceKey
);

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const { token, password } = await req.json();
    if (!token || !password) {
      return new Response(JSON.stringify({ error: "token and password required" }), {
        status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const supabase = createClient(supabaseUrl, supabaseServiceKey);

    const { data: link, error } = await supabase
      .from("share_links")
      .select("*")
      .eq("token", token)
      .single();

    if (error || !link) {
      return new Response(JSON.stringify({ error: "Link not found" }), {
        status: 404, headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // Check expiry
    if (link.expires_at && new Date(link.expires_at) < new Date()) {
      return new Response(JSON.stringify({ error: "Link expired" }), {
        status: 410, headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // Verify password
    const valid = await compare(password, link.password_hash);
    if (!valid) {
      return new Response(JSON.stringify({ error: "Wrong password" }), {
        status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // Fetch site name
    const { data: site } = await supabase
      .from("sites")
      .select("name")
      .eq("id", link.site_id)
      .single();

    // Issue JWT (24h expiry)
    const jwt = await new SignJWT({
      site_id: link.site_id,
      site_name: site?.name || "",
      role: "viewer",
    })
      .setProtectedHeader({ alg: "HS256" })
      .setIssuedAt()
      .setExpirationTime("24h")
      .sign(jwtSecret);

    return new Response(JSON.stringify({ access_token: jwt, site_name: site?.name }), {
      status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: "Internal error" }), {
      status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
```

**Step 2: Deploy Edge Function**
```bash
supabase functions deploy verify-share --no-verify-jwt
```
Note: `--no-verify-jwt` because this endpoint is called by unauthenticated users entering a password.

**Step 3: Set secret**
```bash
supabase secrets set SHARE_JWT_SECRET="<generate a random 64-char string>"
```

**Step 4: Test manually**
```bash
# Should return 404 (no such token)
curl -X POST https://xxx.supabase.co/functions/v1/verify-share \
  -H "Content-Type: application/json" \
  -d '{"token":"nonexistent","password":"test"}'
```

**Step 5: Commit**
```bash
git add supabase/functions/verify-share/index.ts
git commit -m "feat: add verify-share Edge Function for share link auth"
```

---

## Phase 2: Backend Sync Service (Jetson Side)

### Task 4: Add Python Dependency & Config

**Files:**
- Modify: `src/backend/requirements.txt`
- Modify: `src/backend/config.py:1-8` (add new env vars at top)
- Modify: `docker-compose.yml:24-30` (add env vars)
- Modify: `deploy/docker-compose.prod.yaml:18-26` (add env vars)

**Step 1: Add supabase to requirements.txt**

Append to `src/backend/requirements.txt`:
```
supabase>=2.0,<3.0
```

**Step 2: Add config env vars**

In `src/backend/config.py`, after the `RELAY_SERVICE_URL` line (line 10), add:
```python
# Supabase cloud sync (empty = sync disabled, zero impact on local operation)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")       # service_role key
SITE_ID = os.getenv("SITE_ID", "")                 # UUID from Supabase sites table
CLOUD_DASHBOARD_URL = os.getenv("CLOUD_DASHBOARD_URL", "")  # e.g. https://xxx.vercel.app
```

**Step 3: Add env vars to docker-compose.yml**

In `docker-compose.yml` under `robot-a` → `environment`, add:
```yaml
      - SUPABASE_URL=
      - SUPABASE_KEY=
      - SITE_ID=
      - CLOUD_DASHBOARD_URL=
```

**Step 4: Same for deploy/docker-compose.prod.yaml**

Same 4 env vars under `robot-a` → `environment`.

**Step 5: Rebuild Docker image**
```bash
docker compose build robot-a
```

**Step 6: Commit**
```bash
git add src/backend/requirements.txt src/backend/config.py docker-compose.yml deploy/docker-compose.prod.yaml
git commit -m "feat: add Supabase config and Python SDK dependency"
```

---

### Task 5: Add sync_status Column to SQLite

**Files:**
- Modify: `src/backend/database.py` — add migration in `_run_migrations()`

**Step 1: Add sync_status migration**

In `src/backend/database.py`, inside the `migrations` list in `_run_migrations()` (after the `stream_source` migration, around line 334), add:
```python
        # Cloud sync status
        ('sync_status', 'patrol_runs', ['sync_status TEXT']),
        ('sync_status', 'inspection_results', ['sync_status TEXT']),
        ('sync_status', 'generated_reports', ['sync_status TEXT']),
        ('sync_status', 'edge_ai_alerts', ['sync_status TEXT']),
```

**Step 2: Verify**
```bash
docker compose up -d robot-a
docker compose logs robot-a 2>&1 | grep -i migrat
```
Expected: migration logs showing `sync_status` columns added (or nothing if fresh DB).

**Step 3: Commit**
```bash
git add src/backend/database.py
git commit -m "feat: add sync_status column to all synced tables"
```

---

### Task 6: Create sync_service.py

**Files:**
- Create: `src/backend/sync_service.py`

**Step 1: Write sync_service.py**

```python
"""
Cloud Sync Service — syncs patrol data from SQLite to Supabase.
If SUPABASE_URL is empty, all functions are silent no-ops.
"""

import os
import threading
import time
from config import SUPABASE_URL, SUPABASE_KEY, SITE_ID
from database import db_context
from logger import get_logger

logger = get_logger("sync_service", "sync_service.log")

_client = None
_client_lock = threading.Lock()


def _get_client():
    """Lazy-init Supabase client. Returns None if not configured."""
    global _client
    if not SUPABASE_URL or not SUPABASE_KEY or not SITE_ID:
        return None
    with _client_lock:
        if _client is None:
            from supabase import create_client
            _client = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Supabase client initialized")
    return _client


def _upload_image(local_path, run_local_id, filename):
    """Upload image to Supabase Storage. Returns public URL or None."""
    client = _get_client()
    if not client or not local_path or not os.path.exists(local_path):
        return None

    bucket = "inspection-images"
    remote_path = f"{SITE_ID}/{run_local_id}/{filename}"

    try:
        with open(local_path, "rb") as f:
            client.storage.from_(bucket).upload(
                remote_path, f,
                file_options={"content-type": "image/jpeg", "upsert": "true"}
            )
        url = client.storage.from_(bucket).get_public_url(remote_path)
        return url
    except Exception as e:
        logger.warning(f"Image upload failed ({remote_path}): {e}")
        return None


def _mark_synced(table, local_id, status='synced'):
    """Update sync_status in SQLite."""
    with db_context() as (conn, cursor):
        cursor.execute(
            f'UPDATE {table} SET sync_status = ? WHERE id = ?',
            (status, local_id)
        )


def sync_run(run_id):
    """Sync a single patrol run + its inspections + alerts to Supabase."""
    client = _get_client()
    if not client:
        return

    try:
        # Read patrol run from SQLite
        with db_context() as (conn, cursor):
            cursor.execute('SELECT * FROM patrol_runs WHERE id = ?', (run_id,))
            run = cursor.fetchone()
            if not run:
                return

            cursor.execute(
                'SELECT * FROM inspection_results WHERE run_id = ?', (run_id,))
            inspections = [dict(r) for r in cursor.fetchall()]

            cursor.execute(
                'SELECT * FROM edge_ai_alerts WHERE run_id = ?', (run_id,))
            alerts = [dict(r) for r in cursor.fetchall()]

        run = dict(run)

        # Upsert patrol_run
        run_data = {
            'local_id': run['id'],
            'site_id': SITE_ID,
            'robot_id': run.get('robot_id', ''),
            'start_time': run.get('start_time'),
            'end_time': run.get('end_time'),
            'status': run.get('status'),
            'report_content': run.get('report_content'),
            'model_id': run.get('model_id'),
            'input_tokens': run.get('input_tokens', 0) or 0,
            'output_tokens': run.get('output_tokens', 0) or 0,
            'total_tokens': run.get('total_tokens', 0) or 0,
            'report_input_tokens': run.get('report_input_tokens', 0) or 0,
            'report_output_tokens': run.get('report_output_tokens', 0) or 0,
            'report_total_tokens': run.get('report_total_tokens', 0) or 0,
            'telegram_input_tokens': run.get('telegram_input_tokens', 0) or 0,
            'telegram_output_tokens': run.get('telegram_output_tokens', 0) or 0,
            'telegram_total_tokens': run.get('telegram_total_tokens', 0) or 0,
            'video_input_tokens': run.get('video_input_tokens', 0) or 0,
            'video_output_tokens': run.get('video_output_tokens', 0) or 0,
            'video_total_tokens': run.get('video_total_tokens', 0) or 0,
        }

        result = client.table('patrol_runs').upsert(
            run_data, on_conflict='site_id,local_id'
        ).execute()
        cloud_run_id = result.data[0]['id'] if result.data else None

        # Upload images and upsert inspection_results
        for insp in inspections:
            image_url = None
            if insp.get('image_path'):
                local_path = insp['image_path']
                if not os.path.isabs(local_path):
                    from config import DATA_DIR
                    local_path = os.path.join(DATA_DIR, local_path)
                filename = os.path.basename(local_path)
                image_url = _upload_image(local_path, run['id'], filename)

            insp_data = {
                'local_id': insp['id'],
                'run_id': cloud_run_id,
                'site_id': SITE_ID,
                'robot_id': insp.get('robot_id', ''),
                'point_name': insp.get('point_name'),
                'coordinate_x': insp.get('coordinate_x'),
                'coordinate_y': insp.get('coordinate_y'),
                'prompt': insp.get('prompt'),
                'ai_response': insp.get('ai_response'),
                'is_ng': insp.get('is_ng'),
                'image_url': image_url,
                'input_tokens': insp.get('input_tokens', 0) or 0,
                'output_tokens': insp.get('output_tokens', 0) or 0,
                'total_tokens': insp.get('total_tokens', 0) or 0,
                'timestamp': insp.get('timestamp'),
            }
            client.table('inspection_results').upsert(
                insp_data, on_conflict='site_id,local_id'
            ).execute()
            _mark_synced('inspection_results', insp['id'])

        # Upsert edge_ai_alerts
        for alert in alerts:
            image_url = None
            if alert.get('image_path'):
                local_path = alert['image_path']
                if not os.path.isabs(local_path):
                    from config import DATA_DIR
                    local_path = os.path.join(DATA_DIR, local_path)
                filename = os.path.basename(local_path)
                image_url = _upload_image(
                    local_path, run['id'], f"alert_{filename}")

            alert_data = {
                'local_id': alert['id'],
                'run_id': cloud_run_id,
                'site_id': SITE_ID,
                'robot_id': alert.get('robot_id', ''),
                'rule': alert.get('rule'),
                'response': alert.get('response'),
                'image_url': image_url,
                'stream_source': alert.get('stream_source'),
                'timestamp': alert.get('timestamp'),
            }
            client.table('edge_ai_alerts').upsert(
                alert_data, on_conflict='site_id,local_id'
            ).execute()
            _mark_synced('edge_ai_alerts', alert['id'])

        # Mark run as synced
        _mark_synced('patrol_runs', run['id'])
        logger.info(f"Synced patrol run {run_id} ({len(inspections)} inspections, {len(alerts)} alerts)")

    except Exception as e:
        logger.error(f"Failed to sync run {run_id}: {e}")
        _mark_synced('patrol_runs', run_id, 'error')


def sync_report(report_id):
    """Sync a generated report to Supabase."""
    client = _get_client()
    if not client:
        return

    try:
        with db_context() as (conn, cursor):
            cursor.execute(
                'SELECT * FROM generated_reports WHERE id = ?', (report_id,))
            report = cursor.fetchone()
            if not report:
                return

        report = dict(report)
        report_data = {
            'local_id': report['id'],
            'site_id': SITE_ID,
            'robot_id': report.get('robot_id', ''),
            'start_date': report.get('start_date'),
            'end_date': report.get('end_date'),
            'report_content': report.get('report_content'),
            'input_tokens': report.get('input_tokens', 0) or 0,
            'output_tokens': report.get('output_tokens', 0) or 0,
            'total_tokens': report.get('total_tokens', 0) or 0,
            'timestamp': report.get('timestamp'),
        }

        client.table('generated_reports').upsert(
            report_data, on_conflict='site_id,local_id'
        ).execute()

        _mark_synced('generated_reports', report['id'])
        logger.info(f"Synced generated report {report_id}")

    except Exception as e:
        logger.error(f"Failed to sync report {report_id}: {e}")
        _mark_synced('generated_reports', report_id, 'error')


def sync_robot_status(robot_id, robot_name, is_connected):
    """Sync robot heartbeat to Supabase."""
    client = _get_client()
    if not client:
        return

    try:
        from utils import get_current_time_str
        robot_data = {
            'site_id': SITE_ID,
            'robot_id': robot_id,
            'robot_name': robot_name,
            'last_seen': get_current_time_str(),
            'status': 'online' if is_connected else 'offline',
        }
        client.table('robots').upsert(
            robot_data, on_conflict='site_id,robot_id'
        ).execute()
    except Exception as e:
        logger.warning(f"Failed to sync robot status: {e}")


def sync_pending():
    """Catch-up sync: find all unsynced records and sync them."""
    client = _get_client()
    if not client:
        return

    # Sync unsynced patrol_runs
    with db_context() as (conn, cursor):
        cursor.execute(
            "SELECT id FROM patrol_runs WHERE sync_status IS NULL OR sync_status = 'error' ORDER BY id")
        run_ids = [row['id'] for row in cursor.fetchall()]

    for run_id in run_ids:
        sync_run(run_id)

    # Sync unsynced generated_reports
    with db_context() as (conn, cursor):
        cursor.execute(
            "SELECT id FROM generated_reports WHERE sync_status IS NULL OR sync_status = 'error' ORDER BY id")
        report_ids = [row['id'] for row in cursor.fetchall()]

    for report_id in report_ids:
        sync_report(report_id)

    if run_ids or report_ids:
        logger.info(f"Catch-up sync: {len(run_ids)} runs, {len(report_ids)} reports")


def start_background_sync(interval=300):
    """Start daemon thread that runs sync_pending every `interval` seconds."""
    if not SUPABASE_URL:
        return

    def _loop():
        # Initial catch-up on startup
        time.sleep(10)  # wait for app to fully initialize
        sync_pending()
        while True:
            time.sleep(interval)
            try:
                sync_pending()
            except Exception as e:
                logger.error(f"Background sync error: {e}")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info(f"Background sync started (interval={interval}s)")
```

**Step 2: Verify module imports**
```bash
cd /home/snaken/CodeBase/visual-patrol/src/backend
python -c "import sync_service; print('OK')"
```
Expected: `OK` (sync functions are no-ops since SUPABASE_URL is empty).

**Step 3: Commit**
```bash
git add src/backend/sync_service.py
git commit -m "feat: add sync_service for Supabase cloud sync"
```

---

### Task 7: Integrate Sync into Patrol Service & App Startup

**Files:**
- Modify: `src/backend/patrol_service.py:579-585` (after token totals update)
- Modify: `src/backend/app.py:804-808` (after report save)
- Modify: `src/backend/app.py:1029-1050` (startup block)

**Step 1: Add sync call in patrol_service.py**

After line 583 (after `update_run_tokens` and its error handler), add:
```python
        # Sync to cloud (no-op if Supabase not configured)
        try:
            import sync_service
            sync_service.sync_run(self.current_run_id)
        except Exception as e:
            logger.warning(f"Cloud sync failed (non-blocking): {e}")
```

This goes right before the `logger.info(f"Patrol Run {self.current_run_id} finished: {final_status}")` line.

**Step 2: Add sync call in app.py report generation**

In `app.py`, after `save_generated_report()` call (around line 808), add:
```python
        # Sync report to cloud
        try:
            import sync_service
            sync_service.sync_report(report_id)
        except Exception:
            pass  # non-blocking
```

**Step 3: Add sync startup in app.py**

In the `if __name__ == '__main__':` block (around line 1046), before `app.run(...)`, add:
```python
    # Start cloud sync background thread
    import sync_service
    sync_service.start_background_sync()
```

**Step 4: Add robot status sync to heartbeat**

In `app.py`, in the `_heartbeat_loop()` function (around line 1022), after `update_robot_heartbeat(ROBOT_ID, is_connected)`, add:
```python
            import sync_service
            sync_service.sync_robot_status(ROBOT_ID, ROBOT_NAME, is_connected)
```

**Step 5: Verify app starts without errors**
```bash
docker compose up -d robot-a && docker compose logs -f robot-a 2>&1 | head -50
```
Expected: Normal startup, no Supabase-related errors (sync disabled since env vars are empty).

**Step 6: Commit**
```bash
git add src/backend/patrol_service.py src/backend/app.py
git commit -m "feat: integrate sync_service into patrol completion and app startup"
```

---

### Task 8: Add Share Links API

**Files:**
- Modify: `src/backend/app.py` (add 3 new endpoints)

**Step 1: Add share-links endpoints**

In `app.py`, before the `# --- Heartbeat Thread ---` section (around line 1016), add:

```python
# --- Share Links API (Cloud Sync) ---

@app.route('/api/share-links', methods=['GET'])
def list_share_links():
    """List all share links for this site."""
    import sync_service
    client = sync_service._get_client()
    if not client:
        return jsonify({"error": "Cloud sync not configured"}), 503

    from config import SITE_ID, CLOUD_DASHBOARD_URL
    try:
        result = client.table('share_links').select('*').eq(
            'site_id', SITE_ID).order('created_at', desc=True).execute()
        links = []
        for link in result.data:
            links.append({
                'id': link['id'],
                'token': link['token'],
                'label': link.get('label', ''),
                'expires_at': link.get('expires_at'),
                'created_at': link['created_at'],
                'url': f"{CLOUD_DASHBOARD_URL}/share/{link['token']}" if CLOUD_DASHBOARD_URL else '',
            })
        return jsonify(links)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/share-links', methods=['POST'])
def create_share_link():
    """Create a new share link with password."""
    import sync_service
    client = sync_service._get_client()
    if not client:
        return jsonify({"error": "Cloud sync not configured"}), 503

    import uuid
    import hashlib
    from config import SITE_ID, CLOUD_DASHBOARD_URL

    data = request.get_json()
    password = data.get('password', '')
    label = data.get('label', '')
    expires_days = data.get('expires_days')

    if not password or len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400

    token = uuid.uuid4().hex[:12]

    # bcrypt hash (use hashlib as fallback since bcrypt may not be installed)
    try:
        import bcrypt
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Fallback: SHA256 with salt (less secure but functional)
        import secrets
        salt = secrets.token_hex(16)
        password_hash = f"sha256:{salt}:{hashlib.sha256(f'{salt}{password}'.encode()).hexdigest()}"

    expires_at = None
    if expires_days:
        from datetime import datetime, timedelta
        expires_at = (datetime.utcnow() + timedelta(days=int(expires_days))).isoformat()

    try:
        link_data = {
            'token': token,
            'site_id': SITE_ID,
            'password_hash': password_hash,
            'label': label,
            'expires_at': expires_at,
        }
        result = client.table('share_links').insert(link_data).execute()
        url = f"{CLOUD_DASHBOARD_URL}/share/{token}" if CLOUD_DASHBOARD_URL else f"/share/{token}"
        return jsonify({
            'id': result.data[0]['id'],
            'token': token,
            'url': url,
            'label': label,
            'expires_at': expires_at,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/share-links/<link_id>', methods=['DELETE'])
def delete_share_link(link_id):
    """Delete a share link."""
    import sync_service
    client = sync_service._get_client()
    if not client:
        return jsonify({"error": "Cloud sync not configured"}), 503

    try:
        client.table('share_links').delete().eq('id', link_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

**Step 2: Add bcrypt to requirements.txt**

Append to `src/backend/requirements.txt`:
```
bcrypt>=4.0,<5.0
```

**Step 3: Verify endpoints**
```bash
docker compose build robot-a && docker compose up -d robot-a
# Should return 503 (sync not configured)
curl -s http://localhost:5000/api/robot-a/share-links | python -m json.tool
```

**Step 4: Commit**
```bash
git add src/backend/app.py src/backend/requirements.txt
git commit -m "feat: add share-links CRUD API endpoints"
```

---

## Phase 3: Cloud Dashboard (New Frontend)

### Task 9: Scaffold Cloud Dashboard Project

**Files:**
- Create: `cloud-dashboard/index.html`
- Create: `cloud-dashboard/app.js`
- Create: `cloud-dashboard/style.css`
- Create: `cloud-dashboard/vercel.json`

**Step 1: Create vercel.json (SPA routing)**

```json
{
  "rewrites": [
    { "source": "/share/(.*)", "destination": "/index.html" }
  ]
}
```

**Step 2: Create index.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Visual Patrol — Dashboard</title>
    <link rel="stylesheet" href="/style.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
</head>
<body>
    <!-- Auth screen -->
    <div id="auth-screen">
        <div class="auth-card">
            <h1>Visual Patrol</h1>
            <p id="auth-site-label"></p>
            <input type="password" id="auth-password" placeholder="Enter password" autocomplete="off">
            <button id="auth-submit">Enter</button>
            <p id="auth-error" class="error"></p>
        </div>
    </div>

    <!-- Dashboard (hidden until authenticated) -->
    <div id="dashboard" style="display:none;">
        <header>
            <h1>Visual Patrol — <span id="site-name"></span></h1>
            <div class="tabs">
                <button class="tab active" data-tab="history">History</button>
                <button class="tab" data-tab="reports">Reports</button>
                <button class="tab" data-tab="tokens">Tokens</button>
            </div>
        </header>

        <main>
            <section id="tab-history" class="tab-content active">
                <div class="toolbar">
                    <select id="history-robot-filter">
                        <option value="">All Robots</option>
                    </select>
                </div>
                <div id="history-list"></div>
                <!-- History detail modal -->
                <div id="history-modal" class="modal">
                    <div class="modal-content">
                        <span class="modal-close" id="modal-close">&times;</span>
                        <div id="modal-body"></div>
                    </div>
                </div>
            </section>

            <section id="tab-reports" class="tab-content" style="display:none;">
                <div id="reports-list"></div>
            </section>

            <section id="tab-tokens" class="tab-content" style="display:none;">
                <div class="toolbar">
                    <input type="date" id="stats-start-date">
                    <input type="date" id="stats-end-date">
                    <select id="stats-robot-filter">
                        <option value="">All Robots</option>
                    </select>
                    <button id="btn-load-stats">Load</button>
                </div>
                <canvas id="token-chart"></canvas>
            </section>
        </main>

        <!-- Real-time indicator -->
        <div id="realtime-indicator" style="display:none;">
            <span class="pulse"></span> Live updates active
        </div>
    </div>

    <script type="module" src="/app.js"></script>
</body>
</html>
```

**Step 3: Create app.js (entry point + auth + tab switching)**

```javascript
// cloud-dashboard/app.js
// Config — replace with your Supabase project values
const SUPABASE_URL = '__SUPABASE_URL__';  // replaced at build or via env
const SUPABASE_ANON_KEY = '__SUPABASE_ANON_KEY__';
const VERIFY_FUNCTION_URL = `${SUPABASE_URL}/functions/v1/verify-share`;

let supabase = null;
let siteId = null;
let siteName = null;
let accessToken = null;
let robots = [];

// --- Auth ---
function getShareToken() {
    const path = window.location.pathname;
    const match = path.match(/\/share\/([a-zA-Z0-9]+)/);
    return match ? match[1] : null;
}

async function authenticate(password) {
    const token = getShareToken();
    if (!token) {
        showAuthError('Invalid share link');
        return;
    }

    try {
        const res = await fetch(VERIFY_FUNCTION_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, password }),
        });

        if (!res.ok) {
            const err = await res.json();
            showAuthError(err.error || 'Authentication failed');
            return;
        }

        const data = await res.json();
        accessToken = data.access_token;
        siteName = data.site_name;

        // Parse JWT to get site_id
        const payload = JSON.parse(atob(accessToken.split('.')[1]));
        siteId = payload.site_id;

        // Init Supabase client with custom auth
        supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
            global: {
                headers: { Authorization: `Bearer ${accessToken}` }
            }
        });

        showDashboard();
    } catch (e) {
        showAuthError('Connection error. Please try again.');
    }
}

function showAuthError(msg) {
    document.getElementById('auth-error').textContent = msg;
}

function showDashboard() {
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('dashboard').style.display = 'block';
    document.getElementById('site-name').textContent = siteName || 'Dashboard';

    loadRobots().then(() => {
        loadHistory();
        loadReports();
        setupRealtimeSubscription();
    });
}

// --- Tab switching ---
document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => {
            c.style.display = 'none';
            c.classList.remove('active');
        });
        btn.classList.add('active');
        const tab = document.getElementById(`tab-${btn.dataset.tab}`);
        tab.style.display = 'block';
        tab.classList.add('active');

        if (btn.dataset.tab === 'tokens') loadTokenStats();
    });
});

// --- Robots ---
async function loadRobots() {
    try {
        const { data } = await supabase.from('robots').select('*').eq('site_id', siteId);
        robots = data || [];
        populateRobotFilters();
    } catch (e) {
        console.error('Failed to load robots:', e);
    }
}

function populateRobotFilters() {
    ['history-robot-filter', 'stats-robot-filter'].forEach(id => {
        const select = document.getElementById(id);
        if (!select) return;
        while (select.options.length > 1) select.remove(1);
        robots.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.robot_id;
            opt.textContent = r.robot_name;
            select.appendChild(opt);
        });
    });
}

function getRobotName(robotId) {
    const r = robots.find(r => r.robot_id === robotId);
    return r ? r.robot_name : (robotId || '');
}

// --- History ---
async function loadHistory() {
    const list = document.getElementById('history-list');
    const robotFilter = document.getElementById('history-robot-filter')?.value;
    list.innerHTML = '<div class="loading">Loading...</div>';

    let query = supabase.from('patrol_runs').select('*').eq('site_id', siteId)
        .order('local_id', { ascending: false });
    if (robotFilter) query = query.eq('robot_id', robotFilter);

    const { data, error } = await query;
    if (error) { list.innerHTML = `<div class="error">${error.message}</div>`; return; }
    if (!data?.length) { list.innerHTML = '<div class="empty">No patrol history yet.</div>'; return; }

    list.innerHTML = data.map(run => `
        <div class="history-card" onclick="showRunDetail('${run.id}')">
            <div class="history-header">
                <span class="run-id">#${run.local_id}</span>
                <span class="robot-name">${getRobotName(run.robot_id)}</span>
                <span class="status status-${run.status}">${run.status || ''}</span>
            </div>
            <div class="history-meta">
                <span>${run.start_time || ''}</span>
                <span>${run.total_tokens || 0} tokens</span>
            </div>
        </div>
    `).join('');
}

window.showRunDetail = async function(cloudRunId) {
    const modal = document.getElementById('history-modal');
    const body = document.getElementById('modal-body');
    modal.style.display = 'flex';
    body.innerHTML = '<div class="loading">Loading...</div>';

    const { data: run } = await supabase.from('patrol_runs')
        .select('*').eq('id', cloudRunId).single();
    const { data: inspections } = await supabase.from('inspection_results')
        .select('*').eq('run_id', cloudRunId).order('local_id');
    const { data: alerts } = await supabase.from('edge_ai_alerts')
        .select('*').eq('run_id', cloudRunId).order('local_id');

    let html = `<h2>Patrol #${run.local_id} — ${getRobotName(run.robot_id)}</h2>`;
    html += `<p><b>Status:</b> ${run.status} | <b>Start:</b> ${run.start_time} | <b>End:</b> ${run.end_time || 'N/A'}</p>`;

    if (run.report_content) {
        html += `<div class="report-content">${marked.parse(run.report_content)}</div>`;
    }

    if (inspections?.length) {
        html += '<h3>Inspection Results</h3>';
        for (const insp of inspections) {
            const ngClass = insp.is_ng ? 'ng' : 'ok';
            html += `<div class="inspection-card ${ngClass}">`;
            html += `<b>${insp.point_name || 'Unknown'}</b>`;
            if (insp.image_url) html += `<img src="${insp.image_url}" alt="inspection" loading="lazy">`;
            if (insp.ai_response) {
                try {
                    const parsed = JSON.parse(insp.ai_response);
                    html += `<p>${parsed.description || insp.ai_response}</p>`;
                } catch { html += `<p>${insp.ai_response}</p>`; }
            }
            html += '</div>';
        }
    }

    if (alerts?.length) {
        html += '<h3>Edge AI Alerts</h3>';
        for (const alert of alerts) {
            html += `<div class="alert-card">`;
            html += `<b>${alert.rule || ''}</b>: ${alert.response || ''}`;
            if (alert.image_url) html += `<img src="${alert.image_url}" alt="alert" loading="lazy">`;
            html += '</div>';
        }
    }

    body.innerHTML = html;
};

document.getElementById('modal-close')?.addEventListener('click', () => {
    document.getElementById('history-modal').style.display = 'none';
});
document.getElementById('history-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'history-modal') e.target.style.display = 'none';
});

// --- Reports ---
async function loadReports() {
    const list = document.getElementById('reports-list');
    list.innerHTML = '<div class="loading">Loading...</div>';

    const { data, error } = await supabase.from('generated_reports').select('*')
        .eq('site_id', siteId).order('local_id', { ascending: false });

    if (error) { list.innerHTML = `<div class="error">${error.message}</div>`; return; }
    if (!data?.length) { list.innerHTML = '<div class="empty">No reports generated yet.</div>'; return; }

    list.innerHTML = data.map((report, i) => `
        <div class="report-card">
            <div class="report-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
                <span>${report.start_date} ~ ${report.end_date}</span>
                <span>${report.total_tokens || 0} tokens</span>
            </div>
            <div class="report-body ${i > 0 ? 'collapsed' : ''}">
                ${marked.parse(report.report_content || '')}
            </div>
        </div>
    `).join('');
}

// --- Token Stats ---
async function loadTokenStats() {
    const startDate = document.getElementById('stats-start-date')?.value;
    const endDate = document.getElementById('stats-end-date')?.value;
    const robotFilter = document.getElementById('stats-robot-filter')?.value;

    let query = supabase.from('patrol_runs')
        .select('local_id, start_time, robot_id, input_tokens, output_tokens, total_tokens, report_total_tokens, telegram_total_tokens, video_total_tokens')
        .eq('site_id', siteId)
        .order('start_time');

    if (startDate) query = query.gte('start_time', startDate);
    if (endDate) query = query.lte('start_time', endDate + 'T23:59:59');
    if (robotFilter) query = query.eq('robot_id', robotFilter);

    const { data } = await query;
    if (!data?.length) return;

    renderTokenChart(data);
}

function renderTokenChart(data) {
    const canvas = document.getElementById('token-chart');
    const ctx = canvas.getContext('2d');

    // Group by date
    const byDate = {};
    for (const run of data) {
        const date = (run.start_time || '').split('T')[0].split(' ')[0];
        if (!date) continue;
        if (!byDate[date]) byDate[date] = { inspection: 0, report: 0, telegram: 0, video: 0 };
        byDate[date].inspection += (run.total_tokens || 0) - (run.report_total_tokens || 0) - (run.telegram_total_tokens || 0) - (run.video_total_tokens || 0);
        byDate[date].report += run.report_total_tokens || 0;
        byDate[date].telegram += run.telegram_total_tokens || 0;
        byDate[date].video += run.video_total_tokens || 0;
    }

    const labels = Object.keys(byDate).sort();
    if (window._tokenChart) window._tokenChart.destroy();

    window._tokenChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Inspection', data: labels.map(d => byDate[d].inspection), backgroundColor: '#4CAF50' },
                { label: 'Report', data: labels.map(d => byDate[d].report), backgroundColor: '#2196F3' },
                { label: 'Telegram', data: labels.map(d => byDate[d].telegram), backgroundColor: '#FF9800' },
                { label: 'Video', data: labels.map(d => byDate[d].video), backgroundColor: '#9C27B0' },
            ]
        },
        options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true } } }
    });
}

// Default date range for stats
const end = new Date();
const start = new Date();
start.setDate(start.getDate() - 30);
const fmt = d => d.toISOString().split('T')[0];
const startInput = document.getElementById('stats-start-date');
const endInput = document.getElementById('stats-end-date');
if (startInput) startInput.value = fmt(start);
if (endInput) endInput.value = fmt(end);

document.getElementById('btn-load-stats')?.addEventListener('click', loadTokenStats);
document.getElementById('history-robot-filter')?.addEventListener('change', loadHistory);

// --- Realtime ---
function setupRealtimeSubscription() {
    supabase.channel('live-patrol')
        .on('postgres_changes',
            { event: 'INSERT', schema: 'public', table: 'inspection_results', filter: `site_id=eq.${siteId}` },
            () => {
                document.getElementById('realtime-indicator').style.display = 'flex';
                loadHistory();  // refresh
            }
        )
        .on('postgres_changes',
            { event: 'INSERT', schema: 'public', table: 'patrol_runs', filter: `site_id=eq.${siteId}` },
            () => loadHistory()
        )
        .subscribe();
}

// --- Init ---
document.getElementById('auth-submit')?.addEventListener('click', () => {
    const pwd = document.getElementById('auth-password')?.value;
    if (pwd) authenticate(pwd);
});
document.getElementById('auth-password')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const pwd = e.target.value;
        if (pwd) authenticate(pwd);
    }
});

// Check if we have a share token
if (!getShareToken()) {
    document.getElementById('auth-screen').innerHTML =
        '<div class="auth-card"><h1>Visual Patrol</h1><p>Invalid link. Please use a valid share link.</p></div>';
}
```

**Step 4: Create style.css**

A minimal stylesheet that matches the existing SPA's visual style. This will be ~150 lines covering auth screen, tabs, history cards, modal, report cards, chart container, and real-time indicator. Use the same color palette as the existing frontend (dark header, card-based layout).

**Step 5: Commit**
```bash
git add cloud-dashboard/
git commit -m "feat: scaffold cloud dashboard SPA with auth, history, reports, tokens"
```

---

### Task 10: Deploy Cloud Dashboard to Vercel

**Step 1: Create Vercel project**
```bash
cd cloud-dashboard
npx vercel --yes
```
Follow prompts to link to your Vercel account.

**Step 2: Set environment variables in Vercel**
- Go to Vercel Dashboard → Project Settings → Environment Variables
- Or replace `__SUPABASE_URL__` and `__SUPABASE_ANON_KEY__` placeholders in `app.js` with actual values before deploy.

Alternative: Use a build script or inline the values directly (since this is a public anon key, not a secret).

**Step 3: Deploy**
```bash
npx vercel --prod
```

**Step 4: Note the URL**
- Vercel will print: `https://visual-patrol-xxx.vercel.app`
- This is your `CLOUD_DASHBOARD_URL` for docker-compose env vars.

**Step 5: Commit deploy config**
```bash
git add cloud-dashboard/vercel.json
git commit -m "chore: add Vercel deploy config"
```

---

## Phase 4: End-to-End Verification

### Task 11: Connect Everything & Test

**Step 1: Set Jetson env vars**

In `docker-compose.yml` (or `.env` file), fill in the Supabase credentials:
```yaml
- SUPABASE_URL=https://xxx.supabase.co
- SUPABASE_KEY=eyJ... (service_role key)
- SITE_ID=<UUID from Task 2 Step 3>
- CLOUD_DASHBOARD_URL=https://visual-patrol-xxx.vercel.app
```

**Step 2: Restart and verify sync**
```bash
docker compose up -d robot-a
docker compose logs -f robot-a 2>&1 | grep -i sync
```
Expected: `Supabase client initialized`, `Background sync started`

**Step 3: Run a patrol (or trigger catch-up sync)**
- If there's existing patrol data, the background sync should pick it up within 5 minutes.
- Check Supabase Dashboard → Table Editor → `patrol_runs` to see synced data.

**Step 4: Create a share link**
```bash
curl -X POST http://localhost:5000/api/robot-a/share-links \
  -H "Content-Type: application/json" \
  -d '{"label": "Test Link", "password": "test1234", "expires_days": 30}'
```
Expected: Returns `{ "url": "https://visual-patrol-xxx.vercel.app/share/abc123", ... }`

**Step 5: Open the share link in browser**
- Navigate to the URL from Step 4
- Enter password `test1234`
- Should see the dashboard with synced patrol data

**Step 6: Verify real-time (optional)**
- Start a new patrol on Jetson
- Watch the Cloud Dashboard — new inspection results should appear automatically

---

## Summary of All Commits

1. `chore: gitignore supabase credentials file`
2. `feat: add Supabase PostgreSQL schema for cloud sync`
3. `feat: add verify-share Edge Function for share link auth`
4. `feat: add Supabase config and Python SDK dependency`
5. `feat: add sync_status column to all synced tables`
6. `feat: add sync_service for Supabase cloud sync`
7. `feat: integrate sync_service into patrol completion and app startup`
8. `feat: add share-links CRUD API endpoints`
9. `feat: scaffold cloud dashboard SPA with auth, history, reports, tokens`
10. `chore: add Vercel deploy config`
