"""Sensor platform for Concierge Services."""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_EMAIL,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_PASSWORD,
    CONF_SAMPLE_FROM,
    CONF_SAMPLE_SUBJECT,
    CONF_SERVICE_ID,
    CONF_SERVICE_NAME,
    CONF_SERVICE_TYPE,
    DOMAIN,
    PDF_MAX_AGE_DAYS,
    PDF_SUBDIR,
    SERVICE_TYPE_COMMON_EXPENSES,
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_HOT_WATER,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)
from .attribute_extractor import extract_attributes_from_email_body, extract_attributes_from_pdf, _strip_html
from .pdf_downloader import download_pdf_from_email, purge_old_pdfs
from .service_detector import classify_service_type

_LOGGER = logging.getLogger(__name__)

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
    SERVICE_TYPE_COMMON_EXPENSES: "subtotal_departamento",
    SERVICE_TYPE_HOT_WATER: "hot_water_amount",
}

# Billing-breakdown sensor definitions per service type.
# Each tuple: (extracted_attr_key, name_suffix, unit, unique_id_suffix)
# Used by ConciergeServiceBillingBreakdownSensor (one instance per row).

# Water: the two cost-per-unit peak/non-peak sensors renamed per spec v0.7.6.
_WATER_SPECIFIC_SENSORS: list[tuple[str, str, str, str]] = [
    ("fixed_charge",                 "Fixed Charge",                 "$",     "water_fixed_charge"),
    ("cubic_meter_peak_water_cost",  "Cost Per Unit Peak",           "$/m³",  "cost_per_unit_peak"),
    ("cubic_meter_non_peak_water_cost", "Cost Per Unit Non Peak",    "$/m³",  "cost_per_unit_non_peak"),
    ("cubic_meter_overconsumption",  "Cubic Meter Overconsumption",  "$/m³",  "water_cubic_meter_overconsumption"),
    ("cubic_meter_collection",       "Cubic Meter Collection",       "$/m³",  "water_cubic_meter_collection"),
    ("cubic_meter_treatment",        "Cubic Meter Treatment",        "$/m³",  "water_cubic_meter_treatment"),
    ("water_consumption",            "Water Consumption",            "$",     "water_consumption_charge"),
    ("wastewater_recolection",       "Wastewater Recolection",       "$",     "wastewater_recolection"),
    ("wastewater_treatment",         "Wastewater Treatment",         "$",     "wastewater_treatment"),
    ("subtotal",                     "Subtotal",                     "$",     "water_subtotal"),
    ("other_charges",                "Other Charges",                "$",     "water_other_charges"),
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
_COMMON_EXPENSES_SPECIFIC_SENSORS: list[tuple[str, str, str, str]] = [
    ("gastos_comunes_amount",   "Bill",                "$", "gc_bill"),
    ("fondos_amount",           "Fondos 5%",           "$", "gc_fondos_amount"),
    ("subtotal_departamento",   "Subtotal Departamento","$", "gc_subtotal_departamento"),
    ("subtotal_recargos",       "Subtotal Recargos",   "$", "gc_subtotal_recargos"),
]

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

    One connection sensor is created for the main entry as a standalone entity
    (no device) so it does not appear in the "Devices that don't belong to a
    sub-entry" category.
    Per-subentry sensors:
    - All service types: last_update, consumption, total_amount.
    - Gas: cost_per_unit (generic $/unit sensor).
    - Electricity: cost_per_unit + 4 billing-breakdown sensors
      (service_administration, electricity_transport, stabilization_fund,
      electricity_consumption).
    - Water: cost_per_unit is replaced by 11 water-specific sensors
      (fixed_charge, cost_per_unit_peak, cost_per_unit_non_peak, and other
      water billing breakdown fields).
    Each entity is associated with its own subentry so it appears correctly
    grouped in the HA device registry.
    """
    # The coordinator is initialised in __init__.async_setup_entry before the
    # platforms are forwarded, so it is always present at this point.
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    # Main connection sensor (standalone entity, not linked to any device or subentry)
    async_add_entities([ConciergeServicesConnectionSensor(coordinator, config_entry)])

    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        service_type = subentry.data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)

        entities: list[SensorEntity] = [
            ConciergeServiceLastUpdateSensor(
                coordinator, config_entry, subentry_id, subentry.data
            ),
            ConciergeServiceTotalAmountSensor(
                coordinator, config_entry, subentry_id, subentry.data
            ),
        ]

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
            # Water services use granular peak/non-peak cost sensors instead of
            # the generic cost_per_unit sensor, plus additional billing breakdown
            # sensors.
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from IMAP server."""
        try:
            return await self.hass.async_add_executor_job(self._fetch_service_data)
        except Exception as err:
            raise UpdateFailed(f"Error communicating with IMAP server: {err}") from err

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
                purge_old_pdfs(self._pdf_dir, PDF_MAX_AGE_DAYS)
            except Exception as err:
                _LOGGER.debug("Error purging old PDFs: %s", err)

            # Fetch data for each subentry (service device)
            assert self.config_entry is not None
            for subentry_id, subentry in self.config_entry.subentries.items():  # type: ignore[attr-defined]
                service_data = self._find_latest_email_for_service(imap, subentry.data)
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
        self, imap: imaplib.IMAP4_SSL, service_data: dict[str, Any]
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
            service_id = service_data.get(CONF_SERVICE_ID, "")
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

                    if self._matches_service(
                        service_id, service_name, sample_from, sample_subject,
                        from_addr, subject, body,
                    ):
                        if date_header:
                            try:
                                email_date = parsedate_to_datetime(date_header)
                                if latest_date is None or email_date > latest_date:
                                    latest_date = email_date
                                    latest_attributes = extract_attributes_from_email_body(
                                        subject, body, service_type
                                    )
                                    # Attempt to download (or locate) the bill PDF
                                    try:
                                        pdf_path = download_pdf_from_email(
                                            msg,
                                            self._pdf_dir,
                                            service_id,
                                            email_date,
                                            latest_attributes,
                                        )
                                        if pdf_path:
                                            latest_attributes["pdf_path"] = pdf_path
                                            # Extract additional attributes from the
                                            # PDF (e.g. consumption for Metrogas).
                                            # PDF values override email-derived values.
                                            pdf_attrs = extract_attributes_from_pdf(
                                                pdf_path, service_type
                                            )
                                            latest_attributes.update(pdf_attrs)
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
                                                        latest_date = datetime.strptime(
                                                            emission_str, "%d-%m-%Y"
                                                        ).replace(tzinfo=timezone.utc)
                                                    except ValueError:
                                                        pass
                                    except Exception as pdf_err:
                                        _LOGGER.warning(
                                            "PDF download failed for service '%s': %s",
                                            service_id, pdf_err,
                                        )
                            except Exception:
                                pass

                        if latest_date:
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
    ) -> bool:
        """Check if email matches a service based on flexible patterns."""
        combined_text = f"{from_addr} {subject} {body}".lower()

        # Match by sender domain from sample_from (skip generic webmail providers)
        if sample_from:
            domain_match = re.search(r'@([a-zA-Z0-9\-]+)\.[a-zA-Z]+', sample_from)
            if domain_match:
                domain = domain_match.group(1).lower()
                if domain not in _GENERIC_WEBMAIL_DOMAINS and domain in from_addr.lower():
                    return True

        # Match by service name keywords (only when there are significant words)
        if service_name:
            words = service_name.lower().split()
            significant_words = [w for w in words if len(w) > 3]
            if significant_words:
                matches = sum(1 for word in significant_words if word in combined_text)
                if matches >= len(significant_words):
                    return True

        # Match by service_id pattern
        service_pattern = service_id.replace('_', '.*')
        if re.search(service_pattern, combined_text, re.IGNORECASE):
            return True

        # Match by unique keywords from sample_subject.
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
                return True

        return False


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
        self._service_id = subentry_data.get(CONF_SERVICE_ID, subentry_id)
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


class ConciergeServiceCostPerUnitSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the cost per consumption unit extracted from the latest bill."""

    _attr_icon = "mdi:currency-usd"

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


class ConciergeServiceTotalAmountSensor(_ConciergeServiceBaseSensor):
    """Sensor reporting the total bill amount extracted from the latest bill."""

    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = "$"

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


class ConciergeServiceBillingBreakdownSensor(_ConciergeServiceBaseSensor):
    """Sensor exposing a single billing-breakdown attribute as a dedicated entity.

    Instances are created at setup time from the service-type sensor tables
    (``_WATER_SPECIFIC_SENSORS``, ``_ELECTRICITY_SPECIFIC_SENSORS``), one per
    billing field.  This replaces the attribute-based approach where these
    fields were bundled into the status binary sensor.
    """

    _attr_icon = "mdi:currency-usd"

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


class ConciergeServicesConnectionSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor to monitor mail server connection status (no device, standalone entity)."""

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
