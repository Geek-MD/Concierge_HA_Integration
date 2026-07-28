"""Extraction diagnostics shared by Concierge entities and the coordinator."""
from __future__ import annotations

from typing import Any

EXTRACTION_STATUS = "extraction_status"
EXTRACTION_SOURCE = "extraction_source"
EXTRACTION_ERROR = "extraction_error"

EXTRACTION_SUCCESS = "success"
EXTRACTION_PARTIAL = "partial"
EXTRACTION_FAILED = "failed"


def should_attempt_addon(
    availability: bool | None, *, forced_refresh: bool = False
) -> bool:
    """Return whether PDF extraction should call the Concierge add-on.

    A manual refresh is an explicit request to retry the complete extraction
    pipeline, so it must not be blocked by a failed or stale health check. The
    OCR request still has the internal extractor as its fallback.
    """
    return forced_refresh or availability is not False


# These are the monetary values exposed by the primary Gastos Comunes sensors.
COMMON_EXPENSES_CORE_FIELDS: tuple[str, ...] = (
    "gastos_comunes_amount",
    "subtotal",
    "fixed_charge",
    "gc_total",
)

COMMON_EXPENSES_HOT_WATER_FIELDS: tuple[str, ...] = (
    "hot_water_consumption",
    "hot_water_cost_per_m3",
    "hot_water_amount",
    "hot_water_reading_prev",
    "hot_water_reading_curr",
)

COMMON_EXPENSES_SENSOR_FIELDS: tuple[str, ...] = (
    *COMMON_EXPENSES_CORE_FIELDS,
    "funds_provision",
    *COMMON_EXPENSES_HOT_WATER_FIELDS,
)


def _number(attrs: dict[str, Any], key: str) -> float | int | None:
    """Return a finite numeric attribute, excluding booleans."""
    value = attrs.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (
        value != value or value in (float("inf"), -float("inf"))
    ):
        return None
    return value


def reconcile_common_expenses_amounts(
    attrs: dict[str, Any],
    *,
    confidence: dict[str, float] | None = None,
    derived_score: float = 60.0,
    override_score: float = 100.0,
) -> None:
    """Reconcile OCR amounts using the arithmetic printed on the bill."""
    scores = confidence if confidence is not None else {}

    def overridden(key: str) -> bool:
        return scores.get(key, 0) >= override_score

    def set_derived(key: str, value: float | int) -> None:
        if overridden(key):
            return
        attrs[key] = value
        scores[key] = derived_score

    building_total = _number(attrs, "building_total_expense")
    alicuota = _number(attrs, "alicuota")
    bill = _number(attrs, "gastos_comunes_amount")
    if building_total is not None and alicuota is not None:
        expected_bill = round(building_total * alicuota / 100)
        if expected_bill > 0:
            set_derived("gastos_comunes_amount", expected_bill)
            bill = _number(attrs, "gastos_comunes_amount")

    funds_pct = _number(attrs, "fondos_pct")
    funds = _number(attrs, "fondos_amount")
    if bill is not None and funds_pct is not None:
        expected_funds = round(bill * funds_pct / 100)
        if expected_funds >= 0:
            set_derived("fondos_amount", expected_funds)
            funds = _number(attrs, "fondos_amount")
    elif bill is not None:
        subtotal = _number(attrs, "subtotal_departamento")
        if subtotal is None:
            subtotal = _number(attrs, "subtotal")
        if subtotal is not None and subtotal >= bill:
            set_derived("fondos_amount", subtotal - bill)
            funds = _number(attrs, "fondos_amount")

    if funds is not None:
        set_derived("funds_provision", funds)

    if bill is not None and funds is not None:
        subtotal = bill + funds
        set_derived("subtotal_departamento", subtotal)
        set_derived("subtotal", subtotal)
    else:
        subtotal = _number(attrs, "subtotal_departamento")
        if subtotal is None:
            subtotal = _number(attrs, "subtotal")
        if subtotal is not None:
            set_derived("subtotal_departamento", subtotal)
            set_derived("subtotal", subtotal)

    fixed_charge = _number(attrs, "fixed_charge")
    cargo_fijo = _number(attrs, "cargo_fijo")
    if fixed_charge is not None:
        set_derived("cargo_fijo", fixed_charge)
        cargo_fijo = _number(attrs, "cargo_fijo")
    elif cargo_fijo is not None:
        set_derived("fixed_charge", cargo_fijo)
        fixed_charge = _number(attrs, "fixed_charge")

    total_amount = _number(attrs, "total_amount")
    subtotal = _number(attrs, "subtotal")
    charge = fixed_charge if fixed_charge is not None else cargo_fijo
    hot_water = _number(attrs, "hot_water_amount")
    if hot_water is None:
        hot_water = _number(attrs, "subtotal_consumo")
    if total_amount is not None:
        set_derived("gc_total", total_amount)
    elif subtotal is not None:
        computed_total = subtotal
        if hot_water is not None:
            computed_total += hot_water
        if charge is not None:
            computed_total += charge
        set_derived("gc_total", computed_total)


def common_expenses_needs_fallback(attrs: dict[str, Any]) -> bool:
    """Return whether another extractor should be used to fill sensor values."""
    return any(attrs.get(key) is None for key in COMMON_EXPENSES_SENSOR_FIELDS)


def merge_missing_common_expenses_values(
    current: dict[str, Any], fallback: dict[str, Any]
) -> list[str]:
    """Fill missing sensor values and their confidence from a fallback result."""
    copied: list[str] = []
    current_confidence = current.setdefault("_confidence", {})
    fallback_confidence = fallback.get("_confidence", {})
    for key in COMMON_EXPENSES_SENSOR_FIELDS:
        if current.get(key) is None and fallback.get(key) is not None:
            current[key] = fallback[key]
            copied.append(key)
            if key in fallback_confidence:
                current_confidence[key] = fallback_confidence[key]
    return copied


def merge_missing_extracted_attributes(
    current: dict[str, Any], fallback: dict[str, Any]
) -> list[str]:
    """Fill every missing public extraction attribute from another extractor."""
    copied: list[str] = []
    current_confidence = current.setdefault("_confidence", {})
    fallback_confidence = fallback.get("_confidence", {})
    for key, value in fallback.items():
        if key.startswith("_") or value is None or current.get(key) is not None:
            continue
        current[key] = value
        copied.append(key)
        if key in fallback_confidence:
            current_confidence[key] = fallback_confidence[key]
    return copied


def common_expenses_extraction_status(attrs: dict[str, Any]) -> str:
    """Classify Gastos Comunes extraction completeness."""
    present = sum(attrs.get(key) is not None for key in COMMON_EXPENSES_CORE_FIELDS)
    if present == len(COMMON_EXPENSES_CORE_FIELDS):
        return EXTRACTION_SUCCESS
    if present:
        return EXTRACTION_PARTIAL
    return EXTRACTION_FAILED


def set_common_expenses_diagnostics(
    attrs: dict[str, Any],
    *,
    source: str,
    error: str | None = None,
) -> str:
    """Store extraction diagnostics and return the resulting status."""
    status = common_expenses_extraction_status(attrs)
    attrs[EXTRACTION_STATUS] = status
    attrs[EXTRACTION_SOURCE] = source
    if error or status == EXTRACTION_FAILED:
        attrs[EXTRACTION_ERROR] = error or "no_usable_common_expenses_attributes"
    else:
        attrs.pop(EXTRACTION_ERROR, None)
    return status
