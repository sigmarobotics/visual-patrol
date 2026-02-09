"""
Relay Manager - HTTP client for the Jetson-side relay service.

Delegates all relay operations (ffmpeg subprocess management, RTSP transcode)
to the Jetson-side relay_service.py via REST API. All relays are unified:
RTSP input -> transcode -> RTSP output.

Requires RELAY_SERVICE_URL environment variable to be set. When empty, relay
functionality is unavailable (relay_service_client will be None).
"""

import requests as http_requests

from config import RELAY_SERVICE_URL
from logger import get_logger

logger = get_logger("relay_manager", "relay_manager.log")


class RelayServiceClient:
    """HTTP client for the Jetson-side relay service REST API."""

    def __init__(self, base_url):
        self._base_url = base_url.rstrip("/")
        self._session = http_requests.Session()

    def is_available(self):
        """Check if the relay service is reachable."""
        try:
            resp = self._session.get(f"{self._base_url}/health", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def start_relay(self, key, source_url):
        """Start a relay on the service. source_url is always an RTSP URL.

        Returns (rtsp_path, error).
        """
        body = {"key": key, "source_url": source_url}
        try:
            resp = self._session.post(
                f"{self._base_url}/relays", json=body, timeout=10)
            data = resp.json()
            if resp.status_code == 200:
                return data.get("rtsp_path"), None
            return None, data.get("error", f"HTTP {resp.status_code}")
        except Exception as e:
            return None, str(e)

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
        """Stop all relays on the service."""
        try:
            self._session.post(f"{self._base_url}/relays/stop_all", timeout=5)
        except Exception as e:
            logger.warning(f"RelayServiceClient: stop_all error: {e}")


# === Module-level instance ===

relay_service_client = RelayServiceClient(RELAY_SERVICE_URL) if RELAY_SERVICE_URL else None

if relay_service_client:
    logger.info(f"Relay service client configured: {RELAY_SERVICE_URL}")
else:
    logger.info("Relay service not configured (RELAY_SERVICE_URL empty)")
