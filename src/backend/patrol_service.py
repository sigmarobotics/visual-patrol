"""
Patrol Service - Autonomous patrol orchestration with scheduled execution.
"""

import threading
import queue
import time
import os
import uuid
import io
from datetime import datetime
from PIL import Image

from config import ROBOT_ID, ROBOT_NAME, ROBOT_IMAGES_DIR, ROBOT_DATA_DIR, POINTS_FILE, SCHEDULE_FILE
import settings_service
import requests
from utils import load_json, save_json, get_current_time_str, get_filename_timestamp
from database import get_db_connection, db_context, update_run_tokens
from robot_service import robot_service
from frame_hub import frame_hub
from cloud_ai_service import ai_service, parse_ai_response
from pdf_service import generate_patrol_report
from logger import get_logger
from video_recorder import VideoRecorder

logger = get_logger("patrol_service", "patrol_service.log")


class PatrolService:
    """Manages autonomous patrol missions with AI-powered inspection."""

    def __init__(self):
        self.is_patrolling = False
        self.patrol_status = "Idle"
        self.current_patrol_index = -1
        self.current_run_id = None

        # Thread safety
        self.patrol_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.schedule_lock = threading.Lock()
        self.patrol_thread = None

        # Async inspection queue
        self.inspection_queue = queue.Queue()
        threading.Thread(target=self._inspection_worker, daemon=True).start()

        # Scheduled patrols
        self.scheduled_patrols = []
        self._load_schedule()
        threading.Thread(target=self._schedule_checker, daemon=True).start()

    # === Schedule Management ===

    def _load_schedule(self):
        with self.schedule_lock:
            self.scheduled_patrols = load_json(SCHEDULE_FILE, [])
        logger.info(f"Loaded {len(self.scheduled_patrols)} scheduled patrols")

    def _save_schedule(self):
        with self.schedule_lock:
            save_json(SCHEDULE_FILE, self.scheduled_patrols)

    def get_schedule(self):
        with self.schedule_lock:
            return list(self.scheduled_patrols)

    def add_schedule(self, time_str, days=None, enabled=True):
        """Add scheduled patrol. Days: 0=Monday to 6=Sunday."""
        item = {
            "id": str(uuid.uuid4())[:8],
            "time": time_str,
            "days": days or [0, 1, 2, 3, 4, 5, 6],
            "enabled": enabled
        }
        with self.schedule_lock:
            self.scheduled_patrols.append(item)
        self._save_schedule()
        logger.info(f"Added scheduled patrol at {time_str}")
        return item

    def update_schedule(self, schedule_id, time_str=None, days=None, enabled=None):
        with self.schedule_lock:
            for item in self.scheduled_patrols:
                if item.get("id") == schedule_id:
                    if time_str is not None:
                        item["time"] = time_str
                    if days is not None:
                        item["days"] = days
                    if enabled is not None:
                        item["enabled"] = enabled
                    break
        self._save_schedule()

    def delete_schedule(self, schedule_id):
        with self.schedule_lock:
            self.scheduled_patrols = [s for s in self.scheduled_patrols if s.get("id") != schedule_id]
        self._save_schedule()

    def _schedule_checker(self):
        """Background thread checking for scheduled patrols."""
        last_triggered = {}

        while True:
            try:
                settings = settings_service.get_all()
                tz_name = settings.get("timezone", "UTC")

                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo(tz_name))
                except Exception:
                    now = datetime.now()

                current_time_str = now.strftime("%H:%M")
                current_day = now.weekday()
                current_date = now.strftime("%Y-%m-%d")

                with self.schedule_lock:
                    schedules = list(self.scheduled_patrols)

                for schedule in schedules:
                    if not schedule.get("enabled", True):
                        continue

                    schedule_id = schedule.get("id", "")
                    schedule_time = schedule.get("time", "")
                    schedule_days = schedule.get("days", [0, 1, 2, 3, 4, 5, 6])

                    if current_day not in schedule_days:
                        continue

                    if schedule_time == current_time_str:
                        trigger_key = f"{schedule_id}_{current_date}"
                        if trigger_key in last_triggered:
                            continue

                        with self.patrol_lock:
                            if self.is_patrolling:
                                logger.info(f"Scheduled patrol {schedule_id} skipped - already patrolling")
                                continue

                        logger.info(f"Scheduled patrol triggered: {schedule_id} at {schedule_time}")
                        last_triggered[trigger_key] = True
                        self.start_patrol()

                # Cleanup old triggers
                last_triggered = {k: v for k, v in last_triggered.items() if k.endswith(current_date)}

            except Exception as e:
                logger.error(f"Schedule checker error: {e}")

            time.sleep(30)

    # === Status Management ===

    def get_status(self):
        with self.state_lock:
            return {
                "is_patrolling": self.is_patrolling,
                "status": self.patrol_status,
                "current_index": self.current_patrol_index
            }

    def _set_status(self, status):
        with self.state_lock:
            self.patrol_status = status

    def _set_patrol_index(self, index):
        with self.state_lock:
            self.current_patrol_index = index

    # === Patrol Control ===

    def start_patrol(self):
        with self.patrol_lock:
            if self.is_patrolling:
                return False, "Already patrolling"
            old_thread = self.patrol_thread

        # Join outside lock to avoid deadlock
        if old_thread and old_thread.is_alive():
            old_thread.join(timeout=5)

        with self.patrol_lock:
            if self.is_patrolling:  # Re-check after join
                return False, "Already patrolling"
            self.is_patrolling = True
            with self.state_lock:
                self.current_patrol_index = -1
                self.current_run_id = None

        logger.info("Starting patrol...")
        self.patrol_thread = threading.Thread(target=self._patrol_logic, daemon=True)
        self.patrol_thread.start()
        return True, "Started"

    def stop_patrol(self):
        with self.patrol_lock:
            was_patrolling = self.is_patrolling
            self.is_patrolling = False
            self._set_status("Stopping...")

        if was_patrolling:
            logger.info("Stop patrol requested.")
            try:
                robot_service.cancel_command()
            except Exception as e:
                logger.warning(f"cancel_command failed during stop: {e}")
        return True

    # === Inspection Worker ===

    def _inspection_worker(self):
        """Background worker processing inspection queue."""
        while True:
            task = self.inspection_queue.get()
            try:
                run_id, point, image_path, user_prompt, sys_prompt, results_list, img_uuid = task
                point_name = point.get('name', 'Unknown')
                logger.info(f"Worker: Processing {point_name}")

                try:
                    image = Image.open(image_path)
                except Exception as e:
                    logger.error(f"Worker Image Load Error for {point_name}: {e}")
                    continue

                # AI Analysis
                try:
                    response_obj = ai_service.generate_inspection(image, user_prompt, sys_prompt)
                    parsed = parse_ai_response(response_obj)
                except Exception as e:
                    logger.error(f"Worker AI Error for {point_name}: {e}")
                    parsed = parse_ai_response(None)
                    parsed['result_text'] = f"AI Error: {e}"
                    parsed['description'] = str(e)

                # Rename image
                new_path = self._rename_image(image_path, point_name, parsed['is_ng'], img_uuid)

                # Save to DB
                self._save_inspection(
                    run_id, point, point_name, user_prompt,
                    parsed, new_path, "Success"
                )

                results_list.append({"point": point_name, "result": parsed['result_text']})
                logger.info(f"Worker: Finished {point_name}")

            except Exception as e:
                logger.critical(f"Worker Fatal Error: {e}")
            finally:
                self.inspection_queue.task_done()

    def _rename_image(self, image_path, point_name, is_ng, img_uuid):
        """Rename image with point name and status."""
        try:
            safe_name = point_name.replace("/", "_").replace("\\", "_")
            # Transliterate non-ASCII chars to avoid HTTP encoding issues
            safe_name = safe_name.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            status_tag = "NG" if is_ng else "OK"
            new_filename = f"{safe_name}_{status_tag}_{img_uuid}.jpg"
            new_path = os.path.join(os.path.dirname(image_path), new_filename)
            os.rename(image_path, new_path)
            return new_path
        except OSError as e:
            logger.warning(f"Failed to rename image: {e}")
            return image_path

    def _save_inspection(self, run_id, point, point_name, prompt, parsed, image_path, move_status):
        """Save inspection result to database."""
        rel_path = image_path.replace(ROBOT_IMAGES_DIR + "/", "").lstrip('/') if image_path else ""

        try:
            with db_context() as (conn, cursor):
                cursor.execute('''
                    INSERT INTO inspection_results
                    (run_id, point_name, coordinate_x, coordinate_y, prompt, ai_response,
                     is_ng, ai_description, token_usage, input_tokens, output_tokens,
                     total_tokens, image_path, timestamp, robot_moving_status, robot_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    run_id, point_name, point.get('x'), point.get('y'), prompt,
                    parsed['result_text'], 1 if parsed['is_ng'] else 0, parsed['description'],
                    parsed['usage_json'], parsed['input_tokens'], parsed['output_tokens'],
                    parsed['total_tokens'], rel_path, get_current_time_str(), move_status,
                    ROBOT_ID
                ))
        except Exception as e:
            logger.error(f"DB Error saving inspection for {point_name}: {e}")

    # === Main Patrol Logic ===

    def _patrol_logic(self):
        self._set_status("Starting...")
        frame_hub.set_patrol_active(True)
        points = load_json(POINTS_FILE, [])
        settings = settings_service.get_all()

        # Validate AI config
        if not ai_service.is_configured():
            self._set_status("Error: AI Not Configured")
            logger.error("Patrol started but AI not configured.")
            with self.patrol_lock:
                self.is_patrolling = False
            return

        model_name = ai_service.get_model_name()
        to_patrol = [p for p in points if p.get('enabled', True)]

        if not to_patrol:
            self._set_status("No enabled points")
            with self.patrol_lock:
                self.is_patrolling = False
            return

        # Create patrol run record
        try:
            with db_context() as (conn, cursor):
                cursor.execute(
                    'INSERT INTO patrol_runs (start_time, status, robot_serial, model_id, robot_id) VALUES (?, ?, ?, ?, ?)',
                    (get_current_time_str(), "Running", robot_service.get_serial(), model_name, ROBOT_ID)
                )
                with self.state_lock:
                    self.current_run_id = cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to create patrol run: {e}")
            self._set_status("Error: Database Error")
            with self.patrol_lock:
                self.is_patrolling = False
            return

        logger.info(f"Patrol Run {self.current_run_id} started with {len(to_patrol)} points.")

        # Create run folder
        run_folder = f"{self.current_run_id}_{get_filename_timestamp()}"
        run_images_dir = os.path.join(ROBOT_IMAGES_DIR, run_folder)
        os.makedirs(run_images_dir, exist_ok=True)
        
        # Video recording setup
        recorder = None
        video_filename = None
        if settings.get("enable_video_recording", False):
            video_dir = os.path.join(ROBOT_DATA_DIR, "report", "video")
            os.makedirs(video_dir, exist_ok=True)
            video_filename = os.path.join(video_dir, f"{self.current_run_id}_{get_filename_timestamp()}.mp4") # Use mp4 as tested
            
            self._set_status("Starting Video Recording...")
            recorder = VideoRecorder(video_filename, frame_hub.get_latest_frame)
            recorder.start()

        # Edge AI setup (VILA JPS API + RTSP relay)
        from edge_ai_service import edge_ai_monitor
        from relay_manager import relay_service_client
        from config import JETSON_JPS_API_PORT, JETSON_MEDIAMTX_PORT
        edge_ai_active = False

        tg_config = None
        if settings.get("enable_telegram"):
            tg_token = settings.get("telegram_bot_token", "")
            tg_user = settings.get("telegram_user_id", "")
            if tg_token and tg_user:
                tg_config = {"bot_token": tg_token, "user_id": tg_user}

        jetson_host = settings.get("jetson_host", "")
        if settings.get("enable_edge_ai") and jetson_host:
            # Derive URLs from Jetson host (JPS + mediamtx are co-located on Jetson)
            vila_jps_url = f"http://{jetson_host}:{JETSON_JPS_API_PORT}"
            mediamtx_for_jps = f"localhost:{JETSON_MEDIAMTX_PORT}"  # JPS perspective (co-located)
            streams = []

            if settings.get("enable_robot_camera_relay"):
                try:
                    final_path = f"/{ROBOT_ID}/camera"
                    frame_hub.start_rtsp_push(
                        f"{jetson_host}:{JETSON_MEDIAMTX_PORT}", final_path)
                    streams.append({
                        "rtsp_url": f"rtsp://{mediamtx_for_jps}{final_path}",
                        "name": f"{ROBOT_NAME} Camera",
                        "type": "robot_camera",
                        "evidence_func": frame_hub.get_latest_frame,
                    })
                except Exception as e:
                    logger.error(f"Failed to start robot camera push: {e}")

            ext_url = settings.get("external_rtsp_url", "")
            if settings.get("enable_external_rtsp") and ext_url:
                if not relay_service_client:
                    logger.warning("Relay service not configured, skipping external RTSP relay")
                else:
                    try:
                        key = f"{ROBOT_ID}/external"
                        rtsp_path, err = relay_service_client.start_relay(key, ext_url)
                        if err:
                            raise RuntimeError(err)
                        streams.append({
                            "rtsp_url": f"rtsp://{mediamtx_for_jps}{rtsp_path}",
                            "name": "External Camera",
                            "type": "external_rtsp",
                        })
                    except Exception as e:
                        logger.error(f"Failed to start external RTSP relay: {e}")

            # Wait for streams to be live on mediamtx before registering with JPS
            if streams:
                verified = []
                for s in streams:
                    if s["type"] == "robot_camera":
                        ready = frame_hub.wait_for_push_ready(timeout=30)
                    else:
                        s_key = s["rtsp_url"].split(mediamtx_for_jps)[-1].lstrip("/")
                        ready = relay_service_client.wait_for_stream(s_key, timeout=60)
                    if ready:
                        verified.append(s)
                    else:
                        logger.error(f"Stream not available on mediamtx: {s['name']}")
                streams = verified

            rules = settings.get("edge_ai_rules", [])
            if streams and rules:
                edge_ai_monitor.start(self.current_run_id, {
                    "vila_jps_url": vila_jps_url,
                    "streams": streams,
                    "rules": rules,
                    "telegram_config": tg_config,
                    "mediamtx_external": f"{jetson_host}:{JETSON_MEDIAMTX_PORT}",
                })
                edge_ai_active = True

        inspections_data = []
        turbo_mode = settings.get('turbo_mode', False)
        was_patrolling = False

        try:
            # Main patrol loop
            for i, point in enumerate(to_patrol):
                with self.patrol_lock:
                    if not self.is_patrolling:
                        break

                self._set_patrol_index(i)
                point_name = point.get('name', 'Unknown')
                self._set_status(f"Moving to {point_name}...")
                logger.info(f"Moving to {i+1}/{len(to_patrol)}: {point_name}")

                # Move to point
                move_result = self._move_to_point(point)

                if not move_result["success"]:
                    move_status = f"Error: {move_result['error_code']} - {move_result['title']}"
                    self._save_inspection(
                        self.current_run_id, point, point_name, "",
                        {'result_text': "Move Failed", 'is_ng': True,
                         'description': f"{move_result['title']}: {move_result['description']}",
                         'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'usage_json': '{}'},
                        "", move_status
                    )
                    time.sleep(1)
                    continue

                with self.patrol_lock:
                    if not self.is_patrolling:
                        break

                # Wait for fresh camera frame (ensures post-arrival image)
                self._set_status(f"Inspecting {point_name}...")
                if not frame_hub.wait_for_fresh_frame(timeout=10):
                    logger.warning(f"No fresh frame for {point_name}, using cached")
                time.sleep(2)  # let robot physically settle

                self._inspect_point(
                    point, point_name, run_images_dir, settings,
                    turbo_mode, inspections_data
                )

            # Return home before cleanup (edge AI continues monitoring during return)
            with self.patrol_lock:
                was_patrolling = self.is_patrolling

            if was_patrolling:
                self._set_status("Returning Home...")
                try:
                    robot_service.return_home()
                except Exception as e:
                    logger.error(f"Return home failed: {e}")

                # Wait for async inspections to complete (robot is moving home in parallel)
                if turbo_mode:
                    self._set_status("Processing Images...")
                    self.inspection_queue.join()

                time.sleep(2)
        finally:
            # Ensure edge AI monitor is always stopped
            if edge_ai_active:
                try:
                    edge_ai_monitor.stop()
                except Exception as e:
                    logger.error(f"Error stopping edge AI monitor: {e}")
            # Stop ffmpeg push before stopping relays
            try:
                frame_hub.stop_rtsp_push()
            except Exception as e:
                logger.error(f"Error stopping RTSP push: {e}")
            # Ensure RTSP relays are always stopped
            try:
                if relay_service_client:
                    relay_service_client.stop_all()
            except Exception as e:
                logger.error(f"Error stopping relays: {e}")
            # Ensure video recorder is always stopped
            if recorder:
                try:
                    recorder.stop()
                except Exception as e:
                    logger.error(f"Error stopping video recorder: {e}")
            # Re-evaluate polling (may stop if idle + enable_idle_stream=false)
            frame_hub.set_patrol_active(False)

        # Finalize patrol
        final_status = "Completed" if was_patrolling else "Patrol Stopped"

        video_path = None
        video_analysis_text = None

        if recorder and was_patrolling:
            video_path = video_filename

        if was_patrolling:
            if recorder and video_path:
                self._set_status("Analyzing Video...")
                try:
                    vid_prompt = settings.get("video_prompt", "Analyze this patrol video.")
                    analysis_result = ai_service.analyze_video(video_filename, vid_prompt)
                    video_analysis_text = analysis_result['result']
                    # Save video token usage
                    vid_usage = analysis_result.get('usage', {})
                    with db_context() as (conn, cursor):
                        cursor.execute('''
                            UPDATE patrol_runs
                            SET video_input_tokens = ?, video_output_tokens = ?, video_total_tokens = ?
                            WHERE id = ?
                        ''', (vid_usage.get('prompt_token_count', 0),
                              vid_usage.get('candidates_token_count', 0),
                              vid_usage.get('total_token_count', 0),
                              self.current_run_id))
                except Exception as e:
                    logger.error(f"Video analysis failed: {e}")
                    video_analysis_text = f"Analysis Failed: {e}"

            # Write end_time and status before report generation so PDF has complete info
            try:
                with db_context() as (conn, cursor):
                    cursor.execute(
                        'UPDATE patrol_runs SET end_time = ?, status = ?, video_path = ?, video_analysis = ? WHERE id = ?',
                        (get_current_time_str(), final_status, video_path, video_analysis_text, self.current_run_id)
                    )
            except Exception as e:
                logger.error(f"DB Error updating patrol status: {e}")

            self._set_status("Generating Report...")
            live_alert_data = edge_ai_monitor.get_alerts() if edge_ai_active else []
            self._generate_report(inspections_data, settings, video_analysis_text, live_alert_data)

            self._set_status("Finished")

        else:
            # Patrol was stopped — still write end_time and status
            try:
                with db_context() as (conn, cursor):
                    cursor.execute(
                        'UPDATE patrol_runs SET end_time = ?, status = ? WHERE id = ?',
                        (get_current_time_str(), final_status, self.current_run_id)
                    )
            except Exception as e:
                logger.error(f"DB Error updating stopped patrol status: {e}")

        # Update aggregated token totals (after report/telegram tokens are saved)
        try:
            update_run_tokens(self.current_run_id)
        except Exception as e:
            logger.error(f"DB Error updating token totals: {e}")

        logger.info(f"Patrol Run {self.current_run_id} finished: {final_status}")
        with self.patrol_lock:
            self.is_patrolling = False

    def _move_to_point(self, point):
        """Move robot to patrol point. Returns dict with success, error_code, title, description.

        No pre-check for client connectivity — _grpc_retry in move_to() handles
        reconnection automatically, which is essential for mesh network roaming
        where brief disconnects are expected.
        """
        try:
            result = robot_service.move_to(
                float(point['x']), float(point['y']),
                float(point.get('theta', 0.0)), wait=True
            )
            if result and result.success:
                return {"success": True}

            error_code = result.error_code if result else -1
            title, description = "", ""
            try:
                errors = robot_service.get_error_codes()
                if error_code in errors:
                    title = errors[error_code].title_en
                    description = errors[error_code].description_en
            except Exception:
                pass

            logger.warning(f"Move failed: error_code={error_code}, title={title}, description={description}")
            return {"success": False, "error_code": error_code, "title": title, "description": description}
        except Exception as e:
            logger.error(f"Move exception: {e}")
            return {"success": False, "error_code": -1, "title": "Exception", "description": str(e)}

    def _inspect_point(self, point, point_name, run_images_dir, settings, turbo_mode, inspections_data):
        """Capture image and run AI inspection."""
        try:
            img_response = frame_hub.get_latest_frame()
            if not img_response:
                return

            image = Image.open(io.BytesIO(img_response.data))
            img_uuid = str(uuid.uuid4())
            safe_name = point_name.replace("/", "_").replace("\\", "_")
            img_path = os.path.join(run_images_dir, f"{safe_name}_processing_{img_uuid}.jpg")
            image.save(img_path)

            user_prompt = point.get('prompt', 'Is everything normal?')
            sys_prompt = settings.get('system_prompt', '')

            if turbo_mode:
                logger.info(f"Queuing inspection for {point_name}")
                self.inspection_queue.put((
                    self.current_run_id, point, img_path,
                    user_prompt, sys_prompt, inspections_data, img_uuid
                ))
            else:
                logger.info(f"Analyzing {point_name}")
                response_obj = ai_service.generate_inspection(image, user_prompt, sys_prompt)
                parsed = parse_ai_response(response_obj)

                new_path = self._rename_image(img_path, point_name, parsed['is_ng'], img_uuid)
                self._save_inspection(
                    self.current_run_id, point, point_name, user_prompt,
                    parsed, new_path, "Success"
                )
                inspections_data.append({"point": point_name, "result": parsed['result_text']})

        except Exception as e:
            logger.error(f"Inspection Error at {point_name}: {e}")
            self._set_status(f"Error at {point_name}")
            time.sleep(2)

    def _generate_report(self, inspections_data, settings, video_analysis_text=None, live_alert_data=None):
        """Generate AI summary report."""
        if not inspections_data:
            return

        try:
            custom_prompt = settings.get('report_prompt', '').strip()
            if custom_prompt:
                report_prompt = f"{custom_prompt}\n\n"
            else:
                report_prompt = "Generate a summary report for this patrol:\n\n"

            for item in inspections_data:
                report_prompt += f"- Point: {item['point']}\n  Result: {item['result']}\n\n"

            if video_analysis_text:
                report_prompt += f"\n\nVideo Analysis Summary:\n{video_analysis_text}\n\n"

            if live_alert_data:
                report_prompt += f"\n\nEdge AI Alerts ({len(live_alert_data)} triggered):\n"
                for alert in live_alert_data:
                    report_prompt += f"- [{alert['timestamp']}] Rule: {alert['rule']} -> {alert['response']}\n"
                report_prompt += "\n"

            if not custom_prompt:
                report_prompt += "Provide a concise overview of status and anomalies."

            response_obj = ai_service.generate_report(report_prompt)
            parsed = parse_ai_response(response_obj)

            with db_context() as (conn, cursor):
                cursor.execute('''
                    UPDATE patrol_runs
                    SET report_content = ?, token_usage = ?,
                        report_input_tokens = ?, report_output_tokens = ?, report_total_tokens = ?
                    WHERE id = ?
                ''', (parsed['result_text'], parsed['usage_json'],
                      parsed['input_tokens'], parsed['output_tokens'], parsed['total_tokens'],
                      self.current_run_id))

            logger.info("Report generated and saved.")

            # --- Telegram Notification ---
            if settings.get('enable_telegram', False):
                telegram_message, tg_parsed = self._generate_telegram_message(
                    inspections_data, settings, video_analysis_text
                )
                # Save telegram token usage
                with db_context() as (conn, cursor):
                    cursor.execute('''
                        UPDATE patrol_runs
                        SET telegram_input_tokens = ?, telegram_output_tokens = ?, telegram_total_tokens = ?
                        WHERE id = ?
                    ''', (tg_parsed['input_tokens'], tg_parsed['output_tokens'],
                          tg_parsed['total_tokens'], self.current_run_id))
                self._send_telegram_notification(settings, telegram_message)

        except Exception as e:
            logger.error(f"Report Generation Error: {e}")

    def _generate_telegram_message(self, inspections_data, settings, video_analysis_text=None):
        """Generate a concise Telegram message using AI.

        Returns:
            tuple: (message_text, parsed_response_dict)
        """
        empty_parsed = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
        try:
            custom_prompt = settings.get('telegram_message_prompt', '').strip()
            if not custom_prompt:
                custom_prompt = "Generate a concise Telegram notification summarizing this patrol."

            prompt = f"{custom_prompt}\n\n"
            for item in inspections_data:
                prompt += f"- Point: {item['point']}\n  Result: {item['result']}\n\n"

            if video_analysis_text:
                prompt += f"\nVideo Analysis Summary:\n{video_analysis_text}\n\n"

            response_obj = ai_service.generate_report(prompt)
            parsed = parse_ai_response(response_obj)
            return parsed['result_text'], parsed
        except Exception as e:
            logger.error(f"Telegram message generation failed: {e}")
            return "Patrol completed. Failed to generate summary.", empty_parsed

    def _send_telegram_notification(self, settings, message):
        """Send patrol report and PDF to Telegram."""
        bot_token = settings.get('telegram_bot_token')
        user_id = settings.get('telegram_user_id')

        if not bot_token or not user_id:
            logger.warning("Telegram enabled but token or user_id missing.")
            return

        try:
            logger.info("Sending Telegram notification...")

            # 1. Send Text Message
            text_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            text_payload = {
                "chat_id": user_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            resp = requests.post(text_url, json=text_payload, timeout=10)
            if not resp.ok:
                logger.error(f"Telegram Text Error: {resp.text}")

            # 2. Send PDF Document with start_time in filename
            try:
                pdf_bytes = generate_patrol_report(self.current_run_id)

                # Get start_time from database for filename
                pdf_filename = f'Patrol_Report_{self.current_run_id}.pdf'  # Default fallback
                try:
                    with db_context() as (conn, cursor):
                        cursor.execute('SELECT start_time FROM patrol_runs WHERE id = ?', (self.current_run_id,))
                        row = cursor.fetchone()
                        if row and row['start_time']:
                            # Convert "YYYY-MM-DD HH:MM:SS" to "YYYY-MM-DD_HHMMSS"
                            start_time_str = row['start_time']
                            filename_ts = start_time_str.replace(" ", "_").replace(":", "")
                            pdf_filename = f'Patrol_Report_{filename_ts}.pdf'
                except Exception as e_db:
                    logger.warning(f"Could not get start_time for PDF filename: {e_db}")

                doc_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                files = {
                    'document': (
                        pdf_filename,
                        pdf_bytes,
                        'application/pdf'
                    )
                }
                data = {'chat_id': user_id}

                resp_doc = requests.post(doc_url, data=data, files=files, timeout=30)
                if resp_doc.ok:
                    logger.info("Telegram notification sent successfully.")
                else:
                    logger.error(f"Telegram PDF Error: {resp_doc.text}")

            except Exception as e_pdf:
                logger.error(f"Failed to generate/send PDF to Telegram: {e_pdf}")

        except Exception as e:
            logger.error(f"Telegram Notification Failed: {e}")


patrol_service = PatrolService()
