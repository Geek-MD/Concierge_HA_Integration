[![Geek-MD - Concierge HA Integration](https://img.shields.io/static/v1?label=Geek-MD&message=Concierge%20HA%20Integration&color=blue&logo=github)](https://github.com/Geek-MD/Concierge_HA_Integration)
[![Stars](https://img.shields.io/github/stars/Geek-MD/Concierge_HA_Integration?style=social)](https://github.com/Geek-MD/Concierge_HA_Integration)
[![Forks](https://img.shields.io/github/forks/Geek-MD/Concierge_HA_Integration?style=social)](https://github.com/Geek-MD/Concierge_HA_Integration)

[![GitHub Release](https://img.shields.io/github/release/Geek-MD/Concierge_HA_Integration?include_prereleases&sort=semver&color=blue)](https://github.com/Geek-MD/Concierge_HA_Integration/releases)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://github.com/Geek-MD/Concierge_HA_Integration/blob/main/LICENSE)
[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom%20Repository-blue)](https://hacs.xyz/)

[![Ruff + Mypy + Hassfest](https://github.com/Geek-MD/Concierge_HA_Integration/actions/workflows/ci.yaml/badge.svg)](https://github.com/Geek-MD/Concierge/actions/workflows/ci.yaml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

<img width="200" height="200" alt="image" src="https://github.com/Geek-MD/Concierge_HA_Integration/blob/main/custom_components/concierge_ha_integration/brand/icon.png?raw=true" />

# Concierge HA Integration

**Concierge HA Integration** is a custom integration for [Home Assistant](https://www.home-assistant.io) that allows you to manage utility bills (electricity, water, gas, etc.) received by email. The integration automatically detects services, extracts information from emails, and creates devices and sensors for each service with billing data.

> **🇨🇱 Geographic scope — Chile only**
> 
> Concierge HA Integration and Concierge are designed and tested exclusively for Chilean utility service accounts (Aguas Andinas, Enel, etc.). Billing email formats, field labels, and patterns are tuned for Chilean providers.

---

## ✨ Features

- 📧 **IMAP Email Configuration**: Connect your email account where you receive utility bills
- ✅ **Credential Validation**: Automatically verifies that IMAP credentials are correct
- 🔒 **Secure Storage**: Credentials are stored securely in Home Assistant
- 🌐 **Multi-language Support**: Complete interface in Spanish and English
- 🎯 **UI Configuration**: No YAML file editing required
- 🏠 **Friendly Names**: Set custom names for your integrations
- 📍 **Area Assignment**: Associate integrations with specific areas in your home
- 🔍 **Automatic Service Detection**: Detects utility services from your inbox automatically
- 🔎 **IMAP Discovery**: After setup, the integration automatically scans the inbox every hour for new services and surfaces them in **Configuration → Integrations** as devices available to be added — no manual "Add Device" click needed for discovered services (requires HA 2025.4 or newer)
- 📡 **Per-Service Entity Architecture** (v0.7.0+): Each configured service device
  exposes entities based on service type:

  **Gas (7 entities):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_consumption` | Sensor | — | m³ consumed |
  | `sensor.concierge_{id}_cost_per_unit` | Sensor | — | $/m³ |
  | `sensor.concierge_{id}_total_amount` | Sensor | — | Total bill amount (`$`) |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device; recomputes derived sensors as its final step |
  | `button.concierge_{id}_recalculate` | Button | Configuration | Recomputes formula-derived sensors from already-stored values (no email scan) |

  **Electricity (11 entities):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_consumption` | Sensor | — | kWh consumed |
  | `sensor.concierge_{id}_cost_per_unit` | Sensor | — | $/kWh |
  | `sensor.concierge_{id}_total_amount` | Sensor | — | Total bill amount (`$`) |
  | `sensor.concierge_{id}_service_administration` | Sensor | — | Administration fee (`$`) |
  | `sensor.concierge_{id}_electricity_transport` | Sensor | — | Electricity transport charge (`$`) |
  | `sensor.concierge_{id}_stabilization_fund` | Sensor | — | Stabilisation fund charge (`$`) |
  | `sensor.concierge_{id}_electricity_consumption` | Sensor | — | Cost of consumed electricity (`$`) |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device; recomputes derived sensors as its final step |
  | `button.concierge_{id}_recalculate` | Button | Configuration | Recomputes formula-derived sensors from already-stored values (no email scan) |

  **Water (15 entities — formula-derived sensors marked with ✦):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_consumption` | Sensor | — | m³ consumed |
  | `sensor.concierge_{id}_total_amount` ✦ | Sensor | — | Total bill amount (`$`) — `subtotal + other_charges` |
  | `sensor.concierge_{id}_fixed_charge` | Sensor | — | Fixed service charge (`$`) |
  | `sensor.concierge_{id}_cost_per_unit` ✦ | Sensor | — | Effective cost per m³ (`$/m³`) — `water_consumption / consumption` |
  | `sensor.concierge_{id}_cubic_meter_collection` ✦ | Sensor | — | Effective collection cost per m³ (`$/m³`) — `wastewater_recolection / consumption` |
  | `sensor.concierge_{id}_cubic_meter_treatment` ✦ | Sensor | — | Effective treatment cost per m³ (`$/m³`) — `wastewater_treatment / consumption` |
  | `sensor.concierge_{id}_water_consumption` | Sensor | — | Potable water charge (`$`) |
  | `sensor.concierge_{id}_wastewater_recolection` | Sensor | — | Wastewater collection charge (`$`) |
  | `sensor.concierge_{id}_wastewater_treatment` | Sensor | — | Wastewater treatment charge (`$`) |
  | `sensor.concierge_{id}_subtotal` ✦ | Sensor | — | Subtotal before surcharges (`$`) — `water_consumption + wastewater_recolection + wastewater_treatment + fixed_charge` |
  | `sensor.concierge_{id}_other_charges` | Sensor | — | Net surcharges (interest − rounding discount) (`$`) |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device; recomputes derived sensors as its final step |
  | `button.concierge_{id}_recalculate` | Button | Configuration | Recomputes formula-derived sensors from already-stored values (no email scan) |

  > ✦ Formula-derived — automatically recalculated when any constituent value is updated via `set_value` or a new bill is processed.

  **Common Expenses (14 entities — includes Hot Water sub-account):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_bill` | Sensor | — | GC apartment portion (`$`) — alícuota % of building expense |
  | `sensor.concierge_{id}_funds_provision` | Sensor | — | Funds provision amount (`$`) — Bill × Funds % / 100 |
  | `sensor.concierge_{id}_subtotal` | Sensor | — | Subtotal Departamento (`$`) — Bill + Funds Provision |
  | `sensor.concierge_{id}_fixed_charge` | Sensor | — | Cargo Fijo (`$`) |
  | `sensor.concierge_{id}_total` | Sensor | — | Total GC bill (`$`) — Subtotal + Cargo Fijo |
  | `sensor.concierge_{id}_hot_water_consumption` | Sensor | — | Hot Water consumption (`m³`) — from PDF Tier 1 |
  | `sensor.concierge_{id}_hot_water_cost_per_unit` | Sensor | — | Hot Water cost per m³ (`$/m³`) — from PDF Tier 1 |
  | `sensor.concierge_{id}_hot_water_amount` | Sensor | — | Hot Water charge (`$`) — from PDF Tier 1 or derived |
  | `sensor.concierge_{id}_hot_water_prev_reading` | Sensor | — | Hot Water previous meter reading (`m³`) — from PDF Tier 1 |
  | `sensor.concierge_{id}_hot_water_curr_reading` | Sensor | — | Hot Water current meter reading (`m³`) — from PDF Tier 1 |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device; recomputes derived sensors as its final step |
  | `button.concierge_{id}_recalculate` | Button | Configuration | Recomputes formula-derived sensors from already-stored values (no email scan) |

  > **Hot Water** is a sub-account billed within the Common Expenses PDF,
  > there is no separate email for it.  Its five sensors are
  > populated automatically from the PDF text layer (Tier 1).

- 🪵 **Structured Email-Processing Logs** (v1.2.2): Every time the integration scans the
  mailbox it emits detailed log entries so you can verify whether each email was detected
  and why — see [Logging & Diagnostics](#-logging--diagnostics) for details.
- 📘 **Home Assistant Logbook task entries** (v1.3.10): Key integration tasks
  (startup, discovery scans, automatic polling, force refresh, recalculate and
  `set_value`) are now written to the **Registro/Logbook** timeline under the
  dedicated domain `concierge_ha_tasks`.

- 📋 **Status Binary Sensor Attributes**: The `binary_sensor.concierge_{id}_status`
  entity always exposes the following attributes (missing values default to `0`):
  - Service identity: `service_id`, `service_name`, `service_type`, `friendly_name`, `icon`
  - Billing: `folio`, `billing_period_start`, `billing_period_end`, `customer_number`,
    `address`, `due_date`
  - When a PDF has been downloaded: `pdf_path`
  - **Electricity** extras: `tariff_code`, `connected_power`, `connected_power_unit`,
    `area`, `substation`, `pdf_url`
  - **Gas** extras: `pdf_url`
  - **Common Expenses** extras: `gross_common_expenses`, `gross_common_expenses_percentage`,
    `funds_provision_percentage`, `hot_water_amount`, `subtotal_consumo`,
    `previous_measure` (Agua Caliente prev meter reading), `actual_measure` (Agua Caliente curr meter reading)
- 📄 **Heuristic PDF Download**: Automatically downloads the billing PDF for each matched email:
  - If the email has a PDF attachment it is saved directly
  - Otherwise the HTML body is scanned for billing links (*"ver boleta"*, *"descargue su boleta"*, etc.) and the first valid PDF URL is downloaded
  - Files are saved as `{service_id}_{YYYY-MM}_{folio}.pdf` under `config/concierge_ha_integration/pdfs/`
  - The cache keeps the **5 most recent PDFs** (all services combined), deleting older files automatically
  - PDFs older than one year are purged automatically
  - The reconstructed bill download URL is also exposed as the `pdf_url` sensor attribute (electricity and gas sensors)
- 🔧 **Device Architecture**: Each service appears as a separate device
- 📊 **Status Sensor**: Monitor email connection status in real-time

### 🚧 Coming Soon

- 📱 **Service Configuration UI**: Edit detected services after initial discovery
- 📈 **Historical Data**: Track billing history over time

---

## 🔧 Services (Actions)

### `concierge_ha_integration.force_refresh`

Forces an immediate email reading and PDF analysis for a single service device,
bypassing the regular 30-minute polling interval.

| Field | Required | Selector | Description |
|---|---|---|---|
| `device_id` | ✅ | `device` (integration filter) | The Concierge service device to refresh. Only devices from this integration are shown in the picker. |

> **UI filter** — the device picker automatically filters to show **only** devices that belong to
> `concierge_ha_integration`.  Devices from other integrations or domains are never listed.

#### Usage examples

**Developer Tools → Actions:**
```yaml
action: concierge_ha_integration.force_refresh
data:
  device_id: "abc123def456abc123def456"   # HA device registry ID
```

**Automation / Script:**
```yaml
action: concierge_ha_integration.force_refresh
target: {}
data:
  device_id: "{{ device_id('sensor.concierge_enel_total_amount') }}"
```

#### Per-device button entity

Each service device also exposes a `button.concierge_{service_id}_force_refresh` entity
(category: **Configuration**).  Pressing the button from the device detail page
(**Settings → Devices & Services → *device name***) triggers the same targeted refresh
without any scripting or service call.  Its final step automatically calls the
*Recalculate* logic — see below.

---

### `concierge_ha_integration.recalculate`

Recomputes all formula-derived sensors for a Concierge service device using the
values **already stored** in the coordinator — without opening an IMAP connection
or downloading any PDF.

Useful after a manual `set_value` correction: instead of waiting for the next
polling cycle or pressing *Force Refresh*, pressing *Recalculate* immediately
propagates the corrected input value into all formula sensors (e.g. updating
`cargo_fijo` instantly recalculates `gc_total = subtotal_departamento + cargo_fijo`).

| Field | Required | Selector | Description |
|---|---|---|---|
| `device_id` | ✅ | `device` (integration filter) | The Concierge service device to recalculate. Only devices from this integration are shown. |

> **UI filter** — the device picker automatically filters to show **only** devices that
> belong to `concierge_ha_integration`.

#### Usage examples

**Developer Tools → Actions:**
```yaml
action: concierge_ha_integration.recalculate
data:
  device_id: "abc123def456abc123def456"   # HA device registry ID
```

**Automation / Script:**
```yaml
action: concierge_ha_integration.recalculate
data:
  device_id: "{{ device_id('sensor.concierge_common_expenses_total') }}"
```

#### Per-device button entity

Each service device exposes a `button.concierge_{service_id}_recalculate` entity
(category: **Configuration**).  Pressing it from the device detail page triggers
the recomputation immediately without any scripting or service call.

---

### `concierge_ha_integration.set_value`

Forces a specific value for a named attribute of a Concierge service entity and
applies it as a **manual override** in memory. The correction is applied
immediately. The overridden sensor will show
`extraction_confidence = 100`.

Formula-derived sensors (e.g. `sensor.concierge_gastos_comunes_total`, which equals
`subtotal_departamento + cargo_fijo`) are **automatically recalculated** when any of
their inputs change.

| Field | Required | Selector | Description |
|---|---|---|---|
| *(target)* | ✅ | `entity` (integration filter) | **Exactly one** entity belonging to the target Concierge service. Only entities from this integration are shown. |
| `attribute` | ❌ | `text` | Internal attribute key to override (e.g. `fixed_charge`, `gastos_comunes_amount`). When omitted the key is inferred automatically from the entity's unique_id. |
| `value` | ✅ | `text` | The correct value (e.g. `9638`). |

> **UI filter** — the entity picker automatically filters to show **only** entities that belong to
> `concierge_ha_integration`.  Entities from other integrations are never listed.

> **Single entity** — only one entity may be targeted per call.  Selecting multiple entities
> raises an error.

#### Usage examples

**Developer Tools → Actions:**
```yaml
action: concierge_ha_integration.set_value
target:
  entity_id: sensor.concierge_gastos_comunes_fixed_charge
data:
  attribute: fixed_charge   # optional — inferred from entity when omitted
  value: "9638"
```

**Automation / Script:**
```yaml
action: concierge_ha_integration.set_value
target:
  entity_id: sensor.concierge_gastos_comunes_fixed_charge
data:
  value: "9638"
```

---

## 📋 Prerequisites

### Hot Water extraction (Tier 1 + OCR fallback)

Five sensors under each **Gastos Comunes** device report Agua Caliente (hot water)
data extracted from the same "Nota de Cobro" PDF:
`hot_water_consumption`, `hot_water_cost_per_unit`, `hot_water_amount`,
`hot_water_prev_reading`, `hot_water_curr_reading`.

The integration first uses the PDF text layer (Tier 1). If that layer is empty
or incomplete, configure an **OCR.space API key** so it can fall back to OCR
instead of leaving the Gastos Comunes / Agua Caliente sensors as `unknown`.

#### How the extraction pipeline works

For every Gastos Comunes bill that arrives, the integration:

1. **Read PDF text layer (Tier 1)** — `pdfminer` extracts the embedded text.
2. **Fallback to OCR when needed** — if Tier 1 is incomplete, OCR.space is used
   to recover the missing Gastos Comunes / Agua Caliente values.
3. **Extract billing + hot-water fields** — values are parsed from the best
   available source.
4. **Finalize and derive fields** — aliases and computed values are applied
   (for example `gc_total`, `subtotal_consumo`, and fallback derivations).
5. **Sensors updated** — `hot_water_consumption`, `hot_water_cost_per_unit`,
   `hot_water_amount`, `hot_water_prev_reading`, and `hot_water_curr_reading`
   are written to Home Assistant.

---

## 📦 Installation

### Before you install

An OCR.space API key is optional but recommended for Gastos Comunes / Agua
Caliente, because some building PDFs do not expose a usable text layer and need
OCR fallback to avoid `unknown` sensors.

### Option 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations → Custom Repositories**
3. Add this repository:
   ```
   https://github.com/Geek-MD/Concierge_HA_Integration
   ```
   Select type: **Integration**
4. Install and restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and select **Concierge HA Integration**

---

### Option 2: Manual Installation

1. Download this repository
2. Copy the `custom_components/concierge_ha_integration/` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Add the integration through the UI and search for **Concierge HA Integration**

---

## ⚙️ Configuration

All configuration is done through the user interface:

### Step 1: IMAP Credentials

1. Go to **Settings** → **Devices & Services**
2. Click the **+ Add Integration** button
3. Search for **Concierge HA Integration**
4. Enter your email account details:
   - **IMAP Server**: Your IMAP email server
   - **IMAP Port**: The IMAP port (default: `993`)
   - **Email**: Your email address
   - **Password**: Your password or app password

### Step 2: Finalize Setup

After validating credentials, configure:
- **Friendly Name**: A descriptive name for this integration (e.g., "Home Bills", "Casa Principal")

### Step 3: Add Service Devices

Once the integration is set up, service devices can be added in two ways:

#### 🔎 Automatic Discovery (recommended — requires HA 2025.4+)
Right after setup the integration scans your inbox for service providers.
Discovered services appear on the **Concierge HA Integration** integration card as
**"Discovered: {service_name}"** — click the card to confirm and the device is added
automatically.  The scan repeats every hour so newly-arrived bills are noticed.

#### ➕ Manual Addition
Use the **ADD DEVICE** button on the integration card:
- The integration scans your inbox and shows available service providers
- Select a service to add it as a device
- Repeat for each service you want to track
- Each service can be reconfigured later via its device page

> **Note**: Only one Concierge HA Integration instance is allowed per Home Assistant installation
> (`single_config_entry`). To monitor a different email account, reconfigure the existing
> entry using the **CONFIGURE** button.

### Configuration Examples

#### Gmail
- **IMAP Server**: `imap.gmail.com`
- **IMAP Port**: `993`
- **Email**: `youremail@gmail.com`
- **Password**: Use an [app password](https://support.google.com/accounts/answer/185833)

#### Outlook/Hotmail
- **IMAP Server**: `outlook.office365.com`
- **IMAP Port**: `993`
- **Email**: `youremail@outlook.com`
- **Password**: Your account password

#### Yahoo Mail
- **IMAP Server**: `imap.mail.yahoo.com`
- **IMAP Port**: `993`
- **Email**: `youremail@yahoo.com`
- **Password**: Use an [app password](https://help.yahoo.com/kb/generate-manage-third-party-passwords-sln15241.html)

---

## 📊 What Gets Created

After configuration, the integration creates:

### Connection Sensor (standalone entity, no device)
- **Entity ID**: `sensor.concierge_services_status`
- **State**: `OK` or `Problem`
- **Attributes**: `email`, `imap_server`, `imap_port`

### Service Devices (Auto-detected or manually added)
One device per configured service (e.g., "Aguas Andinas", "Enel", "Metrogas"), each
with five entities:

#### Diagnostic: Status Binary Sensor
- **Entity ID**: `binary_sensor.concierge_{service_id}_status`
- **State**: `on` (Problem — no bill data found or last update older than 1 month) / `off` (OK — data retrieved within the last month)
- **Attributes**: billing metadata (folio, period, address, due date, pdf_path) and
  service-type-specific fields (pdf_url, electricity breakdowns, water components, etc.)

#### Diagnostic: Last Update Sensor
- **Entity ID**: `sensor.concierge_{service_id}_last_update`
- **State**: Full ISO 8601 datetime of the most recently processed bill

#### Consumption Sensor
- **Entity ID**: `sensor.concierge_{service_id}_consumption`
- **Unit**: `m³` (gas/water) or `kWh` (electricity)

#### Cost Per Unit Sensor
- **Entity ID**: `sensor.concierge_{service_id}_cost_per_unit`
- **Unit**: `$/m³` (gas) or `$/kWh` (electricity); `None` for water/unknown

#### Total Amount Sensor
- **Entity ID**: `sensor.concierge_{service_id}_total_amount`
- **Unit**: `$`

---

## 🚀 Development Status

- ✅ IMAP account configuration through UI
- ✅ Two-step configuration (credentials + friendly name)
- ✅ Real-time credential validation
- ✅ Secure credential storage
- ✅ Interface in Spanish and English
- ✅ HACS compatibility
- ✅ Status sensor: `sensor.concierge_services_status` (`OK` / `Problem`)
- ✅ Automatic service detection from inbox
- ✅ Support for detecting multiple service types (electricity, gas, water)
- ✅ Service-specific device creation via ADD DEVICE button
- ✅ MQTT-style architecture: email as hub, services as subentry devices
- ✅ Options flow: CONFIGURE button to update IMAP credentials without reinstalling
- ✅ Subentry reconfigure: update service name from the device page
- ✅ Automatic migration (v0.5.1): upgrading from older versions no longer requires reinstalling
- ✅ Automatic discovery (v0.5.2): inbox is scanned periodically; new services surface in the integration card for one-click confirmation (requires HA 2025.4+)
- ✅ Heuristic PDF download: attachment → billing link in HTML → plain-text URL
- ✅ Deterministic PDF filename: `{service_id}_{YYYY-MM}_{folio}.pdf`
- ✅ PDF cache retention: keeps only the 5 newest PDFs (all services combined)
- ✅ Automatic purge of PDFs older than 1 year
- ✅ Billing attribute extraction from email body and PDF (folio, billing period, amounts, consumption, customer number, address, due date, etc.)
- ✅ PDF content analysis: extracts structured billing data from downloaded PDFs (Enel, Metrogas)
- ✅ `pdf_url` attribute on electricity and gas status binary sensors — exposes the bill download URL; correctly populated even when the PDF is already cached (v0.7.10)
- ✅ Metrogas/fidelizador.com bill URL reliably extracted from the plain-text email body via the `[image: Ver boleta]` marker and raw QP-line reconstruction (v0.7.13)
- ✅ acepta.com Custodium multi-hop PDF download: follows the full chain (fidelizador tracking URL → outer wrapper → Custodium JS page → PdfView "no plugin" page → PDF); handles percent-encoded hrefs, extra rendering parameters, and root-relative paths (v0.7.15)
- ✅ Per-service entity architecture (v0.7.0): each service device exposes `binary_sensor.concierge_{id}_status` (Diagnostic) + `sensor.concierge_{id}_last_update` (Diagnostic) + `sensor.concierge_{id}_consumption` + `sensor.concierge_{id}_cost_per_unit` + `sensor.concierge_{id}_total_amount`
- ✅ `sensor.concierge_{id}_last_update` holds the full ISO 8601 bill datetime (v0.7.1)
- ✅ `set_value` manual-override service (v0.9.0): forces a correct value for any named attribute of a Concierge entity; entity picker is filtered to Concierge HA Integration only; `extraction_confidence` is set to 100 on overridden sensors (v0.9.3: entity selection moved to HA `target` so `attribute` and `value` render as proper form inputs in the UI; v0.9.4: restricted to exactly one entity per call; formula-derived sensors auto-recalculate when an input changes)
- ✅ `force_refresh` service (v0.8.4): forces immediate email scan + PDF analysis for a single device; device picker is filtered to Concierge HA Integration only
- ✅ Per-device *Force Refresh* button entity (v0.8.4): `button.concierge_{id}_force_refresh` appears in the device Configuration panel; pressing it triggers the same targeted refresh as the service
- ✅ Agua Caliente sensors on the Gastos Comunes device (v0.9.5): five dedicated sensor entities (`consumption`, `cost_per_unit`, `amount`, `prev_reading`, `curr_reading`) are automatically created for every Gastos Comunes service and populated from the same "Nota de Cobro" PDF via OCR — no separate "Agua Caliente" service device is required or supported, since the hot-water data lives exclusively inside the Gastos Comunes email/PDF
- ✅ **OCR.space cloud API as sole OCR engine (v1.0.2)**: removed RapidOCR (onnxruntime unavailable on HA OS) and Concierge Add-on fallback; users register for a free key at [ocr.space/OCRAPI](https://ocr.space/OCRAPI) and enter it during setup or via CONFIGURE
- ✅ **Gastos Comunes PDF extraction generalised for any building (v1.1.0)**: alícuota, fondos, and building-total patterns were previously hard-coded to the reference building's values; they now match any building's Nota de Cobro regardless of alícuota magnitude, fondos percentage, or total size; the three-amounts fallback is scoped to the breakdown section to prevent false matches
- ✅ **Forwarded-email detection fixed (v1.2.1)**: a new fifth strategy (`service-type-pattern-fallback`) iterates the canonical `SERVICE_PATTERNS` list so that Spanish-language keywords ("gastos comunes", "aguas andinas", "metrogas", …) are recognised regardless of the sender address, fixing cases where bills forwarded through Gmail or other generic webmail providers were silently missed
- ✅ **Structured email-processing logs (v1.2.2)**: every mailbox scan now emits `INFO`-level entries for each matched email (from, subject, date, **detection strategy**), extracted attributes (email body and PDF), and PDF emission-date overrides; `DEBUG`-level entries cover every email evaluated and every non-match — see [Logging & Diagnostics](#-logging--diagnostics)
- ✅ **Registro/Logbook task timeline (v1.3.10)**: the integration now publishes task entries to Home Assistant Logbook under `concierge_ha_tasks` for startup, discovery, automatic IMAP polling, force refresh, recalculate and manual `set_value` operations

### 🔮 Future Enhancements
- Persistent notifications for detected services
- Enhanced attribute display in sensor states
- Historical billing data tracking
- Consumption trends and analytics
- Payment reminders and automations

---

## 🔍 Logging & Diagnostics

Starting with **v1.2.2**, the integration emits structured log entries every time it
scans the mailbox for a service.  These entries let you confirm:

- which emails were evaluated,
- which one matched and **why** (detection strategy),
- what data was extracted from the email body and from the PDF.

### Enabling the logs

Add the following to your `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.concierge_ha_integration: debug
```

Use `info` instead of `debug` if you only want the match/extraction lines without
the per-email evaluation noise.

### Logbook task entries (Registro)

Starting with **v1.3.10**, Concierge also writes task-level entries to Home
Assistant **Logbook / Registro** using the dedicated domain
`concierge_ha_tasks`. This lets you follow what the integration did over time
without enabling verbose logger output.

### Log levels

| Level | When emitted | Example message |
|-------|-------------|-----------------|
| `DEBUG` | Every email fetched from the mailbox | `Concierge Services [Gas]: evaluating email — from='...', subject='...'` |
| `DEBUG` | Email did **not** match the service | `Concierge Services [Gas]: email did not match — from='...', subject='...'` |
| `INFO` | Email **matched** — shows which strategy triggered it | `Concierge Services [Gas]: email matched via strategy 'sender-domain' — from='facturas@metrogas.cl', subject='Boleta Marzo 2026', date='...'` |
| `INFO` | Attributes extracted from the email body | `Concierge Services [Gas]: attributes extracted from email body — total_amount=45230, consumption=18.3` |
| `INFO` | PDF attachment found and being processed | `Concierge Services [Common Expenses]: PDF found at '/config/.../gc_2026-04.pdf' — extracting additional attributes` |
| `INFO` | Attributes extracted from the PDF | `Concierge Services [Common Expenses]: attributes extracted from PDF — subtotal=95000, cargo_fijo=9638, ...` |
| `INFO` | `last_updated` overridden with the PDF's issue date | `Concierge Services [Common Expenses]: last_updated overridden with PDF emission date '15-04-2026'` |
| `DEBUG` | Matching email found but no PDF attachment | `Concierge Services [Gas]: no PDF attachment found in matching email` |
| `WARNING` | No matching email found after scanning the last 100 messages | `No matching email found for service 'Gas' (id: gas) in the last N emails` |

### Detection strategies (in order)

The integration tries five strategies to decide whether an email belongs to a
configured service.  The strategy name appears in the `INFO` match log line:

| Strategy | How it works |
|---|---|
| `sender-domain` | The sender's domain matches the domain recorded in the sample e-mail, and the domain is not a generic webmail provider (Gmail, Hotmail, …) |
| `service-name-keywords` | Every significant word (> 3 chars) from the service name appears in the combined email text |
| `service-id-pattern` | The service ID slug matches a whole-word pattern in the email text |
| `sample-subject-keywords` | At least one unique keyword from the sample subject is found in the email (handles forwarded bills) |
| `service-type-pattern-fallback` | A canonical Spanish-language pattern for this service type (e.g. *"gastos comunes"*, *"aguas andinas"*) matches the email text — last resort for forwarded emails via generic webmail providers |

---

## 📓 Notes

- The integration currently detects services automatically from your inbox
- Services are identified using targeted pattern matching on billing emails
- Works with both emails that carry a PDF attachment and emails that only contain a download link in the HTML body
- Bill PDFs are downloaded automatically to `config/concierge_ha_integration/pdfs/`, keeping only the 5 newest files and purging files older than one year
- All credentials are stored securely in Home Assistant
- It is recommended to use app passwords instead of your main password
- Only one instance is allowed per Home Assistant installation — use the **CONFIGURE** button to change the monitored email account

---

## 📜 License

MIT License. See [LICENSE](https://github.com/Geek-MD/Concierge_HA_Integration/blob/main/LICENSE) for details.

---

<div align="center">
  
💻 **Proudly developed with GitHub Copilot** 🚀

</div>
