import threading
import time
import io
import json
import os
import re
import math
from datetime import datetime
import flask
from flask import Flask, jsonify, request, send_file, render_template, send_from_directory
from PIL import Image

# Config and infrastructure (must run before service imports)
from config import *
from config import _LEGACY_SETTINGS_FILE, _LEGACY_IMAGES_DIR
from utils import load_json, save_json
from database import (
    init_db, db_context, save_generated_report, get_generated_reports,
    register_robot, backfill_robot_id, update_robot_heartbeat, get_all_robots
)

SENSITIVE_KEYS = ['gemini_api_key', 'telegram_bot_token', 'telegram_user_id']
ROBOT_ID_PATTERN = re.compile(r'^robot-[a-z0-9-]+$')

# Create directories and initialize DB before importing services
# (services read DB at module level during instantiation)
ensure_dirs()
init_db()

import settings_service
from robot_service import robot_service
from patrol_service import patrol_service
from cloud_ai_service import ai_service
from edge_ai_service import test_edge_ai
from relay_manager import relay_service_client
from frame_hub import frame_hub
from pdf_service import generate_patrol_report, generate_analysis_report

import logging

app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'templates'),
            static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'static'))

# Logging
from logger import TimezoneFormatter

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

if not root_logger.handlers:
    formatter = TimezoneFormatter('%(asctime)s %(levelname)s: %(message)s')

    # File handler with robot-prefixed log
    log_filename = f"{ROBOT_ID}_app.log" if ROBOT_ID != "default" else "app.log"
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, log_filename))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Console handler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

# Suppress Flask/Werkzeug request logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/state')
def get_state():
    state = robot_service.get_state()
    state['robot_id'] = ROBOT_ID
    state['robot_name'] = ROBOT_NAME
    return jsonify(state)

@app.route('/api/robot_info')
def get_robot_info():
    return jsonify({"robot_id": ROBOT_ID, "robot_name": ROBOT_NAME})

@app.route('/api/robots')
def get_robots():
    return jsonify(get_all_robots())

@app.route('/api/map')
def get_map():
    map_bytes = robot_service.get_map_bytes()
    if map_bytes:
        return send_file(io.BytesIO(map_bytes), mimetype='image/png')
    else:
        return "Map not available", 404

@app.route('/api/move', methods=['POST'])
def move_robot():
    data = request.json
    x = data.get('x')
    y = data.get('y')
    theta = data.get('theta', 0.0)

    if x is None or y is None:
        return jsonify({"error": "Missing x or y"}), 400

    try:
        x, y, theta = float(x), float(y), float(theta)
    except (TypeError, ValueError):
        return jsonify({"error": "x, y, theta must be numbers"}), 400

    if not (-2 * math.pi <= theta <= 2 * math.pi):
        return jsonify({"error": "theta must be between -2π and 2π"}), 400

    if robot_service.move_to(x, y, theta, wait=False):
        return jsonify({"status": "Moving", "target": {"x": x, "y": y, "theta": theta}})
    else:
        return jsonify({"error": "Robot not connected or failed"}), 503

@app.route('/api/manual_control', methods=['POST'])
def manual_control():
    data = request.json
    action = data.get('action')

    try:
        if action == 'forward':
            robot_service.move_forward(distance=0.1, speed=0.1)
        elif action == 'backward':
            robot_service.move_forward(distance=-0.1, speed=0.1)
        elif action == 'left':
            robot_service.rotate(angle=0.1745) # ~10 degrees
        elif action == 'right':
             robot_service.rotate(angle=-0.1745) # ~-10 degrees
        else:
            return jsonify({"error": "Invalid action"}), 400

        return jsonify({"status": "Command sent", "action": action})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/return_home', methods=['POST'])
