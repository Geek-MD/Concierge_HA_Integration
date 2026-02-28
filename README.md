[![Geek-MD - Concierge](https://img.shields.io/static/v1?label=Geek-MD&message=Concierge&color=blue&logo=github)](https://github.com/Geek-MD/Concierge_HA_Integration)
[![Stars](https://img.shields.io/github/stars/Geek-MD/Concierge_HA_Integration?style=social)](https://github.com/Geek-MD/Concierge_HA_Integration)
[![Forks](https://img.shields.io/github/forks/Geek-MD/Concierge_HA_Integration?style=social)](https://github.com/Geek-MD/Concierge_HA_Integration)

[![GitHub Release](https://img.shields.io/github/release/Geek-MD/Concierge_HA_Integration?include_prereleases&sort=semver&color=blue)](https://github.com/Geek-MD/Concierge_HA_Integration/releases)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://github.com/Geek-MD/Concierge_HA_Integration/blob/main/LICENSE)
[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom%20Repository-blue)](https://hacs.xyz/)

[![Ruff + Mypy + Hassfest](https://github.com/Geek-MD/Concierge/actions/workflows/ci.yaml/badge.svg)](https://github.com/Geek-MD/Concierge/actions/workflows/ci.yaml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

<img width="200" height="200" alt="image" src="https://github.com/Geek-MD/Concierge_HA_Integration/blob/main/icon.png?raw=true" />

# Concierge

**Concierge** is a custom integration for [Home Assistant](https://www.home-assistant.io) that allows you to manage utility bills (electricity, water, gas, etc.) received by email. The integration automatically detects services, extracts information from emails, and creates devices and sensors for each service with billing data.

> **🇨🇱 Geographic scope — Chile only (for now)**
> Concierge Services is currently designed and tested exclusively for Chilean utility service accounts (Aguas Andinas, Enel, etc.). Billing email formats, field labels, and patterns are tuned for Chilean providers. Support for other countries may be added in the future.

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
- 🤖 **Targeted Attribute Extraction**: Extracts exactly the billing fields needed before PDF analysis:
  - Company name (`service_name`)
  - Invoice/folio number (`folio`)
  - Billing period start & end (`billing_period_start`, `billing_period_end`)
  - Total amount due (`total_amount`)
  - Customer / account number (`customer_number`)
  - Service address (`address`)
  - Email send date (`last_updated_datetime`)
- 🔧 **Device Architecture**: Each service appears as a separate device
- 📊 **Status Sensor**: Monitor email connection status in real-time

### 🚧 Coming Soon

- 🔔 **Discovery Notifications**: Persistent notifications when new services are detected
- 📱 **Service Configuration**: Configure detected services as individual devices
- 📈 **Historical Data**: Track billing history over time
- 📄 **PDF Analysis**: Enhanced extraction from PDF attachments (future enhancement)

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

Once the integration is set up, add utility services one by one using the **ADD DEVICE** button
on the integration card:
- The integration scans your inbox and detects available service providers
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
- ✅ Passes ruff, mypy and hassfest checks

### 🔮 Future Enhancements
- Persistent notifications for detected services
- Enhanced attribute display in sensor states
- Enhanced PDF attachment processing
- Historical billing data tracking
- Consumption trends and analytics
- Payment reminders and automations

---

## 📓 Notes

- The integration currently detects services automatically from your inbox
- Services are identified using targeted pattern matching on billing emails
- Works best with emails that have attachments (typical for bills)
- No PDF processing required — targeted data extracted directly from email text and subject
- All credentials are stored securely in Home Assistant
- It is recommended to use app passwords instead of your main password
- Only one instance is allowed per Home Assistant installation — use the **CONFIGURE** button to change the monitored email account

---

## 📜 License

MIT License. See [LICENSE](https://github.com/Geek-MD/Concierge_Services/blob/main/LICENSE) for details.

---

💻 **Proudly developed with GitHub Copilot** 🚀
