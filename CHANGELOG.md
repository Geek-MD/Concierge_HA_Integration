# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.12] - 2026-03-19

### Changed
- **Metrogas/fidelizador.com bill URL — primary strategy switched to plain-text
  `[image: Ver boleta]` marker** (`pdf_downloader.py`):

  The previous primary strategy (`_find_fidelizador_href_via_bs4`) searched
  the QP-decoded HTML body for `<img alt="Ver boleta">` using BeautifulSoup.
  While structurally sound, this approach was unreliable in practice because
  the URL reconstruction from the HTML body was incorrect.

  The new primary strategy (`_find_fidelizador_href_in_plain_text`) searches
  the QP-decoded **plain-text** body for the `[image: Ver boleta]` marker.
  Email clients such as Gmail render image-only HTML anchors as plain-text
  `[image: alt_text] <URL>` sequences, making this marker a reliable
  anchor-free indicator for the bill-download button.  The function locates
  this marker via `_IMAGE_VER_BOLETA_PLAIN_RE` and extracts the
  `https://trackercl1.fidelizador.com/` URL that follows it within a
  500-character window.

  The BeautifulSoup HTML approach (`_find_fidelizador_href_via_bs4`) is
  retained as a fallback when the plain-text body does not contain the
  expected marker.

  The `download_pdf_from_email` Strategy 2 download block was also
  refactored so that a `fidelizador_href_url` found via the plain-text path
  is downloaded even when the email has no HTML body.

  Summary of changes:
  - `_IMAGE_VER_BOLETA_PLAIN_RE` module-level compiled regex added.
  - `_find_fidelizador_href_in_plain_text` added.
  - `download_pdf_from_email`: plain-text search runs first; BeautifulSoup
    is demoted to fallback.
  - Strategy 2 download block decoupled from `if html_body:` guard.

- **`manifest.json`**: Version bumped to `0.7.12`.

## [0.7.11] - 2026-03-19

### Changed
- **Metrogas/fidelizador.com bill URL — raw QP-byte regex replaced with BeautifulSoup**
  (`pdf_downloader.py`):

  The Metrogas billing email contains multiple `trackercl1.fidelizador.com`
  click-tracking URLs (social-media icons, account-management buttons, the
  bill-download button, and footer/unsubscribe links).  The previous approach
  (`_find_fidelizador_href_in_html_qp`) selected the bill URL by searching a
  200-byte context window in **raw QP bytes** for billing keywords, relying on
  `_FIDELIZADOR_BILLING_CONTEXT_RE` to account for soft line-breaks (`=\r\n`)
  and QP-encoded characters such as `=3D`.

  The bill-download button is structurally unambiguous: it is always the
  anchor that wraps `<img alt="Ver boleta">`.  The new
  `_find_fidelizador_href_via_bs4` function uses **BeautifulSoup** on the
  already QP-decoded HTML (produced by `_get_html_body` /
  `_decode_qp_if_needed`) to locate this exact element — walking every
  `<a href>` tag and returning the first whose child `<img>` has `alt`
  matching `"Ver boleta"` (case-insensitive via `_VER_BOLETA_RE`) and whose
  `href` starts with `https://trackercl1.fidelizador.com/`.

  Because QP decoding runs before BeautifulSoup, the soft line-break
  (`=\r\n`) that splits long URLs in raw bytes is already removed, and
  `=3D` has been turned back into `=` — BeautifulSoup sees the complete,
  clean `href` with no regex required.

  Summary of changes:
  - `_find_fidelizador_href_in_html_qp` removed.
  - `_find_fidelizador_href_via_bs4` added.
  - `_VER_BOLETA_RE` module-level compiled regex added.

- **`manifest.json`**: `beautifulsoup4>=4.12.3` added to `requirements`;
  version bumped to `0.7.11`.

## [0.7.10] - 2026-03-19

### Changed
- **PDF URL storage redesign — `pdf_url` attribute populated before
  cached-file early return** (`pdf_downloader.py`):

  Previously, when the target PDF was already present on disk,
  `download_pdf_from_email` returned early after reading the download URL
  from a companion `.url` file that had been written alongside the PDF on
  the first download.  This meant `attributes["pdf_url"]` was unavailable
  whenever the companion file was missing or had been deleted.

  URL extraction (fidelizador/BeautifulSoup path and regular HTML link scan)
  is now performed **before** the cached-file check, so
  `attributes["pdf_url"]` is always populated directly from the current
  email body regardless of whether the PDF is already on disk.  The
  companion `.url` file mechanism (`_save_url_companion`) has been removed
  entirely.

  Affected call sites:
  - `download_pdf_from_email`: URL extraction and `attributes["pdf_url"]`
    assignment moved before `if os.path.exists(dest_path)`.
  - `_download_first_valid_pdf`: `_save_url_companion` call removed.
  - `purge_old_pdfs`: no longer removes `*.url` companion files.

- **`manifest.json`**: Version bumped to `0.7.10`.

## [0.7.9] - 2026-03-19

### Fixed
- **Gas service PDF download URL — image-only "Ver boleta" button not matched**
  (`pdf_downloader.py`):

  Metrogas billing emails deliver the "Ver boleta" button as an **image-only
  anchor** — the visible label is a PNG image (`boleta.png`) rather than
  plain text.  The billing-context pattern (`_FIDELIZADOR_BILLING_CONTEXT_RE`)
  previously contained only Spanish/English text keywords (e.g. "ver boleta",
  "descargar boleta"), so the button was never matched and the code fell back
  to the **last** fidelizador.com URL in the HTML, which is a
  footer/unsubscribe link pointing to a different resource.

  The fix adds `boleta(?:=\r?\n)?\.png` to the billing-context regex.  This
  pattern matches the `<img src="…/boleta.png">` tag that is nested inside
  the billing anchor — the image tag is within the context window (from the
  href to the closing `</a>`), so the correct "Ver boleta" URL is now
  selected instead of the footer fallback.  The optional `=\r?\n` allows for
  a QP soft line-break between "boleta" and ".png" in the raw QP-encoded
  email body.

- **`manifest.json`**: Version bumped to `0.7.9`.

## [0.7.8] - 2026-03-18

### Changed
- **Stale-data Problem detection in `binary_sensor.concierge_{service}_status`**
  (`binary_sensor.py`):

  The status binary sensor now reports **Problem** (`is_on = True`) in an
  additional scenario: when the most recently processed bill
  (`sensor.concierge_{service}_last_update`) is **older than one calendar
  month**.  The previous behaviour only flagged a Problem when no bill data
  had ever been found.

  | Condition | State |
  |---|---|
  | No coordinator data | Problem |
  | No bill data for service | Problem |
  | `last_update` is `None` | Problem |
  | `last_update` older than 1 calendar month | **Problem** *(new)* |
  | `last_update` within the last month | OK |

  Calendar-month arithmetic uses `dateutil.relativedelta` (a core HA
  dependency) to handle month-boundary edge cases correctly.  Timezone-naive
  datetimes are safely normalised to UTC before comparison.

- **`requirements.txt`**: `types-python-dateutil` added as a dev dependency
  so that `mypy` can type-check the new `dateutil.relativedelta` import
  without raising `[import-untyped]`.

- **`manifest.json`**: Version bumped to `0.7.8`.

## [0.7.7] - 2026-03-18

### Changed
- **Electricity service — billing charge fields promoted to dedicated sensor entities**
  (`sensor.py`, `binary_sensor.py`):

  The following fields, previously bundled as attributes on
  `binary_sensor.concierge_{service}_status`, are now individual sensor entities:

  | New entity | Attribute replaced | Unit |
  |---|---|---|
  | `sensor.concierge_{service}_service_administration` | `service_administration` | `$` |
  | `sensor.concierge_{service}_electricity_transport` | `electricity_transport` | `$` |
  | `sensor.concierge_{service}_stabilization_fund` | `stabilization_fund` | `$` |
  | `sensor.concierge_{service}_electricity_consumption` | `electricity_consumption` | `$` |

  All four are backed by the now-generic `ConciergeServiceBillingBreakdownSensor`
  class (renamed from `ConciergeWaterSpecificSensor`, which was already fully
  parameterised).

- **`ConciergeWaterSpecificSensor` → `ConciergeServiceBillingBreakdownSensor`**
  (`sensor.py`): class renamed to reflect its use for both water and electricity
  billing breakdowns.

- **`manifest.json`**: Version bumped to `0.7.7`.

## [0.7.6] - 2026-03-18

### Changed
- **Water service — water billing fields promoted to dedicated sensor entities**
  (`sensor.py`, `binary_sensor.py`):

  The 11 water-specific billing fields that were previously bundled as
  attributes on `binary_sensor.concierge_{service}_status` are now exposed as
  individual sensor entities:

  | New entity | Attribute replaced | Unit |
  |---|---|---|
  | `sensor.concierge_{service}_fixed_charge` | `fixed_charge` | `$` |
  | `sensor.concierge_{service}_cost_per_unit_peak` | `cubic_meter_peak_water_cost` | `$/m³` |
  | `sensor.concierge_{service}_cost_per_unit_non_peak` | `cubic_meter_non_peak_water_cost` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_overconsumption` | `cubic_meter_overconsumption` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_collection` | `cubic_meter_collection` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_treatment` | `cubic_meter_treatment` | `$/m³` |
  | `sensor.concierge_{service}_water_consumption` | `water_consumption` | `$` |
  | `sensor.concierge_{service}_wastewater_recolection` | `wastewater_recolection` | `$` |
  | `sensor.concierge_{service}_wastewater_treatment` | `wastewater_treatment` | `$` |
  | `sensor.concierge_{service}_subtotal` | `subtotal` | `$` |
  | `sensor.concierge_{service}_other_charges` | `other_charges` | `$` |

  The generic `sensor.concierge_{service}_cost_per_unit` sensor is **not**
  created for water service subentries; the new `cost_per_unit_peak` and
  `cost_per_unit_non_peak` sensors replace it entirely.

- **`sensor.concierge_{service}_last_update` — relative-time display** (`sensor.py`):

  The sensor now carries `device_class: timestamp` and returns a native
  `datetime` object instead of an ISO-format string.  The Home Assistant
  frontend automatically renders this as a locale-aware relative time string
  ("hace 2 días", "hace 1 semana", "2 days ago", etc.) in the user's
  configured language.

- **`manifest.json`**: Version bumped to `0.7.6`.



### Fixed
- **mypy type error in `_find_fidelizador_href_in_html_qp`** (`pdf_downloader.py`):

  `get_payload(decode=True)` returns `bytes | str | None`.  Mypy therefore
  typed the `payload` variable as `bytes | str | None`, which caused a type
  error on the `_FIDELIZADOR_BILLING_CONTEXT_RE.search(context_window)` call
  because `Pattern[bytes].search()` requires a `Buffer` (bytes-like object),
  not `str`.

  **Fix:** an `isinstance(payload, bytes)` guard is added immediately after
  the `if not payload` early-exit.  This narrows the type to `bytes` for the
  rest of the loop body, eliminating the error and removing the three
  `# type: ignore` suppressions that were masking the same underlying issue
  on the `finditer`, `find`, `min`, and slice calls.

