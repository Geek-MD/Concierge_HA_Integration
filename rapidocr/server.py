#!/usr/bin/env python3
"""RapidOCR HTTP API server for the Home Assistant RapidOCR add-on.

Exposes a single endpoint:

    POST /ocr/file?lang=<lang>&psm=<psm>

that accepts a multipart/form-data upload with a ``file`` field (PNG/JPEG
image) and returns JSON::

    {"text": "<extracted plain text>"}

The endpoint is intentionally compatible with the Tesseract OCR add-on API
consumed by the Concierge HA Integration, so the same ``ocr_api_url`` setting
works with both add-ons.
"""
from __future__ import annotations

import io
import logging
import os

import numpy as np
from flask import Flask, Response, jsonify, request
from PIL import Image

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
_log_level_name = os.environ.get("RAPIDOCR_LOG_LEVEL", "info").lower()
_log_level = _LOG_LEVELS.get(_log_level_name, logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("rapidocr_server")

# ---------------------------------------------------------------------------
# RapidOCR constants  (mirror those in the Concierge integration)
# ---------------------------------------------------------------------------
# Maximum Y-pixel distance between two OCR bounding boxes to be grouped on
# the same text line during text reconstruction.
_OCR_ROW_Y_THRESHOLD: int = 20

# Minimum per-box confidence score from RapidOCR for a text block to be
# included in the output.
_OCR_BOX_MIN_CONFIDENCE: float = 0.5

# ---------------------------------------------------------------------------
# RapidOCR engine — initialised once at import time
# ---------------------------------------------------------------------------
try:
    from rapidocr import RapidOCR  # type: ignore[import-untyped, attr-defined]

    _ocr: RapidOCR | None = RapidOCR()
    _LOGGER.info("RapidOCR engine initialised successfully.")
except Exception as _exc:  # noqa: BLE001
    _LOGGER.error("Failed to initialise RapidOCR engine: %s", _exc)
    _ocr = None

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helper: convert RapidOCR bounding-box results to plain text
# ---------------------------------------------------------------------------
def _ocr_boxes_to_text(raw_results: list) -> str:
    """Convert RapidOCR ``[bbox, text, score]`` results to a plain-text string.

    Items are grouped into logical lines (rows whose Y centre coordinates
    differ by less than ``_OCR_ROW_Y_THRESHOLD`` pixels) and then sorted
    left-to-right within each row before joining with spaces.  Rows are
    separated by newlines.
    """
    items: list[tuple[float, float, str]] = []
    for entry in raw_results:
        if len(entry) < 2:
            continue
        bbox, text = entry[0], entry[1]
        score = float(entry[2]) if len(entry) > 2 else 1.0
        if score < _OCR_BOX_MIN_CONFIDENCE:
            continue
        try:
            ys = [pt[1] for pt in bbox]
            xs = [pt[0] for pt in bbox]
            y_center = sum(ys) / len(ys)
            x_left = min(xs)
        except (TypeError, IndexError):
            continue
        items.append((y_center, x_left, str(text)))

    if not items:
        return ""

    items.sort(key=lambda t: (t[0], t[1]))

    rows: list[list[tuple[float, float, str]]] = []
    current_row = [items[0]]
    current_y = items[0][0]

    for item in items[1:]:
        if abs(item[0] - current_y) <= _OCR_ROW_Y_THRESHOLD:
            current_row.append(item)
        else:
            rows.append(current_row)
            current_row = [item]
            current_y = item[0]
    rows.append(current_row)

    lines: list[str] = []
    for row in rows:
        row.sort(key=lambda t: t[1])
        lines.append(" ".join(t[2] for t in row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health() -> Response:
    """Liveness / readiness probe."""
    return jsonify({"status": "ok", "ocr_available": _ocr is not None})


@app.route("/ocr/file", methods=["POST"])
def ocr_file() -> Response | tuple[Response, int]:
    """OCR endpoint compatible with the Tesseract OCR add-on API.

    Query parameters
    ----------------
    lang : str
        Language hint (e.g. ``spa``, ``eng``).  Accepted for API
        compatibility; RapidOCR selects the language model automatically.
    psm : int
        Tesseract Page Segmentation Mode.  Accepted for API compatibility;
        ignored because RapidOCR does not use PSM modes.

    Request body
    ------------
    multipart/form-data with a ``file`` field containing a PNG or JPEG image.

    Response
    --------
    JSON ``{"text": "<extracted text>"}`` on success.
    """
    if _ocr is None:
        return jsonify({"error": "OCR engine not available", "text": ""}), 503

    if "file" not in request.files:
        return jsonify({"error": "No 'file' field in multipart request", "text": ""}), 400

    file = request.files["file"]
    lang = request.args.get("lang", "")
    psm = request.args.get("psm", "")
    _LOGGER.debug(
        "OCR request: filename=%s lang=%s psm=%s size=%s bytes",
        file.filename,
        lang,
        psm,
        request.content_length,
    )

    try:
        image_bytes = file.read()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img_array = np.array(img)

        result = _ocr(img_array)

        if result is None or not result:
            _LOGGER.debug("RapidOCR returned no results.")
            return jsonify({"text": ""})

        # RapidOCR v3+ returns a RapidOCROutput object with .boxes / .txts / .scores
        scores = result.scores if result.scores is not None else [1.0] * len(result.boxes)
        raw_results = [
            [box.tolist(), txt, float(score)]
            for box, txt, score in zip(result.boxes, result.txts or [], scores)
        ]

        text = _ocr_boxes_to_text(raw_results)
        _LOGGER.debug("OCR complete: %d blocks, %d chars", len(raw_results), len(text))
        return jsonify({"text": text})

    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("OCR processing failed: %s", exc)
        return jsonify({"error": str(exc), "text": ""}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.environ.get("RAPIDOCR_HOST", "0.0.0.0")
    port = int(os.environ.get("RAPIDOCR_PORT", "8099"))

    _LOGGER.info("RapidOCR HTTP server listening on %s:%d", host, port)
    app.run(host=host, port=port, debug=False)
