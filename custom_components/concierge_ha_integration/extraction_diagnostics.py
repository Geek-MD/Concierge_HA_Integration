"""Extraction diagnostics shared by Concierge entities and the coordinator."""
from __future__ import annotations

from typing import Any

EXTRACTION_STATUS = "extraction_status"
EXTRACTION_SOURCE = "extraction_source"
EXTRACTION_ERROR = "extraction_error"

EXTRACTION_SUCCESS = "success"
EXTRACTION_PARTIAL = "partial"
EXTRACTION_FAILED = "failed"

# These are the monetary values exposed by the primary Gastos Comunes sensors.
COMMON_EXPENSES_CORE_FIELDS: tuple[str, ...] = (
    "gastos_comunes_amount",
    "subtotal",
    "fixed_charge",
    "gc_total",
)


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
