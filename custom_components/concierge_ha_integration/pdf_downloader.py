"""PDF download helper for Concierge HA Integration.

Heuristic that tries two strategies in order:

1. **PDF attachment** — if the email carries a ``application/pdf`` MIME part
   (or any part with a ``.pdf`` filename), its bytes are written directly to
   disk.
2. **Link in HTML body** — if no attachment is found the raw HTML of the
   email body is parsed for ``<a href>`` elements whose visible link text
   matches common Spanish/English billing keywords
   (e.g. *"ver boleta"*, *"descargue su boleta"*) **or** whose ``href``
   ends in ``.pdf``.  The first URL that returns a valid PDF response is
   saved.

Saved files are named with the scheme::

    {service_id}_{YYYY}-{MM}_{folio}.pdf   # folio known
    {service_id}_{YYYY}-{MM}.pdf           # folio unavailable

``{YYYY}-{MM}`` is taken from ``billing_period_start`` when the attribute
extractor already found it; otherwise it falls back to the email's ``Date``
header.

A ``purge_old_pdfs()`` helper removes files older than *max_age_days*
(default: 365) from the download directory so storage does not grow
unbounded.
"""
from __future__ import annotations

import email as _email_lib
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Visible link-text patterns that suggest a billing PDF link (Spanish + English)
_PDF_LINK_KEYWORDS = re.compile(
    r"ver\s+boleta"
    r"|descarga(?:r|e)?\s+(?:su\s+)?boleta"
    r"|ver\s+factura"
    r"|descarga(?:r|e)?\s+(?:su\s+)?factura"
    r"|ver\s+cuenta"
    r"|descargar\s+(?:pdf|documento)"
    r"|download.*(?:invoice|bill|statement|pdf)",
    re.IGNORECASE,
)

# HTTP User-Agent sent when fetching PDF links from email bodies
_HTTP_USER_AGENT = "ConciergeHAIntegration/0.4.10 (Home Assistant custom integration)"

# Timeout (seconds) for each HTTP download attempt
_DOWNLOAD_TIMEOUT = 30

# First four bytes of every valid PDF file
_PDF_MAGIC = b"%PDF"


# ---------------------------------------------------------------------------
# Internal HTML parser
# ---------------------------------------------------------------------------

