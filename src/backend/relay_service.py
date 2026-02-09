"""
Relay Service - Standalone Flask app managing ffmpeg relay processes on Jetson.

Runs on Jetson alongside mediamtx and JPS. Provides REST API for VP Flask
backends to start/stop RTSP relays.

Unified relay type: RTSP input → transcode H264 Baseline → mediamtx RTSP output.
Input can be from frame_hub ffmpeg push (/raw/...) or external RTSP cameras.

Env vars:
    RELAY_SERVICE_PORT: Listen port (default 5020)
    MEDIAMTX_HOST: mediamtx host:port (default localhost:8555)
    USE_NVENC: Use NVENC hardware encoder (default true)
    RELAY_FPS: Output framerate for transcode (default 0.5)
    LOG_DIR: Log directory (default ./logs)
"""

import atexit
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from flask import Flask, request, jsonify

# --- Config ---

PORT = int(os.getenv("RELAY_SERVICE_PORT", "5020"))
MEDIAMTX_HOST = os.getenv("MEDIAMTX_HOST", "localhost:8555")
USE_NVENC = os.getenv("USE_NVENC", "true").lower() in ("true", "1", "yes")
RELAY_FPS = os.getenv("RELAY_FPS", "0.5")
LOG_DIR = os.getenv("LOG_DIR", "./logs")

MAX_RETRIES = 0  # 0 = unlimited retries
MONITOR_INTERVAL = 10

# --- Logging ---

os.makedirs(LOG_DIR, exist_ok=True)

formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

file_handler = logging.FileHandler(os.path.join(LOG_DIR, "relay_service.log"))
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger = logging.getLogger("relay_service")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# --- Relay Entry ---


class _RelayEntry:
    __slots__ = ("key", "process", "stop_event",
                 "started_at", "restart_count", "source_url", "rtsp_url")

    def __init__(self, key, process, rtsp_url, source_url):
        self.key = key
        self.process = process
        self.rtsp_url = rtsp_url
        self.source_url = source_url
        self.stop_event = threading.Event()
        self.started_at = time.time()
        self.restart_count = 0


# --- Relay Manager ---


class RelayServiceManager:
    """Manages ffmpeg relay subprocesses for the relay service."""

    def __init__(self):
        self._relays = {}  # key -> _RelayEntry
        self._lock = threading.Lock()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        atexit.register(self.stop_all)

    def start_relay(self, key, source_url, mediamtx_url=None):
        """Start a relay. Unified: RTSP input -> transcode -> mediamtx output.

        Args:
            key: Relay key / mediamtx output path (e.g., "robot-a/camera")
            source_url: RTSP input URL. Can be:
                - rtsp://localhost:8555/raw/robot-a/camera  (from frame_hub push)
                - rtsp://admin:pass@192.168.50.45:554/live  (external camera)
            mediamtx_url: Override mediamtx host:port (default MEDIAMTX_HOST)

        Returns:
            (rtsp_path, error_msg)
        """
        rtsp_path = f"/{key}"
        target_host = mediamtx_url or MEDIAMTX_HOST
        rtsp_url = f"rtsp://{target_host}{rtsp_path}"

        with self._lock:
            if key in self._relays and self._relays[key].process.poll() is None:
                logger.info(f"Relay already running: {key}")
                return rtsp_path, None

        if not source_url:
            return None, "source_url is required"

        proc, err = self._start_rtsp_transcode(key, source_url, rtsp_url)
        if err:
            return None, err

        entry = _RelayEntry(key, proc, rtsp_url, source_url)
        threading.Thread(target=self._stderr_reader, args=(proc, key), daemon=True).start()

        with self._lock:
            self._relays[key] = entry

        logger.info(f"Relay started: {key} ({source_url}) -> {rtsp_url}")
        return rtsp_path, None

    def stop_relay(self, key):
        """Stop a specific relay."""
        with self._lock:
            entry = self._relays.pop(key, None)
        if not entry:
            return

        logger.info(f"Stopping relay: {key}")
        entry.stop_event.set()
        self._terminate_process(entry.process)

    def stop_all(self):
        """Stop all active relays."""
        with self._lock:
            keys = list(self._relays.keys())
        for key in keys:
            self.stop_relay(key)

    def get_status(self):
        """Return status dict for all relays."""
        result = {}
        with self._lock:
            for key, entry in self._relays.items():
                running = entry.process.poll() is None
                uptime = time.time() - entry.started_at if running else 0
                result[key] = {
                    "running": running,
                    "uptime": round(uptime, 1),
                    "restart_count": entry.restart_count,
                }
        return result

    def wait_for_stream(self, key, timeout=15):
        """Check if a relay's stream is ready on mediamtx via RTSP DESCRIBE."""
        with self._lock:
            entry = self._relays.get(key)
        if not entry:
            return False

        return _wait_for_stream(entry.rtsp_url, max_wait=timeout)

    # --- Internal ---

    def _start_rtsp_transcode(self, key, source_url, rtsp_url):
        """ffmpeg: RTSP input -> transcode H264 Baseline -> RTSP output."""
        vf = f"fps={RELAY_FPS},scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
        if USE_NVENC:
            cmd = [
                "ffmpeg", "-y",
                "-rtsp_transport", "tcp",
                "-i", source_url,
                "-an",
                "-vf", vf,
                "-c:v", "h264_nvmpi",
                "-b:v", "2M",
                "-pix_fmt", "yuv420p",
                "-f", "rtsp", "-rtsp_transport", "tcp",
                rtsp_url,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-rtsp_transport", "tcp",
                "-i", source_url,
                "-an",
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-level", "3.1",
                "-pix_fmt", "yuv420p",
                "-x264-params", "keyint=1:min-keyint=1:repeat-headers=1",
                "-bsf:v", "dump_extra",
                "-f", "rtsp", "-rtsp_transport", "tcp",
                rtsp_url,
            ]

        logger.info(f"Starting RTSP transcode: {key} (nvenc={USE_NVENC}, fps={RELAY_FPS})")
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                bufsize=0,
            )
            return proc, None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def _stderr_reader(proc, key):
        """Read ffmpeg stderr and log it."""
        try:
            for line in proc.stderr:
                line_str = line.decode(errors="ignore").strip() if isinstance(line, bytes) else line.strip()
                if line_str:
                    logger.info(f"ffmpeg[{key}]: {line_str}")
        except Exception:
            pass

    def _terminate_process(self, proc):
        """SIGTERM → 5s wait → SIGKILL."""
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _monitor_loop(self):
        """Background thread: check relay health, restart dead processes."""
        while True:
            time.sleep(MONITOR_INTERVAL)
            with self._lock:
                entries = list(self._relays.values())

            for entry in entries:
                if entry.process.poll() is None:
                    continue

                if MAX_RETRIES > 0 and entry.restart_count >= MAX_RETRIES:
                    logger.error(f"Relay {entry.key} exceeded max retries ({MAX_RETRIES}), giving up")
                    continue

                delay = min(2 ** entry.restart_count, 30)
                logger.warning(f"Relay {entry.key} died, restarting in {delay}s (attempt {entry.restart_count + 1})")
                time.sleep(delay)

                try:
                    proc, err = self._start_rtsp_transcode(entry.key, entry.source_url, entry.rtsp_url)
                    if err:
                        raise RuntimeError(err)
                    threading.Thread(target=self._stderr_reader, args=(proc, entry.key), daemon=True).start()
                    with self._lock:
                        entry.process = proc
                        entry.restart_count += 1
                        entry.started_at = time.time()

                    logger.info(f"Relay {entry.key} restarted successfully")
                    entry.restart_count = 0  # reset on success
                except Exception as e:
                    logger.error(f"Failed to restart relay {entry.key}: {e}")


