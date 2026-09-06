"""Config flow for the Komari integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
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
    ObjectSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .api import KomariApiClient, select_ping_stats_method
from .const import (
    API_PATH,
    BUILT_IN_METRIC_KEYS,
    CONF_API_KEY,
    CONF_ENABLE_EXPIRY_ENTITIES,
    CONF_ENABLE_TRAFFIC_ENTITIES,
    CONF_METRIC_INTERVAL,
    CONF_METRIC_KEYS,
    CONF_METRIC_TAG_FILTERS,
    CONF_PING_INTERVAL,
    CONF_PING_STAT_KEYS,
    CONF_PING_TASK_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    DYNAMIC_METRIC_METHODS,
    MAX_METRIC_ENTITIES,
    MAX_METRIC_INTERVAL,
    MAX_METRIC_KEYS,
    MAX_PING_ENTITIES,
    MAX_PING_INTERVAL,
    MAX_PING_TASKS,
    MAX_SCAN_INTERVAL,
    METHOD_PUBLIC_PING_TASKS,
    MIN_METRIC_INTERVAL,
    MIN_PING_INTERVAL,
    MIN_SCAN_INTERVAL,
    PING_STAT_KEYS,
    PING_STATS_METHODS,
    SUPPORTED_DYNAMIC_METRIC_TYPES,
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

RECONFIGURE_CONFIRM_SCHEMA = vol.Schema(
    {vol.Required("confirm", default=False): BooleanSelector()}
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

    _reconfigure_candidate: dict[str, Any] | None = None
    _reconfigure_title: str | None = None

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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the panel URL or TLS verification for the current entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                normalized_url = normalize_panel_url(str(user_input[CONF_URL]))
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_url"
            else:
                if any(
                    other_entry.entry_id != entry.entry_id
                    and other_entry.data.get(CONF_URL) == normalized_url
                    for other_entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")

                candidate = dict(entry.data)
                candidate[CONF_URL] = normalized_url
                candidate[CONF_VERIFY_SSL] = bool(
                    user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                )
                bootstrap, errors = await self._async_validate_with_errors(candidate)
                if bootstrap is not None:
                    host = URL(normalized_url).host or normalized_url
                    title = bootstrap.panel.name or host
                    try:
                        current_url = normalize_panel_url(str(entry.data[CONF_URL]))
                    except KeyError, TypeError, ValueError:
                        current_url = str(entry.data.get(CONF_URL, ""))

                    if normalized_url == current_url:
                        return self.async_update_reload_and_abort(
                            entry,
                            title=title,
                            data=candidate,
                        )

                    self._reconfigure_candidate = candidate
                    self._reconfigure_title = title
                    return await self.async_step_reconfigure_confirm()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_URL,
                        default=str(entry.data[CONF_URL]),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                    vol.Required(
                        CONF_VERIFY_SSL,
                        default=bool(
                            entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                        ),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm changing the URL of the logical Komari panel."""
        entry = self._get_reconfigure_entry()
        candidate = self._reconfigure_candidate
        if candidate is None:
            return await self.async_step_reconfigure()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "not_confirmed"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=self._reconfigure_title or entry.title,
                    data=candidate,
                )

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=RECONFIGURE_CONFIRM_SCHEMA,
            description_placeholders={
                "old_url": str(entry.data[CONF_URL]),
                "new_url": str(candidate[CONF_URL]),
            },
            errors=errors,
        )


