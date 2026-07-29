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
diagnostics = _load_module("extraction_diagnostics")
extractor = _load_module("attribute_extractor")


class AddonStructuredExtractionTests(unittest.TestCase):
    """Verify extraction survives compatible add-on schema variations."""

    def test_email_bill_issue_date_is_extracted_from_provider_labels(self) -> None:
        attrs = extractor.extract_attributes_from_email_body(
            "Tu boleta",
            "Fecha de emisión: 20/07/2026\nFecha de vencimiento: 31/07/2026",
        )
        self.assertEqual(attrs["emission_date"], "20-07-2026")

    def test_enel_boleta_date_is_not_confused_with_email_transport_date(self) -> None:
        attrs = extractor.extract_attributes_from_email_body(
            "Documento disponible",
            "N° Boleta 12345678 del 18-07-2026",
        )
        self.assertEqual(attrs["emission_date"], "18-07-2026")

    def test_compact_water_bill_populates_every_water_breakdown_field(self) -> None:
        text = """CARGO FIJO
CONSUMO AGUA POTABLE
RECOLECCION AGUAS SERVIDAS
TRATAMIENTO AGUAS SERVIDAS
SUBTOTAL SERVICIO
TOTAL VENTA
DESCUENTO LEY REDONDEO

TOTAL A PAGAR

8,27
8,27
8,27

944
5.056
3.817
2.574
12.391
12.391
-1

CONSUMO TOTAL
8,27 m3
MODALIDAD DE PRORRATEO
Cargo fijo = $ 944
Metro cúbico agua potable punta = $ 605,25
Metro cúbico agua potable no punta = $ 611,48
FECHA EMISIÓN:02-JUL-2026
"""
        attrs = extractor._extract_water_pdf_attributes(text)
        self.assertEqual(attrs["water_consumption_non_peak_m3"], 8.27)
        self.assertEqual(attrs["water_consumption_non_peak"], 5056)
        self.assertEqual(attrs["water_consumption_peak_m3"], 0.0)
        self.assertEqual(attrs["water_consumption_peak"], 0)
        self.assertEqual(attrs["wastewater_recolection"], 3817)
        self.assertEqual(attrs["wastewater_treatment"], 2574)
        self.assertEqual(attrs["cost_per_unit_non_peak"], 611.48)
        self.assertEqual(attrs["cost_per_unit_peak"], 605.25)
        self.assertEqual(attrs["subtotal"], 12391)
        self.assertEqual(attrs["total_amount"], 12390)

    def test_enel_bill_without_stabilization_row_populates_all_sensors(self) -> None:
        text = """Total a pagar:
Monto del periodo 29 May 2026 - 26 Jun 2026

$83.222

Servicio Eléctrico
Administración del servicio
Electricidad Consumida (346kWh)
Transporte de electricidad

$
$
$

725
76.678
5.820

Período de lectura: 29/05/2026 - 26/06/2026
"""
        attrs = extractor._extract_electricity_pdf_attributes(text)
        self.assertEqual(attrs["consumption"], 346)
        self.assertEqual(attrs["consumption_unit"], "kWh")
        self.assertEqual(attrs["service_administration"], 725)
        self.assertEqual(attrs["electricity_consumption"], 76678)
        self.assertEqual(attrs["electricity_transport"], 5820)
        self.assertEqual(attrs["stabilization_fund"], 0)
        self.assertEqual(attrs["cost_per_kwh"], 221.61)
        self.assertEqual(attrs["total_amount"], 83222)

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

    def test_real_template_shape_explains_missing_sensor_values(self) -> None:
        """Null template cells stay missing even when their section matched."""
        response = {
            "template_id": "coe_administraciones",
            "document_type": "gasto_comun",
            "sections": {
                "fechas_emision": {
                    "fecha_emision": "20-07-2026",
                    "pagar_hasta": "31-07-2026",
                },
                "tabla_nota_cobro": {
                    "copropietario": "JORGE CORDERO ORELLANA",
                    "alicuota_total": "0,95110%",
                    "gasto_comun_a_prorratear": "$ 14.171.762",
                },
                "tabla_desglose_departamento": {
                    "gasto_comun_monto": None,
                    "provision_fondos_monto": None,
                    "subtotal_departamento": None,
                },
                "tabla_consumos_generales": {},
                "tabla_gastos_por_unidad": {
                    "cargo_fijo": None,
                    "total_del_mes": None,
                },
            },
            "meta": {
                "matched_sections": {
                    "tabla_desglose_departamento": {
                        "matched": True,
                        "score": 0.992,
                        "line_results": {
                            "gasto_comun_monto": {"value": None},
                        },
                    }
                }
            },
        }

        attrs = extractor.extract_attributes_from_addon_ocr_json(response)

        self.assertEqual(attrs["emission_date"], "20-07-2026")
        self.assertEqual(attrs["due_date"], "31-07-2026")
        self.assertEqual(attrs["owner_name"], "JORGE CORDERO ORELLANA")
        self.assertEqual(attrs["gastos_comunes_amount"], 134_788)
        self.assertNotIn("funds_provision", attrs)
        self.assertNotIn("fixed_charge", attrs)
        self.assertNotIn("gc_total", attrs)
        self.assertTrue(diagnostics.common_expenses_needs_fallback(attrs))

    def test_meta_line_results_fill_missing_canonical_section_values(self) -> None:
        response = {
            "sections": {"tabla_gastos_por_unidad": {"cargo_fijo": None}},
            "meta": {
                "matched_sections": {
                    "tabla_gastos_por_unidad": {
                        "line_results": {
                            "cargo_fijo": {"value": "$ 16.136"},
                            "total_del_mes": {"value": "$ 190.475"},
                        }
                    }
                }
            },
        }

        attrs = extractor.extract_attributes_from_addon_ocr_json(response)

        self.assertEqual(attrs["fixed_charge"], 16_136)
        self.assertEqual(attrs["gc_total"], 190_475)


if __name__ == "__main__":
    unittest.main()
