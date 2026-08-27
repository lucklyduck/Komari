"""Asynchronous JSON-RPC2 client for Komari."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from itertools import count
from json import JSONDecodeError
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    API_PATH,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    METHOD_COMMON_NODES,
    METHOD_COMMON_PUBLIC_INFO,
    METHOD_COMMON_VERSION,
    METHOD_LATEST_STATUS,
    METHOD_PING,
    METHOD_PING_METRIC_STATS,
    METHOD_PUBLIC_NODES,
    METHOD_PUBLIC_PING_TASKS,
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
    KomariNode,
    KomariNodeStatus,
    KomariPanelInfo,
    KomariPingSeriesKey,
    KomariPingStats,
    KomariPingTask,
    parse_nodes,
    parse_panel_info,
    parse_ping_metric_stats,
    parse_ping_tasks,
    parse_statuses,
)

RPC_METHOD_NOT_FOUND = -32601
_AUTH_ERROR_WORDS = ("auth", "credential", "forbidden", "permission", "unauthorized")


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

    async def async_call(
        self,
        method: str,
        params: object | None = None,
    ) -> Any:
        """Call one Komari JSON-RPC2 method."""
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
                try:
                    payload = await response.json(content_type=None)
                except (JSONDecodeError, ValueError) as err:
                    raise KomariProtocolError(
                        "Komari returned an invalid JSON response"
                    ) from err
        except TimeoutError as err:
            raise KomariConnectionError("Timed out while contacting Komari") from err
        except ClientError as err:
            raise KomariConnectionError("Unable to contact Komari") from err

        return self._parse_rpc_response(payload, request_id)

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
        if METHOD_PING_METRIC_STATS not in capabilities:
            raise KomariUnsupportedError("Komari does not expose Ping statistics")
        return parse_ping_metric_stats(
            await self.async_call(
                METHOD_PING_METRIC_STATS,
                {
                    "entity_ids": list(node_uuids),
                    "task_ids": list(task_ids),
                    "hours": hours,
                    "max_points": max_points,
                },
            )
        )

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
