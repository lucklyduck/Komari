"""Tests for the local dual-panel acceptance helper."""

from tests.manual.fake_komari import (
    ADDED_NODE_UUID,
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
    """The helper exposes P1 RPC methods and deterministic dynamic discovery."""
    panel = FakeKomariPanel("Panel A", node_list_as_mapping=True)
    assert panel.rpc_result("rpc.ping") == "pong"
    assert panel.rpc_result("rpc.version") == "2.0"
    assert panel.rpc_result("rpc.methods") == list(RPC_METHODS)

    panel.add_node()
    assert ADDED_NODE_UUID in panel.nodes
    assert ADDED_NODE_UUID in panel.statuses
