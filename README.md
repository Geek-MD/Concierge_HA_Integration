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

  **Gas (6 entities):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_consumption` | Sensor | — | m³ consumed |
  | `sensor.concierge_{id}_cost_per_unit` | Sensor | — | $/m³ |
  | `sensor.concierge_{id}_total_amount` | Sensor | — | Total bill amount (`$`) |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device |

  **Electricity (10 entities):**

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
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device |

  **Water (15 entities — `cost_per_unit` is replaced by granular sensors):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_consumption` | Sensor | — | m³ consumed |
  | `sensor.concierge_{id}_total_amount` | Sensor | — | Total bill amount (`$`) |
  | `sensor.concierge_{id}_fixed_charge` | Sensor | — | Fixed service charge (`$`) |
  | `sensor.concierge_{id}_cost_per_unit_peak` | Sensor | — | Cost per m³ — peak (`$/m³`) |
  | `sensor.concierge_{id}_cost_per_unit_non_peak` | Sensor | — | Cost per m³ — non-peak (`$/m³`) |
  | `sensor.concierge_{id}_cubic_meter_overconsumption` | Sensor | — | Cost per m³ — overconsumption (`$/m³`) |
  | `sensor.concierge_{id}_cubic_meter_collection` | Sensor | — | Cost per m³ — collection (`$/m³`) |
  | `sensor.concierge_{id}_cubic_meter_treatment` | Sensor | — | Cost per m³ — treatment (`$/m³`) |
  | `sensor.concierge_{id}_water_consumption` | Sensor | — | Potable water charge (`$`) |
  | `sensor.concierge_{id}_wastewater_recolection` | Sensor | — | Wastewater collection charge (`$`) |
  | `sensor.concierge_{id}_wastewater_treatment` | Sensor | — | Wastewater treatment charge (`$`) |
  | `sensor.concierge_{id}_subtotal` | Sensor | — | Subtotal before surcharges (`$`) |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device |

  **Common Expenses (8 entities):**

  | Entity | Type | Category | Value / Purpose |
  |---|---|---|---|
  | `binary_sensor.concierge_{id}_status` | Binary sensor | Diagnostic | `on` = problem (no data or data older than 1 month), `off` = OK |
  | `sensor.concierge_{id}_last_update` | Sensor | Diagnostic | Datetime of the latest processed bill — displayed as relative time ("hace 2 días") |
  | `sensor.concierge_{id}_bill` | Sensor | — | GC apartment portion (`$`) — alícuota % of building expense |
  | `sensor.concierge_{id}_funds_provision` | Sensor | — | Funds provision amount (`$`) — Bill × Funds % / 100 |
  | `sensor.concierge_{id}_subtotal` | Sensor | — | Subtotal Departamento (`$`) — Bill + Funds Provision |
  | `sensor.concierge_{id}_fixed_charge` | Sensor | — | Cargo Fijo (`$`) |
  | `sensor.concierge_{id}_total` | Sensor | — | Total GC bill (`$`) — Subtotal + Cargo Fijo |
  | `button.concierge_{id}_force_refresh` | Button | Configuration | Triggers an immediate email + PDF re-scan for this device |

- 📋 **Status Binary Sensor Attributes**: The `binary_sensor.concierge_{id}_status`
  entity always exposes the following attributes (missing values default to `0`):
  - Service identity: `service_id`, `service_name`, `service_type`, `friendly_name`, `icon`
  - Billing: `folio`, `billing_period_start`, `billing_period_end`, `customer_number`,
    `address`, `due_date`
  - When a PDF has been downloaded: `pdf_path`
  - **Electricity** extras: `tariff_code`, `connected_power`, `connected_power_unit`,
    `area`, `substation`, `pdf_url`
  - **Gas** extras: `pdf_url`
- 📄 **Heuristic PDF Download**: Automatically downloads the billing PDF for each matched email:
  - If the email has a PDF attachment it is saved directly
  - Otherwise the HTML body is scanned for billing links (*"ver boleta"*, *"descargue su boleta"*, etc.) and the first valid PDF URL is downloaded
  - Files are saved as `{service_id}_{YYYY-MM}_{folio}.pdf` under `config/concierge_ha_integration/pdfs/`
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
without any scripting or service call.

