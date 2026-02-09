"""
Frame Hub - centralized camera frame management.

Single gRPC polling thread feeds an in-memory frame cache. All local consumers
(frontend MJPEG, Gemini inspection, video recording, evidence capture) read from
the cache instead of making independent gRPC calls.

On-demand ffmpeg push sends frames to Jetson mediamtx as RTSP for Edge AI relay.

Polling lifecycle is controlled by patrol state + enable_idle_stream setting:
- Patrolling: always polling
- Idle + enable_idle_stream=true: polling (frontend shows live feed)
- Idle + enable_idle_stream=false: NOT polling (zero gRPC bandwidth)
"""

import subprocess
import signal
import threading
import time

from logger import get_logger

logger = get_logger("frame_hub", "frame_hub.log")

POLL_INTERVAL = 0.1    # ~10fps gRPC polling
FEEDER_INTERVAL = 2.0  # 0.5fps for ffmpeg push
PUSH_MONITOR_INTERVAL = 5.0  # check ffmpeg health every 5s


class FrameHub:
    def __init__(self, frame_func):
        """
        Args:
            frame_func: callable returning gRPC image response with .data attribute
                        (i.e., robot_service.get_front_camera_image)
        """
        self._frame_func = frame_func
        self._latest_frame = None        # gRPC response object (has .data)
        self._frame_lock = threading.Lock()

        # Polling lifecycle
        self._polling = False
        self._poll_thread = None
        self._poll_stop = threading.Event()
        self._patrol_active = False
        self._idle_stream_enabled = True  # matches settings enable_idle_stream

        # ffmpeg RTSP push (on-demand)
        self._ffmpeg_proc = None
        self._feeder_thread = None
        self._feeder_stop = threading.Event()
        self._monitor_thread = None
        self._push_target = None   # stored for auto-restart
        self._push_path = None     # stored for auto-restart

    # --- Polling Lifecycle ---

    def start_polling(self):
        """Start gRPC polling, updating frame cache at ~10fps. Idempotent."""
        if self._polling:
            return
        self._polling = True
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("Frame polling started")

    def stop_polling(self):
        """Stop gRPC polling. Frame cache set to None. Idempotent."""
        if not self._polling:
            return
        self._polling = False
        self._poll_stop.set()
        with self._frame_lock:
            self._latest_frame = None
        logger.info("Frame polling stopped")

    def set_patrol_active(self, active):
        """Called by patrol_service when patrol starts/stops."""
        self._patrol_active = active
        self._evaluate()

    def on_idle_stream_changed(self, enabled):
        """Called when enable_idle_stream setting changes."""
        self._idle_stream_enabled = enabled
        self._evaluate()

    def _evaluate(self):
        """Start or stop polling based on patrol state + idle stream setting."""
        should_poll = self._patrol_active or self._idle_stream_enabled
        if should_poll:
            self.start_polling()
        else:
            self.stop_polling()

    def _poll_loop(self):
        """Background thread: poll gRPC at ~10fps, cache latest frame."""
        while not self._poll_stop.is_set():
            try:
                frame = self._frame_func()
                if frame:
                    with self._frame_lock:
                        self._latest_frame = frame
            except Exception as e:
                logger.debug(f"Poll error: {e}")
            self._poll_stop.wait(POLL_INTERVAL)

    # --- Frame Cache API ---

    def get_latest_frame(self):
        """Return latest cached gRPC image response (has .data attribute).
        Returns None if no frame available.
        Compatible with robot_service.get_front_camera_image() return format.
        """
        with self._frame_lock:
            return self._latest_frame

    # --- RTSP Push to Jetson mediamtx ---

    def start_rtsp_push(self, target_mediamtx, rtsp_path):
        """Start ffmpeg pushing frames from cache to Jetson mediamtx as RTSP.

        Automatically ensures polling is running (push needs frames).
        Spawns a monitor thread that auto-restarts ffmpeg if it dies.

        Args:
            target_mediamtx: Jetson mediamtx address (e.g., "192.168.50.1:8555")
            rtsp_path: RTSP path (e.g., "/raw/robot-a/camera")
        """
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            logger.info("RTSP push already running")
            return

        self._push_target = target_mediamtx
        self._push_path = rtsp_path
        self.start_polling()  # push needs frames in cache

        self._start_ffmpeg_and_feeder()

        # Start monitor thread for auto-restart
        self._feeder_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_push, daemon=True)
        self._monitor_thread.start()

    def stop_rtsp_push(self):
        """Stop ffmpeg RTSP push. Does not stop polling (controlled by _evaluate)."""
        self._feeder_stop.set()

        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.stdin.close()
            except Exception:
                pass
            self._terminate_process(self._ffmpeg_proc)
            self._ffmpeg_proc = None

        if self._feeder_thread and self._feeder_thread.is_alive():
            self._feeder_thread.join(timeout=3)
        self._feeder_thread = None

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3)
        self._monitor_thread = None

        self._push_target = None
        self._push_path = None

        self._evaluate()  # re-evaluate if polling should continue
        logger.info("RTSP push stopped")

    def _start_ffmpeg_and_feeder(self):
        """Start ffmpeg process and feeder thread. Used for initial start and restart."""
        rtsp_url = f"rtsp://{self._push_target}{self._push_path}"
        cmd = [
            "ffmpeg", "-y",
            "-f", "image2pipe",
            "-framerate", str(1.0 / FEEDER_INTERVAL),  # 0.5fps
            "-i", "pipe:0",
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-pix_fmt", "yuv420p",
            "-x264-params", "keyint=1:min-keyint=1:repeat-headers=1",
            "-bsf:v", "dump_extra",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            rtsp_url,
        ]

        logger.info(f"Starting RTSP push: {rtsp_url}")
        self._ffmpeg_proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            bufsize=0,
        )
        # stderr reader thread (log ffmpeg output)
        threading.Thread(
            target=self._stderr_reader,
            args=(self._ffmpeg_proc,),
            daemon=True,
        ).start()

        # feeder thread: read from cache at 0.5fps, write JPEG to ffmpeg stdin
        self._feeder_thread = threading.Thread(
            target=self._feeder_loop, daemon=True
        )
        self._feeder_thread.start()

    def _feeder_loop(self):
        """Feed JPEG frames from cache to ffmpeg stdin at 0.5fps."""
        while not self._feeder_stop.is_set():
            try:
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                    frame = self.get_latest_frame()
                    if frame and frame.data:
                        self._ffmpeg_proc.stdin.write(frame.data)
                        self._ffmpeg_proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            except Exception as e:
                logger.debug(f"Feeder error: {e}")
            self._feeder_stop.wait(FEEDER_INTERVAL)

    def _monitor_push(self):
        """Monitor ffmpeg health, auto-restart if dead."""
        while not self._feeder_stop.is_set():
            self._feeder_stop.wait(PUSH_MONITOR_INTERVAL)
            if self._feeder_stop.is_set():
                break

            if self._ffmpeg_proc and self._ffmpeg_proc.poll() is not None:
                logger.warning("ffmpeg push died, restarting...")
                try:
                    self._start_ffmpeg_and_feeder()
                    logger.info("ffmpeg push restarted successfully")
                except Exception as e:
                    logger.error(f"ffmpeg push restart failed: {e}")

    @staticmethod
    def _stderr_reader(proc):
        try:
            for line in proc.stderr:
                s = line.decode(errors="ignore").strip() if isinstance(line, bytes) else line.strip()
                if s:
                    logger.debug(f"ffmpeg: {s}")
        except Exception:
            pass

    @staticmethod
    def _terminate_process(proc):
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


# === Module-level instance ===

from robot_service import robot_service
from settings_service import settings_service

frame_hub = FrameHub(robot_service.get_front_camera_image)

# Initialize polling based on current settings
_idle_stream = settings_service.get("enable_idle_stream")
if _idle_stream is None:
    _idle_stream = True  # default
frame_hub.on_idle_stream_changed(_idle_stream)
