"""Calendar-month bill history independent of recorder retention."""
from __future__ import annotations

from datetime import date
import math
from typing import Any


def months_ending(today: date) -> list[str]:
    """Return twelve calendar months, including the current month."""
    end = today.year * 12 + today.month - 1
    return [f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(end - 11, end + 1)]


def record_bill(
    history: dict[str, dict[str, dict[str, Any]]],
    service_id: str,
    issued: date,
    amount: object,
    today: date,
) -> bool:
    """Upsert by issue date: polling and corrections never duplicate a bill."""
    if isinstance(amount, bool) or not isinstance(amount, (float, int)):
        return False
    if not math.isfinite(amount) or issued > today:
        return False
    month = issued.strftime("%Y-%m")
    months = months_ending(today)
    if month not in months:
        return False
    bills = history.setdefault(service_id, {})
    key = issued.isoformat()
    value = {"month": month, "amount": amount}
    changed = bills.get(key) != value
    bills[key] = value
    for old_key in list(bills):
        if bills[old_key]["month"] not in months:
            del bills[old_key]
            changed = True
    return changed


def monthly_totals(bills: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Sum distinct bills; an absent month is unknown, never zero."""
    result = []
    for month in months_ending(today):
        amounts = [bill["amount"] for bill in bills.values() if bill["month"] == month]
        result.append({"month": month, "amount": sum(amounts) if amounts else None})
    return result
