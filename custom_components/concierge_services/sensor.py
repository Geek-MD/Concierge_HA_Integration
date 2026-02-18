"""Sensor platform for Concierge Services."""
from __future__ import annotations

import imaplib
import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DOMAIN,
    CONF_IMAP_SERVER,
    CONF_IMAP_PORT,
    CONF_EMAIL,
    CONF_PASSWORD,
)

_LOGGER = logging.getLogger(__name__)

# Update interval for checking mail server connection
SCAN_INTERVAL = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services sensors."""
    coordinator = ConciergeServicesCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()
    
    async_add_entities([ConciergeServicesConnectionSensor(coordinator, config_entry)])


class ConciergeServicesCoordinator(DataUpdateCoordinator[str]):
    """Class to manage fetching Concierge Services data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.config_entry = config_entry

    async def _async_update_data(self) -> str:
        """Fetch data from IMAP server."""
        try:
            return await self.hass.async_add_executor_job(self._check_connection)
        except Exception as err:
            raise UpdateFailed(f"Error communicating with IMAP server: {err}") from err

    def _check_connection(self) -> str:
        """Check IMAP connection."""
        try:
            data = self.config_entry.data
            imap = imaplib.IMAP4_SSL(data[CONF_IMAP_SERVER], data[CONF_IMAP_PORT])
            imap.login(data[CONF_EMAIL], data[CONF_PASSWORD])
            imap.logout()
            return "OK"
        except Exception as err:
            _LOGGER.warning("IMAP connection check failed: %s", err)
            return "Problem"


class ConciergeServicesConnectionSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor to monitor mail server connection status."""

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = f"Concierge Services {config_entry.data[CONF_EMAIL]} Connection"
        self._attr_unique_id = f"{config_entry.entry_id}_connection"
        self._attr_icon = "mdi:email-check"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self.coordinator.data

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return additional state attributes."""
        return {
            "email": self.coordinator.config_entry.data[CONF_EMAIL],
            "imap_server": self.coordinator.config_entry.data[CONF_IMAP_SERVER],
            "imap_port": self.coordinator.config_entry.data[CONF_IMAP_PORT],
        }