# --- Stream readiness check ---


def _wait_for_stream(rtsp_url, max_wait=20):
    """Poll RTSP URL via DESCRIBE until the stream is ready on mediamtx."""
    parsed = urlparse(rtsp_url)
    host = parsed.hostname
    port = parsed.port or 8554
    path = parsed.path or "/"

    deadline = time.time() + max_wait
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            describe = (
                f"DESCRIBE rtsp://{host}:{port}{path} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"\r\n"
            )
            s.sendall(describe.encode())
            resp = s.recv(1024).decode(errors="ignore")
            s.close()

            if "RTSP/1.0 200" in resp:
                logger.info(f"Stream ready after {attempt} attempts: {rtsp_url}")
                return True
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"Stream not ready after {max_wait}s ({attempt} attempts): {rtsp_url}")
    return False


# --- Flask App ---

manager = RelayServiceManager()
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/relays", methods=["GET"])
def list_relays():
    return jsonify(manager.get_status())


@app.route("/relays", methods=["POST"])
def start_relay():
    data = request.get_json(force=True)
    key = data.get("key")
    source_url = data.get("source_url")

    if not key or not source_url:
        return jsonify({"error": "key and source_url required"}), 400

    mediamtx_url = data.get("mediamtx_url")

    rtsp_path, err = manager.start_relay(key, source_url, mediamtx_url)
    if err:
        return jsonify({"error": err}), 400

    return jsonify({"key": key, "rtsp_path": rtsp_path})


@app.route("/relays/<path:key>", methods=["DELETE"])
def stop_relay(key):
    manager.stop_relay(key)
    return jsonify({"stopped": key})


@app.route("/relays/<path:key>/ready", methods=["GET"])
def check_ready(key):
    timeout = request.args.get("timeout", 15, type=int)
    ready = manager.wait_for_stream(key, timeout=timeout)
    return jsonify({"key": key, "ready": ready})


@app.route("/relays/stop_all", methods=["POST"])
def stop_all_relays():
    manager.stop_all()
    return jsonify({"stopped": "all"})


if __name__ == "__main__":
    logger.info(f"Relay Service starting on port {PORT} (mediamtx={MEDIAMTX_HOST}, nvenc={USE_NVENC}, fps={RELAY_FPS})")
    app.run(host="0.0.0.0", port=PORT, debug=False)
