import os
import shutil

# Robot identity (from environment, defaults to single-robot mode)
ROBOT_ID = os.getenv("ROBOT_ID", "default")
ROBOT_NAME = os.getenv("ROBOT_NAME", "Robot")
ROBOT_IP = os.getenv("ROBOT_IP", "192.168.50.133:26400")

# mediamtx RTSP relay
MEDIAMTX_INTERNAL = os.getenv("MEDIAMTX_INTERNAL", "localhost:8554")
MEDIAMTX_EXTERNAL = os.getenv("MEDIAMTX_EXTERNAL", "localhost:8554")

# Relay service (Jetson-side ffmpeg relay, empty = use local RelayManager)
RELAY_SERVICE_URL = os.getenv("RELAY_SERVICE_URL", "")

# Jetson service ports (fixed, co-located on Jetson)
JETSON_JPS_API_PORT = 5010
JETSON_MEDIAMTX_PORT = 8555

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOG_DIR = os.getenv("LOG_DIR", os.path.join(BASE_DIR, "logs"))

# Shared paths
REPORT_DIR = os.path.join(DATA_DIR, "report")
DB_FILE = os.path.join(REPORT_DIR, "report.db")

# Per-robot paths
ROBOT_DATA_DIR = os.path.join(DATA_DIR, ROBOT_ID)
ROBOT_CONFIG_DIR = os.path.join(ROBOT_DATA_DIR, "config")
ROBOT_IMAGES_DIR = os.path.join(ROBOT_DATA_DIR, "report", "images")

POINTS_FILE = os.path.join(ROBOT_CONFIG_DIR, "points.json")
SCHEDULE_FILE = os.path.join(ROBOT_CONFIG_DIR, "patrol_schedule.json")

# Legacy paths (for migration)
_LEGACY_CONFIG_DIR = os.path.join(DATA_DIR, "config")
_LEGACY_POINTS_FILE = os.path.join(_LEGACY_CONFIG_DIR, "points.json")
_LEGACY_SCHEDULE_FILE = os.path.join(_LEGACY_CONFIG_DIR, "patrol_schedule.json")
_LEGACY_SETTINGS_FILE = os.path.join(_LEGACY_CONFIG_DIR, "settings.json")
_LEGACY_IMAGES_DIR = os.path.join(REPORT_DIR, "images")

DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "gemini_model": "gemini-3-flash-preview",
    "system_prompt": "You are a helpful robot assistant. Analyze this image from my patrol.",
    "timezone": "UTC",
    "enable_video_recording": False,
    "video_prompt": "Analyze this video of a robot patrol. Identify any safety hazards, obstacles, or anomalies.",
    "enable_idle_stream": True,
    "report_prompt": """**任務：填寫巡檢報告表**

**背景資訊/表格結構：**
以下是本次巡檢的項目清單。請以這個結構為基礎，進行評估與填寫。

| 類別 (Category) | 編號 (No.) | 巡檢項目 (Check Item) |
| :--- | :--- | :--- |
| 用電安全 | 1 | 公共區域電氣設備使用完畢是否依程序關閉—廁所及走廊 |
| 用電安全 | 2 | 公共區域是否不當使用插座—辨識公共插座是否沒有插線 |
| 室內環境 | 1 | 是否沒有物品掉落阻礙通行 |
| 室內環境 | 2 | 室內照明是否足夠 |
| 防災避難設施 | 1 | 有效光不足場域緊急照明設備是否有正常操作 |
| 防災避難設施 | 2 | 室內裝設有避難指標或避難方向指示燈是否正常運作 |
| 防災避難設施 | 3 | 滅火器是否放置到位位置 |
| 防災避難設施 | 4 | 逃生通道是否無障礙物 |
| 其他 | 1 | 是否有偵測到人體跌倒? |
| 其他 | 2 | 夜間關懷—深夜辦公室電燈未關? |

**指令：**
請以表格形式輸出巡檢結果。除了原有的「類別」、「編號」和「巡檢項目」三欄外，請務必新增以下兩欄：
1.  **結果 (Result)：** 填寫「**O**」（表示符合/正常）或「**X**」（表示不符合/異常）。
2.  **備註/異常說明 (Notes)：** 詳細說明任何標記為「X」的項目，或需要注意的事項。

**請以一個完整的 Markdown 表格呈現最終的巡檢報告。**""",
    "multiday_report_prompt": "Generate a comprehensive summary report for the selected period, highlighting trends and anomalies.",
    "telegram_message_prompt": "Based on the patrol inspection results below, generate a concise Telegram notification message in Traditional Chinese. Summarize the overall status, highlight any anomalies (marked X), and keep it under 500 characters. Use emoji sparingly for readability.",
    "vlm_provider": "gemini",
    "vila_server_url": "http://localhost:9000",
    "vila_model": "VILA1.5-3B",
    "vila_alert_url": "",  # VILA alert endpoint (e.g. http://192.168.50.35:5015), empty = use chat API

    "enable_live_monitor": False,
    "live_monitor_interval": 5,
    "live_monitor_rules": [],

    "jetson_host": "",
    "enable_robot_camera_relay": False,
    "enable_external_rtsp": False,
    "external_rtsp_url": "",
}


def ensure_dirs():
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(ROBOT_CONFIG_DIR, exist_ok=True)
    os.makedirs(ROBOT_IMAGES_DIR, exist_ok=True)


def migrate_legacy_files():
    """Migrate legacy config files to per-robot directories (one-time)."""
    # Migrate points.json
    if os.path.exists(_LEGACY_POINTS_FILE) and not os.path.exists(POINTS_FILE):
        print(f"Migrating points.json to {POINTS_FILE}")
        shutil.copy2(_LEGACY_POINTS_FILE, POINTS_FILE)

    # Migrate patrol_schedule.json
    if os.path.exists(_LEGACY_SCHEDULE_FILE) and not os.path.exists(SCHEDULE_FILE):
        print(f"Migrating patrol_schedule.json to {SCHEDULE_FILE}")
        shutil.copy2(_LEGACY_SCHEDULE_FILE, SCHEDULE_FILE)
