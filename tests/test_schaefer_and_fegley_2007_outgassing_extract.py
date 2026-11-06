"""Source-grounded evidence pins for Schaefer and Fegley (2007)."""

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schaefer-and-fegley-2007-icarus-outgassing-of-oc.yaml"


def test_authors_model_claims_have_located_methods_and_resolved_inputs() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = {
        observation["observation_id"]: observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
    }
    expected_ids = (
        "schaefer_2007_ch4_major_gas_model_claim",
        "schaefer_2007_gas_chemistry_figures_figure_only",
    )
    composition_parents = [
        "schaefer_2007_table1_h_chondrite_falls",
        "schaefer_2007_table1_l_chondrite_falls",
        "schaefer_2007_table1_ll_chondrite_falls",
    ]

    for observation_id in expected_ids:
        observation = observations[observation_id]
        values = observation["values"]
        assert values["evidence_class"] == "model_derived"
        assert values["method_class"] == "model_derived"
        derivation = observation["derivation"]
        assert "raw_inputs" not in derivation
        assert observation["derived_from"] == composition_parents
        assert derivation["inputs"] == composition_parents
        assert "Gibbs energy minimization" in derivation["method"]["state"]["value"]
        assert derivation["method"]["locator"]["published_page"] == 463
        assert derivation["method"]["locator"]["section"] == "2.1 Chemical equilibrium calculations"
        assert derivation["references"] == [
            "Van Zeggern and Storey (1970)",
            "Belov et al. (1999)",
            "Koblitz (2005)",
            "Ghosh et al. (2003)",
        ]

    assert observations[expected_ids[1]]["values"]["admission_status"] == "figure_only"
    assert observations[expected_ids[0]]["values"]["major_C_bearing_gas_during_metamorphism"] == "CH4"
    assert observations[expected_ids[0]]["basis_locator"]["published_page"] == 468
    assert "major carbon-bearing species at all temperatures" in observations[expected_ids[0]]["basis_locator"]["note"]

    hebes = observations["schaefer_2007_hebe_tp_program_printed"]
    assert hebes["derived_from"] == ["schaefer_2007_table1_h_chondrite_falls"]
    assert hebes["derivation"]["inputs"] == hebes["derived_from"]
    assert hebes["derivation"]["method"]["locator"]["equation"] == "1"
    assert hebes["derivation"]["method"]["locator"]["published_page"] == 466

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues == ()
    for observation_id in expected_ids:
        migrated = migrator.result.observations[f"{EXTRACT.stem}::{observation_id}"]
        assert migrated.evidence.class_.is_value
        assert migrated.evidence.class_.value is EvidenceClass.MODEL_DERIVED


def test_unresolved_model_composition_parent_is_a_hard_issue(tmp_path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        observation
        for species in document["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == "schaefer_2007_ch4_major_gas_model_claim"
    )
    target["derived_from"] = ["missing_composition_parent"]
    target["derivation"]["inputs"] = ["missing_composition_parent"]
    mutated = tmp_path / EXTRACT.name
    mutated.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")

    migrator = migrate_one_extract(ROOT, mutated, index={}, aliases={})
    migrator.finalize()
    assert migrator.result.validation.hard_issues
