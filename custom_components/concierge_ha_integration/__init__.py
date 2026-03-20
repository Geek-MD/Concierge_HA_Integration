"""The Concierge Services integration."""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_EMAIL,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_PASSWORD,
    CONF_SERVICE_ID,
    DOMAIN,
)
from .sensor import ConciergeServicesCoordinator
from .service_detector import detect_services_from_imap, normalize_service_id

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS: list[str] = ["sensor", "binary_sensor"]

# How often to re-scan the inbox for new services
_DISCOVERY_INTERVAL = timedelta(hours=1)

# Key used inside hass.data[DOMAIN][entry_id] to track queued discoveries
_PENDING_DISCOVERIES_KEY = "pending_discoveries"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries to the current version.

    Version history:
    - 1.1 (≤ v0.4.x): entities registered without config_subentry_id; hub device
      associated with the main config entry.
    - 1.2 (≥ v0.5.x): service entities carry config_subentry_id; hub device removed.
    - 1.3 (≥ v0.7.0): single service sensor split into binary_sensor + 4 sensors;
      old sensor.concierge_services_* entities removed from entity registry.
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

    if entry.version == 1 and entry.minor_version < 3:
        _migrate_1_2_to_1_3(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)  # type: ignore[call-arg]
        _LOGGER.info("Concierge Services migration to version 1.3 completed")

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


@callback
def _migrate_1_2_to_1_3(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate entity registry from v1.2 (≤ v0.6.x) to v1.3 (≥ v0.7.0).

    Removes the old single-sensor entities (unique_id = entry_id + "_" + subentry_id)
    that have been replaced by binary_sensor + 4 dedicated sensors.
    """
    ent_reg = er.async_get(hass)
    subentries = entry.subentries  # type: ignore[attr-defined]

    old_unique_ids: set[str] = {
        f"{entry.entry_id}_{sub_id}" for sub_id in subentries
    }

    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity_entry.unique_id in old_unique_ids:
            ent_reg.async_remove(entity_entry.entity_id)
            _LOGGER.debug(
                "Removed legacy service sensor entity %s (unique_id: %s)",
                entity_entry.entity_id,
                entity_entry.unique_id,
            )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Concierge Services from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id][_PENDING_DISCOVERIES_KEY] = set()

    # Effective config merges original data with any options overrides
    effective_cfg = {**entry.data, **entry.options}

    # Initialise the shared coordinator here so that both the sensor and
    # binary_sensor platforms can access it from hass.data without a race.
    coordinator = ConciergeServicesCoordinator(hass, entry, effective_cfg)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    # Forward the setup to sensor and binary_sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever it is updated (options changed, subentries
    # added/removed) so that sensors are recreated with the latest config.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Run an initial inbox scan for new services right after setup.
    hass.async_create_task(
        _async_discover_services(hass, entry),
        f"concierge_ha_integration discovery initial {entry.entry_id}",
    )

    # Schedule periodic re-scans so newly-received bills are noticed.
    @callback
    def _schedule_discovery(_now: object) -> None:
        """Create a discovery task on each interval tick."""
        hass.async_create_task(
            _async_discover_services(hass, entry),
            f"concierge_ha_integration discovery periodic {entry.entry_id}",
        )

    entry.async_on_unload(
        async_track_time_interval(hass, _schedule_discovery, _DISCOVERY_INTERVAL)
    )

    _LOGGER.info(
        "Concierge Services integration loaded for %s",
        entry.data.get("email"),
    )

    return True


async def _async_discover_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Scan the IMAP inbox and trigger discovery flows for new services.

    A discovery subentry flow is initiated for every service found in the
    inbox that is not yet configured as a subentry and does not already have
    a pending discovery flow in progress.  The user then decides whether to
    add the device from the integration card in Configuration → Integrations.
    """
    cfg = {**entry.data, **entry.options}

    try:
        services = await hass.async_add_executor_job(
            detect_services_from_imap,
            cfg[CONF_IMAP_SERVER],
            cfg[CONF_IMAP_PORT],
            cfg[CONF_EMAIL],
            cfg[CONF_PASSWORD],
            100,
        )
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.debug("Concierge Services discovery scan failed: %s", err)
        return

    # IDs already configured as subentries.
    # Normalise legacy IDs (e.g. "aguas_andinas" → "agua") to avoid
    # re-offering services that were configured under an older display name.
    existing_ids: set[str | None] = {
        normalize_service_id(sub.data.get(CONF_SERVICE_ID, ""))
        for sub in entry.subentries.values()  # type: ignore[attr-defined]
    }

    # IDs for which a discovery flow is already in progress (avoid duplicates)
    pending: set[str] = (
        hass.data.get(DOMAIN, {})
        .get(entry.entry_id, {})
        .get(_PENDING_DISCOVERIES_KEY, set())
    )

    # Retrieve the subentry flow manager (requires HA ≥ 2025.4)
    subentries_mgr = getattr(hass.config_entries, "subentries", None)
    if subentries_mgr is None:
        _LOGGER.debug(
            "Concierge Services: subentry discovery requires HA 2025.4 or newer"
        )
        return

    for service in services:
        if service.service_id in existing_ids or service.service_id in pending:
            continue

        _LOGGER.info(
            "Concierge Services: discovered new service '%s' (%s)",
            service.service_name,
            service.service_id,
        )
        pending.add(service.service_id)

        try:
            hass.async_create_task(
                subentries_mgr.async_init(  # type: ignore[attr-defined]
                    (entry.entry_id, "service"),
                    context={"source": "discovery"},
                    data=asdict(service),
                ),
                f"concierge_ha_integration discovery flow {service.service_id}",
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Concierge Services: failed to init discovery flow for '%s': %s",
                service.service_name,
                err,
            )
            pending.discard(service.service_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
