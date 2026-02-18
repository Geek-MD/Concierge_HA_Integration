[![Geek-MD - Concierge Services](https://img.shields.io/static/v1?label=Geek-MD&message=Concierge%20Services&color=blue&logo=github)](https://github.com/Geek-MD/Concierge_Services)
[![Stars](https://img.shields.io/github/stars/Geek-MD/Concierge_Services?style=social)](https://github.com/Geek-MD/Concierge_Services)
[![Forks](https://img.shields.io/github/forks/Geek-MD/Concierge_Services?style=social)](https://github.com/Geek-MD/Concierge_Services)

[![GitHub Release](https://img.shields.io/github/release/Geek-MD/Concierge_Services?include_prereleases&sort=semver&color=blue)](https://github.com/Geek-MD/Concierge_Services/releases)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://github.com/Geek-MD/Concierge_Services/blob/main/LICENSE)
[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom%20Repository-blue)](https://hacs.xyz/)

[![Ruff + Mypy + Hassfest](https://github.com/Geek-MD/Concierge_Services/actions/workflows/ci.yaml/badge.svg)](https://github.com/Geek-MD/Concierge_Services/actions/workflows/ci.yaml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

# Concierge Services

**Concierge Services** is a custom integration for [Home Assistant](https://www.home-assistant.io) that allows you to manage utility bills (electricity, water, gas, etc.) received by email. The integration automatically detects services, extracts information from emails using heuristic analysis, and creates devices and sensors for each service with billing data.

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
- 🤖 **Heuristic Attribute Extraction**: Intelligently extracts billing data from email content
  - Account/customer numbers
  - Invoice/folio numbers
  - Total amounts due
  - Due dates and billing periods
  - Consumption data
  - Addresses and company information
  - Any structured data found in emails
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

All configuration is done through the user interface in two simple steps:

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
- **Area**: Associate the integration with a specific area in your home (optional)

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

### ✅ Version 0.2.0 (Current)
- ✅ IMAP account configuration through UI
- ✅ Two-step configuration (credentials + friendly name/area)
- ✅ Real-time credential validation
- ✅ Secure credential storage
- ✅ Interface in Spanish and English
- ✅ HACS compatibility
- ✅ Device architecture with proper device_info
- ✅ Status sensor: "Concierge Services - Status"
- ✅ Automatic service detection from inbox
- ✅ Heuristic attribute extraction from emails
- ✅ Support for detecting multiple service types
- ✅ Flexible pattern matching for billing data

### 🔜 Version 0.3.0 (Upcoming)
- 🔜 Persistent notifications for detected services
- 🔜 Service-specific device creation
- 🔜 Individual sensors per configured service
- 🔜 Enhanced attribute display in sensor states
- 🔜 Service configuration UI flow

### 🔮 Future Enhancements
- Enhanced PDF attachment processing
- Historical billing data tracking
- Consumption trends and analytics
- Payment reminders and automations
- Multi-account support improvements

---

## 📓 Notes

- The integration currently detects services automatically from your inbox
- Services are identified using heuristic analysis of email content
- Works best with emails that have attachments (typical for bills)
- No PDF processing required - extracts data directly from email text
- All credentials are stored securely in Home Assistant
- It is recommended to use app passwords instead of your main password
- Multiple instances supported (different email accounts)

---

## 📜 License

MIT License. See [LICENSE](https://github.com/Geek-MD/Concierge_Services/blob/main/LICENSE) for details.

---

💻 **Proudly developed with GitHub Copilot** 🚀