---

### `concierge_ha_integration.set_value`

Forces a specific value for a named attribute of a Concierge service entity and
persists it as a **learning override**.  The correction is applied immediately and
will be re-applied automatically after every future email/PDF analysis, overriding
any value extracted by pdfminer or OCR.  The overridden sensor will show
`extraction_confidence = 100`.

| Field | Required | Selector | Description |
|---|---|---|---|
| `entity_id` | ✅ | `entity` (integration filter) | Any entity belonging to the target Concierge service. Only entities from this integration are shown. |
| `attribute` | ❌ | `text` | Internal attribute key to override (e.g. `fixed_charge`, `gastos_comunes_amount`). When omitted the key is inferred automatically from the entity's unique_id. |
| `value` | ✅ | `text` | The correct value (e.g. `9638`). |

> **UI filter** — the entity picker automatically filters to show **only** entities that belong to
> `concierge_ha_integration`.  Entities from other integrations are never listed.

#### Usage examples

**Developer Tools → Actions:**
```yaml
action: concierge_ha_integration.set_value
data:
  entity_id: sensor.concierge_gastos_comunes_fixed_charge
  attribute: fixed_charge   # optional — inferred from entity when omitted
  value: "9638"
```

**Automation / Script:**
```yaml
action: concierge_ha_integration.set_value
data:
  entity_id: sensor.concierge_gastos_comunes_fixed_charge
  value: "9638"
```

---

## 📦 Installation

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
- ✅ Automatic purge of PDFs older than 1 year
- ✅ Billing attribute extraction from email body and PDF (folio, billing period, amounts, consumption, customer number, address, due date, etc.)
- ✅ PDF content analysis: extracts structured billing data from downloaded PDFs (Enel, Metrogas)
- ✅ `pdf_url` attribute on electricity and gas status binary sensors — exposes the bill download URL; correctly populated even when the PDF is already cached (v0.7.10)
- ✅ Metrogas/fidelizador.com bill URL reliably extracted from the plain-text email body via the `[image: Ver boleta]` marker and raw QP-line reconstruction (v0.7.13)
- ✅ acepta.com Custodium multi-hop PDF download: follows the full chain (fidelizador tracking URL → outer wrapper → Custodium JS page → PdfView "no plugin" page → PDF); handles percent-encoded hrefs, extra rendering parameters, and root-relative paths (v0.7.15)
- ✅ Per-service entity architecture (v0.7.0): each service device exposes `binary_sensor.concierge_{id}_status` (Diagnostic) + `sensor.concierge_{id}_last_update` (Diagnostic) + `sensor.concierge_{id}_consumption` + `sensor.concierge_{id}_cost_per_unit` + `sensor.concierge_{id}_total_amount`
- ✅ `sensor.concierge_{id}_last_update` holds the full ISO 8601 bill datetime (v0.7.1)
- ✅ `set_value` learning-override service (v0.9.0): forces a correct value for any named attribute of a Concierge entity; entity picker is filtered to Concierge HA Integration only; `extraction_confidence` is set to 100 on overridden sensors (v0.9.2: UI shows two plain text boxes for `attribute` and `value`; `device_id` injected by HA UI is silently ignored)
- ✅ `force_refresh` service (v0.8.4): forces immediate email scan + PDF analysis for a single device; device picker is filtered to Concierge HA Integration only
- ✅ Per-device *Force Refresh* button entity (v0.8.4): `button.concierge_{id}_force_refresh` appears in the device Configuration panel; pressing it triggers the same targeted refresh as the service

### 🔮 Future Enhancements
- Persistent notifications for detected services
- Enhanced attribute display in sensor states
- Historical billing data tracking
- Consumption trends and analytics
- Payment reminders and automations

---

## 📓 Notes

- The integration currently detects services automatically from your inbox
- Services are identified using targeted pattern matching on billing emails
- Works with both emails that carry a PDF attachment and emails that only contain a download link in the HTML body
- Bill PDFs are downloaded automatically to `config/concierge_ha_integration/pdfs/` and purged after one year
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
