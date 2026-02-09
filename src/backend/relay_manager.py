"""
Relay Manager - HTTP client for the Jetson-side relay service.

Delegates all relay operations (ffmpeg subprocess management, RTSP push to mediamtx)
to the Jetson-side relay_service.py via REST API. Robot camera frames are fed via
a background FrameFeederThread that POSTs JPEG frames to the relay service.

Requires RELAY_SERVICE_URL environment variable to be set. When empty, relay
functionality is unavailable (relay_service_client will be None).
"""

import threading
import time

import requests as http_requests

from config import RELAY_SERVICE_URL
from logger import get_logger

logger = get_logger("relay_manager", "relay_manager.log")

FEEDER_INTERVAL = 2.0  # 0.5 fps (1 frame per 2s)


class RelayServiceClient:
    """HTTP client for the Jetson-side relay service REST API."""

    def __init__(self, base_url):
        self._base_url = base_url.rstrip("/")
        self._session = http_requests.Session()
        self._feeders = {}  # key -> FrameFeederThread
        self._lock = threading.Lock()

    def is_available(self):
        """Check if the relay service is reachable."""
        try:
            resp = self._session.get(f"{self._base_url}/health", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def start_relay(self, key, relay_type, source_url=None):
        """Start a relay on the service. Returns (rtsp_path, error)."""
        body = {"key": key, "type": relay_type}
        if source_url:
            body["source_url"] = source_url
        try:
            resp = self._session.post(
                f"{self._base_url}/relays", json=body, timeout=10)
            data = resp.json()
            if resp.status_code == 200:
                return data.get("rtsp_path"), None
            return None, data.get("error", f"HTTP {resp.status_code}")
        except Exception as e:
            return None, str(e)

    def feed_frame(self, key, jpeg_bytes):
        """POST a single JPEG frame to the relay service."""
        try:
            resp = self._session.post(
                f"{self._base_url}/relays/{key}/frame",
                data=jpeg_bytes,
                headers={"Content-Type": "application/octet-stream"},
                timeout=5,
            )
            return resp.status_code == 204
        except Exception:
            return False

    def stop_relay(self, key):
        """Stop a specific relay on the service."""
        try:
            self._session.delete(f"{self._base_url}/relays/{key}", timeout=5)
        except Exception as e:
            logger.warning(f"RelayServiceClient: stop_relay({key}) error: {e}")

    def wait_for_stream(self, key, timeout=15):
        """Blocking readiness check on the relay service (Jetson localhost)."""
        try:
            resp = self._session.get(
                f"{self._base_url}/relays/{key}/ready",
                params={"timeout": timeout},
                timeout=timeout + 5,
            )
            if resp.status_code == 200:
                return resp.json().get("ready", False)
            return False
        except Exception as e:
            logger.warning(f"RelayServiceClient: wait_for_stream({key}) error: {e}")
            return False

    def get_status(self):
        """Get status of all relays from the service."""
        try:
            resp = self._session.get(f"{self._base_url}/relays", timeout=5)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            logger.warning(f"RelayServiceClient: get_status error: {e}")
            return {}

    def stop_all(self):
        """Stop all relays on the service and all local frame feeders."""
        self.stop_all_feeders()
        try:
            self._session.post(f"{self._base_url}/relays/stop_all", timeout=5)
        except Exception as e:
            logger.warning(f"RelayServiceClient: stop_all error: {e}")

    def start_frame_feeder(self, key, frame_func):
        """Start a FrameFeederThread that grabs gRPC frames and POSTs them."""
        with self._lock:
            if key in self._feeders:
                return
            feeder = FrameFeederThread(key, frame_func, self)
            feeder.start()
            self._feeders[key] = feeder
            logger.info(f"Started frame feeder for {key}")

    def stop_frame_feeder(self, key):
        """Stop a specific frame feeder thread."""
        with self._lock:
            feeder = self._feeders.pop(key, None)
        if feeder:
            feeder.stop()
            logger.info(f"Stopped frame feeder for {key}")

    def stop_all_feeders(self):
        """Stop all frame feeder threads."""
        with self._lock:
            feeders = list(self._feeders.values())
            self._feeders.clear()
        for feeder in feeders:
            feeder.stop()


class FrameFeederThread:
    """Grabs gRPC frames and POSTs them to the relay service at ~0.5fps."""

    def __init__(self, key, frame_func, client):
        self._key = key
        self._frame_func = frame_func
        self._client = client
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                img = self._frame_func()
                if img and img.data:
                    self._client.feed_frame(self._key, img.data)
            except Exception as e:
                logger.debug(f"FrameFeeder error for {self._key}: {e}")
            self._stop_event.wait(FEEDER_INTERVAL)


# === Module-level instance ===

relay_service_client = RelayServiceClient(RELAY_SERVICE_URL) if RELAY_SERVICE_URL else None

if relay_service_client:
    logger.info(f"Relay service client configured: {RELAY_SERVICE_URL}")
else:
    logger.info("Relay service not configured (RELAY_SERVICE_URL empty)")
