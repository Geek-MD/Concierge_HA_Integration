"""Attribute extraction module for Concierge Services.

Extracts billing attributes from email body using heuristic analysis.
This module uses flexible pattern matching to discover any relevant attributes.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

_LOGGER = logging.getLogger(__name__)


# Common field names that typically indicate relevant billing information
FIELD_INDICATORS = [
    # Spanish
    "número", "numero", "nº", "n°", "folio", "cuenta", "cliente", "factura", "boleta",
    "total", "monto", "importe", "pagar", "precio", "valor", "cargo", "subtotal",
    "período", "periodo", "fecha", "vencimiento", "vence", "dirección", "direccion",
    "domicilio", "rut", "consumo", "uso", "medidor", "lectura", "anterior", "actual",
    "tarifa", "descuento", "recargo", "mora", "interés", "interes", "saldo",
    "deuda", "pagado", "pendiente", "servicio", "plan", "contrato", "sucursal",
    "comuna", "ciudad", "región", "region", "código", "codigo", "referencia",
    # English
    "number", "account", "customer", "invoice", "bill", "receipt", "total", "amount",
    "due", "date", "period", "address", "consumption", "usage", "meter", "reading",
    "rate", "tariff", "discount", "charge", "balance", "debt", "service", "plan",
]

# Patterns for structured data (key-value pairs)
KEY_VALUE_PATTERNS = [
    # Pattern: "Label: Value" or "Label = Value"
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\.]{2,30})\s*[:=]\s*([^\n\r]{1,100})",
    # Pattern: "Label    Value" (multiple spaces/tabs)
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\.]{2,30})\s{2,}([^\n\r]{1,100})",
]

# Patterns for amounts/numbers with currency
CURRENCY_PATTERNS = [
    r"\$\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})?)",
    r"([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})?)\s*(?:CLP|USD|EUR|pesos?)",
]

# Patterns for dates
DATE_PATTERNS = [
    r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
    r"([0-9]{1,2}\s+de\s+[a-zA-Z]+\s+de\s+[0-9]{4})",
    r"([A-Za-z]+\s+[0-9]{1,2},?\s+[0-9]{4})",
]

# Patterns for IDs/numbers (account numbers, folios, etc.)
ID_PATTERNS = [
    r"\b([0-9]{6,})\b",
    r"\b([0-9]{1,3}(?:[.-][0-9]{3}){2,}(?:-[0-9kK])?)\b",
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


def _normalize_key(key: str) -> str:
    """Normalize a key name for storage."""
    # Remove special characters and normalize
    key = key.strip().lower()
    key = re.sub(r'[^a-z0-9áéíóúñü\s]', '', key)
    key = re.sub(r'\s+', '_', key)
    # Limit length
    if len(key) > 50:
        key = key[:50]
    return key


def _normalize_value(value: str) -> str:
    """Normalize a value for storage."""
    # Clean up whitespace
    value = re.sub(r'\s+', ' ', value.strip())
    # Limit length
    if len(value) > 200:
        value = value[:200]
    return value


def _is_relevant_field(key: str) -> bool:
    """Check if a field name seems relevant based on common indicators."""
    key_lower = key.lower()

    for indicator in FIELD_INDICATORS:
        if indicator in key_lower:
            return True

    return False


def _extract_key_value_pairs(text: str) -> dict[str, str]:
    """Extract key-value pairs from text using heuristic patterns."""
    pairs: dict[str, str] = {}

    # Matches HTML entities — indicates unstripped HTML content that should be ignored
    _html_entity_re = re.compile(r"&(?:amp|lt|gt|quot|nbsp|apos);", re.IGNORECASE)

    for pattern in KEY_VALUE_PATTERNS:
        matches = re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE)

        for match in matches:
            key = match.group(1).strip()
            value = match.group(2).strip()

            if len(key) > 50:
                continue

            if len(value) < 1:
                continue

            # Skip values that contain HTML entities (raw HTML leaked through)
            if _html_entity_re.search(value):
                continue

            # Skip values with repeated special characters (formatting noise)
            if len(re.findall(r'[=\-_\*]{3,}', value)) > 0:
                continue

            if _is_relevant_field(key):
                normalized_key = _normalize_key(key)
                normalized_value = _normalize_value(value)

                if normalized_key not in pairs and normalized_value:
                    pairs[normalized_key] = normalized_value

    return pairs


def _extract_currency_amounts(text: str) -> dict[str, str]:
    """Extract currency amounts from text."""
    amounts: dict[str, str] = {}
    
    for pattern in CURRENCY_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        
        for i, match in enumerate(matches):
            amount = match.group(1).strip()
            
            # Store with generic key if we find amounts
            key = f"monto_{i + 1}" if i > 0 else "monto"
            if key not in amounts:
                amounts[key] = amount
    
    return amounts


def _extract_dates(text: str) -> dict[str, str]:
    """Extract dates from text."""
    dates: dict[str, str] = {}

    _DATE_KEYS = ["billing_period_start", "billing_period_end"]

    for pattern in DATE_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)

        for i, match in enumerate(matches):
            if i >= len(_DATE_KEYS):
                break
            date = match.group(1).strip()
            key = _DATE_KEYS[i]
            if key not in dates:
                dates[key] = date

    return dates


def _extract_ids(text: str) -> dict[str, str]:
    """Extract ID numbers from text."""
    ids: dict[str, str] = {}
    
    for pattern in ID_PATTERNS:
        matches = re.finditer(pattern, text)
        
        # Limit to first few IDs to avoid noise
        for i, match in enumerate(matches):
            if i >= 5:  # Max 5 IDs
                break
            
            id_value = match.group(1).strip()
            
            # Skip very long numbers (probably not IDs)
            if len(id_value) > 20:
                continue
            
            key = f"numero_{i + 1}" if i > 0 else "numero"
            if key not in ids:
                ids[key] = id_value
    
    return ids


def extract_attributes_from_email_body(
    subject: str, body: str
) -> dict[str, Any]:
    """
    Extract billing attributes from email subject and body using heuristics.
    
    This function uses flexible pattern matching to discover any relevant
    attributes without being limited to predefined fields.
    
    Args:
        subject: Email subject line
        body: Email body text
    
    Returns:
        Dictionary with extracted attributes
    """
    attributes: dict[str, Any] = {}
    
    try:
        # First, extract attributes specifically from subject line
        # These often contain important identifiers like folio numbers
        subject_attrs = _extract_from_subject(subject)
        attributes.update(subject_attrs)
        
        # Then process subject and body together for comprehensive extraction
        combined_text = f"{subject}\n\n{body}"
        
        # Limit text length for performance
        if len(combined_text) > 15000:
            combined_text = combined_text[:15000]
        
        # Extract structured key-value pairs (most important)
        key_value_pairs = _extract_key_value_pairs(combined_text)
        # Merge without overwriting subject-specific attributes
        for key, value in key_value_pairs.items():
            if key not in attributes:
                attributes[key] = value
        
        # Extract currency amounts
        amounts = _extract_currency_amounts(combined_text)
        for key, value in amounts.items():
            if key not in attributes:
                attributes[key] = value
        
        # Extract dates
        dates = _extract_dates(combined_text)
        for key, value in dates.items():
            if key not in attributes:
                attributes[key] = value
        
        # Extract ID numbers
        ids = _extract_ids(combined_text)
        for key, value in ids.items():
            if key not in attributes:
                attributes[key] = value
        
        # Add metadata about extraction
        attributes["_extraction_method"] = "heuristic"
        attributes["_attributes_found"] = len([k for k in attributes.keys() if not k.startswith("_")])
        
    except Exception as err:
        _LOGGER.debug("Error extracting attributes: %s", err)
        attributes["_extraction_error"] = str(err)
    
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
            attrs["_folio_from_subject"] = match.group(1)
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

