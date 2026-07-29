"""Tests for billing-cycle status calculations."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

_MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "concierge_ha_integration"
    / "billing.py"
)
_SPEC = importlib.util.spec_from_file_location("concierge_billing", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
is_bill_overdue = _MODULE.is_bill_overdue
parse_bill_date = _MODULE.parse_bill_date


class BillingStatusTests(unittest.TestCase):
    """Verify service-specific billing intervals."""

    def test_monthly_bill_becomes_overdue_after_one_month(self) -> None:
        """A monthly service should report a problem after one month."""
        last_updated = datetime(2026, 1, 15, tzinfo=timezone.utc)

        self.assertTrue(
            is_bill_overdue(
                last_updated,
                datetime(2026, 2, 16, tzinfo=timezone.utc),
                1,
            )
        )

    def test_bill_date_parses_common_chilean_formats(self) -> None:
        """Dates printed on bills should normalize independently of email dates."""
        self.assertEqual(parse_bill_date("20-07-2026").isoformat(), "2026-07-20")
        self.assertEqual(parse_bill_date("20/07/2026").isoformat(), "2026-07-20")
        self.assertEqual(parse_bill_date("02-JUL-2026").isoformat(), "2026-07-02")
        self.assertEqual(
            parse_bill_date("20 de julio de 2026").isoformat(), "2026-07-20"
        )

    def test_invalid_bill_date_has_no_transport_timestamp_fallback(self) -> None:
        """Missing or malformed bill dates must leave the status unresolved."""
        self.assertIsNone(parse_bill_date(None))
        self.assertIsNone(parse_bill_date("correo recibido ayer"))

    def test_bimonthly_bill_remains_current_during_second_month(self) -> None:
        """A two-month service such as Metrogas should remain current longer."""
        last_updated = datetime(2026, 1, 15, tzinfo=timezone.utc)

        self.assertFalse(
            is_bill_overdue(
                last_updated,
                datetime(2026, 3, 15, tzinfo=timezone.utc),
                2,
            )
        )
        self.assertTrue(
            is_bill_overdue(
                last_updated,
                datetime(2026, 3, 16, tzinfo=timezone.utc),
                2,
            )
        )
