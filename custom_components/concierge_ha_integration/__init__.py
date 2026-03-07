"""The Concierge Services integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS: list[str] = ["sensor"]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries to the current version.

    Version history:
    - 1.1 (≤ v0.4.x): entities registered without config_subentry_id; hub device
      associated with the main config entry.
    - 1.2 (≥ v0.5.x): service entities carry config_subentry_id; hub device removed.
    """
    _LOGGER.info(
        "Migrating Concierge Services config entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    if entry.version == 1 and entry.minor_version < 2:
        _migrate_1_1_to_1_2(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)  # type: ignore[call-arg]
        _LOGGER.info("Concierge Services migration to version 1.2 completed")

    return True


@callback
def _migrate_1_1_to_1_2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate entity/device registry from v1.1 (≤ v0.4.x) to v1.2 (≥ v0.5.x).

    - Assigns config_subentry_id to service entities so they appear under
      their respective subentry in the HA device registry.
    - Updates service device associations from the main config entry to their
      own subentry (fixes "Devices that don't belong to a sub-entry").
    - Removes the legacy hub device that was tied to the main config entry.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Build mapping: unique_id → subentry_id for all known subentries.
    # config_entry.subentries requires HA ≥ 2025.2 (subentry API).
    subentry_id_map: dict[str, str] = {}
    subentries = entry.subentries  # type: ignore[attr-defined]
    for sub_id in subentries:
        subentry_id_map[f"{entry.entry_id}_{sub_id}"] = sub_id

    # 1. Migrate entity registry: assign config_subentry_id to service entities
    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity_entry.config_subentry_id is not None:  # type: ignore[attr-defined]
            continue  # Already migrated
        sub_id = subentry_id_map.get(entity_entry.unique_id)
        if sub_id:
            ent_reg.async_update_entity(  # type: ignore[call-arg]
                entity_entry.entity_id,
                config_subentry_id=sub_id,
            )
            _LOGGER.debug(
                "Migrated entity %s → subentry %s",
                entity_entry.entity_id,
                sub_id,
            )

    # 2. Remove legacy hub device (identifiers tied to main config entry)
    hub_device = dev_reg.async_get_device(  # type: ignore[call-arg]
        identifiers={(DOMAIN, entry.entry_id)}
    )
    if hub_device is not None:
        dev_reg.async_remove_device(hub_device.id)
        _LOGGER.debug("Removed legacy hub device %s", hub_device.id)

    # 3. Migrate service devices: move from main-entry association to subentry
    for sub_id in subentries:
        device = dev_reg.async_get_device(  # type: ignore[call-arg]
            identifiers={(DOMAIN, f"{entry.entry_id}_{sub_id}")}
        )
        if device is None:
            continue
        # Only migrate devices still associated with the main entry (None subentry)
        current_subentries: set[str | None] = (
            device.config_entries_subentries.get(entry.entry_id, set())  # type: ignore[attr-defined]
        )
        if None not in current_subentries:
            continue  # Already correctly associated with the subentry
        # Add the proper subentry association first…
        dev_reg.async_update_device(  # type: ignore[call-arg]
            device.id,
            add_config_entry_id=entry.entry_id,
            add_config_subentry_id=sub_id,
        )
        # …then remove the stale main-entry (None) association
        dev_reg.async_update_device(  # type: ignore[call-arg]
            device.id,
            remove_config_entry_id=entry.entry_id,
            remove_config_subentry_id=None,
        )
        _LOGGER.debug(
            "Migrated device for subentry %s to subentry association", sub_id
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Concierge Services from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    # Forward the setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever it is updated (options changed, subentries
    # added/removed) so that sensors are recreated with the latest config.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info(
        "Concierge Services integration loaded for %s",
        entry.data.get("email"),
    )

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
