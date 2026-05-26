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
from pathlib import Path
import re
import unicodedata
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Any

from .const import (
    JSON_MAX_FILES,
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
#   OCR       (85) – extracted via OCR.space cloud API; more accurate
#                    for image-backed PDFs but still fallible.
#   DERIVED   (60) – calculated from other extracted values (e.g.
#                    subtotal_consumo = total − subtotal_depto − cargo_fijo).
#   OVERRIDE (100) – user-supplied correction stored by the ``set_value``
#                    service; treated as ground truth.
CONF_SCORE_PDFMINER: float = 70.0
CONF_SCORE_OCR: float = 85.0
CONF_SCORE_DERIVED: float = 60.0
CONF_SCORE_OVERRIDE: float = 100.0

# ---------------------------------------------------------------------------
# OCR engine availability state
# ---------------------------------------------------------------------------
# Tracks whether the OCR engine is available.
#   None  – not yet determined (no OCR attempt has been made in this process)
#   True  – a successful OCR run confirmed the engine is working
#   False – the OCR engine failed (no API key configured or runtime error)
#
# Updated by ``_try_ocr_pdf`` and read by the HA sensor coordinator to manage
# a persistent Repair issue and notification that guides users to configure
# an OCR.space API key.
_ocr_available: bool | None = None


def is_ocr_available() -> bool | None:
    """Return the current OCR-engine availability state.

    ``None``  – no OCR attempt has been made yet.
    ``True``  – OCR engine working (OCR.space API).
    ``False`` – OCR engine unavailable (no API key or runtime failure).
    """
    return _ocr_available


def _store_ocrspace_json_snapshot(
    pdf_path: str,
    payload: dict[str, Any],
    json_dir: str = "",
) -> None:
    """Store the OCR.space raw JSON payload for *pdf_path* in *json_dir*.

    The output file is named after the PDF stem (e.g. ``gc_2026-04_45313.json``)
    so each new OCR run naturally overwrites the previous result for the same
    billing PDF.  Up to :data:`JSON_MAX_FILES` files are kept in *json_dir*;
    older files are deleted when the limit is exceeded.

    Args:
        pdf_path: Absolute path to the source PDF (used to derive the filename).
        payload:  OCR.space API response dict to serialise as JSON.
        json_dir: Directory where the JSON file is written.  Defaults to a
                  ``json/`` sibling directory next to the PDF's parent when
                  not supplied (backward-compatible fallback).
    """
    import json

    if not pdf_path:
        return

    try:
        pdf_file = Path(pdf_path).resolve()
        if json_dir:
            out_dir = Path(json_dir)
        else:
            # Fallback: place json/ next to the pdfs/ directory
            out_dir = pdf_file.parent.parent / "json"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pdf_file.stem}.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _LOGGER.debug("OCR.space JSON snapshot saved to '%s'", out_path)
    except OSError as err:
        _LOGGER.warning(
            "Could not store OCR.space JSON snapshot for '%s': %s", pdf_path, err
        )
        return

    try:
        snapshots = sorted(
            out_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_path in snapshots[JSON_MAX_FILES:]:
            try:
                old_path.unlink()
            except OSError as err:
                _LOGGER.debug(
                    "Could not delete old OCR JSON snapshot '%s': %s", old_path, err
                )
    except OSError as err:
        _LOGGER.debug(
            "Could not apply retention to OCR JSON dir '%s': %s", out_dir, err
        )


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
_WATER_AA_PDF_FIXED_CHARGE_ROW_RE = re.compile(
    r"^CARGO\s+FIJO(?:\s*\|\s*|\s+)(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
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
# Newer Aguas Andinas layout (column-serialised, NO PUNTA + PUNTA split):
# pdfminer reads labels first, then m³ sub-values, then CLP amounts — all on
# separate lines.  CARGO FIJO has no m³ sub-value; SUBTOTAL SERVICIO,
# TOTAL VENTA and DESCUENTO LEY REDONDEO also appear only in the amounts
# column.  Four m³ values follow the labels block (NO PUNTA, PUNTA,
# RECOLECCION, TRATAMIENTO), then eight CLP amounts in row order.
#
# Group mapping:
#   group 1  – NO PUNTA m³          (e.g. "9,69")
#   group 2  – PUNTA m³             (e.g. "1,94")
#   group 3  – RECOLECCION m³       (e.g. "11,63")
#   group 4  – TRATAMIENTO m³       (e.g. "11,63")
#   group 5  – CARGO FIJO CLP       (e.g. "914")
#   group 6  – NO PUNTA CLP         (e.g. "5.803")
#   group 7  – PUNTA CLP            (e.g. "1.150")
#   group 8  – RECOLECCION CLP      (e.g. "5.267")
#   group 9  – TRATAMIENTO CLP      (e.g. "3.564")
#   group 10 – SUBTOTAL CLP         (e.g. "16.698")
#   group 11 – TOTAL VENTA CLP      (not stored)
#   group 12 – DESCUENTO CLP        (e.g. "-8")
_WATER_AA_PDF_BILLING_TABLE_NOPUNTA_RE = re.compile(
    r"CARGO\s+FIJO\n"
    r"CONSUMO\s+AGUA\s+POTABLE\s+NO\s+PUNTA\n"
    r"CONSUMO\s+AGUA\s+POTABLE\s+PUNTA\n"
    r"RECOLECCI[OÓ]N\s+AGUAS\s+SERVIDAS\n"
    r"TRATAMIENTO\s+AGUAS\s+SERVIDAS\n"
    r"SUBTOTAL\s+SERVICIO\n"
    r"TOTAL\s+VENTA\n"
    r"DESCUENTO\s+LEY\s+REDONDEO\n"
    r"\s*TOTAL\s+A\s+PAGAR\s*\n"           # intermediate label (not stored)
    r"\s*([0-9][0-9.,]*)\s*\n"             # group 1  – NO PUNTA m³
    r"\s*([0-9][0-9.,]*)\s*\n"             # group 2  – PUNTA m³
    r"\s*([0-9][0-9.,]*)\s*\n"             # group 3  – RECOLECCION m³
    r"\s*([0-9][0-9.,]*)\s*\n"             # group 4  – TRATAMIENTO m³
    r"\s*([0-9][0-9.,]+)\s*\n"             # group 5  – CARGO FIJO CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 6  – NO PUNTA CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 7  – PUNTA CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 8  – RECOLECCION CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 9  – TRATAMIENTO CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 10 – SUBTOTAL CLP
    r"([0-9][0-9.,]+)\s*\n"               # group 11 – TOTAL VENTA CLP (not stored)
    r"(-?[0-9][0-9.,]*)",                  # group 12 – DESCUENTO CLP
    re.IGNORECASE,
)
# Line-by-line fallback patterns (used when the column-serialised table
# structure is not present — e.g. custom or third-party PDF layouts).
_WATER_AA_PDF_WATER_NO_PUNTA_RE = re.compile(
    r"^CONSUMO\s+AGUA\s+POTABLE\s+NO\s+PUNTA(?:\s*\|\s*|\s+)"
    r"([0-9]+(?:[.,][0-9]+)?)(?:\s*\|\s*|\s+)"
    r"(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WATER_AA_PDF_WATER_PUNTA_RE = re.compile(
    r"^CONSUMO\s+AGUA\s+POTABLE\s+PUNTA(?:\s*\|\s*|\s+)"
    r"([0-9]+(?:[.,][0-9]+)?)(?:\s*\|\s*|\s+)"
    r"(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WATER_AA_PDF_WW_RECOLECTION_RE = re.compile(
    r"^RECOLECCI[OÓ]N\s+AGUAS\s+SERVIDAS[^\n]*?(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WATER_AA_PDF_WW_TREATMENT_RE = re.compile(
    r"^TRATAMIENTO\s+AGUAS\s+SERVIDAS[^\n]*?(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WATER_AA_PDF_INTEREST_RE = re.compile(
    r"^INTER[EÉ]S\s+DEUDA[^\n]*?(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WATER_AA_PDF_ROUNDING_DISCOUNT_RE = re.compile(
    r"^DESCUENTO\s+LEY\s+REDONDEO[^\n]*?(-?[0-9][0-9.,]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
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
        ``fixed_charge``                  – fixed service charge as integer
        ``water_consumption_non_peak``    – no-punta potable-water amount (CLP)
        ``water_consumption_peak``        – punta potable-water amount (CLP)
        ``water_consumption_non_peak_m3`` – no-punta potable-water consumption (m³)
        ``water_consumption_peak_m3``     – punta potable-water consumption (m³)
        ``water_consumption``             – potable water charge as integer (CLP)
        ``wastewater_recolection``        – wastewater collection charge as integer (CLP)
        ``wastewater_treatment``          – wastewater treatment charge as integer (CLP)
        ``other_charges``                 – net surcharges (interest − rounding) as integer

    The following attributes are formula-derived from the values above and are
    computed here as an initial pass (confidence ``CONF_SCORE_DERIVED``).  They
    are also recomputed by ``_recompute_water_derived_attrs`` after any
    ``set_value`` override, so they always stay consistent:

        ``cost_per_unit_non_peak`` = ``water_consumption_non_peak / water_consumption_non_peak_m3``
        ``cost_per_unit_peak``     = ``water_consumption_peak / water_consumption_peak_m3``
        ``cubic_meter_collection`` = ``wastewater_recolection / consumption``
        ``cubic_meter_treatment``  = ``wastewater_treatment / consumption``
        ``subtotal``               = ``fixed_charge + water_consumption_non_peak
                                     + water_consumption_peak + wastewater_recolection
                                     + wastewater_treatment``
        ``total_amount``           = ``subtotal + other_charges``
    """
    attrs: dict[str, Any] = {}
    _confidence: dict[str, float] = {}

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

    # Fixed charge — published tariff block "Cargo fijo = $ NNN"
    fixed_match = _WATER_AA_PDF_FIXED_CHARGE_RE.search(text)
    if fixed_match:
        attrs["fixed_charge"] = _parse_amount_to_int(fixed_match.group(1))
    else:
        fixed_row_match = _WATER_AA_PDF_FIXED_CHARGE_ROW_RE.search(text)
        if fixed_row_match:
            attrs["fixed_charge"] = _parse_amount_to_int(fixed_row_match.group(1))

    # Billing breakdown table — water_consumption, wastewater charges and
    # other_charges.
    # Supports:
    #   1) legacy column-serialised table (single "CONSUMO AGUA POTABLE" row)
    #   2) newer column-serialised table ("NO PUNTA" + "PUNTA" split rows)
    #   3) line-by-line fallback (each row on its own line with m³ and CLP)
    table_match = _WATER_AA_PDF_BILLING_TABLE_RE.search(text)
    if table_match:
        water_cons = _parse_amount_to_int(table_match.group(2))
        ww_recol   = _parse_amount_to_int(table_match.group(3))
        ww_treat   = _parse_amount_to_int(table_match.group(4))
        interes    = _parse_amount_to_int(table_match.group(6))
        descuento  = _parse_amount_to_int(table_match.group(8))

        attrs["water_consumption"]      = water_cons
        attrs["wastewater_recolection"] = ww_recol
        attrs["wastewater_treatment"]   = ww_treat
        attrs["other_charges"]          = interes + descuento
    else:
        nopunta_match = _WATER_AA_PDF_BILLING_TABLE_NOPUNTA_RE.search(text)
        if nopunta_match:
            # Column-serialised format with NO PUNTA / PUNTA split.
            # fixed_charge is already set from the tariff section; overwrite it
            # with the value from the billing table (same figure, more direct).
            attrs["fixed_charge"] = _parse_amount_to_int(nopunta_match.group(5))
            attrs["water_consumption_non_peak_m3"] = _parse_consumption_to_float(nopunta_match.group(1))
            attrs["water_consumption_non_peak"]    = _parse_amount_to_int(nopunta_match.group(6))
            attrs["water_consumption_peak_m3"]     = _parse_consumption_to_float(nopunta_match.group(2))
            attrs["water_consumption_peak"]        = _parse_amount_to_int(nopunta_match.group(7))
            attrs["water_consumption"]             = (
                attrs["water_consumption_non_peak"] + attrs["water_consumption_peak"]
            )
            attrs["wastewater_recolection"] = _parse_amount_to_int(nopunta_match.group(8))
            attrs["wastewater_treatment"]   = _parse_amount_to_int(nopunta_match.group(9))
            attrs["other_charges"]          = _parse_amount_to_int(nopunta_match.group(12))
        else:
            # Line-by-line fallback: each row on its own line with m³ and CLP.
            no_punta_m = _WATER_AA_PDF_WATER_NO_PUNTA_RE.search(text)
            punta_m = _WATER_AA_PDF_WATER_PUNTA_RE.search(text)
            if no_punta_m:
                attrs["water_consumption_non_peak_m3"] = _parse_consumption_to_float(no_punta_m.group(1))
                attrs["water_consumption_non_peak"] = _parse_amount_to_int(no_punta_m.group(2))
            if punta_m:
                attrs["water_consumption_peak_m3"] = _parse_consumption_to_float(punta_m.group(1))
                attrs["water_consumption_peak"] = _parse_amount_to_int(punta_m.group(2))
            if no_punta_m and punta_m:
                attrs["water_consumption"] = (
                    attrs["water_consumption_non_peak"] + attrs["water_consumption_peak"]
                )

            reco_m = _WATER_AA_PDF_WW_RECOLECTION_RE.search(text)
            if reco_m:
                attrs["wastewater_recolection"] = _parse_amount_to_int(reco_m.group(1))

            treat_m = _WATER_AA_PDF_WW_TREATMENT_RE.search(text)
            if treat_m:
                attrs["wastewater_treatment"] = _parse_amount_to_int(treat_m.group(1))

            # "Other Charges" is driven by "Descuento Ley Redondeo";
            # include "Interés Deuda" when present for backwards compatibility.
            descuento_m = _WATER_AA_PDF_ROUNDING_DISCOUNT_RE.search(text)
            interes_m = _WATER_AA_PDF_INTEREST_RE.search(text)
            if descuento_m or interes_m:
                descuento = _parse_amount_to_int(descuento_m.group(1)) if descuento_m else 0
                interes = _parse_amount_to_int(interes_m.group(1)) if interes_m else 0
                attrs["other_charges"] = descuento + interes

    # Formula-derived attributes (initial computation; also recomputed by
    # _recompute_water_derived_attrs on set_value overrides).
    no_punta_m3 = attrs.get("water_consumption_non_peak_m3")
    no_punta_amt = attrs.get("water_consumption_non_peak")
    if no_punta_m3 and no_punta_amt is not None:
        attrs["cost_per_unit_non_peak"] = round(no_punta_amt / no_punta_m3, 2)
        _confidence["cost_per_unit_non_peak"] = CONF_SCORE_DERIVED

    punta_m3 = attrs.get("water_consumption_peak_m3")
    punta_amt = attrs.get("water_consumption_peak")
    if punta_m3 and punta_amt is not None:
        attrs["cost_per_unit_peak"] = round(punta_amt / punta_m3, 2)
        _confidence["cost_per_unit_peak"] = CONF_SCORE_DERIVED

    consumption = attrs.get("consumption")
    if consumption:
        wr = attrs.get("wastewater_recolection")
        if wr is not None:
            attrs["cubic_meter_collection"] = round(wr / consumption, 2)
            _confidence["cubic_meter_collection"] = CONF_SCORE_DERIVED

        wt = attrs.get("wastewater_treatment")
        if wt is not None:
            attrs["cubic_meter_treatment"] = round(wt / consumption, 2)
            _confidence["cubic_meter_treatment"] = CONF_SCORE_DERIVED

    wc2 = attrs.get("water_consumption")
    if wc2 is None:
        np_amt = attrs.get("water_consumption_non_peak")
        p_amt = attrs.get("water_consumption_peak")
        if np_amt is not None and p_amt is not None:
            wc2 = np_amt + p_amt
            attrs["water_consumption"] = wc2
            _confidence["water_consumption"] = CONF_SCORE_DERIVED
    wr2    = attrs.get("wastewater_recolection")
    wt2    = attrs.get("wastewater_treatment")
    fixed2 = attrs.get("fixed_charge")
    if wc2 is not None and wr2 is not None and wt2 is not None and fixed2 is not None:
        subtotal = wc2 + wr2 + wt2 + fixed2
        attrs["subtotal"] = subtotal
        _confidence["subtotal"] = CONF_SCORE_DERIVED
        other = attrs.get("other_charges")
        if other is not None:
            attrs["total_amount"] = subtotal + other
            _confidence["total_amount"] = CONF_SCORE_DERIVED

    if _confidence:
        attrs.setdefault("_confidence", {}).update(_confidence)

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
#   2. OCR.space cloud API  – requires an API key; provides the hot-water table
#                             (Agua Caliente) that lives only in the image.

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
# Fallback: any amount whose formatted representation is ≥ 9 characters, which
# corresponds to CLP amounts ≥ $1,000,000 ("1.000.000").  The previous pattern
# required the amount to start with "1" (i.e., ≥ $10,000,000), which excluded
# smaller buildings whose total common expense is in the low millions.
_GC_BUILDING_TOTAL_RE = re.compile(
    r"(?:gasto\s+com[uú]n|outo\s+c:om[oó]n|prorratu?r)[^\n]*\n[^\n]*\$\s*([\d.]+)"
    r"|\$\s*(\d[\d.]{8,})",            # fallback: formatted amount ≥ 9 chars (≥ $1,000,000)
    re.IGNORECASE,
)

# Gastos comunes apartment portion (alícuota %) → amount.
# Text shows: "O 95110 % " on one line, "$133.946 " on the next.
# The alícuota is always a sub-1% fraction rendered as "0,XXXXX %" where the
# leading "0," is font-garbled to "O " by pdfminer.  The fractional digits are
# 4–6 characters (e.g. "95110", "32500", "08750").  The previous pattern
# hardcoded the leading digit as "9", restricting matches to alícuotas ≥ 0.9%.
# Changing \d{4,6} accepts any alícuota (e.g. 0.3xxxx%, 0.08xxx%).
_GC_AMOUNT_RE = re.compile(
    r"[O0]\s*\d{4,6}\s*%\s*[\s\n]*\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# Fondos provision percentage (e.g. "FONDOS 5% DEL GASTO MENSUAL" → 5).
# Only the integer part is captured because the percentage is always a whole
# number (5, 10, …); the "00" suffix from pdfminer garbling is discarded.
_GC_FONDOS_PCT_RE = re.compile(
    r"fondos\s+(\d+)\s*%",
    re.IGNORECASE,
)

# Fondos provision amount: the percentage line precedes the $ amount.
# pdfminer renders "5,0 %" as "500 %" (comma becomes 0, no space before %).
# General garbling rule: "X,0 %" → "X00 %" where X is the integer percentage.
# This works for any whole-number fondos percentage (5%, 10%, 7%, …):
#   5%  → "500 %" or "5,0 %"
#   10% → "1000 %" or "10,0 %"
# Pattern: 1–2 integer digits, then the separator char (0 or ,), then "0 %".
# The previous pattern hardcoded the leading digit as "5" (fondos = 5% only).
_GC_FONDOS_AMOUNT_RE = re.compile(
    r"\d{1,2}[0,]\s*0\s*%\s*[\s\n]*\$\s*([\d.,]+)",
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
# OCR patterns (applied to OCR output)
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

# OCR building name: the OCR engine renders "Edificio Jose Miguel Pagar Hasta:"
# or "Edificio Jose Miguel Fecha Emisión:" on a single row-grouped line.
# Capture the words between "Edificio" and "Pagar/Fecha".
_GC_OCR_BUILDING_NAME_RE = re.compile(
    r"edificio\s+([\w\s]{2,50}?)\s+(?:pagar|fecha)\s",
    re.IGNORECASE,
)

# OCR Cargo Fijo: the fixed-charge row.  OCR may omit the space between
# words ("CargoFijo"). Use \s* (zero-or-more spaces) to handle both.
# Use a tight window (≤ 30 chars) to avoid capturing the next-line total.
_GC_OCR_CARGO_FIJO_RE = re.compile(
    r"cargo\s*fijo[\s|]{0,30}\$?\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR Emission date: OCR reads the label accurately as "Fecha Emisión:"
# (pdfminer produces a garbled form such as "Fecha Eml11ón:").
# The date may appear on the same OCR line as the label or immediately after.
_GC_OCR_EMISSION_DATE_RE = re.compile(
    r"fecha\s+emis[^\n:]{0,15}[:\s]+(\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)

# OCR Due date: "Pagar Hasta: 01-03-2026"
_GC_OCR_DUE_DATE_RE = re.compile(
    r"pagar\s+hasta[:\s]+(\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)

# OCR Alícuota: reads "0,95110 %" or "0.95110 %" without font garbling.
# Group 1 is the fractional digit string only (same convention as
# _GC_ALICUOTA_RE), so the "0." prefix construction can be reused.
_GC_OCR_ALICUOTA_RE = re.compile(
    r"(?:al[íi]cuota\s+total\s*)?0[,.](\d{4,6})\s*%",
    re.IGNORECASE,
)

# OCR GC amount: the apartment GC portion follows the alícuota percentage on
# the same line or the next ("0,95110 %  $133.946").  Up to 20 whitespace /
# pipe characters are allowed between the percent sign and the dollar sign
# to handle both inline and multi-line OCR layouts.
_GC_OCR_GC_AMOUNT_RE = re.compile(
    r"0[,.]\d{4,6}\s*%[\s|]{0,20}\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR Fondos provision amount: "FONDOS 5% DEL GASTO MENSUAL … $6.697"
# The label and its dollar amount may be separated by up to 60 characters
# (description text + whitespace).
_GC_OCR_FONDOS_AMOUNT_RE = re.compile(
    r"fondos\s+\d+\s*%[\s\S]{0,60}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR Subtotal departamento (GC + fondos provision subtotal)
_GC_OCR_SUBTOTAL_DEPTO_RE = re.compile(
    r"subtotal\s+departamento[\s|]{0,30}\$?\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR Grand total: "Total del mes  $171.266" or "Total a pagar  $171.266"
_GC_OCR_TOTAL_AMOUNT_RE = re.compile(
    r"total\s+(?:del\s+mes|a\s+pagar)[\s|]{0,20}\$?\s*([\d.,]+)",
    re.IGNORECASE,
)

# OCR Last-payment section: labels are readable ("Último" not garbled).
# [uú] with IGNORECASE covers both "ultimo" (garbled) and "Último" (clean).
_GC_OCR_LAST_PAYMENT_DATE_RE = re.compile(
    r"fecha\s+[uú]ltim[ao]\s+pago[\s\S]{0,100}?(\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)
_GC_OCR_LAST_PAYMENT_AMOUNT_RE = re.compile(
    r"monto\s+[uú]ltim[ao]\s+pago[\s\S]{0,100}?\$\s*([\d.,]+)",
    re.IGNORECASE,
)
_GC_OCR_LAST_PAYMENT_FOLIO_RE = re.compile(
    r"folio\s+[uú]ltim[\s\S]{0,100}?(\d{5,6})",
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
# from a 72-DPI base.
_OCR_ZOOM_FACTOR: int = 3

# Upscale factor applied to cropped sections sent to the OCR API (pass 2).
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

_GC_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "services_templates"
    / "common_expenses"
    / "edificio_jose_miguel.md"
)

_GC_TEMPLATE_ANCHOR_TOKENS: dict[str, tuple[str, ...]] = {
    "emission_date": ("fecha", "emision"),
    "due_date": ("pagar", "hasta"),
    "owner_name": ("copropietario",),
    "alicuota": ("alicuota", "total"),
    "building_total_expense": ("gasto", "comun", "prorratear"),
    "concepto": ("concepto",),
    "fondos_amount": ("provision", "fondos"),
    "subtotal_departamento": ("subtotal", "departamento"),
    "hot_water_prev": ("lectura", "anterior"),
    "hot_water_curr": ("lectura", "actual"),
    "hot_water_consumption": ("consumos",),
    "hot_water_cost": ("valor",),
    "hot_water_amount": ("subtotal", "consumo"),
    "cargo_fijo": ("cargo", "fijo"),
    "subtotal_recargos": ("subtotal", "recargos"),
    "total_amount": ("total", "mes"),
    "last_payment_date": ("fecha", "ultimo", "pago"),
    "last_payment_amount": ("monto", "ultimo", "pago"),
    "last_payment_folio": ("folio", "ultimo", "pago"),
}

_GC_TEMPLATE_ANCHOR_OUTPUT_KEYS: dict[str, tuple[str, ...]] = {
    "emission_date": ("emission_date",),
    "due_date": ("due_date",),
    "owner_name": ("owner_name",),
    "alicuota": ("alicuota",),
    "building_total_expense": ("building_total_expense",),
    "concepto": ("gastos_comunes_amount",),
    "fondos_amount": ("fondos_amount",),
    "subtotal_departamento": ("subtotal_departamento",),
    "hot_water_prev": ("hot_water_reading_prev",),
    "hot_water_curr": ("hot_water_reading_curr",),
    "hot_water_consumption": ("hot_water_consumption",),
    "hot_water_cost": ("hot_water_cost_per_m3",),
    "hot_water_amount": ("hot_water_amount", "subtotal_consumo"),
    "cargo_fijo": ("cargo_fijo",),
    "subtotal_recargos": ("subtotal_recargos",),
    "total_amount": ("total_amount",),
    "last_payment_date": ("last_payment_date",),
    "last_payment_amount": ("last_payment_amount",),
    "last_payment_folio": ("last_payment_folio",),
}

# Thresholds used to decide whether template/JSON drift is significant enough
# to notify users. They are intentionally conservative: at least ~1/3 of
# anchors must be missing (or missing values) and the absolute floor is 4 keys,
# which avoids alerts for small OCR glitches while still surfacing layout drift.
_GC_TEMPLATE_MISMATCH_MIN_MISSING_ANCHORS = 4  # absolute floor to ignore small OCR noise
_GC_TEMPLATE_MISMATCH_MIN_MISSING_RATIO = 0.35  # approximately one third of anchors missing
_GC_TEMPLATE_MISMATCH_MIN_VALUE_GAPS = 4  # absolute floor to avoid one-off extraction misses
_GC_TEMPLATE_MISMATCH_MIN_VALUE_GAP_RATIO = 0.35  # approximately one third without values
_GC_TEMPLATE_MISMATCH_MIN_UNEXPECTED_LINES = 1
_GC_TEMPLATE_MISMATCH_EXCERPT_LINES = 10
_GC_TEMPLATE_PLACEHOLDER_TOKENS = frozenset(
    {
        "dd",
        "mm",
        "aaaa",
        "xx",
        "xxx",
        "xxxx",
        "xxxxxxxxx",
        "referencial",
        "nombre",
        "apellido",
        "mes",
    }
)
_GC_OPTIONAL_JSON_IGNORE_TOKEN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("paga", "tu", "gasto", "comun"),
    ("ingresa", "a"),
    ("codigo", "cliente"),
    ("pagos", "kastor"),
)

_GC_DATE_RE = re.compile(r"(\d{2}[/-]\d{2}[/-]\d{4})")
_GC_AMOUNT_CAPTURE_RE = re.compile(
    r"\$?\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)"
)
_GC_AMOUNT_WITH_DOLLAR_CAPTURE_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)"
)
_GC_METER_VALUE_RE = re.compile(r"(\d{1,3}[,.]\d{6})")
_OCR_MIN_DIMENSION = 1.0
_OCR_FALLBACK_CHAR_WIDTH = 8.0
_OCR_ROW_GROUPING_FACTOR = 0.9
_OCR_HORIZONTAL_PROXIMITY_TOLERANCE = 25.0
_GC_ANCHOR_SCORE_TOKEN_WEIGHT = 10.0
_GC_ANCHOR_SCORE_COMPLETE_BONUS = 10.0
_GC_ROW_LINES_SCORE_DIVISOR = 10.0


def _normalize_gc_anchor_text(value: str) -> str:
    """Return *value* normalized for anchor matching."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _safe_ocr_float(value: Any) -> float:
    """Return *value* converted to float, defaulting to 0.0."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _gc_anchor_score(token_hits: int, total_tokens: int, similarity: float = 0.0) -> float:
    """Return a semantic score for an OCR/template anchor match."""
    score = token_hits * _GC_ANCHOR_SCORE_TOKEN_WEIGHT
    if token_hits == total_tokens:
        score += _GC_ANCHOR_SCORE_COMPLETE_BONUS
    return score + similarity


def _build_gc_ocr_pages(
    ocr_raw_results: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Return OCR overlay pages grouped into visual rows sorted by proximity."""
    pages: list[list[dict[str, Any]]] = []

    for parsed_idx, parsed in enumerate(ocr_raw_results):
        overlay = parsed.get("Overlay")
        if not isinstance(overlay, dict):
            continue
        overlay_lines = overlay.get("Lines")
        if not isinstance(overlay_lines, list):
            continue

        raw_lines: list[dict[str, Any]] = []
        for line_idx, line in enumerate(overlay_lines):
            if not isinstance(line, dict):
                continue
            text = str(line.get("LineText", "")).strip()
            if not text:
                continue

            words_raw = line.get("Words")
            words: list[dict[str, Any]] = []
            if isinstance(words_raw, list):
                for word in words_raw:
                    if not isinstance(word, dict):
                        continue
                    word_text = str(word.get("WordText", "")).strip()
                    if not word_text:
                        continue
                    left = _safe_ocr_float(word.get("Left"))
                    top = _safe_ocr_float(word.get("Top"))
                    width = _safe_ocr_float(word.get("Width"))
                    height = _safe_ocr_float(word.get("Height")) or _OCR_MIN_DIMENSION
                    words.append(
                        {
                            "text": word_text,
                            "left": left,
                            "top": top,
                            "right": left + max(width, _OCR_MIN_DIMENSION),
                            "bottom": top + height,
                        }
                    )

            if words:
                left = min(word["left"] for word in words)
                top = min(word["top"] for word in words)
                right = max(word["right"] for word in words)
                bottom = max(word["bottom"] for word in words)
            else:
                left = _safe_ocr_float(line.get("MinLeft"))
                top = _safe_ocr_float(line.get("MinTop"))
                height = _safe_ocr_float(line.get("MaxHeight")) or _OCR_MIN_DIMENSION
                width = _safe_ocr_float(line.get("Width")) or max(
                    len(text) * _OCR_FALLBACK_CHAR_WIDTH, _OCR_MIN_DIMENSION
                )
                right = left + width
                bottom = top + height

            raw_lines.append(
                {
                    "page_idx": parsed_idx,
                    "line_idx": line_idx,
                    "text": text,
                    "norm": _normalize_gc_anchor_text(text),
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                }
            )

        raw_lines.sort(key=lambda item: (item["top"], item["left"]))
        if not raw_lines:
            continue

        rows: list[dict[str, Any]] = []
        for line in raw_lines:
            center_y = (line["top"] + line["bottom"]) / 2.0
            line_height = max(line["bottom"] - line["top"], _OCR_MIN_DIMENSION)
            if rows:
                last_row = rows[-1]
                row_height = max(
                    last_row["bottom"] - last_row["top"], _OCR_MIN_DIMENSION
                )
                if abs(center_y - last_row["center_y"]) <= max(
                    line_height, row_height
                ) * _OCR_ROW_GROUPING_FACTOR:
                    last_row["lines"].append(line)
                    last_row["top"] = min(last_row["top"], line["top"])
                    last_row["bottom"] = max(last_row["bottom"], line["bottom"])
                    last_row["left"] = min(last_row["left"], line["left"])
                    last_row["right"] = max(last_row["right"], line["right"])
                    last_row["center_y"] = (
                        last_row["top"] + last_row["bottom"]
                    ) / 2.0
                    continue

            rows.append(
                {
                    "page_idx": parsed_idx,
                    "lines": [line],
                    "top": line["top"],
                    "bottom": line["bottom"],
                    "left": line["left"],
                    "right": line["right"],
                    "center_y": center_y,
                }
            )

        for row in rows:
            row["lines"].sort(key=lambda item: item["left"])
            row["text"] = " | ".join(line["text"] for line in row["lines"])
            row["norm"] = _normalize_gc_anchor_text(row["text"])
        pages.append(rows)

    return pages


def _find_gc_anchor_line(
    pages: list[list[dict[str, Any]]],
    anchor_key: str,
    template_lines: list[str],
) -> dict[str, Any] | None:
    """Return the best OCR line candidate for *anchor_key*."""
    tokens = _GC_TEMPLATE_ANCHOR_TOKENS.get(anchor_key, ())
    if not tokens:
        return None

    anchor_ref = _find_gc_template_anchor(template_lines, anchor_key) or ""
    best_match: dict[str, Any] | None = None
    best_score = 0.0

    for page_idx, rows in enumerate(pages):
        for row_idx, row in enumerate(rows):
            for line_idx, line in enumerate(row["lines"]):
                token_hits = sum(token in line["norm"] for token in tokens)
                if token_hits == 0:
                    continue
                if len(tokens) > 1 and token_hits < len(tokens) - 1:
                    continue
                similarity = (
                    difflib.SequenceMatcher(None, line["norm"], anchor_ref).ratio()
                    if anchor_ref
                    else 0.0
                )
                score = _gc_anchor_score(token_hits, len(tokens), similarity)
                if score > best_score:
                    best_score = score
                    best_match = {
                        "page_idx": page_idx,
                        "row_idx": row_idx,
                        "line_idx": line_idx,
                        "line": line,
                    }

    return best_match


def _find_gc_row_by_tokens(
    pages: list[list[dict[str, Any]]], tokens: tuple[str, ...]
) -> dict[str, Any] | None:
    """Return the best OCR row whose text matches *tokens* semantically."""
    best_match: dict[str, Any] | None = None
    best_score = 0.0

    for page_idx, rows in enumerate(pages):
        for row_idx, row in enumerate(rows):
            token_hits = sum(token in row["norm"] for token in tokens)
            if token_hits == 0:
                continue
            if len(tokens) > 1 and token_hits < len(tokens) - 1:
                continue
            score = _gc_anchor_score(token_hits, len(tokens)) + (
                len(row["lines"]) / _GC_ROW_LINES_SCORE_DIVISOR
            )
            if score > best_score:
                best_score = score
                best_match = {
                    "page_idx": page_idx,
                    "row_idx": row_idx,
                    "row": row,
                }

    return best_match


def _extract_regex_near_gc_anchor(
    pages: list[list[dict[str, Any]]],
    anchor_match: dict[str, Any] | None,
    pattern: re.Pattern[str],
    *,
    max_row_gap: int = 2,
) -> str | None:
    """Extract a regex value using semantic proximity to an OCR anchor line."""
    if anchor_match is None:
        return None

    rows = pages[anchor_match["page_idx"]]
    row_idx = anchor_match["row_idx"]
    line_idx = anchor_match["line_idx"]
    anchor_line = anchor_match["line"]
    candidate_texts: list[str] = []

    current_row = rows[row_idx]
    for idx, line in enumerate(current_row["lines"]):
        if idx == line_idx:
            continue
        if line["left"] >= anchor_line["left"] - _OCR_HORIZONTAL_PROXIMITY_TOLERANCE:
            candidate_texts.append(line["text"])
    candidate_texts.append(anchor_line["text"])
    candidate_texts.append(current_row["text"])

    for row_gap in range(1, max_row_gap + 1):
        if row_idx + row_gap < len(rows):
            candidate_texts.append(rows[row_idx + row_gap]["text"])
        if row_idx - row_gap >= 0:
            candidate_texts.append(rows[row_idx - row_gap]["text"])

    seen: set[str] = set()
    for text in candidate_texts:
        if text in seen:
            continue
        seen.add(text)
        match = pattern.search(text)
        if match:
            return (match.group(1) if match.lastindex else match.group(0)).strip()

    return None


def _load_gc_template_lines() -> list[str]:
    """Load normalized reference lines from the common-expenses markdown template."""
    try:
        template_text = _GC_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as err:
        _LOGGER.debug("Could not read common-expenses template '%s': %s", _GC_TEMPLATE_PATH, err)
        return []

    lines: list[str] = []
    for raw_line in template_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [line]
        if "|" in line:
            parts = [part.strip() for part in line.split("|") if part.strip()]
        for part in parts:
            normalized = _normalize_gc_anchor_text(part)
            if not normalized:
                continue
            if normalized.replace("-", "") == "":
                continue
            lines.append(normalized)
    return lines


def _find_gc_template_anchor(
    template_lines: list[str],
    anchor_key: str,
) -> str | None:
    """Return the template anchor line that best matches *anchor_key*."""
    tokens = _GC_TEMPLATE_ANCHOR_TOKENS.get(anchor_key)
    if not tokens:
        return None
    for line in template_lines:
        if all(token in line for token in tokens):
            return line
    return None


def _gc_template_line_skeleton(norm_line: str) -> str:
    """Return a stable template-comparison skeleton for *norm_line*."""
    tokens: list[str] = []
    for token in norm_line.split():
        if token in _GC_TEMPLATE_PLACEHOLDER_TOKENS:
            continue
        if token in _MONTH_NAME_TO_NUM:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def _gc_line_matches_template(
    norm_line: str,
    template_line_skeletons: list[str],
) -> bool:
    """Return whether an OCR line is represented by the markdown template."""
    line_skeleton = _gc_template_line_skeleton(norm_line)
    if not line_skeleton:
        return False
    for template_skeleton in template_line_skeletons:
        if (
            line_skeleton == template_skeleton
            or line_skeleton.startswith(template_skeleton)
            or template_skeleton.startswith(line_skeleton)
        ):
            return True
    return False


def _gc_is_ignorable_optional_json_line(
    raw_line: str,
    norm_line: str,
    prev_norm_line: str,
) -> bool:
    """Return whether an unexpected OCR line belongs to a known optional block."""
    compact_norm = re.sub(r"\s+", " ", norm_line).strip()
    for token_group in _GC_OPTIONAL_JSON_IGNORE_TOKEN_GROUPS:
        if all(token in compact_norm for token in token_group):
            return True
    if "fono" in compact_norm:
        return True
    if "fono" in prev_norm_line and re.fullmatch(r"[+\d().\-\s]{6,}", raw_line.strip()):
        return True
    return False


def _gc_is_potential_unexpected_json_line(raw_line: str, norm_line: str) -> bool:
    """Return whether line shape suggests a structural OCR JSON addition."""
    if ":" in raw_line:
        return True
    return "http" in norm_line or "www" in norm_line


def _extract_common_expenses_from_ocr_json(
    ocr_raw_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract attributes from OCR.space JSON using structure and proximity."""
    pages = _build_gc_ocr_pages(ocr_raw_results)
    if not pages:
        return {}

    best_rows = max(pages, key=len)
    best_lines = [
        line["text"]
        for row in best_rows
        for line in row["lines"]
        if line["text"].strip()
    ]
    best_text = "\n".join(row["text"] for row in best_rows if row["text"].strip())

    template_lines = _load_gc_template_lines()
    if not template_lines:
        return {}
    template_line_skeletons = sorted(
        {
            skeleton
            for line in template_lines
            if (skeleton := _gc_template_line_skeleton(line))
        }
    )
    attrs: dict[str, Any] = {}
    anchor_matches: dict[str, dict[str, Any] | None] = {
        key: _find_gc_anchor_line(pages, key, template_lines)
        for key in _GC_TEMPLATE_ANCHOR_TOKENS
    }

    billing_year = ""
    period_m = _GC_BILLING_PERIOD_RE.search(best_text)
    if period_m:
        month_name = period_m.group(1).lower()
        billing_year = period_m.group(2)
        attrs["billing_period_month"] = period_m.group(1).capitalize()
        attrs["billing_period_year"] = billing_year
        month_num = _MONTH_NAME_TO_NUM.get(month_name[:3], 0)
        if month_num:
            attrs["billing_period_start"] = f"01-{month_num:02d}-{billing_year}"
            last_day = calendar.monthrange(int(billing_year), month_num)[1]
            attrs["billing_period_end"] = f"{last_day:02d}-{month_num:02d}-{billing_year}"

    emission = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("emission_date"), _GC_DATE_RE, max_row_gap=2
    )
    if emission:
        emission_norm = emission.replace("/", "-")
        attrs["emission_date"] = emission_norm
        year_match = re.search(r"(\d{4})$", emission_norm)
        if year_match:
            billing_year = year_match.group(1)

    due = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("due_date"), _GC_DATE_RE, max_row_gap=2
    )
    if due:
        due_norm = due.replace("/", "-")
        attrs["due_date"] = _gc_fix_year(due_norm, billing_year) if billing_year else due_norm

    for line in best_lines:
        if _normalize_gc_anchor_text(line).startswith("edificio "):
            attrs["building_name"] = line.strip()
            break

    for idx, row in enumerate(best_rows):
        rut_match = _GC_RUT_RE.search(row["text"])
        if rut_match:
            attrs["building_rut"] = rut_match.group(1)
            for nxt in range(idx + 1, min(len(best_rows), idx + 4)):
                next_text = best_rows[nxt]["text"].strip()
                if next_text and "fono" not in _normalize_gc_anchor_text(next_text):
                    attrs["address"] = next_text
                    break
            break

    for line in best_lines:
        apt_match = _GC_APARTMENT_RE.search(line)
        if apt_match:
            attrs["apartment"] = apt_match.group(1)
            break

    owner_raw = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("owner_name"), _GC_OWNER_RE, max_row_gap=1
    )
    if owner_raw:
        attrs["owner_name"] = owner_raw

    ali_raw = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("alicuota"),
        re.compile(r"(?:^|[^0-9])0[,.](\d{4,6})\s*%"),
        max_row_gap=1,
    )
    if ali_raw:
        try:
            attrs["alicuota"] = round(float("0." + ali_raw), 4)
        except ValueError:
            pass

    bld_total_raw = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("building_total_expense"),
        _GC_AMOUNT_CAPTURE_RE,
        max_row_gap=1,
    )
    if bld_total_raw:
        attrs["building_total_expense"] = _parse_amount_to_int(bld_total_raw)

    gasto_row = _find_gc_row_by_tokens(pages, ("gasto", "comun"))
    if gasto_row is not None and gasto_row["row"]["lines"]:
        gasto_raw = _extract_regex_near_gc_anchor(
            pages,
            {
                "page_idx": gasto_row["page_idx"],
                "row_idx": gasto_row["row_idx"],
                "line_idx": 0,
                "line": gasto_row["row"]["lines"][0],
            },
            _GC_AMOUNT_CAPTURE_RE,
            max_row_gap=1,
        )
        if gasto_raw:
            attrs["gastos_comunes_amount"] = _parse_amount_to_int(gasto_raw)

    fondos_raw = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("fondos_amount"), _GC_AMOUNT_CAPTURE_RE, max_row_gap=1
    )
    if fondos_raw:
        attrs["fondos_amount"] = _parse_amount_to_int(fondos_raw)
    fondos_anchor = anchor_matches.get("fondos_amount")
    if fondos_anchor is not None:
        pct_match = re.search(r"fondos\s+(\d+)", fondos_anchor["line"]["norm"])
        if pct_match:
            attrs["fondos_pct"] = int(pct_match.group(1))

    sub_depto_raw = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("subtotal_departamento"),
        _GC_AMOUNT_CAPTURE_RE,
        max_row_gap=1,
    )
    if sub_depto_raw:
        attrs["subtotal_departamento"] = _parse_amount_to_int(sub_depto_raw)

    hot_water_row = _find_gc_row_by_tokens(pages, ("agua", "caliente"))
    if hot_water_row is not None and hot_water_row["row"].get("text"):
        row_text = hot_water_row["row"]["text"]
        meter_matches = _GC_METER_VALUE_RE.findall(row_text)
        if len(meter_matches) >= 2:
            attrs["hot_water_reading_prev"] = _parse_meter_reading(meter_matches[0])
            attrs["hot_water_reading_curr"] = _parse_meter_reading(meter_matches[1])
        if len(meter_matches) >= 3:
            attrs["hot_water_consumption"] = _parse_meter_reading(meter_matches[2])
        elif (
            attrs.get("hot_water_reading_prev") is not None
            and attrs.get("hot_water_reading_curr") is not None
        ):
            attrs["hot_water_consumption"] = round(
                attrs["hot_water_reading_curr"] - attrs["hot_water_reading_prev"], 6
            )
        if "hot_water_consumption" in attrs:
            attrs["hot_water_consumption_unit"] = "m³"
        cost_match = re.search(r"([\d.]+,\d{2}|[\d,]+\.\d{2})", row_text)
        if cost_match:
            attrs["hot_water_cost_per_m3"] = _parse_consumption_to_float(cost_match.group(1))
        amount_match = _GC_AMOUNT_CAPTURE_RE.search(row_text)
        if amount_match:
            attrs["hot_water_amount"] = _parse_amount_to_int(amount_match.group(1))

    hw_amount_raw = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("hot_water_amount"), _GC_AMOUNT_CAPTURE_RE, max_row_gap=1
    )
    if hw_amount_raw:
        subtotal_consumo = _parse_amount_to_int(hw_amount_raw)
        attrs["subtotal_consumo"] = subtotal_consumo
        attrs["hot_water_amount"] = subtotal_consumo

    cargo_raw = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("cargo_fijo"), _GC_AMOUNT_CAPTURE_RE, max_row_gap=1
    )
    if cargo_raw:
        attrs["cargo_fijo"] = _parse_amount_to_int(cargo_raw)

    recargos_raw = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("subtotal_recargos"),
        _GC_AMOUNT_CAPTURE_RE,
        max_row_gap=1,
    )
    if recargos_raw:
        attrs["subtotal_recargos"] = _parse_amount_to_int(recargos_raw)

    total_raw = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("total_amount"), _GC_AMOUNT_CAPTURE_RE, max_row_gap=1
    )
    if total_raw:
        attrs["total_amount"] = _parse_amount_to_int(total_raw)

    lp_date = _extract_regex_near_gc_anchor(
        pages, anchor_matches.get("last_payment_date"), _GC_DATE_RE, max_row_gap=1
    )
    if lp_date:
        lp_norm = lp_date.replace("/", "-")
        attrs["last_payment_date"] = _gc_fix_year(lp_norm, billing_year) if billing_year else lp_norm

    lp_amount_raw = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("last_payment_amount"),
        _GC_AMOUNT_CAPTURE_RE,
        max_row_gap=1,
    )
    if lp_amount_raw:
        attrs["last_payment_amount"] = _parse_amount_to_int(lp_amount_raw)

    lp_folio = _extract_regex_near_gc_anchor(
        pages,
        anchor_matches.get("last_payment_folio"),
        re.compile(r"(\d{5,6})"),
        max_row_gap=1,
    )
    if lp_folio:
        attrs["last_payment_folio"] = lp_folio

    total_anchor_count = len(_GC_TEMPLATE_ANCHOR_TOKENS)
    found_anchor_keys = sorted(
        [key for key, match in anchor_matches.items() if match is not None]
    )
    missing_anchor_keys = sorted(
        [key for key, match in anchor_matches.items() if match is None]
    )
    found_anchor_count = len(found_anchor_keys)
    missing_anchor_count = len(missing_anchor_keys)
    missing_anchor_ratio = (
        missing_anchor_count / total_anchor_count if total_anchor_count else 0.0
    )

    def _anchor_has_value(anchor_key: str) -> bool:
        output_keys = _GC_TEMPLATE_ANCHOR_OUTPUT_KEYS.get(anchor_key, ())
        return any(
            key in attrs and attrs[key] not in ("", None)
            for key in output_keys
        )

    value_gap_keys = sorted(
        [
            key
            for key in found_anchor_keys
            if _GC_TEMPLATE_ANCHOR_OUTPUT_KEYS.get(key) and not _anchor_has_value(key)
        ]
    )
    value_gap_count = len(value_gap_keys)
    value_gap_ratio = (
        value_gap_count / found_anchor_count if found_anchor_count else 0.0
    )
    unexpected_line_entries: list[tuple[int, str]] = []
    row_norms = [_normalize_gc_anchor_text(row["text"]) for row in best_rows]
    for idx, (row, norm_line) in enumerate(zip(best_rows, row_norms, strict=True)):
        raw_line = row["text"]
        if not norm_line:
            continue
        if not _gc_is_potential_unexpected_json_line(raw_line, norm_line):
            continue
        prev_norm_line = row_norms[idx - 1] if idx > 0 else ""
        if _gc_is_ignorable_optional_json_line(raw_line, norm_line, prev_norm_line):
            continue
        if _gc_line_matches_template(norm_line, template_line_skeletons):
            continue
        unexpected_line_entries.append((idx, raw_line.strip()[:200]))
    unexpected_json_lines = [line for _, line in unexpected_line_entries]
    unexpected_line_count = len(unexpected_json_lines)
    anchor_coverage_pct = round(
        (found_anchor_count / total_anchor_count) * 100.0, 1
    ) if total_anchor_count else 0.0

    significant_missing = (
        missing_anchor_count >= _GC_TEMPLATE_MISMATCH_MIN_MISSING_ANCHORS
        and missing_anchor_ratio >= _GC_TEMPLATE_MISMATCH_MIN_MISSING_RATIO
    )
    significant_value_gaps = (
        value_gap_count >= _GC_TEMPLATE_MISMATCH_MIN_VALUE_GAPS
        and value_gap_ratio >= _GC_TEMPLATE_MISMATCH_MIN_VALUE_GAP_RATIO
    )
    significant_unexpected_lines = (
        unexpected_line_count >= _GC_TEMPLATE_MISMATCH_MIN_UNEXPECTED_LINES
    )
    is_significant = (
        significant_missing or significant_value_gaps or significant_unexpected_lines
    )

    if is_significant:
        excerpt_index_set: set[int] = set()
        for key in value_gap_keys:
            anchor_match = anchor_matches.get(key)
            if anchor_match is None:
                continue
            for off in (0, 1):
                candidate = anchor_match["row_idx"] + off
                if 0 <= candidate < len(best_rows):
                    excerpt_index_set.add(candidate)
        for idx, _line in unexpected_line_entries:
            excerpt_index_set.add(idx)
        excerpt_indices = sorted(excerpt_index_set)
        if not excerpt_indices:
            excerpt_indices = list(
                range(min(len(best_rows), _GC_TEMPLATE_MISMATCH_EXCERPT_LINES))
            )
        excerpt_lines = [
            best_rows[idx]["text"].strip()[:200]
            for idx in excerpt_indices[:_GC_TEMPLATE_MISMATCH_EXCERPT_LINES]
            if best_rows[idx]["text"].strip()
        ]

        attrs["_gc_template_mismatch"] = {
            "is_significant": True,
            "anchor_coverage_pct": anchor_coverage_pct,
            "total_anchors": total_anchor_count,
            "found_anchors": found_anchor_count,
            "missing_anchor_keys": missing_anchor_keys,
            "value_gap_keys": value_gap_keys,
            "matched_anchor_keys": found_anchor_keys,
            "unexpected_json_lines": unexpected_json_lines,
            "ocr_json_overlay_excerpt": excerpt_lines,
        }

    return attrs


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


def _finalize_common_expenses_attrs(
    attrs: dict[str, Any], confidence: dict[str, float]
) -> dict[str, Any]:
    """Apply aliases, derivations, and confidence metadata in-place."""
    building = attrs.get("building_name", "")
    raw_addr = attrs.get("address", "")
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

    if "building_total_expense" in attrs:
        attrs["gross_common_expenses"] = attrs["building_total_expense"]
    if "alicuota" in attrs:
        attrs["gross_common_expenses_percentage"] = attrs["alicuota"]
    if "hot_water_reading_prev" in attrs:
        attrs["previous_measure"] = attrs["hot_water_reading_prev"]
    if "hot_water_reading_curr" in attrs:
        attrs["actual_measure"] = attrs["hot_water_reading_curr"]

    if "fondos_pct" in attrs:
        attrs["funds_provision_percentage"] = attrs["fondos_pct"]
        confidence["funds_provision_percentage"] = confidence.get(
            "fondos_pct", CONF_SCORE_OCR
        )
    if "fondos_amount" in attrs:
        attrs["funds_provision"] = attrs["fondos_amount"]
        confidence["funds_provision"] = confidence.get(
            "fondos_amount", CONF_SCORE_OCR
        )
    if "subtotal_departamento" in attrs:
        attrs["subtotal"] = attrs["subtotal_departamento"]
        confidence["subtotal"] = confidence.get(
            "subtotal_departamento", CONF_SCORE_OCR
        )
    if (
        ("cargo_fijo" not in attrs or attrs.get("cargo_fijo") is None)
        and "subtotal_recargos" in attrs
        and attrs.get("subtotal_recargos") is not None
    ):
        attrs["cargo_fijo"] = attrs["subtotal_recargos"]
        confidence["cargo_fijo"] = confidence.get(
            "subtotal_recargos", CONF_SCORE_OCR
        )
    if "cargo_fijo" in attrs:
        attrs["fixed_charge"] = attrs["cargo_fijo"]
        confidence["fixed_charge"] = confidence.get("cargo_fijo", CONF_SCORE_OCR)

    if "gastos_comunes_amount" not in attrs or attrs.get("gastos_comunes_amount") is None:
        sub_depto = attrs.get("subtotal_departamento")
        fondos = attrs.get("fondos_amount")
        if sub_depto is not None and fondos is not None:
            attrs["gastos_comunes_amount"] = sub_depto - fondos
            confidence["gastos_comunes_amount"] = CONF_SCORE_DERIVED

    if "subtotal_consumo" not in attrs or attrs.get("subtotal_consumo") is None:
        total = attrs.get("total_amount")
        sub_depto = attrs.get("subtotal_departamento")
        cargo = attrs.get("cargo_fijo")
        if (
            total is not None
            and sub_depto is not None
            and cargo is not None
            and total > sub_depto + cargo
        ):
            derived_consumo = total - sub_depto - cargo
            attrs["subtotal_consumo"] = derived_consumo
            confidence["subtotal_consumo"] = CONF_SCORE_DERIVED
            if "hot_water_amount" not in attrs or attrs.get("hot_water_amount") is None:
                attrs["hot_water_amount"] = derived_consumo
                confidence["hot_water_amount"] = CONF_SCORE_DERIVED
        elif total is not None and sub_depto is not None and cargo is None and total > sub_depto:
            derived_consumo = total - sub_depto
            attrs["subtotal_consumo"] = derived_consumo
            confidence["subtotal_consumo"] = CONF_SCORE_DERIVED
            if "hot_water_amount" not in attrs or attrs.get("hot_water_amount") is None:
                attrs["hot_water_amount"] = derived_consumo
                confidence["hot_water_amount"] = CONF_SCORE_DERIVED

    subtotal_val = (
        attrs["subtotal_departamento"]
        if "subtotal_departamento" in attrs
        else attrs.get("subtotal")
    )
    cargo_val = (
        attrs["cargo_fijo"] if "cargo_fijo" in attrs else attrs.get("fixed_charge")
    )
    if subtotal_val is not None and cargo_val is not None:
        attrs["gc_total"] = subtotal_val + cargo_val
        confidence["gc_total"] = CONF_SCORE_DERIVED
    elif subtotal_val is not None and cargo_val is None:
        attrs["gc_total"] = subtotal_val
        confidence["gc_total"] = CONF_SCORE_DERIVED
    elif (
        attrs.get("total_amount") is not None
        and attrs.get("subtotal_consumo") is not None
    ):
        attrs["gc_total"] = attrs["total_amount"] - attrs["subtotal_consumo"]
        confidence["gc_total"] = CONF_SCORE_DERIVED
    elif attrs.get("total_amount") is not None:
        attrs["gc_total"] = attrs["total_amount"]
        confidence["gc_total"] = CONF_SCORE_DERIVED

    if "building_total_expense" in attrs:
        confidence["gross_common_expenses"] = confidence.get(
            "building_total_expense", CONF_SCORE_OCR
        )
    if "alicuota" in attrs:
        confidence["gross_common_expenses_percentage"] = confidence.get(
            "alicuota", CONF_SCORE_OCR
        )
    if "hot_water_reading_prev" in attrs:
        confidence["previous_measure"] = confidence.get(
            "hot_water_reading_prev", CONF_SCORE_OCR
        )
    if "hot_water_reading_curr" in attrs:
        confidence["actual_measure"] = confidence.get(
            "hot_water_reading_curr", CONF_SCORE_OCR
        )

    if confidence:
        attrs["_confidence"] = confidence

    return attrs


def _try_ocr_pdf_via_ocrspace(
    pdf_path: str,
    api_key: str,
    json_dir: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Render the first page of *pdf_path* and OCR it via the OCR.space cloud API.

    Uses the free `OCR.space <https://ocr.space/OCRAPI>`_ REST API
    (``POST https://api.ocr.space/parse/image``).  Two passes are performed:

    * **Pass 1** — full rendered page (Spanish, engine 2).
    * **Pass 2** — agua-caliente crop (≈ 30–55 % from top, enlarged 2×,
      engine 2) for improved hot-water table recognition.

    The free public API key is ``"helloworld"``; users can obtain a higher-
    quota key by registering at https://ocr.space/OCRAPI.

    Args:
        pdf_path: Absolute path to the PDF file.
        api_key:  OCR.space API key (``"helloworld"`` for the free demo tier).
        json_dir: Directory where the OCR JSON snapshot is saved.  When empty
                  the snapshot is placed in a ``json/`` sibling next to
                  the PDF's parent directory.

    Returns:
        ``(ocr_text, parsed_results)`` where *ocr_text* is the combined plain
        text from both passes and *parsed_results* is the combined OCR.space
        ``ParsedResults`` list.
    """
    import base64
    import io
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError as exc:
        _LOGGER.warning(
            "OCR.space unavailable for '%s': missing library (%s). "
            "The integration requires 'pypdfium2' and 'Pillow' to render PDF pages.",
            pdf_path,
            exc,
        )
        return "", []

    _lanczos = getattr(Image, "Resampling", Image).LANCZOS
    _OCRSPACE_URL = "https://api.ocr.space/parse/image"  # noqa: N806

    def _png_b64(img: "Image.Image") -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _call_ocrspace(
        img: "Image.Image",
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
        """POST *img* to OCR.space and return text, ParsedResults and raw JSON."""
        payload = urllib.parse.urlencode(
            {
                "apikey": api_key,
                "base64Image": f"data:image/png;base64,{_png_b64(img)}",
                "language": "spa",
                "OCREngine": "2",
                "isOverlayRequired": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            _OCRSPACE_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = _json.loads(resp.read().decode("utf-8"))
        if data.get("IsErroredOnProcessing"):
            _LOGGER.debug(
                "OCR.space error for '%s': %s",
                pdf_path,
                data.get("ErrorMessage"),
            )
            return "", [], data
        results = data.get("ParsedResults") or []
        typed_results = [r for r in results if isinstance(r, dict)]
        return "\n".join(r.get("ParsedText", "") for r in typed_results), typed_results, data

    try:
        doc = pdfium.PdfDocument(pdf_path)
        page = doc[0]
        page_height = page.get_height()
        bitmap = page.render(scale=_OCR_ZOOM_FACTOR)
        doc.close()
        img_full = bitmap.to_pil()

        # Pass 1 — full page
        text_full, raw_full, full_resp = _call_ocrspace(img_full)

        # Pass 2 — agua caliente area crop (≈ 30–55 % from top), enlarged 2×
        img_width = img_full.width
        crop2_top = int(page_height * _OCR_CROP2_TOP_RATIO * _OCR_ZOOM_FACTOR)
        crop2_bot = int(page_height * _OCR_CROP2_BOTTOM_RATIO * _OCR_ZOOM_FACTOR)
        crop2 = img_full.crop((0, crop2_top, img_width, crop2_bot))
        crop2 = crop2.resize(
            (
                img_width * _OCR_CROP_RESIZE_FACTOR,
                (crop2_bot - crop2_top) * _OCR_CROP_RESIZE_FACTOR,
            ),
            _lanczos,
        )
        text_crop2, raw_crop2, crop_resp = _call_ocrspace(crop2)

        _store_ocrspace_json_snapshot(
            pdf_path,
            {
                "pdf_path": pdf_path,
                "ocr_provider": "ocr.space",
                "passes": {
                    "full_page": full_resp or {},
                    "agua_caliente_crop": crop_resp or {},
                },
                "parsed_results_count": len(raw_full) + len(raw_crop2),
                "has_ocr_text": bool(text_full.strip() or text_crop2.strip()),
            },
            json_dir=json_dir,
        )
        _LOGGER.info(
            "OCR.space JSON snapshot stored for '%s' (json_dir=%s, parsed_results=%d)",
            pdf_path,
            json_dir or "auto",
            len(raw_full) + len(raw_crop2),
        )

        return text_full + "\n" + text_crop2, [*raw_full, *raw_crop2]

    except urllib.error.URLError as exc:
        _LOGGER.debug(
            "OCR.space API unreachable for '%s': %s. "
            "Check your internet connection or verify the API key.",
            pdf_path,
            exc,
        )
        return "", []
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("OCR.space failed for '%s': %s", pdf_path, err)
        return "", []


def _try_ocr_pdf(
    pdf_path: str,
    ocrspace_api_key: str = "",
    json_dir: str = "",
) -> tuple[str, list]:
    """OCR the first page of *pdf_path* using the OCR.space cloud API.

    Calls ``POST https://api.ocr.space/parse/image`` with two passes:
    a full-page pass and a cropped Agua Caliente section pass.

    Args:
        pdf_path:         Absolute path to the PDF file.
        ocrspace_api_key: OCR.space API key (``"helloworld"`` for the free
                          demo tier; register at https://ocr.space/OCRAPI
                          for a higher-quota free key).
        json_dir:         Directory where the OCR JSON snapshot is saved.

    Returns:
        ``(ocr_text, parsed_results)`` where *ocr_text* is the extracted text
        and *parsed_results* is the OCR.space ``ParsedResults`` payload used by
        template-guided extraction; returns ``("", [])`` when no key is
        configured or the call fails.
    """
    global _ocr_available  # noqa: PLW0603

    if not ocrspace_api_key:
        # No key configured at all — OCR is unavailable.
        _ocr_available = False
        return "", []

    # A key IS configured: mark OCR as available regardless of whether this
    # specific PDF yields any text (transient API errors or PDFs without
    # hot-water content must not trigger the "key not configured" notification).
    _ocr_available = True
    space_text, space_raw = _try_ocr_pdf_via_ocrspace(
        pdf_path, ocrspace_api_key, json_dir=json_dir
    )
    if space_text:
        return space_text, space_raw
    _LOGGER.debug("OCR.space returned no text for '%s'", pdf_path)
    return "", []


def _extract_common_expenses_pdf_attributes(
    _text: str, pdf_path: str = "", ocrspace_api_key: str = "", json_dir: str = ""
) -> dict[str, Any]:
    """Extract Gastos Comunes and Agua Caliente attributes using OCR Tier 2 only."""
    attrs: dict[str, Any] = {}
    confidence: dict[str, float] = {}

    if not pdf_path:
        return {}

    ocr_text, ocr_raw = _try_ocr_pdf(pdf_path, ocrspace_api_key, json_dir=json_dir)
    if not ocr_raw and not ocr_text.strip():
        _LOGGER.info(
            "Common-expenses OCR-only extraction skipped for '%s': no Tier 2 data available",
            pdf_path,
        )
        return {}

    template_json_attrs = _extract_common_expenses_from_ocr_json(ocr_raw)
    for key, value in template_json_attrs.items():
        attrs[key] = value
        if not key.startswith("_"):
            confidence[key] = CONF_SCORE_OCR

    _LOGGER.debug(
        "Common-expenses OCR-only extraction populated %d non-metadata attributes from OCR JSON for '%s'",
        len([key for key in attrs if not key.startswith("_")]),
        pdf_path,
    )
    return _finalize_common_expenses_attrs(attrs, confidence)


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
    text: str,
    service_type: str,
    pdf_path: str = "",
    ocrspace_api_key: str = "",
    json_dir: str = "",
) -> dict[str, Any]:
    """Dispatch to the **PDF** extractor for *service_type* and return its results.

    Each service type has a dedicated PDF extractor whose patterns are tuned to
    the layout of that issuer's PDF bill — separate from the email extractor.
    Service types that do not yet have a PDF-specific extractor fall back to an
    empty dict (no attributes extracted from the PDF).

    Args:
        text:             Plain text extracted from the PDF via pdfminer.
        service_type:     One of the ``SERVICE_TYPE_*`` constants.
        pdf_path:         Optional absolute path to the PDF file.  Passed to the
                          common-expenses extractor to enable optional OCR.
        ocrspace_api_key: Optional OCR.space API key used for OCR extraction.
        json_dir:         Directory where the OCR JSON snapshot is saved.
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
        return _extract_common_expenses_pdf_attributes(
            text, pdf_path, ocrspace_api_key, json_dir=json_dir
        )
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


def extract_attributes_from_pdf(
    pdf_path: str,
    service_type: str = SERVICE_TYPE_UNKNOWN,
    ocrspace_api_key: str = "",
    json_dir: str = "",
) -> dict[str, Any]:
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
      ``consumption_unit``, ``fixed_charge``,
      ``water_consumption``, ``wastewater_recolection``, ``wastewater_treatment``,
      ``other_charges``; formula-derived: ``cost_per_unit``,
      ``cubic_meter_collection``, ``cubic_meter_treatment``,
      ``subtotal``, ``total_amount``
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
      caller can select the relevant fields.  The PDF uses a "sandwich" layout:
      a full-page JPEG image as background plus a partially complete embedded
      text layer created by a prior OCR pass (identifiable by the
      ``HiddenHorzOCR`` font).  pdfminer reads that embedded layer directly.
      Tier-1 (embedded text layer, no OCR needed): ``billing_period_month``,
      ``billing_period_year``, ``billing_period_start``, ``billing_period_end``,
      ``emission_date``, ``due_date``, ``building_name``, ``building_rut``,
      ``address``, ``apartment``, ``owner_name``, ``alicuota``,
      ``building_total_expense``, ``gastos_comunes_amount``, ``fondos_amount``,
      ``subtotal_departamento``, ``subtotal_recargos``, ``total_amount``,
      ``last_payment_date``, ``last_payment_amount``, ``last_payment_folio``.
      Tier-2 (OCR on JPEG — hot-water table absent from embedded text):
      uses the OCR.space cloud API (*ocrspace_api_key*).
      ``hot_water_reading_prev``, ``hot_water_reading_curr``,
      ``hot_water_consumption``, ``hot_water_consumption_unit``,
      ``hot_water_cost_per_m3``, ``hot_water_amount``, ``subtotal_consumo``.

    Args:
        pdf_path:         Absolute path to the downloaded PDF file.
        service_type:     One of the ``SERVICE_TYPE_*`` constants.
        ocrspace_api_key: Optional OCR.space API key (``"helloworld"`` for the
                          free demo tier; register at https://ocr.space/OCRAPI
                          for a higher-quota free key).
        json_dir:         Directory where the OCR JSON snapshot is saved.
                          Defaults to a ``json/`` sibling next to the PDF's
                          parent directory when not supplied.

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

    if not pdf_text and service_type not in (
        SERVICE_TYPE_COMMON_EXPENSES,
        SERVICE_TYPE_HOT_WATER,
    ):
        return {}

    _LOGGER.debug(
        "pdfminer extracted %d chars from '%s' (service_type=%s). Text layer:\n%s",
        len(pdf_text),
        pdf_path,
        service_type,
        pdf_text,
    )

    attrs: dict[str, Any] = {}
    try:
        # Cap text length for performance (PDFs can be large)
        if len(pdf_text) > 50000:
            pdf_text = pdf_text[:50000]

        # Apply the PDF-specific extractor for this service type.
        # Each service type has its own PDF extractor tuned to that issuer's
        # PDF layout (separate from the email extractor).
        pdf_attrs = _extract_pdf_type_specific_attributes(
            pdf_text, service_type, pdf_path, ocrspace_api_key, json_dir=json_dir
        )
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
