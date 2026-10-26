"""Regression tests for PDF download caching."""

from datetime import datetime, timezone
from email.message import EmailMessage
import importlib.util
from pathlib import Path
import tempfile
import unittest


_MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "concierge_ha_integration"
    / "pdf_downloader.py"
)
_SPEC = importlib.util.spec_from_file_location("concierge_pdf_downloader", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
download_pdf_from_email = _MODULE.download_pdf_from_email


class PdfDownloaderCacheTests(unittest.TestCase):
    """Ensure a billing-period filename is not treated as document identity."""

    def test_current_attachment_replaces_same_month_cached_pdf(self) -> None:
        """A new gas attachment must overwrite a stale same-period document."""
        message = EmailMessage()
        message.set_content("Su nueva boleta de gas")
        message.add_attachment(
            b"%PDF-new-gas-bill",
            maintype="application",
            subtype="pdf",
            filename="boleta.pdf",
        )

        with tempfile.TemporaryDirectory() as pdf_dir:
            cached = Path(pdf_dir) / "gas_2026-09.pdf"
            cached.write_bytes(b"%PDF-wrong-old-document")

            result = download_pdf_from_email(
                message,
                pdf_dir,
                "gas",
                datetime(2026, 9, 22, tzinfo=timezone.utc),
            )

            self.assertEqual(result, str(cached))
            self.assertEqual(cached.read_bytes(), b"%PDF-new-gas-bill")


if __name__ == "__main__":
    unittest.main()
