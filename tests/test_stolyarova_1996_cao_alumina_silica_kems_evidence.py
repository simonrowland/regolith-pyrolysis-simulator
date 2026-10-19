"""Regression pins for Stolyarova et al. (1996) oxygen evidence and apparatus."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, MetricOperation, ResidualStatus
from simulator.battery.score import (
    _e15_residual_point,
    _reference_has_measured_evidence,
)
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
    for observation in observations:
        derivation = observation["values"]["derivation"]
        assert derivation["method"]["state"]["value"].startswith(
            "K_ref = Gurvich K°(T)"
        )
        assert derivation["method"]["locator"]["equation"] == "7–9"
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["note"] == (
            "The row-wise ion-current inputs are not printed."
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
    assert all(observation.derived_from is None for observation in oxygen_rows)
    assert all(
        observation.derivation is not None
        and observation.derivation.has_located_unprinted_raw_inputs
        and observation.derivation.has_located_method
        for observation in oxygen_rows
    )
    for observation in oxygen_rows:
        temperature = observation.identity.temperature_K
        assert temperature is not None and temperature.is_value
        assert temperature.value == Decimal("1933")
        composition = (observation.point_conditions or {}).get("composition")
        assert composition is not None and composition.state.is_value
        assert composition.locator is not None
    oxygen_paths = tuple(
        f"observation[{observation.observation_id}]"
        for observation in oxygen_rows
    )
    assert not [
        issue
        for issue in migrated.validation.hard_issues
        if issue.path.startswith(oxygen_paths)
    ]

    e15_probe = {
        "status": ResidualStatus.MATCH.value,
        "numeric": {"value": "1", "operation": MetricOperation.DEX.value},
    }
    assert all(
        not _reference_has_measured_evidence(observation)
        and _e15_residual_point(e15_probe, observation) is None
        for observation in oxygen_rows
    )
