"""Config flow for Concierge Services integration."""
from __future__ import annotations

import imaplib
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

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
    DEFAULT_IMAP_PORT,
    DOMAIN,
)
from .service_detector import detect_services_from_imap

_LOGGER = logging.getLogger(__name__)

# Schema for the initial IMAP credential step
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMAP_SERVER, description={"suggested_value": "imap.gmail.com"}): str,
        vol.Required(CONF_IMAP_PORT, default=DEFAULT_IMAP_PORT): int,
        vol.Required(CONF_EMAIL, description={"suggested_value": "user@gmail.com"}): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_imap_connection(hass: Any, data: dict[str, Any]) -> None:
    """Validate the IMAP connection (runs in executor to avoid blocking)."""

    def _test_connection() -> None:
        try:
            imap = imaplib.IMAP4_SSL(data[CONF_IMAP_SERVER], data[CONF_IMAP_PORT])
            imap.login(data[CONF_EMAIL], data[CONF_PASSWORD])
            imap.logout()
        except imaplib.IMAP4.error as err:
            _LOGGER.error("IMAP authentication failed: %s", err)
            raise InvalidAuth from err
        except Exception as err:
            _LOGGER.error("IMAP connection failed: %s", err)
            raise CannotConnect from err

    await hass.async_add_executor_job(_test_connection)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Concierge Services.

    Only one instance of this integration is allowed (single_config_entry).
    The CONFIGURE button triggers OptionsFlowHandler.
    The ADD DEVICE button triggers ServiceSubentryFlowHandler.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._imap_data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Options flow  →  "CONFIGURE" button on the integration card
    # ------------------------------------------------------------------
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Return the options flow handler for reconfiguring IMAP settings."""
        return OptionsFlowHandler()

    # ------------------------------------------------------------------
    # Subentry flow  →  "ADD DEVICE" button on the integration card
    # ------------------------------------------------------------------
    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:  # type: ignore[name-defined]
        """Return the subentry types supported by this integration."""
        return {"service": ServiceSubentryFlowHandler}

    # ------------------------------------------------------------------
    # Initial setup steps
    # ------------------------------------------------------------------
    async def async_step_user(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial IMAP credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
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
                self._imap_data = user_input
                return await self.async_step_finalize()

        return self.async_show_form(  # type: ignore[return-value]
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_finalize(  # type: ignore[override]
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a friendly name and create the config entry."""
        if user_input is not None:
            friendly_name = user_input.get("friendly_name") or self._imap_data[CONF_EMAIL]
            return self.async_create_entry(  # type: ignore[return-value]
                title=friendly_name,
                data={**self._imap_data, "friendly_name": friendly_name},
            )

        finalize_schema = vol.Schema(
            {
                vol.Optional(
                    "friendly_name",
                    default=self._imap_data[CONF_EMAIL],
                ): str,
            }
        )

        return self.async_show_form(  # type: ignore[return-value]
            step_id="finalize",
            data_schema=finalize_schema,
            description_placeholders={"email": self._imap_data[CONF_EMAIL]},
        )


# ---------------------------------------------------------------------------
# Options flow  –  reconfigure IMAP credentials
# ---------------------------------------------------------------------------


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Allow the user to reconfigure the IMAP account credentials."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the reconfiguration form pre-filled with current values."""
        errors: dict[str, str] = {}

        # Effective config = original data merged with any saved options
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            # Validate the new credentials before saving
            merged = {**current, **user_input}
            try:
                await validate_imap_connection(self.hass, merged)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Persist new settings as options (overrides data on reload)
                return self.async_create_entry(title="", data=user_input)  # type: ignore[return-value]

        options_schema = vol.Schema(
            {
                vol.Required(CONF_IMAP_SERVER, default=current.get(CONF_IMAP_SERVER, "imap.gmail.com")): str,
                vol.Required(CONF_IMAP_PORT, default=current.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)): int,
                vol.Required(CONF_EMAIL, default=current.get(CONF_EMAIL, "")): str,
                vol.Required(CONF_PASSWORD, default=current.get(CONF_PASSWORD, "")): str,
                vol.Optional(
                    "friendly_name",
                    default=current.get("friendly_name", current.get(CONF_EMAIL, "")),
                ): str,
            }
        )

        return self.async_show_form(  # type: ignore[return-value]
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Subentry flow  –  add / reconfigure a service device
# ---------------------------------------------------------------------------


class ServiceSubentryFlowHandler(config_entries.ConfigSubentryFlow):  # type: ignore[name-defined]
    """Handle adding or reconfiguring a Concierge Services service device.

    Each subentry represents one service account (e.g. Aguas Andinas).
    """

    def __init__(self) -> None:
        """Initialize the subentry flow."""
        self._available_services: list[Any] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan for available services and let the user pick one."""
        errors: dict[str, str] = {}

        if user_input is not None:
            service_id = user_input[CONF_SERVICE_ID]
            selected = next(
                (s for s in self._available_services if s.service_id == service_id),
                None,
            )
            if selected is None:
                errors["base"] = "service_not_found"
            else:
                return self.async_create_entry(  # type: ignore[return-value]
                    title=selected.service_name,
                    data={
                        CONF_SERVICE_ID: selected.service_id,
                        CONF_SERVICE_NAME: selected.service_name,
                        CONF_SERVICE_TYPE: selected.service_type,
                        CONF_SAMPLE_FROM: selected.sample_from,
                        CONF_SAMPLE_SUBJECT: selected.sample_subject,
                        "email_count": selected.email_count,
                    },
                )

        # Scan inbox on the first pass (before showing the form)
        if not self._available_services:
            entry = self._get_entry()  # type: ignore[attr-defined]
            cfg = {**entry.data, **entry.options}
            try:
                all_services = await self.hass.async_add_executor_job(
                    detect_services_from_imap,
                    cfg[CONF_IMAP_SERVER],
                    cfg[CONF_IMAP_PORT],
                    cfg[CONF_EMAIL],
                    cfg[CONF_PASSWORD],
                    100,
                )
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Failed to detect services: %s", err)
                return self.async_abort(reason="detection_failed")  # type: ignore[return-value]

            # Remove services that are already configured as subentries
            existing_ids = {
                subentry.data.get(CONF_SERVICE_ID)
                for subentry in entry.subentries.values()
            }
            self._available_services = [
                s for s in all_services if s.service_id not in existing_ids
            ]

        if not self._available_services:
            return self.async_abort(reason="no_services_available")  # type: ignore[return-value]

        service_options = {
            s.service_id: f"{s.service_name} ({s.email_count} emails)"
            for s in self._available_services
        }

        return self.async_show_form(  # type: ignore[return-value]
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_SERVICE_ID): vol.In(service_options)}
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._available_services))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reconfigure an existing service subentry (rename it)."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
        current = subentry.data

        if user_input is not None:
            new_name = user_input.get(CONF_SERVICE_NAME) or current.get(CONF_SERVICE_NAME, "")
            return self.async_create_entry(  # type: ignore[return-value]
                title=new_name,
                data={**current, CONF_SERVICE_NAME: new_name},
            )

        return self.async_show_form(  # type: ignore[return-value]
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SERVICE_NAME,
                        default=current.get(CONF_SERVICE_NAME, ""),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "service_name": current.get(CONF_SERVICE_NAME, ""),
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

