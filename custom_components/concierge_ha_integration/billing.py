"""Billing-cycle helpers for Concierge service accounts."""

import calendar
from datetime import date, datetime
import re
import unicodedata


_SPANISH_MONTHS = {
    "ene": 1,
    "enero": 1,
    "feb": 2,
    "febrero": 2,
    "mar": 3,
    "marzo": 3,
    "abr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "junio": 6,
    "jul": 7,
    "julio": 7,
    "ago": 8,
    "agosto": 8,
    "sep": 9,
    "sept": 9,
    "septiembre": 9,
    "oct": 10,
    "octubre": 10,
    "nov": 11,
    "noviembre": 11,
    "dic": 12,
    "diciembre": 12,
}


def parse_bill_date(value: object) -> date | None:
    """Parse a date or billing month extracted from a bill.

    Month-only values use the first day of the month.  Accepting surrounding
    text also lets callers use a meaningful PDF filename as a fallback.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", raw.lower())
        if unicodedata.category(char) != "Mn"
    )
    compact_match = re.fullmatch(
        r"(\d{1,2})-([a-z]+)-(\d{4})",
        normalized,
    )
    if compact_match:
        month = _SPANISH_MONTHS.get(compact_match.group(2).rstrip("."))
        if month is None:
            return None
        try:
            return date(
                int(compact_match.group(3)),
                month,
                int(compact_match.group(1)),
            )
        except ValueError:
            return None

    match = re.fullmatch(
        r"(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})",
        normalized,
    )
    if match:
        month = _SPANISH_MONTHS.get(match.group(2).rstrip("."))
        if month is None:
            return None
        try:
            return date(int(match.group(3)), month, int(match.group(1)))
        except ValueError:
            return None

    numeric_month = re.search(
        r"(?<!\d)(?:(?P<year>20\d{2})[-_/ .](?P<month>0?[1-9]|1[0-2])|"
        r"(?P<month_first>0?[1-9]|1[0-2])[-_/ .](?P<year_last>20\d{2}))(?!\d)",
        normalized,
    )
    if numeric_month:
        year = int(numeric_month.group("year") or numeric_month.group("year_last"))
        month = int(
            numeric_month.group("month") or numeric_month.group("month_first")
        )
        return date(year, month, 1)

    named_month = re.search(
        r"(?<![a-z])([a-z]+)\.?\s+(?:de\s+)?(20\d{2})(?!\d)", normalized
    )
    if named_month:
        month = _SPANISH_MONTHS.get(named_month.group(1))
        if month is not None:
            return date(int(named_month.group(2)), month, 1)

    return None


def is_bill_overdue(
    last_updated: datetime, now: datetime, billing_interval_months: int
) -> bool:
    """Return whether a bill is older than its configured billing cycle."""
    month_index = last_updated.month - 1 + billing_interval_months
    year = last_updated.year + month_index // 12
    month = month_index % 12 + 1
    day = min(last_updated.day, calendar.monthrange(year, month)[1])
    billing_deadline = last_updated.replace(year=year, month=month, day=day)
    return billing_deadline < now
