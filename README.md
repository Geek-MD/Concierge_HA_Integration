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
- 🤖 **Standard Attribute Set**: Every service sensor always exposes the full
  set of standard attributes.  Missing values default to `0`:
  - Service identity: `service_id`, `service_name`, `service_type`,
    `friendly_name`, `icon`
  - Timing: `last_updated_datetime`
  - Billing: `folio`, `billing_period_start`, `billing_period_end`,
    `total_amount` (integer), `due_date`
  - Account: `customer_number`, `address`
  - Usage: `consumption`, `consumption_unit`
- 📋 **Type-specific Attributes**: In addition to the standard set, each service
  type exposes its own extra attributes (numeric attributes default to `0`;
  `pdf_url` defaults to `""`):
  - **Electricity** (`service_type: electricity`):
    `service_administration`, `electricity_transport`, `stabilization_fund`,
    `electricity_consumption`, `cost_per_kwh`, `tariff_code`,
    `connected_power`, `connected_power_unit`, `area`, `substation`, `pdf_url`
  - **Gas** (`service_type: gas`):
    `cost_per_m3s`, `pdf_url`
  - **Water** (`service_type: water`):
    `fixed_charge`, `cubic_meter_peak_water_cost`,
    `cubic_meter_non_peak_water_cost`, `cubic_meter_overconsumption`,
    `cubic_meter_collection`, `cubic_meter_treatment`, `water_consumption`,
    `wastewater_recolection`, `wastewater_treatment`, `subtotal`,
    `other_charges`
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
- 📑 **PDF Analysis**: Extract structured billing data directly from downloaded PDFs

---

## 📦 Installation

### Option 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations → Custom Repositories**
3. Add this repository:
   ```
   https://github.com/Geek-MD/Concierge_Services
   ```
   Select type: **Integration**
4. Install and restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and select **Concierge Services**

---

### Option 2: Manual Installation

1. Download this repository
2. Copy the `custom_components/concierge_services/` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Add the integration through the UI

---

## ⚙️ Configuration

All configuration is done through the user interface:

### Step 1: IMAP Credentials

1. Go to **Settings** → **Devices & Services**
2. Click the **+ Add Integration** button
3. Search for **Concierge Services**
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
Discovered services appear on the **Concierge Services** integration card as
**"Discovered: {service_name}"** — click the card to confirm and the device is added
automatically.  The scan repeats every hour so newly-arrived bills are noticed.

#### ➕ Manual Addition
Use the **ADD DEVICE** button on the integration card:
- The integration scans your inbox and shows available service providers
- Select a service to add it as a device
- Repeat for each service you want to track
- Each service can be reconfigured later via its device page

> **Note**: Only one Concierge Services instance is allowed per Home Assistant installation
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

### Main Device
- **Name**: Your configured friendly name (e.g., "Casa Principal")
- **Area**: Your selected area (if configured)
- **Manufacturer**: Concierge Services
- **Model**: Email Integration

### Status Sensor
- **Name**: "Concierge Services - Status"
- **State**: "OK" or "Problem"
- **Attributes**:
  - Email address
  - IMAP server
  - IMAP port

### Service Devices (Auto-detected)
As the integration scans your inbox, it automatically detects utility services and will create:
- Individual devices per service (e.g., "Aguas Andinas", "Enel")
- Sensors with extracted billing information
- Device hierarchy linked to the main integration

---

## 🚀 Development Status

- ✅ IMAP account configuration through UI
- ✅ Two-step configuration (credentials + friendly name)
- ✅ Real-time credential validation
- ✅ Secure credential storage
- ✅ Interface in Spanish and English
- ✅ HACS compatibility
- ✅ Device architecture with proper device_info
- ✅ Status sensor: "Concierge Services - Status"
- ✅ Automatic service detection from inbox
- ✅ Support for detecting multiple service types
- ✅ Service-specific device creation via ADD DEVICE button
- ✅ Individual sensors per configured service
- ✅ MQTT-style architecture: email as hub, services as devices
- ✅ Options flow: CONFIGURE button to update IMAP credentials without reinstalling
- ✅ Subentry reconfigure: update service name from the device page
- ✅ Targeted attribute extraction (8 defined fields, no heuristic noise)
- ✅ HTML email body stripping (prefers text/plain, strips text/html)
- ✅ Folio extracted from subject, ready for PDF confirmation
- ✅ Billing period start/end, total amount, customer number, address
- ✅ Fix: AttributeError when clicking ADD DEVICE button (v0.4.3)
- ✅ Standard attribute set with defaults (v0.5.0): `folio`, `billing_period_start`, `billing_period_end`, `total_amount` (int), `customer_number`, `address`, `due_date`, `consumption`, `consumption_unit`, `icon`, `friendly_name`
- ✅ Device grouping fix (v0.5.0): service devices now appear correctly grouped under their subentry (no more "Dispositivos que no pertenecen a una subentrada")
- ✅ Hub device removed (v0.5.1): the connection/status sensor is now a standalone entity with no device, eliminating the "Dispositivos que no pertenecen a una subentrada" section entirely
- ✅ Automatic migration (v0.5.1): upgrading from v0.4.x no longer requires deleting and re-adding the integration; entity/device registry is migrated automatically on first startup
- ✅ Heuristic PDF download: attachment → billing link in HTML body (v0.4.10)
- ✅ Deterministic PDF filename: `{service_id}_{YYYY-MM}_{folio}.pdf` (v0.4.10)
- ✅ Automatic purge of PDFs older than 1 year (v0.4.10)
- ✅ `pdf_url` attribute on gas sensor (v0.6.14): exposes the reconstructed bill download URL
- ✅ `pdf_url` attribute on electricity sensor (v0.6.15): same attribute available for Enel/electricity bills
- ✅ Passes ruff, mypy and hassfest checks

### 🔮 Future Enhancements
- Persistent notifications for detected services
- Enhanced attribute display in sensor states
- PDF content analysis for structured data extraction
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

MIT License. See [LICENSE](https://github.com/Geek-MD/Concierge_Services/blob/main/LICENSE) for details.

---

<div align="center">
  
💻 **Proudly developed with GitHub Copilot** 🚀

</div>
