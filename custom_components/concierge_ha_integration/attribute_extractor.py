"""Attribute extraction module for Concierge Services.

Extracts billing attributes from email body using targeted field patterns.
Only the standard fields required before PDF analysis are extracted.

Standard attributes (with default 0 when not found in the email):
    folio, billing_period_start, billing_period_end, customer_number,
    address, due_date, total_amount, consumption, consumption_unit.

Extraction is modularised by service type so that each utility category
(water, gas, electricity, …) can use patterns tuned to its own email
format.  The main entry point is :func:`extract_attributes_from_email_body`,
which accepts an optional *service_type* argument (one of the
``SERVICE_TYPE_*`` constants defined in :mod:`const`).
"""
from __future__ import annotations

import logging
import re
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Any

from .const import (
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)

_LOGGER = logging.getLogger(__name__)


# Patterns for billing period dates (start and end)
DATE_PATTERNS = [
    r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    r"([0-9]{1,2}\s+de\s+[a-zA-Z]+\s+de\s+[0-9]{4})",
    r"([A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})",
]


def _strip_html(html_text: str) -> str:
    """Strip HTML tags and decode entities, returning only visible text.

    Applies ``html.unescape()`` a second time after the parser so that
    double-encoded entities (e.g. ``&amp;oacute;`` → ``&oacute;`` → ``ó``)
    found in some utility company emails (e.g. Aguas Andinas) are fully
    decoded.
    """
    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self._parts.append(data)

        def get_text(self) -> str:
            return "\n".join(self._parts)

    parser = _TextExtractor()
    parser.feed(html_text)
    return _html_unescape(parser.get_text())


# Label patterns that precede a total-amount value
_TOTAL_LABELS = re.compile(
    r"(?:total\s+a\s+pagar|monto\s+total|total\s+factura|importe\s+total"
    r"|total\s+cuenta|valor\s+a\s+pagar|total)[:\s]+",
    re.IGNORECASE,
)

# Label patterns that precede a customer/account number
# "de" is optional to match both "Número de Cliente:" and "Número Cliente:" (Metrogas)
_CUSTOMER_LABELS = re.compile(
    r"(?:n[úu]mero\s+(?:de\s+)?(?:cuenta|cliente)|n[°º]\s*(?:cuenta|cliente)"
    r"|cuenta\s+n[°º]?|cliente\s+n[°º]?|customer\s+(?:number|no\.?)|account\s+(?:number|no\.?))"
    r"[:\s]+",
    re.IGNORECASE,
)

# Label patterns that precede an address value
_ADDRESS_LABELS = re.compile(
    r"(?:direcci[oó]n(?:\s+de\s+suministro)?|domicilio|address)[:\s]+",
    re.IGNORECASE,
)

# Label pattern that precedes a payment due date
_DUE_DATE_LABELS = re.compile(
    r"fecha\s+de\s+vencimiento[:\s]+",
    re.IGNORECASE,
)

# Currency amount pattern (reused by _extract_total_amount)
_AMOUNT_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)"
    r"|([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)\s*(?:CLP|USD|EUR|pesos?)",
    re.IGNORECASE,
)


def _parse_amount_to_int(raw: str) -> int:
    """Convert a formatted amount string to an integer.

    Handles Chilean/Spanish format (dot as thousands separator, comma as
    decimal) and English format (comma as thousands separator, dot as decimal).
    A dot or comma followed by exactly 3 digits is always treated as a
    thousands separator; followed by 1–2 digits it is treated as decimal
    (which is then discarded because the result is an integer).

    Examples::

        "12.013"   → 12013   # Chilean: dot = thousands separator
        "122.060"  → 122060  # Chilean: dot = thousands separator
        "1.234,56" → 1234    # Chilean: dot = thousands, comma = decimal
        "12,013"   → 12013   # English: comma = thousands separator
        "1,234.56" → 1234    # English: comma = thousands, dot = decimal
    """
    raw = raw.strip()
    m = re.search(r"([,.])(\d+)$", raw)
    if m:
        frac_digits = len(m.group(2))
        if frac_digits <= 2:
            # Last separator is decimal — strip the decimal part
            decimal_sep = m.group(1)
            raw = raw[: m.start()]
            thousands_sep = "," if decimal_sep == "." else "."
            raw = raw.replace(thousands_sep, "")
        else:
            # Last separator is a thousands separator — remove all separators
            raw = raw.replace(".", "").replace(",", "")
    raw = raw.strip()
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def _parse_consumption_to_float(raw: str) -> float:
    """Convert a formatted consumption string to a float.

    Handles Chilean/Spanish format (dot as thousands separator, comma as
    decimal) and English format (comma as thousands separator, dot as decimal).

    Examples::

        "12.5"   → 12.5    # decimal (1 frac digit → decimal sep)
        "12,5"   → 12.5    # Chilean decimal
        "1.500"  → 1500.0  # thousands separator (3 frac digits)
        "150"    → 150.0   # plain integer
    """
    raw = raw.strip()
    m = re.search(r"([,.])(\d+)$", raw)
    if m:
        frac_digits = len(m.group(2))
        if frac_digits <= 2:
            # Last separator is decimal — normalise to English dot notation
            decimal_sep = m.group(1)
            integer_part = raw[: m.start()]
            thousands_sep = "," if decimal_sep == "." else "."
            integer_part = integer_part.replace(thousands_sep, "")
            raw = f"{integer_part}.{m.group(2)}"
        else:
            # Last separator is a thousands separator — remove all separators
            raw = raw.replace(".", "").replace(",", "")
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0


def _extract_total_amount(text: str) -> int | None:
    """Return the total amount due (as integer) found in *text*, or None."""
    for label_match in _TOTAL_LABELS.finditer(text):
        rest = text[label_match.end():]
        amount_match = _AMOUNT_RE.search(rest[:60])
        if amount_match:
            raw = (amount_match.group(1) or amount_match.group(2) or "").strip()
            if raw:
                return _parse_amount_to_int(raw)
    # Fallback: first currency amount in the whole text
    amount_match = _AMOUNT_RE.search(text)
    if amount_match:
        raw = (amount_match.group(1) or amount_match.group(2) or "").strip()
        return _parse_amount_to_int(raw) if raw else None
    return None


