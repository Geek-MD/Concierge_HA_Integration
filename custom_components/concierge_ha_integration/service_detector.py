"""Service detection module for Concierge Services."""
from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header
from typing import Any

from .const import (
    SERVICE_TYPE_ELECTRICITY,
    SERVICE_TYPE_GAS,
    SERVICE_TYPE_TELECOM,
    SERVICE_TYPE_UNKNOWN,
    SERVICE_TYPE_WATER,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DetectedService:
    """Represents a detected service from email."""

    service_name: str
    service_id: str
    service_type: str
    sample_subject: str
    sample_from: str
    email_count: int


# Generic patterns to identify billing/service emails
BILLING_INDICATORS = [
    # Subject patterns
    r"factura|boleta|cuenta|cuota|pago|cobro|consumo",
    r"invoice|bill|payment|statement",
    r"folio|número de cuenta|nº de cliente",
    # Common billing terms
    r"vencimiento|fecha de pago|total a pagar|monto",
    r"due date|amount due|total due",
    # Electronic document indicators
    r"dte|documento tributario|electronica",
]

# Common service providers patterns: (regex, display_name, service_type)
# service_type must be one of the SERVICE_TYPE_* constants from const.py.
SERVICE_PATTERNS: list[tuple[str, str, str]] = [
    # Water utilities
    (r"aguas?\s+andinas?", "Aguas Andinas", SERVICE_TYPE_WATER),
    (r"essbio|esval|nuevo\s+sur", "Agua", SERVICE_TYPE_WATER),
    # Electricity utilities
    (r"enel|chilectra|cge\s+distribuci[oó]n", "Electricidad", SERVICE_TYPE_ELECTRICITY),
    # Gas utilities
    (r"metrogas|lipigas|gasco", "Gas", SERVICE_TYPE_GAS),
    # Telecom
    (r"movistar|entel|claro|wom|vtr", "Telecomunicaciones", SERVICE_TYPE_TELECOM),
    (r"mundo.*pac[íi]fico|gtd|telefonica", "Internet/TV", SERVICE_TYPE_TELECOM),
    # Generic utility fallback (type resolved at runtime from keyword)
    (r"compa[ñn][íi]a\s+de\s+agua", "Agua", SERVICE_TYPE_WATER),
    (r"compa[ñn][íi]a\s+de\s+electricidad", "Electricidad", SERVICE_TYPE_ELECTRICITY),
    (r"compa[ñn][íi]a\s+de\s+gas", "Gas", SERVICE_TYPE_GAS),
]


def _decode_mime_words(s: str) -> str:
    """Decode MIME encoded-word strings."""
    decoded_fragments = decode_header(s)
    result = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            result.append(fragment.decode(encoding or "utf-8", errors="ignore"))
        else:
            result.append(fragment)
    return "".join(result)


def _get_email_body(msg: email.message.Message) -> str:
    """Extract text content from email body."""
    body = ""
    
    try:
        # Handle multipart emails
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # Skip attachments
                if "attachment" in content_disposition:
                    continue
                
                # Get text/plain or text/html content
                if content_type in ["text/plain", "text/html"]:
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            body += payload.decode(charset, errors="ignore") + " "  # type: ignore[union-attr]
                    except Exception:
                        pass
            # Limit body length for performance
            body = body[:5000]
        else:
            # Handle non-multipart emails
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="ignore")[:5000]  # type: ignore[union-attr]
            except Exception:
                pass
    except Exception as err:
        _LOGGER.debug("Error extracting email body: %s", err)
    
    return body


