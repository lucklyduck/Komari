"""Tests for the Komari RPC2 client and response models."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

import pytest
from aiohttp import ClientConnectionError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aiohttp import ClientSession

from custom_components.komari.api import KomariApiClient
from custom_components.komari.const import (
    DYNAMIC_METRIC_METHODS,
    MAX_METRIC_RESPONSE_BYTES,
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
)
from custom_components.komari.exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)
from custom_components.komari.models import (
    KomariMetricSeriesKey,
    TrafficLimitType,
    parse_datetime,
    parse_metric_definitions,
    parse_metric_values,
    parse_nodes,
    parse_ping_metric_stats,
    parse_ping_records_stats,
    parse_ping_tasks,
    parse_statuses,
)

FIXTURES = Path(__file__).parent / "fixtures"
NODE_UUID = "00000000-0000-0000-0000-000000000001"


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload
        encoded = (
            json.dumps(payload).encode("utf-8")
            if not isinstance(payload, Exception)
            else b""
        )
        self.content = _FakeContent(encoded)
        self.content_length: int | None = len(encoded)

    async def __aenter__(self) -> Self:
        """Enter the response context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the response context."""

    async def json(self, *, content_type: None = None) -> object:
        """Return the configured JSON payload."""
        del content_type
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeContent:
    """Byte-stream stand-in used by the bounded Metric response path."""

    def __init__(self, payload: bytes) -> None:
        """Store a response body that can be read in bounded chunks."""
        self._payload = payload
        self._offset = 0

    async def read(self, size: int) -> bytes:
        """Return the next response chunk."""
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class FakeSession:
    """Queue-backed session for API unit tests."""

    def __init__(
        self,
        responses: list[FakeResponse | Exception],
        *,
        rpc_results: Mapping[str, object] | None = None,
    ) -> None:
        self.responses = deque(responses)
        self.rpc_results = rpc_results
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a request and return the next response."""
        self.requests.append({"url": url, **kwargs})
        if self.rpc_results is not None:
            request = kwargs["json"]
            return rpc_result(
                request["id"],
                self.rpc_results[request["method"]],
            )
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def rpc_result(request_id: int, result: object) -> FakeResponse:
    """Build one successful JSON-RPC response."""
    return FakeResponse(
        200,
        {"jsonrpc": "2.0", "id": request_id, "result": result},
    )


