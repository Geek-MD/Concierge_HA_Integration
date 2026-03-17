# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
