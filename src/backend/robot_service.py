import threading
import time
import kachaka_api
from config import ROBOT_IP


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
            print(f"Connected to Kachaka at {target_ip}")
            return True
        except Exception as e:
            print(f"Failed to connect to Kachaka at {target_ip}: {e}")
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
                    print(f"Error fetching map: {e}")

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
                print(f"Error polling robot state: {e}")
                # Reset client on persistent errors to trigger reconnect
                with self.client_lock:
                    self.client = None

            time.sleep(0.1)

    def get_state(self):
        with self.state_lock:
            return self.robot_state.copy()

    def get_map_bytes(self):
        with self.state_lock:
            return self.map_image_bytes

    def move_to(self, x, y, theta, wait=True):
        with self.client_lock:
            client = self.client
        if client:
            return client.move_to_pose(x, y, theta, wait_for_completion=wait)
        return None

    def move_forward(self, distance, speed=0.1):
        with self.client_lock:
            client = self.client
        if client:
            client.move_forward(distance_meter=distance, speed=speed)

    def rotate(self, angle):
        with self.client_lock:
            client = self.client
        if client:
            client.rotate_in_place(angle_radian=angle)

    def return_home(self):
        with self.client_lock:
            client = self.client
        if client:
            return client.return_home()

    def cancel_command(self):
        with self.client_lock:
            client = self.client
        if client:
            client.cancel_command()

    def get_front_camera_image(self):
        with self.client_lock:
            client = self.client
        if client:
            return client.get_front_camera_ros_compressed_image()
        return None

    def get_back_camera_image(self):
        with self.client_lock:
            client = self.client
        if client:
            return client.get_back_camera_ros_compressed_image()
        return None

    def get_serial(self):
        with self.client_lock:
            client = self.client
        if client:
            return client.get_robot_serial_number()
        return "unknown"

    def get_error_codes(self):
        """Get current robot error codes from Kachaka SDK."""
        with self.client_lock:
            client = self.client
        if client:
            try:
                return client.get_robot_error_code()
            except Exception:
                return {}
        return {}

    def get_locations(self):
        """Get all saved locations from the robot"""
        with self.client_lock:
            client = self.client
        if client:
            try:
                locations = client.get_locations()
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
                print(f"Error getting locations: {e}")
                return []
        return []

# Singleton instance
robot_service = RobotService()
