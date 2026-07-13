"""Sensor platform for Concierge Services."""
from __future__ import annotations

import email
import hashlib
import imaplib
import json
import logging
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    ADDON_API_PORT,
    ADDON_API_URL,
    ADDON_CHECK_DELAY_SECONDS,
    ADDON_COMMON_EXPENSES_TEMPLATE_ID,
    ADDON_NOTIFICATION_ID,
    ADDON_SLUG,
    ADDON_STARTUP_TIMEOUT_SECONDS,
    ADDON_STATUS_INSTALLED,
    ADDON_STATUS_NOT_INSTALLED,
    ADDON_STATUS_OPTIONS,
    ADDON_STATUS_RUNNING,
    ADDON_STATUS_STARTING,
    ADDON_STATUS_UNKNOWN,
    ADDON_STATUS_UNSUPPORTED,
    CONF_EMAIL,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_OCRSPACE_API_KEY,
    CONF_PASSWORD,
    CONF_SAMPLE_FROM,
    CONF_SAMPLE_SUBJECT,
    CONF_SERVICE_ID,
    CONF_SERVICE_NAME,
    CONF_SERVICE_TYPE,
    DOMAIN,
    JSON_SUBDIR,
    PDF_MAX_FILES,
    PDF_MAX_AGE_DAYS,
    PDF_SUBDIR,
    SERVICE_TYPE_COMMON_EXPENSES,
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_HOT_WATER,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)
from .attribute_extractor import (
    CONF_SCORE_DERIVED,
    CONF_SCORE_OVERRIDE,
    extract_attributes_from_addon_ocr_json,
    extract_attributes_from_email_body,
    extract_attributes_from_pdf,
    _strip_html,
)
from .pdf_downloader import delete_service_pdfs, download_pdf_from_email, purge_old_pdfs
from .service_detector import (
    SERVICE_PATTERNS,
    classify_service_type,
    normalize_service_id,
)
from .task_logbook import async_log_task

_LOGGER = logging.getLogger(__name__)

