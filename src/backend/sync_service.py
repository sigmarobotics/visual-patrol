"""
Sync Service - Syncs patrol data from local SQLite to Supabase cloud.
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
    """Lazy-init Supabase client. Returns None if config not set."""
    global _client
    if not SUPABASE_URL or not SUPABASE_KEY or not SITE_ID:
        return None
    with _client_lock:
        if _client is None:
            try:
                from supabase import create_client
                _client = create_client(SUPABASE_URL, SUPABASE_KEY)
                logger.info("Supabase client initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Supabase client: {e}")
                return None
    return _client


def _upload_image(local_path, run_local_id, filename):
    """
    Upload an image to Supabase Storage bucket 'inspection-images'.
    Remote path: {SITE_ID}/{run_local_id}/{filename}
    Returns public URL or None on failure.
    """
    client = _get_client()
    if client is None:
        return None

    if not local_path or not os.path.exists(local_path):
        return None

    remote_path = f"{SITE_ID}/{run_local_id}/{filename}"
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        client.storage.from_("inspection-images").upload(
            path=remote_path,
            file=data,
            file_options={"content-type": "image/jpeg", "upsert": "true"},
        )
        public_url = client.storage.from_("inspection-images").get_public_url(remote_path)
        return public_url
    except Exception as e:
        logger.warning(f"Image upload failed ({local_path}): {e}")
        return None


def _mark_synced(table, local_id, status="synced"):
    """UPDATE table SET sync_status = status WHERE id = local_id."""
    try:
        with db_context() as (conn, cursor):
            cursor.execute(
                f"UPDATE {table} SET sync_status = ? WHERE id = ?",
                (status, local_id),
            )
    except Exception as e:
        logger.warning(f"Failed to mark {table} id={local_id} as {status}: {e}")


def sync_run(run_id):
    """
    Sync a patrol run and its associated inspection results and edge AI alerts
    to Supabase. Marks all records as synced in SQLite on success.
    """
    client = _get_client()
    if client is None:
        return

    try:
        # Read patrol_run from SQLite
        with db_context() as (conn, cursor):
            cursor.execute("SELECT * FROM patrol_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            if row is None:
                logger.warning(f"sync_run: patrol_run id={run_id} not found")
                return
            run = dict(row)

            # Read inspection_results
            cursor.execute(
                "SELECT * FROM inspection_results WHERE run_id = ?", (run_id,)
            )
            inspections = [dict(r) for r in cursor.fetchall()]

            # Read edge_ai_alerts
            cursor.execute(
                "SELECT * FROM edge_ai_alerts WHERE run_id = ?", (run_id,)
            )
            alerts = [dict(r) for r in cursor.fetchall()]

        # Build patrol_run payload for Supabase upsert
        run_payload = {
            "local_id": run["id"],
            "site_id": SITE_ID,
            "robot_id": run.get("robot_id", ""),
            "start_time": run.get("start_time"),
            "end_time": run.get("end_time"),
            "status": run.get("status"),
            "report_content": run.get("report_content"),
            "model_id": run.get("model_id"),
            "input_tokens": run.get("input_tokens", 0) or 0,
            "output_tokens": run.get("output_tokens", 0) or 0,
            "total_tokens": run.get("total_tokens", 0) or 0,
            "report_input_tokens": run.get("report_input_tokens", 0) or 0,
            "report_output_tokens": run.get("report_output_tokens", 0) or 0,
            "report_total_tokens": run.get("report_total_tokens", 0) or 0,
            "telegram_input_tokens": run.get("telegram_input_tokens", 0) or 0,
            "telegram_output_tokens": run.get("telegram_output_tokens", 0) or 0,
            "telegram_total_tokens": run.get("telegram_total_tokens", 0) or 0,
            "video_input_tokens": run.get("video_input_tokens", 0) or 0,
            "video_output_tokens": run.get("video_output_tokens", 0) or 0,
            "video_total_tokens": run.get("video_total_tokens", 0) or 0,
        }

        # Upsert patrol_run to Supabase
        result = (
            client.table("patrol_runs")
            .upsert(run_payload, on_conflict="site_id,local_id")
            .execute()
        )

        if not result.data:
            raise ValueError("Upsert patrol_run returned no data")

        cloud_run_id = result.data[0]["id"]

        # Sync inspection results
        for insp in inspections:
            image_url = None
            local_img = insp.get("image_path")
            if local_img:
                from config import DATA_DIR
                if not os.path.isabs(local_img):
                    local_img = os.path.join(DATA_DIR, local_img)
                filename = os.path.basename(local_img)
                image_url = _upload_image(local_img, run_id, filename)

            insp_payload = {
                "local_id": insp["id"],
                "site_id": SITE_ID,
                "run_id": cloud_run_id,
                "robot_id": insp.get("robot_id", ""),
                "point_name": insp.get("point_name"),
                "coordinate_x": insp.get("coordinate_x"),
                "coordinate_y": insp.get("coordinate_y"),
                "prompt": insp.get("prompt"),
                "ai_response": insp.get("ai_response"),
                "is_ng": insp.get("is_ng"),
                "image_url": image_url,
                "input_tokens": insp.get("input_tokens", 0) or 0,
                "output_tokens": insp.get("output_tokens", 0) or 0,
                "total_tokens": insp.get("total_tokens", 0) or 0,
                "timestamp": insp.get("timestamp"),
            }
            client.table("inspection_results").upsert(
                insp_payload, on_conflict="site_id,local_id"
            ).execute()
            _mark_synced("inspection_results", insp["id"])

        # Sync edge AI alerts
        for alert in alerts:
            image_url = None
            local_img = alert.get("image_path")
            if local_img:
                from config import DATA_DIR
                if not os.path.isabs(local_img):
                    local_img = os.path.join(DATA_DIR, local_img)
                filename = os.path.basename(local_img)
                image_url = _upload_image(local_img, run_id, f"alert_{filename}")

            alert_payload = {
                "local_id": alert["id"],
                "site_id": SITE_ID,
                "run_id": cloud_run_id,
                "robot_id": alert.get("robot_id", ""),
                "rule": alert.get("rule"),
                "response": alert.get("response"),
                "image_url": image_url,
                "stream_source": alert.get("stream_source"),
                "timestamp": alert.get("timestamp"),
            }
            client.table("edge_ai_alerts").upsert(
                alert_payload, on_conflict="site_id,local_id"
            ).execute()
            _mark_synced("edge_ai_alerts", alert["id"])

        # Mark patrol_run as synced
        _mark_synced("patrol_runs", run_id)
        logger.info(f"sync_run: run_id={run_id} synced to cloud (cloud_id={cloud_run_id})")

    except Exception as e:
        logger.warning(f"sync_run: run_id={run_id} failed: {e}")
        _mark_synced("patrol_runs", run_id, status="error")


def sync_report(report_id):
    """
    Sync a generated report from SQLite to Supabase.
    Marks the record as synced in SQLite on success.
    """
    client = _get_client()
    if client is None:
        return

    try:
        with db_context() as (conn, cursor):
            cursor.execute(
                "SELECT * FROM generated_reports WHERE id = ?", (report_id,)
            )
            row = cursor.fetchone()
            if row is None:
                logger.warning(f"sync_report: generated_report id={report_id} not found")
                return
            report = dict(row)

        payload = {
            "local_id": report["id"],
            "site_id": SITE_ID,
            "robot_id": report.get("robot_id", ""),
            "start_date": report.get("start_date"),
            "end_date": report.get("end_date"),
            "report_content": report.get("report_content"),
            "input_tokens": report.get("input_tokens", 0) or 0,
            "output_tokens": report.get("output_tokens", 0) or 0,
            "total_tokens": report.get("total_tokens", 0) or 0,
            "timestamp": report.get("timestamp"),
        }

        client.table("generated_reports").upsert(
            payload, on_conflict="site_id,local_id"
        ).execute()

        _mark_synced("generated_reports", report_id)
        logger.info(f"sync_report: report_id={report_id} synced to cloud")

    except Exception as e:
        logger.warning(f"sync_report: report_id={report_id} failed: {e}")
        _mark_synced("generated_reports", report_id, status="error")


def sync_robot_status(robot_id, robot_name, is_connected):
    """
    Upsert robot status to Supabase robots table.
    """
    client = _get_client()
    if client is None:
        return

    try:
        payload = {
            "site_id": SITE_ID,
            "robot_id": robot_id,
            "robot_name": robot_name,
            "status": "online" if is_connected else "offline",
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        }
        client.table("robots").upsert(
            payload, on_conflict="site_id,robot_id"
        ).execute()
    except Exception as e:
        logger.warning(f"sync_robot_status: robot_id={robot_id} failed: {e}")


def sync_pending():
    """
    Query SQLite for patrol_runs and generated_reports where sync_status
    is NULL or 'error', then sync each to Supabase.
    """
    client = _get_client()
    if client is None:
        return

    try:
        with db_context() as (conn, cursor):
            cursor.execute(
                "SELECT id FROM patrol_runs WHERE sync_status IS NULL OR sync_status = 'error'"
            )
            run_ids = [row["id"] for row in cursor.fetchall()]

            cursor.execute(
                "SELECT id FROM generated_reports WHERE sync_status IS NULL OR sync_status = 'error'"
            )
            report_ids = [row["id"] for row in cursor.fetchall()]

        for run_id in run_ids:
            sync_run(run_id)

        for report_id in report_ids:
            sync_report(report_id)

        if run_ids or report_ids:
            logger.info(
                f"sync_pending: processed {len(run_ids)} runs, {len(report_ids)} reports"
            )

    except Exception as e:
        logger.warning(f"sync_pending: failed: {e}")


def start_background_sync(interval=300):
    """
    Start a background daemon thread that periodically syncs pending records.
    If SUPABASE_URL is not configured, returns immediately without starting.
    interval: seconds between sync cycles (default 300 = 5 minutes)
    """
    if not SUPABASE_URL:
        return

    def _sync_loop():
        # Wait for app initialization
        time.sleep(10)
        sync_pending()
        while True:
            time.sleep(interval)
            sync_pending()

    thread = threading.Thread(target=_sync_loop, daemon=True, name="sync_background")
    thread.start()
    logger.info(f"Background sync started (interval={interval}s)")
