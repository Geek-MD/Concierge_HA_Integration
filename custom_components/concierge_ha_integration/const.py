"""Constants for the Concierge Services integration."""

DOMAIN = "concierge_services"

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
SERVICE_TYPE_UNKNOWN = "unknown"
