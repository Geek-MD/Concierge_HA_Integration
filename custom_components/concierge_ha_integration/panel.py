"""Concierge sidebar panel, native input_boolean and persistent bill history."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import ToggleEntity
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .billing import parse_bill_date
from .const import CONF_SERVICE_NAME, CONF_SERVICE_TYPE, DOMAIN
from .cost_history import monthly_totals, record_bill

PANEL_PATH = "concierge"
VISIBILITY_ENTITY = "input_boolean.concierge_show_panel"
MODULE_URL = "/concierge_panel/concierge-panel.js"


class PanelVisibility(ToggleEntity, RestoreEntity):
    """A real input_boolean served by the native input_boolean actions."""

    _attr_should_poll = False
    _attr_name = "Concierge: mostrar panel"
    _attr_icon = "mdi:view-dashboard"
    _attr_is_on = True

    def __init__(self) -> None:
        """Reserve a stable ID without creating a user-editable helper."""
        self.entity_id = VISIBILITY_ENTITY

    async def async_added_to_hass(self) -> None:
        """Restore the user's visibility choice across restarts and reloads."""
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            self._attr_is_on = state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show the sidebar panel."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hide the sidebar panel."""
        self._attr_is_on = False
        self.async_write_ha_state()


class ConciergePanel:
    """Own entry-scoped history and subscriptions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        """Initialize runtime state."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.cost_history")
        self.history: dict[str, dict[str, dict[str, Any]]] = {}
        self.lock = asyncio.Lock()
        self.active = True
        self.owns_helper = False

    async def async_setup(self) -> None:
        """Register assets once and bind panel lifecycle to the config entry."""
        # History is loaded before the coordinator's first refresh.
        # HTTP routes survive config-entry reloads.
        if not self.hass.data.get(f"{DOMAIN}_panel_static"):
            await self.hass.http.async_register_static_paths([
                StaticPathConfig(
                    MODULE_URL,
                    str(Path(__file__).parent / "frontend" / "concierge-panel.js"),
                    False,
                )
            ])
            websocket_api.async_register_command(self.hass, websocket_panel_data)
            self.hass.data[f"{DOMAIN}_panel_static"] = True
        component = self.hass.data[DATA_INSTANCES]["input_boolean"]
        if component.get_entity(VISIBILITY_ENTITY) is None:
            await component.async_add_entities([PanelVisibility()])
            self.owns_helper = True
        self.entry.async_on_unload(async_track_state_change_event(
            self.hass, [VISIBILITY_ENTITY], self._visibility_changed
        ))
        self.entry.async_on_unload(self.coordinator.async_add_listener(self._updated))
        self._updated()
        await self.async_visibility()

    @callback
    def _updated(self) -> None:
        """Record only valid bill amounts, including manual corrections."""
        if not self.active:
            return
        changed = False
        services = (self.coordinator.data or {}).get("services", {})
        # CI pins HA 2025.1; subentry types were introduced in later HA releases.
        for sub_id, sub in self.entry.subentries.items():  # type: ignore[attr-defined]
            data = services.get(sub_id, {})
            key = {"common_expenses": "gc_total", "hot_water": "hot_water_amount"}.get(
                sub.data.get(CONF_SERVICE_TYPE), "total_amount"
            )
            for bill in [*data.pop("historical_bills", []), data]:
                attrs = bill.get("attributes", {})
                issued = parse_bill_date(attrs.get("emission_date")) or parse_bill_date(bill.get("last_updated"))
                if issued:
                    changed |= record_bill(
                        self.history, sub_id, issued, attrs.get(key), dt_util.now().date()
                    )
        if changed:
            self.store.async_delay_save(lambda: self.history, 2)

    async def _visibility_changed(self, event: Any) -> None:
        """Serialize rapid toggles using the current state."""
        await self.async_visibility()

    async def async_visibility(self) -> None:
        """Register or remove the panel according to the helper state."""
        async with self.lock:
            frontend.async_remove_panel(self.hass, PANEL_PATH)
            if self.active and self.hass.states.is_state(VISIBILITY_ENTITY, "on"):
                await panel_custom.async_register_panel(
                    self.hass,
                    frontend_url_path=PANEL_PATH,
                    webcomponent_name="concierge-panel",
                    sidebar_title="Concierge",
                    sidebar_icon="mdi:receipt-text",
                    module_url=f"{MODULE_URL}?v=2.0.0",
                )

    def payload(self) -> dict[str, Any]:
        """Use registry IDs so user-renamed entities keep appearing."""
        registry = er.async_get(self.hass)
        entities = er.async_entries_for_config_entry(registry, self.entry.entry_id)
        today = dt_util.now().date()
        return {"services": [
            {
                "id": sub_id,
                "name": sub.data.get(CONF_SERVICE_NAME, sub.title),
                "months": monthly_totals(self.history.get(sub_id, {}), today),
                "entities": [
                    {"entity_id": entity.entity_id, "disabled": bool(entity.disabled_by)}
                    for entity in entities if entity.config_subentry_id == sub_id  # type: ignore[attr-defined]
                ],
            }
            for sub_id, sub in self.entry.subentries.items()  # type: ignore[attr-defined]
        ]}

    async def async_unload(self) -> None:
        """Flush history and remove owned UI resources."""
        self.active = False
        await self.async_visibility()
        await self.store.async_save(self.history)
        if self.owns_helper:
            await self.hass.data[DATA_INSTANCES]["input_boolean"].async_remove_entity(
                VISIBILITY_ENTITY
            )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel_data"})
@callback
def websocket_panel_data(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return panel data over the authenticated Home Assistant connection."""
    for data in hass.data.get(DOMAIN, {}).values():
        if panel := data.get("panel"):
            connection.send_result(msg["id"], panel.payload())
            return
    connection.send_error(msg["id"], "not_loaded", "Concierge is not loaded")
