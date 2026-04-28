"""Button platform for Concierge Services — per-device force-refresh and recalculate."""
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
from .service_detector import normalize_service_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services button entities.

    Two buttons are created for every subentry (service device), each
    associated with its own subentry so they appear grouped correctly in
    the Home Assistant device registry:

    - **Force Refresh** — triggers a full email + PDF re-scan and then calls
      Recalculate as its final step.
    - **Recalculate** — recomputes only the formula-derived sensors (e.g.
      ``gc_total``) from the values already stored in the coordinator, without
      touching the mailbox.  Can also be called independently at any time.
    """
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        async_add_entities(
            [
                ConciergeServiceForceRefreshButton(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
                ConciergeServiceRecalculateButton(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
            ],
            config_subentry_id=subentry_id,  # type: ignore[call-arg]
        )


class ConciergeServiceForceRefreshButton(ButtonEntity):
    """Button entity that forces an immediate email scan and PDF analysis.

    Pressing this button triggers ``ConciergeServicesCoordinator.async_refresh_service``
    for the specific service device, bypassing the regular polling interval.
    As its final step, force refresh automatically calls the Recalculate logic
    (``async_recompute_derived``) so formula-derived sensors are always up to
    date after a scan.

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

        service_id: str = normalize_service_id(subentry_data.get(CONF_SERVICE_ID, subentry_id))
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


class ConciergeServiceRecalculateButton(ButtonEntity):
    """Button entity that recomputes only the formula-derived sensors.

    Pressing this button triggers
    ``ConciergeServicesCoordinator.async_recompute_derived`` for the specific
    service device.  Unlike *Force Refresh*, it does **not** open an IMAP
    connection or re-download any PDF — it only re-runs the arithmetic
    formulas (e.g. ``gc_total = subtotal_departamento + cargo_fijo``) on the
    attribute values that are already stored in the coordinator.

    Useful after a manual ``set_value`` override to immediately propagate the
    corrected input value into all formula sensors without waiting for the
    next polling cycle or triggering a full refresh.

    Entity category: CONFIG — appears in the device Configuration panel.
    """

    _attr_has_entity_name = False
    _attr_icon = "mdi:calculator-variant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the recalculate button."""
        self._coordinator = coordinator
        self._subentry_id = subentry_id

        service_id: str = normalize_service_id(subentry_data.get(CONF_SERVICE_ID, subentry_id))
        service_name: str = subentry_data.get(
            CONF_SERVICE_NAME,
            service_id.replace("_", " ").title(),
        )

        self._attr_name = f"Concierge {service_id} Recalculate"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_recalculate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{subentry_id}")},
            name=service_name,
            manufacturer="Concierge Services",
            model="Service Account",
        )

    async def async_press(self) -> None:
        """Handle button press — recompute derived sensors for this device."""
        _LOGGER.info(
            "Concierge Services: recalculate triggered via button for subentry %s",
            self._subentry_id,
        )
        await self._coordinator.async_recompute_derived(self._subentry_id)
