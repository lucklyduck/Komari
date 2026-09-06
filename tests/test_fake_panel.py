"""Tests for the local three-panel acceptance helper."""

from typing import Any, cast

from tests.manual.fake_komari import (
    ADDED_NODE_UUID,
    ADDED_TASK_ID,
    FAKE_API_KEYS,
    METRIC_DEFINITIONS,
    METRIC_HIGH_CARDINALITY_SERIES,
    METRIC_METHODS,
    PING_METHODS,
    RPC_METHODS,
    SHARED_NODE_UUID,
    FakeKomariPanel,
)


def test_fake_panels_share_uuid_but_not_state() -> None:
    """Two panels expose the same UUID through both supported list shapes."""
    panel_a = FakeKomariPanel("Panel A", node_list_as_mapping=True)
    panel_b = FakeKomariPanel("Panel B", node_list_as_mapping=False)

    nodes_a = panel_a.rpc_result("public:getNodesInformation")
    nodes_b = panel_b.rpc_result("public:getNodesInformation")
    assert isinstance(nodes_a, dict)
    assert isinstance(nodes_b, list)
    assert SHARED_NODE_UUID in nodes_a
    assert nodes_b[0]["uuid"] == SHARED_NODE_UUID

    panel_a.statuses[SHARED_NODE_UUID]["online"] = False
    assert panel_b.statuses[SHARED_NODE_UUID]["online"] is True


def test_fake_panel_capabilities_and_controls() -> None:
    """The helper exposes P2 RPC methods and deterministic dynamic discovery."""
    panel = FakeKomariPanel("Panel A", node_list_as_mapping=True)
    assert panel.rpc_result("rpc.ping") == "pong"
    assert panel.rpc_result("rpc.version") == "2.0"
    assert panel.rpc_result("rpc.methods") == list(RPC_METHODS)

    panel.add_node()
    assert ADDED_NODE_UUID in panel.nodes
    assert ADDED_NODE_UUID in panel.statuses

    panel.rename_shared_node()
    assert panel.nodes[SHARED_NODE_UUID]["uuid"] == SHARED_NODE_UUID
    assert "renamed" in panel.nodes[SHARED_NODE_UUID]["name"]
    panel.delete_added_node()
    assert ADDED_NODE_UUID not in panel.nodes


def test_fake_panel_ping_tasks_stats_and_lifecycle_controls() -> None:
    """Task identity, missing fields, records, and controls are deterministic."""
    panel = FakeKomariPanel(
        "Panel B",
        node_list_as_mapping=False,
        task_kind="tcp",
        task_count=3,
    )
    tasks = cast(
        "list[dict[str, Any]]",
        panel.rpc_result("public:getPublicPingTasks"),
    )
    assert len(tasks) == 3
    assert all(task["type"] == "tcp" for task in tasks)

    stats_payload = cast(
        "dict[str, Any]",
        panel.rpc_result(
            "public:getPingMetricStats",
            {"entity_ids": [SHARED_NODE_UUID], "task_ids": ["101"]},
        ),
    )
    assert len(stats_payload["stats"]) == 1
    assert "min" not in stats_payload["stats"][0]
    records_payload = cast("dict[str, Any]", panel.rpc_result("common:getRecords"))
    assert records_payload["records"]

    first_task_id = str(tasks[0]["id"])
    panel.rename_first_task()
    renamed = cast(
        "list[dict[str, Any]]",
        panel.rpc_result("public:getPublicPingTasks"),
    )
    assert str(renamed[0]["id"]) == first_task_id
    assert "renamed" in renamed[0]["name"]
    panel.add_task()
    assert ADDED_TASK_ID in panel.tasks
    panel.remove_added_task()
    assert ADDED_TASK_ID not in panel.tasks

    panel.ping_failing = True
    assert PING_METHODS
    panel.method_not_found.add("public:getPingMetricStats")
    assert "public:getPingMetricStats" in panel.method_not_found


def test_fake_panel_metrics_cover_empty_and_high_cardinality_responses() -> None:
    """The fake panel provides deterministic safe and hostile Metric payloads."""
    panel = FakeKomariPanel("Panel A", node_list_as_mapping=True)
    assert set(RPC_METHODS) >= METRIC_METHODS
    definitions = cast(
        "list[dict[str, Any]]",
        panel.rpc_result("public:listMetricDefinitions"),
    )
    assert definitions == [dict(definition) for definition in METRIC_DEFINITIONS]

    payload = cast(
        "dict[str, list[dict[str, Any]]]",
        panel.rpc_result(
            "public:queryMetrics",
            {
                "metric_keys": ["custom.rate", "custom.empty"],
                "entity_ids": [SHARED_NODE_UUID],
                "tags": {},
            },
        ),
    )
    assert {series["metric_key"] for series in payload["series"]} == {
        "custom.rate",
        "custom.empty",
    }
    assert any(
        point["value"] is None
        for series in payload["series"]
        for point in series["points"]
    )

    panel.metric_malformed = True
    assert panel.rpc_result("public:queryMetrics") == {"series": "malformed"}
    panel.metric_malformed = False
    panel.metric_high_cardinality = True
    high_cardinality = cast(
        "dict[str, list[dict[str, Any]]]",
        panel.rpc_result(
            "public:queryMetrics",
            {
                "metric_keys": ["custom.rate"],
                "entity_ids": [SHARED_NODE_UUID],
                "tags": {},
            },
        ),
    )
    assert len(high_cardinality["series"]) == METRIC_HIGH_CARDINALITY_SERIES


def test_fake_private_panel_key_rotation_is_deterministic() -> None:
    """Private Fake C rejects anonymous/old keys and accepts the replacement."""
    panel = FakeKomariPanel(
        "Panel C",
        node_list_as_mapping=True,
        private_site=True,
    )
    assert panel.is_authorized(None) is False
    assert panel.is_authorized(f"Bearer {FAKE_API_KEYS[0]}") is True
    panel.rotate_key()
    assert panel.is_authorized(f"Bearer {FAKE_API_KEYS[0]}") is False
    assert panel.is_authorized(f"Bearer {FAKE_API_KEYS[1]}") is True


def test_fake_optional_hardware_and_traffic_shapes_are_explicit() -> None:
    """Only the opted-in fake node advertises GPU and temperature support."""
    without_optional = FakeKomariPanel("Panel B", node_list_as_mapping=False)
    with_optional = FakeKomariPanel(
        "Panel A",
        node_list_as_mapping=True,
        optional_hardware=True,
    )
    node_without = without_optional.nodes[SHARED_NODE_UUID]
    status_without = without_optional.statuses[SHARED_NODE_UUID]
    node_with = with_optional.nodes[SHARED_NODE_UUID]
    status_with = with_optional.statuses[SHARED_NODE_UUID]

    assert "gpu_name" not in node_without
    assert "temp" not in status_without
    assert node_without["traffic_limit_type"] == "max"
    assert node_with["gpu_name"] == "Fake GPU"
    assert status_with["temp"] == 42.5
    assert node_with["traffic_limit_type"] == "sum"
