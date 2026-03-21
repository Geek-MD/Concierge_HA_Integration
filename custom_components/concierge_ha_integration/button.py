"""Button platform for Concierge Services — per-device force-refresh."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_SERVICE_ID,
    CONF_SERVICE_NAME,
    DOMAIN,
)
from .sensor import ConciergeServicesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services button entities.

    One force-refresh button is created for every subentry (service device)
    and associated with its own subentry so it appears grouped correctly in
    the Home Assistant device registry.
    """
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        async_add_entities(
            [
                ConciergeServiceForceRefreshButton(
                    coordinator, config_entry, subentry_id, subentry.data
                )
            ],
            config_subentry_id=subentry_id,  # type: ignore[call-arg]
        )


class ConciergeServiceForceRefreshButton(ButtonEntity):
    """Button entity that forces an immediate email scan and PDF analysis.

    Pressing this button triggers ``ConciergeServicesCoordinator.async_refresh_service``
    for the specific service device, bypassing the regular polling interval.

    Entity category: CONFIG — appears in the device Configuration panel so it
    does not clutter the main entity list.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the force-refresh button."""
        self._coordinator = coordinator
        self._subentry_id = subentry_id

        service_id: str = subentry_data.get(CONF_SERVICE_ID, subentry_id)
        service_name: str = subentry_data.get(
            CONF_SERVICE_NAME,
            service_id.replace("_", " ").title(),
        )

        self._attr_name = f"Concierge {service_id} Force Refresh"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_force_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{subentry_id}")},
            name=service_name,
            manufacturer="Concierge Services",
            model="Service Account",
        )

    async def async_press(self) -> None:
        """Handle button press — force email scan and PDF analysis for this device."""
        _LOGGER.info(
            "Concierge Services: force refresh triggered via button for subentry %s",
            self._subentry_id,
        )
        await self._coordinator.async_refresh_service(self._subentry_id)
