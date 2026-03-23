"""Binary sensor platform for Concierge Services."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dateutil.relativedelta import relativedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERVICE_ID,
    CONF_SERVICE_NAME,
    CONF_SERVICE_TYPE,
    DOMAIN,
    SERVICE_TYPE_COMMON_EXPENSES,
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_HOT_WATER,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)
from .sensor import ConciergeServicesCoordinator
from .service_detector import normalize_service_id

# Attributes retained in the status binary sensor for every service type.
# consumption, consumption_unit, and total_amount are now dedicated sensors.
_STATUS_COMMON_ATTRS: tuple[str, ...] = (
    "folio",
    "billing_period_start",
    "billing_period_end",
    "customer_number",
    "address",
    "due_date",
)

# Service-type-specific attribute defaults retained in the status sensor.
# cost_per_m3s and cost_per_kwh move to the dedicated cost_per_unit sensor.
_GAS_STATUS_ATTR_DEFAULTS: dict[str, Any] = {
    "pdf_url": "",
}
_ELECTRICITY_STATUS_ATTR_DEFAULTS: dict[str, Any] = {
    "tariff_code": 0,
    "connected_power": 0,
    "connected_power_unit": 0,
    "area": 0,
    "substation": 0,
    "pdf_url": "",
}
_WATER_STATUS_ATTR_DEFAULTS: dict[str, Any] = {
}

# Gastos Comunes: building-level totals used to verify the apartment portion,
# plus the funds-provision percentage and agua caliente subtotal extracted
# from the same "Nota de Cobro" PDF.
_COMMON_EXPENSES_STATUS_ATTR_DEFAULTS: dict[str, Any] = {
    "gross_common_expenses": 0,
    "gross_common_expenses_percentage": 0,
    "funds_provision_percentage": 0,
    "hot_water_amount": 0,
    "subtotal_consumo": 0,
    "previous_measure": 0,
    "actual_measure": 0,
}

# Agua Caliente: meter readings used to verify consumption.
_HOT_WATER_STATUS_ATTR_DEFAULTS: dict[str, Any] = {
    "previous_measure": 0,
    "actual_measure": 0,
}

_SERVICE_TYPE_STATUS_ATTR_DEFAULTS: dict[str, dict[str, Any]] = {
    SERVICE_TYPE_WATER: _WATER_STATUS_ATTR_DEFAULTS,
    SERVICE_TYPE_GAS: _GAS_STATUS_ATTR_DEFAULTS,
    SERVICE_TYPE_ELECTRICITY: _ELECTRICITY_STATUS_ATTR_DEFAULTS,
    SERVICE_TYPE_COMMON_EXPENSES: _COMMON_EXPENSES_STATUS_ATTR_DEFAULTS,
    SERVICE_TYPE_HOT_WATER: _HOT_WATER_STATUS_ATTR_DEFAULTS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services binary sensors.

    One status binary sensor is created for every subentry (service device),
    each associated with its own subentry so it appears correctly grouped
    in the Home Assistant device registry under the Diagnostic category.
    """
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        async_add_entities(
            [
                ConciergeServiceStatusBinarySensor(
                    coordinator, config_entry, subentry_id, subentry.data
                )
            ],
            config_subentry_id=subentry_id,  # type: ignore[call-arg]
        )


class ConciergeServiceStatusBinarySensor(
    CoordinatorEntity[ConciergeServicesCoordinator], BinarySensorEntity
):
    """Binary sensor showing whether there is a problem fetching a service's bill data.

    ``is_on = True``  → problem   (no bill data found for this service)
    ``is_on = False`` → no problem (bill data retrieved successfully)

    Device class: PROBLEM — displayed as "Problem / No problem" in the UI.
    Entity category: DIAGNOSTIC — appears in the device Diagnostic panel.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:file-document-outline"

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the status binary sensor."""
        super().__init__(coordinator)
        self._subentry_id = subentry_id
        self._service_id = normalize_service_id(subentry_data.get(CONF_SERVICE_ID, subentry_id))
        # Use service_id (human-readable slug) as fallback, never the raw subentry UUID
        self._service_name = subentry_data.get(
            CONF_SERVICE_NAME,
            self._service_id.replace("_", " ").title(),
        )
        self._subentry_data = subentry_data
        self._config_entry = config_entry
        self._attr_name = f"Concierge {self._service_id} Status"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_status"

        # Linked to the same service device as the companion sensor entities.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{subentry_id}")},
            name=self._service_name,
            manufacturer="Concierge Services",
            model="Service Account",
        )

    @property
    def is_on(self) -> bool:
        """Return True when there is a problem with this service's bill data.

        A problem is reported when:
        - No coordinator data is available.
        - No bill data has been found for this service.
        - The most recent bill is older than one calendar month.
        """
        if not self.coordinator.data:
            return True
        service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
        if not service_data:
            return True
        last_updated: datetime | None = service_data.get("last_updated")
        if last_updated is None:
            return True
        now = datetime.now(timezone.utc)
        # Ensure last_updated is timezone-aware before comparing.
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        return last_updated + relativedelta(months=1) < now

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        Universal attributes are always present with a default of ``0``.
        consumption, consumption_unit, total_amount, and cost_per_* attributes
        are omitted here — they are exposed through dedicated sensor entities.
        Service-type-specific attributes (minus cost_per_*) are included only
        for the matching service type.
        """
        service_type = self._subentry_data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)

        attrs: dict[str, Any] = {
            "service_id": self._service_id,
            "service_name": self._service_name,
            "service_type": service_type,
            "folio": 0,
            "billing_period_start": 0,
            "billing_period_end": 0,
            "customer_number": 0,
            "address": 0,
            "due_date": 0,
            "icon": "mdi:file-document-outline",
            "friendly_name": self._service_name,
        }

        # Service-type-specific attributes — only for the matching type.
        type_specific_defaults = _SERVICE_TYPE_STATUS_ATTR_DEFAULTS.get(service_type, {})
        attrs.update(type_specific_defaults)

        if self.coordinator.data:
            service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
            if service_data:
                extracted_attrs = service_data.get("attributes", {})
                if extracted_attrs:
                    # Override universal attributes with extracted values.
                    for key in _STATUS_COMMON_ATTRS:
                        value = extracted_attrs.get(key)
                        if value is not None:
                            attrs[key] = value

                    # Override service-type-specific attributes.
                    for key in type_specific_defaults:
                        value = extracted_attrs.get(key)
                        if value is not None:
                            attrs[key] = value

                    # Include pdf_path when a bill PDF was downloaded.
                    if pdf_path := extracted_attrs.get("pdf_path"):
                        attrs["pdf_path"] = pdf_path

        return attrs
