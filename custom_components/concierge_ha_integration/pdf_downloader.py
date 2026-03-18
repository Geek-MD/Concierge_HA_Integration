"""PDF download helper for Concierge HA Integration.

Heuristic that tries three strategies in order:

1. **PDF attachment** — if the email carries a ``application/pdf`` MIME part
   (or any part with a ``.pdf`` filename), its bytes are written directly to
   disk.
2. **Link in HTML body** — if no attachment is found the raw HTML of the
   email body is parsed for ``<a href>`` elements **and** ``<script>`` blocks.
   Three tiers of candidates are collected (best first):

   a. Links whose visible text **or** enclosed ``<img alt>`` text matches
      common Spanish/English billing keywords
      (e.g. *"ver boleta"*, *"descargue su factura"*, *"descargar PDF"*),
      **or** URLs found in ``<script>`` blocks whose surrounding code context
      contains the same keywords.
   b. Links whose ``href`` ends in ``.pdf`` (with optional query string), or
      script-block URLs that end in ``.pdf``.
   c. Links whose ``href`` contains billing-related terms
      (*pdf*, *boleta*, *factura*, *invoice*, *bill*, *comprobante*,
      *acepta.com*, etc.) anywhere in the URL, or script-block URLs that
      match the same patterns.

   ``<a data-url>``, ``<a data-href>``, and ``onclick`` attributes are also
   checked when ``href`` is a non-HTTP placeholder (``#``, ``javascript:…``),
   covering email platforms that store the real URL outside the ``href``.

   Each candidate URL is fetched; the response is validated by magic-byte
   check (``%PDF``) and/or ``Content-Type`` header before the file is
   written.
3. **URL in plain-text body** — if no HTML body is present (or no valid PDF
   was found in the HTML), the plain-text parts of the email are scanned for
   bare HTTP/HTTPS URLs that contain billing-related terms.  The same
   fetch-and-validate logic is applied.

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
import quopri
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Visible link-text / img-alt patterns that suggest a billing PDF link
# (Spanish + English).  Matched against the human-readable label of the link.
_PDF_LINK_KEYWORDS = re.compile(
    r"ver\s+boleta"
    # descarg(?:ar?|ue[ns]?) covers: descarga, descargar, descargue, descarguen, descargues
    r"|descarg(?:ar?|ue[ns]?)\s+(?:su\s+|tu\s+)?boleta"
    r"|revisa(?:r)?\s+(?:tu\s+|su\s+)?boleta"
    r"|ver\s+factura"
    r"|descarg(?:ar?|ue[ns]?)\s+(?:su\s+|tu\s+)?factura"
    r"|ver\s+(?:tu\s+|su\s+)?factura"
    r"|ver\s+cuenta"
    r"|ver\s+comprobante"
    r"|descarg(?:ar?|ue[ns]?)\s+comprobante"
    r"|ver\s+documento"
    r"|descarg(?:ar?|ue[ns]?)\s+(?:pdf|documento|archivo)"
    r"|bajar\s+(?:pdf|boleta|factura)"
    r"|obtener\s+(?:pdf|boleta|factura)"
    r"|imprimir\s+boleta"
    r"|imprimir\s+factura"
    r"|visualizar\s+(?:boleta|factura|documento)"
    r"|ver\s+cobro"
    r"|download.*(?:invoice|bill|statement|pdf|receipt)"
    r"|view.*(?:invoice|bill|statement|receipt)"
    r"|get.*(?:invoice|bill|pdf)",
    re.IGNORECASE,
)

# URL-path/query patterns that indicate a billing PDF link even when the
# visible text does not match _PDF_LINK_KEYWORDS.  Matched against the href.
_PDF_HREF_KEYWORDS = re.compile(
    r"[/=_-](?:pdf|boleta|factura|invoice|bill|comprobante|receipt|documento)"
    r"|(?:pdf|boleta|factura|invoice|bill|comprobante|receipt|documento)[/=_?&]"
    r"|descargar"
    r"|download"
    r"|visualizar"
    # acepta.com is Chile's official electronic-document management portal;
    # any URL on that domain is a billing document viewer/download link.
    r"|acepta\.com"
    # fidelizador.com is a Chilean email-marketing platform used by Metrogas
    # and other utilities; their tracker URLs redirect to the real document.
    r"|fidelizador\.com",
    re.IGNORECASE,
)

# Regex to extract bare HTTP/HTTPS URLs from plain-text email bodies
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Regex to extract the content of every <script>…</script> block.
# Used to find billing URLs that email platforms inject into <a href>
# attributes via JavaScript at render time (e.g. Metrogas / acepta.com).
# The closing tag uses [^>]* to tolerate malformed HTML such as
# </script >, </script  bar>, or other stray content before >.
_SCRIPT_TAG_RE = re.compile(
    r"<script\b[^>]*>(.*?)</script[^>]*>",
    re.IGNORECASE | re.DOTALL,
)

# HTML block/container tags that delimit the context window used to associate
# adjacent (sibling) text with a nearby link.  When one of these tags is
# opened or closed, any pending no-text link is flushed and the context
# buffer is reset so that text from unrelated cells/sections is not mixed in.
_CONTAINER_TAGS = frozenset({
    "td", "th", "tr", "div", "p", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "table", "tbody", "thead", "tfoot",
})

# HTTP User-Agent sent when fetching PDF links from email bodies
_HTTP_USER_AGENT = "ConciergeHAIntegration/0.6.15 (Home Assistant custom integration)"

# Timeout (seconds) for each HTTP download attempt
_DOWNLOAD_TIMEOUT = 30

# First four bytes of every valid PDF file
_PDF_MAGIC = b"%PDF"

# <meta http-equiv="refresh" content="N; url=REDIRECT_URL"> — order of the
# two attributes may be swapped, so we match both orderings separately.
# Group 1 (when http-equiv comes first) or Group 2 (when content comes first)
# holds the redirect URL.
_META_REFRESH_RE = re.compile(
    r'<meta\b[^>]*\bhttp-equiv\s*=\s*["\']?refresh["\']?\b[^>]*'
    r'\bcontent\s*=\s*["\'][^"\']*;\s*url\s*=\s*([^"\'>\s]+)["\']'
    r'|<meta\b[^>]*\bcontent\s*=\s*["\'][^"\']*;\s*url\s*=\s*([^"\'>\s]+)["\']'
    r'[^>]*\bhttp-equiv\s*=\s*["\']?refresh["\']?',
    re.IGNORECASE,
)

# JavaScript patterns that unconditionally redirect the browser to a URL:
#   window.location.href = 'URL'
#   window.location = 'URL'
#   location.href = 'URL'
#   location.replace('URL')
#   location.assign('URL')
_JS_LOCATION_RE = re.compile(
    r'(?:window\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']'
    r'|location\.(?:replace|assign)\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Raw QP fidelizador.com URL extractor
# ---------------------------------------------------------------------------
#
# Metrogas billing emails are delivered by *fidelizador.com* and contain
# *multiple* click-tracking URLs that all share the
# ``trackercl1.fidelizador.com`` host.  These tracking URLs appear as:
#
#   1. An RFC 2396 angle-bracket link in the **plain-text** body —
#      the "Ver en el navegador" (view-in-browser) reference that sits at the
#      top of the email, above all other content.  In practice this is the
#      same URL that drives the bill download button, routed through
#      fidelizador.com's click-tracking redirect.
#
#   2. One or more ``<a href=3D"…">`` elements in the **HTML** body —
#      including social-media icon links, account links, and the actual
#      bill-download button.
#
# Because every occurrence shares the same domain, the URLs are
# indistinguishable by domain alone.  The critical observation is:
#
#   * In the *plain-text* part the bill link appears **first** (line 1 of the
#     body), while social-media / account links appear later.
#   * The entire email is Quoted-Printable encoded *without* a
#     ``Content-Transfer-Encoding`` header (fidelizador.com omits it).
#     Python's ``email`` library therefore does **not** decode the payload
#     automatically, so the URL may be split across lines with a QP soft
#     line-break (``=\r?\n``):
#
#       <https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4=
#       E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30>
#
#     → reconstructed:
#       https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4
#       E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30
#
# Strategy: walk all MIME parts in document order (plain-text before HTML in a
# standard multipart/alternative message), skip any part that Python has
# already QP-decoded (CTE header present), and search the raw bytes for the
# ``https://trackercl1.fidelizador.com/`` prefix.  The first URL found is the
# plain-text "Ver en el navegador" link and is the correct one to follow.
#
# ``=`` is deliberately excluded from the ordinary-character class in the
# capture so the regex engine is forced to use one of the two QP-escape
# alternatives (``=[0-9A-Fa-f]{2}`` or ``=\r?\n``) whenever it sees ``=``,
# correctly decoding or skipping QP-encoded bytes and soft line-breaks.
_FIDELIZADOR_URL_RE = re.compile(
    rb"https://trackercl1\.fidelizador\.com/"
    rb"(?:[^\r\n\"'<>=\s]|=[0-9A-Fa-f]{2}|=\r?\n)+",
    re.IGNORECASE,
)

# Matches a QP-encoded ``href=3D"https://trackercl1.fidelizador.com/…"``
# attribute in the **raw bytes** of a Quoted-Printable HTML body.
#
# fidelizador.com emails QP-encode the HTML including the ``=`` attribute
# assignment operator, producing ``href=3D"URL"`` in the raw stream.  The URL
# token itself may be split across lines via a ``=\r?\n`` soft line-break.
#
# The captured group (group 1) is the raw URL bytes; pass them through
# ``quopri.decodestring()`` to reconstruct the clean URL.
_FIDELIZADOR_HTML_HREF_QP_RE = re.compile(
    rb'href=3D"(https://trackercl1\.fidelizador\.com/'
    rb'(?:[^\r\n"<>=]|=[0-9A-Fa-f]{2}|=\r?\n)*)',
    re.IGNORECASE,
)


class _LinkExtractor(HTMLParser):
    """Extract (href, link_text) pairs from an HTML document.

    *link_text* is the concatenation of:
    - any visible character data inside the ``<a>…</a>`` tag, **and**
    - the ``alt`` attribute of any ``<img>`` tag nested inside the link.

    This ensures that image-only buttons (e.g.
    ``<a href="…"><img alt="Ver boleta" …></a>``) are treated the same way
    as text links.

    **Adjacent-text fallback** — when both the above yield an empty string
    (image-only button with no ``alt`` text), the extractor also considers:

    1. Text that appeared *before* the ``<a>`` tag within the same container
       element (e.g. ``Ver boleta <a …><img …></a>``).
    2. Text that appears *after* the ``</a>`` tag within the same container
       element (e.g. ``<a …><img …></a> Ver boleta``).

    This covers the Metrogas email pattern where the button image has an
    empty ``alt`` attribute and the human-readable label ("Ver boleta") is
    a sibling text node placed after the closing ``</a>`` tag.  Context is
    reset at every :data:`_CONTAINER_TAGS` boundary so that text from
    unrelated table cells is never associated with a link.

    **Embedded-resource URLs** — the following void/non-anchor elements
    are also scanned because email senders (notably Metrogas / acepta.com)
    sometimes embed the actual document viewer URL in them rather than in a
    plain ``<a href>``:

    - ``<iframe src="…">`` — the acepta.com document depot viewer is
      commonly embedded as an inline frame in the email body or in the
      redirect page served by the click-tracking platform (fidelizador.com).
    - ``<frame src="…">`` — older frameset variant.
    - ``<embed src="…">`` — plugin-based embedding (PDF viewer).
    - ``<object data="…">`` — object-element embedding.
    - ``<form action="…">`` — some acepta.com integrations use a GET form
      whose ``action`` is the document depot URL.

    URLs from these elements are added with an empty text label and
    therefore classified purely by their URL content (tier 3 in the
    priority scheme used by :func:`_find_pdf_links_in_html`).
    """

    def __init__(self) -> None:
        super().__init__()
        self._links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._in_link = False
        # Text accumulated outside <a> tags within the current container.
        # Used as a fallback label when the link itself carries no text.
        self._context_before: list[str] = []
        # A no-text link that has been recorded but whose label has not yet
        # been finalised — it waits for text that follows the </a> tag.
        self._pending_href: str | None = None
        self._pending_label: list[str] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _flush_pending(self) -> None:
        """Commit the pending no-text link with whatever label was collected."""
        if self._pending_href is not None:
            label = " ".join(self._pending_label).strip()
            self._links.append((self._pending_href, label))
            self._pending_href = None
            self._pending_label = []

    def _reset_context(self) -> None:
        """Flush pending link and clear the intra-container text buffer."""
        self._flush_pending()
        self._context_before = []

    # ------------------------------------------------------------------
    # HTMLParser callbacks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "a":
            # A new <a> starts: commit any pending no-text link first so its
            # label window does not extend into the new link's preceding text.
            self._flush_pending()
            href = attr_dict.get("href") or ""
            # When the href is a non-navigable placeholder (``#``,
            # ``javascript:…``, empty) look for the real URL in common
            # ``data-*`` attributes or the ``onclick`` handler.  Some email
            # platforms (e.g. Metrogas / acepta.com) store the document URL
            # there and set the visible ``href`` dynamically via JavaScript.
            if not href.startswith("http"):
                for data_key in (
                    "data-url", "data-href", "data-link",
                    "data-target", "data-action",
                ):
                    candidate = (attr_dict.get(data_key) or "").strip()
                    if candidate.startswith("http"):
                        href = candidate
                        break
                if not href.startswith("http"):
                    onclick = attr_dict.get("onclick") or ""
                    url_m = _URL_IN_TEXT.search(onclick)
                    if url_m:
                        href = url_m.group()
            if href:
                self._current_href = href
                self._current_text = []
                self._in_link = True
        elif tag == "img" and self._in_link:
            # Capture the alt text of images nested inside a link
            alt = (attr_dict.get("alt") or "").strip()
            if alt:
                self._current_text.append(alt)
        elif tag in ("iframe", "frame"):
            # Inline-frame / frame: the document viewer URL is in "src".
            # acepta.com depot viewer URLs appear here in Metrogas emails
            # and in the redirect HTML served by fidelizador.com.
            src = (attr_dict.get("src") or "").strip()
            if src.startswith("http"):
                self._links.append((src, ""))
        elif tag == "embed":
            src = (attr_dict.get("src") or "").strip()
            if src.startswith("http"):
                self._links.append((src, ""))
        elif tag == "object":
            data = (attr_dict.get("data") or "").strip()
            if data.startswith("http"):
                self._links.append((data, ""))
        elif tag == "form":
            # Some acepta.com integrations expose the depot URL as a GET form action.
            action = (attr_dict.get("action") or "").strip()
            if action.startswith("http"):
                self._links.append((action, ""))
        elif tag in _CONTAINER_TAGS:
            # Entering a new container: reset context so text from the
            # previous cell/section is not carried over.
            self._reset_context()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            text = " ".join(self._current_text).strip()
            if self._current_href:
                if text:
                    # The link has explicit text (data or img alt): use it.
                    self._links.append((self._current_href, text))
                else:
                    # No explicit link text: try preceding context first.
                    preceding = " ".join(self._context_before).strip()
                    if preceding:
                        self._links.append((self._current_href, preceding))
                    else:
                        # No preceding text either: mark as pending and wait
                        # for text that follows the </a> within this container.
                        self._pending_href = self._current_href
                        self._pending_label = []
            self._current_href = None
            self._current_text = []
            self._in_link = False
            # Reset preceding-text buffer after the link is processed.
            self._context_before = []
        elif tag in _CONTAINER_TAGS:
            # Leaving a container: flush any pending link.
            self._reset_context()

    def handle_data(self, data: str) -> None:
        if self._in_link:
            stripped = data.strip()
            if stripped:
                self._current_text.append(stripped)
        else:
            stripped = data.strip()
            if stripped:
                if self._pending_href is not None:
                    # Text following a no-text link: accumulate as its label.
                    self._pending_label.append(stripped)
                else:
                    # Text preceding any upcoming link within this container.
                    self._context_before.append(stripped)

    def get_links(self) -> list[tuple[str, str]]:
        self._flush_pending()
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


def _decode_qp_if_needed(
    payload: bytes,
    part: _email_lib.message.Message,
) -> bytes:
    """Apply Quoted-Printable decoding as a fallback when the CTE header is absent.

    Python's :meth:`email.message.Message.get_payload` with ``decode=True``
    only strips QP encoding when the part explicitly declares
    ``Content-Transfer-Encoding: quoted-printable``.  Some email senders
    (e.g. the *fidelizador.com* email-marketing platform used by Metrogas)
    omit this header even though the body **is** QP-encoded.  Without the
    header, ``get_payload(decode=True)`` returns the raw bytes unchanged, so
    the HTML reaches :class:`_LinkExtractor` with:

    * ``=3D`` in place of ``=`` in attribute assignments — causing
      ``HTMLParser`` to misparse ``href=3D"..."`` as an unquoted attribute
      value starting with ``3D"`` rather than the expected HTTP URL.
    * ``=\\n`` (soft line-break) splitting a long URL across two lines — the
      second fragment is parsed as a separate, meaningless attribute.

    As a result, the fidelizador.com tracking URL for the *"Ver boleta"*
    button is never extracted and the PDF cannot be downloaded.

    This helper detects QP-encoded payloads by looking for the ``=\\n`` /
    ``=\\r\\n`` soft line-break marker, which is unambiguous: it has no valid
    meaning in HTML or plain-text outside of QP-encoded content.  When found,
    :func:`quopri.decodestring` is applied so that the bytes passed to the
    HTML/text parsers are always clean UTF-8 / charset-encoded text.

    Args:
        payload: Raw bytes returned by ``part.get_payload(decode=True)``.
        part:    The MIME part (used to read the CTE header).

    Returns:
        QP-decoded bytes when a soft line-break is detected and the CTE header
        does not already say ``quoted-printable``; otherwise *payload*
        unchanged.
    """
    cte = (part.get("Content-Transfer-Encoding") or "").lower().strip()
    if cte == "quoted-printable":
        # Already decoded by get_payload(decode=True).
        return payload
    # Detect QP soft line-breaks, which are unambiguous indicators of
    # QP-encoded content.
    if b"=\r\n" in payload or b"=\n" in payload:
        try:
            return quopri.decodestring(payload)
        except Exception:
            pass
    return payload


def _find_fidelizador_links_in_raw_qp_parts(
    msg: _email_lib.message.Message,
) -> list[str]:
    """Find ``trackercl1.fidelizador.com`` URLs in raw QP-encoded MIME parts.

    Metrogas billing emails delivered by *fidelizador.com* are
    Quoted-Printable encoded **without** a ``Content-Transfer-Encoding``
    header, so Python's :mod:`email` library does not decode them
    automatically.  The bill tracker URL appears in **two** places inside
    the raw bytes:

    1. **Plain-text part** — as an RFC 2396 angle-bracket link at the very
       top of the body (the "Ver en el navegador" / view-in-browser
       reference)::

           <https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4=
           E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30>

       → reconstructed full URL (after removing the ``=\\n`` soft
       line-break)::

           https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4
           E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30

    2. **HTML part** — as a ``href=3D"URL"`` attribute on the image-only
       bill download button ``<a>``, also potentially split by a ``=\\n``
       soft line-break.

    The plain-text part comes **first** in a standard
    ``multipart/alternative`` message, so the URL it contains is found
    before any HTML-body URL.  This is important because the HTML body may
    contain other ``trackercl1.fidelizador.com`` URLs (social-media icons,
    account links, etc.) that would redirect to unrelated pages.

    The function walks MIME parts in document order (using ``msg.walk()``),
    skips any part that already has a ``Content-Transfer-Encoding:
    quoted-printable`` header (Python has decoded those automatically),
    and applies :data:`_FIDELIZADOR_URL_RE` to the raw bytes to find and
    reconstruct the URL.  :func:`quopri.decodestring` is used to remove any
    ``=\\n`` soft line-breaks embedded in the URL.

    Args:
        msg: Parsed email message.

    Returns:
        Deduplicated list of reconstructed ``https://trackercl1.fidelizador
        .com/…`` URLs, in the order they appear across all parts (plain-text
        part URLs come before HTML part URLs in a standard email).
    """
    results: list[str] = []
    seen: set[str] = set()

    parts: list[_email_lib.message.Message] = (
        list(msg.walk()) if msg.is_multipart() else [msg]
    )
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        cte = (part.get("Content-Transfer-Encoding") or "").lower().strip()
        if cte == "quoted-printable":
            # Python already decoded this part; raw QP patterns are gone.
            # The URL will be found later by the normal HTML/text parsers.
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        # Only process payloads that show QP soft line-breaks — the
        # unambiguous sign that the body is QP-encoded without a CTE header.
        if b"=\r\n" not in payload and b"=\n" not in payload:  # type: ignore[operator]
            continue
        for m in _FIDELIZADOR_URL_RE.finditer(payload):  # type: ignore[arg-type]
            raw_url: bytes = m.group()
            # QP-decode: removes =\n soft line-breaks that split the URL
            # across lines and converts any =XX hex codes in the path.
            try:
                url = quopri.decodestring(raw_url).decode("utf-8", errors="ignore")
            except Exception:
                url = raw_url.decode("ascii", errors="ignore")
            url = url.strip()
            if url.startswith("https://trackercl1.fidelizador.com/") and url not in seen:
                seen.add(url)
                results.append(url)
                _LOGGER.debug(
                    "Fidelizador.com bill URL found in raw QP %s part: %s",
                    content_type,
                    url,
                )

    return results


def _find_fidelizador_href_in_html_qp(
    msg: _email_lib.message.Message,
) -> str | None:
    """Find the gas bill download URL from the ``<a href>`` div in raw QP HTML.

    Metrogas billing emails (delivered via *fidelizador.com*) contain **multiple**
    ``trackercl1.fidelizador.com`` click-tracking URLs embedded in the HTML body:
    social-media icon links and account-management buttons appear **earlier** in
    the document, while the bill download button appears **later**.  This means
    the last ``href=3D"https://trackercl1.fidelizador.com/…"`` occurrence in the
    raw HTML bytes belongs to the correct download button.

    The email HTML is Quoted-Printable encoded without a
    ``Content-Transfer-Encoding`` header.  Inside the raw bytes, the
    ``href`` attribute assignment appears as ``href=3D"URL"`` (the ``=`` character
    in ``="`` is itself QP-encoded as ``=3D``).  The URL token may additionally
    span a ``=\\r?\\n`` soft line-break.

    Reconstruction steps (matching the problem statement requirements):

    1. Locate the last ``href=3D"https://trackercl1.fidelizador.com/…"`` in the
       raw HTML bytes.
    2. Capture everything between ``href=3D"`` and the closing ``"``, which may
       span QP soft line-breaks.
    3. Apply :func:`quopri.decodestring` to remove ``=\\n`` soft line-breaks and
       decode any ``=XX`` hex codes, yielding the clean URL.

    This is the **sole** authoritative method for obtaining the gas bill PDF URL
    from Metrogas / fidelizador.com emails; all other extraction paths (plain-text
    part, general HTML link extraction) have been shown to return incorrect URLs.

    Args:
        msg: Parsed email message.

    Returns:
        Reconstructed ``https://trackercl1.fidelizador.com/…`` URL, or ``None``
        when no matching ``href=3D"…"`` attribute is found.
    """
    parts: list[_email_lib.message.Message] = (
        list(msg.walk()) if msg.is_multipart() else [msg]
    )
    last_url: str | None = None

    for part in parts:
        if part.get_content_type() != "text/html":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        cte = (part.get("Content-Transfer-Encoding") or "").lower().strip()
        if cte == "quoted-printable":
            # Python already decoded this part — raw QP patterns are gone.
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        # Only process payloads that show QP soft line-breaks, the
        # unambiguous sign that the body is QP-encoded without a CTE header.
        if b"=\r\n" not in payload and b"=\n" not in payload:  # type: ignore[operator]
            continue
        for m in _FIDELIZADOR_HTML_HREF_QP_RE.finditer(payload):  # type: ignore[arg-type]
            raw_url: bytes = m.group(1)
            # QP-decode: removes =\n soft line-breaks and =XX hex codes.
            try:
                url = quopri.decodestring(raw_url).decode("utf-8", errors="ignore")
            except Exception:
                url = raw_url.decode("ascii", errors="ignore")
            url = url.strip()
            if url.startswith("https://trackercl1.fidelizador.com/"):
                # Keep updating last_url: the final occurrence in the HTML
                # is the bill download button (social-media links appear
                # earlier in the document).
                last_url = url
                _LOGGER.debug(
                    "Fidelizador.com href found in raw QP HTML (updating last): %s",
                    url,
                )

    if last_url:
        _LOGGER.debug("Fidelizador.com bill download URL (last href in HTML): %s", last_url)
    return last_url


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
                payload = _decode_qp_if_needed(payload, part)  # type: ignore[arg-type]
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                payload = _decode_qp_if_needed(payload, msg)  # type: ignore[arg-type]
                charset = msg.get_content_charset() or "utf-8"
                html_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    return "\n".join(html_parts)


def _find_urls_in_script_tags(html_body: str) -> list[tuple[str, str]]:
    """Extract ``(url, context_snippet)`` pairs from ``<script>`` blocks.

    Some email senders (notably Metrogas via acepta.com) embed the actual
    document URL inside a JavaScript variable in the email ``<head>`` and
    set the visible ``<a href>`` dynamically at browser render time.
    Because the Python email parser does not execute JavaScript, the
    ``href`` attribute seen by :class:`_LinkExtractor` remains a placeholder
    (``#`` or ``javascript:…``) and the real URL is never extracted through
    normal link parsing.

    This function walks every ``<script>…</script>`` block in the HTML,
    finds all HTTP/HTTPS URLs within them, and returns each URL paired with
    a 200-character context snippet (the text surrounding the URL inside the
    script).  The context is used by :func:`_find_pdf_links_in_html` to
    classify the URL into the same three-tier priority scheme used for
    ``<a href>`` links.

    Args:
        html_body: Raw HTML string of the email (or any HTML document).

    Returns:
        Deduplicated list of ``(url, context_snippet)`` tuples, one per
        distinct URL found across all ``<script>`` blocks.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for script_m in _SCRIPT_TAG_RE.finditer(html_body):
        script_text = script_m.group(1)
        for url_m in _URL_IN_TEXT.finditer(script_text):
            # Strip trailing JS string delimiters / punctuation that may be
            # attached to the URL inside the script source.
            url = url_m.group().rstrip("\"',;)")
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            # Capture a window of surrounding text for keyword classification.
            start = max(0, url_m.start() - 200)
            end = min(len(script_text), url_m.end() + 200)
            results.append((url, script_text[start:end]))

    return results


def _extract_url_from_html_redirect(html: str) -> str | None:
    """Try to find a redirect target URL from an HTML page.

    Some email click-tracking services (e.g. *fidelizador.com*) serve a thin
    HTML wrapper whose only purpose is to redirect the browser to the real
    document URL.  Because ``urllib`` already follows HTTP-level redirects
    (301/302/307/308), this function handles the two most common
    *client-side* redirect mechanisms embedded in the HTML:

    1. **``<meta http-equiv="refresh">``** — e.g.
       ``<meta http-equiv="refresh" content="0; url=https://…">``.
    2. **JavaScript location assignment** — e.g.
       ``window.location.href = 'https://…'`` or
       ``location.replace('https://…')``.

    The first HTTP URL found by either mechanism is returned.  ``None`` is
    returned when no redirect target can be extracted.

    Args:
        html: Raw HTML text of the response page.

    Returns:
        Absolute HTTP/HTTPS redirect target URL, or ``None``.
    """
    # 1. <meta http-equiv="refresh" …>
    m = _META_REFRESH_RE.search(html)
    if m:
        url = (m.group(1) or m.group(2) or "").strip()
        if url.startswith("http"):
            return url

    # 2. JavaScript location redirect inside <script> blocks
    for script_m in _SCRIPT_TAG_RE.finditer(html):
        script_text = script_m.group(1)
        js_m = _JS_LOCATION_RE.search(script_text)
        if js_m:
            url = (js_m.group(1) or js_m.group(2) or "").strip()
            if url.startswith("http"):
                return url

    return None


def _charset_from_content_type(content_type: str, default: str = "utf-8") -> str:
    """Parse the ``charset`` parameter from a ``Content-Type`` header value.

    Args:
        content_type: Raw ``Content-Type`` header string,
                      e.g. ``"text/html; charset=iso-8859-1"``.
        default:      Charset to return when none is found.

    Returns:
        Charset string (lowercased), or *default*.
    """
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip()
    return default


def _try_html_redirect_download(
    original_url: str,
    html_data: bytes,
    content_type: str,
    dest_path: str,
    _depth: int = 0,
) -> str | None:
    """Attempt to extract a PDF from an HTML page returned instead of a PDF.

    Called by :func:`_download_first_valid_pdf` when a candidate URL returns
    an HTML page.  This covers two families of cases:

    1. **Click-tracking redirects** (e.g. *fidelizador.com*) — the HTML is a
       thin wrapper that redirects the browser to the real document URL via a
       ``<meta http-equiv="refresh">`` tag or a JavaScript statement such as
       ``window.location.href = '…'`` or ``window.location = variable``.

    2. **Document viewer pages** (e.g. *acepta.com/depot/*) — the HTML is an
       actual viewer page that embeds the PDF in an ``<iframe>``, an
       ``<embed>``, an ``<object>``, or exposes a download ``<a href>`` link.

    The function builds a prioritised candidate list and tries each URL in
    order:

    a. An explicit client-side redirect discovered by
       :func:`_extract_url_from_html_redirect` (meta-refresh / direct JS
       location assignment).
    b. All billing-related URLs found anywhere in the HTML by
       :func:`_find_pdf_links_in_html` — this covers ``<a href>``, script
       variables (e.g. ``var url = "https://acepta.com/…"``), ``<iframe
       src>``, ``<embed src>``, ``<object data>``, and ``<form action>``.

    If a candidate itself returns HTML and the recursion depth is below
    ``_MAX_HTML_DEPTH``, this function is called recursively so that a
    two-hop chain (fidelizador → acepta viewer → PDF) is resolved
    automatically.

    Args:
        original_url: The URL that returned the HTML page (for logging).
        html_data:    Raw bytes of the HTML response body.
        content_type: ``Content-Type`` header of the HTML response.
        dest_path:    Destination path to write the PDF to.
        _depth:       Current recursion depth (internal; callers omit this).

    Returns:
        *dest_path* on success, ``None`` if no valid PDF could be obtained.
    """
    _MAX_HTML_DEPTH = 2

    charset = _charset_from_content_type(content_type, default="iso-8859-1")
    try:
        html_text = html_data.decode(charset, errors="ignore")
    except Exception:
        html_text = html_data.decode("utf-8", errors="ignore")

    # --- Build a deduplicated, prioritised candidate list -----------------
    seen: set[str] = set()
    candidates: list[str] = []

    # Priority 1 — explicit client-side redirect (meta-refresh / JS literal)
    redirect_url = _extract_url_from_html_redirect(html_text)
    if redirect_url:
        seen.add(redirect_url)
        candidates.append(redirect_url)

    # Priority 2 — billing URLs found anywhere in the HTML
    # (covers <a href>, <iframe src>, <embed>, <object>, <form action>,
    #  <script> variables, etc.)
    for url in _find_pdf_links_in_html(html_text):
        if url not in seen:
            seen.add(url)
            candidates.append(url)

    if not candidates:
        _LOGGER.warning(
            "URL %s returned HTML but no redirect target or billing URL found "
            "(depth=%d)",
            original_url,
            _depth,
        )
        return None

    _LOGGER.info(
        "URL %s returned HTML (depth=%d); trying %d candidate(s): %s",
        original_url,
        _depth,
        len(candidates),
        candidates,
    )

    for url in candidates:
        _LOGGER.info("Fetching HTML-page candidate: %s", url)
        try:
            rreq = urllib.request.Request(
                url, headers={"User-Agent": _HTTP_USER_AGENT}
            )
            with urllib.request.urlopen(rreq, timeout=_DOWNLOAD_TIMEOUT) as rresp:
                rct: str = rresp.headers.get("Content-Type", "")
                rdata: bytes = rresp.read()
        except urllib.error.URLError as err:
            _LOGGER.warning("URL error fetching %s: %s", url, err)
            continue

        if "application/pdf" in rct or rdata[:4] == _PDF_MAGIC:
            with open(dest_path, "wb") as fh:
                fh.write(rdata)
            _LOGGER.info(
                "Downloaded PDF via HTML page (depth=%d) %s → %s",
                _depth,
                url,
                dest_path,
            )
            return dest_path

        # Another HTML response — recurse if depth budget allows
        if "text/html" in rct and _depth < _MAX_HTML_DEPTH:
            result = _try_html_redirect_download(
                url, rdata, rct, dest_path, _depth + 1
            )
            if result:
                return result
            continue

        _LOGGER.warning(
            "Candidate %s did not return a PDF "
            "(Content-Type: %s, first bytes: %r)",
            url,
            rct,
            rdata[:16],
        )

    return None


def _find_pdf_links_in_html(html_body: str) -> list[str]:
    """Return candidate PDF URLs from an HTML body, best candidates first.

    Priority:
    1. Links whose **visible text or img alt** (or nearby sibling text)
       matches :data:`_PDF_LINK_KEYWORDS`, **or** script-block URLs whose
       surrounding context (within a 200-char window) matches the same
       keywords (e.g. ``"boleta"`` or ``"ver boleta"`` appears near the URL
       inside the JavaScript code).
    2. Links / script-block URLs whose ``href`` / URL ends with ``.pdf``
       (with optional query string).
    3. Links / script-block URLs whose ``href`` / URL contains
       billing-related terms matched by :data:`_PDF_HREF_KEYWORDS` (which
       includes the ``acepta.com`` document portal domain).

    ``<script>`` blocks are scanned in addition to ``<a href>`` tags so that
    URLs injected into anchor elements via JavaScript at render time (e.g.
    Metrogas / acepta.com pattern where the real document URL lives in a JS
    variable in the email ``<head>``) are also discovered.
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
    billing_href_links: list[str] = []

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
        elif _PDF_HREF_KEYWORDS.search(href):
            billing_href_links.append(href)

    # Also scan <script> blocks for billing URLs that JavaScript would
    # otherwise inject into <a href> at browser render time.
    for url, context in _find_urls_in_script_tags(html_body):
        if url in seen:
            continue
        seen.add(url)
        if _PDF_LINK_KEYWORDS.search(context):
            keyword_links.append(url)
        elif re.search(r"\.pdf($|\?)", url, re.IGNORECASE):
            pdf_href_links.append(url)
        elif _PDF_HREF_KEYWORDS.search(url):
            billing_href_links.append(url)

    return keyword_links + pdf_href_links + billing_href_links


def _get_plain_text_body(msg: _email_lib.message.Message) -> str:
    """Return the plain-text content of an email (concatenated parts)."""
    text_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload:
                payload = _decode_qp_if_needed(payload, part)  # type: ignore[arg-type]
                charset = part.get_content_charset() or "utf-8"
                text_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    else:
        if msg.get_content_type() == "text/plain":
            payload = msg.get_payload(decode=True)
            if payload:
                payload = _decode_qp_if_needed(payload, msg)  # type: ignore[arg-type]
                charset = msg.get_content_charset() or "utf-8"
                text_parts.append(
                    payload.decode(charset, errors="ignore")  # type: ignore[union-attr]
                )
    return "\n".join(text_parts)


def _find_urls_in_plain_text(text_body: str) -> list[str]:
    """Return candidate PDF URLs from a plain-text email body, best first.

    Handles two common plain-text link formats:

    1. **RFC 2396 notation** — a label on one line followed by ``<URL>`` on
       the next (as rendered by Gmail, Thunderbird, etc.)::

           DESCARGUE SU BOLETA
           <https://click.info-enel.com/?qs=...>

       The label is matched against :data:`_PDF_LINK_KEYWORDS`; if it
       matches, the URL is treated as a top-priority candidate.

    2. **Bare HTTP/HTTPS URL** — the URL appears directly in the text body,
       filtered by ``.pdf`` suffix or :data:`_PDF_HREF_KEYWORDS`.

    Priority:
    1. RFC 2396 URLs whose preceding label matches :data:`_PDF_LINK_KEYWORDS`.
    2. URLs ending in ``.pdf``.
    3. URLs whose path/query matches :data:`_PDF_HREF_KEYWORDS`.
    """
    seen: set[str] = set()
    keyword_urls: list[str] = []
    pdf_urls: list[str] = []
    billing_urls: list[str] = []

    def _classify(url: str, label: str = "") -> None:
        url = url.strip().rstrip(".,;:!?)>\"'")
        if not url.startswith("http"):
            return
        if url in seen:
            return
        seen.add(url)
        if _PDF_LINK_KEYWORDS.search(label):
            keyword_urls.append(url)
        elif re.search(r"\.pdf($|\?)", url, re.IGNORECASE):
            pdf_urls.append(url)
        elif _PDF_HREF_KEYWORDS.search(url):
            billing_urls.append(url)

    prev_text = ""
    for line in text_body.splitlines():
        stripped = line.strip()
        # RFC 2396: the entire line is <URL> — use the previous line as label
        m = re.fullmatch(r"<(https?://[^\s<>\"']+)>", stripped)
        if m:
            _classify(m.group(1), prev_text)
            prev_text = ""  # label was consumed; reset so it is not reused
            continue
        # Bare URLs embedded anywhere in the line (no label context)
        for url_m in _URL_IN_TEXT.finditer(stripped):
            _classify(url_m.group(), "")
        if stripped:
            prev_text = stripped

    return keyword_urls + pdf_urls + billing_urls


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
    """Heuristic PDF downloader — attachment first, then HTML/text links.

    Strategy
    --------
    1. Walk MIME parts looking for ``application/pdf`` or a ``.pdf`` filename.
       If found, write bytes to *pdf_dir* and return the path.
    2. Parse ``text/html`` parts.  Two sub-steps are tried in order:

       a. **Raw QP fidelizador.com href URL** (Metrogas / fidelizador.com) —
          the **sole** authoritative method for Metrogas emails.  The raw HTML
          bytes are scanned for ``href=3D"https://trackercl1.fidelizador.com/…"``
          attributes (using :func:`_find_fidelizador_href_in_html_qp`).  The
          *last* such ``href`` in document order is the bill download button;
          social-media icon links and account-management buttons appear earlier
          in the HTML.  The URL is reconstructed by removing the QP soft
          line-break (``=\\n``) and the ``3D`` encoding of the ``=`` sign in
          the attribute assignment (``href=3D"…"`` → ``href="…"``).  When a URL
          is found it is stored in ``attributes["pdf_url"]`` before the download
          is attempted.

       b. **HTML link extraction** — the decoded HTML is parsed for
          ``<a href>`` elements and ``<script>`` blocks.  Three tiers of
          candidates are tried (best first):

          i.  Links whose visible text **or** img-alt matches billing
              keywords, **or** script-block URLs whose surrounding context
              matches the same keywords.
          ii. Links / script-block URLs whose ``href`` / URL ends in
              ``.pdf``.
          iii. Links / script-block URLs whose ``href`` / URL contains
               billing-related terms anywhere, including the ``acepta.com``
               document portal domain.

          ``<a data-url>``, ``<a data-href>``, and ``<a onclick>``
          attributes are also checked when ``href`` is a non-HTTP
          placeholder.

       Each candidate is fetched and validated (magic bytes / Content-Type).
    3. If no HTML body is present or no valid PDF was found in step 2, scan
       the ``text/plain`` parts for bare HTTP/HTTPS URLs that contain
       billing-related terms and attempt to download each one.

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
                    When a fidelizador.com bill URL is found it is also stored
                    here as ``attributes["pdf_url"]``.

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
        _LOGGER.info("PDF already present, skipping download: %s", dest_path)
        return dest_path

    _LOGGER.info("Starting PDF download for service '%s' (target: %s)", service_id, dest_path)

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
    if html_body:
        # Attempt 2a: fidelizador.com bill download URL extracted from the
        # ``href=3D"…"`` attribute inside its specific div in the raw QP HTML.
        # This is the sole authoritative method for Metrogas / fidelizador.com
        # emails: the URL is the *last* ``href=3D"trackercl1.fidelizador.com/…"``
        # in the HTML body, because social-media icon links and account-management
        # buttons appear earlier in the document whereas the bill download button
        # comes later.  The URL is reconstructed by removing QP soft line-breaks
        # (``=\n``) and stripping the ``3D`` that encodes the ``=`` sign in the
        # attribute assignment (``href=3D"…"`` → ``href="…"``).
        fidelizador_href_url = _find_fidelizador_href_in_html_qp(msg)
        if fidelizador_href_url:
            _LOGGER.info(
                "Found fidelizador.com bill download URL in raw QP HTML href for "
                "service '%s': %s",
                service_id,
                fidelizador_href_url,
            )
            if attributes is not None:
                attributes["pdf_url"] = fidelizador_href_url
            result = _download_first_valid_pdf([fidelizador_href_url], dest_path, service_id)
            if result:
                return result

        # Attempt 2b: regular HTML link extraction (keyword / .pdf /
        # billing-term priority tiers).
        candidate_urls = _find_pdf_links_in_html(html_body)
        if not candidate_urls:
            _LOGGER.info(
                "No PDF links found in HTML body for service '%s'", service_id
            )
        else:
            _LOGGER.info(
                "Found %d PDF candidate URL(s) in HTML body for service '%s'",
                len(candidate_urls),
                service_id,
            )
            result = _download_first_valid_pdf(candidate_urls, dest_path, service_id)
            if result:
                return result
    else:
        _LOGGER.debug("No HTML body in email for service '%s'", service_id)

    # --- Strategy 3: URL in plain-text body ---
    text_body = _get_plain_text_body(msg)
    if not text_body:
        _LOGGER.debug("No plain-text body in email for service '%s'", service_id)
    else:
        candidate_urls = _find_urls_in_plain_text(text_body)
        if not candidate_urls:
            _LOGGER.info(
                "No PDF URLs found in plain-text body for service '%s'", service_id
            )
        else:
            _LOGGER.info(
                "Found %d PDF candidate URL(s) in plain-text body for service '%s'",
                len(candidate_urls),
                service_id,
            )
            result = _download_first_valid_pdf(candidate_urls, dest_path, service_id)
            if result:
                return result

    _LOGGER.warning("No PDF could be obtained for service '%s'", service_id)
    return None


def _download_first_valid_pdf(
    candidate_urls: list[str],
    dest_path: str,
    service_id: str,
) -> str | None:
    """Try each URL in *candidate_urls* until a valid PDF is saved.

    The response is validated by magic-byte check (``%PDF``) and/or the
    ``Content-Type`` header.  Returns *dest_path* on success, ``None`` if
    all candidates failed.
    """
    for url in candidate_urls:
        try:
            _LOGGER.info("Attempting PDF download from %s", url)
            req = urllib.request.Request(
                url, headers={"User-Agent": _HTTP_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
                content_type: str = resp.headers.get("Content-Type", "")
                pdf_data: bytes = resp.read()

            # Validate PDF magic bytes or content-type header.
            if "application/pdf" in content_type or pdf_data[:4] == _PDF_MAGIC:
                with open(dest_path, "wb") as fh:
                    fh.write(pdf_data)
                _LOGGER.info("Downloaded PDF %s → %s", url, dest_path)
                return dest_path

            # If the response is HTML, attempt to follow a client-side
            # redirect (meta-refresh or JS location assignment) that some
            # click-tracking services (e.g. fidelizador.com) use to record
            # clicks before sending the browser to the real document URL.
            if "text/html" in content_type:
                result = _try_html_redirect_download(
                    url, pdf_data, content_type, dest_path
                )
                if result:
                    return result
                continue

            _LOGGER.warning(
                "URL %s did not return a PDF (Content-Type: %s, first bytes: %r)",
                url,
                content_type,
                pdf_data[:16],
            )

        except urllib.error.URLError as err:
            _LOGGER.warning("URL error downloading %s: %s", url, err)
        except OSError as err:
            _LOGGER.warning("Could not write downloaded PDF to %s: %s", dest_path, err)

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
