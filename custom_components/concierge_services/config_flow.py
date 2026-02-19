"""Config flow for Concierge Services integration."""
from __future__ import annotations

import imaplib
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_IMAP_SERVER,
    CONF_IMAP_PORT,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERVICES,
    DEFAULT_IMAP_PORT,
)
from .service_detector import detect_services_from_imap

_LOGGER = logging.getLogger(__name__)

# Configuration schema
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAP_SERVER, description={"suggested_value": "imap.gmail.com"}): str,
        vol.Required(CONF_IMAP_PORT, default=DEFAULT_IMAP_PORT): int,
        vol.Required(CONF_EMAIL, description={"suggested_value": "user@gmail.com"}): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_imap_connection(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the IMAP connection."""
    def _test_connection() -> bool:
        """Test IMAP connection in executor."""
        try:
            # Connect to IMAP server
            imap = imaplib.IMAP4_SSL(data[CONF_IMAP_SERVER], data[CONF_IMAP_PORT])
            
            # Try to authenticate
            imap.login(data[CONF_EMAIL], data[CONF_PASSWORD])
            
            # Logout if successful
            imap.logout()
            
            return True
        except imaplib.IMAP4.error as err:
            _LOGGER.error("IMAP authentication failed: %s", err)
            raise InvalidAuth from err
        except Exception as err:
            _LOGGER.error("IMAP connection failed: %s", err)
            raise CannotConnect from err
    
    # Run the connection test in executor to avoid blocking
    try:
        await hass.async_add_executor_job(_test_connection)
    except (CannotConnect, InvalidAuth):
        raise
    
    # Return info that can be used to create the entry
    return {"title": data[CONF_EMAIL]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Concierge Services."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._imap_data: dict[str, Any] = {}
        self._detected_services: list[Any] = []
        self._services_metadata: dict[str, dict[str, Any]] = {}

    async def async_step_user(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            # Check if this email is already configured
            await self.async_set_unique_id(user_input[CONF_EMAIL])
            self._abort_if_unique_id_configured()
            
            try:
                await validate_imap_connection(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Store IMAP data and proceed to naming/area selection
                self._imap_data = user_input
                return await self.async_step_finalize()

        return self.async_show_form(  # type: ignore[return-value]
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_finalize(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the finalization step with friendly name and area."""
        errors: dict[str, str] = {}
        
        # Get available areas
        area_registry = ar.async_get(self.hass)
        areas = area_registry.async_list_areas()
        area_options = {area.id: area.name for area in areas}
        area_options[""] = "No area"
        
        if user_input is not None:
            # Store friendly name and area, then move to service selection
            self._imap_data["friendly_name"] = user_input.get("friendly_name", self._imap_data[CONF_EMAIL])
            self._imap_data["area_id"] = user_input.get("area_id", "")
            
            # Proceed to service detection
            return await self.async_step_detect_services()
        
        # Create schema for finalization
        finalize_schema = vol.Schema(
            {
                vol.Optional(
                    "friendly_name",
                    default=self._imap_data[CONF_EMAIL]
                ): str,
                vol.Optional("area_id", default=""): vol.In(area_options),
            }
        )
        
        return self.async_show_form(  # type: ignore[return-value]
            step_id="finalize",
            data_schema=finalize_schema,
            errors=errors,
            description_placeholders={
                "email": self._imap_data[CONF_EMAIL],
            },
        )
    
    async def async_step_detect_services(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Detect services from the email account."""
        errors: dict[str, str] = {}
        
        try:
            # Run service detection
            _LOGGER.info("Starting service detection for %s", self._imap_data[CONF_EMAIL])
            self._detected_services = await self.hass.async_add_executor_job(
                detect_services_from_imap,
                self._imap_data[CONF_IMAP_SERVER],
                self._imap_data[CONF_IMAP_PORT],
                self._imap_data[CONF_EMAIL],
                self._imap_data[CONF_PASSWORD],
                100,  # Scan last 100 emails
            )
            
            # Build services metadata
            for service in self._detected_services:
                self._services_metadata[service.service_id] = {
                    "name": service.service_name,
                    "sample_subject": service.sample_subject,
                    "sample_from": service.sample_from,
                    "email_count": service.email_count,
                }
            
            _LOGGER.info("Detected %d services: %s", len(self._detected_services), 
                        [s.service_name for s in self._detected_services])
            
            # Proceed to service selection
            return await self.async_step_select_services()
            
        except imaplib.IMAP4.error as err:
            _LOGGER.exception("IMAP error during service detection: %s", err)
            errors["base"] = "detection_failed"
            # Allow user to continue without services
            return await self._create_entry_with_services([])
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error detecting services: %s", err)
            errors["base"] = "detection_failed"
            # Allow user to continue without services
            return await self._create_entry_with_services([])
    
    async def async_step_select_services(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user select which services to configure."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            # Get selected services
            selected_service_ids = user_input.get(CONF_SERVICES, [])
            
            # Create the entry with selected services
            return await self._create_entry_with_services(selected_service_ids)
        
        # Build options for service selection
        if not self._detected_services:
            # No services detected, create entry without services
            _LOGGER.info("No services detected, creating entry without services")
            return await self._create_entry_with_services([])
        
        # Create checkboxes for each detected service
        service_options = {
            service.service_id: f"{service.service_name} ({service.email_count} emails)"
            for service in self._detected_services
        }
        
        # Pre-select all services by default
        default_services = [service.service_id for service in self._detected_services]
        
        select_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVICES,
                    default=default_services
                ): cv.multi_select(service_options),
            }
        )
        
        return self.async_show_form(  # type: ignore[return-value]
            step_id="select_services",
            data_schema=select_schema,
            errors=errors,
            description_placeholders={
                "count": str(len(self._detected_services)),
            },
        )
    
    async def _create_entry_with_services(self, selected_service_ids: list[str]) -> FlowResult:
        """Create the config entry with selected services."""
        # Filter metadata to only include selected services
        selected_metadata = {
            service_id: self._services_metadata[service_id]
            for service_id in selected_service_ids
            if service_id in self._services_metadata
        }
        
        # Combine all data
        config_data = {
            **self._imap_data,
            CONF_SERVICES: selected_service_ids,
            "services_metadata": selected_metadata,
        }
        
        title = self._imap_data.get("friendly_name", self._imap_data[CONF_EMAIL])
        
        _LOGGER.info(
            "Creating entry '%s' with %d services: %s",
            title,
            len(selected_service_ids),
            selected_service_ids,
        )
        
        return self.async_create_entry(  # type: ignore[return-value]
            title=title,
            data=config_data,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
