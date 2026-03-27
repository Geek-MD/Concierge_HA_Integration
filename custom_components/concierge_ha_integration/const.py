"""Constants for the Concierge Services integration."""

DOMAIN = "concierge_ha_integration"

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

# OCR.space API — optional free API key for the OCR.space cloud OCR service
# (https://ocr.space/OCRAPI). The free tier key is "helloworld" (rate-limited);
# register at https://ocr.space/OCRAPI for a higher-quota free key.
CONF_OCRSPACE_API_KEY = "ocrspace_api_key"
