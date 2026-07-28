"""Regression tests for add-on structured-template compatibility."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest


PACKAGE = "concierge_ha_integration"
COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / PACKAGE


def _load_module(name: str):
    """Load an integration module without importing Home Assistant's entrypoint."""
    qualified_name = f"{PACKAGE}.{name}"
    spec = spec_from_file_location(qualified_name, COMPONENT_DIR / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PACKAGE] = package

_load_module("const")
_load_module("extraction_diagnostics")
extractor = _load_module("attribute_extractor")


class AddonStructuredExtractionTests(unittest.TestCase):
    """Verify extraction survives compatible add-on schema variations."""

    def test_nested_values_and_renamed_fields_populate_sensor_amounts(self) -> None:
        response = {
            "sections": {
                "tabla_desglose_departamento": {
                    "rows": [{"Gasto común": {"value": "$ 120.000"}}],
                    "Provisión de fondos monto": "$ 6.000",
                    "subtotal_departamento": "$ 126.000",
                },
                "tabla_gastos_por_unidad": {
                    "cargo_fijo": "$ 4.000",
                    "subtotal_recargos": "$ 4.000",
                    "total_del_mes": "$ 150.000",
                },
                "tabla_consumos_generales": {"subtotal_consumo": "$ 20.000"},
            }
        }

        attrs = extractor.extract_attributes_from_addon_ocr_json(response)

        self.assertEqual(attrs["gastos_comunes_amount"], 120_000)
        self.assertEqual(attrs["funds_provision"], 6_000)
        self.assertEqual(attrs["subtotal"], 126_000)
        self.assertEqual(attrs["fixed_charge"], 4_000)
        self.assertEqual(attrs["hot_water_amount"], 20_000)
        self.assertEqual(attrs["gc_total"], 150_000)

    def test_human_labelled_rows_populate_funds_and_hot_water(self) -> None:
        response = {
            "sections": {
                "tabla_desglose_departamento": {
                    "rows": [
                        {"Gasto común": {"Detalle": "0,95110 %", "Monto a pagar": "$ 134.788"}},
                        {
                            "PROVISIÓN DE FONDOS 5% DEL GASTO MENSUAL": {
                                "Detalle": "5,00 %",
                                "Monto a pagar": "$ 6.739",
                            }
                        },
                    ],
                    "Subtotal Departamento": "$ 141.527",
                },
                "tabla_consumos_generales": {
                    "rows": [{
                        "Agua Caliente": {
                            "Lectura Anterior": "298,300000",
                            "Lectura Actual": "301,800000",
                            "Consumos": "3,500000",
                            "Valor": "9.374,86",
                            "Total": "$ 32.812",
                        }
                    }],
                    "Subtotal Consumo": "$ 32.812",
                },
                "tabla_gastos_por_unidad": {
                    "Cargo Fijo": "$ 16.136",
                    "Total del mes": "$ 190.475",
                },
            }
        }

        attrs = extractor.extract_attributes_from_addon_ocr_json(response)

        self.assertEqual(attrs["funds_provision_percentage"], 5)
        self.assertEqual(attrs["funds_provision"], 6_739)
        self.assertEqual(attrs["hot_water_reading_prev"], 298.3)
        self.assertEqual(attrs["hot_water_reading_curr"], 301.8)
        self.assertEqual(attrs["hot_water_amount"], 32_812)
        self.assertEqual(attrs["gc_total"], 190_475)


if __name__ == "__main__":
    unittest.main()