class KomariOptionsFlow(OptionsFlowWithReload):
    """Configure per-panel polling and optional entity families."""

    def __init__(self) -> None:
        """Initialize one options transaction."""
        self._pending: dict[str, object] = {}
        self._metric_preview_count: int | None = None

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
            and bool(snapshot.capabilities & PING_STATS_METHODS)
            and snapshot.ping_tasks
        )

    def _has_dynamic_metrics(self) -> bool:
        """Return whether both Metric RPC methods are currently available."""
        snapshot = self._snapshot
        return bool(
            snapshot is not None
            and DYNAMIC_METRIC_METHODS.issubset(snapshot.capabilities)
        )

    def _selected_values(self, key: str) -> tuple[str, ...]:
        """Return one pending multi-select value with a concrete type."""
        value = self._pending.get(key)
        if not isinstance(value, list | tuple):
            return ()
        return tuple(str(item) for item in value)

    def _metric_definitions(self) -> dict[str, Any]:
        """Return the currently selectable retained custom Metric definitions."""
        snapshot = self._snapshot
        definitions = getattr(snapshot, "metric_definitions", {})
        if not isinstance(definitions, Mapping):
            return {}
        return {
            metric_key: definition
            for metric_key, definition in definitions.items()
            if isinstance(metric_key, str)
            and metric_key not in BUILT_IN_METRIC_KEYS
            and getattr(definition, "metric_type", None)
            in SUPPORTED_DYNAMIC_METRIC_TYPES
            and getattr(definition, "retention_days", 0) > 0
        }

    def _metric_selection_options(self) -> list[SelectOptionDict]:
        """Build real Definition choices while preserving old unavailable keys."""
        definitions = self._metric_definitions()
        options = [
            SelectOptionDict(
                value=metric_key,
                label=self._metric_label(metric_key, definition),
            )
            for metric_key, definition in sorted(definitions.items())
        ]
        available = frozenset(definitions)
        options.extend(
            SelectOptionDict(value=metric_key, label=f"{metric_key} (unavailable)")
            for metric_key in self._selected_values(CONF_METRIC_KEYS)
            if metric_key not in available
        )
        return options

    def _metric_label(self, metric_key: str, definition: Any) -> str:
        """Return a stable human label without changing a Metric's identity."""
        description = getattr(definition, "description", None)
        if isinstance(description, str) and description.strip():
            return f"{description.strip()} ({metric_key})"
        if isinstance(description, Mapping):
            language = self.hass.config.language
            language_candidates = (
                language,
                language.split("-", 1)[0],
                "en",
            )
            for candidate in language_candidates:
                value = description.get(candidate)
                if isinstance(value, str) and value.strip():
                    return f"{value.strip()} ({metric_key})"
            for _, value in sorted(description.items()):
                if isinstance(value, str) and value.strip():
                    return f"{value.strip()} ({metric_key})"
        return metric_key

    def _ping_entity_estimate(self) -> int:
        """Estimate only currently discoverable Ping entities."""
        snapshot = self._snapshot
        if snapshot is None or not self._has_ping():
            return 0
        selected_task_count = sum(
            task_id in snapshot.ping_tasks
            for task_id in self._selected_values(CONF_PING_TASK_IDS)
        )
        return (
            len(snapshot.nodes)
            * selected_task_count
            * len(self._selected_values(CONF_PING_STAT_KEYS))
        )

    async def _async_continue_after_ping(self) -> ConfigFlowResult:
        """Continue into optional Metric configuration after Ping choices."""
        if self._has_dynamic_metrics() and (
            self._metric_definitions() or self._selected_values(CONF_METRIC_KEYS)
        ):
            return await self.async_step_metrics()
        if self._selected_values(CONF_METRIC_KEYS):
            return await self.async_step_metrics_unavailable()
        return await self.async_step_confirm()

    def _create_options_entry(self) -> ConfigFlowResult:
        """Persist a fully normalized options payload exactly once."""
        options = KomariOptions.from_mapping(self._pending)
        return self.async_create_entry(title="", data=options.as_dict())

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
            return await self._async_continue_after_ping()

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
                try:
                    if selected and snapshot is not None:
                        select_ping_stats_method(
                            snapshot.capabilities,
                            tuple(snapshot.nodes),
                            selected,
                        )
                except KomariUnsupportedError:
                    errors["base"] = "ping_batch_unsupported"
                else:
                    self._pending[CONF_PING_TASK_IDS] = list(selected)
                    if not selected:
                        return await self._async_continue_after_ping()
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
                return await self._async_continue_after_ping()

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

    async def async_step_metrics(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Select retained custom Metrics and their isolated polling interval."""
        if not self._has_dynamic_metrics():
            if self._selected_values(CONF_METRIC_KEYS):
                return await self.async_step_metrics_unavailable()
            return await self.async_step_confirm()

        selection_options = self._metric_selection_options()
        if not selection_options:
            return await self.async_step_confirm()

        errors: dict[str, str] = {}
        if user_input is not None:
            raw_selected = user_input.get(CONF_METRIC_KEYS, [])
            if not isinstance(raw_selected, list | tuple) or any(
                not isinstance(metric_key, str) for metric_key in raw_selected
            ):
                errors["base"] = "too_many_metrics"
            else:
                selected = tuple(raw_selected)
                allowed = {str(option["value"]) for option in selection_options}
                if (
                    len(selected) > MAX_METRIC_KEYS
                    or len(set(selected)) != len(selected)
                    or any(metric_key not in allowed for metric_key in selected)
                ):
                    errors["base"] = "too_many_metrics"
                else:
                    self._pending[CONF_METRIC_INTERVAL] = user_input.get(
                        CONF_METRIC_INTERVAL,
                        self._pending[CONF_METRIC_INTERVAL],
                    )
                    self._pending[CONF_METRIC_KEYS] = list(selected)
                    previous_filters = self._pending.get(CONF_METRIC_TAG_FILTERS, {})
                    self._pending[CONF_METRIC_TAG_FILTERS] = (
                        {
                            metric_key: tags
                            for metric_key, tags in previous_filters.items()
                            if metric_key in selected
                        }
                        if isinstance(previous_filters, Mapping)
                        else {}
                    )
                    self._metric_preview_count = None
                    if not selected:
                        return await self.async_step_confirm()
                    return await self.async_step_metric_filters()

        return self.async_show_form(
            step_id="metrics",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METRIC_INTERVAL,
                        default=self._pending[CONF_METRIC_INTERVAL],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_METRIC_INTERVAL,
                            max=MAX_METRIC_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                    vol.Required(
                        CONF_METRIC_KEYS,
                        default=self._pending[CONF_METRIC_KEYS],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=selection_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_metrics_unavailable(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Preserve saved Metric choices while the optional API is unavailable."""
        if user_input is not None:
            if user_input.get("clear_saved_metric_selections", False):
                self._pending[CONF_METRIC_KEYS] = []
                self._pending[CONF_METRIC_TAG_FILTERS] = {}
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="metrics_unavailable",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "clear_saved_metric_selections",
                        default=False,
                    ): BooleanSelector()
                }
            ),
        )

    async def async_step_metric_filters(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Capture exact Metric tag filters without allowing broad fallback."""
        selected = self._selected_values(CONF_METRIC_KEYS)
        if not selected:
            return await self.async_step_confirm()

        errors: dict[str, str] = {}
        if user_input is not None:
            raw_filters = user_input.get(CONF_METRIC_TAG_FILTERS, {})
            candidate = dict(self._pending)
            candidate[CONF_METRIC_TAG_FILTERS] = raw_filters
            normalized = KomariOptions.from_mapping(candidate)
            if (
                not isinstance(raw_filters, Mapping)
                or any(
                    not isinstance(metric_key, str) or metric_key not in selected
                    for metric_key in raw_filters
                )
                or normalized.metric_configuration_invalid
                or bool(normalized.invalid_metric_filter_keys & frozenset(selected))
            ):
                errors["base"] = "invalid_metric_filter"
            else:
                self._pending[CONF_METRIC_TAG_FILTERS] = normalized.as_dict()[
                    CONF_METRIC_TAG_FILTERS
                ]
                self._metric_preview_count = None
                return await self.async_step_metric_preview()

        return self.async_show_form(
            step_id="metric_filters",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METRIC_TAG_FILTERS,
                        default=self._pending[CONF_METRIC_TAG_FILTERS],
                    ): ObjectSelector()
                }
            ),
            errors=errors,
        )

    async def _async_metric_preview(self) -> int:
        """Issue bounded read-only queries and return viable dynamic entities."""
        snapshot = self._snapshot
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        client = getattr(runtime_data, "client", None)
        if (
            snapshot is None
            or client is None
            or not DYNAMIC_METRIC_METHODS.issubset(snapshot.capabilities)
        ):
            raise KomariUnsupportedError("Metric preview is unavailable")

        options = KomariOptions.from_mapping(self._pending)
        definitions = self._metric_definitions()
        groups: dict[tuple[tuple[str, str], ...], list[str]] = {}
        for metric_key in options.metric_keys:
            if metric_key in options.invalid_metric_filter_keys:
                continue
            if metric_key not in definitions:
                continue
            tags = options.metric_tag_filters.get(metric_key, {})
            groups.setdefault(tuple(sorted(tags.items())), []).append(metric_key)

        if not groups or not snapshot.nodes:
            return 0
        if len(groups) > MAX_METRIC_KEYS:
            raise KomariProtocolError("Metric preview has too many filter groups")

        series_keys: set[object] = set()
        for canonical_tags, metric_keys in groups.items():
            values = await client.async_query_metrics(
                snapshot.capabilities,
                metric_keys=tuple(metric_keys),
                entity_ids=tuple(snapshot.nodes),
                tags=dict(canonical_tags),
                hours=4,
                max_points=1,
                aggregation="last",
                fill_empty=False,
            )
            series_keys.update(
                series_key
                for series_key, value in values.items()
                if series_key.entity_id in snapshot.nodes
                and series_key.metric_key in metric_keys
                and value.value is not None
            )
        return len(series_keys)

    async def async_step_metric_preview(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show an enforced read-only Metric entity estimate before saving."""
        errors: dict[str, str] = {}
        if self._metric_preview_count is None:
            try:
                self._metric_preview_count = await self._async_metric_preview()
            except Exception:
                _LOGGER.debug(
                    "Unable to preview selected Metric entities",
                    exc_info=True,
                )
                errors["base"] = "metric_preview_failed"

        estimate = self._metric_preview_count or 0
        if not errors and estimate > MAX_METRIC_ENTITIES:
            errors["base"] = "too_many_metric_entities"
        elif not errors and self._ping_entity_estimate() > MAX_PING_ENTITIES:
            errors["base"] = "too_many_ping_entities"
        elif user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "not_confirmed"
            else:
                return self._create_options_entry()

        return self.async_show_form(
            step_id="metric_preview",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            description_placeholders={
                "entity_count": str(estimate),
                "entity_limit": str(MAX_METRIC_ENTITIES),
            },
            errors=errors,
        )

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Preview the maximum Ping entity count and save once."""
        estimate = self._ping_entity_estimate()
        errors: dict[str, str] = {}
        if estimate > MAX_PING_ENTITIES:
            errors["base"] = "too_many_ping_entities"
        elif user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "not_confirmed"
            else:
                return self._create_options_entry()

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
