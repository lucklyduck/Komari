"""Constants for the Komari integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "komari"

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONF_API_KEY = "api_key"

API_PATH = "/api/rpc2"
DEFAULT_REQUEST_TIMEOUT = 20.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
INVENTORY_REFRESH_INTERVAL = timedelta(minutes=10)

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

REQUIRED_METHODS = frozenset({METHOD_LATEST_STATUS})
