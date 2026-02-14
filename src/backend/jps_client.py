"""
VILA JPS API Client - shared HTTP operations for LiveMonitor and TestLiveMonitor.

Provides stateless functions for stream registration, alert rules, and WebSocket listening.
"""

import json
import threading
from urllib.parse import urlparse

import requests
import websocket

from logger import get_logger

logger = get_logger("jps_client", "edge_ai_service.log")

WS_PORT = 5016
WS_RECONNECT_DELAY = 5
WS_MAX_RECONNECTS = 10


# --- HTTP API ---

def cleanup_stale_streams(vila_jps_url):
    """Remove all existing streams from VILA JPS to avoid 'Stream Maximum reached' errors."""
    try:
        url = f"{vila_jps_url}/api/v1/live-stream"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        for s in resp.json():
            sid = s.get("id")
            if sid:
                try:
                    deregister_stream(vila_jps_url, sid)
                    logger.info(f"Cleaned up stale stream: {sid}")
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Failed to cleanup stale streams: {e}")


def register_stream(vila_jps_url, rtsp_url, name):
    """POST /api/v1/live-stream to register a stream. Returns stream_id or None."""
    url = f"{vila_jps_url}/api/v1/live-stream"
    body = {"liveStreamUrl": rtsp_url, "name": name}
    resp = requests.post(url, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("id") or data.get("stream_id")


def set_alert_rules(vila_jps_url, stream_id, rules):
    """POST /api/v1/alerts to set alert rules for a stream."""
    url = f"{vila_jps_url}/api/v1/alerts"
    body = {"alerts": rules, "id": stream_id}
    resp = requests.post(url, json=body, timeout=60)
    resp.raise_for_status()


def deregister_stream(vila_jps_url, stream_id):
    """DELETE /api/v1/live-stream/{stream_id}."""
    url = f"{vila_jps_url}/api/v1/live-stream/{stream_id}"
    resp = requests.delete(url, timeout=10)
    resp.raise_for_status()


# --- WebSocket Listener ---

def run_ws_listener(vila_jps_url, stop_event, on_message, on_connect=None, on_disconnect=None,
                    on_max_reconnects=None, label=""):
    """Connect to VILA JPS WebSocket and dispatch events via callbacks.

    Args:
        vila_jps_url: JPS base URL (e.g. "http://jetson:5010")
        stop_event: threading.Event — set to stop the listener
        on_message: callable(raw_str) — called for each received message
        on_connect: callable() or None — called when WS connects
        on_disconnect: callable() or None — called when WS disconnects
        on_max_reconnects: callable() or None — called when max reconnects exceeded
        label: str — log prefix (e.g. "VILA" or "Test")

    Returns:
        websocket.WebSocket reference (mutable) for external close, or None.
    """
    parsed = urlparse(vila_jps_url.rstrip("/"))
    ws_host = parsed.hostname
    ws_url = f"ws://{ws_host}:{WS_PORT}/api/v1/alerts/ws"

    ws_ref = [None]  # mutable ref so caller can close
    reconnect_count = 0

    while not stop_event.is_set() and reconnect_count < WS_MAX_RECONNECTS:
        try:
            logger.info(f"{label} WS connecting: {ws_url}")
            ws = websocket.WebSocket()
            ws.settimeout(5)
            ws.connect(ws_url)
            ws_ref[0] = ws
            logger.info(f"{label} WS connected")
            reconnect_count = 0
            if on_connect:
                on_connect()

            while not stop_event.is_set():
                try:
                    raw = ws.recv()
                    if not raw:
                        continue
                    on_message(raw)
                except websocket.WebSocketTimeoutException:
                    continue
                except websocket.WebSocketConnectionClosedException:
                    logger.warning(f"{label} WS closed by server")
                    break

        except Exception as e:
            if stop_event.is_set():
                break
            reconnect_count += 1
            if on_disconnect:
                on_disconnect()
            logger.warning(f"{label} WS error (attempt {reconnect_count}/{WS_MAX_RECONNECTS}): {e}")
            stop_event.wait(WS_RECONNECT_DELAY)

    if on_disconnect:
        on_disconnect()

    if reconnect_count >= WS_MAX_RECONNECTS:
        logger.error(f"{label} WS max reconnects exceeded")
        if on_max_reconnects:
            on_max_reconnects()

    return ws_ref[0]
