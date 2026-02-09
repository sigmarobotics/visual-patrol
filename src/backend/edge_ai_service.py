"""
Edge AI Service - Background camera monitoring via VILA JPS Alert API during patrol.

Uses VILA JPS Stream API + Alert API + WebSocket for efficient continuous monitoring.
TestLiveMonitor uses the same JPS flow for settings page quick test (no DB writes).
"""

import base64
import json
import os
import threading
import time
from urllib.parse import urlparse

import cv2
import requests
import websocket

from config import ROBOT_ID, ROBOT_DATA_DIR
from database import db_context
from logger import get_logger
from utils import get_current_time_str

logger = get_logger("edge_ai_service", "edge_ai_service.log")

MAX_RULES = 10
WS_PORT = 5016
WS_RECONNECT_DELAY = 5
WS_MAX_RECONNECTS = 10


class LiveMonitor:
    """Background monitor using VILA JPS API: stream registration, alert rules, WebSocket events."""

    def __init__(self):
        self.is_monitoring = False
        self.current_run_id = None
        self.alerts = []
        self.alert_cooldowns = {}  # {rule_string: last_trigger_timestamp}
        self.cooldown_seconds = 60
        self._lock = threading.Lock()

        # JPS state
        self._stream_ids = []  # list of (stream_id, stream_config)
        self._ws_thread = None
        self._ws = None
        self._ws_stop = threading.Event()
        self._config = None

    def start(self, run_id, config):
        """Start edge AI monitoring with VILA JPS API.

        Args:
            run_id: Current patrol run ID.
            config: dict with keys:
                vila_jps_url: str - VILA JPS base URL (e.g. "http://localhost:5010")
                streams: list of dicts, each with:
                    rtsp_url: str - full RTSP URL for VILA to pull
                    name: str - human-readable name
                    type: str - "robot_camera" or "external_rtsp"
                    evidence_func: callable (optional) - returns gRPC image for evidence capture
                rules: list of str - alert rule strings
                telegram_config: dict or None - {bot_token, user_id}
                mediamtx_external: str - mediamtx host:port for evidence capture
        """
        if self.is_monitoring:
            return

        self.current_run_id = run_id
        self._config = config
        self.alerts = []
        self.alert_cooldowns = {}
        self._stream_ids = []
        self._ws_stop.clear()

        vila_jps_url = config["vila_jps_url"].rstrip("/")
        streams = config.get("streams", [])
        rules = config.get("rules", [])

        if not streams or not rules:
            logger.warning("LiveMonitor: no streams or no rules, skipping")
            return

        # Truncate rules to max
        if len(rules) > MAX_RULES:
            logger.warning(f"Truncating rules from {len(rules)} to {MAX_RULES}")
            rules = rules[:MAX_RULES]

        # 0. Clean up any stale streams from previous runs
        self._cleanup_stale_streams(vila_jps_url)

        # 1. Register each stream (with retry — gstDecoder may need time)
        for stream in streams:
            stream_id = None
            for attempt in range(1, 4):
                try:
                    stream_id = self._register_stream(vila_jps_url, stream["rtsp_url"], stream["name"])
                    if stream_id:
                        self._stream_ids.append((stream_id, stream))
                        logger.info(f"Registered stream (attempt {attempt}): {stream['name']} -> stream_id={stream_id}")
                        break
                    logger.warning(f"JPS registration returned no id for {stream['name']} (attempt {attempt}/3)")
                except Exception as e:
                    logger.warning(f"JPS registration failed for {stream['name']} (attempt {attempt}/3): {e}")
                if attempt < 3:
                    time.sleep(5)
            if not stream_id:
                logger.error(f"Failed to register stream after 3 attempts: {stream['name']}")

        if not self._stream_ids:
            logger.error("No streams registered, aborting LiveMonitor start")
            return

        # 2. Set alert rules per stream
        for stream_id, stream in self._stream_ids:
            try:
                self._set_alert_rules(vila_jps_url, stream_id, rules)
                logger.info(f"Set {len(rules)} alert rules for stream {stream_id}")
            except Exception as e:
                logger.error(f"Error setting alert rules for stream {stream_id}: {e}")

        # 3. Start WebSocket listener
        self.is_monitoring = True
        self._ws_thread = threading.Thread(target=self._ws_listener, daemon=True)
        self._ws_thread.start()

        logger.info(f"LiveMonitor started for run {run_id}: {len(self._stream_ids)} streams, {len(rules)} rules")

    def stop(self):
        """Stop monitoring: close WebSocket, deregister streams."""
        if not self.is_monitoring:
            return

        self.is_monitoring = False
        self._ws_stop.set()

        # Close WebSocket
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._ws_thread:
            self._ws_thread.join(timeout=10)
            self._ws_thread = None

        # Deregister streams
        if self._config:
            vila_jps_url = self._config["vila_jps_url"].rstrip("/")
            for stream_id, _ in self._stream_ids:
                try:
                    self._deregister_stream(vila_jps_url, stream_id)
                    logger.info(f"Deregistered stream: {stream_id}")
                except Exception as e:
                    logger.warning(f"Error deregistering stream {stream_id}: {e}")

        self._stream_ids = []
        logger.info(f"LiveMonitor stopped. Total alerts: {len(self.alerts)}")

    def get_alerts(self):
        """Return list of alerts collected during this run."""
        with self._lock:
            return list(self.alerts)

    # --- VILA JPS API ---

    def _cleanup_stale_streams(self, vila_jps_url):
        """Remove any existing streams from VILA JPS to avoid 'Stream Maximum reached' errors."""
        try:
            url = f"{vila_jps_url}/api/v1/live-stream"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            streams = resp.json()
            for s in streams:
                sid = s.get("id")
                if sid:
                    try:
                        self._deregister_stream(vila_jps_url, sid)
                        logger.info(f"Cleaned up stale stream: {sid}")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to cleanup stale streams: {e}")

    def _register_stream(self, vila_jps_url, rtsp_url, name):
        """POST /api/v1/live-stream to register a stream. Returns stream_id or None."""
        url = f"{vila_jps_url}/api/v1/live-stream"
        body = {"liveStreamUrl": rtsp_url, "name": name}
        resp = requests.post(url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("id") or data.get("stream_id")

    def _set_alert_rules(self, vila_jps_url, stream_id, rules):
        """POST /api/v1/alerts to set alert rules for a stream."""
        url = f"{vila_jps_url}/api/v1/alerts"
        body = {"alerts": rules, "id": stream_id}
        resp = requests.post(url, json=body, timeout=15)
        resp.raise_for_status()

    def _deregister_stream(self, vila_jps_url, stream_id):
        """DELETE /api/v1/live-stream/{stream_id}."""
        url = f"{vila_jps_url}/api/v1/live-stream/{stream_id}"
        resp = requests.delete(url, timeout=10)
        resp.raise_for_status()

    # --- WebSocket Listener ---

    def _ws_listener(self):
        """Connect to VILA JPS WebSocket and listen for alert events."""
        vila_jps_url = self._config["vila_jps_url"].rstrip("/")
        parsed = urlparse(vila_jps_url)
        ws_host = parsed.hostname
        ws_url = f"ws://{ws_host}:{WS_PORT}/api/v1/alerts/ws"

        evidence_dir = os.path.join(ROBOT_DATA_DIR, "report", "edge_ai_alerts")
        os.makedirs(evidence_dir, exist_ok=True)

        reconnect_count = 0

        while not self._ws_stop.is_set() and reconnect_count < WS_MAX_RECONNECTS:
            try:
                logger.info(f"Connecting to VILA WS: {ws_url}")
                self._ws = websocket.WebSocket()
                self._ws.settimeout(5)
                self._ws.connect(ws_url)
                logger.info("VILA WebSocket connected")
                reconnect_count = 0  # Reset on successful connect

                while not self._ws_stop.is_set():
                    try:
                        raw = self._ws.recv()
                        if not raw:
                            continue
                        self._handle_ws_event(raw, evidence_dir)
                    except websocket.WebSocketTimeoutException:
                        continue
                    except websocket.WebSocketConnectionClosedException:
                        logger.warning("VILA WebSocket closed by server")
                        break

            except Exception as e:
                if self._ws_stop.is_set():
                    break
                reconnect_count += 1
                logger.warning(f"VILA WS error (attempt {reconnect_count}/{WS_MAX_RECONNECTS}): {e}")
                self._ws_stop.wait(WS_RECONNECT_DELAY)

        if reconnect_count >= WS_MAX_RECONNECTS:
            logger.error("VILA WebSocket max reconnects exceeded")

    def _handle_ws_event(self, raw, evidence_dir):
        """Process a single WebSocket alert event."""
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug(f"Non-JSON WS message: {raw[:200]}")
            return

        rule_string = event.get("rule_string") or event.get("alert") or event.get("rule", "")
        stream_id = event.get("stream_id") or event.get("id", "")
        alert_id = event.get("alert_id", "")

        if not rule_string:
            return

        # Find the stream config for this stream_id
        stream_config = None
        for sid, sc in self._stream_ids:
            if sid == stream_id:
                stream_config = sc
                break

        stream_type = stream_config.get("type", "unknown") if stream_config else "unknown"
        stream_name = stream_config.get("name", "Unknown") if stream_config else "Unknown"

        # Cooldown check (defense-in-depth; VILA also has 60s cooldown)
        now = time.time()
        cooldown_key = f"{stream_id}:{rule_string}"
        last_trigger = self.alert_cooldowns.get(cooldown_key, 0)
        if now - last_trigger < self.cooldown_seconds:
            return
        self.alert_cooldowns[cooldown_key] = now

        timestamp = get_current_time_str()
        logger.warning(f"ALERT: stream={stream_name} rule='{rule_string}' alert_id={alert_id}")

        # Capture evidence frame
        jpeg_bytes = self._capture_evidence(stream_config)

        # Save evidence image
        img_path = ""
        rel_img_path = ""
        if jpeg_bytes:
            safe_rule = rule_string[:40].replace("/", "_").replace("\\", "_").replace(" ", "_")
            img_filename = f"{self.current_run_id}_{int(now)}_{safe_rule}.jpg"
            img_path = os.path.join(evidence_dir, img_filename)
            try:
                with open(img_path, "wb") as f:
                    f.write(jpeg_bytes)
                rel_img_path = os.path.relpath(img_path, ROBOT_DATA_DIR)
            except Exception as e:
                logger.error(f"Failed to save evidence image: {e}")
                img_path = ""

        # Save to DB
        try:
            with db_context() as (conn, cursor):
                cursor.execute('''
                    INSERT INTO edge_ai_alerts (run_id, rule, response, image_path, timestamp, robot_id, stream_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (self.current_run_id, rule_string, "triggered", rel_img_path,
                      timestamp, ROBOT_ID, stream_type))
        except Exception as e:
            logger.error(f"Failed to save live alert to DB: {e}")

        alert_entry = {
            "rule": rule_string,
            "response": "triggered",
            "image_path": rel_img_path,
            "timestamp": timestamp,
            "stream_source": stream_type,
            "stream_name": stream_name,
        }

        with self._lock:
            self.alerts.append(alert_entry)

        # Send to Telegram
        tg_config = self._config.get("telegram_config") if self._config else None
        if tg_config and jpeg_bytes:
            self._send_telegram_alert(rule_string, stream_name, timestamp, jpeg_bytes, tg_config)

    def _capture_evidence(self, stream_config):
        """Capture a JPEG evidence frame from the appropriate source."""
        if not stream_config:
            return None

        stream_type = stream_config.get("type", "")

        # Robot camera: use gRPC frame_func for best quality
        if stream_type == "robot_camera":
            evidence_func = stream_config.get("evidence_func")
            if evidence_func:
                try:
                    img = evidence_func()
                    if img and img.data:
                        return img.data
                except Exception as e:
                    logger.error(f"Evidence capture via gRPC failed: {e}")

        # External RTSP: capture from the relay RTSP URL
        if stream_type == "external_rtsp":
            mediamtx_ext = self._config.get("mediamtx_external", "localhost:8554") if self._config else None
            rtsp_url = stream_config.get("rtsp_url", "")
            if rtsp_url and mediamtx_ext:
                try:
                    cap = cv2.VideoCapture(rtsp_url)
                    ret, frame = cap.read()
                    cap.release()
                    if ret and frame is not None:
                        _, buf = cv2.imencode('.jpg', frame)
                        return buf.tobytes()
                except Exception as e:
                    logger.error(f"Evidence capture via RTSP failed: {e}")

        return None

    def _send_telegram_alert(self, rule, stream_name, timestamp, jpeg_bytes, tg_config):
        """Send alert photo + caption to Telegram."""
        bot_token = tg_config.get("bot_token")
        user_id = tg_config.get("user_id")
        if not bot_token or not user_id:
            return

        try:
            caption = (
                f"⚠️ Edge AI Alert\n\n"
                f"Rule: {rule}\n"
                f"Source: {stream_name}\n"
                f"Robot: {ROBOT_ID}\n"
                f"Time: {timestamp}"
            )
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            files = {"photo": (f"alert_{int(time.time())}.jpg", jpeg_bytes, "image/jpeg")}
            data = {"chat_id": user_id, "caption": caption}
            resp = requests.post(url, data=data, files=files, timeout=10)
            if resp.ok:
                logger.info(f"Telegram alert sent for rule: {rule}")
            else:
                logger.error(f"Telegram alert error: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")


edge_ai_monitor = LiveMonitor()


# === Test Monitor (settings page: relay → VILA JPS → WebSocket alerts) ===

class TestLiveMonitor:
    """Test monitor for settings page using VILA JPS flow. No DB writes, keeps alerts in memory.

    Flow: start relay → register stream with JPS → set alert rules → WebSocket listener.
    Also pulls snapshot frames from mediamtx RTSP to verify relay pipeline.
    """

    MAX_ALERTS = 100
    SNAPSHOT_INTERVAL = 0.5  # seconds between RTSP frame grabs

    def __init__(self):
        self.is_running = False
        self.alerts = []
        self.ws_messages = []  # all raw WS messages for debugging
        self.error = None
        self.ws_connected = False
        self._lock = threading.Lock()
        self._latest_frame = None  # JPEG bytes from mediamtx
        self._ws_thread = None
        self._ws = None
        self._ws_stop = threading.Event()
        self._snapshot_thread = None
        self._snapshot_stop = threading.Event()
        self._jps_thread = None
        self._stream_id = None
        self._relay_key = None
        self._config = None
        self._use_relay_service = False

    def start(self, config):
        """Start test monitor: relay → JPS → WebSocket.

        Args:
            config: dict with keys:
                vila_jps_url: str - VILA JPS base URL
                rules: list[str] - alert rule strings
                stream_source: str - "robot_camera" or "external_rtsp"
                external_rtsp_url: str - external RTSP URL (if stream_source == "external_rtsp")
                robot_id: str - robot identifier
                frame_func: callable - returns gRPC image (for robot_camera relay)
                mediamtx_internal: str - mediamtx host:port (ffmpeg push target)
                mediamtx_external: str - mediamtx host:port (VILA pull source)
        """
        if self.is_running:
            return

        self._config = config
        self.alerts = []
        self.ws_messages = []
        self.error = None
        self.ws_connected = False
        self._latest_frame = None
        self._stream_id = None
        self._relay_key = None
        self._ws_stop.clear()
        self._snapshot_stop.clear()

        stream_source = config.get("stream_source", "robot_camera")

        # Validate source availability early
        if stream_source == "external_rtsp":
            if not config.get("external_rtsp_url"):
                self.error = "External RTSP URL not configured"
                return
        else:
            if not config.get("frame_func"):
                self.error = "Robot camera not available"
                return

        # Relay → snapshot (from mediamtx) → JPS registration → WebSocket
        # All in background so start() returns immediately
        self.is_running = True
        self._jps_thread = threading.Thread(
            target=self._jps_setup,
            args=(config,),
            daemon=True,
        )
        self._jps_thread.start()

    def _jps_setup(self, config):
        """Background: start relay → wait for mediamtx → register with JPS → rules → WebSocket."""
        vila_jps_url = config["vila_jps_url"].rstrip("/")
        rules = config["rules"]
        stream_source = config.get("stream_source", "robot_camera")
        robot_id = config["robot_id"]
        mediamtx_internal = config["mediamtx_internal"]
        mediamtx_external = config["mediamtx_external"]

        # 1. Start relay (ffmpeg → mediamtx)
        from relay_manager import relay_manager, relay_service_client, wait_for_stream

        use_relay_service = relay_service_client and relay_service_client.is_available()
        self._use_relay_service = use_relay_service

        if relay_service_client and not use_relay_service:
            logger.warning("Relay service configured but not reachable, falling back to local RelayManager")

        try:
            if stream_source == "external_rtsp":
                ext_url = config.get("external_rtsp_url", "")
                self._relay_key = f"{robot_id}/external"
                if use_relay_service:
                    rtsp_path, err = relay_service_client.start_relay(self._relay_key, "external_rtsp", source_url=ext_url)
                    if err:
                        raise RuntimeError(err)
                else:
                    rtsp_path = relay_manager.start_external_rtsp_relay(
                        robot_id, ext_url, mediamtx_internal)
            else:
                frame_func = config.get("frame_func")
                self._relay_key = f"{robot_id}/camera"
                if use_relay_service:
                    rtsp_path, err = relay_service_client.start_relay(self._relay_key, "robot_camera")
                    if err:
                        raise RuntimeError(err)
                    relay_service_client.start_frame_feeder(self._relay_key, frame_func)
                else:
                    rtsp_path = relay_manager.start_robot_camera_relay(
                        robot_id, frame_func, mediamtx_internal)
        except Exception as e:
            with self._lock:
                self.error = f"Relay start failed: {e}"
            logger.error(self.error)
            self.is_running = False
            return

        rtsp_url_for_jps = f"rtsp://{mediamtx_external}{rtsp_path}"
        rtsp_check_url = f"rtsp://{mediamtx_internal}{rtsp_path}"
        logger.info(f"Relay started: JPS will pull from {rtsp_url_for_jps}")

        # 2. Wait for stream on mediamtx
        if not self.is_running:
            return
        if use_relay_service:
            stream_ready = relay_service_client.wait_for_stream(self._relay_key, timeout=20)
        else:
            stream_ready = wait_for_stream(rtsp_check_url, max_wait=20)
        if not stream_ready:
            with self._lock:
                self.error = "Relay stream not available on mediamtx (timeout 20s)"
            logger.error(self.error)
            self._stop_relay()
            self.is_running = False
            return

        # Start snapshot thread now that stream is available on mediamtx
        self._snapshot_thread = threading.Thread(
            target=self._snapshot_loop,
            daemon=True,
        )
        self._snapshot_thread.start()

        # 3. Cleanup stale JPS streams
        try:
            self._cleanup_stale_streams(vila_jps_url)
        except Exception as e:
            logger.warning(f"Stale stream cleanup failed: {e}")

        # 4. Register stream with JPS (retry — gstDecoder may need time)
        stream_name = f"Test-{robot_id}"
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            if not self.is_running:
                return
            try:
                self._stream_id = self._register_stream(vila_jps_url, rtsp_url_for_jps, stream_name)
                if self._stream_id:
                    logger.info(f"Registered test stream (attempt {attempt}): {stream_name} -> stream_id={self._stream_id}")
                    break
                logger.warning(f"JPS registration returned no id (attempt {attempt}/{max_attempts})")
            except Exception as e:
                logger.warning(f"JPS registration failed (attempt {attempt}/{max_attempts}): {e}")
                self._stream_id = None

            if attempt < max_attempts:
                for _ in range(10):
                    if not self.is_running:
                        return
                    time.sleep(1)

        if not self._stream_id:
            with self._lock:
                self.error = "Failed to register stream with VILA JPS after retries"
            logger.error(self.error)
            self._stop_relay()
            self.is_running = False
            return

        # 5. Set alert rules
        try:
            self._set_alert_rules(vila_jps_url, self._stream_id, rules)
            logger.info(f"Set {len(rules)} alert rules for test stream")
        except Exception as e:
            with self._lock:
                self.error = f"Failed to set alert rules: {e}"
            logger.error(self.error)
            self._stop_relay()
            self.is_running = False
            return

        # 6. Start WebSocket listener
        self._ws_thread = threading.Thread(target=self._ws_listener, daemon=True)
        self._ws_thread.start()
        logger.info(f"JPS setup complete: stream_id={self._stream_id}")

    def stop(self):
        if not self.is_running:
            return

        self.is_running = False
        self._ws_stop.set()
        self._snapshot_stop.set()

        # Close WebSocket
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._ws_thread:
            self._ws_thread.join(timeout=10)
            self._ws_thread = None

        if self._jps_thread:
            self._jps_thread.join(timeout=15)
            self._jps_thread = None

        if self._snapshot_thread:
            self._snapshot_thread.join(timeout=5)
            self._snapshot_thread = None

        # Deregister stream from JPS
        if self._stream_id and self._config:
            vila_jps_url = self._config["vila_jps_url"].rstrip("/")
            try:
                self._deregister_stream(vila_jps_url, self._stream_id)
                logger.info(f"Deregistered test stream: {self._stream_id}")
            except Exception as e:
                logger.warning(f"Test stream deregister failed: {e}")

        # Stop relay
        self._stop_relay()

        self._latest_frame = None
        self._stream_id = None
        logger.info(f"TestLiveMonitor stopped. Alerts: {len(self.alerts)}")

    def get_status(self):
        with self._lock:
            return {
                "active": self.is_running,
                "ws_connected": self.ws_connected,
                "alert_count": len(self.alerts),
                "alerts": list(self.alerts),
                "ws_messages": list(self.ws_messages),
                "error": self.error,
            }

    def get_snapshot(self):
        """Return latest JPEG bytes captured from mediamtx, or None."""
        with self._lock:
            return self._latest_frame

    # --- Internal helpers ---

    def _stop_relay(self):
        """Stop only the relay started by this test."""
        if self._relay_key:
            try:
                if getattr(self, '_use_relay_service', False):
                    from relay_manager import relay_service_client
                    if relay_service_client:
                        relay_service_client.stop_frame_feeder(self._relay_key)
                        relay_service_client.stop_relay(self._relay_key)
                else:
                    from relay_manager import relay_manager
                    relay_manager.stop_relay(self._relay_key)
            except Exception as e:
                logger.warning(f"Relay stop failed: {e}")
            self._relay_key = None

    def _snapshot_loop(self):
        """Background thread: capture frames from mediamtx RTSP for live preview.

        Pulls from mediamtx using the relay key, validating the full pipeline:
        source → relay service → ffmpeg → mediamtx → this snapshot.
        Works for both robot_camera and external_rtsp relay types.
        """
        mediamtx_internal = self._config.get("mediamtx_internal", "")
        if not mediamtx_internal or not self._relay_key:
            return
        rtsp_url = f"rtsp://{mediamtx_internal}/{self._relay_key}"
        logger.info(f"Snapshot pulling from mediamtx: {rtsp_url}")

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = None
        while not self._snapshot_stop.is_set():
            try:
                if cap is None or not cap.isOpened():
                    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if not cap.isOpened():
                        logger.warning(f"Failed to open RTSP for snapshot: {rtsp_url}")
                        self._snapshot_stop.wait(3)
                        continue

                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    with self._lock:
                        self._latest_frame = buf.tobytes()
                else:
                    cap.release()
                    cap = None

            except Exception as e:
                logger.debug(f"Snapshot capture error (RTSP): {e}")
                if cap:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None

            self._snapshot_stop.wait(self.SNAPSHOT_INTERVAL)

        if cap:
            try:
                cap.release()
            except Exception:
                pass

    # --- VILA JPS API (same as LiveMonitor) ---

    def _cleanup_stale_streams(self, vila_jps_url):
        url = f"{vila_jps_url}/api/v1/live-stream"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        for s in resp.json():
            sid = s.get("id")
            if sid:
                try:
                    self._deregister_stream(vila_jps_url, sid)
                    logger.info(f"Cleaned up stale stream: {sid}")
                except Exception:
                    pass

    def _register_stream(self, vila_jps_url, rtsp_url, name):
        url = f"{vila_jps_url}/api/v1/live-stream"
        body = {"liveStreamUrl": rtsp_url, "name": name}
        resp = requests.post(url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("id") or data.get("stream_id")

    def _set_alert_rules(self, vila_jps_url, stream_id, rules):
        url = f"{vila_jps_url}/api/v1/alerts"
        body = {"alerts": rules, "id": stream_id}
        resp = requests.post(url, json=body, timeout=15)
        resp.raise_for_status()

    def _deregister_stream(self, vila_jps_url, stream_id):
        url = f"{vila_jps_url}/api/v1/live-stream/{stream_id}"
        resp = requests.delete(url, timeout=10)
        resp.raise_for_status()

    # --- WebSocket Listener ---

    def _ws_listener(self):
        vila_jps_url = self._config["vila_jps_url"].rstrip("/")
        parsed = urlparse(vila_jps_url)
        ws_host = parsed.hostname
        ws_url = f"ws://{ws_host}:{WS_PORT}/api/v1/alerts/ws"

        reconnect_count = 0

        while not self._ws_stop.is_set() and reconnect_count < WS_MAX_RECONNECTS:
            try:
                logger.info(f"Test WS connecting: {ws_url}")
                self._ws = websocket.WebSocket()
                self._ws.settimeout(5)
                self._ws.connect(ws_url)
                with self._lock:
                    self.ws_connected = True
                logger.info("Test WS connected")
                reconnect_count = 0

                while not self._ws_stop.is_set():
                    try:
                        raw = self._ws.recv()
                        if not raw:
                            continue
                        self._handle_ws_event(raw)
                    except websocket.WebSocketTimeoutException:
                        continue
                    except websocket.WebSocketConnectionClosedException:
                        logger.warning("Test WS closed by server")
                        break

            except Exception as e:
                if self._ws_stop.is_set():
                    break
                reconnect_count += 1
                with self._lock:
                    self.ws_connected = False
                logger.warning(f"Test WS error (attempt {reconnect_count}/{WS_MAX_RECONNECTS}): {e}")
                self._ws_stop.wait(WS_RECONNECT_DELAY)

        with self._lock:
            self.ws_connected = False

        if reconnect_count >= WS_MAX_RECONNECTS:
            logger.error("Test WS max reconnects exceeded")
            with self._lock:
                self.error = "WebSocket max reconnects exceeded"

    def _handle_ws_event(self, raw):
        """Process a single WebSocket alert event (no DB writes, memory only)."""
        timestamp = get_current_time_str()

        # Store every raw WS message for debugging display
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            with self._lock:
                self.ws_messages.append({"timestamp": timestamp, "raw": raw[:500]})
            logger.debug(f"Non-JSON WS message: {raw[:200]}")
            return

        with self._lock:
            self.ws_messages.append({"timestamp": timestamp, "event": event})
            if len(self.ws_messages) > 200:
                self.ws_messages = self.ws_messages[-200:]

        rule_string = event.get("rule_string") or event.get("alert") or event.get("rule", "")
        if not rule_string:
            return

        # Attach current snapshot as evidence
        image_b64 = None
        with self._lock:
            if self._latest_frame:
                image_b64 = base64.b64encode(self._latest_frame).decode()

        alert_entry = {
            "rule": rule_string,
            "timestamp": timestamp,
            "image": f"data:image/jpeg;base64,{image_b64}" if image_b64 else None,
        }

        with self._lock:
            self.alerts.append(alert_entry)
            if len(self.alerts) > self.MAX_ALERTS:
                self.alerts = self.alerts[-self.MAX_ALERTS:]

        logger.info(f"Test alert: rule='{rule_string}' at {timestamp}")


test_edge_ai = TestLiveMonitor()
