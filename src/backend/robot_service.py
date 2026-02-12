import threading
import time
import grpc
import kachaka_api
from config import ROBOT_IP
from logger import get_logger

logger = get_logger(__name__, "robot.log")

GRPC_RETRY_DELAY = 2.0
COMMAND_POLL_INTERVAL = 0.5  # polling interval for command completion


class RobotService:
    def __init__(self):
        self.client = None
        self.client_lock = threading.Lock()  # Lock for client access (TOCTOU fix)
        self.state_lock = threading.Lock()
        self.robot_state = {
            "battery": 0,
            "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "map_info": {
                "resolution": 0.05,
                "width": 0,
                "height": 0,
                "origin_x": 0.0,
                "origin_y": 0.0
            },
        }
        self.map_image_bytes = None

        # Start background polling
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.polling_thread.start()

    def connect(self):
        target_ip = ROBOT_IP

        try:
            new_client = kachaka_api.KachakaApiClient(target_ip)
            new_client.get_robot_serial_number()
            with self.client_lock:
                self.client = new_client
            logger.info(f"Connected to Kachaka at {target_ip}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Kachaka at {target_ip}: {e}")
            with self.client_lock:
                self.client = None
            return False

    def get_client(self):
        with self.client_lock:
            return self.client

    def _polling_loop(self):
        while True:
            with self.client_lock:
                current_client = self.client

            if not current_client:
                self.connect()
                with self.client_lock:
                    current_client = self.client
                if not current_client:
                    time.sleep(2)
                    continue

            # Fetch map if missing
            with self.state_lock:
                map_missing = self.map_image_bytes is None

            if map_missing:
                try:
                    png_map = current_client.get_png_map()
                    with self.state_lock:
                        self.map_image_bytes = png_map.data
                        self.robot_state["map_info"].update({
                            "resolution": png_map.resolution,
                            "width": png_map.width,
                            "height": png_map.height,
                            "origin_x": png_map.origin.x,
                            "origin_y": png_map.origin.y
                        })
                except Exception as e:
                    logger.warning(f"Error fetching map: {e}")

            # Poll status
            try:
                pose = current_client.get_robot_pose()
                battery = current_client.get_battery_info()

                with self.state_lock:
                    actual_pose = getattr(pose, 'pose', pose)
                    self.robot_state["pose"].update({
                        "x": actual_pose.x,
                        "y": actual_pose.y,
                        "theta": actual_pose.theta
                    })

                    if isinstance(battery, tuple) and len(battery) > 0:
                        self.robot_state["battery"] = int(battery[0])
                    elif hasattr(battery, 'percentage'):
                        self.robot_state["battery"] = int(battery.percentage)
                    else:
                        self.robot_state["battery"] = int(battery) if isinstance(battery, (int, float)) else 0

            except Exception as e:
                logger.warning(f"Error polling robot state: {e}")
                # Reset client on persistent errors to trigger reconnect
                with self.client_lock:
                    self.client = None

            time.sleep(0.1)

    def _grpc_retry(self, operation, label="gRPC call", max_retries=None):
        """Execute a gRPC operation with automatic retry on connection failure.

        By default retries forever (max_retries=None) — suitable for mesh
        networks where connectivity is intermittent but will eventually recover.
        Set max_retries for fire-and-forget calls like cancel_command.

        Args:
            operation: callable(client) -> result
            label: description for log messages
            max_retries: max attempts (None = infinite)
        """
        attempt = 0
        while True:
            if max_retries is not None and attempt > max_retries:
                logger.warning(f"{label}: giving up after {attempt} attempts")
                return None

            with self.client_lock:
                client = self.client

            if not client:
                attempt += 1
                logger.warning(f"{label}: no client (attempt {attempt}), reconnecting...")
                self.connect()
                time.sleep(GRPC_RETRY_DELAY)
                continue

            try:
                return operation(client)
            except grpc.RpcError as e:
                attempt += 1
                logger.warning(f"{label}: gRPC error (attempt {attempt}): {e.code().name}")
                with self.client_lock:
                    self.client = None
                time.sleep(GRPC_RETRY_DELAY)
                self.connect()

    def get_state(self):
        with self.state_lock:
            return self.robot_state.copy()

    def get_map_bytes(self):
        with self.state_lock:
            return self.map_image_bytes

    def move_to(self, x, y, theta, wait=True):
        """Move robot to pose. Disconnection-safe for mesh network roaming.

        Phase 1: Send move command (retry until accepted).
        Phase 2: Poll is_command_running() until done (reconnect-safe — never
                 re-sends the move command, just keeps checking status).
        """
        # Phase 1: fire command (no wait)
        result = self._grpc_retry(
            lambda c: c.move_to_pose(x, y, theta, wait_for_completion=False),
            label="move_to (send)")

        if not wait:
            return result

        # Phase 2: poll for completion (reconnect-safe)
        while True:
            running = self._grpc_retry(
                lambda c: c.is_command_running(),
                label="move_to (poll)")
            if not running:
                break
            time.sleep(COMMAND_POLL_INTERVAL)

        # Get final result
        last = self._grpc_retry(
            lambda c: c.get_last_command_result(),
            label="move_to (result)")
        if last:
            return last[0]  # Result(success, error_code)
        return result

    def move_forward(self, distance, speed=0.1):
        self._grpc_retry(
            lambda c: c.move_forward(distance_meter=distance, speed=speed),
            label="move_forward")

    def rotate(self, angle):
        self._grpc_retry(
            lambda c: c.rotate_in_place(angle_radian=angle),
            label="rotate")

    def return_home(self):
        """Return to charging dock. Same two-phase pattern as move_to."""
        self._grpc_retry(
            lambda c: c.return_home(wait_for_completion=False),
            label="return_home (send)")

        while True:
            running = self._grpc_retry(
                lambda c: c.is_command_running(),
                label="return_home (poll)")
            if not running:
                break
            time.sleep(COMMAND_POLL_INTERVAL)

        last = self._grpc_retry(
            lambda c: c.get_last_command_result(),
            label="return_home (result)")
        if last:
            return last[0]
        return None

    def cancel_command(self):
        self._grpc_retry(
            lambda c: c.cancel_command(),
            label="cancel_command",
            max_retries=3)

    def get_front_camera_image(self):
        return self._grpc_retry(
            lambda c: c.get_front_camera_ros_compressed_image(),
            label="get_front_camera")

    def get_back_camera_image(self):
        return self._grpc_retry(
            lambda c: c.get_back_camera_ros_compressed_image(),
            label="get_back_camera")

    def get_serial(self):
        return self._grpc_retry(
            lambda c: c.get_robot_serial_number(),
            label="get_serial")

    def get_error_codes(self):
        """Get current robot error codes from Kachaka SDK."""
        try:
            return self._grpc_retry(
                lambda c: c.get_robot_error_code(),
                label="get_error_codes")
        except Exception:
            return {}

    def get_locations(self):
        """Get all saved locations from the robot."""
        try:
            locations = self._grpc_retry(
                lambda c: c.get_locations(),
                label="get_locations")
            result = []
            for loc in locations:
                result.append({
                    "id": loc.id,
                    "name": loc.name,
                    "x": loc.pose.x,
                    "y": loc.pose.y,
                    "theta": loc.pose.theta
                })
            return result
        except Exception as e:
            logger.error(f"Error getting locations: {e}")
            return []

# Singleton instance
robot_service = RobotService()
