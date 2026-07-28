"""Billing-cycle helpers for Concierge service accounts."""

import calendar
from datetime import datetime


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
