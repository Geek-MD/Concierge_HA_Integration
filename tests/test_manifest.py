"""Guard Home Assistant's manifest ordering before running Hassfest."""
import json
from pathlib import Path
import unittest


class ManifestTests(unittest.TestCase):
    """Keep identity keys first and all remaining keys alphabetical."""

    def test_manifest_key_order_matches_hassfest(self) -> None:
        root = Path(__file__).parents[1] / "custom_components"
        manifests = list(root.glob("*/manifest.json"))
        self.assertTrue(manifests, "No integration manifests found")
        for path in manifests:
            with self.subTest(integration=path.parent.name):
                keys = list(json.loads(path.read_text()))
                expected = ["domain", "name", *sorted(set(keys) - {"domain", "name"})]
                self.assertEqual(
                    keys,
                    expected,
                    'Hassfest requires "domain", "name", then alphabetical order',
                )
