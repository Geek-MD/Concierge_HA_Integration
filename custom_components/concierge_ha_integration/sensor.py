"""Sensor platform for Concierge Services."""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
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
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
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
}

# The extracted attribute key that holds the cost-per-unit value, per service type.
_COST_PER_UNIT_ATTR: dict[str, str] = {
    SERVICE_TYPE_GAS: "cost_per_m3s",
    SERVICE_TYPE_ELECTRICITY: "cost_per_kwh",
}

# Unit of measure for cost-per-unit sensors, per service type.
_COST_PER_UNIT_UNITS: dict[str, str] = {
    SERVICE_TYPE_GAS: "$/m³",
    SERVICE_TYPE_ELECTRICITY: "$/kWh",
}

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
    Four service sensors are created for every subentry (service device):
    last_update, consumption, cost_per_unit, and total_amount — each associated
    with its own subentry so they appear correctly grouped in the HA device
    registry.
    """
    # The coordinator is initialised in __init__.async_setup_entry before the
    # platforms are forwarded, so it is always present at this point.
    coordinator: ConciergeServicesCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    )

    # Main connection sensor (standalone entity, not linked to any device or subentry)
    async_add_entities([ConciergeServicesConnectionSensor(coordinator, config_entry)])

    # Four service sensors per subentry, each linked to its own subentry.
    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        async_add_entities(
            [
                ConciergeServiceLastUpdateSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
                ConciergeServiceConsumptionSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
                ConciergeServiceCostPerUnitSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
                ConciergeServiceTotalAmountSensor(
                    coordinator, config_entry, subentry_id, subentry.data
                ),
            ],
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

    Entity category: Configuration — appears in the device Configuration panel.
    """

    _attr_entity_category = EntityCategory.CONFIG
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
    def native_value(self) -> str | None:
        """Return the last bill datetime as a full ISO-format datetime string."""
        if not self.coordinator.data:
            return None
        service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
        if not service_data:
            return None
        last_updated = service_data.get("last_updated")
        if last_updated:
            return last_updated.isoformat()
        return None


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

    @property
    def native_value(self) -> float | None:
        """Return the consumption value."""
        return self._get_extracted_attrs().get("consumption")


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
        self._attr_name = f"Concierge {self._service_id} Total Amount"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}_total_amount"

    @property
    def native_value(self) -> float | None:
        """Return the total bill amount."""
        return self._get_extracted_attrs().get("total_amount")


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
