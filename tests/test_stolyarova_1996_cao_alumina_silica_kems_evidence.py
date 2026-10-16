"""Regression pins for Stolyarova et al. (1996) oxygen evidence and apparatus."""

from pathlib import Path
from copy import deepcopy

import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "stolyarova-1996-cao-alumina-silica-kems.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME


def test_table2_oxygen_routes_keep_calculation_and_apparatus_evidence(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        observation
        for observation in document["species"]["O"]["observations"]
        if observation["observation_id"].startswith("stolyarova_1996_table2_")
    ]
    assert len(observations) == 3
    assert [observation["values"]["method_class"] for observation in observations] == [
        "quoted_attributed",
        "quoted_attributed",
        "quoted_attributed",
    ]
    assert [observation["reaction_as_printed"] for observation in observations] == [
        "(MoO3) = (MoO2) + (O)",
        "(AlO) = (Al) + (O)",
        "(SiO2) = (SiO) + (O)",
    ]
    assert [observation["values"]["points"][0][key] for observation, key in zip(
        observations,
        (
            "P_O_times_10^8_atm_as_printed",
            "P_O_prime_times_10^8_atm_as_printed",
            "P_O_double_prime_times_10^8_atm_as_printed",
        ),
    )] == ["0.36", "0.40", "0.16"]
    assert all(
        observation["standard_state"]["tag"] == "unknown"
        for observation in observations
    )

    bench = document["benches"][0]
    assert bench["cell_material_and_liner"]["state"]["tag"] == "unknown"
    assert bench["cell_materials"][0]["state"]["value"] == "Mo"
    assert bench["cell_materials"][0]["inferred"] is True
    assert "Inferred from the identified Mo+" in bench["cell_materials"][0]["inference"]
    calibration = document["experiments"][0]["apparatus"]["calibration"]
    assert calibration["method"]["state"]["value"] == (
        "ion-current comparison using silver vapor pressure as standard"
    )
    assert calibration["reference_substance"]["state"]["value"] == "silver vapor"

    focused_document = deepcopy(document)
    focused_document["species"] = {"O": focused_document["species"]["O"]}
    migrated = _migrate_real_extract(
        tmp_path, EXTRACT_NAME, extract_doc=focused_document
    )
    assert migrated.validation is not None
    assert migrated.validation.hard_issues == ()
    oxygen_rows = [
        observation
        for observation in migrated.observations.values()
        if "stolyarova_1996_table2_pO" in observation.observation_id
    ]
    assert len(oxygen_rows) == 55
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        for observation in oxygen_rows
    )
