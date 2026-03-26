#!/usr/bin/with-contenv bashio

LOG_LEVEL=$(bashio::config 'log_level')
PORT=$(bashio::config 'port')

export RAPIDOCR_LOG_LEVEL="${LOG_LEVEL:-info}"
export RAPIDOCR_HOST="0.0.0.0"
export RAPIDOCR_PORT="${PORT:-8099}"

bashio::log.info "Starting RapidOCR server on port ${RAPIDOCR_PORT} (log_level=${RAPIDOCR_LOG_LEVEL})"

exec python3 /server.py
