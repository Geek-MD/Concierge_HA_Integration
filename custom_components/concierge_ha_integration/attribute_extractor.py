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

import calendar
import difflib
import logging
import re
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Any

from .const import (
    SERVICE_TYPE_COMMON_EXPENSES,
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_HOT_WATER,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction confidence scores (0–100)
# ---------------------------------------------------------------------------
# Each PDF-sourced attribute carries a confidence score that reflects how
# reliable the extraction method is.  Callers can surface these scores in
# sensor ``extra_state_attributes`` so users see at a glance how trustworthy
# each value is.
#
# Score meanings:
#   PDFMINER  (70) – extracted from the PDF text layer; pdfminer can
#                    misread font-encoded glyphs (e.g. "6" → "8").
#   OCR       (85) – extracted via Tesseract OCR on the PDF image; more
#                    accurate for image-backed PDFs but still fallible.
#   DERIVED   (60) – calculated from other extracted values (e.g.
#                    subtotal_consumo = total − subtotal_depto − cargo_fijo).
#   OVERRIDE (100) – user-supplied correction stored by the ``set_value``
#                    service; treated as ground truth.
CONF_SCORE_PDFMINER: float = 70.0
CONF_SCORE_OCR: float = 85.0
CONF_SCORE_DERIVED: float = 60.0
CONF_SCORE_OVERRIDE: float = 100.0

# ---------------------------------------------------------------------------
# Tesseract-OCR availability state
# ---------------------------------------------------------------------------
# Tracks whether the ``tesseract-ocr`` system binary is available.
#   None  – not yet determined (no OCR attempt has been made in this process)
#   True  – a successful OCR run confirmed the binary is present
#   False – ``TesseractNotFoundError`` was raised; binary is absent
#
# Updated by ``_try_ocr_pdf`` and read by the HA sensor coordinator to manage
# a persistent Repair issue that guides users through installing Tesseract.
_tesseract_available: bool | None = None


def is_tesseract_available() -> bool | None:
    """Return the current Tesseract-OCR availability state.

    ``None``  – no OCR attempt has been made yet.
    ``True``  – Tesseract binary found and working.
    ``False`` – ``TesseractNotFoundError`` raised; binary missing.
    """
    return _tesseract_available


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
# Billing breakdown table — pdfminer serialises the table column-by-column:
# all row labels first, then the three per-row consumption sub-values (10,98
# repeated for each water service), then the eight CLP amounts in row order.
# The intermediate "TOTAL A PAGAR" label sits between the last row label and
# the sub-values but is NOT a separate billing row.
#
# Row / group mapping for the captured amounts (Chilean format, dot = thousands):
#   group 1 – CARGO FIJO               (e.g. "914")
#   group 2 – CONSUMO AGUA POTABLE     (e.g. "6.426")
#   group 3 – RECOLECCION AGUAS SERV.  (e.g. "4.902")
#   group 4 – TRATAMIENTO AGUAS SERV.  (e.g. "3.360")
#   group 5 – SUBTOTAL SERVICIO        (e.g. "15.602")
#   group 6 – INTERÉS DEUDA            (e.g. "99")
#   group 7 – TOTAL VENTA              (e.g. "15.701")  [not stored separately]
#   group 8 – DESCUENTO LEY REDONDEO   (e.g. "-1")
_WATER_AA_PDF_BILLING_TABLE_RE = re.compile(
    r"CARGO\s+FIJO\n"
    r"CONSUMO\s+AGUA\s+POTABLE\n"
    r"RECOLECCION\s+AGUAS\s+SERVIDAS\n"
    r"TRATAMIENTO\s+AGUAS\s+SERVIDAS\n"
    r"SUBTOTAL\s+SERVICIO\n"
    r"INTER[EÉ]S\s+DEUDA\n"
    r"TOTAL\s+VENTA\n"
    r"DESCUENTO\s+LEY\s+REDONDEO\n"
    r"\s*TOTAL\s+A\s+PAGAR\s*\n"           # intermediate row (not stored)
    r"\s*(?:[0-9][0-9.,]*\s*\n)+"          # per-row consumption sub-values
    r"\s*([0-9][0-9.,]+)\s*\n"             # group 1 – CARGO FIJO
    r"([0-9][0-9.,]+)\s*\n"               # group 2 – CONSUMO AGUA POTABLE
    r"([0-9][0-9.,]+)\s*\n"               # group 3 – RECOLECCION
    r"([0-9][0-9.,]+)\s*\n"               # group 4 – TRATAMIENTO
    r"([0-9][0-9.,]+)\s*\n"               # group 5 – SUBTOTAL
    r"([0-9][0-9.,]+)\s*\n"               # group 6 – INTERÉS DEUDA
    r"([0-9][0-9.,]+)\s*\n"               # group 7 – TOTAL VENTA
    r"(-?[0-9][0-9.,]*)",                  # group 8 – DESCUENTO LEY REDONDEO
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
        ``water_consumption``             – potable water charge as integer (CLP)
        ``wastewater_recolection``        – wastewater collection charge as integer (CLP)
        ``wastewater_treatment``          – wastewater treatment charge as integer (CLP)
        ``subtotal``                      – subtotal before surcharges as integer (CLP)
        ``other_charges``                 – net surcharges (interest − rounding) as integer
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

    # Billing breakdown table — water_consumption, wastewater charges,
    # subtotal, and other_charges (surcharges net of rounding discount).
    # pdfminer serialises the table as labels → sub-values → amounts; the
    # regex anchors on the label block to recover amounts in row order.
    # Tolerance for cross-verification: published tariff rates are rounded
    # to 2 decimal places, so computed products may differ from the billed
    # integer by a few CLP.
    _BILLING_VERIFY_TOLERANCE = 10  # CLP
    table_match = _WATER_AA_PDF_BILLING_TABLE_RE.search(text)
    if table_match:
        billed_fixed = _parse_amount_to_int(table_match.group(1))
        water_cons   = _parse_amount_to_int(table_match.group(2))
        ww_recol     = _parse_amount_to_int(table_match.group(3))
        ww_treat     = _parse_amount_to_int(table_match.group(4))
        subtotal     = _parse_amount_to_int(table_match.group(5))
        interes      = _parse_amount_to_int(table_match.group(6))
        descuento    = _parse_amount_to_int(table_match.group(8))

        attrs["water_consumption"]      = water_cons
        attrs["wastewater_recolection"] = ww_recol
        attrs["wastewater_treatment"]   = ww_treat
        attrs["subtotal"]               = subtotal
        attrs["other_charges"]          = interes + descuento

        # Cross-verify each water charge against consumption × published rate.
        consumption = attrs.get("consumption")
        if consumption:
            for field, extracted, rate_key in (
                ("water_consumption",      water_cons, "cubic_meter_non_peak_water_cost"),
                ("wastewater_recolection", ww_recol,   "cubic_meter_collection"),
                ("wastewater_treatment",   ww_treat,   "cubic_meter_treatment"),
            ):
                rate = attrs.get(rate_key)
                if rate:
                    expected = round(consumption * rate)
                    diff = abs(extracted - expected)
                    if diff > _BILLING_VERIFY_TOLERANCE:
                        _LOGGER.warning(
                            "Water PDF billing check: %s=%d but "
                            "round(consumption × %s) = round(%.2f × %.2f) = %d "
                            "(diff=%d, tolerance=%d)",
                            field, extracted,
                            rate_key, consumption, rate, expected,
                            diff, _BILLING_VERIFY_TOLERANCE,
                        )

        # Cross-verify subtotal = fixed_charge + three service charges.
        expected_subtotal = billed_fixed + water_cons + ww_recol + ww_treat
        diff = abs(subtotal - expected_subtotal)
        if diff > _BILLING_VERIFY_TOLERANCE:
            _LOGGER.warning(
                "Water PDF billing check: subtotal=%d but "
                "fixed_charge(%d) + water_consumption(%d) + "
                "wastewater_recolection(%d) + wastewater_treatment(%d) = %d "
                "(diff=%d, tolerance=%d)",
                subtotal, billed_fixed, water_cons, ww_recol, ww_treat,
                expected_subtotal, diff, _BILLING_VERIFY_TOLERANCE,
            )

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
# Common-expenses service extractor (Gastos Comunes / Nota de Cobro)
# ---------------------------------------------------------------------------
# The Gastos Comunes PDF is a JPEG-backed document: the main billing table is
# embedded as a JPEG image while only a partial text layer (pdfminer-readable)
# overlays certain fields.  Two extraction tiers are used:
#   1. pdfminer text layer  – always available; provides amounts, dates, owner.
#   2. OCR (optional)       – requires pypdfium2 + pytesseract + tesseract-ocr
#                             installed on the host; provides the hot-water
#                             table (Agua Caliente) that lives only in the image.

# "Nota de Cobro Enero 2026" – billing month and year
_GC_BILLING_PERIOD_RE = re.compile(
    r"nota\s+d[eo]\s+cobro\s+([A-Za-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1"
    r"\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]+)\s+(\d{4})",
    re.IGNORECASE,
)

# Emission + due dates are on separate lines after the garbled labels.
# Layout (pdfminer lines):
#   "Fecha Eml11ón: "      ← font-garbled "Fecha Emisión:"
#   "Pagar Huta: "         ← font-garbled "Pagar Hasta:"
#   ""
#   "18-02-2026 "          ← emission date (first date)
#   "01-03-2028 "          ← due date (second date, year may be garbled)
# Capture both dates together to correctly assign emission vs. due.
_GC_DATES_BLOCK_RE = re.compile(
    r"fecha\s+em[^:\n]+[:\s]+"         # "Fecha Emisión:" (garbled label)
    r"pagar\s+[^:\n]+[:\s]+"           # "Pagar Hasta:"  (garbled label)
    r"[\s\S]{0,30}?"
    r"(\d{2}[/-]\d{2}[/-]\d{4})"       # first date = emission date
    r"[\s\S]{0,20}?"
    r"(\d{2}[/-]\d{2}[/-]\d{4})",      # second date = due date
    re.IGNORECASE,
)

# Building RUT: "RUT: 56080400-8"
_GC_RUT_RE = re.compile(
    r"rut[:\s]+(\d{7,9}-[\dkK])",
    re.IGNORECASE,
)

# Building name: "Edificio <Name>"
# Capture everything up to the newline that precedes "RUT:"
_GC_BUILDING_NAME_RE = re.compile(
    r"edificio\s+([\w\s]{2,40}?)\s*\n",
    re.IGNORECASE,
)

# Known-correct building name used as a reference for heuristic correction
# when pdfminer font-garbling produces a slightly wrong name (e.g. "Jon"
# instead of "Jose").  Month-to-month the name is stable; only a
# substantially different extracted name is accepted as a genuine change.
_GC_KNOWN_BUILDING_NAME: str = "Edificio Jose Miguel"

# Minimum SequenceMatcher similarity ratio to treat the pdfminer-extracted
# building name as a garbled version of _GC_KNOWN_BUILDING_NAME and
# replace it with the known-correct value.
_GC_BUILDING_NAME_SIMILARITY_THRESHOLD: float = 0.75

# Address: first non-empty line after the RUT line (e.g. "Curico 380 - Santiago")
_GC_ADDRESS_RE = re.compile(
    r"rut:\s*\d[\d.-]+-[\dkK]\s*\n([^\n]{5,60})",
    re.IGNORECASE,
)

# Apartment / Depto
_GC_APARTMENT_RE = re.compile(
    r"d[oe]pto\.?\s*(\d+)",
    re.IGNORECASE,
)

# Owner name: consecutive ALL-CAPS words (2–4 words) — allow optional trailing space
_GC_OWNER_RE = re.compile(
    r"^([A-Z\u00C0-\u00DC]{2,}(?:\s+[A-Z\u00C0-\u00DC]{2,}){1,4})\s*$",
    re.MULTILINE,
)

# Alícuota percentage (e.g. "0,95110 %" or "O 95110 %" after font garbling)
_GC_ALICUOTA_RE = re.compile(
    r"(?:al[íif]cuota\s+total\s*)?[O0]\s*[,.]?\s*(\d{4,6})\s*%",
    re.IGNORECASE,
)

# Building total expense: "$14.083.315" — 8-digit+ amounts appearing after the
# "Gasto Común" / "Prorratur" / garbled label line.
# Fallback: any large amount starting with "14" or "1x" followed by 7+ digits.
_GC_BUILDING_TOTAL_RE = re.compile(
    r"(?:gasto\s+com[uú]n|outo\s+c:om[oó]n|prorratu?r)[^\n]*\n[^\n]*\$\s*([\d.]+)"
    r"|\$\s*(1\d[\d.]{6,})",           # fallback: ≥ 8-digit amount
    re.IGNORECASE,
)

# Gastos comunes apartment portion (alícuota %) → amount
# Text shows: "O 95110 % " on one line, "$133.946 " on the next.
_GC_AMOUNT_RE = re.compile(
    r"[O0]\s*9\d{4}\s*%\s*[\s\n]*\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Fondos provision percentage (e.g. "FONDOS 5% DEL GASTO MENSUAL" → 5).
# Only the integer part is captured because the percentage is always a whole
# number (5, 10, …); the "00" suffix from pdfminer garbling is discarded.
_GC_FONDOS_PCT_RE = re.compile(
    r"fondos\s+(\d+)\s*%",
    re.IGNORECASE,
)

# Fondos 5% → amount (text shows "500% " then "$ 6.697 ")
_GC_FONDOS_AMOUNT_RE = re.compile(
    r"5[0,]\s*0\s*%\s*[\s\n]*\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Cargo Fijo → fixed-charge amount (appears in the "recargos" section).
# In pdfminer text the label is "Cargo Fijo"; in OCR output it can be garbled.
# OCR-extracted value is preferred because pdfminer misreads some digits.
_GC_CARGO_FIJO_RE = re.compile(
    r"cargo\s+fijo[\s\S]{0,40}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Subtotal departamento (GC + fondos):
# pdfminer text layer does NOT contain this label — derived from three-amounts block.
_GC_SUBTOTAL_DEPTO_RE = re.compile(
    r"subtotal\s+departamento[\s\S]{0,40}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Three consecutive amounts that represent GC / fondos / subtotal
# e.g. "$133.946 \n$ 6.697 \n$140.643 "
_GC_THREE_AMOUNTS_RE = re.compile(
    r"\$\s*([\d.,]+)\s*\n\s*\$\s*([\d.,]+)\s*\n\s*\$\s*([\d.,]+)",
)

# Subtotal Recargos: the label appears in the same block as "Total del mes".
# Its amount is the FIRST dollar value in the totals block.
_GC_SUBTOTAL_RECARGOS_RE = re.compile(
    r"subtotal\s+recargos[\s\S]{0,60}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Total del mes / Total a pagar — the actual total is the SECOND or THIRD amount
# after the "Subtotal Recargos / Total del mes" label block.
# Strategy: find all dollar amounts in the block starting at "Subtotal Recargos"
# and return the maximum (the grand total is always the largest).
_GC_TOTALS_SECTION_RE = re.compile(
    r"subtotal\s+recargos[\s\S]{0,300}",
    re.IGNORECASE,
)

# Last-payment section: three labels (Fecha / Monto / Folio) appear first,
# then their values (date / amount / 5-digit folio) on separate lines.
# Layout:
#   "Fecha ultlmo Pago "   ← L45
#   ""
#   "Monto ultlmo Pago "   ← L47
#   ""
#   "Follo ultimo Pago "   ← L49
#   ""
#   "23-01-2028 "          ← L51  (date, year garbled)
#   ""
#   "$177.154 "            ← L53  (amount)
#   ""
#   "44829 "               ← L55  (folio, 5 digits)
_GC_LAST_PAYMENT_DATE_RE = re.compile(
    r"fecha\s+ult[il][lm][oi]\s+pago[\s\S]{0,100}?(\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)
_GC_LAST_PAYMENT_AMOUNT_RE = re.compile(
    r"monto\s+ult[il][lm][oi]\s+pago[\s\S]{0,100}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)
_GC_LAST_PAYMENT_FOLIO_RE = re.compile(
    r"fol[il][oi]\s+ulti[\s\S]{0,100}?(\d{5,6})",   # 5–6 digits only (avoids 4-digit years)
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# OCR patterns (applied to tesseract output, which is more legible)
# ---------------------------------------------------------------------------
# Hot-water table row — two variants:
# a) All on one line: "Agua Caliente  585,396000  588,379000  2,983000  7.034,70  $20.985"
# b) "Agua Caliente" on preceding line, numbers on next line
# c) Consumo column absent (OCR may skip it): "Agua Caliente | 585,396000| 588,379000 7.034,70"
# OCR may render commas as periods and may insert '|' column separators.
_GC_OCR_HOT_WATER_ROW_RE = re.compile(
    r"agua\s+caliente[\s\S]{0,60}?"
    r"([\d,.]{6,}\d{3})[\s|]+"   # group 1: lectura anterior (e.g. 585,396000 or 585.396000)
    r"([\d,.]{6,}\d{3})[\s|]+"   # group 2: lectura actual   (e.g. 588,379000)
    r"(?:([\d,.]+)[\s|]+)?"       # group 3: consumo (optional — OCR may omit the column)
    r"([\d.,]+)"                  # group 4: valor total       (e.g. 7.034,70)
    r"(?:[\s\S]{0,20}?\$\s*([\d.,]+))?",  # group 5: optional monto (e.g. $20.985)
    re.IGNORECASE,
)
# Subtotal Consumo (hot-water subtotal) from OCR — the amount can be on the
# same line or a nearby line.
_GC_OCR_SUBTOTAL_CONSUMO_RE = re.compile(
    r"subtotal\s+consumo[\s\S]{0,60}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR building name: PSM-4 pass renders "Edificio Jose Miguel Fecha Emisión:"
# on a single line — capture the words between "Edificio" and "Fecha".
_GC_OCR_BUILDING_NAME_RE = re.compile(
    r"edificio\s+([\w\s]{2,50}?)\s+fecha\s+em",
    re.IGNORECASE,
)

# OCR Cargo Fijo: the fixed-charge row.  PSM modes that preserve columns render
# "Cargo Fijo   $9.638" (or with a pipe separator) on one line.
# Use a tight window (≤ 30 chars) to avoid capturing the next-line total.
_GC_OCR_CARGO_FIJO_RE = re.compile(
    r"cargo\s+fijo[\s|]{0,30}\$?\s*([\d.,]+)",
    re.IGNORECASE,
)

# Maximum allowable year drift due to pdfminer font-encoding garbling.
# Years garbled by more than this threshold are left unchanged.
_GC_MAX_YEAR_DRIFT = 4

# ---------------------------------------------------------------------------
# Meter-reading format constants
# ---------------------------------------------------------------------------
# Minimum number of fractional digits for a separator to be treated as a
# decimal point rather than a thousands separator.
_METER_DECIMAL_MIN_DIGITS: int = 4

# Meter reading with no separator: digit counts and split positions.
# A 9-digit integer (e.g. 585396000) → first 3 digits = integer, next 3 = decimal.
_METER_9DIGIT_INT_DIGITS: int = 3
_METER_9DIGIT_DEC_DIGITS: int = 3
# A 7-digit integer (e.g. 2983000) → first 1 digit = integer, next 3 = decimal.
_METER_7DIGIT_INT_DIGITS: int = 1
_METER_7DIGIT_DEC_DIGITS: int = 3

# ---------------------------------------------------------------------------
# OCR rendering constants
# ---------------------------------------------------------------------------
# Zoom factor applied when rendering PDF pages for OCR.  3× gives ~216 DPI
# from a 72-DPI base, which is adequate for Tesseract accuracy.
_OCR_ZOOM_FACTOR: int = 3

# Upscale factor applied to cropped sections before Tesseract (pass 2).
_OCR_CROP_RESIZE_FACTOR: int = 2

# Vertical crop ratios (fraction of page height) for the Agua Caliente table.
_OCR_CROP2_TOP_RATIO: float = 0.30
_OCR_CROP2_BOTTOM_RATIO: float = 0.55

# Month → number mapping (Spanish)
_MONTH_NAME_TO_NUM: dict[str, int] = {
    "enero": 1, "ene": 1,
    "febrero": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "mayo": 5,
    "junio": 6, "jun": 6,
    "julio": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "septiembre": 9, "sep": 9,
    "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}


def _gc_fix_year(date_str: str, expected_year: str) -> str:
    """Replace a garbled 4-digit year in *date_str* with *expected_year*.

    pdfminer's font-encoding issues can cause digits to shift (e.g. "6"→"8"),
    producing years like "2028" when the actual year is "2026".  If the
    extracted year differs from *expected_year* by at most 4 years and the
    extracted year is in the future relative to *expected_year*, substitute it.
    """
    m = re.search(r"(\d{4})$", date_str)
    if not m:
        return date_str
    extracted_year = int(m.group(1))
    try:
        correct_year = int(expected_year)
    except ValueError:
        return date_str
    if 0 < extracted_year - correct_year <= _GC_MAX_YEAR_DRIFT:
        return date_str[: m.start()] + expected_year
    return date_str


def _parse_meter_reading(raw: str) -> float:
    """Parse a water/hot-water meter reading that has 6 decimal places.

    Meter readings in Chilean building documents use the format
    ``NNN,NNNxxx`` or ``NNN.NNNxxx`` where ``xxx`` is typically ``000``
    (sub-precision padding).  OCR often strips the decimal separator,
    yielding a 9-digit integer like ``585396000``.  This function
    normalises all such variants to a float in m³.

    Examples::

        "585,396000" → 585.396   (comma = decimal point, 6 decimal digits)
        "585396000"  → 585.396   (no separator, 9 digits → first 3 = integer)
        "2,983000"   → 2.983     (comma = decimal, 6 decimal digits)
        "588.379000" → 588.379   (dot = decimal)
    """
    raw = raw.strip().replace(" ", "")
    # Remove thousands separator if present (only when followed by exactly 3 digits)
    # For meter readings, any separator followed by 4+ digits is a decimal point.
    sep_match = re.search(r"([,.])([\d]+)$", raw)
    if sep_match:
        frac = sep_match.group(2)
        if len(frac) >= _METER_DECIMAL_MIN_DIGITS:
            # Treat separator as decimal point regardless of digit count
            integer_part = raw[: sep_match.start()].replace(".", "").replace(",", "")
            return float(f"{integer_part}.{frac}")
    # No separator or only 3 digits after sep — try plain integer interpretation.
    # A 9-digit number with no decimal: first 3 digits are integer part.
    clean = raw.replace(",", "").replace(".", "")
    if clean.isdigit():
        if len(clean) == 9:  # noqa: PLR2004 -- 9-digit meter-reading format
            return float(
                clean[:_METER_9DIGIT_INT_DIGITS]
                + "."
                + clean[_METER_9DIGIT_INT_DIGITS : _METER_9DIGIT_INT_DIGITS + _METER_9DIGIT_DEC_DIGITS]
            )
        if len(clean) == 7:  # noqa: PLR2004 -- 7-digit meter-reading format
            return float(
                clean[:_METER_7DIGIT_INT_DIGITS]
                + "."
                + clean[_METER_7DIGIT_INT_DIGITS : _METER_7DIGIT_INT_DIGITS + _METER_7DIGIT_DEC_DIGITS]
            )
    # Fallback: standard consumption parser
    return _parse_consumption_to_float(raw)


def _try_ocr_pdf(pdf_path: str) -> str:
    """Render the first page of *pdf_path* and run Tesseract OCR on it.

    Uses three passes:
    1. Full-page OCR with PSM 1 (auto orientation) — captures header fields.
    2. Middle-section crop with PSM 6 (uniform block) — captures the water
       consumption table (Agua Caliente) which is column-heavy.
    3. Full-page OCR with PSM 4 (single column) — captures the totals table
       with accurate ``subtotal_recargos`` (e.g. ``$9.638``).

    Returns the combined OCR plain text, or an empty string if the required
    libraries (``pypdfium2``, ``pytesseract``, ``PIL``) are not installed or if
    any error occurs.  Failures are logged at DEBUG level only.
    """
    global _tesseract_available  # noqa: PLW0603
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
        import pytesseract  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError as exc:
        _LOGGER.warning(
            "OCR unavailable for '%s': missing library (%s). "
            "The integration requires 'pypdfium2', 'pytesseract', and 'Pillow'; "
            "also ensure the 'tesseract-ocr' system binary is installed to "
            "enable Agua Caliente (hot water) sensor extraction.",
            pdf_path,
            exc,
        )
        return ""
    # Use the modern Resampling API if available (Pillow ≥ 10.0.0, where LANCZOS
    # moved under Image.Resampling), else fall back to the legacy attribute.
    _lanczos = getattr(Image, "Resampling", Image).LANCZOS
    try:
        doc = pdfium.PdfDocument(pdf_path)
        page = doc[0]
        page_height = page.get_height()
        bitmap = page.render(scale=_OCR_ZOOM_FACTOR)
        doc.close()
        img_full = bitmap.to_pil()

        # Pass 1 — full page, PSM 1
        text_full: str = pytesseract.image_to_string(
            img_full, lang="spa", config="--psm 1"
        )

        # Pass 2 — agua caliente area crop (≈ 30–55 % from top), PSM 6
        img_width = img_full.width
        crop2_top = int(page_height * _OCR_CROP2_TOP_RATIO * _OCR_ZOOM_FACTOR)
        crop2_bot = int(page_height * _OCR_CROP2_BOTTOM_RATIO * _OCR_ZOOM_FACTOR)
        crop2 = img_full.crop((0, crop2_top, img_width, crop2_bot))
        crop2 = crop2.resize(
            (img_width * _OCR_CROP_RESIZE_FACTOR,
             (crop2_bot - crop2_top) * _OCR_CROP_RESIZE_FACTOR),
            _lanczos,
        )
        text_crop2: str = pytesseract.image_to_string(crop2, lang="spa", config="--psm 6")

        # Pass 3 — full page, PSM 4 (single column).
        # This pass reliably captures the totals table with accurate
        # ``subtotal_recargos`` (column-separated amounts, e.g. "$9.638").
        text_crop3: str = pytesseract.image_to_string(
            img_full, lang="spa", config="--psm 4"
        )

        _tesseract_available = True
        return text_full + "\n" + text_crop2 + "\n" + text_crop3
    except pytesseract.TesseractNotFoundError as exc:
        _tesseract_available = False
        _LOGGER.debug(
            "Tesseract OCR not found for '%s': %s. "
            "Install tesseract-ocr to enable Agua Caliente extraction.",
            pdf_path,
            exc,
        )
        return ""
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("OCR failed for '%s': %s", pdf_path, err)
        return ""


def _extract_common_expenses_pdf_attributes(
    text: str, pdf_path: str = ""
) -> dict[str, Any]:
    """Extract Gastos Comunes (and optional Agua Caliente) attributes from a PDF.

    This extractor handles the "Nota de Cobro" document issued by Chilean
    building administrators.  The PDF is JPEG-backed: only a partial text layer
    (pdfminer-readable, but font-encoded) overlays certain fields.  The
    hot-water table lives exclusively in the JPEG background and requires OCR
    (optional: pypdfium2 + pytesseract + tesseract-ocr must be installed).

    **Tier 1 – pdfminer text layer** (always attempted):
        ``billing_period_month``, ``billing_period_year``,
        ``billing_period_start``, ``billing_period_end``,
        ``emission_date``, ``due_date``,
        ``building_name``, ``building_rut``, ``address``,
        ``apartment``, ``owner_name``, ``alicuota``,
        ``building_total_expense``,
        ``gastos_comunes_amount``, ``fondos_amount``, ``subtotal_departamento``,
        ``subtotal_recargos``, ``total_amount``,
        ``last_payment_date``, ``last_payment_amount``, ``last_payment_folio``

    **Tier 2 – OCR on JPEG background** (requires pdf_path + optional libs):
        ``hot_water_reading_prev``, ``hot_water_reading_curr``,
        ``hot_water_consumption``, ``hot_water_consumption_unit``,
        ``hot_water_cost_per_m3``, ``hot_water_amount``

    Reference PDF: "Gastos Comunes Enero 2026" (Edificio Jose Miguel, 1 page).
    """
    attrs: dict[str, Any] = {}
    # Per-attribute confidence scores populated as each field is extracted.
    # Merged into attrs["_confidence"] at the end of the function.
    _confidence: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Billing period (month + year)
    # ------------------------------------------------------------------
    period_m = _GC_BILLING_PERIOD_RE.search(text)
    if period_m:
        month_name = period_m.group(1).lower()
        year_str = period_m.group(2)
        attrs["billing_period_month"] = period_m.group(1).capitalize()
        attrs["billing_period_year"] = year_str
        month_num = _MONTH_NAME_TO_NUM.get(month_name[:3], 0)
        if month_num:
            attrs["billing_period_start"] = f"01-{month_num:02d}-{year_str}"
            # End = last day of billing month (approximate: next month day 0)
            last_day = calendar.monthrange(int(year_str), month_num)[1]
            attrs["billing_period_end"] = f"{last_day:02d}-{month_num:02d}-{year_str}"
        for _k in ("billing_period_month", "billing_period_year",
                   "billing_period_start", "billing_period_end"):
            if _k in attrs:
                _confidence[_k] = CONF_SCORE_PDFMINER
    else:
        year_str = ""

    # ------------------------------------------------------------------
    # Emission date + due date (two dates extracted together)
    # Both labels and their values appear in the same text-block region.
    # The first date is the emission date; the second is the due date.
    # ------------------------------------------------------------------
    dates_m = _GC_DATES_BLOCK_RE.search(text)
    if dates_m:
        attrs["emission_date"] = dates_m.group(1).replace("/", "-")
        raw_due = dates_m.group(2).replace("/", "-")
        attrs["due_date"] = _gc_fix_year(raw_due, year_str) if year_str else raw_due
        _confidence["emission_date"] = CONF_SCORE_PDFMINER
        _confidence["due_date"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Building RUT and name
    # ------------------------------------------------------------------
    rut_m = _GC_RUT_RE.search(text)
    if rut_m:
        attrs["building_rut"] = rut_m.group(1)
        _confidence["building_rut"] = CONF_SCORE_PDFMINER

    name_m = _GC_BUILDING_NAME_RE.search(text)
    if name_m:
        extracted_name = ("Edificio " + name_m.group(1).strip()).strip()
        # Heuristic: pdfminer font-garbling can produce a slightly wrong
        # building name (e.g. "Jon" instead of "Jose").  When the extracted
        # name is very similar to the known-correct reference name, use the
        # reference so the value stays stable from month to month.  Only
        # accept the extracted name when it differs substantially (genuine
        # building change).  The OCR tier (Tier 2) always overrides this
        # with the accurately-read value when the optional libraries are
        # available.
        sim = difflib.SequenceMatcher(
            None,
            extracted_name.lower(),
            _GC_KNOWN_BUILDING_NAME.lower(),
        ).ratio()
        if sim >= _GC_BUILDING_NAME_SIMILARITY_THRESHOLD:
            attrs["building_name"] = _GC_KNOWN_BUILDING_NAME
        else:
            attrs["building_name"] = extracted_name
        _confidence["building_name"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Address (line immediately after the RUT line)
    # ------------------------------------------------------------------
    addr_m = _GC_ADDRESS_RE.search(text)
    if addr_m:
        attrs["address"] = addr_m.group(1).strip()
        _confidence["address"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Apartment number
    # ------------------------------------------------------------------
    apt_m = _GC_APARTMENT_RE.search(text)
    if apt_m:
        attrs["apartment"] = apt_m.group(1)
        _confidence["apartment"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Owner name (UPPERCASE, 2–4 words)
    # ------------------------------------------------------------------
    owner_candidates = _GC_OWNER_RE.findall(text)
    # Filter out short single-word matches (e.g. "SANTIAGO") and known labels
    _OWNER_EXCLUDE = {"SANTIAGO", "RUT", "FONDOS", "MENSUAL", "ADMINISTRACIONES"}
    for candidate in owner_candidates:
        words = candidate.split()
        if len(words) >= 2 and not _OWNER_EXCLUDE.issuperset(words):
            attrs["owner_name"] = candidate.strip()
            _confidence["owner_name"] = CONF_SCORE_PDFMINER
            break

    # ------------------------------------------------------------------
    # Alícuota percentage
    # ------------------------------------------------------------------
    alicuota_m = _GC_ALICUOTA_RE.search(text)
    if alicuota_m:
        raw = "0." + (alicuota_m.group(1).lstrip("0") or "0")
        try:
            attrs["alicuota"] = round(float(raw), 4)
            _confidence["alicuota"] = CONF_SCORE_PDFMINER
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Building total expense (e.g. $14.083.315)
    # ------------------------------------------------------------------
    bldg_total_m = _GC_BUILDING_TOTAL_RE.search(text)
    if bldg_total_m:
        raw = (bldg_total_m.group(1) or bldg_total_m.group(2) or "").replace(".", "")
        if raw.isdigit():
            attrs["building_total_expense"] = int(raw)
            _confidence["building_total_expense"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Fondos provision percentage (integer, e.g. 5 from "FONDOS 5%")
    # ------------------------------------------------------------------
    fondos_pct_m = _GC_FONDOS_PCT_RE.search(text)
    if fondos_pct_m:
        try:
            attrs["fondos_pct"] = int(fondos_pct_m.group(1))
            _confidence["fondos_pct"] = CONF_SCORE_PDFMINER
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Cargo Fijo (fixed charge) — pdfminer tier
    # The label may not appear clearly; rely on OCR for accuracy (see
    # Tier 2 below).  This pattern is a best-effort attempt on the text
    # layer so the value is available even without OCR libraries.
    # ------------------------------------------------------------------
    cargo_fijo_m = _GC_CARGO_FIJO_RE.search(text)
    if cargo_fijo_m:
        attrs["cargo_fijo"] = _parse_amount_to_int(cargo_fijo_m.group(1))
        _confidence["cargo_fijo"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Breakdown table: GC amount / fondos / subtotal departamento
    # First try individual patterns; fall back to the three-amount block.
    # ------------------------------------------------------------------
    gc_m = _GC_AMOUNT_RE.search(text)
    if gc_m:
        attrs["gastos_comunes_amount"] = _parse_amount_to_int(gc_m.group(1))
        _confidence["gastos_comunes_amount"] = CONF_SCORE_PDFMINER

    fondos_m = _GC_FONDOS_AMOUNT_RE.search(text)
    if fondos_m:
        attrs["fondos_amount"] = _parse_amount_to_int(fondos_m.group(1))
        _confidence["fondos_amount"] = CONF_SCORE_PDFMINER

    sub_depto_m = _GC_SUBTOTAL_DEPTO_RE.search(text)
    if sub_depto_m:
        attrs["subtotal_departamento"] = _parse_amount_to_int(sub_depto_m.group(1))
        _confidence["subtotal_departamento"] = CONF_SCORE_PDFMINER

    # Fallback: three-consecutive-amounts block (GC / fondos / subtotal)
    if not (
        attrs.get("gastos_comunes_amount")
        and attrs.get("fondos_amount")
        and attrs.get("subtotal_departamento")
    ):
        three_m = _GC_THREE_AMOUNTS_RE.search(text)
        if three_m:
            a1 = _parse_amount_to_int(three_m.group(1))
            a2 = _parse_amount_to_int(three_m.group(2))
            a3 = _parse_amount_to_int(three_m.group(3))
            if not attrs.get("gastos_comunes_amount") and a1:
                attrs["gastos_comunes_amount"] = a1
                _confidence["gastos_comunes_amount"] = CONF_SCORE_PDFMINER
            if not attrs.get("fondos_amount") and a2:
                attrs["fondos_amount"] = a2
                _confidence["fondos_amount"] = CONF_SCORE_PDFMINER
            if not attrs.get("subtotal_departamento") and a3:
                attrs["subtotal_departamento"] = a3
                _confidence["subtotal_departamento"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Subtotal recargos + total amount
    # The totals block has three amounts: recargos, total del mes, total a pagar.
    # We take the FIRST as recargos and the MAXIMUM as the grand total.
    # ------------------------------------------------------------------
    totals_m = _GC_TOTALS_SECTION_RE.search(text)
    if totals_m:
        section = totals_m.group(0)
        all_amounts = re.findall(r"\$\s*([\d.,]+)", section)
        parsed_amounts = [_parse_amount_to_int(a) for a in all_amounts]
        if parsed_amounts:
            attrs["subtotal_recargos"] = parsed_amounts[0]
            _confidence["subtotal_recargos"] = CONF_SCORE_PDFMINER
        if len(parsed_amounts) >= 2:
            attrs["total_amount"] = max(parsed_amounts)
            _confidence["total_amount"] = CONF_SCORE_PDFMINER
    else:
        # Fallback individual patterns
        rec_m = _GC_SUBTOTAL_RECARGOS_RE.search(text)
        if rec_m:
            attrs["subtotal_recargos"] = _parse_amount_to_int(rec_m.group(1))
            _confidence["subtotal_recargos"] = CONF_SCORE_PDFMINER
        total_generic = _extract_total_amount(text)
        if total_generic:
            attrs["total_amount"] = total_generic
            _confidence["total_amount"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Last payment information
    # Three labels appear consecutively, then three values on separate lines.
    # ------------------------------------------------------------------
    lp_date_m = _GC_LAST_PAYMENT_DATE_RE.search(text)
    if lp_date_m:
        raw_lp = lp_date_m.group(1).replace("/", "-")
        attrs["last_payment_date"] = _gc_fix_year(raw_lp, year_str) if year_str else raw_lp
        _confidence["last_payment_date"] = CONF_SCORE_PDFMINER

    lp_amount_m = _GC_LAST_PAYMENT_AMOUNT_RE.search(text)
    if lp_amount_m:
        attrs["last_payment_amount"] = _parse_amount_to_int(lp_amount_m.group(1))
        _confidence["last_payment_amount"] = CONF_SCORE_PDFMINER

    lp_folio_m = _GC_LAST_PAYMENT_FOLIO_RE.search(text)
    if lp_folio_m:
        attrs["last_payment_folio"] = lp_folio_m.group(1)
        _confidence["last_payment_folio"] = CONF_SCORE_PDFMINER

    # ------------------------------------------------------------------
    # Tier 2: OCR-based hot-water extraction (optional)
    # ------------------------------------------------------------------
    if pdf_path:
        ocr_text = _try_ocr_pdf(pdf_path)
        if ocr_text:
            # Hot-water row — use meter-reading-aware parser for readings
            hw_m = _GC_OCR_HOT_WATER_ROW_RE.search(ocr_text)
            if hw_m:
                attrs["hot_water_reading_prev"] = _parse_meter_reading(hw_m.group(1))
                attrs["hot_water_reading_curr"] = _parse_meter_reading(hw_m.group(2))
                # group 3 (consumo) is optional — OCR sometimes omits the column
                if hw_m.group(3):
                    attrs["hot_water_consumption"] = _parse_meter_reading(hw_m.group(3))
                else:
                    # Derive consumption from meter readings when the OCR column
                    # is missing: consumo = lectura_actual − lectura_anterior
                    prev_r = attrs["hot_water_reading_prev"]
                    curr_r = attrs["hot_water_reading_curr"]
                    if prev_r is not None and curr_r is not None:
                        attrs["hot_water_consumption"] = round(curr_r - prev_r, 6)
                attrs["hot_water_consumption_unit"] = "m³"
                attrs["hot_water_cost_per_m3"] = _parse_consumption_to_float(
                    hw_m.group(4)
                )
                for _k in ("hot_water_reading_prev", "hot_water_reading_curr",
                           "hot_water_consumption", "hot_water_cost_per_m3"):
                    _confidence[_k] = CONF_SCORE_OCR
                # Monto is optional in the regex (group 5 may be None)
                if hw_m.group(5):
                    attrs["hot_water_amount"] = _parse_amount_to_int(hw_m.group(5))
                    _confidence["hot_water_amount"] = CONF_SCORE_OCR

            # Subtotal Consumo (hot-water subtotal) — prefer this as hot-water
            # total if individual monto was not captured.
            sc_m = _GC_OCR_SUBTOTAL_CONSUMO_RE.search(ocr_text)
            if sc_m:
                subtotal_consumo = _parse_amount_to_int(sc_m.group(1))
                attrs["subtotal_consumo"] = subtotal_consumo
                _confidence["subtotal_consumo"] = CONF_SCORE_OCR
                if not attrs.get("hot_water_amount") and subtotal_consumo:
                    attrs["hot_water_amount"] = subtotal_consumo
                    _confidence["hot_water_amount"] = CONF_SCORE_OCR

            # OCR gives more accurate subtotal_recargos when the $ amount
            # appears on the same line as the label (PSM modes that preserve
            # columns). Use a tight pattern (≤ 20 chars) to avoid accidentally
            # capturing the next line's total.
            rec_ocr_m = re.search(
                r"subtotal\s+recargos[\s|]{0,15}\$?\s*([\d.,]+)",
                ocr_text,
                re.IGNORECASE,
            )
            if rec_ocr_m:
                candidate = _parse_amount_to_int(rec_ocr_m.group(1))
                # Sanity: recargos should be smaller than the grand total
                if candidate and candidate < attrs.get("total_amount", float("inf")):
                    attrs["subtotal_recargos"] = candidate
                    _confidence["subtotal_recargos"] = CONF_SCORE_OCR

            # OCR (PSM 4) gives a cleaner building name than pdfminer which
            # garbles font-encoded characters (e.g. "Jon" instead of "Jose").
            bldg_ocr_m = _GC_OCR_BUILDING_NAME_RE.search(ocr_text)
            if bldg_ocr_m:
                attrs["building_name"] = "Edificio " + bldg_ocr_m.group(1).strip()
                _confidence["building_name"] = CONF_SCORE_OCR

            # OCR gives the correct Cargo Fijo amount (pdfminer may misread
            # digit glyphs, e.g. "$9.638" → "$9.838").  Override any pdfminer
            # value when OCR succeeds.
            cargo_fijo_ocr_m = _GC_OCR_CARGO_FIJO_RE.search(ocr_text)
            if cargo_fijo_ocr_m:
                candidate = _parse_amount_to_int(cargo_fijo_ocr_m.group(1))
                # Sanity: cargo fijo should be smaller than the grand total
                if candidate and candidate < attrs.get("total_amount", float("inf")):
                    attrs["cargo_fijo"] = candidate
                    _confidence["cargo_fijo"] = CONF_SCORE_OCR

    # ------------------------------------------------------------------
    # Combined address for binary-sensor display
    # Format: "<building_name>, <street> Depto.<apt>, <city>"
    # Built from the three separately extracted fields; stored back into
    # the ``address`` key so the standard binary-sensor lookup works.
    # ------------------------------------------------------------------
    building = attrs.get("building_name", "")
    raw_addr = attrs.get("address", "")  # e.g. "Curico 380 - Santiago"
    apt_str = attrs.get("apartment", "")
    if building and raw_addr:
        if " - " in raw_addr:
            street_part, city_part = raw_addr.split(" - ", 1)
        else:
            street_part, city_part = raw_addr, ""
        street_seg = street_part.strip()
        if apt_str:
            street_seg += f" Depto.{apt_str}"
        combined_parts = [building]
        if street_seg:
            combined_parts.append(street_seg)
        if city_part:
            combined_parts.append(city_part.strip())
        attrs["address"] = ", ".join(combined_parts)

    # ------------------------------------------------------------------
    # Attribute aliases consumed by the status binary sensor.
    # The binary sensor looks up keys by the alias names defined in its
    # service-type-specific defaults dict; these aliases map the raw
    # extracted keys to the expected names without duplicating logic.
    # ------------------------------------------------------------------
    # common_expenses binary-sensor attributes
    if "building_total_expense" in attrs:
        attrs["gross_common_expenses"] = attrs["building_total_expense"]
    if "alicuota" in attrs:
        attrs["gross_common_expenses_percentage"] = attrs["alicuota"]
    # hot_water binary-sensor attributes
    if "hot_water_reading_prev" in attrs:
        attrs["previous_measure"] = attrs["hot_water_reading_prev"]
    if "hot_water_reading_curr" in attrs:
        attrs["actual_measure"] = attrs["hot_water_reading_curr"]

    # ------------------------------------------------------------------
    # Derived common-expenses sensor aliases
    # These keys are consumed by the dedicated breakdown sensor entities.
    # ------------------------------------------------------------------
    # Funds provision percentage: integer (e.g. 5 from "FONDOS 5%")
    if "fondos_pct" in attrs:
        attrs["funds_provision_percentage"] = attrs["fondos_pct"]
        _confidence["funds_provision_percentage"] = _confidence.get("fondos_pct", CONF_SCORE_PDFMINER)
    # Funds provision amount = fondos 5% (e.g. $6.697)
    if "fondos_amount" in attrs:
        attrs["funds_provision"] = attrs["fondos_amount"]
        _confidence["funds_provision"] = _confidence.get("fondos_amount", CONF_SCORE_PDFMINER)
    # Subtotal = GC apartment portion + fondos (Subtotal Departamento)
    if "subtotal_departamento" in attrs:
        attrs["subtotal"] = attrs["subtotal_departamento"]
        _confidence["subtotal"] = _confidence.get("subtotal_departamento", CONF_SCORE_PDFMINER)
    # Fixed charge (Cargo Fijo) — prefer cargo_fijo; fall back to subtotal_recargos
    if not attrs.get("cargo_fijo") and attrs.get("subtotal_recargos"):
        attrs["cargo_fijo"] = attrs["subtotal_recargos"]
        _confidence["cargo_fijo"] = _confidence.get("subtotal_recargos", CONF_SCORE_PDFMINER)
    if "cargo_fijo" in attrs:
        attrs["fixed_charge"] = attrs["cargo_fijo"]
        _confidence["fixed_charge"] = _confidence.get("cargo_fijo", CONF_SCORE_PDFMINER)

    # ------------------------------------------------------------------
    # Agua caliente (Subtotal Consumo) derivation — fallback when OCR did
    # not capture the value directly via _GC_OCR_SUBTOTAL_CONSUMO_RE.
    #
    # The "Nota de Cobro" PDF structure guarantees:
    #   Total del mes = Subtotal Departamento + Subtotal Consumo + Cargo Fijo
    # so Subtotal Consumo can be back-calculated when the other three are
    # known.  When cargo_fijo comes from the OCR tier its value is correct
    # ($9.638); without OCR the pdfminer text layer may misread it ($9.838
    # due to font garbling), making the derived figure slightly off (~$200).
    # ------------------------------------------------------------------
    if not attrs.get("subtotal_consumo"):
        total = attrs.get("total_amount", 0)
        sub_depto = attrs.get("subtotal_departamento", 0)
        cargo = attrs.get("cargo_fijo", 0)
        if total and sub_depto and cargo and total > sub_depto + cargo:
            derived_consumo = total - sub_depto - cargo
            attrs["subtotal_consumo"] = derived_consumo
            _confidence["subtotal_consumo"] = CONF_SCORE_DERIVED
            if not attrs.get("hot_water_amount"):
                attrs["hot_water_amount"] = derived_consumo
                _confidence["hot_water_amount"] = CONF_SCORE_DERIVED

    # GC total = Subtotal Departamento + Cargo Fijo
    # (does NOT include hot-water, which is a separate device)
    subtotal_val = attrs.get("subtotal_departamento", 0)
    cargo_val = attrs.get("cargo_fijo", 0)
    if subtotal_val and cargo_val:
        attrs["gc_total"] = subtotal_val + cargo_val
        _confidence["gc_total"] = CONF_SCORE_DERIVED

    # Propagate alias confidence from binary-sensor attributes
    if "building_total_expense" in attrs:
        _confidence["gross_common_expenses"] = _confidence.get(
            "building_total_expense", CONF_SCORE_PDFMINER
        )
    if "alicuota" in attrs:
        _confidence["gross_common_expenses_percentage"] = _confidence.get(
            "alicuota", CONF_SCORE_PDFMINER
        )
    if "hot_water_reading_prev" in attrs:
        _confidence["previous_measure"] = _confidence.get(
            "hot_water_reading_prev", CONF_SCORE_OCR
        )
    if "hot_water_reading_curr" in attrs:
        _confidence["actual_measure"] = _confidence.get(
            "hot_water_reading_curr", CONF_SCORE_OCR
        )

    # Store per-attribute confidence scores in the returned dict.
    # Keys starting with "_" are metadata — not exposed as HA state attributes.
    if _confidence:
        attrs["_confidence"] = _confidence

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
    # Common expenses and hot water carry no useful data in the notification
    # email body — all information is in the PDF attachment.
    if service_type in (SERVICE_TYPE_COMMON_EXPENSES, SERVICE_TYPE_HOT_WATER):
        return {}
    return {}


def _extract_pdf_type_specific_attributes(
    text: str, service_type: str, pdf_path: str = ""
) -> dict[str, Any]:
    """Dispatch to the **PDF** extractor for *service_type* and return its results.

    Each service type has a dedicated PDF extractor whose patterns are tuned to
    the layout of that issuer's PDF bill — separate from the email extractor.
    Service types that do not yet have a PDF-specific extractor fall back to an
    empty dict (no attributes extracted from the PDF).

    Args:
        text:         Plain text extracted from the PDF via pdfminer.
        service_type: One of the ``SERVICE_TYPE_*`` constants.
        pdf_path:     Optional absolute path to the PDF file.  Passed to the
                      common-expenses extractor to enable optional OCR.
    """
    if service_type == SERVICE_TYPE_WATER:
        return _extract_water_pdf_attributes(text)
    if service_type == SERVICE_TYPE_GAS:
        return _extract_gas_pdf_attributes(text)
    if service_type == SERVICE_TYPE_ELECTRICITY:
        return _extract_electricity_pdf_attributes(text)
    if service_type in (SERVICE_TYPE_COMMON_EXPENSES, SERVICE_TYPE_HOT_WATER):
        # Both devices are fed by the same PDF; the caller can differentiate
        # by service_type when consuming the returned dictionary.
        return _extract_common_expenses_pdf_attributes(text, pdf_path)
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
      ``cubic_meter_treatment``, ``water_consumption``,
      ``wastewater_recolection``, ``wastewater_treatment``,
      ``subtotal``, ``other_charges``
    - **gas** — :func:`_extract_gas_pdf_attributes` (Metrogas, Jan 2026):
      ``consumption``, ``consumption_unit``, ``cost_per_m3s``, ``total_amount``
    - **electricity** — :func:`_extract_electricity_pdf_attributes` (Enel, Feb 2026):
      ``billing_period_start``, ``billing_period_end``, ``consumption``,
      ``consumption_unit``, ``electricity_consumption``, ``service_administration``,
      ``electricity_transport``, ``stabilization_fund``, ``cost_per_kwh``
    - **common_expenses / hot_water** — :func:`_extract_common_expenses_pdf_attributes`
      (Chilean building administrator "Nota de Cobro", Jan 2026).
      Both ``SERVICE_TYPE_COMMON_EXPENSES`` and ``SERVICE_TYPE_HOT_WATER`` share
      the same PDF; the full extracted dictionary is returned for both so the
      caller can select the relevant fields.
      Tier-1 (pdfminer): ``billing_period_month``, ``billing_period_year``,
      ``billing_period_start``, ``billing_period_end``, ``emission_date``,
      ``due_date``, ``building_name``, ``building_rut``, ``address``,
      ``apartment``, ``owner_name``, ``alicuota``, ``building_total_expense``,
      ``gastos_comunes_amount``, ``fondos_amount``, ``subtotal_departamento``,
      ``subtotal_recargos``, ``total_amount``, ``last_payment_date``,
      ``last_payment_amount``, ``last_payment_folio``.
      Tier-2 (OCR, requires pypdfium2 + pytesseract + tesseract-ocr):
      ``hot_water_reading_prev``, ``hot_water_reading_curr``,
      ``hot_water_consumption``, ``hot_water_consumption_unit``,
      ``hot_water_cost_per_m3``, ``hot_water_amount``, ``subtotal_consumo``.

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
        pdf_attrs = _extract_pdf_type_specific_attributes(pdf_text, service_type, pdf_path)
        attrs.update(pdf_attrs)

        # Ensure every non-metadata attribute has a confidence score.
        # Extractors that do not provide per-attribute confidence (water, gas,
        # electricity) receive the default pdfminer confidence (70%).
        confidence = attrs.setdefault("_confidence", {})
        for key in list(attrs):
            if not key.startswith("_") and key not in confidence:
                confidence[key] = CONF_SCORE_PDFMINER
    except Exception as err:
        _LOGGER.debug("Error extracting attributes from PDF '%s': %s", pdf_path, err)

    return attrs
