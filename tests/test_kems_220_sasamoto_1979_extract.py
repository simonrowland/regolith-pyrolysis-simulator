"""Pins Sasamoto, Kobayashi and Sata (1979) liquid GeO2 parent-species ratios."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-220-sasamoto-1979.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_sasamoto_1979_ratios_aps_and_deferred_figures() -> None:
    document = _document()
    assert document["source_id"] == "kems-220-sasamoto-1979"
    assert document["source"]["year"] == 1979
    assert document["review_status"] == "draft"
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    block = document["species"]["GeO2"]
    obs = {row["observation_id"]: row for row in block["observations"]}
    assert set(obs) == {"sasamoto_1979_parent_species_abundance_ratios"}
    values = obs["sasamoto_1979_parent_species_abundance_ratios"]["values"]
    assert values["method_class"] == "kems"
    assert values["ratios"]["GeO2_g_over_GeO_g"] == "0.001"
    assert values["ratios"]["Ge3O3_g_over_GeO_g"] == "0.002"
    assert values["temperature_C_as_printed"] == "about 1300"

    ctx = {row["observation_id"]: row for row in block["context"]}
    method = ctx["sasamoto_1979_experimental_method"]["values"]
    assert "99.99 percent purity" in method["sample_as_printed"]
    assert method["effusion_hole_diameter_as_printed"] == "0.3 mm"
    assert "Matauch-Herzog" in method["instrument_as_printed"]

    aps = ctx["sasamoto_1979_observed_ions_and_appearance_potentials"]["values"]["appearance_potentials_eV"]
    assert aps["Ge_plus"] == "16.4"
    assert aps["GeO_plus"] == "13.4"
    assert aps["GeO2_plus"] == "13.4"
    assert aps["Ge2O2_plus"] == "15.8 ± 3"
    assert aps["Ge3O3_plus"] == "12.4"
    assert ctx["sasamoto_1979_observed_ions_and_appearance_potentials"]["values"]["drowart_values_reprinted"] is False

    eq2 = ctx["sasamoto_1979_estimated_geo2_vapor_pressure"]["values"]
    assert "6.05" in eq2["equation_as_printed"]
    assert "1.88 x 10^4" in eq2["equation_as_printed"]
    assert eq2["supporting_TP_table"] == "not_printed"

    figures = ctx["sasamoto_1979_figures"]["values"]
    assert figures["digitisation"] == "deferred to a figure pass"
    by_id = {row["id"]: row for row in figures["figures_as_printed"]}
    assert by_id["2"]["ionizing_volt_as_printed"] == "43 eV"
    assert by_id["2"]["temperature_as_printed"] == "1380 °C"
    assert by_id["2"]["isotope_abundance_ratios_printed_on_figure"]["74Ge"] == "0.3654"
    assert by_id["3"]["temperature_as_printed"] == "1160 °C"
    assert "2,600" in ctx["sasamoto_1979_discussion_resolution"]["values"]["sasamoto_response_as_printed"]

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert len(migrator.result.observations) == 1
    assert not migrator.result.validation.hard_issues
