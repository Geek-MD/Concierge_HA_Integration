"""Tests for extraction completeness diagnostics."""
from pathlib import Path
import sys
import unittest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "concierge_ha_integration"
    ),
)

from extraction_diagnostics import (  # noqa: E402
    EXTRACTION_ERROR,
    EXTRACTION_FAILED,
    EXTRACTION_PARTIAL,
    EXTRACTION_SOURCE,
    EXTRACTION_STATUS,
    EXTRACTION_SUCCESS,
    common_expenses_needs_fallback,
    common_expenses_extraction_status,
    merge_missing_common_expenses_values,
    reconcile_common_expenses_amounts,
    set_common_expenses_diagnostics,
    should_attempt_addon,
)


class ExtractionDiagnosticsTests(unittest.TestCase):
    """Verify completeness classification and stored diagnostics."""

    def test_force_refresh_retries_addon_after_failed_health_check(self) -> None:
        """A manual refresh must call OCR even if availability is cached false."""
        self.assertTrue(should_attempt_addon(False, forced_refresh=True))
        self.assertFalse(should_attempt_addon(False))
        self.assertTrue(should_attempt_addon(None))

    def test_common_expenses_extraction_success(self) -> None:
        attrs = {
            "gastos_comunes_amount": 100,
            "subtotal": 90,
            "fixed_charge": 10,
            "gc_total": 100,
        }

        self.assertEqual(
            common_expenses_extraction_status(attrs), EXTRACTION_SUCCESS
        )

    def test_common_expenses_extraction_partial(self) -> None:
        self.assertEqual(
            common_expenses_extraction_status({"subtotal": 90}),
            EXTRACTION_PARTIAL,
        )

    def test_partial_diagnostics_identify_missing_fields(self) -> None:
        attrs: dict[str, object] = {"gastos_comunes_amount": 90}

        set_common_expenses_diagnostics(attrs, source="addon_template")

        self.assertEqual(attrs[EXTRACTION_STATUS], EXTRACTION_PARTIAL)
        self.assertEqual(
            attrs[EXTRACTION_ERROR],
            "missing_required_fields:subtotal,fixed_charge,gc_total",
        )

    def test_common_expenses_extraction_failed(self) -> None:
        self.assertEqual(common_expenses_extraction_status({}), EXTRACTION_FAILED)

    def test_diagnostics_include_source_and_failure_reason(self) -> None:
        attrs: dict[str, object] = {}

        status = set_common_expenses_diagnostics(attrs, source="addon_raw")

        self.assertEqual(status, EXTRACTION_FAILED)
        self.assertEqual(attrs[EXTRACTION_STATUS], EXTRACTION_FAILED)
        self.assertEqual(attrs[EXTRACTION_SOURCE], "addon_raw")
        self.assertEqual(
            attrs[EXTRACTION_ERROR], "no_usable_common_expenses_attributes"
        )

    def test_reconciles_incorrect_bill_funds_subtotal_and_total(self) -> None:
        attrs = {
            "building_total_expense": 14_171_762,
            "alicuota": 0.95110,
            "fondos_pct": 5,
            "gastos_comunes_amount": 999_999,
            "fondos_amount": 88_888,
            "funds_provision": 88_888,
            "subtotal": 1_088_887,
            "hot_water_amount": 32_812,
            "fixed_charge": 16_136,
            "gc_total": 1_093_887,
        }
        confidence: dict[str, float] = {}

        reconcile_common_expenses_amounts(attrs, confidence=confidence)

        self.assertEqual(attrs["gastos_comunes_amount"], 134_788)
        self.assertEqual(attrs["funds_provision"], 6_739)
        self.assertEqual(attrs["subtotal"], 141_527)
        self.assertEqual(attrs["gc_total"], 190_475)

    def test_reconciliation_preserves_manual_override(self) -> None:
        attrs = {
            "building_total_expense": 10_000_000,
            "alicuota": 1.25,
            "gastos_comunes_amount": 130_000,
        }

        reconcile_common_expenses_amounts(
            attrs,
            confidence={"gastos_comunes_amount": 100.0},
            override_score=100.0,
        )

        self.assertEqual(attrs["gastos_comunes_amount"], 130_000)

    def test_fallback_is_needed_when_hot_water_values_are_missing(self) -> None:
        attrs = {
            "gastos_comunes_amount": 100,
            "subtotal": 110,
            "fixed_charge": 10,
            "gc_total": 120,
        }

        self.assertTrue(common_expenses_needs_fallback(attrs))
        attrs.update(
            {
                "funds_provision": 10,
                "hot_water_consumption": 3.5,
                "hot_water_cost_per_m3": 1_000,
                "hot_water_amount": 3_500,
                "hot_water_reading_prev": 20,
                "hot_water_reading_curr": 23.5,
            }
        )
        self.assertFalse(common_expenses_needs_fallback(attrs))

    def test_partial_refresh_preserves_only_missing_sensor_values(self) -> None:
        current = {"fixed_charge": 20, "_confidence": {"fixed_charge": 80.0}}
        previous = {
            "gastos_comunes_amount": 100,
            "fixed_charge": 10,
            "hot_water_amount": 30,
            "_confidence": {
                "gastos_comunes_amount": 75.0,
                "fixed_charge": 75.0,
                "hot_water_amount": 75.0,
            },
        }

        copied = merge_missing_common_expenses_values(current, previous)

        self.assertEqual(
            copied, ["gastos_comunes_amount", "hot_water_amount"]
        )
        self.assertEqual(current["fixed_charge"], 20)
        self.assertEqual(current["gastos_comunes_amount"], 100)
        self.assertEqual(current["_confidence"]["hot_water_amount"], 75.0)


if __name__ == "__main__":
    unittest.main()