def _extract_customer_number(text: str) -> str | None:
    """Return the customer/account number found in *text*, or None."""
    for label_match in _CUSTOMER_LABELS.finditer(text):
        rest = text[label_match.end():]
        # Grab first sequence of digits (possibly formatted with dots/dashes)
        num_match = re.search(r"([0-9][0-9.\-]{0,19})", rest[:80])
        if num_match:
            return num_match.group(1).strip()
    return None


def _extract_address(text: str) -> str | None:
    """Return the service address found in *text*, or None."""
    for label_match in _ADDRESS_LABELS.finditer(text):
        rest = text[label_match.end():]
        # Take up to end-of-line, trimmed
        line_match = re.search(r"([^\n\r]{5,120})", rest)
        if line_match:
            value = re.sub(r"\s+", " ", line_match.group(1)).strip()
            if value:
                return value
    return None


def _extract_due_date(text: str) -> str | None:
    """Return the payment due date found in *text*, or None.

    Looks for a ``Fecha de vencimiento`` label (common on Chilean utility
    bills) and returns the date that immediately follows it.
    """
    for label_match in _DUE_DATE_LABELS.finditer(text):
        rest = text[label_match.end():]
        for pattern in DATE_PATTERNS:
            date_match = re.search(pattern, rest[:60], re.IGNORECASE)
            if date_match:
                return date_match.group(1).strip()
    return None


def _extract_dates(text: str) -> dict[str, str]:
    """Extract billing period start and end dates from *text*."""
    dates: dict[str, str] = {}
    _DATE_KEYS = ["billing_period_start", "billing_period_end"]

    for pattern in DATE_PATTERNS:
        for i, match in enumerate(re.finditer(pattern, text, re.IGNORECASE)):
            if i >= len(_DATE_KEYS):
                break
            key = _DATE_KEYS[i]
            if key not in dates:
                dates[key] = match.group(1).strip()

    return dates


# ---------------------------------------------------------------------------
# Water-service extractor (reference implementation: Aguas Andinas)
# ---------------------------------------------------------------------------
# Consumption in cubic metres (m³ / M3)
_WATER_CONSUMPTION_RE = re.compile(
    r"([0-9]+(?:[.,][0-9]+)?)\s*m[³3]",
    re.IGNORECASE,
)
# Labels that precede meter-reading values
_WATER_READING_LABELS = re.compile(
    r"lectura\s+(?:anterior|actual|medidor)[:\s]+",
    re.IGNORECASE,
)
# Label for water meter number
_WATER_METER_LABELS = re.compile(
    r"(?:n[úu]mero\s+de\s+medidor|medidor\s+n[°º]?)[:\s]+",
    re.IGNORECASE,
)
# Aguas Andinas packs all billing info in one <td>:
# "ADDRESS    ACCOUNT_NUM    DATE al DATE"
# The ALL-CAPS address precedes the account number (5+ digits + dash + 1 digit).
# Requires 2+ spaces as separator so street numbers like "385-515" are not
# mistaken for account numbers (only 3 digits before the dash).
_WATER_AA_PACKED_RE = re.compile(
    r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9 ,\.\-/#]{5,}?)"
    r"\s{2,}"
    r"(\d{5,}-\d)\b",
)


def _extract_water_attributes(text: str) -> dict[str, Any]:
    """Extract water-service-specific attributes.

    Tuned for Aguas Andinas (reference email: February 2026).
    Key observations:
    - HTML-only email with double-encoded entities (fixed by ``_strip_html``).
    - Labels (Dirección, Número de Cuenta, Período) are in the left-hand
      ``<td>``; all values are packed in the right-hand ``<td>`` separated
      by multiple spaces: ``ADDRESS    ACCOUNT_NUM    DATE al DATE``.
    - Generic label-based extractors fail for this layout; the packed-values
      pattern below handles it.
    - Consumption (m³) is not in the email — only in the PDF attachment.

    Extracted fields (all optional):
        ``address``        – service address from packed-values paragraph
        ``customer_number``– account number (overrides wrong generic value)
        ``consumption``    – volume consumed (label-based only)
        ``consumption_unit``– unit of consumption (``"m3"``)
        ``meter_reading``  – meter reading value(s) found after a reading label
        ``meter_number``   – water meter identifier
    """
    attrs: dict[str, Any] = {}

    # Aguas Andinas packed-values: address + account number in one paragraph.
    # This overrides any wrong values the generic extractor may have produced
    # due to the table layout separating labels from values.
    aa_match = _WATER_AA_PACKED_RE.search(text)
    if aa_match:
        addr = re.sub(r"\s+", " ", aa_match.group(1)).strip()
        if addr:
            attrs["address"] = addr
        acct = aa_match.group(2).strip()
        if acct:
            attrs["customer_number"] = acct

    # Consumption volume (label-based; bare m³ fallback omitted to avoid
    # false positives — Aguas Andinas does not include consumption in the email)
    for label_match in _WATER_CONSUMPTION_RE.finditer(text):
        attrs["consumption"] = _parse_consumption_to_float(label_match.group(1).strip())
        attrs["consumption_unit"] = "m3"
        break

    # Meter readings (anterior / actual)
    readings: list[str] = []
    for label_match in _WATER_READING_LABELS.finditer(text):
        rest = text[label_match.end():]
        val_match = re.search(r"([0-9][0-9.,]{0,15})", rest[:60])
        if val_match:
            readings.append(val_match.group(1).strip())
    if readings:
        attrs["meter_reading"] = readings[0] if len(readings) == 1 else readings

    # Meter number
    for label_match in _WATER_METER_LABELS.finditer(text):
        rest = text[label_match.end():]
        num_match = re.search(r"([A-Za-z0-9][-A-Za-z0-9]{1,19})", rest[:60])
        if num_match:
            attrs["meter_number"] = num_match.group(1).strip()
            break

    return attrs