class _LinkExtractor(HTMLParser):
    """Extract (href, link_text) pairs from an HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self._links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._in_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href:
                self._current_href = href
                self._current_text = []
                self._in_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            text = " ".join(self._current_text).strip()
            if self._current_href:
                self._links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []
            self._in_link = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            stripped = data.strip()
            if stripped:
                self._current_text.append(stripped)

    def get_links(self) -> list[tuple[str, str]]:
        return list(self._links)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_pdf_attachment_bytes(msg: _email_lib.message.Message) -> bytes | None:
    """Return the raw bytes of the first PDF attachment found, or ``None``."""
    if not msg.is_multipart():
        return None
    for part in msg.walk():
        content_type = part.get_content_type()
        filename = part.get_filename() or ""
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                return payload  # type: ignore[return-value]
    return None


def _get_html_body(msg: _email_lib.message.Message) -> str:
    """Return the raw HTML content of an email (concatenated parts)."""
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/html":
                continue
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                html_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    return "\n".join(html_parts)


def _find_pdf_links_in_html(html_body: str) -> list[str]:
    """Return candidate PDF URLs from an HTML body, best candidates first.

    Priority:
    1. Links whose **visible text** matches :data:`_PDF_LINK_KEYWORDS`.
    2. Links whose ``href`` ends with ``.pdf`` (with optional query string).
    """
    extractor = _LinkExtractor()
    try:
        extractor.feed(html_body)
    except Exception:
        return []

    links = extractor.get_links()

    seen: set[str] = set()
    keyword_links: list[str] = []
    pdf_href_links: list[str] = []

    for href, text in links:
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        if _PDF_LINK_KEYWORDS.search(text):
            keyword_links.append(href)
        elif re.search(r"\.pdf($|\?)", href, re.IGNORECASE):
            pdf_href_links.append(href)

    return keyword_links + pdf_href_links


def _build_filename(
    service_id: str,
    email_date: datetime | None,
    attributes: dict[str, Any] | None,
) -> str:
    """Build a deterministic filename for a bill PDF.

    Format: ``{service_id}_{YYYY}-{MM}_{folio}.pdf``
    Falls back to ``{service_id}_{YYYY}-{MM}.pdf`` when folio is unavailable.

    ``YYYY-MM`` is derived from ``billing_period_start`` (preferred) or the
    email's ``Date`` header, whichever is available first.
    """
    attrs = attributes or {}

    # --- determine year + month ---
    year: int | None = None
    month: int | None = None

    period_start = attrs.get("billing_period_start")
    if period_start:
        # period_start may be a date string like "2026-02-01" or "01/02/2026"
        m = re.search(r"(\d{4})[/-](\d{1,2})", str(period_start))
        if not m:
            # Try DD/MM/YYYY
            m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", str(period_start))
            if m:
                year = int(m.group(3))
                month = int(m.group(2))
        else:
            year = int(m.group(1))
            month = int(m.group(2))

    if year is None or month is None:
        ref_date = email_date or datetime.now(tz=timezone.utc)
        year = ref_date.year
        month = ref_date.month

    date_part = f"{year:04d}-{month:02d}"

    # --- folio (optional) ---
    folio = attrs.get("folio")
    if folio:
        return f"{service_id}_{date_part}_{folio}.pdf"
    return f"{service_id}_{date_part}.pdf"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download_pdf_from_email(
    msg: _email_lib.message.Message,
    pdf_dir: str,
    service_id: str,
    email_date: datetime | None = None,
    attributes: dict[str, Any] | None = None,
) -> str | None:
    """Heuristic PDF downloader — attachment first, then HTML link.

    Strategy
    --------
    1. Walk MIME parts looking for ``application/pdf`` or a ``.pdf`` filename.
       If found, write bytes to *pdf_dir* and return the path.
    2. Parse ``text/html`` parts for ``<a href>`` elements whose visible text
       matches billing keywords or whose href ends in ``.pdf``.  The first URL
       that returns a valid PDF response is saved.

    The saved filename encodes service identity and billing period so that
    subsequent runs with the same bill produce the same name and the download
    is skipped if the file already exists.

    Args:
        msg:        Parsed :class:`email.message.Message`.
        pdf_dir:    Directory where PDFs are stored (created if absent).
        service_id: Slug used to build the filename (e.g. ``aguas_andinas``).
        email_date: Parsed ``Date`` header of the email (used in filename).
        attributes: Already-extracted billing attributes; used to get
                    ``billing_period_start`` and ``folio`` for the filename.

    Returns:
        Absolute path of the saved PDF, or ``None`` if nothing was found.
    """
    try:
        os.makedirs(pdf_dir, exist_ok=True)
    except OSError as err:
        _LOGGER.warning("Cannot create PDF directory %s: %s", pdf_dir, err)
        return None

    filename = _build_filename(service_id, email_date, attributes)
    dest_path = os.path.join(pdf_dir, filename)

    if os.path.exists(dest_path):
        _LOGGER.debug("PDF already present, skipping download: %s", dest_path)
        return dest_path

    # --- Strategy 1: PDF attachment ---
    pdf_bytes = _get_pdf_attachment_bytes(msg)
    if pdf_bytes:
        try:
            with open(dest_path, "wb") as fh:
                fh.write(pdf_bytes)
            _LOGGER.info("Saved PDF attachment → %s", dest_path)
            return dest_path
        except OSError as err:
            _LOGGER.warning("Could not write PDF attachment to %s: %s", dest_path, err)

    # --- Strategy 2: Link in HTML body ---
    html_body = _get_html_body(msg)
    if not html_body:
        _LOGGER.debug("No HTML body in email for service '%s'", service_id)
        return None

    candidate_urls = _find_pdf_links_in_html(html_body)
    if not candidate_urls:
        _LOGGER.debug("No PDF links found in HTML body for service '%s'", service_id)
        return None

    for url in candidate_urls:
        try:
            _LOGGER.debug("Attempting PDF download from %s", url)
            req = urllib.request.Request(
                url, headers={"User-Agent": _HTTP_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
                content_type: str = resp.headers.get("Content-Type", "")
                pdf_data: bytes = resp.read()

            # Validate PDF magic bytes or content-type header
            if not (
                "application/pdf" in content_type
                or pdf_data[:4] == _PDF_MAGIC
            ):
                _LOGGER.debug(
                    "URL %s did not return a PDF (Content-Type: %s)", url, content_type
                )
                continue

            with open(dest_path, "wb") as fh:
                fh.write(pdf_data)
            _LOGGER.info("Downloaded PDF %s → %s", url, dest_path)
            return dest_path

        except urllib.error.URLError as err:
            _LOGGER.debug("URL error downloading %s: %s", url, err)
        except OSError as err:
            _LOGGER.warning("Could not write downloaded PDF to %s: %s", dest_path, err)

    _LOGGER.debug("No PDF could be obtained for service '%s'", service_id)
    return None


def purge_old_pdfs(pdf_dir: str, max_age_days: int = 365) -> int:
    """Delete ``.pdf`` files in *pdf_dir* that are older than *max_age_days*.

    Args:
        pdf_dir:      Directory to scan.
        max_age_days: Files whose mtime is older than this are removed.

    Returns:
        Number of files deleted.
    """
    if not os.path.isdir(pdf_dir):
        return 0

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    deleted = 0

    for fname in os.listdir(pdf_dir):
        if not fname.lower().endswith(".pdf"):
            continue
        fpath = os.path.join(pdf_dir, fname)
        try:
            if os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                _LOGGER.info("Purged old PDF: %s", fpath)
                deleted += 1
        except OSError as err:
            _LOGGER.debug("Could not check/remove %s: %s", fpath, err)

    if deleted:
        _LOGGER.info("Purged %d old PDF(s) from %s", deleted, pdf_dir)

    return deleted
