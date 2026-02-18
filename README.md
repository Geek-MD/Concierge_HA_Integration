[![Geek-MD - Concierge Services](https://img.shields.io/static/v1?label=Geek-MD&message=Concierge%20Services&color=blue&logo=github)](https://github.com/Geek-MD/Concierge_Services)
[![Stars](https://img.shields.io/github/stars/Geek-MD/Concierge_Services?style=social)](https://github.com/Geek-MD/Concierge_Services)
[![Forks](https://img.shields.io/github/forks/Geek-MD/Concierge_Services?style=social)](https://github.com/Geek-MD/Concierge_Services)

[![GitHub Release](https://img.shields.io/github/release/Geek-MD/Concierge_Services?include_prereleases&sort=semver&color=blue)](https://github.com/Geek-MD/Concierge_Services/releases)
[![License](https://img.shields.io/badge/License-MIT-blue)](https://github.com/Geek-MD/Concierge_Services/blob/main/LICENSE)
[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom%20Repository-blue)](https://hacs.xyz/)

# Concierge Services

**Concierge Services** is a custom integration for [Home Assistant](https://www.home-assistant.io) that allows you to manage utility bills (electricity, water, gas, etc.) received by email. The integration automatically extracts information from attached PDFs and creates sensors for each service with the total amount due and additional data.

---

## ✨ Features

- 📧 **IMAP Email Configuration**: Connect your email account where you receive utility bills
- ✅ **Credential Validation**: Automatically verifies that IMAP credentials are correct
- 🔒 **Secure Storage**: Credentials are stored securely in Home Assistant
- 🌐 **Multi-language Support**: Complete interface in Spanish and English
- 🎯 **UI Configuration**: No YAML file editing required

### 🚧 Coming Soon

- 📊 **Sensors per Service**: Configure individual sensors for each service (electricity, water, gas, etc.)
- 📄 **PDF Extraction**: Automatically analyze bill PDFs
- 💰 **Total Amount Due**: Sensor displays the total amount to pay
- 📈 **Detailed Attributes**: Consumption, customer number, period, and other data as sensor attributes
- 🔔 **Notifications**: Alerts when a new bill arrives

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

All configuration is done through the user interface.

1. Go to **Settings** → **Devices & Services**
2. Click the **+ Add Integration** button
3. Search for **Concierge Services**
4. Enter your email account details:
   - **IMAP Server**: Your IMAP email server
   - **IMAP Port**: The IMAP port (default: `993`)
   - **Email**: Your email address
   - **Password**: Your password or app password

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

## 🚀 Development Status

### ✅ Phase 1: Credential Configuration (Completed)
- IMAP account configuration through UI
- Real-time credential validation
- Secure credential storage
- Interface in Spanish and English
- HACS compatibility

### 🔜 Upcoming Phases

#### Phase 2: Sensor Creation
- Configure individual sensors per service
- Specify service name (e.g., "Electricity", "Water", "Gas")
- Define PDF fields to extract

#### Phase 3: Email Reading
- Connect to configured IMAP server
- Filter emails from service accounts
- Download attached PDF files
- Identify new bills

#### Phase 4: Data Extraction
- Parse PDFs with OCR/parsing
- Extract configurable information:
  - Customer number
  - Billing period
  - Consumption
  - Total amount due
  - Due date

#### Phase 5: Sensor Updates
- Update sensor state with total amount due
- Store additional data as attributes
- Trigger events when new bill arrives
- History of previous bills

---

## 📓 Notes

- The integration currently only configures IMAP credentials
- Subsequent phases will add sensor functionality and email reading
- All credentials are stored securely in Home Assistant
- It is recommended to use app passwords instead of your main password

---

## 🙋‍♂️ Support

If you encounter any issues or have suggestions, please [open an issue](https://github.com/Geek-MD/Concierge_Services/issues).

---

## 📄 License

MIT © Edison Montes [_@GeekMD_](https://github.com/Geek-MD)

---

<div align="center">
  
💻 **Proudly developed with GitHub Copilot** 🚀

</div>