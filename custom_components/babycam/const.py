"""Constants for the BabyCam integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "babycam"
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

PANEL_URL_PATH = "babycam"
PANEL_TITLE = "BabyCam"
PANEL_ICON = "mdi:baby-face-outline"
STATIC_URL_BASE = "/babycam_static"
PANEL_MODULE = f"{STATIC_URL_BASE}/babycam-panel.js"

# Camera source types
SOURCE_ENTITY = "entity"
SOURCE_REOLINK = "reolink"

# Config entry data keys
CONF_SOURCE_TYPE = "source_type"
CONF_CAMERA_ENTITY = "camera_entity"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_RTSP_PORT = "rtsp_port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_CHANNEL = "channel"
CONF_USE_HTTPS = "use_https"
CONF_STREAM_MAIN = "stream_main"
CONF_STREAM_SUB = "stream_sub"

# Options keys
CONF_SNAPSHOT_INTERVAL = "snapshot_interval"
CONF_DETECTION_THRESHOLD = "detection_threshold"
CONF_ABSENCE_GRACE = "absence_grace"
CONF_ROI = "roi"
CONF_CRY_DETECTION = "cry_detection"
CONF_CRY_THRESHOLD = "cry_threshold"
CONF_CRY_COOLDOWN = "cry_cooldown"
CONF_NOTIFY_SERVICES = "notify_services"
CONF_MEDIA_PLAYERS = "media_players"
CONF_PRESENCE_MODEL = "presence_model"
CONF_CRY_MODEL = "cry_model"
CONF_CONNECTION_TIMEOUT = "connection_timeout"

# Runtime toggles persisted in entry options by the switch/select/number entities
OPT_STREAM_MODE = "stream_mode"
OPT_AI_ENABLED = "ai_enabled"
OPT_NOTIFICATIONS_ENABLED = "notifications_enabled"

# Defaults
DEFAULT_PORT = 80
DEFAULT_RTSP_PORT = 554
DEFAULT_CHANNEL = 0
DEFAULT_STREAM_MAIN = "h264Preview_01_main"
DEFAULT_STREAM_SUB = "h264Preview_01_sub"
DEFAULT_SNAPSHOT_INTERVAL = 30  # seconds
MIN_SNAPSHOT_INTERVAL = 5
MAX_SNAPSHOT_INTERVAL = 600
DEFAULT_DETECTION_THRESHOLD = 0.4
DEFAULT_ABSENCE_GRACE = 3  # consecutive misses before "not in bed"
DEFAULT_ROI = "0,0,1,1"  # x1,y1,x2,y2 as fractions of the frame
DEFAULT_CRY_THRESHOLD = 0.5
DEFAULT_CRY_COOLDOWN = 120  # seconds between cry notifications
DEFAULT_CONNECTION_TIMEOUT = 90  # seconds without a snapshot -> connection lost
DEFAULT_PRESENCE_MODEL = "yolov8n.onnx"
DEFAULT_CRY_MODEL = "yamnet.onnx"

MODELS_SUBDIR = "babycam/models"

# Stream / view modes shown in the panel
MODE_SNAPSHOTS = "snapshots"
MODE_LOW = "low"
MODE_HIGH = "high"
STREAM_MODES = [MODE_SNAPSHOTS, MODE_LOW, MODE_HIGH]
DEFAULT_STREAM_MODE = MODE_LOW

# Monitoring state
MONITOR_OFF = "off"
MONITOR_AUTO = "auto"  # started by AI detection
MONITOR_MANUAL = "manual"  # started by user

# Services
SERVICE_START_MONITORING = "start_monitoring"
SERVICE_STOP_MONITORING = "stop_monitoring"
SERVICE_CHECK_NOW = "check_now"

# Events
EVENT_CRY_DETECTED = f"{DOMAIN}_cry_detected"
EVENT_CONNECTION_LOST = f"{DOMAIN}_connection_lost"
EVENT_BABY_DETECTED = f"{DOMAIN}_baby_detected"

# Dispatcher signal
SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"

# Audio view
AUDIO_VIEW_URL = "/api/babycam/{entry_id}/audio.mp3"