def load_fixture(name: str) -> object:
    """Load one sanitized Komari fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(session: FakeSession, api_key: str | None = None) -> KomariApiClient:
    """Create a client around a test session."""
    return KomariApiClient(
        cast("ClientSession", session),
        "https://panel.example",
        api_key,
    )


def test_client_properties_normalize_trailing_slash() -> None:
    """Client properties expose normalized, non-secret connection metadata."""
    client = KomariApiClient(
        cast("ClientSession", FakeSession([])),
        "https://panel.example/",
        " secret-key ",
    )
    assert client.base_url == "https://panel.example"
    assert client.endpoint == "https://panel.example/api/rpc2"
    assert client.authenticated is True


@pytest.mark.asyncio
async def test_rpc_call_uses_bearer_auth_without_leaking_key() -> None:
    """The API key is sent only in the Authorization header."""
    session = FakeSession([rpc_result(1, "pong")])
    client = make_client(session, "secret-key")

    await client.async_ping()

    request = session.requests[0]
    assert request["url"] == "https://panel.example/api/rpc2"
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in request["url"]
    assert request["json"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "rpc.ping",
    }
    assert request["allow_redirects"] is False


@pytest.mark.asyncio
async def test_anonymous_call_omits_authorization() -> None:
    """Anonymous RPC calls do not send an Authorization header."""
    session = FakeSession([rpc_result(1, "pong")])
    await make_client(session).async_ping()
    assert "Authorization" not in session.requests[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_http_authentication_error(status: int) -> None:
    """Authentication HTTP responses map to a safe auth exception."""
    client = make_client(FakeSession([FakeResponse(status, {})]), "secret-key")
    with pytest.raises(KomariAuthenticationError, match="credentials") as error:
        await client.async_ping()
    assert "secret-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 500])
async def test_http_connection_errors(status: int) -> None:
    """Redirects and server failures never expose response bodies."""
    client = make_client(FakeSession([FakeResponse(status, {"secret": "body"})]))
    with pytest.raises(KomariConnectionError) as error:
        await client.async_ping()
    assert "body" not in str(error.value)


@pytest.mark.asyncio
async def test_client_transport_error() -> None:
    """aiohttp failures map to a connection error."""
    client = make_client(FakeSession([ClientConnectionError("private host")]))
    with pytest.raises(KomariConnectionError, match="Unable to contact") as error:
        await client.async_ping()
    assert "private host" not in str(error.value)


@pytest.mark.asyncio
async def test_client_timeout() -> None:
    """Timeouts map to a connection error."""
    client = make_client(FakeSession([TimeoutError()]))
    with pytest.raises(KomariConnectionError, match="Timed out"):
        await client.async_ping()


@pytest.mark.asyncio
async def test_invalid_json_and_response_id() -> None:
    """Invalid JSON and mismatched RPC ids are rejected."""
    invalid_json = make_client(FakeSession([FakeResponse(200, ValueError("raw"))]))
    with pytest.raises(KomariProtocolError, match="invalid JSON"):
        await invalid_json.async_ping()

    wrong_id = make_client(
        FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 99, "result": "pong"})])
    )
    with pytest.raises(KomariProtocolError, match="id"):
        await wrong_id.async_ping()


@pytest.mark.asyncio
async def test_rpc_error_mapping() -> None:
    """Method and permission errors retain only safe RPC metadata."""
    missing = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32601, "message": "Method not found"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariMethodNotFoundError):
        await missing.async_ping()

    denied = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32000, "message": "Permission denied"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariPermissionError):
        await denied.async_ping()

    generic = make_client(
        FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32001, "message": "Temporary failure"},
                    },
                )
            ]
        )
    )
    with pytest.raises(KomariRpcError, match="RPC request failed") as error:
        await generic.async_ping()
    assert "Temporary failure" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"jsonrpc": "1.0", "id": 1, "result": "pong"},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": "pong", "error": {}},
        {"jsonrpc": "2.0", "id": 1, "error": "invalid"},
    ],
)
async def test_invalid_rpc_envelopes(payload: object) -> None:
    """Malformed JSON-RPC envelopes are rejected."""
    with pytest.raises(KomariProtocolError):
        await make_client(FakeSession([FakeResponse(200, payload)])).async_ping()


@pytest.mark.asyncio
async def test_invalid_ping_version_and_methods_results() -> None:
    """Built-in RPC method result types are validated."""
    with pytest.raises(KomariProtocolError, match="pong"):
        await make_client(FakeSession([rpc_result(1, "not-pong")])).async_ping()

    version_client = make_client(FakeSession([rpc_result(1, 2)]))
    with pytest.raises(KomariProtocolError, match="version"):
        await version_client.async_get_rpc_version()

    methods_client = make_client(FakeSession([rpc_result(1, ["rpc.ping", 7])]))
    with pytest.raises(KomariProtocolError, match="methods"):
        await methods_client.async_get_methods()


@pytest.mark.asyncio
async def test_bootstrap_selects_public_methods() -> None:
    """Anonymous bootstrap uses public metadata and node methods."""
    methods = [
        METHOD_PUBLIC_SETTINGS,
        METHOD_PUBLIC_VERSION,
        METHOD_PUBLIC_NODES,
        METHOD_LATEST_STATUS,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.ping": "pong",
            "rpc.version": "2.0",
            "rpc.methods": methods,
            METHOD_PUBLIC_SETTINGS: {"sitename": "Example", "private_site": False},
            METHOD_PUBLIC_VERSION: {"version": "1.4.3", "hash": "abcdef"},
            METHOD_PUBLIC_NODES: load_fixture("nodes_array.json"),
            METHOD_LATEST_STATUS: load_fixture("latest_status.json"),
        },
    )

    bootstrap = await make_client(session).async_get_bootstrap()

    assert bootstrap.panel.name == "Example"
    assert bootstrap.panel.version == "1.4.3"
    assert bootstrap.panel.rpc_version == "2.0"
    assert bootstrap.nodes[NODE_UUID].name == "Example node"
    assert bootstrap.statuses[NODE_UUID].online is True
    requested_methods = {request["json"]["method"] for request in session.requests}
    assert METHOD_PUBLIC_NODES in requested_methods


@pytest.mark.asyncio
async def test_authenticated_bootstrap_uses_common_fallbacks() -> None:
    """Authenticated bootstrap prefers complete nodes and valid fallback metadata."""
    methods = [
        METHOD_COMMON_PUBLIC_INFO,
        METHOD_COMMON_VERSION,
        METHOD_COMMON_NODES,
        METHOD_LATEST_STATUS,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.ping": "pong",
            "rpc.version": None,
            "rpc.methods": methods,
            METHOD_COMMON_PUBLIC_INFO: {"sitename": "Private", "private_site": True},
            METHOD_COMMON_VERSION: {"version": "1.4.3", "hash": "abcdef"},
            METHOD_COMMON_NODES: load_fixture("nodes_map.json"),
            METHOD_LATEST_STATUS: load_fixture("latest_status.json"),
        },
    )

    bootstrap = await make_client(session, "key").async_get_bootstrap()

    assert bootstrap.panel.private is True
    assert bootstrap.panel.rpc_version is None
    requested_methods = {request["json"]["method"] for request in session.requests}
    assert METHOD_COMMON_NODES in requested_methods


@pytest.mark.asyncio
async def test_node_list_capability_is_required() -> None:
    """A panel without any documented node-list method is unsupported."""
    capabilities = frozenset({METHOD_LATEST_STATUS})
    with pytest.raises(KomariUnsupportedError, match="node-list"):
        await make_client(FakeSession([])).async_get_nodes(capabilities)


@pytest.mark.asyncio
async def test_bootstrap_rejects_missing_required_method() -> None:
    """A panel without latest status support is not accepted."""
    session = FakeSession(
        [
            rpc_result(1, "pong"),
            rpc_result(2, "2.0"),
            rpc_result(3, [METHOD_PUBLIC_SETTINGS, METHOD_PUBLIC_VERSION]),
        ]
    )
    with pytest.raises(KomariUnsupportedError, match=METHOD_LATEST_STATUS):
        await make_client(session).async_get_bootstrap()


def test_node_mapping_and_array_normalize_identically() -> None:
    """Current mapping and older array shapes produce the same model."""
    mapped = parse_nodes(load_fixture("nodes_map.json"))
    array = parse_nodes(load_fixture("nodes_array.json"))
    assert mapped == array
    node = mapped[NODE_UUID]
    assert node.memory_total == 8 * 1024**3
    assert node.traffic_limit_type is TrafficLimitType.SUM
    assert node.expired_at == datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_status_values_and_zero_are_preserved() -> None:
    """Valid zero values remain available in the normalized model."""
    status = parse_statuses(load_fixture("latest_status.json"))[NODE_UUID]
    assert status.gpu_usage == 0
    assert status.swap_used == 0
    assert status.memory_used == 4 * 1024**3
    assert status.network_in_rate == 1024
    assert status.observed_at == datetime(2026, 8, 26, tzinfo=UTC)


@pytest.mark.parametrize(
    "gpu_name",
    [None, "", "none", "null", "N/A", "n-a", "unknown", "-"],
)
def test_gpu_absence_sentinels_are_normalized(gpu_name: object) -> None:
    """Common Komari no-GPU names do not become capabilities."""
    node = parse_nodes([{"uuid": NODE_UUID, "gpu_name": gpu_name}])[NODE_UUID]
    assert node.gpu_name is None


def test_meaningful_gpu_name_is_preserved() -> None:
    """Real GPU model text remains available for capability discovery."""
    node = parse_nodes([{"uuid": NODE_UUID, "gpu_name": " Example GPU "}])[NODE_UUID]
    assert node.gpu_name == "Example GPU"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-26T00:00:00Z", datetime(2026, 8, 26, tzinfo=UTC)),
        (1_777_593_600, datetime(2026, 5, 1, tzinfo=UTC)),
        (1_777_593_600_000, datetime(2026, 5, 1, tzinfo=UTC)),
        ("2026-08-26T00:00:00", None),
        ("not-a-date", None),
        (None, None),
    ],
)
def test_datetime_parsing(value: object, expected: datetime | None) -> None:
    """Only explicit aware or supported epoch timestamps are accepted."""
    assert parse_datetime(value) == expected


@pytest.mark.parametrize(
    "payload",
    [None, "nodes", ["not-an-object"], {"": {"name": "missing uuid"}}],
)
def test_invalid_node_shapes(payload: object) -> None:
    """Malformed node payloads fail explicitly."""
    with pytest.raises(KomariProtocolError):
        parse_nodes(payload)


def test_invalid_status_shape_and_values() -> None:
    """Malformed status identity/state fails and invalid metrics become None."""
    with pytest.raises(KomariProtocolError):
        parse_statuses({NODE_UUID: {"client": NODE_UUID}})

    status_payload: dict[str, object] = {
        NODE_UUID: {
            "client": NODE_UUID,
            "online": True,
            "net_in": -1,
            "cpu": float("nan"),
        }
    }
    status = parse_statuses(status_payload)[NODE_UUID]
    assert status.network_in_rate is None
    assert status.cpu_usage is None


def test_ping_task_and_stats_fixtures_are_normalized() -> None:
    """Current task IDs and per-field missing stats normalize deterministically."""
    tasks = parse_ping_tasks(load_fixture("ping_tasks.json"))
    assert tuple(tasks) == ("101", "tcp-202")
    assert tasks["101"].kind == "http"
    assert tasks["101"].clients == {
        "fixture-node-sum",
        "hidden-fixture-node",
    }

    stats = parse_ping_metric_stats(load_fixture("ping_stats.json"))
    first = next(
        value
        for key, value in stats.items()
        if key.node_uuid == "fixture-node-sum" and key.task_id == "101"
    )
    second = next(
        value
        for key, value in stats.items()
        if key.node_uuid == "fixture-node-max" and key.task_id == "tcp-202"
    )
    assert first.latest == 25
    assert first.minimum == 10
    assert first.p99 == 88
    assert second.minimum is None
    assert second.total == 120


def test_ping_records_fallback_uses_locked_statistics_algorithms() -> None:
    """Records fallback computes loss, nearest-rank percentiles, and population SD."""
    stats = parse_ping_records_stats(
        load_fixture("ping_records.json"),
        node_uuids=("fixture-node-sum", "fixture-node-max"),
        task_ids=("101", "tcp-202"),
        max_points=120,
    )

    first = stats[next(key for key in stats if key.task_id == "101")]
    second = stats[next(key for key in stats if key.task_id == "tcp-202")]
    assert first.latest == 30
    assert first.loss == pytest.approx(100 / 3)
    assert first.minimum == 10
    assert first.maximum == 30
    assert first.average == 20
    assert first.p50 == 10
    assert first.p99 == 30
    assert first.stddev == 10
    assert first.total == 3
    assert first.valid == 2
    assert second.latest == 60
    assert second.loss == 0
    assert second.p50 == 40
    assert second.p99 == 60
    assert second.stddev == pytest.approx(16.3299316)


def test_ping_records_fallback_filters_and_caps_each_series() -> None:
    """Only selected visible series survive and each retains its latest point window."""
    payload = load_fixture("ping_records.json")
    assert isinstance(payload, dict)
    payload["records"].append(
        {
            "task_id": 101,
            "time": "2030-01-01T00:02:00Z",
            "value": -1,
            "client": "fixture-node-sum",
        }
    )
    payload["records"].append(
        {
            "task_id": 101,
            "time": "2030-01-01T00:03:00Z",
            "value": 999,
            "client": "hidden-fixture-node",
        }
    )

    stats = parse_ping_records_stats(
        payload,
        node_uuids=("fixture-node-sum",),
        task_ids=("101",),
        max_points=2,
    )

    assert len(stats) == 1
    result = next(iter(stats.values()))
    assert result.total == 2
    assert result.valid == 1
    assert result.latest == 30
    assert result.loss == 50


def test_ping_records_with_only_failures_keep_loss_but_no_latency() -> None:
    """A real series with no valid samples retains loss and sample counts."""
    stats = parse_ping_records_stats(
        {
            "records": [
                {
                    "task_id": 1,
                    "time": "2030-01-01T00:00:00Z",
                    "value": -1,
                    "client": "visible-node",
                },
                {
                    "task_id": 1,
                    "time": "2030-01-01T00:01:00Z",
                    "value": -2,
                    "client": "visible-node",
                },
            ]
        },
        node_uuids=("visible-node",),
        task_ids=("1",),
        max_points=120,
    )
    result = next(iter(stats.values()))

    assert result.loss == 100
    assert result.total == 2
    assert result.valid == 0
    assert result.latest is None
    assert result.minimum is None
    assert result.p50 is None
    assert result.stddev is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"records": ["bad"]},
        {"records": [{"client": "node", "task_id": 1, "value": 10}]},
        {
            "records": [
                {
                    "client": "node",
                    "task_id": 1,
                    "value": float("nan"),
                    "time": "2030-01-01T00:00:00Z",
                }
            ]
        },
    ],
)
def test_invalid_ping_record_shapes_fail_explicitly(payload: object) -> None:
    """Malformed fallback records never silently create partial series."""
    with pytest.raises(KomariProtocolError):
        parse_ping_records_stats(
            payload,
            node_uuids=("node",),
            task_ids=("1",),
            max_points=120,
        )


def test_ping_records_reject_non_positive_point_limit() -> None:
    """The records adapter enforces the fixed positive point budget."""
    with pytest.raises(KomariProtocolError, match="max_points"):
        parse_ping_records_stats(
            {"records": []},
            node_uuids=("node",),
            task_ids=("1",),
            max_points=0,
        )


@pytest.mark.parametrize(
    "payload",
    [None, {}, ["task"], [{"name": "missing id"}], [{"id": True}]],
)
def test_invalid_ping_task_shapes_fail_explicitly(payload: object) -> None:
    """Malformed Ping task structures never create unstable identities."""
    with pytest.raises(KomariProtocolError):
        parse_ping_tasks(payload)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"stats": ["bad"]},
        {"stats": [{"task_id": 1}]},
        {"stats": [{"entity_id": "node", "task_id": True}]},
    ],
)
def test_invalid_ping_stats_shapes_fail_explicitly(payload: object) -> None:
    """Malformed Ping series data is isolated as a protocol failure."""
    with pytest.raises(KomariProtocolError):
        parse_ping_metric_stats(payload)


async def test_client_fetches_ping_tasks_and_one_batch_of_stats() -> None:
    """The typed client uses exactly one request for all node/task combinations."""
    task_payload = load_fixture("ping_tasks.json")
    stats_payload = load_fixture("ping_stats.json")
    session = FakeSession(
        [],
        rpc_results={
            METHOD_PUBLIC_PING_TASKS: task_payload,
            METHOD_PING_METRIC_STATS: stats_payload,
        },
    )
    client = make_client(session)
    capabilities = frozenset({METHOD_PUBLIC_PING_TASKS, METHOD_PING_METRIC_STATS})

    tasks = await client.async_get_ping_tasks(capabilities)
    stats = await client.async_get_ping_stats(
        capabilities,
        ("fixture-node-sum", "fixture-node-max"),
        ("101", "tcp-202"),
        hours=1,
        max_points=120,
    )

    assert len(tasks) == 2
    assert len(stats) == 2
    assert len(session.requests) == 2
    assert session.requests[1]["json"]["params"] == {
        "entity_ids": ["fixture-node-sum", "fixture-node-max"],
        "task_ids": ["101", "tcp-202"],
        "hours": 1,
        "max_points": 120,
    }


async def test_client_filters_unrequested_direct_ping_series() -> None:
    """Unexpected hidden or unselected direct results never enter the snapshot."""
    session = FakeSession(
        [],
        rpc_results={METHOD_PING_METRIC_STATS: load_fixture("ping_stats.json")},
    )
    stats = await make_client(session).async_get_ping_stats(
        frozenset({METHOD_PING_METRIC_STATS}),
        ("fixture-node-sum",),
        ("101",),
        hours=1,
        max_points=120,
    )

    assert len(stats) == 1
    assert next(iter(stats)).node_uuid == "fixture-node-sum"
    assert next(iter(stats)).task_id == "101"


async def test_client_uses_one_public_records_request_for_one_task() -> None:
    """One selected task uses one task-centric public records request."""
    session = FakeSession(
        [],
        rpc_results={METHOD_PUBLIC_PING_RECORDS: load_fixture("ping_records.json")},
    )
    stats = await make_client(session).async_get_ping_stats(
        frozenset({METHOD_PUBLIC_PING_RECORDS}),
        ("fixture-node-sum", "fixture-node-max"),
        ("101",),
        hours=1,
        max_points=120,
    )

    assert len(stats) == 1
    assert len(session.requests) == 1
    assert session.requests[0]["json"]["params"] == {
        "hours": "1",
        "task_id": "101",
    }


async def test_client_uses_one_public_records_request_for_one_node() -> None:
    """One visible node uses one node-centric request for multiple tasks."""
    session = FakeSession(
        [],
        rpc_results={METHOD_PUBLIC_PING_RECORDS: load_fixture("ping_records.json")},
    )
    stats = await make_client(session).async_get_ping_stats(
        frozenset({METHOD_PUBLIC_PING_RECORDS}),
        ("fixture-node-max",),
        ("101", "tcp-202"),
        hours=1,
        max_points=120,
    )

    assert len(stats) == 1
    assert session.requests[0]["json"]["params"] == {
        "hours": "1",
        "uuid": "fixture-node-max",
    }


async def test_client_uses_one_common_records_request_for_full_matrix() -> None:
    """A multi-node/task fallback remains one global capability-confirmed call."""
    session = FakeSession(
        [],
        rpc_results={METHOD_COMMON_RECORDS: load_fixture("ping_records.json")},
    )
    stats = await make_client(session).async_get_ping_stats(
        frozenset({METHOD_PUBLIC_PING_RECORDS, METHOD_COMMON_RECORDS}),
        ("fixture-node-sum", "fixture-node-max"),
        ("101", "tcp-202"),
        hours=1,
        max_points=120,
    )

    assert len(stats) == 2
    assert len(session.requests) == 1
    assert session.requests[0]["json"]["method"] == METHOD_COMMON_RECORDS
    assert session.requests[0]["json"]["params"] == {
        "type": "ping",
        "hours": 1,
        "maxCount": 120,
    }


async def test_public_records_never_fans_out_for_full_matrix() -> None:
    """Public records alone is rejected when it would require N-by-M requests."""
    session = FakeSession([])
    with pytest.raises(KomariUnsupportedError, match="one-request"):
        await make_client(session).async_get_ping_stats(
            frozenset({METHOD_PUBLIC_PING_RECORDS}),
            ("node-1", "node-2"),
            ("1", "2"),
            hours=1,
            max_points=120,
        )

    assert session.requests == []


async def test_ping_methods_require_advertised_capabilities() -> None:
    """The client never guesses optional Ping method availability."""
    client = make_client(FakeSession([]))
    with pytest.raises(KomariUnsupportedError):
        await client.async_get_ping_tasks(frozenset())
    with pytest.raises(KomariUnsupportedError):
        await client.async_get_ping_stats(
            frozenset(),
            (),
            (),
            hours=1,
            max_points=120,
        )


async def test_metadata_refresh_reprobes_methods_and_panel_versions() -> None:
    """Six-hour metadata refresh is capability-driven and internally coherent."""
    capabilities = [
        METHOD_LATEST_STATUS,
        METHOD_PUBLIC_NODES,
        METHOD_PUBLIC_SETTINGS,
        METHOD_PUBLIC_VERSION,
    ]
    session = FakeSession(
        [],
        rpc_results={
            "rpc.version": "2.0",
            "rpc.methods": capabilities,
            METHOD_PUBLIC_SETTINGS: {"sitename": "Fixture panel"},
            METHOD_PUBLIC_VERSION: {"version": "2.0.0", "hash": "fixture"},
        },
    )

    panel, discovered = await make_client(session).async_get_metadata()

    assert panel.name == "Fixture panel"
    assert panel.version == "2.0.0"
    assert discovered == frozenset(capabilities)


def test_metric_models_canonicalize_supported_wire_shapes() -> None:
    """Definitions and values preserve only bounded, identity-safe fields."""
    definitions = parse_metric_definitions(
        [
            {
                "name": "custom.temperature",
                "description": {"en": "Temperature", "zh": "温度"},
                "type": "gauge",
                "unit": "°C",
                "retention_days": 7,
                "metadata": {"safe": "value", "ignored": 1},
            },
            {
                "name": "custom.counter",
                "description": "A count",
                "type": "counter",
                "retention_days": 0,
            },
        ]
    )
    assert definitions["custom.temperature"].description == {
        "en": "Temperature",
        "zh": "温度",
    }
    assert definitions["custom.temperature"].metadata == {"safe": "value"}
    assert definitions["custom.counter"].retention_days == 0

    values = parse_metric_values(
        {
            "series": [
                {
                    "metric_key": "custom.temperature",
                    "entity_id": NODE_UUID,
                    "tags": {"zone": "edge", "host": "a"},
                    "points": [
                        {
                            "timestamp": "2026-08-26T00:00:00Z",
                            "value": 20,
                            "labels": {"ignored": "yes"},
                        },
                        {
                            "time": "2026-08-26T00:01:00Z",
                            "value": 21.5,
                        },
                    ],
                },
                {
                    "metric_key": "custom.counter",
                    "entity_id": NODE_UUID,
                    "tags": {},
                    "points": [{"ts": "2026-08-26T00:02:00Z", "value": None}],
                },
            ]
        }
    )
    temperature_key = KomariMetricSeriesKey(
        "custom.temperature",
        NODE_UUID,
        (("host", "a"), ("zone", "edge")),
    )
    assert values[temperature_key].value == 21.5
    assert values[temperature_key].observed_at == datetime(
        2026,
        8,
        26,
        0,
        1,
        tzinfo=UTC,
    )
    assert values[KomariMetricSeriesKey("custom.counter", NODE_UUID, ())].value is None


def test_metric_models_reject_unbounded_or_invalid_top_level_shapes() -> None:
    """A malformed Metric envelope never publishes a partial snapshot."""
    with pytest.raises(KomariProtocolError):
        parse_metric_definitions({"definitions": []})
    with pytest.raises(KomariProtocolError):
        parse_metric_values({"series": "not-a-list"})
    with pytest.raises(KomariProtocolError):
        parse_metric_values(
            {
                "series": [
                    {
                        "metric_key": "custom.rate",
                        "entity_id": NODE_UUID,
                        "tags": {},
                        "points": [{}] * 129,
                    }
                ]
            }
        )


def test_metric_fixtures_cover_localized_descriptions_and_null_values() -> None:
    """Sanitized F16 fixtures retain only protocol shapes needed by the tests."""
    definitions = parse_metric_definitions(load_fixture("metric_definitions.json"))
    values = parse_metric_values(load_fixture("metric_query.json"))

    assert definitions["custom.rate"].description == {
        "en": "Fixture rate",
        "zh-Hans": "固定测试速率",
    }
    assert (
        values[
            KomariMetricSeriesKey(
                "custom.rate",
                NODE_UUID,
                (("region", "fixture"), ("scope", "node")),
            )
        ].value
        == 12.5
    )
    assert values[KomariMetricSeriesKey("custom.empty", NODE_UUID, ())].value is None


def test_metric_models_skip_untrusted_points_without_coercion() -> None:
    """Null is valid, while bool, non-finite, and naive points are ignored."""
    values = parse_metric_values(
        {
            "series": [
                {
                    "metric_key": "custom.rate",
                    "entity_id": NODE_UUID,
                    "tags": {},
                    "points": [
                        {"time": "2026-08-26T00:00:00", "value": 1},
                        {"time": "2026-08-26T00:01:00Z", "value": True},
                        {"time": "2026-08-26T00:02:00Z", "value": float("nan")},
                        {"time": "2026-08-26T00:03:00Z", "value": None},
                    ],
                }
            ]
        }
    )
    key = KomariMetricSeriesKey("custom.rate", NODE_UUID, ())
    assert values[key].value is None
    assert values.skipped_point_count == 3


async def test_metric_client_uses_only_bounded_documented_query_fields() -> None:
    """Metric RPC calls never fall back to records or add undeclared parameters."""
    session = FakeSession(
        [],
        rpc_results={
            METHOD_PUBLIC_METRIC_DEFINITIONS: [
                {
                    "name": "custom.rate",
                    "type": "gauge",
                    "retention_days": 1,
                }
            ],
            METHOD_PUBLIC_QUERY_METRICS: {
                "series": [
                    {
                        "metric_key": "custom.rate",
                        "entity_id": NODE_UUID,
                        "tags": {"region": "edge"},
                        "points": [{"time": "2026-08-26T00:00:00Z", "value": 12}],
                    },
                    {
                        "metric_key": "custom.rate",
                        "entity_id": "unknown-node",
                        "tags": {"region": "edge"},
                        "points": [{"time": "2026-08-26T00:00:00Z", "value": 13}],
                    },
                ]
            },
        },
    )
    client = make_client(session)
    definitions = await client.async_get_metric_definitions(DYNAMIC_METRIC_METHODS)
    values = await client.async_query_metrics(
        DYNAMIC_METRIC_METHODS,
        metric_keys=("custom.rate",),
        entity_ids=(NODE_UUID,),
        tags={"region": "edge"},
    )

    assert tuple(definitions) == ("custom.rate",)
    assert len(values) == 1
    assert client.last_metric_skipped_series_count == 1
    assert client.last_metric_skipped_point_count == 0
    assert session.requests[1]["json"] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": METHOD_PUBLIC_QUERY_METRICS,
        "params": {
            "metric_keys": ["custom.rate"],
            "entity_ids": [NODE_UUID],
            "tags": {"region": "edge"},
            "hours": 4,
            "max_points": 1,
            "aggregation": "last",
            "fill_empty": False,
        },
    }
    assert all(
        request["json"]["method"] != METHOD_COMMON_RECORDS
        for request in session.requests
    )


async def test_metric_client_enforces_capabilities_and_response_limit() -> None:
    """Optional Metrics reject partial capabilities and oversized responses."""
    with pytest.raises(KomariUnsupportedError):
        await make_client(FakeSession([])).async_get_metric_definitions(frozenset())

    response = rpc_result(1, [])
    response.content_length = MAX_METRIC_RESPONSE_BYTES + 1
    with pytest.raises(KomariProtocolError, match="size"):
        await make_client(FakeSession([response])).async_get_metric_definitions(
            DYNAMIC_METRIC_METHODS
        )


@pytest.mark.parametrize(
    "payload",
    [
        [{}] * 257,
        [None],
        [{"name": "", "type": "gauge"}],
        [{"name": "custom.rate", "type": "gauge", "retention_days": -1}],
    ],
)
def test_metric_definition_models_fail_closed_for_invalid_entries(
    payload: object,
) -> None:
    """Invalid Definition items cannot turn into a partial discovery snapshot."""
    with pytest.raises(KomariProtocolError):
        parse_metric_definitions(payload)
    with pytest.raises(KomariProtocolError, match="duplicate"):
        parse_metric_definitions(
            [
                {"name": "custom.rate", "type": "gauge"},
                {"name": "custom.rate", "type": "gauge"},
            ]
        )


def test_metric_value_models_skip_invalid_series_and_points_safely() -> None:
    """Malformed series are counted while a newer finite point wins."""
    values = parse_metric_values(
        {
            "series": [
                None,
                {
                    "metric_key": "custom.rate",
                    "entity_id": NODE_UUID,
                    "tags": {"bad": 1},
                    "points": [],
                },
                {
                    "metric_key": "custom.rate",
                    "entity_id": NODE_UUID,
                    "tags": {},
                    "points": [
                        None,
                        {"time": "not-a-time", "value": 1},
                        {"time": "2026-08-26T00:00:00Z", "value": "12"},
                        {"time": "2026-08-26T00:01:00Z", "value": 2},
                    ],
                },
            ]
        }
    )
    key = KomariMetricSeriesKey("custom.rate", NODE_UUID, ())
    assert values[key].value == 2
    assert values.skipped_series_count == 2
    assert values.skipped_point_count == 3

    with pytest.raises(KomariProtocolError, match="series limit"):
        parse_metric_values({"series": [{}] * 513})
    with pytest.raises(KomariProtocolError, match="point limit"):
        parse_metric_values(
            {
                "series": [
                    {
                        "metric_key": "custom.rate",
                        "entity_id": NODE_UUID,
                        "tags": {},
                        "points": [{}] * 129,
                    }
                ]
            }
        )


async def test_metric_client_short_circuits_empty_and_rejects_unsafe_inputs() -> None:
    """Validation stops unsafe Metric requests before they reach a panel."""
    session = FakeSession([])
    client = make_client(session)
    assert (
        await client.async_query_metrics(
            DYNAMIC_METRIC_METHODS,
            metric_keys=(),
            entity_ids=(NODE_UUID,),
            tags={},
        )
        == {}
    )
    assert client.last_metric_skipped_series_count == 0
    assert client.last_metric_skipped_point_count == 0

    invalid_inputs = (
        {"metric_keys": tuple(f"custom.{index}" for index in range(9))},
        {"entity_ids": ("",)},
        {"tags": {"x" * 65: "edge"}},
        {"tags": {"x": "y" * 4096}},
        {"tags": {str(index): "y" * 256 for index in range(16)}},
        {"aggregation": "avg"},
    )
    unsafe_query = cast("Any", client.async_query_metrics)
    for invalid in invalid_inputs:
        arguments: dict[str, object] = {
            "metric_keys": ("custom.rate",),
            "entity_ids": (NODE_UUID,),
            "tags": {},
        }
        arguments.update(invalid)
        with pytest.raises(KomariProtocolError):
            await unsafe_query(DYNAMIC_METRIC_METHODS, **arguments)
    with pytest.raises(KomariUnsupportedError):
        await client.async_query_metrics(
            frozenset(),
            metric_keys=("custom.rate",),
            entity_ids=(NODE_UUID,),
            tags={},
        )
    with pytest.raises(ValueError, match="positive"):
        await client.async_call(METHOD_PING, max_response_bytes=0)
    assert session.requests == []

    common_nodes_session = FakeSession(
        [],
        rpc_results={METHOD_COMMON_NODES: {NODE_UUID: {"uuid": NODE_UUID}}},
    )
    assert tuple(
        await make_client(common_nodes_session).async_get_nodes(
            frozenset({METHOD_COMMON_NODES})
        )
    ) == (NODE_UUID,)
    assert (
        await make_client(FakeSession([])).async_get_ping_stats(
            frozenset({METHOD_PING_METRIC_STATS}),
            (),
            (),
            hours=1,
            max_points=1,
        )
        == {}
    )
    with pytest.raises(KomariUnsupportedError):
        KomariApiClient._select_method(frozenset(), "preferred", "fallback")


async def test_metric_client_rejects_streamed_oversize_and_invalid_json() -> None:
    """The streaming size guard works even when a server omits Content-Length."""
    oversize = rpc_result(1, [])
    oversize.content_length = None
    oversize.content = _FakeContent(b"x" * (MAX_METRIC_RESPONSE_BYTES + 1))
    with pytest.raises(KomariProtocolError, match="size"):
        await make_client(FakeSession([oversize])).async_get_metric_definitions(
            DYNAMIC_METRIC_METHODS
        )

    invalid_json = rpc_result(1, [])
    invalid_json.content_length = None
    invalid_json.content = _FakeContent(b"not-json")
    with pytest.raises(KomariProtocolError, match="invalid JSON"):
        await make_client(FakeSession([invalid_json])).async_get_metric_definitions(
            DYNAMIC_METRIC_METHODS
        )
