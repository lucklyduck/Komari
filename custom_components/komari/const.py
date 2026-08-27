"""Constants for the Komari integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "komari"

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONF_API_KEY = "api_key"
CONF_ENABLE_EXPIRY_ENTITIES = "enable_expiry_entities"
CONF_ENABLE_TRAFFIC_ENTITIES = "enable_traffic_entities"
CONF_PING_INTERVAL = "ping_interval"
CONF_PING_STAT_KEYS = "ping_stat_keys"
CONF_PING_TASK_IDS = "ping_task_ids"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_VERIFY_SSL = True
DEFAULT_ENABLE_EXPIRY_ENTITIES = True
DEFAULT_ENABLE_TRAFFIC_ENTITIES = True
DEFAULT_PING_INTERVAL = 300
DEFAULT_PING_STAT_KEYS = ("latest", "loss")
DEFAULT_SCAN_INTERVAL_SECONDS = 30

MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 300
MIN_PING_INTERVAL = 60
MAX_PING_INTERVAL = 1800
MAX_PING_TASKS = 8
MAX_PING_ENTITIES = 256
AUTHORITATIVE_MISSING_THRESHOLD = 3
PING_QUERY_HOURS = 1
PING_QUERY_MAX_POINTS = 120
PING_STAT_KEYS = (
    "latest",
    "loss",
    "min",
    "max",
    "avg",
    "p50",
    "p99",
    "stddev",
)

API_PATH = "/api/rpc2"
DEFAULT_REQUEST_TIMEOUT = 20.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)
INVENTORY_REFRESH_INTERVAL = timedelta(minutes=10)
PING_TASK_REFRESH_INTERVAL = timedelta(minutes=10)
CAPABILITY_REFRESH_INTERVAL = timedelta(hours=6)

METHOD_PING = "rpc.ping"
METHOD_RPC_VERSION = "rpc.version"
METHOD_RPC_METHODS = "rpc.methods"
METHOD_PUBLIC_SETTINGS = "public:getPublicSettings"
METHOD_COMMON_PUBLIC_INFO = "common:getPublicInfo"
METHOD_PUBLIC_VERSION = "public:getVersion"
METHOD_COMMON_VERSION = "common:getVersion"
METHOD_PUBLIC_NODES = "public:getNodesInformation"
METHOD_COMMON_NODES = "common:getNodes"
METHOD_LATEST_STATUS = "common:getNodesLatestStatus"
METHOD_PUBLIC_PING_TASKS = "public:getPublicPingTasks"
METHOD_PING_METRIC_STATS = "public:getPingMetricStats"
METHOD_PUBLIC_PING_RECORDS = "public:getPingRecords"
METHOD_COMMON_RECORDS = "common:getRecords"

PING_STATS_METHODS = frozenset(
    {
        METHOD_PING_METRIC_STATS,
        METHOD_PUBLIC_PING_RECORDS,
        METHOD_COMMON_RECORDS,
    }
)

REQUIRED_METHODS = frozenset({METHOD_LATEST_STATUS})
