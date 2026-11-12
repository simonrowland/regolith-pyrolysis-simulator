"""Pins Schaefer & Fegley (2010) Icarus accretion atmospheres as context-only model output."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sf10-accretion-2010.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_sf10_2010_table1_is_context_only_model_output() -> None:
    document = _document()
    assert document["source_id"] == "sf10-accretion-2010"
    assert document["source"]["year"] == 2010
    assert document["review_status"] == "draft"
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    block = document["species"]["impact_generated_atmosphere"]
    assert block["observations"] == []
    records = {row["observation_id"]: row for row in block["context"]}
    assert set(records) == {
        "sf10_2010_methods_and_nominal_conditions",
        "sf10_2010_table1_major_gas_compositions",
        "sf10_2010_printed_hcl_and_mixing_model",
        "sf10_2010_figure1",
        "sf10_2010_figures_2_to_6",
    }

    methods = records["sf10_2010_methods_and_nominal_conditions"]["values"]
    assert "Van Zeggern and Storey (1970)" in methods["calculation_as_printed"]
    assert "Fegley, 1972" not in methods["calculation_as_printed"]
    assert methods["nominal_T_K"] == 1500
    assert methods["nominal_P_bar"] == 100
    assert "same database as in Schaefer and Fegley (2007)" in methods["database_as_printed"]

    table = records["sf10_2010_table1_major_gas_compositions"]["values"]
    assert table["method_class"] == "model_derived"
    assert table["conditions_as_printed"] == "1500 K and 100 bars"
    gases = table["composition_vol_percent"]
    assert gases["H2"]["CI"] == "4.36"
    assert gases["H2"]["H"] == "48.49"
    assert gases["H2O"]["CM"] == "73.38"
    assert gases["CH4"]["CI"] == "2 × 10−7"
    assert gases["CO"]["EL"] == "67.00"
    assert gases["N2"]["EH"] == "1.31"
    assert gases["SO2"]["CV"] == "7.41"
    assert gases["Total"]["CI"] == "99.99"
    assert gases["Total"]["EL"] == "99.98"

    hcl = records["sf10_2010_printed_hcl_and_mixing_model"]["values"]
    assert "0.44%" in hcl["HCl_as_printed"]
    assert "0.06%" in hcl["HCl_as_printed"]
    assert "0.20%" in hcl["HCl_as_printed"]
    assert "70% EH, 21% H, 5% CV, and 4% CI" in hcl["oxygen_isotope_mixing_model_as_printed"]

    assert records["sf10_2010_figure1"]["values"]["digitisation"] == "deferred to a figure pass"
    assert records["sf10_2010_figure1"]["values"]["numeric_points"] == "none"
    fig_ids = [
        row["id"]
        for row in records["sf10_2010_figures_2_to_6"]["values"]["figures_as_printed"]
    ]
    assert fig_ids == ["2", "3", "4", "5", "6"]
    assert records["sf10_2010_figures_2_to_6"]["values"]["digitisation"] == "deferred to a figure pass"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
