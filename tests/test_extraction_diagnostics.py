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
    common_expenses_extraction_status,
    set_common_expenses_diagnostics,
)


class ExtractionDiagnosticsTests(unittest.TestCase):
    """Verify completeness classification and stored diagnostics."""

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


if __name__ == "__main__":
    unittest.main()
