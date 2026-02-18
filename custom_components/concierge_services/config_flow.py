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
        vol.Required(CONF_IMAP_SERVER): str,
        vol.Required(CONF_IMAP_PORT, default=DEFAULT_IMAP_PORT): int,
        vol.Required(CONF_EMAIL): str,
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
                info = await validate_imap_connection(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)  # type: ignore[return-value]

        return self.async_show_form(  # type: ignore[return-value]
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
