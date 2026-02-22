"""Sensor platform for Concierge Services."""
from __future__ import annotations

import email
import imaplib
import logging
from datetime import timedelta
from email.header import decode_header
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
    DOMAIN,
    CONF_IMAP_SERVER,
    CONF_IMAP_PORT,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERVICES,
)
from .attribute_extractor import extract_attributes_from_email_body, _strip_html

_LOGGER = logging.getLogger(__name__)

# Update interval for checking mail server connection
SCAN_INTERVAL = timedelta(minutes=30)

# Store service metadata (name mapping)
# This will be populated from config entry data
SERVICE_METADATA: dict[str, dict[str, str]] = {}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Concierge Services sensors."""
    coordinator = ConciergeServicesCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()
    
    # Get configured services and their metadata
    configured_services = config_entry.data.get(CONF_SERVICES, [])
    services_metadata = config_entry.data.get("services_metadata", {})
    
    # Create sensors for each configured service
    entities: list[SensorEntity] = []
    
    for service_id in configured_services:
        # Get service name from metadata
        service_name = services_metadata.get(service_id, {}).get("name", service_id.replace('_', ' ').title())
        
        entities.append(
            ConciergeServiceSensor(coordinator, config_entry, service_id, service_name)
        )
    
    # Also add connection sensor
    entities.append(ConciergeServicesConnectionSensor(coordinator, config_entry))
    
    async_add_entities(entities)


class ConciergeServicesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from IMAP server."""
        try:
            return await self.hass.async_add_executor_job(self._fetch_service_data)
        except Exception as err:
            raise UpdateFailed(f"Error communicating with IMAP server: {err}") from err

    def _fetch_service_data(self) -> dict[str, Any]:
        """Fetch service data from IMAP."""
        assert self.config_entry is not None
        imap = None
        result: dict[str, Any] = {
            "connection_status": "Problem",
            "services": {},
        }

        try:
            data = self.config_entry.data
            imap = imaplib.IMAP4_SSL(data[CONF_IMAP_SERVER], data[CONF_IMAP_PORT])
            imap.login(data[CONF_EMAIL], data[CONF_PASSWORD])
            result["connection_status"] = "OK"
            
            # Get configured services and their metadata
            configured_services = data.get(CONF_SERVICES, [])
            services_metadata = data.get("services_metadata", {})
            
            # For each service, find the latest email
            for service_id in configured_services:
                service_meta = services_metadata.get(service_id, {})
                
                # Search for emails matching this service and extract attributes
                service_data = self._find_latest_email_for_service(imap, service_id, service_meta)
                
                result["services"][service_id] = service_data
            
        except imaplib.IMAP4.error as err:
            _LOGGER.warning(
                "IMAP authentication failed for %s: %s",
                self.config_entry.data[CONF_EMAIL],
                err,
            )
            result["connection_status"] = "Problem"
        except Exception as err:
            _LOGGER.warning(
                "IMAP connection failed for %s@%s:%s: %s",
                self.config_entry.data[CONF_EMAIL],
                self.config_entry.data[CONF_IMAP_SERVER],
                self.config_entry.data[CONF_IMAP_PORT],
                err,
            )
            result["connection_status"] = "Problem"
        finally:
            if imap is not None:
                try:
                    imap.logout()
                except Exception:
                    pass  # Ignore logout errors
        
        return result
    
    def _find_latest_email_for_service(
        self, imap: imaplib.IMAP4_SSL, service_id: str, service_meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Find the latest email for a service and extract attributes."""
        result: dict[str, Any] = {
            "last_updated": None,
            "attributes": {},
        }
        
        try:
            imap.select("INBOX")
            
            # Search for all emails
            status, messages = imap.search(None, "ALL")
            
            if status != "OK":
                return result
            
            email_ids = messages[0].split()
            
            # Scan recent emails (last 50)
            email_ids = email_ids[-50:]
            
            latest_date = None
            latest_attributes: dict[str, Any] = {}
            
            # Get sample from and subject from metadata for matching
            sample_from = service_meta.get("sample_from", "")
            sample_subject = service_meta.get("sample_subject", "")
            service_name = service_meta.get("name", "")
            
            for email_id in reversed(email_ids):  # Start from most recent
                try:
                    # Fetch email headers
                    status, msg_data = imap.fetch(email_id, "(RFC822)")
                    
                    if status != "OK":
                        continue
                    
                    raw_email = msg_data[0][1]  # type: ignore[index]
                    msg = email.message_from_bytes(raw_email)  # type: ignore[arg-type]
                    
                    # Check if email has attachments (requirement: bills usually come as attachments)
                    if not self._has_attachments(msg):
                        continue
                    
                    # Get from and subject
                    from_header = msg.get("From", "")
                    subject_header = msg.get("Subject", "")
                    date_header = msg.get("Date", "")
                    
                    # Decode headers
                    from_addr = self._decode_mime_words(from_header)
                    subject = self._decode_mime_words(subject_header)
                    
                    # Get email body
                    body = self._get_email_body(msg)
                    
                    # Check if this email matches the service
                    if self._matches_service(service_id, service_name, sample_from, sample_subject, 
                                            from_addr, subject, body):
                        # Parse date
                        email_date = None
                        if date_header:
                            try:
                                from email.utils import parsedate_to_datetime
                                email_date = parsedate_to_datetime(date_header)
                                if latest_date is None or email_date > latest_date:
                                    latest_date = email_date
                                    
                                    # Extract attributes from this email
                                    latest_attributes = extract_attributes_from_email_body(subject, body)
                            except Exception:
                                pass
                        
                        # Found a match, no need to check older emails
                        if latest_date:
                            break
                
                except Exception as err:
                    _LOGGER.debug("Error processing email %s: %s", email_id, err)
                    continue
            
            result["last_updated"] = latest_date
            result["attributes"] = latest_attributes
            
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
        body: str
    ) -> bool:
        """Check if email matches a service based on flexible patterns."""
        import re
        
        combined_text = f"{from_addr} {subject} {body}".lower()
        
        # Extract domain from sample_from for matching
        if sample_from:
            domain_match = re.search(r'@([a-zA-Z0-9\-]+)\.[a-zA-Z]+', sample_from)
            if domain_match:
                domain = domain_match.group(1)
                if domain.lower() in from_addr.lower():
                    return True
        
        # Check if service name appears in the email
        if service_name:
            # Split service name into words and check if they appear
            words = service_name.lower().split()
            if len(words) > 0:
                # Check if all significant words appear
                matches = sum(1 for word in words if len(word) > 3 and word in combined_text)
                if matches >= len([w for w in words if len(w) > 3]):
                    return True
        
        # Check if service_id patterns appear (with underscores as wildcards)
        service_pattern = service_id.replace('_', '.*')
        if re.search(service_pattern, combined_text, re.IGNORECASE):
            return True
        
        return False


class ConciergeServiceSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor for a specific service (e.g., Aguas Andinas)."""

    def __init__(
        self,
        coordinator: ConciergeServicesCoordinator,
        config_entry: ConfigEntry,
        service_id: str,
        service_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._service_id = service_id
        self._service_name = service_name
        self._config_entry = config_entry
        self._attr_name = f"Concierge Services - {service_name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{service_id}"
        self._attr_icon = "mdi:file-document-outline"
        
        # Set device info to group this sensor under a service device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{service_id}")},
            name=service_name,
            manufacturer="Concierge Services",
            model="Service Account",
            via_device=(DOMAIN, config_entry.entry_id),
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor (last update date)."""
        if not self.coordinator.data:
            return None
        
        service_data = self.coordinator.data.get("services", {}).get(self._service_id)
        if not service_data:
            return None
        
        last_updated = service_data.get("last_updated")
        if last_updated:
            # Format as ISO date
            return last_updated.date().isoformat()
        
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs: dict[str, Any] = {
            "service_id": self._service_id,
            "service_name": self._service_name,
        }
        
        if self.coordinator.data:
            service_data = self.coordinator.data.get("services", {}).get(self._service_id)
            if service_data:
                last_updated = service_data.get("last_updated")
                if last_updated:
                    attrs["last_updated_datetime"] = last_updated.isoformat()
                
                # Add extracted attributes from email
                extracted_attrs = service_data.get("attributes", {})
                if extracted_attrs:
                    # Add all extracted attributes with a prefix to identify them
                    for key, value in extracted_attrs.items():
                        # Skip internal metadata fields
                        if not key.startswith("_"):
                            attrs[key] = value
                    
                    # Add extraction metadata
                    if "_attributes_found" in extracted_attrs:
                        attrs["attributes_extracted_count"] = extracted_attrs["_attributes_found"]
        
        return attrs


class ConciergeServicesConnectionSensor(CoordinatorEntity[ConciergeServicesCoordinator], SensorEntity):
    """Sensor to monitor mail server connection status."""

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
        
        # Set device info to group this sensor under the main integration device
        # Use friendly_name if available, otherwise use email
        friendly_name = config_entry.data.get("friendly_name", config_entry.data[CONF_EMAIL])
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
    def extra_state_attributes(self) -> dict[str, str]:
        """Return additional state attributes."""
        assert self.coordinator.config_entry is not None
        return {
            "email": self.coordinator.config_entry.data[CONF_EMAIL],
            "imap_server": self.coordinator.config_entry.data[CONF_IMAP_SERVER],
            "imap_port": self.coordinator.config_entry.data[CONF_IMAP_PORT],
        }
