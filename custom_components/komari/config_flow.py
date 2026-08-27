"""Config flow for the Komari integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .api import KomariApiClient
from .const import API_PATH, CONF_API_KEY, DEFAULT_VERIFY_SSL, DOMAIN
from .exceptions import (
    KomariAuthenticationError,
    KomariConnectionError,
    KomariMethodNotFoundError,
    KomariPermissionError,
    KomariProtocolError,
    KomariRpcError,
    KomariUnsupportedError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import KomariBootstrapData

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Optional(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def normalize_panel_url(raw_url: str) -> str:
    """Return a canonical panel root URL or raise ValueError."""
    candidate = raw_url.strip()
    if not candidate or "?" in candidate or "#" in candidate:
        raise ValueError("Panel URL must not contain a query or fragment")

    try:
        parsed = URL(candidate)
        scheme = parsed.scheme.casefold()
        host = parsed.host
        port = parsed.port
    except (TypeError, ValueError) as err:
        raise ValueError("Panel URL is invalid") from err

    if scheme not in {"http", "https"} or not host:
        raise ValueError("Panel URL must use http or https and include a host")
    if parsed.user is not None or parsed.password is not None:
        raise ValueError("Panel URL must not contain user information")

    path = parsed.path.rstrip("/")
    if path == API_PATH or path.endswith(API_PATH):
        path = path[: -len(API_PATH)].rstrip("/")

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    try:
        normalized = URL.build(scheme=scheme, host=host, port=port, path=path)
    except ValueError as err:
        raise ValueError("Panel URL is invalid") from err
    return str(normalized)


def _normalized_entry_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize user input for stable duplicate detection and storage."""
    data: dict[str, Any] = {
        CONF_URL: normalize_panel_url(user_input[CONF_URL]),
        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
    }
    api_key = str(user_input.get(CONF_API_KEY, "")).strip()
    if api_key:
        data[CONF_API_KEY] = api_key
    return data


class KomariConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Komari configuration and API-key reauthentication."""

    VERSION = 1
    MINOR_VERSION = 1

    async def _async_validate(self, data: Mapping[str, Any]) -> KomariBootstrapData:
        """Validate one panel without retaining its client session wrapper."""
        session = async_get_clientsession(
            self.hass,
            verify_ssl=bool(data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        )
        client = KomariApiClient(
            session,
            str(data[CONF_URL]),
            data.get(CONF_API_KEY),
        )
        bootstrap = await client.async_get_bootstrap()
        if bootstrap.panel.private and not client.authenticated:
            raise KomariAuthenticationError("Komari requires an API key for this panel")
        return bootstrap

    async def _async_validate_with_errors(
        self, data: Mapping[str, Any]
    ) -> tuple[KomariBootstrapData | None, dict[str, str]]:
        """Validate input and map sanitized client failures to form errors."""
        try:
            return await self._async_validate(data), {}
        except KomariAuthenticationError, KomariPermissionError:
            return None, {"base": "invalid_auth"}
        except KomariConnectionError:
            return None, {"base": "cannot_connect"}
        except KomariUnsupportedError, KomariMethodNotFoundError:
            return None, {"base": "unsupported_panel"}
        except KomariProtocolError, KomariRpcError:
            return None, {"base": "invalid_response"}
        except Exception:
            _LOGGER.exception("Unexpected exception while validating Komari")
            return None, {"base": "unknown"}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure one Komari panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = _normalized_entry_data(user_input)
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_url"
            else:
                self._async_abort_entries_match({CONF_URL: data[CONF_URL]})
                bootstrap, errors = await self._async_validate_with_errors(data)
                if bootstrap is not None:
                    host = URL(data[CONF_URL]).host or data[CONF_URL]
                    return self.async_create_entry(
                        title=bootstrap.panel.name or host,
                        data=data,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start API-key reauthentication for the current entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and replace only the current entry's API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = str(user_input.get(CONF_API_KEY, "")).strip()
            if not api_key:
                errors["base"] = "invalid_auth"
            else:
                candidate = dict(entry.data)
                candidate[CONF_API_KEY] = api_key
                bootstrap, errors = await self._async_validate_with_errors(candidate)
                if bootstrap is not None:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_API_KEY: api_key},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
