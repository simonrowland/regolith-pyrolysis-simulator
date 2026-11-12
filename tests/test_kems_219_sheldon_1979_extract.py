"""Pins Sheldon and Gilles (1979) Table 1 relative ionization cross-sections as context."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kems-219-sheldon-1979.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_sheldon_1979_table1_and_gains_are_context_only() -> None:
    document = _document()
    assert document["source_id"] == "kems-219-sheldon-1979"
    assert document["source"]["year"] == 1979
    assert document["review_status"] == "draft"
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    block = document["species"]["titanium_oxides"]
    assert block["observations"] == []
    records = {row["observation_id"]: row for row in block["context"]}
    assert set(records) == {
        "sheldon_1979_experimental_method",
        "sheldon_1979_table1_relative_cross_sections",
        "sheldon_1979_multiplier_gains",
        "sheldon_1979_titanium_vaporization_time_and_systematic_error",
        "sheldon_1979_tio2_measurements",
        "sheldon_1979_discussion_fragmentation",
    }

    method = records["sheldon_1979_experimental_method"]["values"]
    assert method["electron_energies_eV_as_printed"][0] == "9.7"
    assert method["electron_energies_eV_as_printed"][-1] == "71.7"
    assert "1.25 mg of silver and 1.30 mg of titanium" in method["ti_ag_experiment_as_printed"]
    assert "Ti3O5.015" in method["ti3o5_sample_as_printed"]
    assert method["previous_pressures_reprinted"] is False

    table = records["sheldon_1979_table1_relative_cross_sections"]["values"]
    rows = {row["electron_energy_eV"]: row for row in table["rows"]}
    assert rows["9.7"]["sigma_Ti_gamma_Ti_over_sigma_Ag_gamma_Ag"] == "2.32"
    assert rows["13.7"]["sigma_TiO_over_sigma_Ti"] == "1.17"
    assert rows["21.7"]["sigma_TiO_gamma_TiO_over_sigma_Ag_gamma_Ag"] == "2.41"
    assert rows["31.7"]["sigma_Ti_over_sigma_Ag"] == "0.81"
    assert rows["71.7"]["sigma_TiO_over_sigma_Ti"] == "2.35"
    assert len(table["rows"]) == 7

    gains = records["sheldon_1979_multiplier_gains"]["values"]
    assert gains["gamma_Ti_as_printed"] == "(4.30 ± 0.08) × 10^4"
    assert gains["gamma_Ag_as_printed"] == "(2.68 ± 0.02) × 10^4"

    tio2 = records["sheldon_1979_tio2_measurements"]["values"]
    assert "1/2.2" in tio2["ratio_from_reference_4_as_printed"]
    assert tio2["scan_crop_as_printed"] == "not_printed"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