def _has_attachments(msg: email.message.Message) -> bool:
    """Check if email has attachments."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition", ""))
                if "attachment" in content_disposition:
                    return True
                
                # Also check for inline attachments with filename
                filename = part.get_filename()
                if filename:
                    return True
        
        return False
    except Exception as err:
        _LOGGER.debug("Error checking attachments: %s", err)
        return False


def _is_billing_email(from_addr: str, subject: str, body: str) -> bool:
    """Check if email appears to be a billing/service email."""
    combined_text = f"{from_addr} {subject} {body}".lower()
    
    # Check for billing indicators
    for pattern in BILLING_INDICATORS:
        if re.search(pattern, combined_text, re.IGNORECASE):
            return True
    
    return False


def _extract_service_name(from_addr: str, subject: str, body: str) -> tuple[str, str, str] | None:
    """
    Extract service name and type from email content.

    Returns:
        Tuple of (service_name, service_id, service_type) or None if not identifiable
    """
    combined_text = f"{from_addr} {subject} {body}"

    # Try to match against known service patterns
    for pattern, service_name, service_type in SERVICE_PATTERNS:
        if re.search(pattern, combined_text, re.IGNORECASE):
            # Create a normalized service_id
            service_id = re.sub(r'[^a-z0-9]+', '_', service_name.lower())
            return (service_name, service_id, service_type)

    # If no specific match, try to extract company name from sender domain
    domain_match = re.search(r'@([a-zA-Z0-9\-]+)\.[a-zA-Z]+', from_addr)
    if domain_match:
        domain = domain_match.group(1)
        # Clean up common prefixes/suffixes
        domain = re.sub(r'^(admin|noreply|info|facturacion|dte|no-reply)', '', domain, flags=re.IGNORECASE)
        domain = re.sub(r'(admin|cl)$', '', domain, flags=re.IGNORECASE)
        domain = domain.strip('-_')

        if len(domain) > 3:  # Avoid very short domain parts
            # Capitalize first letter of each word
            service_name = ' '.join(word.capitalize() for word in re.split(r'[-_]', domain))
            service_id = re.sub(r'[^a-z0-9]+', '_', domain.lower())
            return (service_name, service_id, SERVICE_TYPE_UNKNOWN)

    # Try to extract from subject (look for company names in uppercase)
    # Pattern: consecutive uppercase words that might be a company name
    uppercase_matches = re.findall(r'\b[A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){0,3}\s+S\.?A\.?', subject)
    if uppercase_matches:
        company_name = uppercase_matches[0].strip()
        # Clean up
        company_name = re.sub(r'\s+S\.?A\.?$', '', company_name)
        service_id = re.sub(r'[^a-z0-9]+', '_', company_name.lower())
        return (company_name.title(), service_id, SERVICE_TYPE_UNKNOWN)

    return None


def detect_services_from_imap(
    imap_server: str,
    imap_port: int,
    email_address: str,
    password: str,
    max_emails: int = 100,
) -> list[DetectedService]:
    """
    Scan IMAP mailbox and detect available services using heuristics.
    
    Args:
        imap_server: IMAP server address
        imap_port: IMAP server port
        email_address: Email address for login
        password: Email password
        max_emails: Maximum number of emails to scan (default: 100)
    
    Returns:
        List of detected services
    """
    detected_services: dict[str, dict[str, Any]] = {}
    
    imap = None
    try:
        # Connect to IMAP server
        imap = imaplib.IMAP4_SSL(imap_server, imap_port)
        imap.login(email_address, password)
        
        # Select inbox
        imap.select("INBOX")
        
        # Search for all emails (or limit to recent ones)
        status, messages = imap.search(None, "ALL")
        
        if status != "OK":
            _LOGGER.warning("Failed to search emails")
            return []
        
        # Get email IDs
        email_ids = messages[0].split()
        
        # Limit number of emails to scan
        email_ids = email_ids[-max_emails:]
        
        _LOGGER.info("Scanning %d emails for service detection", len(email_ids))
        
        # Scan emails
        for email_id in email_ids:
            try:
                # Fetch email
                status, msg_data = imap.fetch(email_id, "(RFC822)")
                
                if status != "OK":
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]  # type: ignore[index]
                msg = email.message_from_bytes(raw_email)  # type: ignore[arg-type]
                
                # Check if email has attachments (requirement: bills usually come as attachments)
                if not _has_attachments(msg):
                    continue
                
                # Get from and subject
                from_header = msg.get("From", "")
                subject_header = msg.get("Subject", "")
                
                # Decode headers
                from_addr = _decode_mime_words(from_header)
                subject = _decode_mime_words(subject_header)
                
                # Get email body
                body = _get_email_body(msg)
                
                # Check if this is a billing email
                if not _is_billing_email(from_addr, subject, body):
                    continue
                
                # Try to extract service name
                service_info = _extract_service_name(from_addr, subject, body)
                
                if service_info:
                    service_name, service_id, service_type = service_info

                    # Add or update detected service
                    if service_id not in detected_services:
                        detected_services[service_id] = {
                            "name": service_name,
                            "id": service_id,
                            "type": service_type,
                            "sample_subject": subject,
                            "sample_from": from_addr,
                            "count": 1,
                        }
                    else:
                        detected_services[service_id]["count"] += 1
                
            except Exception as err:
                _LOGGER.debug("Error processing email %s: %s", email_id, err)
                continue
        
    except imaplib.IMAP4.error as err:
        _LOGGER.error("IMAP error during service detection: %s", err)
        raise
    except Exception as err:
        _LOGGER.error("Error during service detection: %s", err)
        raise
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
    
    # Convert to list of DetectedService objects
    result = [
        DetectedService(
            service_name=svc["name"],
            service_id=svc["id"],
            service_type=svc["type"],
            sample_subject=svc["sample_subject"],
            sample_from=svc["sample_from"],
            email_count=svc["count"],
        )
        for svc in detected_services.values()
    ]
    
    _LOGGER.info("Detected %d services: %s", len(result), [s.service_name for s in result])
    
    return result


def get_service_patterns_for_id(service_id: str) -> dict[str, Any]:
    """
    Get detection patterns for a specific service ID.
    
    This is used by the sensor to re-identify emails belonging to a service.
    """
    # For generic detection, we'll use the service_id as part of the pattern
    # This is a simplified version - in practice, we store the patterns during detection
    return {
        "id": service_id,
        "from_patterns": [],
        "subject_patterns": [],
        "body_patterns": [service_id.replace('_', '.*')],
    }


def classify_service_type(from_addr: str, subject: str) -> str:
    """Classify the service type from the email sender and subject.

    This can be used when service metadata does not already carry a type,
    e.g. for services detected in earlier versions of the integration.

    Args:
        from_addr: Decoded ``From`` header of the email.
        subject:   Decoded ``Subject`` header of the email.

    Returns:
        One of the SERVICE_TYPE_* constants.
    """
    combined = f"{from_addr} {subject}"
    for pattern, _name, service_type in SERVICE_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return service_type
    return SERVICE_TYPE_UNKNOWN
