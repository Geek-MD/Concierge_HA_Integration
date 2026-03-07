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
    SERVICE_TYPE_UNKNOWN,
)
from .attribute_extractor import extract_attributes_from_email_body, _strip_html
from .pdf_downloader import download_pdf_from_email, purge_old_pdfs
from .service_detector import classify_service_type

_LOGGER = logging.getLogger(__name__)

# Update interval for checking mail server connection
SCAN_INTERVAL = timedelta(minutes=30)

# Standard attributes exposed by each service sensor.
# Any attribute not extractable from the email defaults to 0.
_STANDARD_ATTRS: tuple[str, ...] = (
    "folio",
    "billing_period_start",
    "billing_period_end",
    "customer_number",
    "address",
    "due_date",
    "total_amount",
    "consumption",
    "consumption_unit",
)

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

    One connection sensor is created for the main entry (hub device).
    One service sensor is created for every subentry (service device),
    each associated with its own subentry so it appears correctly grouped
    in the Home Assistant device registry.
    """
    # Effective config merges original data with any options overrides
    effective_cfg = {**config_entry.data, **config_entry.options}

    coordinator = ConciergeServicesCoordinator(hass, config_entry, effective_cfg)
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator so __init__ can clean it up on unload
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    # Main connection sensor (hub device) — not linked to any subentry
    async_add_entities([ConciergeServicesConnectionSensor(coordinator, config_entry)])

    # One service sensor per subentry, each linked to its own subentry so
    # devices appear grouped under the subentry in the HA UI (not under
    # "Dispositivos que no pertenecen a una subentrada").
    for subentry_id, subentry in config_entry.subentries.items():  # type: ignore[attr-defined]
        async_add_entities(
            [ConciergeServiceSensor(coordinator, config_entry, subentry_id, subentry.data)],
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
            imap = imaplib.IMAP4_SSL(cfg[CONF_IMAP_SERVER], cfg[CONF_IMAP_PORT])
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
                                    except Exception as pdf_err:
                                        _LOGGER.debug(
                                            "PDF download skipped for service '%s': %s",
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


class ConciergeServiceSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor for a specific service device (one per subentry)."""

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        subentry_id: str,
        subentry_data: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
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
        self._attr_name = f"Concierge Services - {self._service_name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{subentry_id}"
        self._attr_icon = "mdi:file-document-outline"

        # Service device linked to the main hub device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{subentry_id}")},
            name=self._service_name,
            manufacturer="Concierge Services",
            model="Service Account",
            via_device=(DOMAIN, config_entry.entry_id),
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor (last update date)."""
        if not self.coordinator.data:
            return None

        service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
        if not service_data:
            return None

        last_updated = service_data.get("last_updated")
        if last_updated:
            return last_updated.date().isoformat()

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes.

        All standard attributes are always present.  If the value could not be
        extracted from the email the attribute defaults to ``0``.
        """
        service_type = self._subentry_data.get(CONF_SERVICE_TYPE, SERVICE_TYPE_UNKNOWN)

        # Initialise every standard attribute with its default value (0)
        attrs: dict[str, Any] = {
            "service_id": self._service_id,
            "service_name": self._service_name,
            "service_type": service_type,
            "last_updated_datetime": 0,
            "folio": 0,
            "billing_period_start": 0,
            "billing_period_end": 0,
            "customer_number": 0,
            "address": 0,
            "due_date": 0,
            "icon": "mdi:file-document-outline",
            "friendly_name": self._service_name,
            "total_amount": 0,
            "consumption": 0,
            "consumption_unit": 0,
        }

        if self.coordinator.data:
            service_data = self.coordinator.data.get("services", {}).get(self._subentry_id)
            if service_data:
                last_updated = service_data.get("last_updated")
                if last_updated:
                    attrs["last_updated_datetime"] = last_updated.isoformat()

                extracted_attrs = service_data.get("attributes", {})
                if extracted_attrs:
                    # Override standard attributes with extracted values
                    for key in _STANDARD_ATTRS:
                        value = extracted_attrs.get(key)
                        if value is not None:
                            attrs[key] = value

                    # Include pdf_path when a bill PDF was downloaded
                    if pdf_path := extracted_attrs.get("pdf_path"):
                        attrs["pdf_path"] = pdf_path

        return attrs


class ConciergeServicesConnectionSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor to monitor mail server connection status (main hub device)."""

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

        friendly_name = config_entry.data.get(
            "friendly_name", config_entry.data.get(CONF_EMAIL, "Concierge Services")
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=friendly_name,
            manufacturer="Concierge Services",
            model="Email Integration",
            configuration_url="https://github.com/Geek-MD/Concierge_Services",
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