- **`manifest.json`**: Version bumped to `0.7.5`.

## [0.7.4] - 2026-03-18

### Fixed
- **Gas bill PDF download — wrong fidelizador.com URL selected** (`pdf_downloader.py`):

  `_find_fidelizador_href_in_html_qp` was choosing the **last**
  `trackercl1.fidelizador.com` URL in the raw QP HTML body, based on the
  incorrect assumption that the "Ver boleta" download button always appears
  last.  In practice Metrogas emails contain footer/unsubscribe tracking links
  **after** the bill-download button, so the last URL is always a footer link,
  not the billing button.

  **Fix:** The function now identifies the correct URL by searching for billing
  keywords (`ver boleta`, `descargar boleta`, `ver factura`, etc.) within a
  bounded context window in the raw HTML bytes around each candidate URL
  (200 bytes before the `href=3D"` + the link text up to the closing `</a>`).
  The **first URL whose context contains a billing keyword** is returned as
  the preferred bill-download URL.  Bounding the window at the closing `</a>`
  prevents the window from bleeding into the link text of the *next* anchor
  and falsely matching social-media or footer links.  When no billing-context
  match is found the previous last-URL logic is kept as a fallback.

  New module-level constant `_FIDELIZADOR_BILLING_CONTEXT_RE` (bytes pattern)
  encodes the same keyword set as `_PDF_LINK_KEYWORDS`, adapted for matching
  against raw QP-encoded HTML bytes.

- **`manifest.json`**: Version bumped to `0.7.4`.

## [0.7.3] - 2026-03-18

### Fixed
- **Spurious warnings eliminated from PDF downloader** (`pdf_downloader.py`):
  Five intermediate-probe log messages that were emitted at `WARNING` level
  (and therefore appeared in the Home Assistant issue/error panel) have been
  downgraded to `DEBUG`.  These messages represent **expected** failures while
  the downloader heuristically probes multiple candidate URLs:

  - `"URL … returned HTML but no redirect target or billing URL found"` —
    emitted when a click-tracker page (e.g. fidelizador.com) does not contain
    a recognisable redirect or billing link.
  - `"URL error fetching …"` — emitted when a candidate URL inside an HTML
    redirect page returns a network error (e.g. HTTP 404/400).
  - `"Candidate … did not return a PDF"` — emitted when a URL fetched from an
    HTML page returns a non-PDF content type (e.g. a CDN JS file or a video).
  - `"URL … did not return a PDF"` — same as above, in the top-level candidate
    loop.
  - `"URL error downloading …"` — emitted when a top-level candidate URL
    returns an HTTP error (404, 400, etc.).

  Real disk-write failures (`OSError`) continue to be logged at `WARNING`
  level since they represent genuine operational problems.

- **`manifest.json`**: Version bumped to `0.7.3`.

## [0.7.2] - 2026-03-18

### Fixed
- **`sensor.concierge_*_last_update` entity category changed from `CONFIG` to
  `DIAGNOSTIC`** (`sensor.py`): Home Assistant does not allow sensor entities to
  use `EntityCategory.CONFIG`; attempting to register them raised a
  `HomeAssistantError` and the sensors were never added.  The category has been
  corrected to `EntityCategory.DIAGNOSTIC`, which is the appropriate category for
  informational read-only sensors.

- **`manifest.json`**: Version bumped to `0.7.2`.

## [0.7.1] - 2026-03-18

### Changed
- **`sensor.concierge_*_last_update` now holds the full datetime** (`sensor.py`):
  `ConciergeServiceLastUpdateSensor.native_value` now returns the full
  ISO 8601 datetime string (``last_updated.isoformat()``) instead of only the
  date portion (``last_updated.date().isoformat()``).  This is the information
  that was previously exposed as the ``last_updated_datetime`` attribute on the
  companion binary sensor.

- **`last_updated_datetime` attribute removed from status binary sensor**
  (`binary_sensor.py`): The ``last_updated_datetime`` attribute has been dropped
  from ``binary_sensor.concierge_*_status``.  The equivalent information is now
  available directly as the state value of ``sensor.concierge_*_last_update``.

- **`manifest.json`**: Version bumped to `0.7.1`.

### Fixed
- **`pdf_url` attribute now populated after PDF is cached** (`pdf_downloader.py`):
  When the bill PDF was already present on disk from a previous coordinator
  cycle, ``download_pdf_from_email()`` returned early without setting
  ``attributes["pdf_url"]``, causing the attribute to be empty on every run
  after the first download.

  The fix introduces a companion ``.url`` file (``{pdf_path}.url``) that is
  written alongside the PDF whenever a successful URL-based download completes.
  On subsequent cycles the early-return path reads the companion file and
  restores ``attributes["pdf_url"]`` so the sensor always exposes the correct
  download URL.  ``purge_old_pdfs()`` is updated to also delete companion
  ``.url`` files when the associated PDF is purged.

## [0.7.0] - 2026-03-18

### Added
- **`binary_sensor.py`** (new file): `ConciergeServiceStatusBinarySensor` — a
  `BinarySensorDeviceClass.PROBLEM` entity with `EntityCategory.DIAGNOSTIC`.
  One status binary sensor is created per service sub-entry and appears in the
  HA device Diagnostic panel.  `is_on = True` means no bill data was found for
  that service; `is_on = False` means data was retrieved successfully.  All
  attributes that were not promoted to dedicated sensors (folio, billing period,
  address, due date, service-type-specific fields, ``pdf_path``) are retained
  here as `extra_state_attributes`.

- **New per-service sensor entities** (`sensor.py`): The single monolithic
  service sensor has been replaced by four focused sensors per sub-entry,
  aligned with Home Assistant device conventions:

  | Entity ID | Category | Value |
  |---|---|---|
  | `sensor.concierge_{service_id}_last_update` | Diagnostic | Last bill date (ISO 8601) |
  | `sensor.concierge_{service_id}_consumption` | — | m³ (gas/water) or kWh (electricity) |
  | `sensor.concierge_{service_id}_cost_per_unit` | — | $/m³ (gas) or $/kWh (electricity); `None` for water/unknown |
  | `sensor.concierge_{service_id}_total_amount` | — | Total bill amount (`$`) |

  These are implemented as a `_ConciergeServiceBaseSensor` base class plus
  four subclasses: `ConciergeServiceLastUpdateSensor`,
  `ConciergeServiceConsumptionSensor`, `ConciergeServiceCostPerUnitSensor`,
  and `ConciergeServiceTotalAmountSensor`.

### Changed
- **`__init__.py`**: The shared `ConciergeServicesCoordinator` is now
  initialised (including its first refresh) inside `async_setup_entry`,
  *before* `async_forward_entry_setups` is called.  This eliminates the race
  condition that existed when both the `sensor` and `binary_sensor` platforms
  tried to create the coordinator independently.  Both platforms now read the
  coordinator from `hass.data[DOMAIN][entry_id]["coordinator"]`.

- **`__init__.py`**: `PLATFORMS` list extended from `["sensor"]` to
  `["sensor", "binary_sensor"]`.

- **`sensor.py`**: `async_setup_entry` now reads the pre-initialised
  coordinator from `hass.data` instead of creating its own instance.
  `ConciergeServiceSensor` (the old monolithic sensor) has been removed.

- **`config_flow.py`** / **`manifest.json`**: `MINOR_VERSION` bumped to `3`,
  integration version updated to `0.7.0`.

### Removed
- **Legacy single-service sensor** (`sensor.py`): `ConciergeServiceSensor`
  and its single-entity-per-service approach have been removed.  Existing
  installations are migrated automatically via `_migrate_1_2_to_1_3`, which
  removes all stale `sensor.concierge_services_*` entities from the entity
  registry on upgrade (schema version 1.2 → 1.3).

## [0.6.16] - 2026-03-18

### Fixed
- **``pdf_url`` attribute empty in both sensors** (`pdf_downloader.py`):
  Previously the ``pdf_url`` sensor attribute was only populated when the
  fidelizador.com bill download URL was found in the raw Quoted-Printable HTML
  (strategy 2a, Metrogas emails).  For all other download paths — HTML link
  extraction (strategy 2b) and plain-text URL scan (strategy 3) — the
  attribute was never set and always remained ``""``.

  ``_download_first_valid_pdf()`` now accepts an optional *attributes* dict
  parameter.  Whenever a URL in the candidate list yields a valid PDF, the
  function stores that URL in ``attributes["pdf_url"]``.  All three call sites
  inside ``download_pdf_from_email()`` pass the shared *attributes* dict, so
  ``pdf_url`` is populated regardless of which strategy produces the download.

