"""Constants for the Concierge Services integration."""

DOMAIN = "concierge_ha_integration"
TASK_LOGBOOK_DOMAIN = "concierge_ha_tasks"
TASK_LOGBOOK_NAME = "Concierge Tasks"

# Configuration keys
CONF_IMAP_SERVER = "imap_server"
CONF_IMAP_PORT = "imap_port"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Subentry configuration keys
CONF_SERVICE_ID = "service_id"
CONF_SERVICE_NAME = "service_name"
CONF_SERVICE_TYPE = "service_type"
CONF_SAMPLE_FROM = "sample_from"
CONF_SAMPLE_SUBJECT = "sample_subject"

# Default values
DEFAULT_IMAP_PORT = 993

# Service type constants used to route to the appropriate extraction tools
SERVICE_TYPE_WATER = "water"
SERVICE_TYPE_GAS = "gas"
SERVICE_TYPE_ELECTRICITY = "electricity"
SERVICE_TYPE_TELECOM = "telecom"
SERVICE_TYPE_COMMON_EXPENSES = "common_expenses"
SERVICE_TYPE_HOT_WATER = "hot_water"
SERVICE_TYPE_UNKNOWN = "unknown"

# PDF storage — subdirectory (relative to the HA config dir) and retention
PDF_SUBDIR = "concierge_ha_integration/pdfs"
PDF_MAX_AGE_DAYS = 365
PDF_MAX_FILES = 5

# OCR JSON storage — parallel directory next to pdfs/, same retention limit
JSON_SUBDIR = "concierge_ha_integration/json"
JSON_MAX_FILES = 5

# OCR.space API — optional free API key for the OCR.space cloud OCR service
# (https://ocr.space/OCRAPI). The free tier key is "helloworld" (rate-limited);
# register at https://ocr.space/OCRAPI for a higher-quota free key.
CONF_OCRSPACE_API_KEY = "ocrspace_api_key"

# Concierge addon (https://github.com/Geek-MD/Concierge_addon)
# When installed, the addon exposes an OCR REST API used to analyse
# common-expenses and hot-water PDFs instead of the internal extractor.
ADDON_SLUG = "concierge_ocr"
ADDON_API_PORT = 8099
ADDON_API_URL = "http://localhost:8099"
ADDON_NOTIFICATION_ID = "concierge_addon_not_installed"
