"""Asynchronous JSON-RPC2 client for Komari."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from itertools import count
from json import JSONDecodeError, dumps, loads
from types import MappingProxyType
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    API_PATH,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    DYNAMIC_METRIC_METHODS,
    MAX_METRIC_FILTERS_JSON_BYTES,
    MAX_METRIC_KEYS,
    MAX_METRIC_RESPONSE_BYTES,
    MAX_METRIC_TAG_KEY_LENGTH,
    MAX_METRIC_TAG_VALUE_LENGTH,
    MAX_METRIC_TAGS,
    METHOD_COMMON_NODES,
    METHOD_COMMON_PUBLIC_INFO,
    METHOD_COMMON_RECORDS,
    METHOD_COMMON_VERSION,
    METHOD_LATEST_STATUS,
    METHOD_PING,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_METRIC_DEFINITIONS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_RECORDS,
    METHOD_PUBLIC_PING_TASKS,
    METHOD_PUBLIC_QUERY_METRICS,
    METHOD_PUBLIC_SETTINGS,
    METHOD_PUBLIC_VERSION,
    METHOD_RPC_METHODS,
    METHOD_RPC_VERSION,
    REQUIRED_METHODS,
)
from .exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)
from .models import (
    KomariBootstrapData,
    KomariMetricDefinition,
    KomariMetricSeriesKey,
    KomariMetricValue,
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    KomariPingSeriesKey,
    KomariPingStats,
    KomariPingTask,
    parse_metric_definitions,
    parse_metric_values,
    parse_nodes,
    parse_panel_info,
    parse_ping_metric_stats,
    parse_ping_records_stats,
    parse_ping_tasks,
    parse_statuses,
)

RPC_METHOD_NOT_FOUND = -32601
_AUTH_ERROR_WORDS = ("auth", "credential", "forbidden", "permission", "unauthorized")


def select_ping_stats_method(
    capabilities: frozenset[str],
    node_uuids: tuple[str, ...],
    task_ids: tuple[str, ...],
) -> str:
    """Select one capability that can cover the whole requested Ping batch."""
    if METHOD_PING_METRIC_STATS in capabilities:
        return METHOD_PING_METRIC_STATS
    if METHOD_PUBLIC_PING_RECORDS in capabilities and (
        len(node_uuids) == 1 or len(task_ids) == 1
    ):
        return METHOD_PUBLIC_PING_RECORDS
    if METHOD_COMMON_RECORDS in capabilities:
        return METHOD_COMMON_RECORDS
    raise KomariUnsupportedError(
        "Komari does not expose a one-request Ping statistics method"
    )


class KomariApiClient:
    """Client for the read-only Komari RPC2 API."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        api_key: str | None = None,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        max_concurrency: int = 4,
    ) -> None:
        """Initialize the client without taking ownership of the session."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._timeout = ClientTimeout(
            total=request_timeout,
            connect=connect_timeout,
        )
        self._request_ids = count(1)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._last_metric_skipped_series_count = 0
        self._last_metric_skipped_point_count = 0

    @property
    def base_url(self) -> str:
        """Return the normalized panel base URL."""
        return self._base_url

    @property
    def endpoint(self) -> str:
        """Return the RPC2 endpoint."""
        return f"{self._base_url}{API_PATH}"

    @property
    def authenticated(self) -> bool:
        """Return whether an API key is configured."""
        return self._api_key is not None

    @property
    def last_metric_skipped_series_count(self) -> int:
        """Return the sanitized skip count from the latest Metric query."""
        return self._last_metric_skipped_series_count

    @property
    def last_metric_skipped_point_count(self) -> int:
        """Return the sanitized point skip count from the latest Metric query."""
        return self._last_metric_skipped_point_count

    async def async_call(
        self,
        method: str,
        params: object | None = None,
        *,
        max_response_bytes: int | None = None,
    ) -> Any:
        """Call one Komari JSON-RPC2 method with an optional decoded-size cap."""
        if max_response_bytes is not None and max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        request_id = next(self._request_ids)
        request: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with (
                self._semaphore,
                self._session.post(
                    self.endpoint,
                    json=request,
                    headers=headers,
                    timeout=self._timeout,
                    allow_redirects=False,
                ) as response,
            ):
                if response.status in (401, 403):
                    raise KomariAuthenticationError(
                        "Komari rejected the configured credentials"
                    )
                if 300 <= response.status < 400:
                    raise KomariConnectionError(
                        "Komari returned an unexpected redirect"
                    )
                if response.status >= 400:
                    raise KomariConnectionError(
                        f"Komari returned HTTP {response.status}"
                    )
                payload = await self._async_read_json_response(
                    response,
                    max_response_bytes=max_response_bytes,
                )
        except TimeoutError as err:
            raise KomariConnectionError("Timed out while contacting Komari") from err
        except ClientError as err:
            raise KomariConnectionError("Unable to contact Komari") from err

        return self._parse_rpc_response(payload, request_id)

    @staticmethod
    async def _async_read_json_response(
        response: Any,
        *,
        max_response_bytes: int | None,
    ) -> object:
        """Decode JSON, streaming only responses that need a hard size limit."""
        if max_response_bytes is None:
            try:
                return await response.json(content_type=None)
            except (JSONDecodeError, ValueError) as err:
                raise KomariProtocolError(
                    "Komari returned an invalid JSON response"
                ) from err

        content_length = getattr(response, "content_length", None)
        if isinstance(content_length, int) and content_length > max_response_bytes:
            raise KomariProtocolError("Komari response exceeds safe size limit")

        chunks: list[bytes] = []
        received = 0
        try:
            while chunk := await response.content.read(64 * 1024):
                received += len(chunk)
                if received > max_response_bytes:
                    raise KomariProtocolError("Komari response exceeds safe size limit")
                chunks.append(chunk)
            return loads(b"".join(chunks))
        except (JSONDecodeError, UnicodeDecodeError, ValueError) as err:
            raise KomariProtocolError(
                "Komari returned an invalid JSON response"
            ) from err

    async def async_ping(self) -> None:
        """Validate that the endpoint is a Komari RPC2 server."""
        result = await self.async_call(METHOD_PING)
        if result != "pong":
            raise KomariProtocolError("Komari RPC ping did not return pong")

    async def async_get_rpc_version(self) -> str | None:
        """Return the RPC protocol version."""
        result = await self.async_call(METHOD_RPC_VERSION)
        if result is None:
            return None
        if not isinstance(result, str):
            raise KomariProtocolError("RPC version must be a string")
        return result.strip() or None

    async def async_get_methods(self) -> frozenset[str]:
        """Return available RPC methods."""
        result = await self.async_call(METHOD_RPC_METHODS, {"internal": False})
        if not isinstance(result, list) or not all(
            isinstance(method, str) for method in result
        ):
            raise KomariProtocolError("RPC methods must be an array of strings")
        return frozenset(result)

    async def async_get_panel_info(
        self,
        capabilities: frozenset[str],
        rpc_version: str | None,
    ) -> KomariPanelInfo:
        """Return normalized panel metadata using available public methods."""
        settings_method = self._select_method(
            capabilities,
            METHOD_PUBLIC_SETTINGS,
            METHOD_COMMON_PUBLIC_INFO,
        )
        version_method = self._select_method(
            capabilities,
            METHOD_PUBLIC_VERSION,
            METHOD_COMMON_VERSION,
        )
        settings, version_info = await asyncio.gather(
            self.async_call(settings_method),
            self.async_call(version_method),
        )
        return parse_panel_info(settings, version_info, rpc_version)

    async def async_get_nodes(
        self,
        capabilities: frozenset[str],
    ) -> Mapping[str, KomariNode]:
        """Return normalized node metadata."""
        if self.authenticated and METHOD_COMMON_NODES in capabilities:
            method = METHOD_COMMON_NODES
        elif METHOD_PUBLIC_NODES in capabilities:
            method = METHOD_PUBLIC_NODES
        elif METHOD_COMMON_NODES in capabilities:
            method = METHOD_COMMON_NODES
        else:
            raise KomariUnsupportedError("Komari does not expose a node-list method")
        return parse_nodes(await self.async_call(method))

    async def async_get_latest_status(self) -> Mapping[str, KomariNodeStatus]:
        """Return all latest node statuses."""
        return parse_statuses(await self.async_call(METHOD_LATEST_STATUS))

    async def async_get_ping_tasks(
        self,
        capabilities: frozenset[str],
    ) -> Mapping[str, KomariPingTask]:
        """Return public Ping tasks when the panel exposes the typed method."""
        if METHOD_PUBLIC_PING_TASKS not in capabilities:
            raise KomariUnsupportedError("Komari does not expose public Ping tasks")
        return parse_ping_tasks(await self.async_call(METHOD_PUBLIC_PING_TASKS))

    async def async_get_ping_stats(
        self,
        capabilities: frozenset[str],
        node_uuids: tuple[str, ...],
        task_ids: tuple[str, ...],
        *,
        hours: int,
        max_points: int,
    ) -> Mapping[KomariPingSeriesKey, KomariPingStats]:
        """Return one batch of Ping statistics for visible nodes and tasks."""
        method = select_ping_stats_method(capabilities, node_uuids, task_ids)
        if not node_uuids or not task_ids:
            return MappingProxyType({})

        if method == METHOD_PING_METRIC_STATS:
            stats = parse_ping_metric_stats(
                await self.async_call(
                    method,
                    {
                        "entity_ids": list(node_uuids),
                        "task_ids": list(task_ids),
                        "hours": hours,
                        "max_points": max_points,
                    },
                )
            )
        else:
            if method == METHOD_PUBLIC_PING_RECORDS:
                params: dict[str, object] = {"hours": str(hours)}
                if len(task_ids) == 1:
                    params["task_id"] = task_ids[0]
                else:
                    params["uuid"] = node_uuids[0]
            else:
                params = {
                    "type": "ping",
                    "hours": hours,
                    "maxCount": max_points,
                }
            stats = parse_ping_records_stats(
                await self.async_call(method, params),
                node_uuids=node_uuids,
                task_ids=task_ids,
                max_points=max_points,
            )

        visible_nodes = frozenset(node_uuids)
        selected_tasks = frozenset(task_ids)
        return MappingProxyType(
            {
                key: value
                for key, value in stats.items()
                if key.node_uuid in visible_nodes and key.task_id in selected_tasks
            }
        )

    async def async_get_metric_definitions(
        self,
        capabilities: frozenset[str],
    ) -> Mapping[str, KomariMetricDefinition]:
        """Return bounded Metric Definitions when the whole optional API exists."""
        self._require_dynamic_metric_capabilities(capabilities)
        return parse_metric_definitions(
            await self.async_call(
                METHOD_PUBLIC_METRIC_DEFINITIONS,
                max_response_bytes=MAX_METRIC_RESPONSE_BYTES,
            )
        )

    async def async_query_metrics(
        self,
        capabilities: frozenset[str],
        *,
        metric_keys: tuple[str, ...],
        entity_ids: tuple[str, ...],
        tags: Mapping[str, str],
        hours: int = 4,
        max_points: int = 1,
        aggregation: str = "last",
        fill_empty: bool = False,
    ) -> Mapping[KomariMetricSeriesKey, KomariMetricValue]:
        """Query one exact Metric-filter group without retaining raw points."""
        self._require_dynamic_metric_capabilities(capabilities)
        self._validate_metric_query(
            metric_keys=metric_keys,
            entity_ids=entity_ids,
            tags=tags,
            hours=hours,
            max_points=max_points,
            aggregation=aggregation,
            fill_empty=fill_empty,
        )
        if not metric_keys or not entity_ids:
            self._last_metric_skipped_series_count = 0
            self._last_metric_skipped_point_count = 0
            return MappingProxyType({})

        values = parse_metric_values(
            await self.async_call(
                METHOD_PUBLIC_QUERY_METRICS,
                {
                    "metric_keys": list(metric_keys),
                    "entity_ids": list(entity_ids),
                    "tags": dict(tags),
                    "hours": hours,
                    "max_points": max_points,
                    "aggregation": aggregation,
                    "fill_empty": fill_empty,
                },
                max_response_bytes=MAX_METRIC_RESPONSE_BYTES,
            )
        )
        requested_keys = frozenset(metric_keys)
        requested_nodes = frozenset(entity_ids)
        requested_tags = frozenset(tags.items())
        filtered_values = {
            key: value
            for key, value in values.items()
            if key.metric_key in requested_keys
            and key.entity_id in requested_nodes
            and requested_tags.issubset(key.tags)
        }
        self._last_metric_skipped_series_count = (
            values.skipped_series_count + len(values) - len(filtered_values)
        )
        self._last_metric_skipped_point_count = values.skipped_point_count
        return MappingProxyType(filtered_values)

    async def async_get_metadata(self) -> tuple[KomariPanelInfo, frozenset[str]]:
        """Refresh RPC capabilities, versions, and public panel metadata."""
        rpc_version, capabilities = await asyncio.gather(
            self.async_get_rpc_version(),
            self.async_get_methods(),
        )
        panel = await self.async_get_panel_info(capabilities, rpc_version)
        return panel, capabilities

    async def async_get_bootstrap(self) -> KomariBootstrapData:
        """Validate capabilities and return the initial coherent data set."""
        await self.async_ping()
        rpc_version, capabilities = await asyncio.gather(
            self.async_get_rpc_version(),
            self.async_get_methods(),
        )
        missing = REQUIRED_METHODS - capabilities
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise KomariUnsupportedError(
                f"Komari is missing required RPC methods: {missing_names}"
            )

        panel, nodes, statuses = await asyncio.gather(
            self.async_get_panel_info(capabilities, rpc_version),
            self.async_get_nodes(capabilities),
            self.async_get_latest_status(),
        )
        return KomariBootstrapData(
            panel=panel,
            nodes=nodes,
            statuses=statuses,
            capabilities=capabilities,
        )

    @staticmethod
    def _require_dynamic_metric_capabilities(capabilities: frozenset[str]) -> None:
        """Require both methods because either alone cannot form safe entities."""
        if not DYNAMIC_METRIC_METHODS.issubset(capabilities):
            raise KomariUnsupportedError(
                "Komari does not expose the complete dynamic Metric API"
            )

    @staticmethod
    def _validate_metric_query(
        *,
        metric_keys: tuple[str, ...],
        entity_ids: tuple[str, ...],
        tags: Mapping[str, str],
        hours: int,
        max_points: int,
        aggregation: str,
        fill_empty: bool,
    ) -> None:
        """Reject malformed caller input before it can widen a Metric query."""
        if (
            len(metric_keys) > MAX_METRIC_KEYS
            or len(set(metric_keys)) != len(metric_keys)
            or any(not isinstance(key, str) or not key.strip() for key in metric_keys)
        ):
            raise KomariProtocolError("Metric query has invalid metric keys")
        if any(
            not isinstance(entity_id, str) or not entity_id.strip()
            for entity_id in entity_ids
        ):
            raise KomariProtocolError("Metric query has invalid entity IDs")
        if len(tags) > MAX_METRIC_TAGS or any(
            not isinstance(key, str)
            or not key
            or len(key) > MAX_METRIC_TAG_KEY_LENGTH
            or not isinstance(value, str)
            or len(value) > MAX_METRIC_TAG_VALUE_LENGTH
            for key, value in tags.items()
        ):
            raise KomariProtocolError("Metric query has invalid tag filters")
        encoded_tags = dumps(
            dict(tags), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded_tags) > MAX_METRIC_FILTERS_JSON_BYTES:
            raise KomariProtocolError("Metric query tag filters exceed safe limit")
        if (
            isinstance(hours, bool)
            or not isinstance(hours, int)
            or hours <= 0
            or isinstance(max_points, bool)
            or not isinstance(max_points, int)
            or max_points <= 0
            or not isinstance(aggregation, str)
            or aggregation != "last"
            or not isinstance(fill_empty, bool)
        ):
            raise KomariProtocolError("Metric query has invalid aggregation settings")

    @staticmethod
    def _select_method(
        capabilities: frozenset[str],
        preferred: str,
        fallback: str,
    ) -> str:
        """Select a supported RPC method without guessing names."""
        if preferred in capabilities:
            return preferred
        if fallback in capabilities:
            return fallback
        raise KomariUnsupportedError(
            f"Komari is missing required RPC methods: {preferred} or {fallback}"
        )

    @staticmethod
    def _parse_rpc_response(payload: object, request_id: int) -> Any:
        """Validate and unwrap one JSON-RPC2 response."""
        if not isinstance(payload, Mapping):
            raise KomariProtocolError("RPC response must be an object")
        if payload.get("jsonrpc") != "2.0":
            raise KomariProtocolError("RPC response has an invalid version")
        if payload.get("id") != request_id:
            raise KomariProtocolError("RPC response id does not match the request")

        has_result = "result" in payload
        has_error = "error" in payload
        if has_result == has_error:
            raise KomariProtocolError(
                "RPC response must contain exactly one of result or error"
            )
        if has_result:
            return payload["result"]

        error = payload["error"]
        if not isinstance(error, Mapping):
            raise KomariProtocolError("RPC error must be an object")
        raw_code = error.get("code")
        code = raw_code if isinstance(raw_code, int) else None
        raw_message = error.get("message")
        message = raw_message if isinstance(raw_message, str) else "Unknown RPC error"
        message = message.strip() or "Unknown RPC error"

        if code == RPC_METHOD_NOT_FOUND:
            raise KomariMethodNotFoundError(code, message)
        if any(word in message.casefold() for word in _AUTH_ERROR_WORDS):
            raise KomariPermissionError(code, message)
        raise KomariRpcError(code, message)
