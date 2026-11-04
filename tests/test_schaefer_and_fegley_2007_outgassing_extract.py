"""Source-grounded evidence pins for Schaefer and Fegley (2007)."""

from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schaefer-and-fegley-2007-icarus-outgassing-of-oc.yaml"


def test_authors_model_claims_are_model_derived_with_located_inputs(tmp_path) -> None:
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

    for observation_id in expected_ids:
        observation = observations[observation_id]
        values = observation["values"]
        assert values["evidence_class"] == "model_derived"
        assert values["method_class"] == "model_derived"
        derivation = observation["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["published_page"] == 463
        assert derivation["raw_inputs"]["locator"]["section"] == "2.1 Chemical equilibrium calculations"
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

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    for observation_id in expected_ids:
        migrated = migrator.result.observations[f"{EXTRACT.stem}::{observation_id}"]
        assert migrated.evidence.class_.is_value
        assert migrated.evidence.class_.value is EvidenceClass.MODEL_DERIVED
