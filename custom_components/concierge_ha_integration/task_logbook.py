"""Helpers to write Concierge task entries in Home Assistant Logbook."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

from .const import TASK_LOGBOOK_DOMAIN, TASK_LOGBOOK_NAME

_EVENT_LOGBOOK_ENTRY = "logbook_entry"


@callback
def async_log_task(hass: HomeAssistant, message: str) -> None:
    """Emit a task entry into Home Assistant Logbook under a dedicated domain."""
    hass.bus.async_fire(
        _EVENT_LOGBOOK_ENTRY,
        {
            "domain": TASK_LOGBOOK_DOMAIN,
            "name": TASK_LOGBOOK_NAME,
            "message": message,
        },
    )
