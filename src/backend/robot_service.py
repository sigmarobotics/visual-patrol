import threading
import time
import kachaka_api
from config import ROBOT_IP
from logger import get_logger

logger = get_logger(__name__, "robot.log")

POLL_INTERVAL = 0.1
RECONNECT_WAIT = 2.0
SEND_RETRY_INTERVAL = 2.0
COMMAND_POLL_INTERVAL = 0.5


class RobotService:
    def __init__(self):
        self.client = kachaka_api.KachakaApiClient(ROBOT_IP)
        self.connected = False
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

    def _polling_loop(self):
        was_connected = False
        while True:
            try:
                # Fetch map if missing (failure here doesn't block pose/battery)
                with self.state_lock:
                    map_missing = self.map_image_bytes is None

                if map_missing:
                    try:
                        png_map = self.client.get_png_map()
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

                # Poll pose and battery
                pose = self.client.get_robot_pose()
                battery = self.client.get_battery_info()

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

                if not was_connected:
                    logger.info(f"Connected to Kachaka at {ROBOT_IP}")
                    was_connected = True
                self.connected = True
                time.sleep(POLL_INTERVAL)

            except Exception as e:
                if was_connected:
                    logger.warning(f"Lost connection to Kachaka: {e}")
                    was_connected = False
                self.connected = False
                time.sleep(RECONNECT_WAIT)

    def get_state(self):
        with self.state_lock:
            return self.robot_state.copy()

    def get_map_bytes(self):
        with self.state_lock:
            return self.map_image_bytes

    def move_to(self, x, y, theta, wait=True):
        """Move robot to pose. Resilient to transient gRPC failures on mesh networks.

        Phase 1: Send move command (retry until accepted).
        Phase 2: Poll is_command_running() until done (never re-sends move).
        Phase 3: Try to get result, fail gracefully.
        """
        # Phase 1: send command
        while True:
            try:
                result = self.client.move_to_pose(x, y, theta, wait_for_completion=False)
                break
            except Exception as e:
                logger.warning(f"move_to (send): {e}")
                time.sleep(SEND_RETRY_INTERVAL)

        if not wait:
            return result

        # Phase 2: wait for command to be accepted, then poll for completion
        # Without this, is_command_running() may return False immediately
        # (reflecting the previous command's state) before Kachaka registers the new one.
        for _ in range(20):  # up to 10s for command acceptance
            try:
                if self.client.is_command_running():
                    break
            except Exception as e:
                logger.warning(f"move_to (accept): {e}")
            time.sleep(COMMAND_POLL_INTERVAL)

        while True:
            try:
                if not self.client.is_command_running():
                    break
            except Exception as e:
                logger.warning(f"move_to (poll): {e}")
            time.sleep(COMMAND_POLL_INTERVAL)

        # Phase 3: get result
        try:
            last = self.client.get_last_command_result()
            if last:
                return last[0]
        except Exception as e:
            logger.warning(f"move_to (result): {e}")
        return result

    def return_home(self):
        """Return to charging dock. Same two-phase pattern as move_to."""
        # Phase 1: send command
        while True:
            try:
                self.client.return_home(wait_for_completion=False)
                break
            except Exception as e:
                logger.warning(f"return_home (send): {e}")
                time.sleep(SEND_RETRY_INTERVAL)

        # Phase 2: wait for command acceptance, then poll for completion
        for _ in range(20):
            try:
                if self.client.is_command_running():
                    break
            except Exception as e:
                logger.warning(f"return_home (accept): {e}")
            time.sleep(COMMAND_POLL_INTERVAL)

        while True:
            try:
                if not self.client.is_command_running():
                    break
            except Exception as e:
                logger.warning(f"return_home (poll): {e}")
            time.sleep(COMMAND_POLL_INTERVAL)

        # Phase 3: get result
        try:
            last = self.client.get_last_command_result()
            if last:
                return last[0]
        except Exception as e:
            logger.warning(f"return_home (result): {e}")
        return None

    def move_forward(self, distance, speed=0.1):
        self.client.move_forward(distance_meter=distance, speed=speed)

    def rotate(self, angle):
        self.client.rotate_in_place(angle_radian=angle)

    def cancel_command(self):
        try:
            self.client.cancel_command()
        except Exception as e:
            logger.warning(f"cancel_command: {e}")

    def get_front_camera_image(self):
        try:
            return self.client.get_front_camera_ros_compressed_image()
        except Exception as e:
            logger.warning(f"get_front_camera: {e}")
            return None

    def get_back_camera_image(self):
        try:
            return self.client.get_back_camera_ros_compressed_image()
        except Exception as e:
            logger.warning(f"get_back_camera: {e}")
            return None

    def get_serial(self):
        try:
            return self.client.get_robot_serial_number()
        except Exception as e:
            logger.warning(f"get_serial: {e}")
            return None

    def get_error_codes(self):
        try:
            return self.client.get_robot_error_code()
        except Exception as e:
            logger.warning(f"get_error_codes: {e}")
            return {}

    def get_locations(self):
        try:
            locations = self.client.get_locations()
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
