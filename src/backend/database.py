"""
Database management for Visual Patrol system.
SQLite with automatic schema migrations and multi-robot support.
"""

import sqlite3
import json
from contextlib import contextmanager
from config import DB_FILE


def get_db_connection():
    """Create a new database connection with Row factory and WAL mode."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db_context():
    """
    Context manager for database operations.
    Auto-commits on success, rolls back on error, always closes.

    Usage:
        with db_context() as (conn, cursor):
            cursor.execute("SELECT ...")
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_run_token_totals(run_id):
    """
    Calculate total tokens used in a patrol run across all categories.
    Returns dict with input_tokens, output_tokens, total_tokens per category.
    """
    with db_context() as (conn, cursor):
        # Inspection tokens (aggregated from inspection_results)
        cursor.execute('''
            SELECT
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens
            FROM inspection_results
            WHERE run_id = ?
        ''', (run_id,))
        insp = cursor.fetchone()

        # Per-category tokens from patrol_runs
        cursor.execute('''
            SELECT
                COALESCE(report_input_tokens, 0) as report_input,
                COALESCE(report_output_tokens, 0) as report_output,
                COALESCE(report_total_tokens, 0) as report_total,
                COALESCE(telegram_input_tokens, 0) as tg_input,
                COALESCE(telegram_output_tokens, 0) as tg_output,
                COALESCE(telegram_total_tokens, 0) as tg_total,
                COALESCE(video_input_tokens, 0) as vid_input,
                COALESCE(video_output_tokens, 0) as vid_output,
                COALESCE(video_total_tokens, 0) as vid_total
            FROM patrol_runs
            WHERE id = ?
        ''', (run_id,))
        run = cursor.fetchone()

        insp_in = insp['input_tokens']
        insp_out = insp['output_tokens']
        insp_total = insp['total_tokens']

        report_in = run['report_input'] if run else 0
        report_out = run['report_output'] if run else 0
        report_total = run['report_total'] if run else 0

        tg_in = run['tg_input'] if run else 0
        tg_out = run['tg_output'] if run else 0
        tg_total = run['tg_total'] if run else 0

        vid_in = run['vid_input'] if run else 0
        vid_out = run['vid_output'] if run else 0
        vid_total = run['vid_total'] if run else 0

        grand_in = insp_in + report_in + tg_in + vid_in
        grand_out = insp_out + report_out + tg_out + vid_out
        grand_total = insp_total + report_total + tg_total + vid_total

        return {
            'input_tokens': grand_in,
            'output_tokens': grand_out,
            'total_tokens': grand_total,
        }


def update_run_tokens(run_id):
    """Update patrol_runs table with grand total token counts."""
    totals = get_run_token_totals(run_id)
    with db_context() as (conn, cursor):
        cursor.execute('''
            UPDATE patrol_runs
            SET input_tokens = ?, output_tokens = ?, total_tokens = ?
            WHERE id = ?
        ''', (totals['input_tokens'], totals['output_tokens'],
              totals['total_tokens'], run_id))


