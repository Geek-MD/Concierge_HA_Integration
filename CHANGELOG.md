# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.3] - 2026-02-25

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
