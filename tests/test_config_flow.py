"""Tests for the Komari config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.komari.config_flow import normalize_panel_url
from custom_components.komari.const import CONF_API_KEY, DOMAIN
from custom_components.komari.exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)
from custom_components.komari.models import KomariBootstrapData, KomariPanelInfo
from homeassistant import config_entries
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _bootstrap(
    *, name: str = "Komari Lab", private: bool | None = False
) -> KomariBootstrapData:
    """Build sanitized bootstrap data for flow tests."""
    return KomariBootstrapData(
        panel=KomariPanelInfo(
            name=name,
            version="1.2.3",
            build_hash="sanitized-build",
            rpc_version="2.0",
            private=private,
        ),
        nodes={},
        statuses={},
        capabilities=frozenset(),
    )


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (" HTTPS://Example.COM:443/api/rpc2/ ", "https://example.com"),
        ("http://Example.COM:80/panel/", "http://example.com/panel"),
        ("https://example.com:8443/panel/api/rpc2", "https://example.com:8443/panel"),
        ("http://[2001:db8::1]:8080/", "http://[2001:db8::1]:8080"),
    ],
)
def test_normalize_panel_url(raw_url: str, expected: str) -> None:
    """Panel URLs have one stable representation."""
    assert normalize_panel_url(raw_url) == expected


@pytest.mark.parametrize(
    "raw_url",
    [
        "",
        "example.com",
        "ftp://example.com",
        "https://user:password@example.com",
        "https://example.com?token=secret",
        "https://example.com/#section",
        "https://example.com:not-a-port",
    ],
)
def test_normalize_panel_url_rejects_unsafe_values(raw_url: str) -> None:
    """Credentials and unstable URL components are rejected."""
    with pytest.raises(ValueError, match="Panel URL"):
        normalize_panel_url(raw_url)


async def test_user_flow_creates_public_entry(hass: HomeAssistant) -> None:
    """A public panel can be configured without an API key."""
    bootstrap = _bootstrap()
    with (
        patch(
            "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
            new=AsyncMock(return_value=bootstrap),
        ),
        patch(
            "custom_components.komari.config_flow.async_get_clientsession"
        ) as get_session,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_URL: "https://EXAMPLE.com:443/api/rpc2/",
                CONF_API_KEY: "",
                CONF_VERIFY_SSL: False,
            },
        )

    assert result.get("type") is FlowResultType.CREATE_ENTRY
    assert result.get("title") == "Komari Lab"
    assert result.get("data") == {
        CONF_URL: "https://example.com",
        CONF_VERIFY_SSL: False,
    }
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id is None
    get_session.assert_called_once_with(hass, verify_ssl=False)


async def test_user_flow_uses_host_as_title_and_strips_key(
    hass: HomeAssistant,
) -> None:
    """The host is the fallback title and API keys are stored trimmed."""
    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=_bootstrap(name="")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_URL: "http://panel.example.test:8080/",
                CONF_API_KEY: "  sanitized-key  ",
                CONF_VERIFY_SSL: True,
            },
        )

    assert result.get("type") is FlowResultType.CREATE_ENTRY
    assert result.get("title") == "panel.example.test"
    assert result.get("data") == {
        CONF_URL: "http://panel.example.test:8080",
        CONF_VERIFY_SSL: True,
        CONF_API_KEY: "sanitized-key",
    }


async def test_user_flow_rejects_duplicate_normalized_url(
    hass: HomeAssistant,
) -> None:
    """One panel URL cannot be added twice to the same HA instance."""
    MockConfigEntry(
        domain=DOMAIN,
        title="Existing panel",
        data={CONF_URL: "https://example.com", CONF_VERIFY_SSL: True},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_URL: "https://EXAMPLE.com:443/api/rpc2/",
            CONF_VERIFY_SSL: True,
        },
    )

    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "already_configured"


async def test_user_flow_rejects_private_panel_without_key(
    hass: HomeAssistant,
) -> None:
    """A panel marked private requires an API key."""
    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=_bootstrap(private=True)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_URL: "https://private.example.test"},
        )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    ("error", "flow_error"),
    [
        (KomariConnectionError("offline"), "cannot_connect"),
        (KomariAuthenticationError("denied"), "invalid_auth"),
        (KomariPermissionError(-1, "denied"), "invalid_auth"),
        (KomariUnsupportedError("old panel"), "unsupported_panel"),
        (KomariProtocolError("bad payload"), "invalid_response"),
        (KomariRpcError(-1, "failed"), "invalid_response"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_user_flow_maps_sanitized_errors(
    hass: HomeAssistant,
    error: Exception,
    flow_error: str,
) -> None:
    """Client failures become stable config-flow errors."""
    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_URL: "https://example.test"},
        )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": flow_error}


async def test_user_flow_rejects_invalid_url_before_network(
    hass: HomeAssistant,
) -> None:
    """Unsafe URLs never reach the RPC client."""
    bootstrap = AsyncMock(return_value=_bootstrap())
    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=bootstrap,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_URL: "https://user:secret@example.test"},
        )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": "invalid_url"}
    bootstrap.assert_not_awaited()


async def test_reauth_updates_only_current_api_key(hass: HomeAssistant) -> None:
    """Reauthentication preserves URL, TLS setting, title, and identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Primary panel",
        data={
            CONF_URL: "https://example.test",
            CONF_VERIFY_SSL: False,
            CONF_API_KEY: "old-key",
        },
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id
    original_data = dict(entry.data)

    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(return_value=_bootstrap()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "reauth_confirm"
        flow_id = result.get("flow_id")
        assert flow_id is not None

        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: "  replacement-key  "}
        )

    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "reauth_successful"
    assert entry.entry_id == original_entry_id
    assert entry.title == "Primary panel"
    assert entry.data == {
        **original_data,
        CONF_API_KEY: "replacement-key",
    }


async def test_reauth_keeps_old_key_on_failure(hass: HomeAssistant) -> None:
    """A rejected replacement key does not alter the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_API_KEY: "old-key",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.komari.config_flow.KomariApiClient.async_get_bootstrap",
        new=AsyncMock(side_effect=KomariAuthenticationError("denied")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        flow_id = result.get("flow_id")
        assert flow_id is not None
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_API_KEY: "bad-key"}
        )

    assert result.get("type") is FlowResultType.FORM
    assert result.get("errors") == {"base": "invalid_auth"}
    assert entry.data[CONF_API_KEY] == "old-key"
