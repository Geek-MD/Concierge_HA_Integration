"""The Concierge Services integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS: list[str] = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Concierge Services from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    # Store the config entry data
    hass.data[DOMAIN][entry.entry_id] = entry.data
    
    # Create main device and assign to area if specified
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.data.get("friendly_name", entry.data.get("email")),
        manufacturer="Concierge Services",
        model="Email Integration",
        configuration_url="https://github.com/Geek-MD/Concierge_Services",
    )
    
    # Associate device with area if specified
    area_id = entry.data.get("area_id")
    if area_id:
        device_registry.async_update_device(device.id, area_id=area_id)
    
    # Forward the setup to the platform (when we have platforms)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    _LOGGER.info(
        "Concierge Services integration loaded for %s (friendly name: %s)",
        entry.data.get("email"),
        entry.data.get("friendly_name", entry.data.get("email")),
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms (when we have platforms)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
