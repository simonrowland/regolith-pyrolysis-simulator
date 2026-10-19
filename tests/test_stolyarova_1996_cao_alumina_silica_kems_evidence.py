"""Regression pins for Stolyarova et al. (1996) oxygen evidence and apparatus."""

from pathlib import Path
from copy import deepcopy

import yaml

from simulator.battery.enums import EvidenceClass, RefusalReason
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
    # Published p. 19 calculates p(O), p'(O) and p''(O) from this study's ion
    # currents, Gurvich et al. (66) equilibrium constants, and Mann (67)
    # cross-sections, Eqs. (7)–(9). Table 2 footnote a (p. 20) says those
    # columns were calculated. The row inputs are not printed.
    method = document["extraction"]["method"]
    assert "quoted" not in method.casefold()
    assert "Eqs. (7)–(9)" in method
    assert "footnote a" in method
    assert "Gurvich" in method
    assert "Mann" in method
    assert "not printed" in method
    assert [observation["values"]["method_class"] for observation in observations] == [
        "model_derived",
        "model_derived",
        "model_derived",
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
    oxygen_rows = [
        observation
        for observation in migrated.observations.values()
        if "stolyarova_1996_table2_pO" in observation.observation_id
    ]
    assert len(oxygen_rows) == 55
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MODEL_DERIVED
        for observation in oxygen_rows
    )
    # Eqs. (7)–(9) use unprinted ion currents, so these model_derived rows have
    # no legal derived_from parent. Pin that gap exactly; do not invent parents.
    derived_from_detail = "derived observation requires derived_from"
    issue_ids = []
    for issue in migrated.validation.hard_issues:
        assert issue.reason is RefusalReason.CONDITIONAL_FIELD
        assert issue.detail == derived_from_detail
        prefix = "observation["
        suffix = "].derived_from"
        assert issue.path.startswith(prefix) and issue.path.endswith(suffix)
        issue_ids.append(issue.path[len(prefix):-len(suffix)])
    assert sorted(issue_ids) == sorted(
        observation.observation_id for observation in oxygen_rows
    )
    assert len(issue_ids) == len(set(issue_ids)) == 55
    assert {
        observation_id.split("::")[1]
        for observation_id in issue_ids
    } == {
        "stolyarova_1996_table2_pO_1933k",
        "stolyarova_1996_table2_pO_prime_1933k",
        "stolyarova_1996_table2_pO_double_prime_1933k",
    }
    assert [
        sum(observation_id.split("::")[1] == series for observation_id in issue_ids)
        for series in (
            "stolyarova_1996_table2_pO_1933k",
            "stolyarova_1996_table2_pO_prime_1933k",
            "stolyarova_1996_table2_pO_double_prime_1933k",
        )
    ] == [30, 10, 15]
