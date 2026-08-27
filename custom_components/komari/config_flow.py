"""Config flow for the Komari integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .api import KomariApiClient
from .const import (
    API_PATH,
    CONF_API_KEY,
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_PING_ENTITIES,
    MAX_PING_INTERVAL,
    MAX_PING_TASKS,
    MAX_SCAN_INTERVAL,
    METHOD_PUBLIC_PING_TASKS,
    MIN_PING_INTERVAL,
    MIN_SCAN_INTERVAL,
    PING_STAT_KEYS,
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
from .options import KomariOptions, get_options

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

    @staticmethod
    def async_get_options_flow(
        config_entry: Any,
    ) -> KomariOptionsFlow:
        """Return the per-panel options flow."""
        return KomariOptionsFlow()

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


class KomariOptionsFlow(OptionsFlowWithReload):
    """Configure per-panel polling and optional entity families."""

    def __init__(self) -> None:
        """Initialize one options transaction."""
        self._pending: dict[str, object] = {}

    def _ensure_pending(self) -> None:
        """Seed pending values once from normalized current options."""
        if not self._pending:
            self._pending = get_options(self.config_entry).as_dict()

    @property
    def _snapshot(self) -> Any | None:
        """Return the currently loaded immutable snapshot when available."""
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        return getattr(coordinator, "data", None)

    def _has_ping(self) -> bool:
        """Return whether typed public Ping discovery is available."""
        snapshot = self._snapshot
        return bool(
            snapshot is not None
            and METHOD_PUBLIC_PING_TASKS in snapshot.capabilities
            and snapshot.ping_tasks
        )

    def _selected_values(self, key: str) -> tuple[str, ...]:
        """Return one pending multi-select value with a concrete type."""
        value = self._pending.get(key)
        if not isinstance(value, list | tuple):
            return ()
        return tuple(str(item) for item in value)

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Start with monitoring intervals."""
        return await self.async_step_monitoring(user_input)

    async def async_step_monitoring(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure core and Ping polling intervals."""
        self._ensure_pending()
        if user_input is not None:
            self._pending.update(user_input)
            return await self.async_step_entities()

        return self.async_show_form(
            step_id="monitoring",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self._pending[CONF_SCAN_INTERVAL],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                    vol.Required(
                        CONF_PING_INTERVAL,
                        default=self._pending[CONF_PING_INTERVAL],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_PING_INTERVAL,
                            max=MAX_PING_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
        )

    async def async_step_entities(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure expiry and traffic entity families."""
        if user_input is not None:
            self._pending.update(user_input)
            if self._has_ping():
                return await self.async_step_ping_tasks()
            self._pending[CONF_PING_TASK_IDS] = []
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLE_EXPIRY_ENTITIES,
                        default=self._pending[CONF_ENABLE_EXPIRY_ENTITIES],
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_ENABLE_TRAFFIC_ENTITIES,
                        default=self._pending[CONF_ENABLE_TRAFFIC_ENTITIES],
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_ping_tasks(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select up to eight stable Ping task IDs."""
        snapshot = self._snapshot
        tasks = snapshot.ping_tasks if snapshot is not None else {}
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = tuple(str(item) for item in user_input[CONF_PING_TASK_IDS])
            if len(selected) > MAX_PING_TASKS:
                errors["base"] = "too_many_ping_tasks"
            elif any(task_id not in tasks for task_id in selected):
                errors["base"] = "invalid_ping_task"
            else:
                self._pending[CONF_PING_TASK_IDS] = list(selected)
                if not selected:
                    return await self.async_step_confirm()
                return await self.async_step_ping_stats()

        task_options = [
            SelectOptionDict(value=task.task_id, label=task.name)
            for task in tasks.values()
        ]
        return self.async_show_form(
            step_id="ping_tasks",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PING_TASK_IDS,
                        default=self._pending[CONF_PING_TASK_IDS],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=task_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_ping_stats(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select default or advanced statistics explicitly."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = tuple(str(item) for item in user_input[CONF_PING_STAT_KEYS])
            if not selected:
                errors["base"] = "ping_stats_required"
            elif any(key not in PING_STAT_KEYS for key in selected):
                errors["base"] = "invalid_ping_stat"
            else:
                self._pending[CONF_PING_STAT_KEYS] = list(selected)
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="ping_stats",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PING_STAT_KEYS,
                        default=self._pending[CONF_PING_STAT_KEYS],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(PING_STAT_KEYS),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                            translation_key="ping_statistic",
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Preview the maximum Ping entity count and save once."""
        snapshot = self._snapshot
        node_count = len(snapshot.nodes) if snapshot is not None else 0
        estimate = (
            node_count
            * len(self._selected_values(CONF_PING_TASK_IDS))
            * len(self._selected_values(CONF_PING_STAT_KEYS))
        )
        errors: dict[str, str] = {}
        if estimate > MAX_PING_ENTITIES:
            errors["base"] = "too_many_ping_entities"
        elif user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "not_confirmed"
            else:
                options = KomariOptions.from_mapping(self._pending)
                return self.async_create_entry(title="", data=options.as_dict())

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            description_placeholders={
                "entity_count": str(estimate),
                "entity_limit": str(MAX_PING_ENTITIES),
            },
            errors=errors,
        )
