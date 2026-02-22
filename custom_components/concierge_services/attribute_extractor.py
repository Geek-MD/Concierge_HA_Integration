"""Attribute extraction module for Concierge Services.

Extracts billing attributes from email body using targeted field patterns.
Only the fields required before PDF analysis are extracted.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

_LOGGER = logging.getLogger(__name__)


# Patterns for billing period dates (start and end)
DATE_PATTERNS = [
    r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    r"([0-9]{1,2}\s+de\s+[a-zA-Z]+\s+de\s+[0-9]{4})",
    r"([A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})",
]


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, returning only visible text."""
    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self._parts.append(data)

        def get_text(self) -> str:
            return "\n".join(self._parts)

    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


# Label patterns that precede a total-amount value
_TOTAL_LABELS = re.compile(
    r"(?:total\s+a\s+pagar|monto\s+total|total\s+factura|importe\s+total"
    r"|total\s+cuenta|valor\s+a\s+pagar|total)[:\s]+",
    re.IGNORECASE,
)

# Label patterns that precede a customer/account number
_CUSTOMER_LABELS = re.compile(
    r"(?:n[úu]mero\s+de\s+(?:cuenta|cliente)|n[°º]\s*(?:cuenta|cliente)"
    r"|cuenta\s+n[°º]?|cliente\s+n[°º]?|customer\s+(?:number|no\.?)|account\s+(?:number|no\.?))"
    r"[:\s]+",
    re.IGNORECASE,
)

# Label patterns that precede an address value
_ADDRESS_LABELS = re.compile(
    r"(?:direcci[oó]n(?:\s+de\s+suministro)?|domicilio|address)[:\s]+",
    re.IGNORECASE,
)

# Currency amount pattern (reused by _extract_total_amount)
_AMOUNT_RE = re.compile(
    r"\$\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)"
    r"|([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?)\s*(?:CLP|USD|EUR|pesos?)",
    re.IGNORECASE,
)


def _extract_total_amount(text: str) -> str | None:
    """Return the total amount due found in *text*, or None."""
    for label_match in _TOTAL_LABELS.finditer(text):
        rest = text[label_match.end():]
        amount_match = _AMOUNT_RE.search(rest[:60])
        if amount_match:
            raw = (amount_match.group(1) or amount_match.group(2) or "").strip()
            if raw:
                return raw
    # Fallback: first currency amount in the whole text
    amount_match = _AMOUNT_RE.search(text)
    if amount_match:
        return (amount_match.group(1) or amount_match.group(2) or "").strip() or None
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


def extract_attributes_from_email_body(
    subject: str, body: str
) -> dict[str, Any]:
    """Extract billing attributes from email subject and body.

    Extracts exactly the fields needed before PDF analysis:
    folio, billing_period_start, billing_period_end,
    total_amount, customer_number, address.

    Args:
        subject: Email subject line (decoded).
        body:    Plain-text email body.

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
        r"n[úu]mero[:\s]*([0-9]{6,})",
        r"boleta[:\s]*([0-9]{6,})",
        r"factura[:\s]*([0-9]{6,})",
    ]
    
    for pattern in folio_patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            attrs["folio"] = match.group(1)
            break
    
    # Extract RUT from subject if present
    rut_pattern = r"([0-9]{1,2}\.[0-9]{3}\.[0-9]{3}-[0-9kK])"
    match = re.search(rut_pattern, subject)
    if match:
        attrs["rut_from_subject"] = match.group(1)
    
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