def return_home():
    try:
        robot_service.return_home()
        return jsonify({"status": "Returning home"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cancel_command', methods=['POST'])
def cancel_command():
    try:
        robot_service.cancel_command()
        return jsonify({"status": "Command cancelled"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def gen_frames(camera_func):
    while True:
        try:
            image = camera_func()
            if image:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + image.data + b'\r\n')
            time.sleep(0.1) # ~10fps
        except Exception as e:
            time.sleep(1)

@app.route('/api/camera/front')
def video_feed_front():
    return flask.Response(gen_frames(frame_hub.get_latest_frame),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/camera/back')
def video_feed_back():
    return flask.Response(gen_frames(robot_service.get_back_camera_image),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/test_ai', methods=['POST'])
def test_ai_route():
    try:
        img_response = frame_hub.get_latest_frame()
        if not img_response:
             return jsonify({"error": "Robot camera not available"}), 503

        image = Image.open(io.BytesIO(img_response.data))

        user_prompt = request.json.get('prompt', 'Describe what you see and check if everything is normal.')
        settings = settings_service.get_all()
        sys_prompt = settings.get('system_prompt', '')

        response_obj = ai_service.generate_inspection(image, user_prompt, sys_prompt)

        # Handle new structure
        if isinstance(response_obj, dict) and "result" in response_obj:
            result_text = response_obj["result"]
            usage_data = response_obj.get("usage", {})
        else:
            result_text = response_obj
            usage_data = {}

        return jsonify({"result": result_text, "prompt": user_prompt, "usage": usage_data})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Test Edge AI API (relay → VILA JPS → WebSocket alerts) ---

@app.route('/api/test_edge_ai/start', methods=['POST'])
def test_edge_ai_start():
    if test_edge_ai.is_running:
        return jsonify({"error": "Test already running"}), 409

    data = request.json or {}
    settings = settings_service.get_all()

    jetson_host = data.get('jetson_host') or settings.get('jetson_host', '')
    if not jetson_host:
        return jsonify({"error": "Jetson Host is required"}), 400

    from config import JETSON_JPS_API_PORT, JETSON_MEDIAMTX_PORT
    vila_jps_url = f"http://{jetson_host}:{JETSON_JPS_API_PORT}"

    rules = data.get('rules') or settings.get('edge_ai_rules', [])
    if not rules:
        return jsonify({"error": "At least one alert rule is required"}), 400

    stream_source = data.get('stream_source', 'robot_camera')
    external_rtsp_url = data.get('external_rtsp_url') or settings.get('external_rtsp_url', '')

    test_edge_ai.start({
        "vila_jps_url": vila_jps_url,
        "rules": rules,
        "stream_source": stream_source,
        "external_rtsp_url": external_rtsp_url,
        "robot_id": ROBOT_ID,
        "mediamtx_internal": f"{jetson_host}:{JETSON_MEDIAMTX_PORT}",
        "mediamtx_external": f"localhost:{JETSON_MEDIAMTX_PORT}",
    })

    if test_edge_ai.error:
        err = test_edge_ai.error
        test_edge_ai.error = None
        return jsonify({"error": err}), 500

    return jsonify({"status": "started"})


@app.route('/api/test_edge_ai/stop', methods=['POST'])
def test_edge_ai_stop():
    test_edge_ai.stop()
    return jsonify({"status": "stopped"})


@app.route('/api/test_edge_ai/status', methods=['GET'])
def test_edge_ai_status():
    return jsonify(test_edge_ai.get_status())


@app.route('/api/test_edge_ai/snapshot', methods=['GET'])
def test_edge_ai_snapshot():
    """Return latest JPEG frame captured from mediamtx RTSP relay."""
    frame = test_edge_ai.get_snapshot()
    if not frame:
        return '', 204
    return flask.Response(frame, mimetype='image/jpeg')


# --- Relay & VILA Health API ---

@app.route('/api/relay/status', methods=['GET'])
def relay_status():
    if not relay_service_client:
        return jsonify({})
    return jsonify(relay_service_client.get_status())


@app.route('/api/relay/test', methods=['POST'])
def relay_test():
    """Quick test: start robot camera relay via frame_hub + relay service, wait 3s, check status, stop."""
    if not relay_service_client:
        return jsonify({"error": "Relay service not configured (RELAY_SERVICE_URL empty)"}), 400

    settings = settings_service.get_all()
    jetson_host = settings.get("jetson_host", "")
    if not jetson_host:
        return jsonify({"error": "jetson_host not configured"}), 400

    key = f"{ROBOT_ID}/camera"
    try:
        from config import JETSON_MEDIAMTX_PORT
        raw_path = f"/raw/{ROBOT_ID}/camera"
        frame_hub.start_rtsp_push(f"{jetson_host}:{JETSON_MEDIAMTX_PORT}", raw_path)
        source_url = f"rtsp://localhost:{JETSON_MEDIAMTX_PORT}{raw_path}"
        rtsp_path, err = relay_service_client.start_relay(key, source_url)
        if err:
            frame_hub.stop_rtsp_push()
            return jsonify({"error": err}), 500
        time.sleep(3)
        status = relay_service_client.get_status()
        frame_hub.stop_rtsp_push()
        relay_service_client.stop_relay(key)
        return jsonify({"rtsp_path": rtsp_path, "status": status})
    except Exception as e:
        try:
            frame_hub.stop_rtsp_push()
            relay_service_client.stop_all()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route('/api/edge_ai/health', methods=['GET'])
def edge_ai_health():
    """Check VILA JPS health endpoint."""
    settings = settings_service.get_all()
    jetson_host = settings.get("jetson_host", "")
    if not jetson_host:
        return jsonify({"error": "Jetson Host not configured"}), 400
    from config import JETSON_JPS_API_PORT
    vila_jps_url = f"http://{jetson_host}:{JETSON_JPS_API_PORT}"
    try:
        import requests as req
        resp = req.get(f"{vila_jps_url}/api/v1/health/ready", timeout=5)
        return jsonify({"status": "ok" if resp.ok else "error", "code": resp.status_code})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 503


# --- Patrol & Settings API ---

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        new_settings = request.json
        try:
            # Load existing settings to preserve keys not in request
            current_settings = settings_service.get_all()
            # Skip masked sensitive values (don't overwrite real value with mask)
            for key in SENSITIVE_KEYS:
                val = new_settings.get(key, '')
                if isinstance(val, str) and val.startswith('****'):
                    new_settings.pop(key, None)
            current_settings.update(new_settings)
            # Exclude robot_ip from global settings (it's per-instance via env)
            current_settings.pop('robot_ip', None)

            settings_service.save(current_settings)
            if 'enable_idle_stream' in new_settings:
                frame_hub.on_idle_stream_changed(current_settings.get('enable_idle_stream', True))
            return jsonify({"status": "saved"})
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")
            return jsonify({"error": f"Failed to save settings: {str(e)}"}), 500
    else:
        settings = settings_service.get_all()
        for key in SENSITIVE_KEYS:
            val = settings.get(key, '')
            if val:
                settings[key] = '****' + val[-4:]
        return jsonify(settings)

@app.route('/api/points', methods=['GET', 'POST', 'DELETE'])
def handle_points():
    points = load_json(POINTS_FILE, [])
    if request.method == 'GET':
        return jsonify(points)
    elif request.method == 'POST':
        new_point = request.json
        if not isinstance(new_point, dict):
            return jsonify({"error": "Invalid point data"}), 400
        if 'name' not in new_point or not isinstance(new_point['name'], str):
            return jsonify({"error": "Point name is required"}), 400
        if 'x' not in new_point or 'y' not in new_point:
            return jsonify({"error": "Point x and y are required"}), 400
        try:
            new_point['x'] = float(new_point['x'])
            new_point['y'] = float(new_point['y'])
        except (TypeError, ValueError):
            return jsonify({"error": "x and y must be numbers"}), 400

        if 'id' not in new_point:
            new_point['id'] = str(int(time.time() * 1000))

        updated = False
        for i, p in enumerate(points):
            if p.get('id') == new_point.get('id'):
                points[i] = new_point
                updated = True
                break
        if not updated:
            points.append(new_point)

        try:
            save_json(POINTS_FILE, points)
            return jsonify({"status": "saved", "id": new_point['id']})
        except Exception as e:
            logging.error(f"Failed to save points: {e}")
            return jsonify({"error": f"Failed to save points: {str(e)}"}), 500

    elif request.method == 'DELETE':
        point_id = request.args.get('id')
        points = [p for p in points if p.get('id') != point_id]
        try:
            save_json(POINTS_FILE, points)
            return jsonify({"status": "deleted"})
        except Exception as e:
            logging.error(f"Failed to delete point: {e}")
            return jsonify({"error": f"Failed to delete point: {str(e)}"}), 500

@app.route('/api/points/reorder', methods=['POST'])
def reorder_points():
    new_points = request.json
    if isinstance(new_points, list):
        try:
            save_json(POINTS_FILE, new_points)
            return jsonify({"status": "reordered"})
        except Exception as e:
            logging.error(f"Failed to reorder points: {e}")
            return jsonify({"error": f"Failed to reorder points: {str(e)}"}), 500
    return jsonify({"error": "Invalid format, expected list"}), 400

@app.route('/api/points/export', methods=['GET'])
def export_points():
    return send_file(POINTS_FILE, as_attachment=True, download_name='patrol_points.json')

@app.route('/api/points/import', methods=['POST'])
def import_points():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file:
        try:
            data = json.load(file)
            if isinstance(data, list):
                save_json(POINTS_FILE, data)
                return jsonify({"status": "imported", "count": len(data)})
            else:
                return jsonify({"error": "Invalid format, expected list"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 400

@app.route('/api/points/from_robot', methods=['GET'])
def get_points_from_robot():
    """Fetch locations saved on the robot and merge with existing points"""
    try:
        # Get locations from robot
        robot_locations = robot_service.get_locations()
        if not robot_locations:
            return jsonify({"error": "No locations found on robot or robot not connected"}), 404

        # Load existing points
        existing_points = load_json(POINTS_FILE, [])

        # Helper function to compare coordinates (2 decimal places)
        def coords_match(p1, p2):
            return (round(p1.get('x', 0), 2) == round(p2.get('x', 0), 2) and
                    round(p1.get('y', 0), 2) == round(p2.get('y', 0), 2))

        # Check for duplicates and add new locations
        added = []
        skipped = []

        for loc in robot_locations:
            # Check if this location already exists (same name AND same coordinates)
            is_duplicate = False
            for existing in existing_points:
                if existing.get('name') == loc['name'] and coords_match(existing, loc):
                    is_duplicate = True
                    break

            if is_duplicate:
                skipped.append(loc['name'])
            else:
                # Add as new patrol point
                new_point = {
                    "id": str(int(time.time() * 1000)) + "_" + loc['id'][:8] if loc.get('id') else str(int(time.time() * 1000)),
                    "name": loc['name'],
                    "x": loc['x'],
                    "y": loc['y'],
                    "theta": loc.get('theta', 0.0),
                    "prompt": "Is everything normal?",
                    "enabled": True,
                    "source": "robot"
                }
                existing_points.append(new_point)
                added.append(loc['name'])

        # Save updated points
        if added:
            save_json(POINTS_FILE, existing_points)

        return jsonify({
            "status": "success",
            "added": added,
            "skipped": skipped,
            "total_robot_locations": len(robot_locations),
            "total_points": len(existing_points)
        })

    except Exception as e:
        logging.error(f"Error fetching locations from robot: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/patrol/status', methods=['GET'])
def get_patrol_status_route():
    return jsonify(patrol_service.get_status())

@app.route('/api/patrol/start', methods=['POST'])
def start_patrol_route():
    success, msg = patrol_service.start_patrol()
    if success:
        return jsonify({"status": "started"})
    else:
        return jsonify({"error": msg}), 400

@app.route('/api/patrol/stop', methods=['POST'])
def stop_patrol_route():
    patrol_service.stop_patrol()
    return jsonify({"status": "stopping"})

# --- Scheduled Patrol APIs ---

@app.route('/api/patrol/schedule', methods=['GET', 'POST'])
def handle_patrol_schedule():
    if request.method == 'GET':
        return jsonify(patrol_service.get_schedule())
    elif request.method == 'POST':
        data = request.json
        time_str = data.get('time')
        days = data.get('days')  # Optional: list of day numbers (0=Mon, 6=Sun)
        enabled = data.get('enabled', True)

        if not time_str:
            return jsonify({"error": "Time is required"}), 400

        # Validate time format
        try:
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return jsonify({"error": "Invalid time format. Use HH:MM"}), 400

        if days is not None:
            if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
                return jsonify({"error": "days must be a list of integers 0-6"}), 400

        schedule = patrol_service.add_schedule(time_str, days, enabled)
        return jsonify({"status": "added", "schedule": schedule})

@app.route('/api/patrol/schedule/<schedule_id>', methods=['PUT', 'DELETE'])
def handle_patrol_schedule_item(schedule_id):
    if request.method == 'PUT':
        data = request.json
        time_str = data.get('time')
        days = data.get('days')
        enabled = data.get('enabled')

        # Validate time format if provided
        if time_str:
            try:
                datetime.strptime(time_str, "%H:%M")
            except ValueError:
                return jsonify({"error": "Invalid time format. Use HH:MM"}), 400

        patrol_service.update_schedule(schedule_id, time_str, days, enabled)
        return jsonify({"status": "updated"})
    elif request.method == 'DELETE':
        patrol_service.delete_schedule(schedule_id)
        return jsonify({"status": "deleted"})

@app.route('/api/patrol/edge_ai_alerts', methods=['GET'])
def get_edge_ai_alerts():
    """Return edge AI alerts for the current patrol run."""
    current_run_id = patrol_service.current_run_id
    if not current_run_id:
        return jsonify([])

    with db_context() as (conn, cursor):
        cursor.execute(
            'SELECT id, rule, response, image_path, timestamp, stream_source FROM edge_ai_alerts WHERE run_id = ? ORDER BY id DESC',
            (current_run_id,)
        )
        rows = cursor.fetchall()

    return jsonify([dict(r) for r in rows])

@app.route('/api/patrol/results', methods=['GET'])
def get_patrol_results():
    """Return inspection results for the current patrol run only."""
    current_run_id = patrol_service.current_run_id

    # If no active patrol, return empty list
    if not current_run_id:
        return jsonify([])

    with db_context() as (conn, cursor):
        cursor.execute(
            'SELECT point_name, ai_response, timestamp FROM inspection_results WHERE run_id = ? ORDER BY id ASC',
            (current_run_id,)
        )
        rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "point_name": row[0],
            "result": row[1],
            "timestamp": row[2]
        })
    return jsonify(results)

# --- Stats APIs ---

@app.route('/api/stats/token_usage', methods=['GET'])
def get_token_usage_stats():
    robot_id_filter = request.args.get('robot_id')

    with db_context() as (conn, cursor):
        # 1. Get stats from patrol_runs
        if robot_id_filter:
            query_runs = '''
                SELECT substr(start_time, 1, 10) as date,
                       SUM(COALESCE(input_tokens, 0)) as input,
                       SUM(COALESCE(output_tokens, 0)) as output,
                       SUM(COALESCE(total_tokens, 0)) as total
                FROM patrol_runs
                WHERE start_time IS NOT NULL AND robot_id = ?
                GROUP BY substr(start_time, 1, 10)
            '''
            cursor.execute(query_runs, (robot_id_filter,))
        else:
            query_runs = '''
                SELECT substr(start_time, 1, 10) as date,
                       SUM(COALESCE(input_tokens, 0)) as input,
                       SUM(COALESCE(output_tokens, 0)) as output,
                       SUM(COALESCE(total_tokens, 0)) as total
                FROM patrol_runs
                WHERE start_time IS NOT NULL
                GROUP BY substr(start_time, 1, 10)
            '''
            cursor.execute(query_runs)
        run_rows = cursor.fetchall()

        # 2. Get stats from generated_reports
        if robot_id_filter:
            query_reports = '''
                SELECT substr(timestamp, 1, 10) as date,
                       SUM(COALESCE(input_tokens, 0)) as input,
                       SUM(COALESCE(output_tokens, 0)) as output,
                       SUM(COALESCE(total_tokens, 0)) as total
                FROM generated_reports
                WHERE timestamp IS NOT NULL AND robot_id = ?
                GROUP BY substr(timestamp, 1, 10)
            '''
            cursor.execute(query_reports, (robot_id_filter,))
        else:
            query_reports = '''
                SELECT substr(timestamp, 1, 10) as date,
                       SUM(COALESCE(input_tokens, 0)) as input,
                       SUM(COALESCE(output_tokens, 0)) as output,
                       SUM(COALESCE(total_tokens, 0)) as total
                FROM generated_reports
                WHERE timestamp IS NOT NULL
                GROUP BY substr(timestamp, 1, 10)
            '''
            cursor.execute(query_reports)
        report_rows = cursor.fetchall()

    # Merge results
    usage_map = {}

    for row in run_rows:
        if row['date']:
            date = row['date']
            if date not in usage_map:
                usage_map[date] = {'input': 0, 'output': 0, 'total': 0}
            usage_map[date]['input'] += row['input'] or 0
            usage_map[date]['output'] += row['output'] or 0
            usage_map[date]['total'] += row['total'] or 0

    for row in report_rows:
        if row['date']:
            date = row['date']
            if date not in usage_map:
                usage_map[date] = {'input': 0, 'output': 0, 'total': 0}
            usage_map[date]['input'] += row['input'] or 0
            usage_map[date]['output'] += row['output'] or 0
            usage_map[date]['total'] += row['total'] or 0

    # Sort by date and format results
    results = [
        {"date": k, "input": v['input'], "output": v['output'], "total": v['total']}
        for k, v in sorted(usage_map.items())
    ]
    return jsonify(results)


@app.route('/api/reports', methods=['GET'])
def list_reports():
    return jsonify(get_generated_reports())


@app.route('/api/reports/generate', methods=['POST'])
def generate_report_route():
    data = request.json
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    user_prompt = data.get('prompt')
    report_robot_id = data.get('robot_id')

    if not start_date or not end_date:
        return jsonify({"error": "Start date and end date are required"}), 400

    try:
        # 1. Fetch Inspection Results
        query_start = f"{start_date} 00:00:00"
        query_end = f"{end_date} 23:59:59"

        with db_context() as (conn, cursor):
            if report_robot_id:
                cursor.execute('''
                    SELECT point_name, result, timestamp, is_ng, description FROM (
                        SELECT point_name, ai_response as result, timestamp, is_ng, ai_description as description
                        FROM inspection_results
                        WHERE timestamp BETWEEN ? AND ? AND robot_id = ?
                        ORDER BY timestamp ASC
                    )
                ''', (query_start, query_end, report_robot_id))
            else:
                cursor.execute('''
                    SELECT point_name, result, timestamp, is_ng, description FROM (
                        SELECT point_name, ai_response as result, timestamp, is_ng, ai_description as description
                        FROM inspection_results
                        WHERE timestamp BETWEEN ? AND ?
                        ORDER BY timestamp ASC
                    )
                ''', (query_start, query_end))

            rows = cursor.fetchall()

        if not rows:
             return jsonify({"error": "No inspection data found for this period"}), 404

        # 2. Format Context
        context = f"Inspection Report Data ({start_date} to {end_date}):\n\n"
        for row in rows:
            status = "NG" if row['is_ng'] else "OK"
            context += f"- [{row['timestamp']}] Point: {row['point_name']} | Status: {status} | Details: {row['description'] or row['result']}\n"

        # 3. Call AI Service
        if not user_prompt:
             settings = settings_service.get_all()
             default_prompt = DEFAULT_SETTINGS.get('multiday_report_prompt', "Generate a concise summary report.")
             final_prompt = settings.get('multiday_report_prompt', default_prompt)
        else:
             final_prompt = user_prompt

        response = ai_service.generate_report(f"{final_prompt}\n\nContext:\n{context}")

        # 4. Save to Database
        report_id = save_generated_report(
            start_date, end_date, response['result'], response['usage'],
            robot_id=report_robot_id
        )

        return jsonify({
            "id": report_id,
            "report": response['result'],
            "usage": response['usage']
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/reports/generate/pdf', methods=['GET'])
def generate_multiday_report_pdf():
    """Generate PDF for multi-day analysis report from saved report."""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if not start_date or not end_date:
        return jsonify({"error": "Start date and end date are required"}), 400

    try:
        # Fetch the most recent generated report for this date range
        with db_context() as (conn, cursor):
            cursor.execute('''
                SELECT report_content, input_tokens, output_tokens, total_tokens
                FROM generated_reports
                WHERE start_date = ? AND end_date = ?
                ORDER BY timestamp DESC LIMIT 1
            ''', (start_date, end_date))
            row = cursor.fetchone()

        if not row or not row['report_content']:
            return jsonify({"error": "No report found for this date range. Please generate a report first."}), 404

        report_content = row['report_content']

        # This report's own token usage
        report_tokens = {
            'input': row['input_tokens'] or 0,
            'output': row['output_tokens'] or 0,
            'total': row['total_tokens'] or 0,
        }

        # Period patrol token totals (inspection + per-run report generation)
        query_start = f"{start_date} 00:00:00"
        query_end = f"{end_date} 23:59:59"

        with db_context() as (conn, cursor):
            cursor.execute('''
                SELECT
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens
                FROM patrol_runs
                WHERE start_time BETWEEN ? AND ?
            ''', (query_start, query_end))
            pr = cursor.fetchone()

        period_tokens = {
            'input': pr['input_tokens'] if pr else 0,
            'output': pr['output_tokens'] if pr else 0,
            'total': pr['total_tokens'] if pr else 0,
        }

        # Generate PDF
        pdf_bytes = generate_analysis_report(
            content=report_content,
            start_date=start_date,
            end_date=end_date,
            period_tokens=period_tokens,
            report_tokens=report_tokens
        )

        # Return PDF file
        filename = f'analysis_report_{start_date}_{end_date}.pdf'
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logging.error(f"Failed to generate PDF report: {e}")
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500


# --- History APIs ---

@app.route('/api/history', methods=['GET'])
def get_history():
    robot_id_filter = request.args.get('robot_id')

    with db_context() as (conn, cursor):
        if robot_id_filter:
            cursor.execute(
                'SELECT id, start_time, end_time, status, robot_serial, report_content, model_id, total_tokens, robot_id '
                'FROM patrol_runs WHERE robot_id = ? ORDER BY id DESC',
                (robot_id_filter,)
            )
        else:
            cursor.execute(
                'SELECT id, start_time, end_time, status, robot_serial, report_content, model_id, total_tokens, robot_id '
                'FROM patrol_runs ORDER BY id DESC'
            )
        rows = cursor.fetchall()

    result = []
    for row in rows:
        result.append(dict(row))
    return jsonify(result)

@app.route('/api/history/<int:run_id>', methods=['GET'])
def get_history_detail(run_id):
    with db_context() as (conn, cursor):
        cursor.execute('SELECT * FROM patrol_runs WHERE id = ?', (run_id,))
        run = cursor.fetchone()
        if not run:
            return jsonify({"error": "Run not found"}), 404

        cursor.execute('SELECT * FROM inspection_results WHERE run_id = ?', (run_id,))
        inspections = cursor.fetchall()

        cursor.execute('SELECT * FROM edge_ai_alerts WHERE run_id = ? ORDER BY id ASC', (run_id,))
        edge_ai_alerts = cursor.fetchall()

    return jsonify({
        "run": dict(run),
        "inspections": [dict(i) for i in inspections],
        "edge_ai_alerts": [dict(a) for a in edge_ai_alerts]
    })

@app.route('/api/video/<int:run_id>')
def download_video(run_id):
    """Download the recorded video for a patrol run."""
    with db_context() as (conn, cursor):
        cursor.execute('SELECT video_path FROM patrol_runs WHERE id = ?', (run_id,))
        row = cursor.fetchone()

    if not row or not row['video_path']:
        return jsonify({"error": "No video for this run"}), 404

    video_path = row['video_path']
    if not os.path.isabs(video_path):
        video_path = os.path.join(DATA_DIR, video_path)

    if not os.path.exists(video_path):
        return jsonify({"error": "Video file not found"}), 404

    filename = os.path.basename(video_path)
    return send_file(video_path, as_attachment=True, download_name=filename)

@app.route('/api/report/<int:run_id>/pdf')
def download_pdf_report(run_id):
    """Generate and download PDF report for a patrol run"""
    try:
        # Get start_time for filename
        with db_context() as (conn, cursor):
            cursor.execute('SELECT start_time FROM patrol_runs WHERE id = ?', (run_id,))
            row = cursor.fetchone()

        if row and row['start_time']:
            start_time_str = row['start_time'].replace(' ', '_').replace(':', '')
            filename = f'patrol_report_{run_id}_{start_time_str}.pdf'
        else:
            filename = f'patrol_report_{run_id}.pdf'

        pdf_bytes = generate_patrol_report(run_id)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logging.error(f"Failed to generate PDF for run {run_id}: {e}")
        return jsonify({"error": f"Failed to generate PDF: {str(e)}"}), 500

@app.route('/api/images/<path:filename>')
def serve_image(filename):
    # Try per-robot images dir first, then fallback to legacy
    robot_path = os.path.join(ROBOT_IMAGES_DIR, filename)
    if os.path.exists(robot_path):
        return send_from_directory(ROBOT_IMAGES_DIR, filename)
    # Fallback to legacy images dir
    if os.path.exists(os.path.join(_LEGACY_IMAGES_DIR, filename)):
        return send_from_directory(_LEGACY_IMAGES_DIR, filename)
    return "Image not found", 404

@app.route('/api/robots/<robot_id>/images/<path:filename>')
def serve_robot_image(robot_id, filename):
    """Serve images from any robot's directory."""
    if not ROBOT_ID_PATTERN.match(robot_id):
        return "Invalid robot ID", 400
    robot_images_dir = os.path.join(DATA_DIR, robot_id, "report", "images")
    if os.path.exists(os.path.join(robot_images_dir, filename)):
        return send_from_directory(robot_images_dir, filename)
    # Fallback to legacy
    if os.path.exists(os.path.join(_LEGACY_IMAGES_DIR, filename)):
        return send_from_directory(_LEGACY_IMAGES_DIR, filename)
    return "Image not found", 404


# --- Heartbeat Thread ---

def _heartbeat_loop():
    """Update robot heartbeat every 30 seconds, reflecting actual Kachaka connection status."""
    while True:
        try:
            is_connected = robot_service.get_client() is not None
            update_robot_heartbeat(ROBOT_ID, is_connected)
        except Exception as e:
            logging.warning(f"Heartbeat error: {e}")
        time.sleep(30)


if __name__ == '__main__':
    # Initialize DB and run migrations
    init_db()

    # Migrate legacy settings.json to DB
    settings_service.migrate_from_json(_LEGACY_SETTINGS_FILE)

    # Migrate legacy per-robot files
    migrate_legacy_files()

    # Register this robot
    register_robot(ROBOT_ID, ROBOT_NAME, ROBOT_IP)

    # Backfill robot_id on existing data
    backfill_robot_id(ROBOT_ID)

    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=False)