# Notification ID for the persistent OCR-unavailable notification.
_OCR_NOTIFICATION_ID = "concierge_ocr_unavailable"
_GC_TEMPLATE_MISMATCH_NOTIFICATION_ID = "concierge_gc_template_mismatch"
_GITHUB_ISSUES_URL = "https://github.com/Geek-MD/Concierge_HA_Integration/issues"
_INTEGRATION_VERSION = "unknown"
try:
    _manifest = json.loads(
        (
            Path(__file__).resolve().parent / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    _INTEGRATION_VERSION = str(_manifest.get("version", "unknown"))
except (OSError, ValueError, TypeError):
    pass

# Update interval for checking mail server connection
SCAN_INTERVAL = timedelta(minutes=30)

# Consumption unit of measure by service type.
_CONSUMPTION_UNITS: dict[str, str] = {
    SERVICE_TYPE_GAS: "m³",
    SERVICE_TYPE_WATER: "m³",
    SERVICE_TYPE_ELECTRICITY: "kWh",
    SERVICE_TYPE_HOT_WATER: "m³",
}

# The extracted attribute key that holds the cost-per-unit value, per service type.
_COST_PER_UNIT_ATTR: dict[str, str] = {
    SERVICE_TYPE_GAS: "cost_per_m3s",
    SERVICE_TYPE_ELECTRICITY: "cost_per_kwh",
    SERVICE_TYPE_HOT_WATER: "hot_water_cost_per_m3",
}

# Unit of measure for cost-per-unit sensors, per service type.
_COST_PER_UNIT_UNITS: dict[str, str] = {
    SERVICE_TYPE_GAS: "$/m³",
    SERVICE_TYPE_ELECTRICITY: "$/kWh",
    SERVICE_TYPE_HOT_WATER: "$/m³",
}

# The extracted attribute key that represents the *primary* total amount for
# each service type.  For common-expenses the main payable is the apartment's
# GC + fondos subtotal; for hot-water it is the agua-caliente amount.
# All other service types use the generic ``total_amount`` key.
_TOTAL_AMOUNT_ATTR: dict[str, str] = {
    SERVICE_TYPE_COMMON_EXPENSES: "gc_total",
    SERVICE_TYPE_HOT_WATER: "hot_water_amount",
}

# Billing-breakdown sensor definitions per service type.
# Each tuple: (extracted_attr_key, name_suffix, unit, unique_id_suffix)
# Used by ConciergeServiceBillingBreakdownSensor (one instance per row).

# Water billing-breakdown sensors.
# cost_per_unit_non_peak, cost_per_unit_peak,
# cubic_meter_collection, cubic_meter_treatment, subtotal and total_amount are
# formula-derived (see _recompute_water_derived_attrs).
# water_consumption (combined potable-water charge) is kept as an internal
# attribute for the subtotal formula but is not exposed as a sensor.
_WATER_SPECIFIC_SENSORS: list[tuple[str, str, str, str]] = [
    ("fixed_charge",                "Fixed Charge",               "$",     "water_fixed_charge"),
    ("cost_per_unit_non_peak",      "Cost Per Unit Non Peak",     "$/m³",  "water_cost_per_unit_non_peak"),
    ("cost_per_unit_peak",          "Cost Per Unit Peak",         "$/m³",  "water_cost_per_unit_peak"),
    ("cubic_meter_collection",      "Cubic Meter Collection",     "$/m³",  "water_cubic_meter_collection"),
    ("cubic_meter_treatment",       "Cubic Meter Treatment",      "$/m³",  "water_cubic_meter_treatment"),
    ("water_consumption_non_peak_m3", "Water Non Peak m³",        "m³",    "water_non_peak_m3"),
    ("water_consumption_non_peak",  "Water Non Peak Charge",      "$",     "water_non_peak_charge"),
    ("water_consumption_peak_m3",   "Water Peak m³",              "m³",    "water_peak_m3"),
    ("water_consumption_peak",      "Water Peak Charge",          "$",     "water_peak_charge"),
    ("wastewater_recolection",      "Wastewater Recolection",     "$",     "wastewater_recolection"),
    ("wastewater_treatment",        "Wastewater Treatment",       "$",     "wastewater_treatment"),
    ("subtotal",                    "Subtotal",                   "$",     "water_subtotal"),
    ("other_charges",               "Other Charges",              "$",     "water_other_charges"),
]

# Electricity: billing charge breakdown (all CLP amounts).
_ELECTRICITY_SPECIFIC_SENSORS: list[tuple[str, str, str, str]] = [
    ("service_administration", "Service Administration", "$", "electricity_service_administration"),
    ("electricity_transport",  "Electricity Transport",  "$", "electricity_transport"),
    ("stabilization_fund",     "Stabilization Fund",     "$", "electricity_stabilization_fund"),
    ("electricity_consumption","Electricity Consumption", "$", "electricity_consumption_charge"),
]

# Common expenses: billing breakdown (all CLP amounts).
# gastos_comunes_amount is exposed as "Bill" so the entity ID becomes
# sensor.concierge_{service_id}_bill — the primary payable for the apartment.
# The "Total" sensor (gc_total) covers the GC-only total: Subtotal + Cargo Fijo.
_COMMON_EXPENSES_SPECIFIC_SENSORS: list[tuple[str, str, str, str]] = [
    ("gastos_comunes_amount",       "Bill",                       "$",  "gc_bill"),
    ("funds_provision",             "Funds Provision",            "$",  "gc_funds_provision"),
    ("subtotal",                    "Subtotal",                   "$",  "gc_subtotal"),
    ("fixed_charge",                "Fixed Charge",               "$",  "gc_fixed_charge"),
    ("gc_total",                    "Total",                      "$",  "gc_total"),
]

# Agua Caliente (hot-water) sensors grouped under the Gastos Comunes device.
# These are extracted from the same "Nota de Cobro" PDF text layer (Tier 1).
# All five attributes are set by _extract_common_expenses_pdf_attributes.
_COMMON_EXPENSES_HOT_WATER_SENSORS: list[tuple[str, str, str, str]] = [
    ("hot_water_consumption",  "Hot Water Consumption",  "m³",   "gc_hw_consumption"),
    ("hot_water_cost_per_m3",  "Hot Water Cost Per Unit","$/m³",  "gc_hw_cost_per_m3"),
    ("hot_water_amount",       "Hot Water Amount",       "$",     "gc_hw_amount"),
    ("hot_water_reading_prev", "Hot Water Prev Reading", "m³",    "gc_hw_prev_reading"),
    ("hot_water_reading_curr", "Hot Water Curr Reading", "m³",    "gc_hw_curr_reading"),
]

# Reverse mapping: unique_id suffix → extracted attribute key.
# Used by the set_value service to infer the attribute key from an entity when
# the caller does not specify it explicitly.
_UID_SUFFIX_TO_ATTR_KEY: dict[str, str] = {
    uid_suffix: attr_key
    for attr_key, _, _, uid_suffix in (
        _WATER_SPECIFIC_SENSORS
        + _ELECTRICITY_SPECIFIC_SENSORS
        + _COMMON_EXPENSES_SPECIFIC_SENSORS
        + _COMMON_EXPENSES_HOT_WATER_SENSORS
    )
}


def attr_key_from_uid_suffix(uid_suffix: str, service_type: str) -> str:
    """Return the extracted-attribute key for a sensor's unique_id suffix.

    For billing-breakdown sensors the uid_suffix → attr_key mapping is looked
    up in ``_UID_SUFFIX_TO_ATTR_KEY``.  For the generic ``total_amount``
    sensor the attribute key is service-type-specific (see
    ``_TOTAL_AMOUNT_ATTR``).  Any other suffix is returned as-is.
    """
    if uid_suffix in _UID_SUFFIX_TO_ATTR_KEY:
        return _UID_SUFFIX_TO_ATTR_KEY[uid_suffix]
    if uid_suffix == "total_amount":
        return _TOTAL_AMOUNT_ATTR.get(service_type, "total_amount")
    return uid_suffix


# Webmail provider domains that are too generic for sender-domain matching.
# Emails forwarded through these services carry the forwarder's address, not
# the original utility company's address.
_GENERIC_WEBMAIL_DOMAINS = frozenset({
    "gmail", "hotmail", "yahoo", "outlook", "live", "icloud", "protonmail",
})

# Words that are too common in billing-email subjects to be useful as unique
# service identifiers.  Only alphabetic words with 4+ characters are
# considered; month names are included because they appear in every monthly
# bill regardless of the service provider.
_SUBJECT_SKIP_WORDS = frozenset({
    "fwd", "cuenta", "factura", "boleta", "pago", "este", "esta",
    "mes", "del", "para", "con", "las", "los", "que", "reenvio",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
})


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services sensors.

    Two hub sensors (connection status and addon status) are created for the
    main config entry and linked to the hub device so they appear on the
    integration configuration page.
    Per-subentry sensors:
    - All service types except common_expenses: last_update, consumption,
      total_amount.
    - Common expenses: last_update + breakdown sensors (bill, funds_provision,
      subtotal, fixed_charge, total) + 5 Agua Caliente sensors (consumption,
      cost_per_unit, amount, prev_reading, curr_reading).  No total_amount —
      it would duplicate the "total" (gc_total) breakdown sensor.
    - Gas: cost_per_unit (generic $/unit sensor).
    - Electricity: cost_per_unit + 4 billing-breakdown sensors
      (service_administration, electricity_transport, stabilization_fund,
      electricity_consumption).
    - Water: generic cost_per_unit is complemented by granular water billing
      sensors, including separate no-punta/punta unit-cost entities and other
      water billing breakdown fields.  cost_per_unit_non_peak,
      cost_per_unit_peak, cost_per_unit, cubic_meter_collection,
      cubic_meter_treatment, subtotal, and total_amount are formula-derived.
    Each entity is associated with its own subentry so it appears correctly
    grouped in the HA device registry.
    """
    # The coordinator is initialised in __init__.async_setup_entry before the
    # platforms are forwarded, so it is always present at this point.
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    # Hub sensors: connection status and addon status, both linked to the hub
    # device so they appear on the integration configuration page.
    async_add_entities([
        ConciergeServicesConnectionSensor(coordinator, config_entry),
        ConciergeAddonStatusSensor(coordinator, config_entry),
    ])

    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        service_type = subentry.data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)

        entities: list[SensorEntity] = [
            ConciergeServiceLastUpdateSensor(
                coordinator, config_entry, subentry_id, subentry.data
            ),
        ]

        # Common-expenses already exposes gc_total via the breakdown sensor
        # ("Total"), so the generic total_amount sensor would be a duplicate.
        if service_type != SERVICE_TYPE_COMMON_EXPENSES:
            entities.append(
                ConciergeServiceTotalAmountSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                )
            )

        # Common-expenses device has no meaningful "consumption" metric —
        # the billing unit is monetary only.  All other service types
        # include a consumption sensor.
        if service_type != SERVICE_TYPE_COMMON_EXPENSES:
            entities.insert(
                1,
                ConciergeServiceConsumptionSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
            )

        if service_type == SERVICE_TYPE_WATER:
            # Water services expose additional billing-breakdown sensors,
            # including granular no-punta/punta unit-cost sensors.
            for attr_key, name_suffix, unit, uid_suffix in _WATER_SPECIFIC_SENSORS:
                entities.append(
                    ConciergeServiceBillingBreakdownSensor(
                        coordinator,
                        config_entry,
                        subentry_id,
                        subentry.data,
                        attr_key=attr_key,
                        name_suffix=name_suffix,
                        unit=unit,
                        uid_suffix=uid_suffix,
                    )
                )
        elif service_type == SERVICE_TYPE_COMMON_EXPENSES:
            # Common expenses expose billing breakdown sensors (GC amounts).
            # No consumption or cost-per-unit sensor — the breakdown is by
            # monetary amounts only.
            for attr_key, name_suffix, unit, uid_suffix in _COMMON_EXPENSES_SPECIFIC_SENSORS:
                entities.append(
                    ConciergeServiceBillingBreakdownSensor(
                        coordinator,
                        config_entry,
                        subentry_id,
                        subentry.data,
                        attr_key=attr_key,
                        name_suffix=name_suffix,
                        unit=unit,
                        uid_suffix=uid_suffix,
                    )
                )
            # Agua Caliente is a sub-account within Gastos Comunes: its data
            # comes from the same "Nota de Cobro" PDF.  Expose dedicated sensors
            # on the same GC device so they are grouped together in HA.
            for attr_key, name_suffix, unit, uid_suffix in _COMMON_EXPENSES_HOT_WATER_SENSORS:
                entities.append(
                    ConciergeServiceBillingBreakdownSensor(
                        coordinator,
                        config_entry,
                        subentry_id,
                        subentry.data,
                        attr_key=attr_key,
                        name_suffix=name_suffix,
                        unit=unit,
                        uid_suffix=uid_suffix,
                    )
                )
        elif service_type == SERVICE_TYPE_HOT_WATER:
            # Hot-water device exposes cost-per-m³ sensor (consumption is
            # already included in the common base entities above).
            entities.append(
                ConciergeServiceCostPerUnitSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                )
            )
        else:
            # Gas and electricity expose a single cost-per-unit sensor.
            entities.append(
                ConciergeServiceCostPerUnitSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                )
            )
            # Electricity also exposes billing-charge breakdown sensors.
            if service_type == SERVICE_TYPE_ELECTRICITY:
                for attr_key, name_suffix, unit, uid_suffix in _ELECTRICITY_SPECIFIC_SENSORS:
                    entities.append(
                        ConciergeServiceBillingBreakdownSensor(
                            coordinator,
                            config_entry,
                            subentry_id,
                            subentry.data,
                            attr_key=attr_key,
                            name_suffix=name_suffix,
                            unit=unit,
                            uid_suffix=uid_suffix,
                        )
                    )

        async_add_entities(
            entities,
            config_subentry_id=subentry_id,  # type: ignore[call-arg]
        )


class ConciergeServicesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Concierge Services data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        effective_cfg: dict[str, Any],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.config_entry = config_entry
        self._cfg = effective_cfg
        self._pdf_dir: str = hass.config.path(PDF_SUBDIR)
        self._json_dir: str = hass.config.path(JSON_SUBDIR)
        self._gc_template_alert_fingerprint: str | None = None
        # Tracks whether the Concierge addon is available. None = not yet checked.
        self._addon_available: bool | None = None
        self._addon_api_url: str = ADDON_API_URL
        # Version string reported by GET /status (addon v0.3.1+). None when the
        # /status endpoint is not available (older addon) or addon is not running.
        self._addon_version: str | None = None
        # Current addon lifecycle status exposed by ConciergeAddonStatusSensor.
        # Starts as "unknown" and is updated by _async_manage_addon_notification.
        self._addon_status: str = ADDON_STATUS_UNKNOWN
        # Last addon notification reason shown to the user. This avoids
        # recreating the same notification on every recursive check after the
        # user dismisses it manually.
        self._addon_notification_reason: str | None = None
        # Timestamp when the addon first entered a starting/started-but-unready
        # state so we can enforce the 5-minute startup timeout.
        self._addon_start_wait_since: datetime | None = None
        # Earliest moment at which the addon check may fire.  None means HA
        # has not yet fired EVENT_HOMEASSISTANT_STARTED.  A datetime means
        # "suppress addon checks until this timestamp" (5-minute post-start
        # delay to allow Supervisor data to fully populate).
        self._addon_check_not_before: datetime | None = None

        def _arm_addon_check_delay() -> None:
            """Set the not-before timestamp and schedule a deferred refresh."""
            self._addon_check_not_before = (
                dt_util.utcnow() + timedelta(seconds=ADDON_CHECK_DELAY_SECONDS)
            )

            @callback
            def _trigger_deferred_refresh(_now: datetime) -> None:
                hass.async_create_task(
                    self.async_refresh(),
                    "concierge_ha_integration_addon_check_after_ha_start",
                )

            async_call_later(hass, ADDON_CHECK_DELAY_SECONDS, _trigger_deferred_refresh)

        if hass.state == CoreState.running:
            _arm_addon_check_delay()
        else:
            @callback
            def _on_ha_started(_event: Event) -> None:
                _arm_addon_check_delay()

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)

    @property
    def addon_status(self) -> str:
        """Return the current Concierge OCR addon lifecycle status."""
        return self._addon_status

    @property
    def addon_version(self) -> str | None:
        """Return the version reported by GET /status (addon v0.3.1+), or None."""
        return self._addon_version

    async def async_set_manual_value(
        self, subentry_id: str, attribute: str, value: Any
    ) -> None:
        """Overwrite an attribute value in the coordinator's in-memory data.

        The value is applied immediately so all entities update without waiting
        for the next polling cycle.  The override is in-memory only and does
        not persist across HA restarts or force-refresh scans.

        After applying the value, formula-based derived attributes (e.g.
        ``gc_total = subtotal_departamento + cargo_fijo``) are recomputed so
        that dependent sensor entities update immediately.
        """
        _LOGGER.info(
            "Concierge Services: set_value applied — subentry=%s, %s=%r",
            subentry_id,
            attribute,
            value,
        )

        # Update in-memory coordinator data so entities refresh immediately.
        if self.data is not None:
            service_data = self.data.get("services", {}).get(subentry_id)
            if service_data is not None:
                attrs = service_data.setdefault("attributes", {})
                attrs[attribute] = value
                attrs.setdefault("_confidence", {})[attribute] = CONF_SCORE_OVERRIDE
                # Recompute any formula-derived attributes that depend on the
                # attribute that was just overridden.
                subentry = self.config_entry.subentries.get(subentry_id)  # type: ignore[attr-defined,union-attr]
                service_type = (
                    subentry.data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
                    if subentry
                    else SERVICE_TYPE_UNKNOWN
                )
                if service_type == SERVICE_TYPE_WATER:
                    self._recompute_water_derived_attrs(attrs)
                else:
                    self._recompute_gc_derived_attrs(attrs)
                self.async_set_updated_data(self.data)

    def _recompute_gc_derived_attrs(self, attrs: dict[str, Any]) -> None:
        """Recompute common-expenses formula-derived attributes in-place.

        Re-runs the same alias syncs and arithmetic derivations that
        :func:`attribute_extractor._extract_common_expenses_pdf_attributes`
        applies after extraction, so that entities reporting calculated values
        (e.g. ``gc_total = subtotal_departamento + cargo_fijo``) update
        immediately when a constituent attribute is overridden via the
        ``set_value`` service.

        Attributes that were themselves explicitly overridden by the user
        (confidence == CONF_SCORE_OVERRIDE) are never overwritten.
        """
        confidence = attrs.setdefault("_confidence", {})

        def _is_overridden(key: str) -> bool:
            return confidence.get(key, 0) >= CONF_SCORE_OVERRIDE

        # --- Alias pairs: keep both ends in sync ---
        # fixed_charge ↔ cargo_fijo
        if "fixed_charge" in attrs and not _is_overridden("cargo_fijo"):
            attrs["cargo_fijo"] = attrs["fixed_charge"]
            confidence["cargo_fijo"] = confidence.get("fixed_charge", CONF_SCORE_DERIVED)
        elif "cargo_fijo" in attrs and not _is_overridden("fixed_charge"):
            attrs["fixed_charge"] = attrs["cargo_fijo"]
            confidence["fixed_charge"] = confidence.get("cargo_fijo", CONF_SCORE_DERIVED)

        # subtotal ↔ subtotal_departamento
        if "subtotal" in attrs and not _is_overridden("subtotal_departamento"):
            attrs["subtotal_departamento"] = attrs["subtotal"]
            confidence["subtotal_departamento"] = confidence.get("subtotal", CONF_SCORE_DERIVED)
        elif "subtotal_departamento" in attrs and not _is_overridden("subtotal"):
            attrs["subtotal"] = attrs["subtotal_departamento"]
            confidence["subtotal"] = confidence.get("subtotal_departamento", CONF_SCORE_DERIVED)

        # funds_provision ↔ fondos_amount
        if "funds_provision" in attrs and not _is_overridden("fondos_amount"):
            attrs["fondos_amount"] = attrs["funds_provision"]
            confidence["fondos_amount"] = confidence.get("funds_provision", CONF_SCORE_DERIVED)
        elif "fondos_amount" in attrs and not _is_overridden("funds_provision"):
            attrs["funds_provision"] = attrs["fondos_amount"]
            confidence["funds_provision"] = confidence.get("fondos_amount", CONF_SCORE_DERIVED)

        # --- Formula: gc_total = subtotal_departamento + cargo_fijo ---
        subtotal_val = attrs.get("subtotal_departamento")
        cargo_val = attrs.get("cargo_fijo")
        total_val = attrs.get("total_amount")
        subtotal_consumo_val = attrs.get("subtotal_consumo")
        if not _is_overridden("gc_total"):
            if subtotal_val is not None and cargo_val is not None:
                attrs["gc_total"] = subtotal_val + cargo_val
                confidence["gc_total"] = CONF_SCORE_DERIVED
            elif subtotal_val is not None and cargo_val is None:
                attrs["gc_total"] = subtotal_val
                confidence["gc_total"] = CONF_SCORE_DERIVED
            elif total_val is not None and subtotal_consumo_val is not None:
                attrs["gc_total"] = total_val - subtotal_consumo_val
                confidence["gc_total"] = CONF_SCORE_DERIVED
            elif total_val is not None:
                attrs["gc_total"] = total_val
                confidence["gc_total"] = CONF_SCORE_DERIVED

    def _recompute_water_derived_attrs(self, attrs: dict[str, Any]) -> None:
        """Recompute water-service formula-derived attributes in-place.

        Computes the following attributes from the primary extracted values:

        - ``cost_per_unit_non_peak`` = ``water_consumption_non_peak /
                                       water_consumption_non_peak_m3``
        - ``cost_per_unit_peak``     = ``water_consumption_peak /
                                       water_consumption_peak_m3``
        - ``cubic_meter_collection`` = ``wastewater_recolection / consumption``
        - ``cubic_meter_treatment``  = ``wastewater_treatment / consumption``
        - ``subtotal``               = ``fixed_charge + water_consumption_non_peak
                                       + water_consumption_peak + wastewater_recolection
                                       + wastewater_treatment``
        - ``total_amount``           = ``subtotal + other_charges``

        All results are rounded to 2 decimal places for the rate sensors and
        left as integers for the monetary totals.

        Attributes that were explicitly overridden by the user (confidence ==
        CONF_SCORE_OVERRIDE) are never overwritten.
        """
        confidence = attrs.setdefault("_confidence", {})

        def _is_overridden(key: str) -> bool:
            return confidence.get(key, 0) >= CONF_SCORE_OVERRIDE

        non_peak_m3 = attrs.get("water_consumption_non_peak_m3")
        non_peak_amt = attrs.get("water_consumption_non_peak")
        if (
            non_peak_m3
            and non_peak_amt is not None
            and not _is_overridden("cost_per_unit_non_peak")
        ):
            attrs["cost_per_unit_non_peak"] = round(non_peak_amt / non_peak_m3, 2)
            confidence["cost_per_unit_non_peak"] = CONF_SCORE_DERIVED

        peak_m3 = attrs.get("water_consumption_peak_m3")
        peak_amt = attrs.get("water_consumption_peak")
        if peak_m3 and peak_amt is not None and not _is_overridden("cost_per_unit_peak"):
            attrs["cost_per_unit_peak"] = round(peak_amt / peak_m3, 2)
            confidence["cost_per_unit_peak"] = CONF_SCORE_DERIVED

        consumption = attrs.get("consumption")

        if (
            non_peak_amt is not None
            and peak_amt is not None
            and not _is_overridden("water_consumption")
        ):
            attrs["water_consumption"] = non_peak_amt + peak_amt
            confidence["water_consumption"] = CONF_SCORE_DERIVED

        if consumption:
            ww_recol = attrs.get("wastewater_recolection")
            if ww_recol is not None and not _is_overridden("cubic_meter_collection"):
                attrs["cubic_meter_collection"] = round(ww_recol / consumption, 2)
                confidence["cubic_meter_collection"] = CONF_SCORE_DERIVED

            ww_treat = attrs.get("wastewater_treatment")
            if ww_treat is not None and not _is_overridden("cubic_meter_treatment"):
                attrs["cubic_meter_treatment"] = round(ww_treat / consumption, 2)
                confidence["cubic_meter_treatment"] = CONF_SCORE_DERIVED

        # --- Formula: subtotal = water_consumption + wastewater_recolection
        #              + wastewater_treatment + fixed_charge ---
        water_cons = attrs.get("water_consumption")
        if water_cons is None and non_peak_amt is not None and peak_amt is not None:
            water_cons = non_peak_amt + peak_amt
        ww_recol = attrs.get("wastewater_recolection")
        ww_treat = attrs.get("wastewater_treatment")
        fixed = attrs.get("fixed_charge")
        if (
            water_cons is not None
            and ww_recol is not None
            and ww_treat is not None
            and fixed is not None
            and not _is_overridden("subtotal")
        ):
            subtotal = water_cons + ww_recol + ww_treat + fixed
            attrs["subtotal"] = subtotal
            confidence["subtotal"] = CONF_SCORE_DERIVED

            # --- Formula: total_amount = subtotal + other_charges ---
            other = attrs.get("other_charges")
            if other is not None and not _is_overridden("total_amount"):
                attrs["total_amount"] = subtotal + other
                confidence["total_amount"] = CONF_SCORE_DERIVED

    async def async_recompute_derived(self, subentry_id: str) -> None:
        """Recompute all formula-derived attributes for a single service subentry.

        Reads the current coordinator data for *subentry_id*, runs
        :meth:`_recompute_gc_derived_attrs` on the extracted attributes in-place,
        and pushes the updated state to all listening entities via
        :meth:`async_set_updated_data`.

        This method is the single authoritative place for derived-attribute
        recomputation.  It is called:

        - As the **final step** of :meth:`async_refresh_service` after the fresh
          email/PDF data has already been stored in the coordinator — ensuring
          formula sensors (e.g. ``gc_total = subtotal_departamento + cargo_fijo``)
          always reflect the latest extracted values.
        - Directly by the per-device **Recalculate** button entity, so users can
          trigger a recomputation without a full email re-scan (e.g. after a
          manual ``set_value`` override or when they suspect a derived sensor is
          stale).

        All changes are logged at ``INFO`` level; an unchanged recomputation is
        logged at ``DEBUG`` level.
        """
        if self.data is None:
            _LOGGER.warning(
                "Concierge Services: recompute_derived called but coordinator "
                "data is None — subentry=%s — skipping",
                subentry_id,
            )
            return

        service_data = self.data.get("services", {}).get(subentry_id)
        if service_data is None:
            _LOGGER.warning(
                "Concierge Services: recompute_derived called but no data "
                "found for subentry=%s — skipping",
                subentry_id,
            )
            return

        attrs = service_data.get("attributes", {})

        # Snapshot current non-metadata values so we can log only what changed.
        before = {k: attrs[k] for k in attrs if not k.startswith("_")}

        # Determine service type so we call the correct recompute helper.
        subentry = self.config_entry.subentries.get(subentry_id)  # type: ignore[attr-defined,union-attr]
        service_type = (
            subentry.data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
            if subentry
            else SERVICE_TYPE_UNKNOWN
        )

        if service_type == SERVICE_TYPE_WATER:
            self._recompute_water_derived_attrs(attrs)
        else:
            self._recompute_gc_derived_attrs(attrs)

        changed = {
            k: attrs[k]
            for k in attrs
            if not k.startswith("_")
            and (k not in before or before[k] != attrs[k])
        }

        if changed:
            _LOGGER.info(
                "Concierge Services: derived attributes recomputed — "
                "subentry=%s, changes: %s",
                subentry_id,
                ", ".join(f"{k}={v!r}" for k, v in changed.items()),
            )
        else:
            _LOGGER.debug(
                "Concierge Services: derived attributes recomputed — "
                "subentry=%s, no changes",
                subentry_id,
            )

        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from IMAP server."""
        async_log_task(self.hass, "Ciclo automático de lectura de correos iniciado")

        # Check Concierge addon availability once per update cycle and manage
        # the persistent notification that guides users to install it.
        await self._async_manage_addon_notification()

        try:
            result = await self.hass.async_add_executor_job(self._fetch_service_data)
        except Exception as err:
            async_log_task(self.hass, f"Ciclo automático falló: {err}")
            raise UpdateFailed(f"Error communicating with IMAP server: {err}") from err

        services = result.get("services", {})
        service_count = len(services) if isinstance(services, dict) else 0
        without_match = 0
        if isinstance(services, dict):
            without_match = sum(
                1
                for service_data in services.values()
                if isinstance(service_data, dict)
                and service_data.get("last_updated") is None
            )
        async_log_task(
            self.hass,
            (
                "Ciclo automático completado: "
                f"{service_count} servicio(s), {without_match} sin coincidencia"
            ),
        )
        self._manage_ocr_repair_issue()
        self._manage_gc_template_mismatch_notification(result)
        return result

    def _manage_ocr_repair_issue(self) -> None:
        """Dismiss legacy OCR-space requirement notices.

        OCR.space API key setup is no longer treated as a configuration
        requirement, so previously raised notices are proactively cleared.
        """
        ir.async_delete_issue(self.hass, DOMAIN, "ocr_unavailable")
        persistent_notification.async_dismiss(self.hass, _OCR_NOTIFICATION_ID)

    @staticmethod
    def _sync_check_addon(addon_url: str) -> bool:
        """Check the Concierge addon health endpoint synchronously (runs in executor).

        Returns ``True`` when the addon is reachable and reports ``{"status": "ok"}``.
        """
        try:
            req = urllib.request.Request(
                f"{addon_url}/health",
                method="GET",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                if resp.status != 200:
                    return False
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok"
        except Exception:  # pylint: disable=broad-except
            return False

    @staticmethod
    def _sync_get_addon_status_response(addon_url: str) -> dict[str, Any] | None:
        """Call GET /status on the Concierge addon synchronously (runs in executor).

        Available since addon v0.3.1.  Returns the parsed JSON dict when the
        endpoint responds with HTTP 200, ``status == "ok"`` and ``running == True``.
        Returns ``None`` on any error (connection refused, timeout, unexpected
        payload, HTTP error), so callers can fall back to ``/health``.
        """
        try:
            req = urllib.request.Request(
                f"{addon_url}/status",
                method="GET",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                if resp.status != 200:
                    return None
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "ok" and data.get("running") is True:
                    return data
                return None
        except Exception:  # pylint: disable=broad-except
            return None

    def _get_supervisor_addon_status(self) -> tuple[str, str | None]:
        """Return the Supervisor addon lifecycle state and its hostname URL.

        Lifecycle states:
            - ``unsupported``: Home Assistant is not running with Supervisor.
            - ``unknown``: Supervisor metadata could not be queried.
            - ``not_installed``: The addon is absent from Supervisor metadata.
            - ``starting``: Supervisor reports the addon is still starting.
            - ``started``: Supervisor reports the addon has started.
            - ``stopped``: The addon exists but is not currently running.
        """
        if not is_hassio(self.hass):
            return ("unsupported", None)

        try:
            from homeassistant.components.hassio import (
                get_addons_info,
                get_supervisor_info,
            )
        except ImportError:
            return ("unknown", None)

        # In HA 2026.4+, get_supervisor_info (and other hassio helpers) raise
        # HassioNotReadyError instead of returning None when their data cache
        # hasn't been populated yet.  Treat any exception here as "data not yet
        # available" and suppress the notification to avoid false alarms.
        try:
            supervisor_info = get_supervisor_info(self.hass)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Concierge Services: Supervisor info not yet available; skipping addon check"
            )
            return ("unknown", None)

        if supervisor_info is None:
            # Older HA returns None when data isn't ready.
            _LOGGER.debug(
                "Concierge Services: Supervisor info not yet available; skipping addon check"
            )
            return ("unknown", None)

        # Obtain the list of installed addons.
        #
        # In HA 2026.4+, ``get_addons_list()`` is the dedicated API and the
        # "addons" key in supervisor_info is only a backwards-compat shim filled
        # from DATA_ADDONS_LIST.  When that coordinator hasn't refreshed yet,
        # the key is absent and ``supervisor_info.get("addons", [])`` would
        # return an empty list — incorrectly interpreted as "no addons
        # installed".  Prefer ``get_addons_list()`` so we get an exception (→
        # "unknown") rather than an empty list when data isn't ready yet.
        addon_list: list[Any] | None = None
        try:
            from homeassistant.components.hassio import get_addons_list as _get_addons_list
        except ImportError:
            pass  # older HA — fall back to supervisor_info["addons"] below
        else:
            try:
                addon_list = _get_addons_list(self.hass)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Concierge Services: Addon list not yet available; skipping addon check"
                )
                return ("unknown", None)

        if addon_list is None:
            # Older HA: the "addons" key is embedded directly in supervisor_info.
            # If it is absent (None), data hasn't loaded yet — suppress the check.
            addon_list = supervisor_info.get("addons")
            if addon_list is None:
                _LOGGER.debug(
                    "Concierge Services: Addon list not yet available; skipping addon check"
                )
                return ("unknown", None)

        if not isinstance(addon_list, list):
            _LOGGER.debug(
                "Concierge Services: Supervisor returned unexpected addons list type: %s",
                type(addon_list).__name__,
            )
            return ("unknown", None)

        # Supervisor returns addons with their *full* slug, which for third-party
        # repository addons follows the format ``{repo_slug}_{addon_slug}``
        # (e.g. ``geek_md_concierge_ocr`` instead of ``concierge_ocr``).
        # Perform a two-pass lookup: prefer an exact slug match so that a
        # short-slug entry is never shadowed by a repo-prefixed entry, and only
        # fall back to a suffix match when no exact match is found.
        basic_info = next(
            (
                a
                for a in addon_list
                if isinstance(a, dict)
                and a.get("slug") == ADDON_SLUG
            ),
            None,
        )
        if basic_info is None:
            basic_info = next(
                (
                    a
                    for a in addon_list
                    if isinstance(a, dict)
                    and str(a.get("slug", "")).endswith(f"_{ADDON_SLUG}")
                ),
                None,
            )
        if basic_info is None:
            return ("not_installed", None)

        # Use the actual full slug returned by Supervisor for subsequent API
        # lookups so that ``get_addons_info`` can find the addon even when the
        # Supervisor-assigned slug differs from the short slug in ADDON_SLUG.
        actual_slug = str(basic_info.get("slug") or ADDON_SLUG)

        addon_state = str(basic_info.get("state", "")).lower()

        # Try to obtain the hostname from the detailed addon info for a more
        # specific URL; fall back gracefully when it is unavailable.
        hostname: str | None = None
        try:
            detailed_info = (get_addons_info(self.hass) or {}).get(actual_slug)
        except Exception:  # noqa: BLE001
            detailed_info = None
        if isinstance(detailed_info, dict):
            hostname = detailed_info.get("hostname")
        addon_url = (
            f"http://{hostname}:{ADDON_API_PORT}"
            if isinstance(hostname, str) and hostname
            else None
        )

        if addon_state == "started":
            if addon_url is None:
                _LOGGER.debug(
                    "Concierge Services: addon '%s' started but Supervisor reported no hostname",
                    ADDON_SLUG,
                )
            return ("started", addon_url)

        if addon_state == "starting":
            return ("starting", addon_url)

        _LOGGER.debug(
            "Concierge Services: addon '%s' present in Supervisor but state is '%s'",
            ADDON_SLUG,
            addon_state or "unknown",
        )
        return ("stopped", addon_url)

    @staticmethod
    def _sync_ocr_pdf_via_addon(
        pdf_path: str,
        addon_url: str = ADDON_API_URL,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit *pdf_path* to the Concierge addon OCR API synchronously.

        Calls ``POST {addon_url}/ocr/source`` with ``source_type=local_path`` and
        ``source_value=<pdf_path>``.  Returns the parsed JSON response dict, or an
        empty dict when the call fails.

        This is designed to be called from an executor thread (blocking I/O).
        """
        try:
            form_data: dict[str, str] = {
                "source_type": "local_path",
                "source_value": pdf_path,
            }
            if template_id:
                form_data["template_id"] = template_id
            data = urllib.parse.urlencode(form_data).encode("utf-8")
            req = urllib.request.Request(
                f"{addon_url}/ocr/source",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Concierge addon OCR call failed for '%s': %s", pdf_path, err
            )
            return {}

    async def _async_manage_addon_notification(self) -> None:
        """Check addon lifecycle state and manage notifications accordingly."""
        def _dismiss_addon_notification() -> None:
            persistent_notification.async_dismiss(self.hass, ADDON_NOTIFICATION_ID)
            self._addon_notification_reason = None

        def _create_addon_notification_once(
            *,
            reason: str,
            title: str,
            message: str,
        ) -> None:
            if self._addon_notification_reason == reason:
                _LOGGER.debug(
                    "Concierge Services: addon notice '%s' already shown; skipping recreate",
                    reason,
                )
                return

            persistent_notification.async_create(
                self.hass,
                title=title,
                message=message,
                notification_id=ADDON_NOTIFICATION_ID,
            )
            self._addon_notification_reason = reason

        def _set_addon_status(new_status: str) -> None:
            """Update _addon_status and log the transition when the state changes."""
            if self._addon_status == new_status:
                return
            _LOGGER.info(
                "Concierge Services: addon status changed: '%s' → '%s'",
                self._addon_status,
                new_status,
            )
            self._addon_status = new_status
            self.async_update_listeners()
        # Suppress all addon checks until ADDON_CHECK_DELAY_SECONDS have elapsed
        # after HA fully started.  This avoids false "not installed" alarms while
        # Supervisor is still populating its addon data after a reboot.
        now = dt_util.utcnow()
        if self._addon_check_not_before is None or now < self._addon_check_not_before:
            remaining = (
                (self._addon_check_not_before - now).total_seconds()
                if self._addon_check_not_before is not None
                else ADDON_CHECK_DELAY_SECONDS
            )
            _LOGGER.debug(
                "Concierge Services: addon check suppressed; "
                "%.0f s remaining before first check",
                remaining,
            )
            return

        was_available = self._addon_available
        supervisor_state, supervisor_addon_url = self._get_supervisor_addon_status()

        # Without Supervisor, addons cannot be installed.  Skip the health
        # check entirely and dismiss any leftover notification.
        if supervisor_state == "unsupported":
            _LOGGER.debug(
                "Concierge Services: no Supervisor detected; skipping addon check"
            )
            self._addon_available = False
            self._addon_start_wait_since = None
            _set_addon_status(ADDON_STATUS_UNSUPPORTED)
            _dismiss_addon_notification()
            return

        candidate_urls: list[str] = []
        if supervisor_addon_url:
            candidate_urls.append(supervisor_addon_url)
        if ADDON_API_URL not in candidate_urls:
            candidate_urls.append(ADDON_API_URL)

        self._addon_available = False
        self._addon_api_url = ADDON_API_URL
        self._addon_version = None
        for candidate_url in candidate_urls:
            # Try GET /status first (available since addon v0.3.1).  A positive
            # response means the addon is running and reports its version.
            status_data = await self.hass.async_add_executor_job(
                self._sync_get_addon_status_response, candidate_url
            )
            if status_data is not None:
                self._addon_available = True
                self._addon_api_url = candidate_url
                self._addon_version = status_data.get("version")
                break
            # Fallback: GET /health for addon versions prior to v0.3.1.
            addon_health_ok = await self.hass.async_add_executor_job(
                self._sync_check_addon, candidate_url
            )
            if addon_health_ok:
                self._addon_available = True
                self._addon_api_url = candidate_url
                break

        if self._addon_available:
            self._addon_start_wait_since = None
            if was_available is not True:
                _LOGGER.info(
                    "Concierge Services: Concierge addon detected at %s%s; "
                    "using addon OCR for common-expenses and hot-water PDFs",
                    self._addon_api_url,
                    f" (v{self._addon_version})" if self._addon_version else "",
                )
            _set_addon_status(ADDON_STATUS_RUNNING)
            _dismiss_addon_notification()
            return

        now = dt_util.utcnow()

        if supervisor_state in {"starting", "started"}:
            if self._addon_start_wait_since is None:
                self._addon_start_wait_since = now
                _LOGGER.info(
                    "Concierge Services: addon '%s' is %s but its health endpoint "
                    "is not ready yet at %s; waiting up to %d seconds",
                    ADDON_SLUG,
                    supervisor_state,
                    candidate_urls,
                    ADDON_STARTUP_TIMEOUT_SECONDS,
                )

            elapsed = (now - self._addon_start_wait_since).total_seconds()
            if elapsed < ADDON_STARTUP_TIMEOUT_SECONDS:
                _LOGGER.debug(
                    "Concierge Services: addon '%s' still starting at %s "
                    "(%.0f s / %d s elapsed); suppressing notification",
                    ADDON_SLUG,
                    candidate_urls,
                    elapsed,
                    ADDON_STARTUP_TIMEOUT_SECONDS,
                )
                _set_addon_status(ADDON_STATUS_STARTING)
                _dismiss_addon_notification()
                return

            _LOGGER.warning(
                "Concierge Services: addon '%s' exceeded startup timeout at %s "
                "(%.0f s elapsed)",
                ADDON_SLUG,
                candidate_urls,
                elapsed,
            )
            _set_addon_status(ADDON_STATUS_STARTING)
            _create_addon_notification_once(
                reason="startup_timeout",
                title="Concierge — Addon de OCR con problemas de inicio",
                message=(
                    "La integración Concierge HA detectó que el addon **Concierge OCR API** "
                    "lleva más de 5 minutos arrancando y aún no está listo.\n\n"
                    "Revisa el estado y los logs del addon en Home Assistant. Mientras "
                    "tanto, la integración seguirá usando el extractor interno como respaldo."
                ),
            )
            return

        self._addon_start_wait_since = None

        if supervisor_state == "not_installed":
            _LOGGER.info(
                "Concierge Services: Concierge addon not installed; creating notice"
            )
            _set_addon_status(ADDON_STATUS_NOT_INSTALLED)
            _create_addon_notification_once(
                reason="not_installed",
                title="Concierge — Addon de OCR no instalado",
                message=(
                    "La integración Concierge HA detectó que el addon **Concierge OCR API** "
                    "no está instalado.\n\n"
                    "Para mejorar la extracción de datos de **Gastos Comunes** y **Agua "
                    "Caliente**, instala el addon desde el repositorio:\n\n"
                    "[https://github.com/Geek-MD/Concierge_addon]"
                    "(https://github.com/Geek-MD/Concierge_addon)\n\n"
                    "Una vez instalado y listo, esta notificación desaparecerá automáticamente."
                ),
            )
            return

        if supervisor_state == "stopped":
            _LOGGER.info(
                "Concierge Services: Concierge addon installed but not running; creating notice"
            )
            _set_addon_status(ADDON_STATUS_INSTALLED)
            _create_addon_notification_once(
                reason="stopped",
                title="Concierge — Addon de OCR detenido",
                message=(
                    "La integración Concierge HA detectó que el addon **Concierge OCR API** "
                    "está instalado, pero no está en ejecución.\n\n"
                    "Inícialo desde Home Assistant para habilitar el OCR mejorado de "
                    "**Gastos Comunes** y **Agua Caliente**."
                ),
            )
            return

        # State "unknown" means Supervisor data is not yet populated; suppress
        # any notification to avoid a misleading "no disponible" message.
        if supervisor_state == "unknown":
            _LOGGER.debug(
                "Concierge Services: supervisor state is '%s'; suppressing addon notification and clearing stale notice",
                supervisor_state,
            )
            _set_addon_status(ADDON_STATUS_UNKNOWN)
            _dismiss_addon_notification()
            return

        _LOGGER.info(
            "Concierge Services: addon not reachable at %s and Supervisor state is '%s'",
            candidate_urls,
            supervisor_state,
        )
        _set_addon_status(ADDON_STATUS_UNKNOWN)
        _create_addon_notification_once(
            reason="unreachable",
            title="Concierge — Addon de OCR no disponible",
            message=(
                "La integración Concierge HA no pudo conectarse al addon **Concierge OCR API**.\n\n"
                "Verifica que esté instalado y funcionando correctamente. Mientras tanto, "
                "la integración seguirá usando el extractor interno como respaldo."
            ),
        )

    def _manage_gc_template_mismatch_notification(
        self, coordinator_data: dict[str, Any]
    ) -> None:
        """Create/dismiss template-mismatch notification for common-expenses OCR drift."""
        services = coordinator_data.get("services", {})
        if not isinstance(services, dict):
            return

        reports: list[dict[str, Any]] = []
        for subentry_id, service_data in services.items():
            if not isinstance(service_data, dict):
                continue
            attrs = service_data.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            mismatch = attrs.get("_gc_template_mismatch")
            if not isinstance(mismatch, dict) or not mismatch.get("is_significant"):
                continue

            subentry = self.config_entry.subentries.get(subentry_id)  # type: ignore[attr-defined,union-attr]
            service_type = (
                subentry.data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
                if subentry
                else SERVICE_TYPE_UNKNOWN
            )
            if service_type != SERVICE_TYPE_COMMON_EXPENSES:
                continue

            service_name = (
                subentry.data.get(CONF_SERVICE_NAME, subentry_id)
                if subentry
                else subentry_id
            )
            reports.append(
                {
                    "subentry_id": subentry_id,
                    "service_name": service_name,
                    "anchor_coverage_pct": mismatch.get("anchor_coverage_pct"),
                    "missing_anchor_keys": mismatch.get("missing_anchor_keys", []),
                    "value_gap_keys": mismatch.get("value_gap_keys", []),
                    "unexpected_json_lines": mismatch.get("unexpected_json_lines", []),
                    "ocr_json_overlay_excerpt": mismatch.get(
                        "ocr_json_overlay_excerpt", []
                    ),
                }
            )

        if not reports:
            if self._gc_template_alert_fingerprint is not None:
                _LOGGER.info(
                    "Concierge Services: common-expenses template mismatch notification dismissed"
                )
                persistent_notification.async_dismiss(
                    self.hass, _GC_TEMPLATE_MISMATCH_NOTIFICATION_ID
                )
                self._gc_template_alert_fingerprint = None
            return

        reports.sort(key=lambda item: str(item.get("subentry_id", "")))
        payload = json.dumps(reports, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if fingerprint == self._gc_template_alert_fingerprint:
            _LOGGER.debug(
                "Concierge Services: common-expenses template mismatch notification unchanged; skipping update"
            )
            return

        detected_at = dt_util.utcnow().isoformat()
        message = self._build_gc_template_mismatch_message(reports, detected_at)
        _LOGGER.warning(
            "Concierge Services: significant common-expenses OCR/template mismatch detected "
            "(services=%d, detected_at=%s)",
            len(reports),
            detected_at,
        )
        persistent_notification.async_create(
            self.hass,
            title="Concierge — Potential template mismatch detected",
            message=message,
            notification_id=_GC_TEMPLATE_MISMATCH_NOTIFICATION_ID,
        )
        self._gc_template_alert_fingerprint = fingerprint

    def _build_gc_template_mismatch_message(
        self, reports: list[dict[str, Any]], detected_at: str
    ) -> str:
        """Build notification markdown with copy/paste GitHub issue content.

        Args:
            reports: List of mismatch reports, each containing:
                ``subentry_id``, ``service_name``, ``anchor_coverage_pct``,
                ``missing_anchor_keys``, ``value_gap_keys``, and
                ``ocr_json_overlay_excerpt``.
            detected_at: UTC ISO timestamp for when mismatch detection ran.

        Returns:
            A markdown string for Home Assistant persistent notifications,
            including per-service diagnostics plus a code block ready to paste
            into a manually created GitHub issue.
        """
        details_sections: list[str] = []
        issue_report_lines: list[str] = [
            "### Summary",
            "Potential mismatch between OCR JSON content and the Gastos Comunes markdown template anchors.",
            "",
            "### Environment",
            f"- Integration version: `{_INTEGRATION_VERSION}`",
            f"- Detected at (UTC): `{detected_at}`",
            "",
            "### Affected services",
        ]

        for report in reports:
            subentry_id = str(report.get("subentry_id", "unknown"))
            service_name = str(report.get("service_name", subentry_id))
            coverage = report.get("anchor_coverage_pct")
            coverage_label = f"{coverage}%" if coverage is not None else "unknown"
            missing = report.get("missing_anchor_keys", [])
            gaps = report.get("value_gap_keys", [])
            unexpected = report.get("unexpected_json_lines", [])
            excerpt = report.get("ocr_json_overlay_excerpt", [])
            if not isinstance(missing, list):
                missing = []
            if not isinstance(gaps, list):
                gaps = []
            if not isinstance(unexpected, list):
                unexpected = []
            if not isinstance(excerpt, list):
                excerpt = []

            details_sections.append(
                "\n".join(
                    [
                        f"### Service `{service_name}` (`{subentry_id}`)",
                        f"- Anchor coverage: **{coverage_label}**",
                        f"- Missing anchors: `{', '.join(str(v) for v in missing) if missing else 'none'}`",
                        f"- Anchors without extracted values: `{', '.join(str(v) for v in gaps) if gaps else 'none'}`",
                        "- Unexpected OCR JSON lines not in template: `"
                        + (
                            ", ".join(str(v) for v in unexpected)
                            if unexpected
                            else "none"
                        )
                        + "`",
                    ]
                )
            )
            if excerpt:
                details_sections.append(
                    "OCR JSON Overlay excerpt:\n```text\n"
                    + "\n".join(str(line) for line in excerpt[:10])
                    + "\n```"
                )

            issue_report_lines.extend(
                [
                    f"- `{service_name}` (`{subentry_id}`)",
                    f"  - Anchor coverage: `{coverage_label}`",
                    "  - Missing anchors: `"
                    + (", ".join(str(v) for v in missing) if missing else "none")
                    + "`",
                    "  - Anchors without extracted values: `"
                    + (", ".join(str(v) for v in gaps) if gaps else "none")
                    + "`",
                    "  - Unexpected OCR JSON lines not in template: `"
                    + (", ".join(str(v) for v in unexpected) if unexpected else "none")
                    + "`",
                ]
            )
            if excerpt:
                issue_report_lines.extend(
                    [
                        "  - OCR JSON Overlay excerpt:",
                        "    ```text",
                        *[f"    {str(line)}" for line in excerpt[:10]],
                        "    ```",
                    ]
                )

        issue_body = "\n".join(issue_report_lines)
        return (
            "A significant difference was detected between OCR JSON overlay lines and the "
            "Gastos Comunes markdown template anchors.\n\n"
            f"Open GitHub issues: {_GITHUB_ISSUES_URL}\n\n"
            + "\n\n".join(details_sections)
            + "\n\nCopy and paste this issue body:\n```markdown\n"
            + issue_body
            + "\n```"
        )

    async def async_refresh_service(self, subentry_id: str) -> None:
        """Force an immediate email scan and PDF analysis for a single service subentry.

        Sequence
        --------
        1. Resolve the normalised ``service_id`` slug for this subentry.
        2. **Delete** every cached PDF whose filename starts with
           ``{service_id}_`` so that the downloader fetches a fresh copy from
           the matching email instead of reusing a potentially stale file.
        3. Open a dedicated IMAP connection, find the latest matching email,
           download the bill PDF, and extract all available attributes.
        4. If the fetch returned a valid result (``last_updated`` is not
           ``None``), replace the subentry's coordinator data and notify all
           listening entities so their states update immediately.  If the
           fetch failed or found no matching email, the existing sensor values
           are preserved and a warning is logged.
        5. **Recompute derived attributes** (e.g. ``gc_total``) from the
           freshly extracted values so that formula-based sensors always
           reflect the latest data, including manual values set via
           ``set_value`` before recomputation.
        """
        if self.config_entry is None:
            _LOGGER.error(
                "Concierge Services: force refresh called but config_entry is None "
                "(subentry=%s) — aborting",
                subentry_id,
            )
            return
        subentry = self.config_entry.subentries.get(subentry_id)  # type: ignore[attr-defined]
        service_id_raw: str = subentry.data.get(CONF_SERVICE_ID, subentry_id) if subentry else subentry_id
        service_id: str = normalize_service_id(service_id_raw)
        service_name: str = (
            subentry.data.get(CONF_SERVICE_NAME, service_id.replace("_", " ").title())
            if subentry else service_id
        )

        _LOGGER.info(
            "Concierge Services [%s]: force refresh started — "
            "subentry=%s, service_id='%s'",
            service_name,
            subentry_id,
            service_id,
        )
        async_log_task(
            self.hass,
            f"force_refresh iniciado para {service_name} ({subentry_id})",
        )

        # ------------------------------------------------------------------
        # Step 1 — Delete cached PDFs so the downloader fetches a fresh copy.
        # ------------------------------------------------------------------
        try:
            deleted = await self.hass.async_add_executor_job(
                delete_service_pdfs, self._pdf_dir, service_id
            )
            if deleted:
                _LOGGER.info(
                    "Concierge Services [%s]: deleted %d cached PDF(s) before "
                    "force refresh: %s",
                    service_name,
                    len(deleted),
                    ", ".join(deleted),
                )
            else:
                _LOGGER.info(
                    "Concierge Services [%s]: no cached PDFs to delete — "
                    "will attempt fresh download",
                    service_name,
                )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Concierge Services [%s]: error while deleting cached PDFs "
                "(continuing with refresh): %s",
                service_name,
                err,
            )

        # ------------------------------------------------------------------
        # Step 2 — Scan the mailbox and extract data from email + PDF.
        # ------------------------------------------------------------------
        _LOGGER.info(
            "Concierge Services [%s]: scanning mailbox for latest bill email",
            service_name,
        )
        try:
            result = await self.hass.async_add_executor_job(
                self._fetch_single_service_data, subentry_id
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Concierge Services [%s]: force refresh failed — "
                "mailbox scan raised an exception: %s",
                service_name,
                err,
            )
            async_log_task(
                self.hass,
                f"force_refresh falló para {service_name}: {err}",
            )
            return

        # ------------------------------------------------------------------
        # Step 3 — Update coordinator data only when the scan found a result.
        # ------------------------------------------------------------------
        last_updated = result.get("last_updated")
        extracted_attrs = result.get("attributes", {})
        attr_keys = [k for k in extracted_attrs if not k.startswith("_")]

        if last_updated is None:
            _LOGGER.warning(
                "Concierge Services [%s]: force refresh found no matching email "
                "— existing sensor values are preserved (subentry=%s)",
                service_name,
                subentry_id,
            )
            async_log_task(
                self.hass,
                (
                    "force_refresh sin coincidencia de correo para "
                    f"{service_name} ({subentry_id})"
                ),
            )
            return

        _LOGGER.info(
            "Concierge Services [%s]: force refresh succeeded — "
            "last_updated=%s, attributes extracted: %s",
            service_name,
            last_updated.isoformat() if hasattr(last_updated, "isoformat") else last_updated,
            ", ".join(f"{k}={extracted_attrs[k]!r}" for k in attr_keys) if attr_keys else "(none)",
        )

        self._manage_ocr_repair_issue()
        # Replace the service entry and push state to all listening entities.
        current: dict[str, Any] = (
            self.data if self.data is not None
            else {"connection_status": "OK", "services": {}}
        )
        current.setdefault("services", {})[subentry_id] = result
        self.async_set_updated_data(current)
        self._manage_gc_template_mismatch_notification(current)

        # ------------------------------------------------------------------
        # Step 4 — Recompute formula-derived attributes (e.g. gc_total).
        #
        # Delegated to async_recompute_derived so the recomputation logic
        # lives in one place and the Recalculate button can call it
        # independently.  async_recompute_derived reads from self.data (which
        # was just updated above) and issues a second async_set_updated_data
        # push with the final derived values.
        # ------------------------------------------------------------------
        await self.async_recompute_derived(subentry_id)
        _LOGGER.info(
            "Concierge Services [%s]: sensor states updated from force refresh "
            "(subentry=%s)",
            service_name,
            subentry_id,
        )
        async_log_task(
            self.hass,
            f"force_refresh completado para {service_name} ({subentry_id})",
        )

    def _fetch_single_service_data(self, subentry_id: str) -> dict[str, Any]:
        """Open a fresh IMAP connection and fetch data for one service subentry.

        This runs in an executor thread (blocking I/O).
        """
        imap = None
        empty: dict[str, Any] = {"last_updated": None, "attributes": {}}
        try:
            cfg = self._cfg
            imap = imaplib.IMAP4_SSL(
                cfg[CONF_IMAP_SERVER], cfg[CONF_IMAP_PORT], timeout=30
            )
            imap.login(cfg[CONF_EMAIL], cfg[CONF_PASSWORD])

            assert self.config_entry is not None
            subentry = self.config_entry.subentries.get(subentry_id)  # type: ignore[attr-defined]
            if subentry is None:
                _LOGGER.warning(
                    "Concierge Services: subentry %s not found during force refresh",
                    subentry_id,
                )
                return empty

            return self._find_latest_email_for_service(imap, subentry.data, subentry_id)

        except imaplib.IMAP4.error as err:
            _LOGGER.warning(
                "Concierge Services: IMAP auth failed during force refresh for %s: %s",
                subentry_id,
                err,
            )
            return empty
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Concierge Services: force refresh IMAP error for %s: %s",
                subentry_id,
                err,
            )
            return empty
        finally:
            if imap is not None:
                try:
                    imap.logout()
                except Exception:  # pylint: disable=broad-except
                    pass

    def _fetch_service_data(self) -> dict[str, Any]:
        """Fetch service data from IMAP."""
        imap = None
        result: dict[str, Any] = {
            "connection_status": "Problem",
            "services": {},
        }

        try:
            cfg = self._cfg
            imap = imaplib.IMAP4_SSL(
                cfg[CONF_IMAP_SERVER], cfg[CONF_IMAP_PORT], timeout=30
            )
            imap.login(cfg[CONF_EMAIL], cfg[CONF_PASSWORD])
            result["connection_status"] = "OK"

            # Purge PDFs older than the configured retention period once per cycle
            try:
                purge_old_pdfs(self._pdf_dir, PDF_MAX_AGE_DAYS, PDF_MAX_FILES)
            except Exception as err:
                _LOGGER.debug("Error purging old PDFs: %s", err)

            # Fetch data for each subentry (service device)
            assert self.config_entry is not None
            for subentry_id, subentry in self.config_entry.subentries.items():  # type: ignore[attr-defined]
                service_data = self._find_latest_email_for_service(imap, subentry.data, subentry_id)
                result["services"][subentry_id] = service_data

        except imaplib.IMAP4.error as err:
            _LOGGER.warning(
                "IMAP authentication failed for %s: %s",
                self._cfg.get(CONF_EMAIL),
                err,
            )
            result["connection_status"] = "Problem"
        except Exception as err:
            _LOGGER.warning(
                "IMAP connection failed for %s@%s:%s: %s",
                self._cfg.get(CONF_EMAIL),
                self._cfg.get(CONF_IMAP_SERVER),
                self._cfg.get(CONF_IMAP_PORT),
                err,
            )
            result["connection_status"] = "Problem"
        finally:
            if imap is not None:
                try:
                    imap.logout()
                except Exception:
                    pass

        return result

    def _find_latest_email_for_service(
        self, imap: imaplib.IMAP4_SSL, service_data: dict[str, Any],
        subentry_id: str | None = None,
    ) -> dict[str, Any]:
        """Find the latest email for a service and extract attributes."""
        result: dict[str, Any] = {
            "last_updated": None,
            "attributes": {},
        }

        try:
            imap.select("INBOX")

            status, messages = imap.search(None, "ALL")

            if status != "OK":
                return result

            email_ids = messages[0].split()
            email_ids = email_ids[-100:]

            latest_date = None
            latest_attributes: dict[str, Any] = {}

            sample_from = service_data.get(CONF_SAMPLE_FROM, "")
            sample_subject = service_data.get(CONF_SAMPLE_SUBJECT, "")
            service_name = service_data.get(CONF_SERVICE_NAME, "")
            service_id_raw = service_data.get(CONF_SERVICE_ID, "")
            # Normalise to the canonical English slug for filenames and display;
            # keep the raw stored ID for email-matching (patterns are language-aware).
            service_id = normalize_service_id(service_id_raw)
            service_type: str = service_data.get(CONF_SERVICE_TYPE) or classify_service_type(
                sample_from, sample_subject
            )

            for email_id in reversed(email_ids):
                try:
                    status, msg_data = imap.fetch(email_id, "(RFC822)")

                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]  # type: ignore[index]
                    msg = email.message_from_bytes(raw_email)  # type: ignore[arg-type]

                    from_header = msg.get("From", "")
                    subject_header = msg.get("Subject", "")
                    date_header = msg.get("Date", "")

                    from_addr = self._decode_mime_words(from_header)
                    subject = self._decode_mime_words(subject_header)
                    body = self._get_email_body(msg)

                    _LOGGER.debug(
                        "Concierge Services [%s]: evaluating email — from='%s', subject='%s'",
                        service_name,
                        from_addr,
                        subject,
                    )

                    match_strategy = self._matches_service(
                        service_id_raw, service_name, sample_from, sample_subject,
                        from_addr, subject, body, service_type,
                    )

                    if match_strategy:
                        _LOGGER.info(
                            "Concierge Services [%s]: email matched via strategy '%s' — "
                            "from='%s', subject='%s', date='%s'",
                            service_name,
                            match_strategy,
                            from_addr,
                            subject,
                            date_header,
                        )
                        email_date: datetime | None = None
                        if date_header:
                            try:
                                email_date = parsedate_to_datetime(date_header)
                                latest_date = email_date
                            except (TypeError, ValueError) as err:
                                _LOGGER.debug(
                                    "Concierge Services [%s]: could not parse email Date "
                                    "header '%s' (continuing with extraction): %s",
                                    service_name,
                                    date_header,
                                    err,
                                )

                        latest_attributes = extract_attributes_from_email_body(
                            subject, body, service_type
                        )

                        # Log attributes extracted from the email body
                        body_attr_keys = [
                            k for k in latest_attributes if not k.startswith("_")
                        ]
                        if body_attr_keys:
                            _LOGGER.info(
                                "Concierge Services [%s]: attributes extracted from "
                                "email body — %s",
                                service_name,
                                ", ".join(
                                    f"{k}={latest_attributes[k]!r}"
                                    for k in body_attr_keys
                                ),
                            )
                        else:
                            _LOGGER.debug(
                                "Concierge Services [%s]: no attributes extracted "
                                "from email body",
                                service_name,
                            )

                        # Attempt to download (or locate) the bill PDF
                        try:
                            pdf_path = download_pdf_from_email(
                                msg,
                                self._pdf_dir,
                                service_id,
                                email_date,
                                latest_attributes,
                                max_files=PDF_MAX_FILES,
                            )
                            if pdf_path:
                                latest_attributes["pdf_path"] = pdf_path
                                _LOGGER.info(
                                    "Concierge Services [%s]: PDF found at '%s' — "
                                    "extracting additional attributes",
                                    service_name,
                                    pdf_path,
                                )
                                # Extract additional attributes from the
                                # PDF (e.g. consumption for Metrogas).
                                # PDF values override email-derived values.
                                #
                                # For common_expenses and hot_water: when the
                                # Concierge addon is available use its OCR API
                                # instead of the internal pdfminer extractor.
                                use_addon = (
                                    self._addon_available is True
                                    and service_type in (
                                        SERVICE_TYPE_COMMON_EXPENSES,
                                        SERVICE_TYPE_HOT_WATER,
                                    )
                                )
                                if use_addon:
                                    _LOGGER.info(
                                        "Concierge Services [%s]: using Concierge "
                                        "addon OCR API for PDF '%s'",
                                        service_name,
                                        pdf_path,
                                    )
                                    addon_ocr_json = self._sync_ocr_pdf_via_addon(
                                        pdf_path,
                                        self._addon_api_url,
                                        template_id=ADDON_COMMON_EXPENSES_TEMPLATE_ID,
                                    )
                                    pdf_attrs: dict[str, Any] = (
                                        extract_attributes_from_addon_ocr_json(
                                            addon_ocr_json,
                                            pdf_path=pdf_path,
                                            json_dir=self._json_dir,
                                        )
                                        if addon_ocr_json
                                        else {}
                                    )
                                    if not pdf_attrs:
                                        # Structured template responses omit the
                                        # raw OCR pages needed by the legacy
                                        # extractor, so request a second raw-OCR
                                        # payload before falling back to the
                                        # internal PDF parser.
                                        addon_ocr_json = self._sync_ocr_pdf_via_addon(
                                            pdf_path,
                                            self._addon_api_url,
                                        )
                                    if addon_ocr_json:
                                        pdf_attrs = extract_attributes_from_addon_ocr_json(
                                            addon_ocr_json,
                                            pdf_path=pdf_path,
                                            json_dir=self._json_dir,
                                        )
                                    if not pdf_attrs:
                                        _LOGGER.warning(
                                            "Concierge Services [%s]: addon OCR "
                                            "returned no usable attributes for '%s'; "
                                            "falling back to internal extractor",
                                            service_name,
                                            pdf_path,
                                        )
                                        pdf_attrs = extract_attributes_from_pdf(
                                            pdf_path,
                                            service_type,
                                            str(self._cfg.get(CONF_OCRSPACE_API_KEY, "")),
                                            json_dir=self._json_dir,
                                        )
                                else:
                                    pdf_attrs = extract_attributes_from_pdf(
                                        pdf_path,
                                        service_type,
                                        str(self._cfg.get(CONF_OCRSPACE_API_KEY, "")),
                                        json_dir=self._json_dir,
                                    )
                                latest_attributes.update(pdf_attrs)

                                pdf_attr_keys = [
                                    k for k in pdf_attrs if not k.startswith("_")
                                ]
                                if pdf_attr_keys:
                                    _LOGGER.info(
                                        "Concierge Services [%s]: attributes extracted "
                                        "from PDF — %s",
                                        service_name,
                                        ", ".join(
                                            f"{k}={pdf_attrs[k]!r}"
                                            for k in pdf_attr_keys
                                        ),
                                    )
                                else:
                                    _LOGGER.debug(
                                        "Concierge Services [%s]: no attributes "
                                        "extracted from PDF",
                                        service_name,
                                    )

                                # For PDF-only services (common_expenses,
                                # hot_water) the email Date header reflects
                                # when the administrator forwarded the bill,
                                # not the bill issue date.  Override
                                # last_updated with the bill's Fecha Emisión
                                # extracted from the PDF when available.
                                if service_type in (
                                    SERVICE_TYPE_COMMON_EXPENSES,
                                    SERVICE_TYPE_HOT_WATER,
                                ):
                                    emission_str = latest_attributes.get(
                                        "emission_date"
                                    )
                                    if emission_str:
                                        try:
                                            parsed = datetime.strptime(
                                                emission_str, "%d-%m-%Y"
                                            )
                                            # Use the HA-configured local
                                            # timezone at noon so the sensor
                                            # displays the correct local date
                                            # (avoids UTC-midnight rollover to
                                            # the previous day in negative-offset
                                            # timezones such as America/Santiago).
                                            latest_date = parsed.replace(
                                                hour=12,
                                                minute=0,
                                                second=0,
                                                tzinfo=dt_util.DEFAULT_TIME_ZONE,
                                            )
                                            _LOGGER.info(
                                                "Concierge Services [%s]: last_updated "
                                                "overridden with PDF emission date '%s'",
                                                service_name,
                                                emission_str,
                                            )
                                        except ValueError:
                                            pass
                            else:
                                _LOGGER.debug(
                                    "Concierge Services [%s]: no PDF attachment found "
                                    "in matching email",
                                    service_name,
                                )
                        except Exception as pdf_err:
                            _LOGGER.warning(
                                "PDF download failed for service '%s': %s",
                                service_id, pdf_err,
                            )
                        if latest_date is None:
                            latest_date = dt_util.now()
                            _LOGGER.debug(
                                "Concierge Services [%s]: using current timestamp "
                                "as last_updated fallback",
                                service_name,
                            )
                    else:
                        _LOGGER.debug(
                            "Concierge Services [%s]: email did not match — "
                            "from='%s', subject='%s'",
                            service_name,
                            from_addr,
                            subject,
                        )

                    if match_strategy:
                        # Only analyse the single most-recent matching email.
                        # Emails are iterated newest-first (reversed), so the
                        # first match is always the most recent one.
                        break

                except Exception as err:
                    _LOGGER.debug("Error processing email %s: %s", email_id, err)
                    continue

            result["last_updated"] = latest_date
            result["attributes"] = latest_attributes

            if latest_date is None:
                _LOGGER.warning(
                    "No matching email found for service '%s' (id: %s) in the last %d emails",
                    service_data.get(CONF_SERVICE_NAME, ""),
                    service_data.get(CONF_SERVICE_ID, ""),
                    len(email_ids),
                )

            return result

        except Exception as err:
            _LOGGER.debug("Error finding latest email for service: %s", err)
            return result

    def _get_email_body(self, msg: email.message.Message) -> str:
        """Extract text content from email body, preferring plain text over HTML."""
        body = ""

        try:
            if msg.is_multipart():
                plain_parts: list[str] = []
                html_parts: list[str] = []

                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))

                    if "attachment" in content_disposition:
                        continue

                    try:
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue
                        charset = part.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                        if content_type == "text/plain":
                            plain_parts.append(text)
                        elif content_type == "text/html":
                            html_parts.append(text)
                    except Exception:
                        pass

                # Prefer plain text; fall back to stripped HTML to avoid tag/URL noise
                if plain_parts:
                    body = " ".join(plain_parts)
                elif html_parts:
                    body = _strip_html(" ".join(html_parts))
            else:
                try:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        charset = msg.get_content_charset() or "utf-8"
                        raw = payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                        body = _strip_html(raw) if msg.get_content_type() == "text/html" else raw
                except Exception:
                    pass
        except Exception as err:
            _LOGGER.debug("Error extracting email body: %s", err)

        return body

    def _has_attachments(self, msg: email.message.Message) -> bool:
        """Check if email has attachments."""
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        return True

                    # Also check for inline attachments with filename
                    filename = part.get_filename()
                    if filename:
                        return True

            return False
        except Exception as err:
            _LOGGER.debug("Error checking attachments: %s", err)
            return False

    def _decode_mime_words(self, s: str) -> str:
        """Decode MIME encoded-word strings."""
        decoded_fragments = decode_header(s)
        result = []
        for fragment, encoding in decoded_fragments:
            if isinstance(fragment, bytes):
                result.append(fragment.decode(encoding or "utf-8", errors="ignore"))
            else:
                result.append(fragment)
        return "".join(result)

    def _matches_service(
        self,
        service_id: str,
        service_name: str,
        sample_from: str,
        sample_subject: str,
        from_addr: str,
        subject: str,
        body: str,
        service_type: str = SERVICE_TYPE_UNKNOWN,
    ) -> str | None:
        """Check if email matches a service based on flexible patterns.

        Returns the name of the matching strategy (a non-empty string) when the
        email is accepted, or ``None`` when no strategy fires.  The strategy name
        is used by callers to log which detection path triggered the match.
        """
        combined_text = f"{from_addr} {subject} {body}".lower()

        # Strategy 1 – Match by sender domain from sample_from (skip generic webmail providers)
        if sample_from:
            domain_match = re.search(r'@([a-zA-Z0-9\-]+)\.[a-zA-Z]+', sample_from)
            if domain_match:
                domain = domain_match.group(1).lower()
                if domain not in _GENERIC_WEBMAIL_DOMAINS and domain in from_addr.lower():
                    return "sender-domain"

        # Strategy 2 – Match by service name keywords (only when there are significant words)
        if service_name:
            words = service_name.lower().split()
            significant_words = [w for w in words if len(w) > 3]
            if significant_words:
                matches = sum(1 for word in significant_words if word in combined_text)
                if matches >= len(significant_words):
                    return "service-name-keywords"

        # Strategy 3 – Match by service_id pattern using whole-word boundaries so that a
        # short service ID such as "gas" does not spuriously match words that
        # merely contain it as a substring (e.g. "Gastos Comunes").
        service_pattern = r'\b' + service_id.replace('_', r'.*') + r'\b'
        if re.search(service_pattern, combined_text, re.IGNORECASE):
            return "service-id-pattern"

        # Strategy 4 – Match by unique keywords from sample_subject.
        # This covers forwarded emails where the From domain is a generic webmail
        # provider (e.g. Gmail).  We extract alphabetic words that are specific to
        # the service (filtering out generic billing terms and month names) and
        # require at least one of them to appear in the email being checked.
        if sample_subject:
            unique_words = [
                w.lower()
                for w in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ]{4,}", sample_subject)
                if w.lower() not in _SUBJECT_SKIP_WORDS
            ]
            if unique_words and any(w in combined_text for w in unique_words):
                return "sample-subject-keywords"

        # Strategy 5 (fallback) – Match using the canonical SERVICE_PATTERNS for this service
        # type.  This handles forwarded emails (e.g. via Gmail) where the sender
        # domain is a generic webmail provider — so the domain check is skipped —
        # and the service_name / service_id are stored in English while the email
        # body is in Spanish (e.g. "Gastos Comunes", "Aguas Andinas").  If any
        # pattern associated with the configured service_type matches the email
        # content, consider it a match.
        if service_type and service_type != SERVICE_TYPE_UNKNOWN:
            for pattern, _name, svc_type in SERVICE_PATTERNS:
                if svc_type == service_type and re.search(
                    pattern, combined_text, re.IGNORECASE
                ):
                    return "service-type-pattern-fallback"

        return None


class _ConciergeServiceBaseSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Shared base for per-subentry service sensors."""

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the base sensor."""
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

        # All service sensors share the same device (identified by the subentry).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{subentry_id}")},
            name=self._service_name,
            manufacturer="Concierge Services",
            model="Service Account",
        )

    def _get_extracted_attrs(self) -> dict[str, Any]:
        """Return the extracted attribute dict from coordinator data, or empty dict."""
        if not self.coordinator.data:
            return {}
        service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
        if not service_data:
            return {}
        return service_data.get("attributes", {})

    def _get_confidence(self, attr_key: str) -> float | None:
        """Return the extraction confidence (0–100) for *attr_key*, or None.

        The confidence is stored under the ``_confidence`` metadata key in the
        extracted attributes dict.  Values are:
          70  – pdfminer text layer (may have font-encoding errors)
          85  – OCR (OCR.space cloud API) — more accurate for image-backed PDFs
          60  – derived/calculated from other extracted values
         100  – user-supplied correction (manual override via ``set_value``)
        """
        return self._get_extracted_attrs().get("_confidence", {}).get(attr_key)


class ConciergeServiceLastUpdateSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the date of the latest processed bill for a service.

    Device class TIMESTAMP causes the HA frontend to render the value as a
    relative time string ("hace 2 días", "2 days ago", etc.) in the user's
    configured language.

    Entity category: Diagnostic — appears in the device Diagnostic panel.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the last-update sensor."""
        super().__init__(coordinator, config_entry, subentry_id, subentry_data)
        self._attr_name = f"Concierge {self._service_id} Last Update"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_last_update"

    @property
    def native_value(self) -> datetime | None:
        """Return the last bill datetime; HA renders it as a relative time string."""
        if not self.coordinator.data:
            return None
        service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
        if not service_data:
            return None
        return service_data.get("last_updated")


class ConciergeServiceConsumptionSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the consumption value extracted from the latest bill."""

    _attr_icon = "mdi:gauge"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the consumption sensor."""
        super().__init__(coordinator, config_entry, subentry_id, subentry_data)
        service_type = subentry_data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
        self._attr_name = f"Concierge {self._service_id} Consumption"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_consumption"
        self._attr_native_unit_of_measurement = _CONSUMPTION_UNITS.get(service_type)
        # Hot-water consumption is stored under a separate attribute key.
        self._consumption_attr = (
            "hot_water_consumption"
            if service_type == SERVICE_TYPE_HOT_WATER
            else "consumption"
        )

    @property
    def native_value(self) -> float | None:
        """Return the consumption value."""
        return self._get_extracted_attrs().get(self._consumption_attr)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extraction confidence for this sensor's value."""
        conf = self._get_confidence(self._consumption_attr)
        if conf is not None:
            return {"extraction_confidence": conf}
        return {}


class ConciergeServiceCostPerUnitSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the cost per consumption unit extracted from the latest bill."""

    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the cost-per-unit sensor."""
        super().__init__(coordinator, config_entry, subentry_id, subentry_data)
        service_type = subentry_data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
        self._cost_attr_key: str | None = _COST_PER_UNIT_ATTR.get(service_type)
        self._attr_name = f"Concierge {self._service_id} Cost Per Unit"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_cost_per_unit"
        self._attr_native_unit_of_measurement = _COST_PER_UNIT_UNITS.get(service_type)

    @property
    def native_value(self) -> float | None:
        """Return the cost-per-unit value, or None for service types without one."""
        if not self._cost_attr_key:
            return None
        return self._get_extracted_attrs().get(self._cost_attr_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extraction confidence for this sensor's value."""
        if self._cost_attr_key:
            conf = self._get_confidence(self._cost_attr_key)
            if conf is not None:
                return {"extraction_confidence": conf}
        return {}


class ConciergeServiceTotalAmountSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the total bill amount extracted from the latest bill."""

    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = "$"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the total-amount sensor."""
        super().__init__(coordinator, config_entry, subentry_id, subentry_data)
        service_type = subentry_data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)
        self._attr_name = f"Concierge {self._service_id} Total Amount"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_total_amount"
        # Use the service-type-specific attribute key, falling back to the
        # generic ``total_amount`` for service types not listed in the map.
        self._total_attr_key: str = _TOTAL_AMOUNT_ATTR.get(service_type, "total_amount")

    @property
    def native_value(self) -> float | None:
        """Return the total bill amount for this service device."""
        return self._get_extracted_attrs().get(self._total_attr_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extraction confidence for this sensor's value."""
        conf = self._get_confidence(self._total_attr_key)
        if conf is not None:
            return {"extraction_confidence": conf}
        return {}


class ConciergeServiceBillingBreakdownSensor(_ConciergeServiceBaseSensor):
    """Sensor exposing a single billing-breakdown attribute as a dedicated entity.

    Instances are created at setup time from the service-type sensor tables
    (``_WATER_SPECIFIC_SENSORS``, ``_ELECTRICITY_SPECIFIC_SENSORS``), one per
    billing field.  This replaces the attribute-based approach where these
    fields were bundled into the status binary sensor.
    """

    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
        *,
        attr_key: str,
        name_suffix: str,
        unit: str,
        uid_suffix: str,
    ) -> None:
        """Initialize the billing-breakdown sensor."""
        super().__init__(coordinator, config_entry, subentry_id, subentry_data)
        self._attr_key = attr_key
        self._attr_name = f"Concierge {self._service_id} {name_suffix}"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_{uid_suffix}"
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | int | None:
        """Return the billing breakdown attribute value."""
        return self._get_extracted_attrs().get(self._attr_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extraction confidence for this sensor's value."""
        conf = self._get_confidence(self._attr_key)
        if conf is not None:
            return {"extraction_confidence": conf}
        return {}


class ConciergeServicesConnectionSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor to monitor mail server connection status (linked to the hub device)."""

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "Concierge Services - Status"
        self._attr_unique_id = f"{config_entry.entry_id}_connection"
        self._attr_icon = "mdi:email-check"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title or DOMAIN,
            manufacturer="Concierge Services",
            model="Hub",
        )

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "Problem"
        return self.coordinator.data.get("connection_status", "Problem")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        cfg = {**self._config_entry.data, **self._config_entry.options}
        return {
            "email": cfg.get(CONF_EMAIL, ""),
            "imap_server": cfg.get(CONF_IMAP_SERVER, ""),
            "imap_port": cfg.get(CONF_IMAP_PORT, ""),
        }


class ConciergeAddonStatusSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor reporting the Concierge OCR addon lifecycle state.

    Possible states (see ``ADDON_STATUS_*`` constants in ``const.py``):

    * ``unknown``       — Supervisor data not yet available (transient, post-boot).
    * ``unsupported``   — Home Assistant is not running under Supervisor.
    * ``not_installed`` — The addon is absent from Supervisor.
    * ``installed``     — The addon is installed but currently stopped.
    * ``starting``      — Supervisor reports the addon is starting up.
    * ``running``       — The addon is started and its ``/status`` (or ``/health``) endpoint is healthy.

    When the addon exposes ``GET /status`` (v0.3.1+) and responds positively, the
    ``addon_version`` extra state attribute is populated with the reported version
    string.  For older addon versions the integration falls back to ``GET /health``
    and ``addon_version`` is omitted.

    State changes are persisted automatically by the HA recorder and logged at
    INFO level by the coordinator whenever the status transitions.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ADDON_STATUS_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    # Icon map: keyed by addon status value.
    _STATUS_ICONS: dict[str, str] = {
        ADDON_STATUS_UNKNOWN: "mdi:help-circle-outline",
        ADDON_STATUS_UNSUPPORTED: "mdi:puzzle-remove",
        ADDON_STATUS_NOT_INSTALLED: "mdi:puzzle-outline",
        ADDON_STATUS_INSTALLED: "mdi:puzzle-off",
        ADDON_STATUS_STARTING: "mdi:puzzle-check-outline",
        ADDON_STATUS_RUNNING: "mdi:puzzle-check",
    }

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the addon status sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "Concierge Services - Addon Status"
        self._attr_unique_id = f"{config_entry.entry_id}_addon_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=config_entry.title or DOMAIN,
            manufacturer="Concierge Services",
            model="Hub",
        )

    @property
    def native_value(self) -> str:
        """Return the current addon status string."""
        return self.coordinator.addon_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes, including the addon version when known.

        The ``addon_version`` attribute is populated from the ``GET /status``
        response (available since addon v0.3.1).  It is ``None`` when the addon
        is not running or when the older ``/health``-only API was used.
        """
        version = self.coordinator.addon_version
        if version is not None:
            return {"addon_version": version}
        return {}

    @property
    def icon(self) -> str:
        """Return an icon that reflects the current addon status."""
        return self._STATUS_ICONS.get(
            self.coordinator.addon_status,
            "mdi:puzzle-outline",
        )