# ---------------------------------------------------------------------------
# Water-service PDF extractor (Aguas Andinas-specific; reference PDF: Feb 2026)
# ---------------------------------------------------------------------------
# Address: two ALL-CAPS lines after "SEÑOR RESIDENTE" in the header block.
# pdfminer preserves line breaks, so group(1) = street + number, group(2) = city.
_WATER_AA_PDF_ADDRESS_RE = re.compile(
    r"se[ñn]or\s+residente\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)",
    re.IGNORECASE,
)
# Account number follows the "Nro de cuenta" label at the bottom of the PDF.
# "\s+" spans the blank line pdfminer inserts between label and value.
_WATER_AA_PDF_ACCOUNT_RE = re.compile(
    r"nro\s+de\s+cuenta\s+(\d{5,}-\d)\b",
    re.IGNORECASE,
)
# Due date follows the "VENCIMIENTO" label (two-column layout; blank line between).
_WATER_AA_PDF_DUE_DATE_RE = re.compile(
    r"vencimiento\s+(\d{1,2}-[A-Z]{3}-\d{4})\b",
    re.IGNORECASE,
)
# Marks the start of the consumption/reading block.  pdfminer reads labels
# before values, so the CONSUMO TOTAL value is the last m3 match in the block
# that ends at the "MODALIDAD DE PRORRATEO" line.
_WATER_AA_PDF_CONSUMO_LABEL_RE = re.compile(
    r"consumo\s+total",
    re.IGNORECASE,
)
# Tariff rates published at the bottom of Aguas Andinas bills.
# Format: "Label = $ value" where value may use Chilean format (dot=thousands,
# comma=decimal), e.g. "1.679,38".  The amount sub-pattern captures the full
# number including all separators so that _parse_consumption_to_float can
# convert it correctly.
_WATER_AA_TARIFF_AMT = r"=\s*\$\s*([0-9]+(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)"
_WATER_AA_PDF_FIXED_CHARGE_RE = re.compile(
    r"cargo\s+fijo\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)
_WATER_AA_PDF_PEAK_COST_RE = re.compile(
    r"metro\s+c[úu]bico\s+agua\s+potable\s+punta\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)
_WATER_AA_PDF_NON_PEAK_COST_RE = re.compile(
    r"metro\s+c[úu]bico\s+agua\s+potable\s+no\s+punta\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)
_WATER_AA_PDF_OVERCONSUMPTION_RE = re.compile(
    r"metro\s+c[úu]bico\s+sobreconsumo\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)
_WATER_AA_PDF_COLLECTION_RE = re.compile(
    r"metro\s+c[úu]bico\s+recolecci[oó]n\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)
_WATER_AA_PDF_TREATMENT_RE = re.compile(
    r"metro\s+c[úu]bico\s+tratamiento\s*" + _WATER_AA_TARIFF_AMT,
    re.IGNORECASE,
)


def _extract_water_pdf_attributes(text: str) -> dict[str, Any]:
    """Extract water-service attributes from an **Aguas Andinas PDF** bill.

    This extractor is dedicated to Aguas Andinas PDF bills and handles
    patterns that appear exclusively in the PDF, not in the notification email.

    Key observations (reference PDF: February 2026):
    - pdfminer reads two-column table sections column-by-column: all labels
      appear first, then all values.  Label-based lookups with short windows
      therefore fail; PDF-specific patterns are required.
    - Address spans two lines after ``SEÑOR RESIDENTE`` in the header.
    - Account number follows ``Nro de cuenta`` (with a blank line) at the
      bottom of the bill.
    - Due date follows ``VENCIMIENTO`` (with a blank line) in the header.
    - Consumption block: labels (LECTURA ACTUAL … CONSUMO TOTAL) appear first,
      then the corresponding m³ values.  CONSUMO TOTAL is the last m³ value
      before ``MODALIDAD DE PRORRATEO``.

    Extracted fields (all optional):
        ``address``                       – service address (two-line header block)
        ``customer_number``               – account number from ``Nro de cuenta`` label
        ``due_date``                      – payment due date from ``VENCIMIENTO`` label
        ``consumption``                   – total water consumed (float, m³)
        ``consumption_unit``              – unit of consumption (``"m3"``)
        ``total_amount``                  – total amount due as integer
        ``fixed_charge``                  – fixed service charge as integer
        ``cubic_meter_peak_water_cost``   – cost per m³ (peak) as float
        ``cubic_meter_non_peak_water_cost``– cost per m³ (non-peak) as float
        ``cubic_meter_overconsumption``   – cost per m³ (overconsumption) as float
        ``cubic_meter_collection``        – cost per m³ (collection) as float
        ``cubic_meter_treatment``         – cost per m³ (treatment) as float
    """
    attrs: dict[str, Any] = {}

    # Address — two lines after "SEÑOR RESIDENTE"
    addr_match = _WATER_AA_PDF_ADDRESS_RE.search(text)
    if addr_match:
        line1 = re.sub(r"\s+", " ", addr_match.group(1)).strip()
        line2 = re.sub(r"\s+", " ", addr_match.group(2)).strip()
        if line1 and line2:
            attrs["address"] = f"{line1} {line2}"
        elif line1:
            attrs["address"] = line1

    # Account / customer number — "Nro de cuenta" label at bottom of bill
    acct_match = _WATER_AA_PDF_ACCOUNT_RE.search(text)
    if acct_match:
        attrs["customer_number"] = acct_match.group(1).strip()

    # Due date — "VENCIMIENTO" label in header
    due_match = _WATER_AA_PDF_DUE_DATE_RE.search(text)
    if due_match:
        attrs["due_date"] = due_match.group(1).strip()

    # Consumption — last m³ value in the CONSUMO TOTAL block.
    # pdfminer outputs all row labels first, then the matching values;
    # the CONSUMO TOTAL value is the last m³ entry before "MODALIDAD".
    consumo_match = _WATER_AA_PDF_CONSUMO_LABEL_RE.search(text)
    if consumo_match:
        remaining = text[consumo_match.end():]
        next_section = re.search(r"modalidad", remaining, re.IGNORECASE)
        block = remaining[: next_section.start()] if next_section else remaining[:200]
        m3_matches = list(_WATER_CONSUMPTION_RE.finditer(block))
        if m3_matches:
            last_m3 = m3_matches[-1]
            attrs["consumption"] = _parse_consumption_to_float(last_m3.group(1))
            attrs["consumption_unit"] = "m3"

    # Total amount — generic extractor handles "TOTAL A PAGAR\n\n$ 15.700"
    total = _extract_total_amount(text)
    if total:
        attrs["total_amount"] = total

    # Tariff rates — published block "Cargo fijo = $ NNN" / "Metro cúbico … = $ N,NN"
    fixed_match = _WATER_AA_PDF_FIXED_CHARGE_RE.search(text)
    if fixed_match:
        attrs["fixed_charge"] = _parse_amount_to_int(fixed_match.group(1))

    peak_match = _WATER_AA_PDF_PEAK_COST_RE.search(text)
    if peak_match:
        attrs["cubic_meter_peak_water_cost"] = _parse_consumption_to_float(peak_match.group(1))

    non_peak_match = _WATER_AA_PDF_NON_PEAK_COST_RE.search(text)
    if non_peak_match:
        attrs["cubic_meter_non_peak_water_cost"] = _parse_consumption_to_float(non_peak_match.group(1))

    overconsumption_match = _WATER_AA_PDF_OVERCONSUMPTION_RE.search(text)
    if overconsumption_match:
        attrs["cubic_meter_overconsumption"] = _parse_consumption_to_float(overconsumption_match.group(1))

    collection_match = _WATER_AA_PDF_COLLECTION_RE.search(text)
    if collection_match:
        attrs["cubic_meter_collection"] = _parse_consumption_to_float(collection_match.group(1))

    treatment_match = _WATER_AA_PDF_TREATMENT_RE.search(text)
    if treatment_match:
        attrs["cubic_meter_treatment"] = _parse_consumption_to_float(treatment_match.group(1))

    return attrs


# ---------------------------------------------------------------------------
# Gas-service extractor (tuned for Metrogas; reference email: Jan 2026)
# ---------------------------------------------------------------------------
# Consumption value and unit — used by both email and PDF extractors.
# Group 1 captures the numeric value; group 2 captures the unit (m3s / m3 / m³).
_GAS_CONSUMPTION_RE = re.compile(
    r"([0-9]+(?:[.,][0-9]+)?)\s*(m[³3]s?)",
    re.IGNORECASE,
)
# Email-body label patterns for gas consumption (generic, non-PDF issuers).
# NOTE: Metrogas does NOT include consumption in the email body; it is only
# present in the PDF attachment.  The label-based pattern is kept here for
# gas companies that do include it in their email.
_GAS_CONSUMPTION_LABELS = re.compile(
    r"(?:consumo\s+(?:de\s+)?gas|consumo|volumen\s+consumido)[:\s]+",
    re.IGNORECASE,
)
# Metropuntos loyalty-points label (Metrogas-specific) — kept for future use
# _GAS_METROPUNTOS_LABELS omitted; metropuntos is a service-specific attribute
# Plain number pattern for "Total a pagar" — Metrogas omits the $ sign,
# e.g. "Total a pagar: 12.013" (Chilean thousands separator is '.').
_GAS_PLAIN_AMOUNT_RE = re.compile(
    r"([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)",
)


def _extract_gas_attributes(text: str) -> dict[str, Any]:
    """Extract gas-service attributes from an **email body**.

    Tuned for Metrogas (reference email: January 2026).  Key observations:
    - The HTML-only email carries folio, customer number, address, billing
      period, total due, due date.
    - Gas consumption is **not** included in the Metrogas email — only in the
      PDF attachment.  The label-based consumption search is kept here for
      other gas issuers that do include it in the email body.
    - ``Total a pagar`` in the Metrogas email has no ``$`` prefix
      (e.g. ``12.013``).
    - Customer label is ``Número Cliente:`` (no ``de`` — handled in the
      shared ``_CUSTOMER_LABELS`` pattern).

    Extracted fields (all optional):
        ``total_amount``    – total amount due as integer (overrides generic)
        ``consumption``     – gas consumed (label-based, for issuers that
                               include it in the email body)
        ``consumption_unit``– unit of consumption (e.g. ``"m3"``)
        ``cost_per_m3s``    – cost per m3s; only set when both
                               ``total_amount`` and ``consumption`` are found
                               and consumption > 0
    """
    attrs: dict[str, Any] = {}

    # Total a pagar — Metrogas uses a plain number without a $ prefix in the
    # email body (e.g. "Total a pagar: 12.013").
    for label_match in _TOTAL_LABELS.finditer(text):
        rest = text[label_match.end():]
        val_match = _GAS_PLAIN_AMOUNT_RE.search(rest[:60])
        if val_match:
            raw = val_match.group(1).strip()
            if raw:
                attrs["total_amount"] = _parse_amount_to_int(raw)
                break

    # Gas consumption — label-based only; bare m³ fallback omitted to
    # avoid false positives in HTML that contains no consumption data.
    for label_match in _GAS_CONSUMPTION_LABELS.finditer(text):
        rest = text[label_match.end():]
        val_match = _GAS_CONSUMPTION_RE.search(rest[:80])
        if val_match:
            attrs["consumption"] = _parse_consumption_to_float(val_match.group(1).strip())
            attrs["consumption_unit"] = val_match.group(2).lower()
            break

    # cost_per_m3s — derived when both values are present in the email.
    total = attrs.get("total_amount")
    consumption = attrs.get("consumption")
    if total and consumption and consumption > 0:
        attrs["cost_per_m3s"] = round(total / consumption, 2)

    return attrs


# ---------------------------------------------------------------------------
# Gas-service PDF extractor (Metrogas-specific; reference PDF: Jan 2026)
# ---------------------------------------------------------------------------
# PDF-specific label for gas consumption in Metrogas bills:
# "Gas consumido ( 5,95 m3s )".  The separator allows ":", whitespace, and
# "(" to match the parenthesised format used in the PDF.
_GAS_PDF_CONSUMPTION_LABELS = re.compile(
    r"gas\s+consumido[:\s\(]+",
    re.IGNORECASE,
)


def _extract_gas_pdf_attributes(text: str) -> dict[str, Any]:
    """Extract gas-service attributes from a **Metrogas PDF**.

    This extractor is dedicated to Metrogas PDF bills and handles patterns
    that appear exclusively in the PDF, not in the notification email.

    Key observations (reference PDF: January 2026):
    - Gas consumption is labelled ``Gas consumido ( 5,95 m3s )`` in the PDF.
    - The unit is ``m3s`` (standardised cubic metres), not plain ``m3``.
    - The total amount appears as ``$ 12.013`` near ``Total a pagar``.

    Extracted fields (all optional):
        ``total_amount``    – total amount due as integer
        ``consumption``     – gas consumed in m3s (float)
        ``consumption_unit``– unit of consumption (``"m3s"``)
        ``cost_per_m3s``    – cost per m3s (total_amount / consumption, float)
    """
    attrs: dict[str, Any] = {}

    # Total amount — PDF uses "$" prefix (e.g. "$ 12.013"), handled by the
    # generic _extract_total_amount which looks for _AMOUNT_RE after the label.
    total = _extract_total_amount(text)
    if total:
        attrs["total_amount"] = total

    # Gas consumption — Metrogas PDF label: "Gas consumido ( 5,95 m3s )"
    for label_match in _GAS_PDF_CONSUMPTION_LABELS.finditer(text):
        rest = text[label_match.end():]
        val_match = _GAS_CONSUMPTION_RE.search(rest[:80])
        if val_match:
            attrs["consumption"] = _parse_consumption_to_float(val_match.group(1).strip())
            attrs["consumption_unit"] = val_match.group(2).lower()
            break

    # cost_per_m3s — cost per standardised cubic metre.
    # Calculated only when both values are available and consumption > 0.
    total_val = attrs.get("total_amount")
    consumption_val = attrs.get("consumption")
    if total_val and consumption_val and consumption_val > 0:
        attrs["cost_per_m3s"] = round(total_val / consumption_val, 2)

    return attrs


# ---------------------------------------------------------------------------
# Electricity-service extractor (tuned for Enel Distribución Chile; Feb 2026)
# ---------------------------------------------------------------------------
_ELEC_CONSUMPTION_RE = re.compile(
    r"([0-9]+(?:[.,][0-9]+)?)\s*kWh",
    re.IGNORECASE,
)
_ELEC_CONSUMPTION_LABELS = re.compile(
    r"(?:consumo|energ[íi]a\s+consumida)[:\s]+",
    re.IGNORECASE,
)
# Enel: address follows "suministro ubicado en … ya está disponible"
_ELEC_ENEL_ADDRESS_RE = re.compile(
    r"ubicado\s+en\s+([\s\S]{10,120}?)\s+ya\s+est[aá]",
    re.IGNORECASE,
)
# Enel: invoice number in body as "N° Boleta NNNNNN del …"
_ELEC_ENEL_FOLIO_RE = re.compile(
    r"n[°º]\s*boleta\s+(\d{5,})",
    re.IGNORECASE,
)
# Enel: next billing period as "Próximo periodo de facturación\n DATE - DATE"
_ELEC_ENEL_NEXT_PERIOD_RE = re.compile(
    r"pr[oó]ximo\s+periodo\s+de\s+facturaci[oó]n\s+"
    r"(\d{1,2}-\d{1,2}-\d{4})\s*-\s*(\d{1,2}-\d{1,2}-\d{4})",
    re.IGNORECASE,
)


def _extract_electricity_attributes(text: str) -> dict[str, Any]:
    """Extract electricity-service-specific attributes.

    Tuned for Enel Distribución Chile (reference email: February 2026).
    Key observations:
    - ``multipart/alternative`` with both text/plain and text/html parts;
      the extractor runs on plain text (preferred by the sensor).
    - Customer number in subject (``NNN-D``) and in body (``N° Cliente``);
      the generic ``_CUSTOMER_LABELS`` extractor handles the body match.
    - Invoice number in body: ``N° Boleta NNNNNN del DD-MM-YYYY``
      (not in subject, unlike Metrogas).
    - Address follows ``ubicado en`` (not a ``Dirección:`` label).
    - Total ``$ NNN.NNN`` is preceded by ``¿Cuánto debo pagar?`` which does
      not match generic total labels; the ``_AMOUNT_RE`` fallback finds it.
    - Consumption ``NNN kWh`` followed by ``Consumo real`` or ``estimado``.
    - ``Próximo periodo de facturación DATE - DATE`` is the **next** period;
      the email does **not** show the current billing period.
    - The generic ``_extract_dates`` assigns the boleta issue date and due
      date to ``billing_period_start/end`` — both wrong.  Those fields are
      explicitly cleared here (set to ``None``) so they are omitted from
      the HA state attributes rather than showing misleading values.

    Extracted fields (all optional):
        ``folio``            – invoice (boleta) number from body
        ``address``          – service address (from ``ubicado en``)
        ``consumption``      – energy consumed
        ``consumption_unit`` – unit of consumption (``"kWh"``)
        ``billing_period_start`` – ``None`` (not in email; clears wrong generic value)
        ``billing_period_end``   – ``None`` (not in email; clears wrong generic value)
    """
    attrs: dict[str, Any] = {}

    # Energy consumption — label-preceded first, then bare kWh fallback
    for label_match in _ELEC_CONSUMPTION_LABELS.finditer(text):
        rest = text[label_match.end():]
        val_match = _ELEC_CONSUMPTION_RE.search(rest[:80])
        if val_match:
            attrs["consumption"] = _parse_consumption_to_float(val_match.group(1).strip())
            attrs["consumption_unit"] = "kWh"
            break
    if "consumption" not in attrs:
        val_match = _ELEC_CONSUMPTION_RE.search(text)
        if val_match:
            attrs["consumption"] = _parse_consumption_to_float(val_match.group(1).strip())
            attrs["consumption_unit"] = "kWh"

    # Enel: invoice number from body "N° Boleta NNNNNN del …"
    folio_match = _ELEC_ENEL_FOLIO_RE.search(text)
    if folio_match:
        attrs["folio"] = folio_match.group(1).strip()

    # Enel: service address from "ubicado en ADDRESS ya está disponible"
    addr_match = _ELEC_ENEL_ADDRESS_RE.search(text)
    if addr_match:
        addr = re.sub(r"\s+", " ", addr_match.group(1)).strip()
        if addr:
            attrs["address"] = addr

    # Enel: next billing period — used only to detect "no current period in email"
    next_period_match = _ELEC_ENEL_NEXT_PERIOD_RE.search(text)
    if next_period_match:
        # The email does not include the current billing period.  Clear the
        # wrong dates the generic extractor would have inserted.
        attrs["billing_period_start"] = None
        attrs["billing_period_end"] = None

    return attrs


# ---------------------------------------------------------------------------
# Electricity-service PDF extractor (Enel Distribución Chile; Feb 2026)
# ---------------------------------------------------------------------------
# Spanish abbreviated month names used in Enel PDF date ranges.
_SPANISH_MONTH_MAP: dict[str, int] = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}
# Billing period date range with Spanish month names (primary source):
# "30 Dic 2025 - 29 Ene 2026" — appears after "Monto del periodo" in the PDF.
_ELEC_PDF_BILLING_PERIOD_RE = re.compile(
    r"(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\.?\s+(\d{4})"
    r"\s*[-–]\s*"
    r"(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\.?\s+(\d{4})",
    re.IGNORECASE,
)
# Fallback billing period from "Período de lectura: 30/12/2025 - 29/01/2026"
# (DD/MM/YYYY format, always present in the Enel PDF).
_ELEC_PDF_PERIOD_LECTURA_RE = re.compile(
    r"per[íi]odo\s+de\s+lectura[:\s]+(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
# Enel PDF billing breakdown — pdfminer reads the three-column table as
# separate blocks: labels first, then a column of "$" signs, then amounts.
# Exact structure extracted from Enel.pdf (Feb 2026):
#
#   Administración del servicio
#   Electricidad Consumida (505kWh)
#   Transporte de electricidad
#   Cargo Fondo de Estabilización Ley 21.472
#
#   $              ← all $ signs stacked
#   $
#   $
#   $
#
#   708            ← amounts in the same row order
#   111.824
#   8.479
#   1.049
#
# Groups: (1) consumption value, (2) kWh unit,
#         (3) service_administration, (4) electricity_consumption,
#         (5) electricity_transport, (6) stabilization_fund
_ELEC_PDF_TABLE_RE = re.compile(
    r"administraci[oó]n\s+del\s+servicio\s*\n"
    r"electricidad\s+consumida\s*\(([0-9]+(?:[.,][0-9]+)?)\s*(kWh)\)\s*\n"
    r"transporte\s+de\s+electricidad\s*\n"
    r"cargo\s+fondo\s+de\s+estabilizaci[oó]n[^\n]*\n"
    r"\s*"                              # blank line between labels and $ column
    r"\$\s*\n\$\s*\n\$\s*\n\$\s*\n"    # four "$" signs, one per line
    r"\s*"                              # blank line between $ column and amounts
    r"([0-9]{1,3}(?:[.,][0-9]{3})*)\s*\n"   # service_administration
    r"([0-9]{1,3}(?:[.,][0-9]{3})*)\s*\n"   # electricity_consumption
    r"([0-9]{1,3}(?:[.,][0-9]{3})*)\s*\n"   # electricity_transport
    r"([0-9]{1,3}(?:[.,][0-9]{3})*)",        # stabilization_fund
    re.IGNORECASE,
)
# "Tipo de tarifa contratada: BT1-T2"
_ELEC_PDF_TARIFF_CODE_RE = re.compile(
    r"tipo\s+de\s+tarifa\s+contratada[:\s]+([^\n\r]{1,40})",
    re.IGNORECASE,
)
# "Potencia conectada: 2,500 kW"
# Group 1: numeric value (Chilean format), Group 2: unit (kW / kVA / MW)
_ELEC_PDF_CONNECTED_POWER_RE = re.compile(
    r"potencia\s+conectada[:\s]+([0-9]+(?:[.,][0-9]+)?)\s*(kVA|kW|MW)",
    re.IGNORECASE,
)
# "Área Típica: AREA 1 S Caso 3 (a)"
_ELEC_PDF_AREA_RE = re.compile(
    r"[áa]rea\s+t[íi]pica[:\s]+([^\n\r]{1,80})",
    re.IGNORECASE,
)
# "Subestación: SAN CRISTOBAL"
_ELEC_PDF_SUBSTATION_RE = re.compile(
    r"subestaci[oó]n[:\s]+([^\n\r]{1,80})",
    re.IGNORECASE,
)


def _parse_spanish_date(day: str, month_str: str, year: str) -> str:
    """Convert Spanish date parts to a ``DD-MM-YYYY`` string.

    Returns an empty string if the month abbreviation is not recognised.
    """
    month_num = _SPANISH_MONTH_MAP.get(month_str[:3].lower(), 0)
    if not month_num:
        return ""
    return f"{int(day):02d}-{month_num:02d}-{year}"


def _extract_electricity_pdf_attributes(text: str) -> dict[str, Any]:
    """Extract electricity-service attributes from an **Enel PDF** bill.

    This extractor is dedicated to Enel Distribución Chile PDF bills and
    handles patterns that appear exclusively in the PDF, not in the
    notification email.

    Key observations (reference PDF: February 2026):
    - Billing period dates appear as ``30 Dic 2025 - 29 Ene 2026`` after
      ``Monto del periodo`` and as ``30/12/2025 - 29/01/2026`` after
      ``Período de lectura:``.
    - The billing breakdown is a three-column table; pdfminer reads the
      columns separately: label names, then ``$`` signs, then amounts
      (each column as a block of newline-separated values).
    - Amounts use Chilean thousands format: ``111.824`` → 111 824.

    Extracted fields (all optional):
        ``billing_period_start``   – start of the billing period (DD-MM-YYYY)
        ``billing_period_end``     – end of the billing period (DD-MM-YYYY)
        ``consumption``            – energy consumed (float, kWh, from PDF)
        ``consumption_unit``       – ``"kWh"``
        ``service_administration`` – administration fee (int)
        ``electricity_consumption``– cost of consumed electricity (int)
        ``electricity_transport``  – electricity transport charge (int)
        ``stabilization_fund``     – stabilisation fund charge (int)
        ``cost_per_kwh``           – cost per kWh (electricity_consumption /
                                     consumption, float); only set when both
                                     values are available and consumption > 0
        ``tariff_code``            – contracted tariff type (e.g. ``"BT1-T2"``)
        ``connected_power``        – contracted connected power (int, kW)
        ``connected_power_unit``   – unit of connected power (e.g. ``"kW"``)
        ``area``                   – typical area (e.g. ``"AREA 1 S Caso 3 (a)"``)
        ``substation``             – supplying substation name
    """
    attrs: dict[str, Any] = {}

    # Billing period — primary: Spanish month format "30 Dic 2025 - 29 Ene 2026"
    period_match = _ELEC_PDF_BILLING_PERIOD_RE.search(text)
    if period_match:
        start = _parse_spanish_date(
            period_match.group(1), period_match.group(2), period_match.group(3)
        )
        end = _parse_spanish_date(
            period_match.group(4), period_match.group(5), period_match.group(6)
        )
        if start:
            attrs["billing_period_start"] = start
        if end:
            attrs["billing_period_end"] = end

    # Billing period — fallback: DD/MM/YYYY format from "Período de lectura"
    if "billing_period_start" not in attrs:
        lectura_match = _ELEC_PDF_PERIOD_LECTURA_RE.search(text)
        if lectura_match:
            attrs["billing_period_start"] = lectura_match.group(1).replace("/", "-")
            attrs["billing_period_end"] = lectura_match.group(2).replace("/", "-")

    # Billing breakdown table — matches the column-separated layout extracted
    # by pdfminer: labels / $ signs / amounts (each column as a separate block).
    table_match = _ELEC_PDF_TABLE_RE.search(text)
    if table_match:
        attrs["consumption"] = _parse_consumption_to_float(table_match.group(1).strip())
        attrs["consumption_unit"] = table_match.group(2)        # "kWh"
        attrs["service_administration"] = _parse_amount_to_int(table_match.group(3).strip())
        attrs["electricity_consumption"] = _parse_amount_to_int(table_match.group(4).strip())
        attrs["electricity_transport"] = _parse_amount_to_int(table_match.group(5).strip())
        attrs["stabilization_fund"] = _parse_amount_to_int(table_match.group(6).strip())

    # cost_per_kwh — cost per kWh consumed.
    # Calculated only when both values are available and consumption > 0.
    elec_cost = attrs.get("electricity_consumption")
    consumption_val = attrs.get("consumption")
    if elec_cost and consumption_val and consumption_val > 0:
        attrs["cost_per_kwh"] = round(elec_cost / consumption_val, 2)

    # "Tipo de tarifa contratada: BT1-T2"
    tariff_match = _ELEC_PDF_TARIFF_CODE_RE.search(text)
    if tariff_match:
        attrs["tariff_code"] = tariff_match.group(1).strip()

    # "Potencia conectada: 2,500 kW"
    power_match = _ELEC_PDF_CONNECTED_POWER_RE.search(text)
    if power_match:
        attrs["connected_power"] = _parse_amount_to_int(power_match.group(1).strip())
        attrs["connected_power_unit"] = power_match.group(2)

    # "Área Típica: AREA 1 S Caso 3 (a)"
    area_match = _ELEC_PDF_AREA_RE.search(text)
    if area_match:
        attrs["area"] = area_match.group(1).strip()

    # "Subestación: SAN CRISTOBAL"
    substation_match = _ELEC_PDF_SUBSTATION_RE.search(text)
    if substation_match:
        attrs["substation"] = substation_match.group(1).strip()

    return attrs


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _extract_type_specific_attributes(text: str, service_type: str) -> dict[str, Any]:
    """Dispatch to the **email** extractor for *service_type* and return its results."""
    if service_type == SERVICE_TYPE_WATER:
        return _extract_water_attributes(text)
    if service_type == SERVICE_TYPE_GAS:
        return _extract_gas_attributes(text)
    if service_type == SERVICE_TYPE_ELECTRICITY:
        return _extract_electricity_attributes(text)
    return {}


def _extract_pdf_type_specific_attributes(text: str, service_type: str) -> dict[str, Any]:
    """Dispatch to the **PDF** extractor for *service_type* and return its results.

    Each service type has a dedicated PDF extractor whose patterns are tuned to
    the layout of that issuer's PDF bill — separate from the email extractor.
    Service types that do not yet have a PDF-specific extractor fall back to an
    empty dict (no attributes extracted from the PDF).
    """
    if service_type == SERVICE_TYPE_WATER:
        return _extract_water_pdf_attributes(text)
    if service_type == SERVICE_TYPE_GAS:
        return _extract_gas_pdf_attributes(text)
    if service_type == SERVICE_TYPE_ELECTRICITY:
        return _extract_electricity_pdf_attributes(text)
    return {}


def extract_attributes_from_email_body(
    subject: str, body: str, service_type: str = SERVICE_TYPE_UNKNOWN
) -> dict[str, Any]:
    """Extract billing attributes from email subject and body.

    Extracts the standard fields before PDF analysis:
    folio, billing_period_start, billing_period_end,
    total_amount (integer), customer_number, address, due_date,
    consumption, consumption_unit.

    When *service_type* is provided, additional type-specific overrides
    are applied (e.g. ``consumption``/``consumption_unit`` for water/gas/
    electricity services).

    Args:
        subject:      Email subject line (decoded).
        body:         Plain-text email body.
        service_type: One of the SERVICE_TYPE_* constants (default: ``unknown``).

    Returns:
        Dictionary with extracted attributes.
    """
    attributes: dict[str, Any] = {}

    try:
        # Folio — from subject line (confirmed later against PDF)
        subject_attrs = _extract_from_subject(subject)
        attributes.update(subject_attrs)

        # Combine subject + body for field search; cap for performance
        combined = f"{subject}\n\n{body}"
        if len(combined) > 15000:
            combined = combined[:15000]

        # Billing period dates
        dates = _extract_dates(combined)
        attributes.update(dates)

        # Total amount
        total = _extract_total_amount(combined)
        if total:
            attributes["total_amount"] = total

        # Customer / account number
        customer = _extract_customer_number(combined)
        if customer:
            attributes["customer_number"] = customer

        # Service address
        address = _extract_address(combined)
        if address:
            attributes["address"] = address

        # Payment due date
        due_date = _extract_due_date(combined)
        if due_date:
            attributes["due_date"] = due_date

        # Service-type-specific attributes (consumption, meter readings, etc.)
        type_attrs = _extract_type_specific_attributes(combined, service_type)
        attributes.update(type_attrs)

    except Exception as err:
        _LOGGER.debug("Error extracting attributes: %s", err)

    return attributes



def _extract_from_subject(subject: str) -> dict[str, str]:
    """
    Extract specific attributes from email subject line.

    Subject lines often contain key identifiers in a compact format.
    """
    attrs: dict[str, str] = {}

    # Extract folio/invoice numbers from subject
    folio_patterns = [
        r"folio[:\s]*([0-9]{6,})",
        r"nro\.?\s+([0-9]{6,})",           # "Nro. 0000000061778648" (Metrogas)
        r"n[úu]mero[:\s]*([0-9]{6,})",
        r"boleta[:\s]*([0-9]{6,})",
        r"factura[:\s]*([0-9]{6,})",
    ]

    for pattern in folio_patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            attrs["folio"] = match.group(1)
            break

    return attrs


def extract_attributes_from_email(msg: Any) -> dict[str, Any]:
    """
    Extract attributes directly from an email message object.
    
    Args:
        msg: email.message.Message object
    
    Returns:
        Dictionary with extracted attributes
    """
    from email.header import decode_header
    
    # Decode subject
    subject_header = msg.get("Subject", "")
    decoded_fragments = decode_header(subject_header)
    subject_parts = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            subject_parts.append(fragment.decode(encoding or "utf-8", errors="ignore"))
        else:
            subject_parts.append(fragment)
    subject = "".join(subject_parts)
    
    # Extract body
    body = ""
    try:
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []

            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                        if content_type == "text/plain":
                            plain_parts.append(text)
                        elif content_type == "text/html":
                            html_parts.append(text)
                except Exception:
                    pass

            if plain_parts:
                body = " ".join(plain_parts)
            elif html_parts:
                body = _strip_html(" ".join(html_parts))
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    raw = payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                    body = _strip_html(raw) if msg.get_content_type() == "text/html" else raw
            except Exception:
                pass
    except Exception as err:
        _LOGGER.debug("Error extracting body for attribute extraction: %s", err)
    
    return extract_attributes_from_email_body(subject, body)


def extract_attributes_from_pdf(pdf_path: str, service_type: str = SERVICE_TYPE_UNKNOWN) -> dict[str, Any]:
    """Extract billing attributes from a downloaded PDF file.

    Uses ``pdfminer.six`` to convert the PDF to plain text and then dispatches
    to the **PDF-specific** extractor for *service_type* via
    :func:`_extract_pdf_type_specific_attributes`.  Each service type has its
    own dedicated PDF extractor whose patterns are tuned to that issuer's PDF
    layout — separate from the email extractor.

    Only attributes that can be reliably sourced from the PDF are returned;
    the caller is responsible for merging these with email-derived attributes
    (PDF values take precedence).

    Currently implemented PDF extractors:
    - **water** — :func:`_extract_water_pdf_attributes` (Aguas Andinas, Feb 2026):
      ``address``, ``customer_number``, ``due_date``, ``consumption``,
      ``consumption_unit``, ``total_amount``, ``fixed_charge``,
      ``cubic_meter_peak_water_cost``, ``cubic_meter_non_peak_water_cost``,
      ``cubic_meter_overconsumption``, ``cubic_meter_collection``,
      ``cubic_meter_treatment``
    - **gas** — :func:`_extract_gas_pdf_attributes` (Metrogas, Jan 2026):
      ``consumption``, ``consumption_unit``, ``cost_per_m3s``, ``total_amount``
    - **electricity** — :func:`_extract_electricity_pdf_attributes` (Enel, Feb 2026):
      ``billing_period_start``, ``billing_period_end``, ``consumption``,
      ``consumption_unit``, ``electricity_consumption``, ``service_administration``,
      ``electricity_transport``, ``stabilization_fund``, ``cost_per_kwh``

    Args:
        pdf_path:     Absolute path to the downloaded PDF file.
        service_type: One of the ``SERVICE_TYPE_*`` constants.

    Returns:
        Dictionary with attributes extracted from the PDF, or an empty dict
        if the PDF could not be read, the service type has no PDF extractor,
        or no attributes were found.
    """
    try:
        from pdfminer.high_level import extract_text as _pdf_extract_text  # type: ignore[import-untyped]
    except ImportError:
        _LOGGER.warning(
            "pdfminer.six is not installed; PDF attribute extraction is unavailable. "
            "Install it via 'pip install pdfminer.six' or add it to manifest requirements."
        )
        return {}

    try:
        pdf_text = _pdf_extract_text(pdf_path)
    except Exception as err:
        _LOGGER.debug("Could not extract text from PDF '%s': %s", pdf_path, err)
        return {}

    if not pdf_text:
        return {}

    attrs: dict[str, Any] = {}
    try:
        # Cap text length for performance (PDFs can be large)
        if len(pdf_text) > 50000:
            pdf_text = pdf_text[:50000]

        # Apply the PDF-specific extractor for this service type.
        # Each service type has its own PDF extractor tuned to that issuer's
        # PDF layout (separate from the email extractor).
        pdf_attrs = _extract_pdf_type_specific_attributes(pdf_text, service_type)
        attrs.update(pdf_attrs)
    except Exception as err:
        _LOGGER.debug("Error extracting attributes from PDF '%s': %s", pdf_path, err)

    return attrs