- **Wrong PDF downloaded for gas service** (`pdf_downloader.py`):
  The previous ``_HTTP_USER_AGENT`` value identified the client as a Home
  Assistant custom integration.  Click-tracking servers such as
  *fidelizador.com* inspect the User-Agent header and may return a different
  document (or an error page) when they detect a non-browser client, causing
  the wrong PDF to be saved.

  ``_HTTP_USER_AGENT`` is now set to a standard Mozilla/Chrome browser
  string::

      Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
      (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36

  This causes the fidelizador.com tracker to follow its normal browser
  redirect chain and serve the correct bill PDF.

## [0.6.15] - 2026-03-18

### Added
- **``pdf_url`` attribute for electricity sensor** (`sensor.py`):
  The ``pdf_url`` attribute (introduced for the gas sensor in v0.6.14) is now
  also available on the electricity-service sensor.  It defaults to ``""`` and
  is overridden with the bill download URL whenever a matching email is
  processed and a fidelizador.com URL is reconstructed.

## [0.6.14] - 2026-03-18

### Fixed
- **Gas bill PDF download — definitive fix for incorrect URL selection**
  (`pdf_downloader.py`, `sensor.py`):
  The v0.6.13 strategy used the *first*
  ``trackercl1.fidelizador.com`` URL found in the raw QP bytes, which turned
  out to be the "Ver en el navegador" view-in-browser link from the
  ``text/plain`` part — not the actual bill download button URL.  Following
  that URL yielded the wrong document.

  The definitive fix introduces a new helper
  ``_find_fidelizador_href_in_html_qp()`` that targets exclusively the
  ``text/html`` part and searches the raw QP bytes for
  ``href=3D"https://trackercl1.fidelizador.com/…"`` attributes (the
  Quoted-Printable encoding of ``href="…"``).  Among all matching ``<a href>``
  tags the function returns the **last** one in document order: social-media
  icon links and account-management buttons appear earlier in the HTML body,
  while the bill download button's ``<a>`` element is the last billing CTA.

  URL reconstruction follows the approach required by the problem specification:

  1. Locate the div containing the bill download ``<a>`` element.
  2. Extract the raw bytes of the ``href`` value from ``href=3D"…"``, spanning
     any ``=\\r?\\n`` soft line-breaks.
  3. Apply ``quopri.decodestring()`` to remove soft line-breaks and decode
     ``=XX`` hex codes, yielding the clean URL.

  This is now the **sole** authoritative method for reconstructing the gas bill
  PDF URL from Metrogas / fidelizador.com emails; all previous approaches
  (plain-text part scan, general HTML link extraction) have been shown to
  return incorrect URLs and are no longer used for this purpose.

  Additionally:

  - ``download_pdf_from_email()`` stores the reconstructed URL in
    ``attributes["pdf_url"]`` before attempting the download, making the URL
    available even if the download fails.
  - A new **``pdf_url``** attribute is added to the gas-service sensor
    (``_GAS_ATTR_DEFAULTS``).  It defaults to ``""`` and is overridden with the
    actual reconstructed URL whenever a Metrogas / fidelizador.com email is
    processed.

## [0.6.13] - 2026-03-17

### Fixed
- **Gas bill PDF download — wrong PDF selected when multiple fidelizador.com
  links present** (`pdf_downloader.py`):
  Metrogas billing emails delivered by *fidelizador.com* are Quoted-Printable
  encoded **without** a ``Content-Transfer-Encoding`` header.  They contain
  **multiple** ``trackercl1.fidelizador.com`` click-tracking URLs: one per
  interactive element (social-media icons, account-management buttons, and the
  bill download button).  Because all share the same domain, the
  ``_PDF_HREF_KEYWORDS`` matcher could not distinguish them, and the code was
  following whichever URL appeared first in the decoded HTML — often a
  social-media or account link — and downloading whatever PDF that redirect
  chain produced (which was *not* the gas bill).

  The tracker URL can be found in **two places** in the raw email bytes:

  1. **Plain-text part** — as an RFC 2396 angle-bracket link at the very top
     of the body (the "Ver en el navegador" view-in-browser reference)::

         Ver en el navegador
         <https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4=
         E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30>

     This is the *first* occurrence and the most reliable one: the plain-text
     part has only one ``trackercl1.fidelizador.com`` URL at the top, before
     any social-media or account links.

  2. **HTML part** — as a ``href=3D"URL"`` attribute on the image-only bill
     download button ``<a>`` element, also potentially split by a soft
     line-break.  This occurrence can serve as confirmation that the URL
     is correctly reconstructed.

  In both cases the URL may be split across two QP lines by a soft line-break
  (``=\r?\n``) that must be removed to reconstruct the full URL.

  The fix adds:

  1. **`_FIDELIZADOR_URL_RE` (new regex constant)** — matches
     ``https://trackercl1.fidelizador.com/`` followed by the opaque
     alphanumeric token, spanning QP soft line-breaks (``=\r?\n``) and
     optional QP hex codes (``=XX``).  ``=`` is excluded from the ordinary-
     character class so the engine always uses a QP alternative when it
     encounters ``=``, ensuring no truncation at soft line-breaks.

  2. **`_find_fidelizador_links_in_raw_qp_parts()` (new helper)** — walks
     all MIME parts in document order (``text/plain`` before ``text/html``
     in a standard ``multipart/alternative`` message), skips any part whose
     ``Content-Transfer-Encoding`` header is ``quoted-printable`` (Python
     has already decoded those), checks for QP soft line-breaks as the
     indicator that raw QP processing is needed, and applies
     ``_FIDELIZADOR_URL_RE`` + ``quopri.decodestring()`` to extract and
     reconstruct the full URL.  Because the plain-text part is walked first,
     the first URL returned is the view-in-browser link from the plain-text
     body — the correct one to follow.

  3. **`download_pdf_from_email()` — new attempt 2a** — before the existing
     keyword / ``.pdf`` / billing-term HTML link extraction (now *attempt
     2b*), the function calls ``_find_fidelizador_links_in_raw_qp_parts()``
     and, if any URL is returned, tries to download the PDF from those URLs
     first.  The existing HTML parsing falls through as a fallback if no
     fidelizador.com URL is found in the raw QP bytes or the download fails.

## [0.6.12] - 2026-03-17

### Fixed
- **Gas bill PDF download — Quoted-Printable email body without CTE header**
  (`pdf_downloader.py`): Metrogas billing emails delivered by *fidelizador.com*
  are Quoted-Printable encoded.  In a standards-compliant email the MIME part
  carries a ``Content-Transfer-Encoding: quoted-printable`` header, which
  causes Python's ``email`` library to decode the QP bytes automatically when
  ``get_payload(decode=True)`` is called.  However, some fidelizador.com
  messages omit this header even though the body is fully QP-encoded.  Without
  the header, ``get_payload(decode=True)`` returns the raw bytes unchanged,
  so the HTML body contains:

  * ``=3D`` instead of ``=`` in HTML attribute assignments — for example the
    anchor tag appears as ``<a href=3D"https://trackercl1.fidelizador.com/…">``.
    ``HTMLParser`` treats the ``=`` immediately after ``href`` as the
    attribute-assignment operator and parses ``3D"https://…"`` as an
    *unquoted* attribute value, yielding a ``href`` value of
    ``3D"https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807=``
    instead of the correct URL — which never starts with ``http`` and is
    therefore silently discarded.
  * ``=\n`` (QP soft line-break) splitting the tracking URL across two lines
    — the second fragment (``CB3HF4E1ADBBCE…``) is parsed as a separate,
    meaningless attribute and the first fragment is truncated at ``=``.

  A new private helper ``_decode_qp_if_needed()`` is called immediately after
  ``get_payload(decode=True)`` in both ``_get_html_body()`` and
  ``_get_plain_text_body()``.  It checks whether the raw payload contains
  ``=\n`` or ``=\r\n`` (QP soft line-breaks) — an unambiguous indicator of
  QP-encoded content — and, if so, applies ``quopri.decodestring()`` to strip
  the encoding before the bytes are decoded as a character set and passed to
  the HTML/text parsers.  The helper is a no-op when the ``Content-Transfer-
  Encoding`` header is already ``quoted-printable`` (Python has decoded the
  bytes) or when the payload contains no soft line-breaks.

## [0.6.11] - 2026-03-17

### Fixed
- **Gas bill PDF download — acepta.com depot URL extraction** (`pdf_downloader.py`):
  The acepta.com document depot URL
  (`http://metrogas{YYMM}.acepta.com/depot/{hash}?k={token}`) that the
  Metrogas bill viewer opens is reachable from the email via two paths that
  the previous code did not cover:

  1. **`<iframe src>` / `<embed src>` / `<object data>` / `<form action>`
     extraction in `_LinkExtractor`** — the acepta.com viewer URL often
     appears as an inline-frame source or embedded-object ``data`` attribute
     in the email HTML or in the HTML page served by the fidelizador.com
     click-tracker.  `_LinkExtractor` previously only inspected ``<a href>``
     tags; it now also records HTTP(S) URLs from these four element types
     with an empty text label, so they are classified purely by URL content
     (tier 3 — matches ``acepta\.com`` in `_PDF_HREF_KEYWORDS`).

  2. **`_try_html_redirect_download` — full billing-URL scan as fallback** —
     when the fidelizador.com tracker page uses an *indirect* JavaScript
     redirect (``var u = "https://…"; window.location.href = u;``) the
     ``_extract_url_from_html_redirect`` helper returns ``None`` because it
     only matches *direct string literals*.  The acepta.com URL is, however,
     present in the ``<script>`` block as a string and is already found by
     ``_find_urls_in_script_tags`` → ``_find_pdf_links_in_html``.
     ``_try_html_redirect_download`` now builds a combined candidate list
     from **both** ``_extract_url_from_html_redirect`` (priority 1) **and**
     ``_find_pdf_links_in_html`` (priority 2 — covers ``<a href>``,
     ``<iframe src>``, ``<script>`` variables, etc.), and tries each in
     order.

  3. **Depth-limited recursive HTML following** — the two-hop chain
     *fidelizador.com → acepta.com viewer → PDF download link* is now
     resolved automatically.  ``_try_html_redirect_download`` accepts a
     ``_depth`` parameter and recurses up to two levels deep, so if the
     acepta.com viewer itself returns HTML with a *"Descargar PDF"* link or
     an embedded iframe pointing at the actual PDF bytes, that final hop is
     also followed.  The depth cap (``_MAX_HTML_DEPTH = 2``) prevents
     infinite loops.

## [0.6.10] - 2026-03-17

### Fixed
- **Gas bill PDF download — HTML click-tracking redirects** (`pdf_downloader.py`):
  Metrogas emails delivered via the *fidelizador.com* platform embed billing
  links as click-tracking URLs (e.g.
  ``https://trackercl1.fidelizador.com/…``).  When the code fetched such a
  URL it received an HTML page (``Content-Type: text/html``) instead of a
  PDF, because the tracking server records the click and then redirects the
  browser client-side rather than via an HTTP 301/302 response that
  ``urllib`` would have followed automatically.

  Two complementary changes address this:

  1. **HTML redirect follower in `_download_first_valid_pdf()`** — when a
     fetched URL returns an HTML page, the code now inspects the HTML for
     common client-side redirect mechanisms and, if one is found, fetches
     the redirect target and validates it as a PDF before writing it to
     disk.  Two redirect patterns are recognised:

     - ``<meta http-equiv="refresh" content="N; url=…">``
     - JavaScript ``window.location.href = '…'`` /
       ``location.replace('…')`` / ``location.assign('…')`` assignments
       inside ``<script>`` blocks.

     A single redirect hop is followed per candidate URL to prevent loops.

  2. **`fidelizador.com` added to `_PDF_HREF_KEYWORDS`** — ensures that
     fidelizador.com tracking URLs discovered in email bodies (``<a href>``
     or plain-text) are always classified as billing-related candidates and
     included in the download attempt list, even when the URL path itself
     carries no recognisable billing keyword.

## [0.6.9] - 2026-03-17

### Fixed
- **Gas bill PDF download — JavaScript URL injection** (`pdf_downloader.py`):
  Metrogas emails (sent via the acepta.com platform) embed the real document
  URL inside a JavaScript variable in the email ``<head>`` and set the
  visible ``<a href>`` dynamically at browser render time.  Because the
  Python email parser does not execute JavaScript, the ``href`` attribute
  remained a non-navigable placeholder (``#`` or ``javascript:…``) and the
  real URL was never discovered.

  Three complementary fixes address this:

  1. **`_find_urls_in_script_tags()` (new helper)** — walks every
     ``<script>…</script>`` block in the HTML body, extracts all
     ``http(s)://`` URLs, and classifies them into the same three-tier
     priority scheme used for ``<a href>`` links (keyword context match →
     ``.pdf`` suffix → billing-term / domain match).  The 200-character
     text surrounding each URL in the script is used as the context for
     keyword matching, so variable names such as ``boletaUrl`` or comments
     like ``// URL de la boleta`` promote the URL to the highest tier.

  2. **`acepta.com` added to `_PDF_HREF_KEYWORDS`** — the acepta.com domain
     is Chile's official electronic-document management portal; any URL on
     that domain is a billing document link.  This ensures acepta.com URLs
     found in ``<script>`` blocks (and in ``<a href>`` tags) are always
     picked up as tier-3 candidates even when the surrounding context
     carries no explicit billing keywords.

  3. **`_LinkExtractor` — `data-*` / `onclick` fallback** — when an ``<a>``
     tag has a non-HTTP ``href`` (``#``, ``javascript:…``), the extractor
     now also inspects ``data-url``, ``data-href``, ``data-link``,
     ``data-target``, ``data-action``, and ``onclick`` attributes for a
     real HTTP URL.  This covers email platforms that store the document URL
     in data attributes instead of (or in addition to) a script variable.

## [0.6.8] - 2026-03-17

### Added
- **PDF download logging** (`pdf_downloader.py`, `sensor.py`): Improved
  observability for the PDF download pipeline by elevating key log messages
  from DEBUG to INFO or WARNING level.  These messages are now visible in the
  standard Home Assistant log without enabling debug logging:

  - `INFO`  — PDF download started for each service, number of candidate URLs
    found in the HTML body and plain-text body, each HTTP fetch attempt URL,
    and successful downloads.
  - `INFO`  — "No PDF links found in HTML/plain-text body" when the email
    carries no recognisable billing links.
  - `WARNING` — Failed HTTP requests (network or URL errors), responses that
    are not a valid PDF (unexpected Content-Type / missing magic bytes, with
    first 16 bytes logged for diagnosis), and the final "No PDF could be
    obtained" outcome.
  - `WARNING` — PDF download exceptions that were previously swallowed at
    DEBUG level in the coordinator (`sensor.py`).

### Fixed
- **IMAP connection timeout** (`sensor.py`, `service_detector.py`): Added an
  explicit ``timeout=30`` (seconds) to all ``imaplib.IMAP4_SSL`` constructor
  calls.  Previously, if the IMAP server was unreachable or slow to respond
  the socket would block indefinitely, causing the integration setup to hang
  and triggering the Home Assistant bootstrap warning
  *"Waiting for integrations to complete setup"*.

## [0.6.7] - 2026-03-17

### Fixed
- **Gas bill PDF download** (`pdf_downloader.py`): The Metrogas email uses
  image-only ``<a href="…"><img alt="" …></a>`` buttons where the visible
  label ("Ver boleta") is a sibling text node placed **outside** the
  ``<a>`` tag rather than inside it.  The previous `_LinkExtractor`
  only collected text *inside* ``<a>`` elements, so these buttons were
  returned with an empty label and silently skipped by the keyword filter.

  `_LinkExtractor` now implements an *adjacent-text fallback*:

  - Text that appears **before** an ``<a>`` tag within the same container
    element is kept as the *preceding context*.
  - When a link closes with an empty label, the preceding context is used
    first; if that is also empty the link is marked *pending* and text that
    appears **after** the ``</a>`` (still within the same container) is
    accumulated as its label.
  - Context is reset at every block/container tag boundary
    (``<td>``, ``<tr>``, ``<div>``, ``<p>``, ``<table>``, etc.) so that
    text from unrelated table cells or sections is never incorrectly
    associated with a link.

  With this fix, both occurrences of "Ver boleta" in the Metrogas email are
  correctly matched against `_PDF_LINK_KEYWORDS` and the tracking URL is
  promoted to the top-priority candidate list, enabling the bill PDF to be
  downloaded successfully.


## [0.6.6] - 2026-03-17

### Added
- **5 new water-billing attributes** extracted from the Aguas Andinas PDF
  billing breakdown table (`attribute_extractor.py`):

  pdfminer serialises the billing table column-by-column (all row labels
  first, then three per-row consumption sub-values, then CLP amounts in
  row order). `_WATER_AA_PDF_BILLING_TABLE_RE` anchors on the label block
  to recover all eight amounts at once.

  | Attribute | Source row | Value | Type |
  |---|---|---|---|
  | `water_consumption` | `CONSUMO AGUA POTABLE … 6.426` | `6426` | int |
  | `wastewater_recolection` | `RECOLECCION AGUAS SERVIDAS … 4.902` | `4902` | int |
  | `wastewater_treatment` | `TRATAMIENTO AGUAS SERVIDAS … 3.360` | `3360` | int |
  | `subtotal` | `SUBTOTAL SERVICIO … 15.602` | `15602` | int |
  | `other_charges` | `INTERÉS DEUDA (99) + DESCUENTO LEY REDONDEO (−1)` | `98` | int |

  Cross-verifications (tolerance ±10 CLP; warnings logged on mismatch):
  - `water_consumption ≈ round(consumption × cubic_meter_non_peak_water_cost)`
  - `wastewater_recolection ≈ round(consumption × cubic_meter_collection)`
  - `wastewater_treatment ≈ round(consumption × cubic_meter_treatment)`
  - `subtotal ≈ fixed_charge + water_consumption + wastewater_recolection + wastewater_treatment`

### Changed
- **`_WATER_ATTR_DEFAULTS`** (`sensor.py`): Added zero defaults for
  `water_consumption`, `wastewater_recolection`, `wastewater_treatment`,
  `subtotal`, `other_charges` so the water sensor exposes these fields
  from startup.


### Added
- **Water PDF extractor — Aguas Andinas** (`attribute_extractor.py`):
  New function `_extract_water_pdf_attributes` and its companion regex
  constants, registered in `_extract_pdf_type_specific_attributes` for
  `SERVICE_TYPE_WATER`.

  Key observations (reference PDF: February 2026):
  - pdfminer reads two-column table sections column-by-column (labels first,
    then values), so label-based lookups with short windows fail; each
    pattern is anchored to a landmark visible in the pdfminer output.
  - Address spans two lines after `SEÑOR RESIDENTE` in the header.
  - Account number follows the `Nro de cuenta` label (with a blank line)
    at the bottom of the bill.
  - Due date follows the `VENCIMIENTO` label in the header.
  - CONSUMO TOTAL value is the last m³ entry in the readings block before
    `MODALIDAD DE PRORRATEO`.
  - Tariff rates appear in a published-rates block as `Label = $ value`.

  | Attribute | Source phrase | Value | Type |
  |---|---|---|---|
  | `address` | `SEÑOR RESIDENTE\nGENERAL JOFRE  385-515\nSANTIAGO` | `"GENERAL JOFRE 385-515 SANTIAGO"` | str |
  | `customer_number` | `Nro de cuenta\n\n1630935-4` | `"1630935-4"` | str |
  | `due_date` | `VENCIMIENTO\n\n21-MAR-2026` | `"21-MAR-2026"` | str |
  | `consumption` | `CONSUMO TOTAL … 10,98 m3` | `10.98` | float |
  | `consumption_unit` | `CONSUMO TOTAL … 10,98 m3` | `"m3"` | str |
  | `total_amount` | `TOTAL A PAGAR\n\n$ 15.700` | `15700` | int |
  | `fixed_charge` | `Cargo fijo = $ 914` | `914` | int |
  | `cubic_meter_peak_water_cost` | `Metro cúbico agua potable punta = $ 585,32` | `585.32` | float |
  | `cubic_meter_non_peak_water_cost` | `Metro cúbico agua potable no punta = $ 585,28` | `585.28` | float |
  | `cubic_meter_overconsumption` | `Metro cúbico sobreconsumo = $ 1.679,38` | `1679.38` | float |
  | `cubic_meter_collection` | `Metro cúbico recolección = $ 446,45` | `446.45` | float |
  | `cubic_meter_treatment` | `Metro cúbico tratamiento = $ 306,45` | `306.45` | float |

  New regex constants: `_WATER_AA_PDF_ADDRESS_RE`, `_WATER_AA_PDF_ACCOUNT_RE`,
  `_WATER_AA_PDF_DUE_DATE_RE`, `_WATER_AA_PDF_CONSUMO_LABEL_RE`,
  `_WATER_AA_TARIFF_AMT`, `_WATER_AA_PDF_FIXED_CHARGE_RE`,
  `_WATER_AA_PDF_PEAK_COST_RE`, `_WATER_AA_PDF_NON_PEAK_COST_RE`,
  `_WATER_AA_PDF_OVERCONSUMPTION_RE`, `_WATER_AA_PDF_COLLECTION_RE`,
  `_WATER_AA_PDF_TREATMENT_RE`.


## [0.6.4] - 2026-03-17

### Added
- **5 new electricity-specific PDF attributes** (`attribute_extractor.py`):
  All extracted from the Enel PDF header block that appears at the very
  beginning of the extracted text.

  | Attribute | Source phrase | Value |
  |---|---|---|
  | `tariff_code` | `Tipo de tarifa contratada: BT1-T2` | `"BT1-T2"` |
  | `connected_power` | `Potencia conectada: 2,500 kW` | `2500` (int) |
  | `connected_power_unit` | `Potencia conectada: 2,500 kW` | `"kW"` |
  | `area` | `Área Típica: AREA 1 S Caso 3 (a)` | `"AREA 1 S Caso 3 (a)"` |
  | `substation` | `Subestación: SAN CRISTOBAL` | `"SAN CRISTOBAL"` |

  New regexes: `_ELEC_PDF_TARIFF_CODE_RE`, `_ELEC_PDF_CONNECTED_POWER_RE`,
  `_ELEC_PDF_AREA_RE`, `_ELEC_PDF_SUBSTATION_RE`.

  `connected_power` is parsed by `_parse_amount_to_int` which correctly
  handles the Chilean format ``2,500`` → ``2500``.

### Changed
- **`_ELECTRICITY_ATTR_DEFAULTS`** (`sensor.py`): Added `tariff_code` (default
  `0`), `connected_power` (default `0`), `connected_power_unit` (default `0`),
  `area` (default `0`), `substation` (default `0`) to the electricity sensor's
  attribute set.
- **`manifest.json`**: Version bumped to `0.6.4`.

## [0.6.3] - 2026-03-17

### Changed
- **Service-type-specific attribute sets** (`sensor.py`): Each service sensor
  now exposes only the attributes relevant to its service type, instead of
  showing every attribute (gas + electricity) regardless of type.

  - `_STANDARD_ATTRS` replaced by:
    - `_COMMON_ATTRS` — universal attributes present for every service type
      (folio, billing dates, customer number, address, due date, total amount,
      consumption, consumption unit).
    - `_GAS_ATTR_DEFAULTS` — gas-only defaults: `cost_per_m3s`.
    - `_ELECTRICITY_ATTR_DEFAULTS` — electricity-only defaults:
      `service_administration`, `electricity_transport`, `stabilization_fund`,
      `electricity_consumption`, `cost_per_kwh`.
    - `_SERVICE_TYPE_ATTR_DEFAULTS` — mapping `service_type → defaults dict`,
      making it straightforward to add water-specific attributes later.
  - `extra_state_attributes` initialises universal defaults then adds
    service-type-specific defaults only for the matching type.  Extracted
    values are applied to both sets independently.
  - **Gas sensor** exposes 16 attributes (15 universal + `cost_per_m3s`).
  - **Electricity sensor** exposes 20 attributes (15 universal + 5 electricity).
  - **Water / unknown sensors** expose only the 15 universal attributes.
- **`manifest.json`**: Version bumped to `0.6.3`.



### Added
- **Electricity PDF extractor for Enel Distribución Chile** (`attribute_extractor.py`):
  A new dedicated `_extract_electricity_pdf_attributes(text)` extracts attributes
  that are only present in the Enel PDF bill, not in the notification email.

  Key implementation detail: pdfminer reads the billing breakdown table as three
  separate column blocks (labels / `$` signs / amounts), so the extractor uses a
  single multi-line regex (`_ELEC_PDF_TABLE_RE`) that matches the full block in
  column order rather than expecting label+amount on the same line.

  New attributes extracted from the PDF:
  - `billing_period_start` / `billing_period_end` – billing period dates
    (DD-MM-YYYY), parsed from the Spanish-month format
    "30 Dic 2025 - 29 Ene 2026" (primary) or the DD/MM/YYYY
    "Período de lectura: 30/12/2025 - 29/01/2026" (fallback).
  - `consumption` / `consumption_unit` – energy consumed confirmed from
    the PDF (e.g. `505.0` / `"kWh"`).
  - `service_administration` – administration fee in integer CLP.
  - `electricity_consumption` – cost of consumed electricity in integer CLP.
  - `electricity_transport` – electricity transport charge in integer CLP.
  - `stabilization_fund` – stabilisation fund charge in integer CLP.
  - `cost_per_kwh` – `electricity_consumption / consumption` (float, 2 dp).

- **`_ELEC_PDF_BILLING_PERIOD_RE`** – regex for Spanish-month date range.
- **`_ELEC_PDF_PERIOD_LECTURA_RE`** – fallback regex for DD/MM/YYYY date range.
- **`_ELEC_PDF_TABLE_RE`** – column-aware regex for the Enel billing table.
- **`_SPANISH_MONTH_MAP`** / **`_parse_spanish_date`** – helpers for
  converting Spanish abbreviated month names to DD-MM-YYYY strings.

### Changed
- **`_extract_pdf_type_specific_attributes`** (`attribute_extractor.py`):
  Routes `SERVICE_TYPE_ELECTRICITY` to `_extract_electricity_pdf_attributes`.
- **PDF text cap** (`extract_attributes_from_pdf`): Increased from 15 000 to
  50 000 characters.  The Enel PDF has key data at ~29 000–31 000 chars; the
  old cap silently discarded all of it.
- **`_STANDARD_ATTRS`** / **`extra_state_attributes`** (`sensor.py`): Added
  `service_administration` (default `0`), `electricity_transport` (default `0`),
  `stabilization_fund` (default `0`), `electricity_consumption` (default `0`),
  and `cost_per_kwh` (default `0.0`) to the standard sensor attributes.
- **`manifest.json`**: Version bumped to `0.6.2`.


## [0.6.1] - 2026-03-17

### Changed
- **Dedicated PDF extractor per service type** (`attribute_extractor.py`):
  The PDF extraction pipeline now mirrors the email extraction pipeline with
  **separate extractor functions per service type**, instead of reusing the
  email extractors for PDF text.

  - **`_GAS_PDF_CONSUMPTION_LABELS`** *(new)*: regex for the Metrogas PDF
    consumption label (``gas\s+consumido`` with ``[:\s\(]+`` separator),
    kept separate from the email-body label patterns in
    ``_GAS_CONSUMPTION_LABELS``.
  - **`_extract_gas_pdf_attributes(text)`** *(new)*: dedicated PDF extractor
    for Metrogas gas bills.  Contains only the patterns that appear in the
    Metrogas PDF (``Gas consumido ( 5,95 m3s )``, ``$``-prefixed total
    amount) and calculates ``cost_per_m3s``.
  - **`_extract_pdf_type_specific_attributes(text, service_type)`** *(new)*:
    routing helper for PDF extractors, parallel to the existing
    ``_extract_type_specific_attributes`` (email router).  Service types
    without a PDF extractor return an empty dict.
  - **`_extract_gas_attributes(text)`** *(email-only)*: reverted to handle
    email bodies only — ``gas\s+consumido`` (PDF-only label) and the ``(``
    separator extension have been moved to the new PDF extractor.
  - **`extract_attributes_from_pdf`**: updated to call
    ``_extract_pdf_type_specific_attributes`` instead of the email router.
- **`manifest.json`**: Version bumped to `0.6.1`.

## [0.6.0] - 2026-03-17

### Added
- **PDF data extraction for gas service** (`attribute_extractor.py`):
  A new `extract_attributes_from_pdf()` function uses `pdfminer.six` to
  convert downloaded bill PDFs to plain text and applies the
  service-type-specific PDF extractor.  For Metrogas, the PDF is the
  authoritative source for gas consumption data not present in the
  notification email.

- **Gas consumption from Metrogas PDF** (`attribute_extractor.py`):
  The gas PDF extractor recognises the PDF label
  ``Gas consumido ( 5,95 m3s )`` and extracts the numeric value and unit.

- **`consumption_unit` correctly set to `"m3s"`** (`attribute_extractor.py`):
  The unit of measurement is extracted directly from the matched text
  (group 2 of `_GAS_CONSUMPTION_RE`) instead of being hard-coded, so
  ``"m3s"`` (Metrogas standardised cubic metres) is returned for Metrogas
  PDFs while ``"m3"`` or ``"m³"`` is still returned for other formats.

- **`cost_per_m3s` attribute** (`attribute_extractor.py`, `sensor.py`):
  A new derived attribute ``cost_per_m3s`` (cost per standardised cubic metre)
  is calculated as ``round(total_amount / consumption, 2)`` whenever both
  values are available and consumption is positive.  It is exposed as a
  standard sensor attribute with a default value of ``0.0``.

- **`pdfminer.six` dependency** (`manifest.json`):
  Added ``pdfminer.six>=20221105`` to the integration's pip requirements so
  that Home Assistant installs the library automatically on first load.

### Changed
- **`_GAS_CONSUMPTION_RE`** (`attribute_extractor.py`): Extended to capture
  the unit string (group 2: ``m3s`` / ``m3`` / ``m³``) alongside the
  numeric value (group 1), enabling precise unit reporting.
- **`sensor.py`**: After a bill PDF is downloaded, `extract_attributes_from_pdf`
  is called automatically and its results are merged into the service
  attributes (PDF values take precedence over email-derived values for the
  same keys).  `cost_per_m3s` added to `_STANDARD_ATTRS` with default `0.0`.
- **`manifest.json`**: Version bumped to `0.6.0`.


### Added
- **Richer PDF link detection in email bodies** (`pdf_downloader.py`):
  When no PDF attachment is found, the integration now searches for billing
  PDF links using an improved three-tier heuristic:

  1. **Keyword match on visible text or `<img alt>`** — links whose human-
     readable label (including the `alt` text of button images) matches
     expanded Spanish/English billing vocabulary (*"ver boleta"*, *"revisar
     tu factura"*, *"descargar comprobante"*, *"visualizar documento"*,
     *"view invoice"*, etc.).
  2. **`.pdf` href suffix** — links whose URL ends in `.pdf` (with optional
     query string).
  3. **Billing-term href match** — links whose URL contains billing-related
     path segments or query parameters (*pdf*, *boleta*, *factura*,
     *invoice*, *bill*, *comprobante*, *descargar*, *download*, etc.)
     anywhere in the URL.

- **Plain-text body search** (`pdf_downloader.py`): A new **Strategy 3**
  scans `text/plain` MIME parts (or plain-text-only emails) for bare
  HTTP/HTTPS URLs that reference a PDF or contain billing-related terms.
  This covers services that send only a text email with a link rather than
  styled HTML.

- **Image-link support** (`_LinkExtractor`): The HTML parser now captures
  the `alt` attribute of `<img>` tags nested inside `<a>` elements, so
  image-only buttons (e.g.
  `<a href="…"><img alt="Ver boleta" …></a>`) are treated the same as
  text links.

- **Expanded billing keywords** (`_PDF_LINK_KEYWORDS`): Added *"revisar
  boleta/factura"*, *"ver comprobante"*, *"descargar comprobante"*,
  *"bajar / obtener pdf"*, *"imprimir boleta/factura"*, *"visualizar
  documento"*, *"ver cobro"*, and broader English patterns
  (*"view invoice/bill/statement/receipt"*, *"get invoice/bill/pdf"*).

### Changed
- **`manifest.json`**: Version bumped to `0.5.6`.
- **`_HTTP_USER_AGENT`**: Updated to reflect the new version string
  (`ConciergeHAIntegration/0.5.6`).
- **`_download_first_valid_pdf` helper extracted**: The fetch-and-validate
  loop shared by HTML and plain-text strategies is now a private helper
  function (`_download_first_valid_pdf`) to eliminate code duplication.

## [0.5.5] - 2026-03-08

### Added
- **Local brand images for HA 2026.3+** (`brand/`): Starting with Home Assistant
  2026.3, custom integrations can ship their own brand images directly inside the
  integration directory.  The `brand/` folder now contains both `icon.png` and
  `icon@2x.png` so that the integration icon is served through the new local
  brands-proxy API (`/api/brands/integration/{domain}/{image}`) without requiring
  a separate submission to the `home-assistant/brands` repository.  No changes to
  `manifest.json` or any Python file are needed — placing the images in `brand/`
  is sufficient.

### Changed
- **`manifest.json`**: Version bumped to `0.5.5`.

## [0.5.4] - 2026-03-08

### Fixed
- **Integration UI buttons not translated** (`manifest.json`): `integration_type` was
  set to `"service"`, which caused Home Assistant to fall back to the English
  `strings.json` for all integration-specific UI strings (subentry labels, button text,
  device-type badges). The value has been restored to `"hub"` — as it was first set in
  v0.4.2 — so that HA renders the proper CONFIGURE + ADD DEVICE card and applies the
  integration's own translations (including `es.json`) for all subentry labels.
  Concretely:
  - **"+ Add Service Device"** button now shows as **"+ Agregar Dispositivo de Servicio"**
    in Spanish (and in any other supported locale).
  - **"Service Device"** type badge shown under each subentry group now reads
    **"Dispositivo de Servicio"** in Spanish.
- **"Agregar servicio" button hidden when already configured**: With `integration_type:
  "hub"` and `single_config_entry: true`, Home Assistant no longer shows the "Add new
  instance" button on the integration card once an email account is already configured.
  The `async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured()` guard provides
  an additional code-level safeguard.
- **`AttributeError: 'ConciergeServicesCoordinator' object has no attribute 'get'`**
  (`sensor.py`): `async_setup_entry` in the sensor platform was assigning the
  coordinator object directly to `hass.data[DOMAIN][entry_id]`, overwriting the
  plain dict (containing the `pending_discoveries` set) that
  `__init__.py`'s `async_setup_entry` had just initialised at the same key.
  When the background discovery task subsequently called
  `.get(_PENDING_DISCOVERIES_KEY, set())` on what it expected to be a dict, it
  received a `ConciergeServicesCoordinator` instance instead and crashed.

  Fix: the coordinator is now stored under a `"coordinator"` sub-key
  (`hass.data[DOMAIN][entry_id]["coordinator"]`) so the pending-discoveries dict
  is preserved and the discovery task runs without errors.

## [0.5.2] - 2026-03-07

### Added
- **IMAP-based service discovery** (`__init__.py`, `config_flow.py`): Service
  accounts are now detected automatically via "discovery" and surface in
  **Configuration → Integrations** as devices available to be added — similar
  to how MQTT component discovery works.

  *How it works*:
  1. After the integration is set up, a background task scans the IMAP inbox
     immediately and then repeats every **hour**.
  2. For each detected service that is not already configured as a subentry, the
     integration initiates a subentry discovery flow
     (`hass.config_entries.subentries.async_init`).  Requires **HA 2025.4 or
     newer** (the subentry discovery API was added in that release); on older
     versions the background scan runs silently without triggering flows.
  3. The user sees the discovered device in the integration card and can confirm
     or dismiss it with a single click via the new **"Discovered: {service_name}"**
     confirmation step (`async_step_discovery_confirm`).
  4. Confirming adds the subentry directly using `async_add_subentry` (the
     HA-recommended path for discovery-initiated subentries) and reloads the
     entry so the sensor appears immediately.

- **`async_step_discovery` / `async_step_discovery_confirm`** steps added to
  `ServiceSubentryFlowHandler` (`config_flow.py`): handle the discovery source
  context, check for duplicates (already-configured and already-pending
  discoveries are silently aborted), and confirm the addition with a
  description that includes the service name and e-mail count.

- New string keys (`strings.json`, `translations/en.json`,
  `translations/es.json`):
  - `config_subentries.service.initiate_flow.discovery` — label for the
    discovery-triggered flow card.
  - `config_subentries.service.step.discovery_confirm` — confirmation form.
  - `config_subentries.service.abort.already_configured` — shown when the
    service is already set up.
  - `config_subentries.service.abort.subentry_added` — shown after a
    successful discovery confirmation.

## [0.5.1] - 2026-03-07

### Fixed
- **"Dispositivos que no pertenecen a una subentrada" grouping** (`sensor.py`):
  The connection/status sensor no longer creates a hub device associated with
  the main config entry.  Removing `device_info` from
  `ConciergeServicesConnectionSensor` means no device is registered for the
  main entry, so the "Devices that don't belong to a sub-entry" section is
  no longer shown in the integration page.  The status sensor remains fully
  functional as a standalone entity.
- **Removed `via_device` from service sensors** (`sensor.py`): Each service
  sensor device now stands independently under its own subentry without
  being linked to a (now-removed) hub device.

### Added
- **Automatic migration from v0.4.x** (`__init__.py`, `config_flow.py`):
  Upgrading from v0.4.x no longer requires deleting and re-adding the
  integration.  A `async_migrate_entry` migration (config entry minor
  version 1.1 → 1.2) runs automatically on first startup after the upgrade
  and performs three steps:
  1. Assigns the correct `config_subentry_id` to each service entity already
     in the entity registry.
  2. Removes the legacy hub device (previously associated with the main
     config entry).
  3. Moves each service device's registry association from the main config
     entry to its own subentry, so devices appear under the correct subentry
     group in the HA UI without any manual reconfiguration.

## [0.5.0] - 2026-03-07

### Added
- **Standard attributes with default values** (`sensor.py`): Every service
  sensor now exposes the full set of standard attributes on every update.
  If a value cannot be extracted from the email the attribute defaults to
  `0`, so automations and dashboards never see a missing key:
  `service_id`, `service_name`, `service_type`, `last_updated_datetime`,
  `folio`, `billing_period_start`, `billing_period_end`, `customer_number`,
  `address`, `due_date`, `icon`, `friendly_name`, `total_amount`,
  `consumption`, `consumption_unit`.
- **`icon` and `friendly_name` attributes** (`sensor.py`): Both are now
  included as extra-state attributes on every service sensor.
  `icon` is always `mdi:file-document-outline`; `friendly_name` mirrors
  the configured service name.

### Changed
- **`total_amount` is now an integer** (`attribute_extractor.py`): The
  extracted amount is converted to a plain integer, removing thousands
  separators regardless of locale format (e.g. Chilean `122.060` → `122060`,
  `12.013` → `12013`, `1.234,56` → `1234`).  A dot or comma followed by
  exactly 3 digits is treated as a thousands separator; followed by 1–2
  digits it is treated as a decimal separator (decimal part discarded).
- **`consumption` is now a float** (`attribute_extractor.py`): The extracted
  consumption value is converted to a Python `float` instead of being stored
  as a raw string.  A new `_parse_consumption_to_float()` helper handles both
  Chilean/Spanish format (dot = thousands separator, comma = decimal, e.g.
  `"1.500"` → `1500.0`, `"12,5"` → `12.5`) and English format (`"12.5"` →
  `12.5`).  The default value in the sensor attributes is `0.0`.
- **Unified consumption attributes** (`attribute_extractor.py`): Service-type
  specific fields (`consumption_m3`, `consumption_kwh`) have been replaced
  by the standard attributes `consumption` and `consumption_unit` (`"m3"` or
  `"kWh"` depending on the service type).
- **Removed non-standard attributes** (`attribute_extractor.py`): The
  following service-specific fields are no longer extracted or exposed as
  they will be re-introduced as typed specific attributes in a future
  version: `next_billing_period_start`, `next_billing_period_end`,
  `consumption_type`, `boleta_date`, `metropuntos`, `contracted_power_kw`,
  `rut_from_subject`.
- **Fixed duplicate device registration** (`sensor.py`): Service sensor
  entities are now registered with their respective `config_subentry_id` via
  `async_add_entities(..., config_subentry_id=subentry_id)`.  This causes
  each service device to appear correctly nested under its subentry in the
  Home Assistant device registry (matching the MQTT integration layout
  shown in screenshot-02) and eliminates the "Dispositivos que no
  pertenecen a una subentrada" grouping.

### Removed
- `attributes_extracted_count` debug attribute from service sensors.

## [0.4.10] - 2026-03-06

### Added
- **Heuristic PDF downloader** (`pdf_downloader.py`): New module that locates
  and saves the billing PDF for each matched email using a two-strategy
  heuristic:
  1. **PDF attachment** — walks MIME parts looking for `application/pdf`
     content-type or a filename ending in `.pdf`; saves the raw bytes.
  2. **Link in HTML body** — if no attachment is found, parses `text/html`
     parts with a lightweight `HTMLParser` subclass and collects `<a href>`
     candidates. Priority is given to links whose visible text matches
     Spanish/English billing keywords (*"ver boleta"*, *"descargue su
     boleta"*, *"ver factura"*, *"descargar pdf"*, etc.). A secondary pass
     collects links whose `href` ends in `.pdf`. Each candidate URL is
     fetched; the response is validated by magic-byte check (`%PDF`) and/or
     `Content-Type` header before the file is written.
- **Intelligent filename scheme**: Downloaded PDFs follow a deterministic
  naming convention so that re-running on the same bill produces the same
  filename and the download is skipped if the file already exists:
  ```
  {service_id}_{YYYY}-{MM}_{folio}.pdf   # folio available
  {service_id}_{YYYY}-{MM}.pdf           # folio not extracted
  ```
  `YYYY-MM` comes from `billing_period_start` (extracted by the attribute
  extractor) with a fallback to the email's `Date` header.
- **Automatic PDF purge**: `purge_old_pdfs()` removes files older than
  `PDF_MAX_AGE_DAYS` (365 days) from the download directory. It is called
  automatically once per coordinator update cycle (every 30 minutes).
- **PDF storage constants** (`const.py`): `PDF_SUBDIR` and `PDF_MAX_AGE_DAYS`
  centralise configuration. PDFs are stored under
  `{ha_config_dir}/concierge_ha_integration/pdfs/`.
- **`pdf_path` sensor attribute** (`sensor.py`): When a PDF is successfully
  downloaded its absolute path is exposed as the `pdf_path` extra-state
  attribute on the service sensor.

### Changed
- **`manifest.json`**: Version bumped to `0.4.10`.

## [0.4.8] - 2026-03-03

### Fixed
- **Enel and Metrogas not detected during service discovery**
  (`service_detector.py`, `sensor.py`): The integration required emails to
  have file attachments before evaluating them as billing emails.  Both Enel
  and Metrogas send HTML notification emails *without* PDF attachments (the
  bill is downloaded via a link); only Aguas Andinas attaches the PDF
  directly.  Removed the `_has_attachments` gate from the service detector
  and from the sensor's email-matching loop so that attachment-free billing
  emails are evaluated correctly.
- **Enel and Metrogas emails not matched by sensor after detection**
  (`sensor.py` `_matches_service`): Both emails arrive as Gmail-forwarded
  messages (`From: user@gmail.com`), so the sender-domain check was
  matching the generic domain `"gmail"` against *every* forwarded email,
  causing cross-service matches and missing matches.  Three targeted fixes:
  1. Domain matching now skips known generic webmail providers (gmail,
     hotmail, yahoo, outlook, live, icloud, protonmail).
  2. Service-name matching now correctly handles short names like `"Gas"`
     (previously the `0 >= 0` condition always returned `True`, matching
     every email regardless of content).
  3. A new subject-keyword check extracts company-specific words from the
     stored `sample_subject` (e.g. `"enel"` from
     `"Fwd: Cuenta Enel de este mes …"` or `"metrogas"` from
     `"Fwd: Boleta Metrogas Nro. …"`) and requires at least one of them to
     appear in the candidate email, reliably distinguishing Enel from
     Metrogas even when both are forwarded from the same Gmail account.

### Changed
- **`manifest.json`**: Version bumped to `0.4.8`.

## [0.4.7] - 2026-03-03

### Fixed
- **Duplicate sensors per service account**: `manifest.json` declared
  `"integration_type": "hub"`, which caused Home Assistant to register
  two sensor entities for every service account subentry.  Changed to
  `"integration_type": "service"` (matching the MQTT integration pattern
  described in the architecture).
- **"Invalid handler specified" error on config flow**: `const.py` still
  contained the old domain `"concierge_services"` after the integration was
  renamed to `"concierge_ha_integration"`.  Home Assistant looks up the config
  flow handler by `DOMAIN`, so the mismatch between `const.py` and
  `manifest.json` / the component folder caused the config flow to fail to
  load.  Updated `DOMAIN` in `const.py` to `"concierge_ha_integration"`.

### Changed
- **`manifest.json`**: Version bumped to `0.4.7`.

## [0.4.6] - 2026-03-01

### Changed
- **`manifest.json`**: Version bumped to `0.4.6`.
- Integration name revamped to **Concierge HA Integration**

## [0.4.5] - 2026-02-26

### Fixed
- **`TypeError` when reconfiguring a service subentry** (`config_flow.py`):
  `ServiceSubentryFlowHandler.async_step_reconfigure` was calling
  `self.async_update_and_abort()` without the two required positional arguments
  `entry` and `subentry`.  Added `self._get_entry()` and the already-retrieved
  `subentry` object as the first two arguments so the call matches the
  `ConfigSubentryFlow.async_update_and_abort(entry, subentry, ...)` signature.

### Changed
- **`manifest.json`**: Version bumped to `0.4.5`.

## [0.4.4] - 2026-02-25

### Fixed
- **`ValueError` when reconfiguring a service account** (`config_flow.py`):
  `ServiceSubentryFlowHandler.async_step_reconfigure` was calling `self.async_create_entry()`
  which is only valid when `source == user`.  For reconfigure flows the correct method is
  `self.async_update_and_abort()`, which updates the subentry data in-place and closes the
  flow without creating a duplicate entry.
- **Service entity shows no data after being added** (`sensor.py`):
  `_find_latest_email_for_service` was only scanning the last 50 emails in the inbox,
  while `service_detector.py` scans the last 100.  When the most recent billing email for
  a service was beyond the first 50, the sensor would not find it and the entity state
  would remain empty.  The limit has been raised to 100 to match the detector.
  A `WARNING`-level log message is now emitted when no matching email is found for a
  service, making future diagnosis visible in the HA log without requiring debug logging.

### Changed
- **`manifest.json`**: Version bumped to `0.4.4`.



### Fixed
- **`AttributeError` when adding a service device** (`config_flow.py`): `ServiceSubentryFlowHandler`
  was accessing `self._config_entry`, a private attribute that does not exist on Home Assistant's
  `ConfigSubentryFlow` base class. This caused a 500 error every time the **ADD DEVICE** button
  was clicked.
  - `async_step_user`: replaced `self._config_entry` with `self._get_entry()`.
  - `async_step_reconfigure`: replaced `self._config_entry.subentries[self._subentry_id]` with
    `self._get_reconfigure_subentry()`.

### Changed
- **`manifest.json`**: Version bumped to `0.4.3`.

## [0.4.2] - 2026-02-23

### Added
- **`integration_type: "hub"`** (`manifest.json`): Marks the integration as a hub so Home
  Assistant displays it like the MQTT integration — with a **CONFIGURE** button and an
  **ADD DEVICE** button on the integration card.
- **`single_config_entry: true`** (`manifest.json`): Only one Concierge Services instance
  (one monitored email account) is allowed at a time.
- **Options Flow** (`config_flow.py` → `OptionsFlowHandler`): The **CONFIGURE** button
  opens a pre-filled form to reconfigure the IMAP credentials and friendly name without
  deleting and re-adding the integration.
- **Subentry Flow** (`config_flow.py` → `ServiceSubentryFlowHandler`): The **ADD DEVICE**
  button scans the inbox, filters out services already added, and lets the user select one
  new service account to add as a device. Each device also supports a **reconfigure** step
  so the service name can be updated via the UI.
- **New constants** (`const.py`): `CONF_SERVICE_ID`, `CONF_SERVICE_NAME`,
  `CONF_SERVICE_TYPE`, `CONF_SAMPLE_FROM`, `CONF_SAMPLE_SUBJECT` — used as keys in
  subentry data instead of the old flat `services_metadata` dict.
- **Subentry strings** (`strings.json`, `translations/en.json`, `translations/es.json`):
  New `config_subentries.service` section with step, error, and abort messages for the
  add-device and reconfigure flows.

### Changed
- **Initial config flow** (`config_flow.py`): Simplified to two steps only — IMAP
  credentials (`user`) and friendly name (`finalize`). Service detection during initial
  setup has been removed; services are added individually after setup via ADD DEVICE.
- **`sensor.py`**: `async_setup_entry` now iterates over `config_entry.subentries` to
  create service sensors, instead of reading from the old `services` / `services_metadata`
  keys in `config_entry.data`. The coordinator reads effective credentials from
  `{**entry.data, **entry.options}` so options-flow changes are respected without
  needing to reconfigure from scratch.
- **`__init__.py`**: Removed explicit device-registry calls (devices are created via
  `DeviceInfo` in the sensor entities). Added an `add_update_listener` so the entry
  reloads automatically when options or subentries change.
- **`manifest.json`**: Version bumped to `0.4.2`.

### Removed
- `CONF_SERVICES` constant from `const.py` (replaced by individual subentries).
- `area_id` support from the initial config flow (areas can still be assigned from the
  device card after setup).
- `detect_services` and `select_services` steps from the initial config flow.

### Migration note
Existing config entries from v0.4.0/v0.4.1 will continue to load (the connection sensor
works without subentries). Previously configured service sensors will not appear
automatically — use the **ADD DEVICE** button to re-add them as subentries.


### Added
- **Service-Type Constants** (`const.py`): Added `SERVICE_TYPE_WATER`, `SERVICE_TYPE_GAS`,
  `SERVICE_TYPE_ELECTRICITY`, `SERVICE_TYPE_TELECOM`, and `SERVICE_TYPE_UNKNOWN` constants
  to classify detected services.

- **Modular Type-Specific Extractors** (`attribute_extractor.py`): Extraction tools are
  now organised by service type so each utility category can use patterns tuned to its
  own email format:

  | Service type  | Extra attributes extracted |
  |---|---|
  | `water`       | `address` + `customer_number` (packed-values), `consumption_m3`, `meter_reading`, `meter_number` |
  | `gas`         | `total_amount` (plain-number override), `metropuntos`, `consumption_m3` (label-based) |
  | `electricity` | `folio`, `boleta_date`, `address` (from `ubicado en`), `consumption_kwh`, `consumption_type`, `next_billing_period_start/end` |

- **`due_date` extraction** (`attribute_extractor.py`): New generic extractor
  (`_extract_due_date`) searches for `Fecha de vencimiento` label — confirmed in
  both Metrogas and Enel emails.

- **`_extract_type_specific_attributes` routing helper** (`attribute_extractor.py`):
  Dispatches to the correct type-specific extractor based on `service_type`.

- **`classify_service_type` utility** (`service_detector.py`): Public function that
  infers the service type from the email `From` address and `Subject` line.

### Changed
- **`_strip_html`** (`attribute_extractor.py`): Now applies `html.unescape()` a second
  time after the HTML parser so that double-encoded entities (`&amp;oacute;` →
  `&oacute;` → `ó`) found in Aguas Andinas emails are fully decoded.

- **`_CUSTOMER_LABELS`** (`attribute_extractor.py`): Made `de` optional so both
  `Número de Cliente:` and `Número Cliente:` (Metrogas) are matched.

- **`_extract_from_subject` folio patterns** (`attribute_extractor.py`): Added
  `r"nro\.?\s+([0-9]{6,})"` for the Metrogas `Boleta Metrogas Nro. NNNNNN` format.

- **`SERVICE_PATTERNS`** (`service_detector.py`): 3-tuples with service type added.

- **`DetectedService` dataclass** (`service_detector.py`): Added `service_type` field.

- **`ConciergeServiceSensor.extra_state_attributes`** (`sensor.py`): Exposes
  `service_type`; also filters `None` attribute values so fields cleared by
  type-specific extractors are omitted from the HA UI rather than shown as `null`.

- **Config flow** (`config_flow.py`): Stores `service_type` in `services_metadata`.

### Water extractor — Aguas Andinas (reference email: February 2026)
The Aguas Andinas HTML-only email uses a two-column table layout: labels
(`Dirección:`, `Número de Cuenta:`, `Período de Facturación:`) are in the left `<td>`;
all values are packed in the right `<td>` as a single paragraph:
`ADDRESS    ACCOUNT_NUM    DATE al DATE`.

| Root cause | Fix |
|---|---|
| `&amp;oacute;` double-encoded HTML entities | `_strip_html` applies `html.unescape()` twice |
| Labels and values in separate `<td>` — generic label extractor gives wrong results | `_WATER_AA_PACKED_RE` detects the ALL-CAPS address + `\d{5,}-\d` account pattern; results override generic values via `update()` |

Fields now correctly extracted: `billing_period_start/end` ✓ `total_amount` ✓
`address` ✓ (was `'Número de Cuenta:'`) `customer_number` ✓ (was street number `'385-515'`)

### Gas extractor — Metrogas (reference email: January 2026)
- Folio from subject `Nro.` pattern, plain-number total, `metropuntos` loyalty points.
- Gas consumption (m³) is not in the email body — only in the PDF attachment.

### Electricity extractor — Enel Distribución Chile (reference email: February 2026)
The Enel email has both `text/plain` and `text/html` parts; extractor uses plain text.

| What we learned | How it's handled |
|---|---|
| Invoice number in body: `N° Boleta 361692435 del 02-02-2026` | `_ELEC_ENEL_FOLIO_RE` + `_ELEC_ENEL_BOLETA_DATE_RE` → `folio` + `boleta_date` |
| Address follows `ubicado en` (not `Dirección:`) | `_ELEC_ENEL_ADDRESS_RE` → `address` |
| No current billing period in email; first two dates are boleta date + due date (WRONG) | Electricity extractor sets `billing_period_start/end = None`; sensor filters `None` values |
| `Próximo periodo de facturación` = NEXT billing period | `_ELEC_ENEL_NEXT_PERIOD_RE` → `next_billing_period_start/end` |
| `Consumo real` / `Consumo estimado` quality flag | `_ELEC_ENEL_CONSUMPTION_TYPE_RE` → `consumption_type` |
| `505 kWh` in email body | bare-kWh fallback → `consumption_kwh` |

## [0.3.2] - 2026-02-22

### Changed
- **Targeted Attribute Extraction**: Replaced broad heuristic email parsing with a
  focused extractor that produces exactly the fields needed before PDF analysis:

  | Attribute | Description |
  |---|---|
  | `service_name` | Utility company name (from sensor metadata) |
  | `folio` | Invoice/folio number (extracted from subject; confirmed later by PDF) |
  | `billing_period_start` | Start date of the billing period |
  | `billing_period_end` | End date of the billing period |
  | `total_amount` | Total amount due |
  | `customer_number` | Customer / account number |
  | `address` | Service address |
  | `last_updated_datetime` | Date the company sent the email (from `Date` header) |

- **HTML Body Handling**: Email body extractor now prefers `text/plain` parts;
  falls back to `text/html` only after stripping tags via stdlib `html.parser`.

### Removed
- Generic heuristic extractors (`_extract_key_value_pairs`, `_extract_currency_amounts`,
  `_extract_ids`, `FIELD_INDICATORS`, `KEY_VALUE_PATTERNS`, etc.) — replaced by
  targeted extractors (`_extract_total_amount`, `_extract_customer_number`, `_extract_address`).
- Redundant `empresa` attribute (covered by `service_name`).

### Fixed
- `mypy` errors: added `assert config_entry is not None` guards in
  `_fetch_service_data` and `ConciergeServicesConnectionSensor.extra_state_attributes`.

## [0.3.0] - 2026-02-21

### Added
- **Service Detection Flow**: Integration now automatically detects service accounts from inbox during setup
- **Service Selection**: Users can now select which detected services to configure as devices
- **MQTT-Style Architecture**: Following Home Assistant's MQTT integration pattern:
  - Email account acts as the "service" (hub)
  - Service accounts act as "devices" linked to the hub
- **Multi-Step Configuration**: Enhanced setup flow with service detection and selection
- **Service Metadata Storage**: Detected services are stored with metadata for future updates

### Changed
- **Configuration Flow**: Added two new steps after email setup:
  1. Service detection (automatic scan of inbox)
  2. Service selection (choose which services to configure)
- **Device Creation**: Devices are now created for all selected services during initial setup
- **Sensor Platform**: Updated to properly handle configured services from config entry

### Fixed
- **Service Detection Issue**: Previously detected services were not being converted into devices
- **Device Creation**: Service devices are now properly created during integration setup

## [0.2.0] - 2026-02-18

### Added
- **Device Architecture**: Each service is now represented as a separate device in Home Assistant
- **Friendly Name Configuration**: Users can set a custom friendly name for the integration
- **Area Assignment**: Integration can be associated with a specific area during setup
- **Automatic Service Detection**: Services are detected automatically from email inbox
- **Heuristic Attribute Extraction**: Automatically extracts billing attributes from email content
  - Account numbers, invoice numbers (folio)
  - Total amounts, due dates, billing periods
  - Consumption data, addresses, RUT
  - Company names from email subject
  - Any structured data in email body
- **Device-per-Service**: Each detected service appears as its own device with sensors
- **Status Sensor**: Renamed to "Concierge Services - Status" for consistency
- **Two-Step Configuration Flow**:
  1. IMAP credentials (server, port, email, password)
  2. Finalize (friendly name and area selection)

### Changed
- **Config Flow Simplified**: Service selection removed from initial setup
- **Device Names**: Uses friendly name instead of email address
- **Sensor Naming**: Connection sensor now called "Concierge Services - Status"
- **Device Info**: All sensors now include proper device_info for grouping
- **Architecture**: Prepared for automatic service discovery and notifications

### Technical
- Added `DeviceInfo` to all sensor entities
- Device hierarchy with `via_device` linking service devices to main device
- Area assignment using Home Assistant's area registry
- Services metadata stored in config entry for future use
- Heuristic pattern matching for attribute extraction (40+ field indicators)
- Multi-language support for attribute detection (Spanish and English)

## [0.1.5] - 2026-02-18

### Added
- Mail server connection status sensor that displays "OK" or "Problem"
- Sensor checks IMAP connection every 30 minutes and reports status
- Sensor includes email, server, and port as attributes
- Enhanced configuration form with helpful placeholder text and examples
- Added data_description fields in strings.json and translations for better UX
- Suggested values for IMAP server (e.g., "imap.gmail.com") and email (e.g., "user@gmail.com")

## [0.1.0] - 2026-02-18

### Added
- Initial release of Concierge Services integration
- IMAP email account configuration through Home Assistant UI
- Real-time IMAP credential validation
- Secure credential storage using Home Assistant's storage system
- Multi-language support (English and Spanish)
- HACS compatibility for easy installation
- Configuration flow with user-friendly interface
- Support for major email providers (Gmail, Outlook, Yahoo)
- Basic integration structure and manifest


### Documentation
- Created comprehensive README with installation and configuration instructions
- Created CHANGELOG.md to track project changes
- Added MIT License
- Created HACS configuration file
- Added Spanish translations for the integration UI

### Technical Details
- Integration domain: `concierge_services`
- Configuration flow implementation using Home Assistant's config_flow
- Supports IMAP SSL/TLS connection on port 993
- IoT class: cloud_polling
