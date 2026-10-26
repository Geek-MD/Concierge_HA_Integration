"""Regressions for twelve-month totals and duplicate bill handling."""
from datetime import date
import importlib.util
from pathlib import Path
import unittest

_PATH = Path(__file__).parents[1] / "custom_components/concierge_ha_integration/cost_history.py"
_SPEC = importlib.util.spec_from_file_location("concierge_cost_history", _PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class CostHistoryTests(unittest.TestCase):
    """Exercise real billing aggregation instead of sensor sample sums."""

    def test_calendar_window_crosses_year(self) -> None:
        self.assertEqual(_MODULE.months_ending(date(2026, 2, 1))[0], "2025-03")
        self.assertEqual(_MODULE.months_ending(date(2026, 2, 1))[-1], "2026-02")
        self.assertEqual(len(_MODULE.months_ending(date(2026, 2, 1))), 12)

    def test_duplicate_and_corrected_bills(self) -> None:
        history = {}
        today = date(2026, 10, 26)
        issued = date(2026, 10, 1)
        self.assertTrue(_MODULE.record_bill(history, "water", issued, 100, today))
        self.assertFalse(_MODULE.record_bill(history, "water", issued, 100, today))
        _MODULE.record_bill(history, "water", issued, 120, today)
        _MODULE.record_bill(history, "water", date(2026, 10, 15), 30, today)
        self.assertEqual(_MODULE.monthly_totals(history["water"], today)[-1]["amount"], 150)

    def test_unknown_is_distinct_from_zero(self) -> None:
        history = {}
        today = date(2026, 10, 26)
        _MODULE.record_bill(history, "gas", date(2026, 10, 1), 0, today)
        totals = _MODULE.monthly_totals(history["gas"], today)
        self.assertIsNone(totals[-2]["amount"])
        self.assertEqual(totals[-1]["amount"], 0)

    def test_invalid_and_outside_window_values_are_ignored(self) -> None:
        today = date(2026, 10, 26)
        for amount in (None, True, "100", float("nan"), float("inf")):
            self.assertFalse(_MODULE.record_bill({}, "water", today, amount, today))
        for issued in (date(2025, 10, 31), date(2026, 10, 27)):
            self.assertFalse(_MODULE.record_bill({}, "water", issued, 100, today))

    def test_services_stay_separate_and_old_months_expire(self) -> None:
        history = {}
        today = date(2026, 10, 26)
        _MODULE.record_bill(history, "water", date(2025, 11, 1), 100, today)
        _MODULE.record_bill(history, "gas", today, 200, today)
        _MODULE.record_bill(history, "water", date(2026, 11, 1), 300, date(2026, 11, 2))
        self.assertNotIn("2025-11-01", history["water"])
        self.assertEqual(_MODULE.monthly_totals(history["gas"], today)[-1]["amount"], 200)
