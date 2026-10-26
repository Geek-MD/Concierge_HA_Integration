"""Panel lifecycle tests with Home Assistant boundaries replaced by fakes."""
import ast
import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_cost_history import _MODULE as history_module

_ROOT = Path(__file__).parents[1] / "custom_components/concierge_ha_integration"


def panel_namespace():
    """Load actual panel classes without requiring a running HA installation."""
    tree = ast.parse((_ROOT / "panel.py").read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    namespace = {
        "asyncio": asyncio, "Store": Mock(), "callback": lambda fn: fn,
        "ToggleEntity": type("ToggleEntity", (), {}),
        "RestoreEntity": type("RestoreEntity", (), {}),
        "DOMAIN": "concierge_ha_integration", "PANEL_PATH": "concierge",
        "VISIBILITY_ENTITY": "input_boolean.concierge_show_panel",
        "MODULE_URL": "/concierge_panel/concierge-panel.js",
        "CONF_SERVICE_NAME": "service_name", "CONF_SERVICE_TYPE": "service_type",
        "DATA_INSTANCES": "entity_components",
        "dt_util": SimpleNamespace(now=lambda: SimpleNamespace(date=lambda: date(2026, 10, 26))),
        "parse_bill_date": lambda value: date.fromisoformat(value) if value else None,
        "record_bill": history_module.record_bill,
        "frontend": Mock(), "panel_custom": SimpleNamespace(async_register_panel=AsyncMock()),
    }
    exec("from __future__ import annotations\n" + ast.unparse(ast.Module(body=classes, type_ignores=[])), namespace)
    return namespace


class PanelTests(unittest.IsolatedAsyncioTestCase):
    """Verify real registration, restoration and recording decisions."""

    def setUp(self):
        self.ns = panel_namespace()
        self.hass = Mock()
        self.hass.states.is_state.return_value = True
        self.entry = SimpleNamespace(entry_id="entry", subentries={})
        self.panel = self.ns["ConciergePanel"](self.hass, self.entry, Mock())

    async def test_toggle_registers_and_removes_panel(self):
        await self.panel.async_visibility()
        register = self.ns["panel_custom"].async_register_panel
        self.assertEqual(register.await_count, 1)
        self.hass.states.is_state.return_value = False
        await self.panel.async_visibility()
        self.assertEqual(register.await_count, 1)
        self.assertEqual(self.ns["frontend"].async_remove_panel.call_count, 2)

    async def test_unload_flushes_history_and_prevents_reregistration(self):
        self.panel.store.async_save = AsyncMock()
        self.panel.history = {"water": {"2026-10-01": {"amount": 100, "month": "2026-10"}}}
        await self.panel.async_unload()
        self.panel.store.async_save.assert_awaited_once_with(self.panel.history)
        await self.panel.async_visibility()
        self.ns["panel_custom"].async_register_panel.assert_not_awaited()

    def test_backfill_and_current_totals_use_service_specific_amounts(self):
        self.entry.subentries = {"gc": SimpleNamespace(data={"service_type": "common_expenses"})}
        self.panel.coordinator.data = {"services": {"gc": {
            "historical_bills": [{"attributes": {"emission_date": "2026-09-01", "gc_total": 90}}],
            "attributes": {"emission_date": "2026-10-01", "gc_total": 100, "total_amount": 999},
        }}}
        self.panel._updated()
        self.assertEqual(self.panel.history["gc"]["2026-10-01"]["amount"], 100)
        self.assertEqual(self.panel.history["gc"]["2026-09-01"]["amount"], 90)
        self.panel._updated()
        self.assertEqual(len(self.panel.history["gc"]), 2)


class BackfillTests(unittest.TestCase):
    """Historical scans must not replace the live sensors with the oldest bill."""

    def test_backfill_preserves_latest_and_returns_all_bills(self):
        import email
        import imaplib
        import json
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        namespace = panel_namespace()
        exec((_ROOT / "const.py").read_text(), namespace)
        tree = ast.parse((_ROOT / "sensor.py").read_text())
        coordinator = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ConciergeServicesCoordinator")
        method = next(node for node in coordinator.body if isinstance(node, ast.FunctionDef) and node.name == "_find_latest_email_for_service")
        namespace.update({
            "email": email, "imaplib": imaplib, "datetime": datetime,
            "parsedate_to_datetime": parsedate_to_datetime, "_LOGGER": Mock(),
            "normalize_service_id": lambda value: value,
            "extract_attributes_from_email_body": lambda subject, body, kind: json.loads(body),
            "download_pdf_from_email": lambda *args, **kwargs: None,
            "dt_util": SimpleNamespace(DEFAULT_TIME_ZONE=timezone.utc),
        })
        exec("from __future__ import annotations\n" + ast.unparse(method), namespace)
        owner = SimpleNamespace(
            _decode_mime_words=lambda value: value,
            _get_email_body=lambda msg: msg.get_payload(),
            _matches_service=lambda *args: "test",
            _pdf_dir="/tmp", _cfg={},
        )
        imap = Mock()
        imap.search.return_value = ("OK", [b"1 2"])
        messages = {
            b"1": b'From: gas@example.test\nSubject: gas\n\n{"emission_date":"2026-09-01","total_amount":90}',
            b"2": b'From: gas@example.test\nSubject: gas\n\n{"emission_date":"2026-10-01","total_amount":100}',
        }
        imap.fetch.side_effect = lambda key, mode: ("OK", [(b"RFC822", messages[key])])
        # Avoid a relative import in the isolated function while retaining the
        # actual search, parsing, preservation and historical traversal code.
        method_source = ast.unparse(method).replace("from .cost_history import months_ending", "pass")
        namespace["months_ending"] = history_module.months_ending
        namespace["dt_util"].now = lambda: SimpleNamespace(date=lambda: date(2026, 10, 26))
        exec("from __future__ import annotations\n" + method_source, namespace)
        result = namespace["_find_latest_email_for_service"](
            owner, imap, {"service_type": "gas", "service_id": "gas"}, "gas", True
        )
        self.assertEqual(result["attributes"]["total_amount"], 100)
        self.assertEqual([bill["attributes"]["total_amount"] for bill in result["historical_bills"]], [90, 100])
        self.assertEqual(result["last_updated"].date(), date(2026, 10, 1))
        imap.search.assert_called_once_with(None, "SINCE", "01-Nov-2025")
