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

from .const import (
    DOMAIN,
    CONF_IMAP_SERVER,
    CONF_IMAP_PORT,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_IMAP_PORT,
)

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
            # Combine IMAP data with friendly name and area
            config_data = {
                **self._imap_data,
                "friendly_name": user_input.get("friendly_name", self._imap_data[CONF_EMAIL]),
                "area_id": user_input.get("area_id", ""),
            }
            
            return self.async_create_entry(  # type: ignore[return-value]
                title=user_input.get("friendly_name", self._imap_data[CONF_EMAIL]),
                data=config_data,
            )
        
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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