def get_generated_reports():
    """Get all generated reports ordered by timestamp DESC."""
    with db_context() as (conn, cursor):
        cursor.execute('''
            SELECT id, start_date, end_date, report_content,
                   input_tokens, output_tokens, total_tokens, timestamp, robot_id
            FROM generated_reports
            ORDER BY timestamp DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


def save_generated_report(start_date, end_date, content, usage, robot_id=None):
    """Save AI generated report to database."""
    with db_context() as (conn, cursor):
        cursor.execute('''
            INSERT INTO generated_reports
            (start_date, end_date, report_content, input_tokens, output_tokens, total_tokens, timestamp, robot_id)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), ?)
        ''', (start_date, end_date, content,
              usage.get('prompt_token_count', 0),
              usage.get('candidates_token_count', 0),
              usage.get('total_token_count', 0),
              robot_id))
        return cursor.lastrowid


# === Multi-Robot Functions ===

def register_robot(robot_id, robot_name, robot_ip):
    """Register or update a robot in the robots table."""
    with db_context() as (conn, cursor):
        cursor.execute('''
            INSERT INTO robots (robot_id, robot_name, robot_ip, last_seen, status)
            VALUES (?, ?, ?, datetime('now', 'localtime'), 'online')
            ON CONFLICT(robot_id) DO UPDATE SET
                robot_name = excluded.robot_name,
                robot_ip = excluded.robot_ip,
                last_seen = datetime('now', 'localtime'),
                status = 'online'
        ''', (robot_id, robot_name, robot_ip))


def update_robot_heartbeat(robot_id, is_connected=True):
    """Update last_seen timestamp and connection status for a robot."""
    status = 'online' if is_connected else 'offline'
    with db_context() as (conn, cursor):
        cursor.execute('''
            UPDATE robots SET last_seen = datetime('now', 'localtime'), status = ?
            WHERE robot_id = ?
        ''', (status, robot_id))


def get_all_robots():
    """Get all registered robots."""
    with db_context() as (conn, cursor):
        cursor.execute('SELECT robot_id, robot_name, robot_ip, last_seen, status FROM robots ORDER BY robot_id')
        return [dict(row) for row in cursor.fetchall()]


def backfill_robot_id(robot_id):
    """Set robot_id on rows where it's NULL (one-time migration)."""
    with db_context() as (conn, cursor):
        cursor.execute('UPDATE patrol_runs SET robot_id = ? WHERE robot_id IS NULL', (robot_id,))
        cursor.execute('UPDATE inspection_results SET robot_id = ? WHERE robot_id IS NULL', (robot_id,))
        cursor.execute('UPDATE generated_reports SET robot_id = ? WHERE robot_id IS NULL', (robot_id,))


def get_global_settings():
    """Get all global settings as a dict."""
    from config import DEFAULT_SETTINGS
    settings = DEFAULT_SETTINGS.copy()
    with db_context() as (conn, cursor):
        cursor.execute('SELECT key, value FROM global_settings')
        for row in cursor.fetchall():
            try:
                settings[row['key']] = json.loads(row['value'])
            except (json.JSONDecodeError, TypeError):
                settings[row['key']] = row['value']
    return settings


def save_global_settings(settings_dict):
    """Save settings dict to global_settings table (UPSERT each key)."""
    with db_context() as (conn, cursor):
        for key, value in settings_dict.items():
            json_value = json.dumps(value, ensure_ascii=False)
            cursor.execute('''
                INSERT INTO global_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            ''', (key, json_value))


def init_db():
    """Initialize database schema with migrations."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Core tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patrol_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time TEXT,
            status TEXT,
            robot_serial TEXT,
            report_content TEXT,
            model_id TEXT,
            token_usage TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            video_path TEXT,
            video_analysis TEXT,
            robot_id TEXT,
            report_input_tokens INTEGER,
            report_output_tokens INTEGER,
            report_total_tokens INTEGER,
            telegram_input_tokens INTEGER,
            telegram_output_tokens INTEGER,
            telegram_total_tokens INTEGER,
            video_input_tokens INTEGER,
            video_output_tokens INTEGER,
            video_total_tokens INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS generated_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT,
            end_date TEXT,
            report_content TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            timestamp TEXT,
            robot_id TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspection_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            point_name TEXT,
            coordinate_x REAL,
            coordinate_y REAL,
            prompt TEXT,
            ai_response TEXT,
            is_ng INTEGER,
            ai_description TEXT,
            token_usage TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            image_path TEXT,
            timestamp TEXT,
            robot_moving_status TEXT,
            robot_id TEXT,
            FOREIGN KEY(run_id) REFERENCES patrol_runs(id)
        )
    ''')

    # Multi-robot tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS robots (
            robot_id TEXT PRIMARY KEY,
            robot_name TEXT NOT NULL,
            robot_ip TEXT,
            last_seen TEXT,
            status TEXT DEFAULT 'offline'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS edge_ai_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            rule TEXT,
            response TEXT,
            image_path TEXT,
            timestamp TEXT,
            robot_id TEXT,
            stream_source TEXT,
            FOREIGN KEY(run_id) REFERENCES patrol_runs(id)
        )
    ''')

    # Run migrations for existing databases
    _run_migrations(cursor)

    conn.commit()
    conn.close()


def _run_migrations(cursor):
    """Apply database migrations for backward compatibility."""
    migrations = [
        # (check_column, table, columns_to_add)
        ('is_ng', 'inspection_results', ['is_ng INTEGER', 'ai_description TEXT', 'token_usage TEXT']),
        ('prompt_tokens', 'inspection_results', ['prompt_tokens INTEGER', 'candidate_tokens INTEGER', 'total_tokens INTEGER']),
        ('token_usage', 'patrol_runs', ['token_usage TEXT']),
        ('prompt_tokens', 'patrol_runs', ['prompt_tokens INTEGER', 'candidate_tokens INTEGER', 'total_tokens INTEGER']),
        ('robot_moving_status', 'inspection_results', ['robot_moving_status TEXT']),
        ('video_path', 'patrol_runs', ['video_path TEXT', 'video_analysis TEXT']),
        # Multi-robot migrations
        ('robot_id', 'patrol_runs', ['robot_id TEXT']),
        ('robot_id', 'inspection_results', ['robot_id TEXT']),
        ('robot_id', 'generated_reports', ['robot_id TEXT']),
        ('stream_source', 'edge_ai_alerts', ['stream_source TEXT']),
        # Cloud sync tracking
        ('sync_status', 'patrol_runs', ['sync_status TEXT']),
        ('sync_status', 'inspection_results', ['sync_status TEXT']),
        ('sync_status', 'generated_reports', ['sync_status TEXT']),
        ('sync_status', 'edge_ai_alerts', ['sync_status TEXT']),
    ]

    for check_col, table, columns in migrations:
        try:
            cursor.execute(f"SELECT {check_col} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            print(f"Migrating: Adding columns to {table}...")
            for col_def in columns:
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                except Exception as e:
                    print(f"  Migration warning: {e}")

    # Rename prompt_tokens → input_tokens, candidate_tokens → output_tokens
    _rename_token_columns(cursor)

    # Add per-category token columns to patrol_runs
    _add_category_token_columns(cursor)

    # Rename live_alerts → edge_ai_alerts and settings keys
    _rename_live_to_edge_ai(cursor)


def _rename_token_columns(cursor):
    """Rename prompt_tokens→input_tokens, candidate_tokens→output_tokens on all 3 tables."""
    renames = [
        ('patrol_runs', 'prompt_tokens', 'input_tokens'),
        ('patrol_runs', 'candidate_tokens', 'output_tokens'),
        ('inspection_results', 'prompt_tokens', 'input_tokens'),
        ('inspection_results', 'candidate_tokens', 'output_tokens'),
        ('generated_reports', 'prompt_tokens', 'input_tokens'),
        ('generated_reports', 'candidate_tokens', 'output_tokens'),
    ]
    for table, old_col, new_col in renames:
        # Check if old column exists and new one doesn't
        try:
            cursor.execute(f"SELECT {old_col} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            continue  # old column doesn't exist, nothing to rename
        try:
            cursor.execute(f"SELECT {new_col} FROM {table} LIMIT 1")
            continue  # new column already exists, skip
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")
            print(f"Migrating: Renamed {table}.{old_col} → {new_col}")
        except Exception as e:
            print(f"  Migration warning (rename {table}.{old_col}): {e}")


def _add_category_token_columns(cursor):
    """Add per-category token columns to patrol_runs."""
    new_cols = [
        'report_input_tokens INTEGER',
        'report_output_tokens INTEGER',
        'report_total_tokens INTEGER',
        'telegram_input_tokens INTEGER',
        'telegram_output_tokens INTEGER',
        'telegram_total_tokens INTEGER',
        'video_input_tokens INTEGER',
        'video_output_tokens INTEGER',
        'video_total_tokens INTEGER',
    ]
    for col_def in new_cols:
        col_name = col_def.split()[0]
        try:
            cursor.execute(f"SELECT {col_name} FROM patrol_runs LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute(f"ALTER TABLE patrol_runs ADD COLUMN {col_def}")
                print(f"Migrating: Added patrol_runs.{col_name}")
            except Exception as e:
                print(f"  Migration warning: {e}")


def _rename_live_to_edge_ai(cursor):
    """Rename live_alerts table to edge_ai_alerts and migrate settings keys."""
    # Rename table (idempotent: check if old table exists)
    try:
        cursor.execute("SELECT 1 FROM live_alerts LIMIT 1")
        # Old table exists — rename it
        cursor.execute("ALTER TABLE live_alerts RENAME TO edge_ai_alerts")
        print("Migrating: Renamed table live_alerts → edge_ai_alerts")
    except sqlite3.OperationalError:
        pass  # Old table doesn't exist (already renamed or fresh DB)

    # Rename settings keys in global_settings
    renames = [
        ('enable_live_monitor', 'enable_edge_ai'),
        ('live_monitor_rules', 'edge_ai_rules'),
    ]
    for old_key, new_key in renames:
        try:
            cursor.execute(
                "UPDATE global_settings SET key = ? WHERE key = ?",
                (new_key, old_key)
            )
            if cursor.rowcount > 0:
                print(f"Migrating: Renamed setting {old_key} → {new_key}")
        except Exception as e:
            print(f"  Migration warning (rename setting {old_key}): {e}")
