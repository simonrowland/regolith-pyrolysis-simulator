from __future__ import annotations

from copy import deepcopy
from math import isclose, pi, sqrt
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract
from tests.battery.test_t1199type_hashimoto import _UniqueKeyLoader


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT = REPO_ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml"
EXPECTED_FLOWS = {
    "2m-15-07-16c": (None, None),
    "2m-15-07-16d": (None, "200"),
    "2m-15-07-16g": ("1.8", "200"),
    "2m-15-07-16h": ("24", "175"),
    "p11-06-18a": (None, "200"),
    "1m-15-07-16a": (None, "200"),
    "1m-15-07-16b": ("1.9", "200"),
    "1m-ps6": ("25", "180"),
    "3m-18-07-16b": (None, None),
    "2m-18-07-16a": (None, "200"),
    "2m-18-07-16c": ("12", "188"),
    "1m-ps3": (None, "180"),
    "1m-ps2": ("5", "180"),
    "1m-ps4": ("66", "133"),
    "p09-06-18b": ("130", "65"),
    "p09-06-18a": ("130", "65"),
    "1m-ps5": ("167", "33"),
    "p08-02-17b": (None, None),
    "3m-29-2-16": (None, None),
    "1m-2-3-16": (None, "200"),
    "c18-12-15": ("0", "27"),
    "1m-ps1": ("5", "180"),
    "p09-06-18c": ("5.5", "195"),
    "c17-12-15a": ("13", "27"),
    "1m-1-3-16": ("167", "33"),
    "3m-1-3-16": (None, None),
    "c17-12-15b": ("13", "27"),
    "1m-1-3-16b": ("167", "33"),
    "3m-29-2-16b": (None, None),
    "c17-12-15c": ("13", "27"),
    "2m-16-07-16e": (None, None),
    "2m-16-07-16a": (None, "200"),
    "2m-16-07-16b": ("16", "184"),
    "2m-16-07-16c": ("120", "80"),
    "3m-2-3-16": (None, None),
    "2m-17-07-16a": (None, "200"),
    "2m-17-07-16b": ("16", "184"),
    "2m-17-07-16c": ("120", "80"),
    "2m-16-07-16f": (None, None),
    "2m-16-07-16g": (None, "200"),
    "p11-06-18b": ("25", "175"),
    "2m-16-07-16h": ("42", "158"),
    "2m-16-07-16d": ("188", "12"),
}
AREA_BASIS = (
    "unit read as m2; corroborated by the printed sample mass "
    "(about 25 mg, p. 5) under a sphere assumption"
)
ANALYTICAL_QUOTE = (
    "Following this initial normalisation, 29Si was used as an internal standard "
    "(41 wt. % SiO2 in samples) to correct for matrix effects between the samples "
    "and NIST-612 by comparison with BCR-2G, GSD-1G, GSE-1G or BIR-1G, whose "
    "matrix is a good analogue for the experimental glasses."
)


def _run_slug(experiment: dict) -> str:
    return experiment["experiment_id"].removeprefix("sossi-2019-run-")


def _assert_sossi_gas(document: dict) -> None:
    experiments = document["experiments"]
    assert len(experiments) == 43
    assert {_run_slug(experiment) for experiment in experiments} == set(EXPECTED_FLOWS)

    for experiment in experiments:
        slug = _run_slug(experiment)
        gas_located = experiment["pressure_environment"]["sweep_gas"]
        assert gas_located["locator"]["table"] == "2"
        assert gas_located["locator"]["page"] in (54, 55)
        assert "Evidence class: measured_tabulated" in gas_located["locator"]["note"]
        gas = gas_located["state"]["value"]
        assert gas["species"] is None
        assert gas["flow_sccm"] == {"tag": "unknown", "reason": "not_published"}
        assert gas["partial_pressure_Pa"] == {
            "tag": "unknown",
            "reason": "not_published",
        }
        components = {item["species"]: item for item in gas["components"]}
        assert set(components) == {"CO", "CO2"}
        for species, expected in zip(("CO", "CO2"), EXPECTED_FLOWS[slug]):
            component = components[species]
            assert component["mole_fraction"] == {
                "tag": "unknown",
                "reason": "not_published",
            }
            assert component["partial_pressure_Pa"] == {
                "tag": "unknown",
                "reason": "not_published",
            }
            actual = component["flow_sccm"]
            if expected is None:
                assert actual == {"tag": "unknown", "reason": "not_published"}
            else:
                assert actual == {"tag": "value", "value": expected}


def _assert_derived_area(document: dict) -> None:
    experiments = document["experiments"]
    assert len(experiments) == 43
    for experiment in experiments:
        sample = experiment["sample"]
        area = sample["surface_area_m2"]
        assert area["state"]["tag"] == "value"
        assert area["state"]["value"] == {
            "kind": "point",
            "point": "2e-5",
            "approximate": True,
        }
        assert area["inference"]["output_unit"] == "m2"
        note = area["locator"]["note"]
        assert "authority: derived" in note
        assert "flagged: true" in note
        assert "measured: false" in note
        assert AREA_BASIS in note

    area_m2 = float(experiments[0]["sample"]["surface_area_m2"]["state"]["value"]["point"])
    radius_m = sqrt(area_m2 / (4 * pi))
    volume_m3 = (4 / 3) * pi * radius_m**3
    mass_kg = volume_m3 * 2650
    diameter_mass_kg = (pi / 6) * area_m2**3 * 2650
    assert isclose(radius_m, 1.26156626e-3, rel_tol=1e-7)
    assert isclose(volume_m3, 8.41044174e-9, rel_tol=1e-7)
    assert isclose(mass_kg, 2.22876706e-5, rel_tol=1e-7)
    assert isclose(diameter_mass_kg, 1.1100294e-11, rel_tol=1e-7)
    assert 2.0e6 < mass_kg / diameter_mass_kg < 2.3e6


def _assert_source_root_context(document: dict, extract_text: str) -> None:
    observation = next(
        item
        for item in document["species"]["Mn"]["observations"]
        if item["observation_id"]
        == "sossi_2019_mn_table2_open_furnace_residue_ppm_quoted_20260906"
    )
    equipment = observation["equipment"]
    printed = equipment["sample_surface_area_as_printed"]
    assert printed["value"] == 2.0e-5
    assert printed["units"] == "m"
    assert printed["qualifier"] == "approximate_dimensional_ambiguity"
    scope = printed["note"]
    assert all(experiment["experiment_id"] in scope for experiment in document["experiments"])

    radius = equipment["sample_radius_model_assumption"]
    assert radius["value"] == 0.001
    assert radius["qualifier"] == "model_assumption_not_measured_geometry"
    assert "used for all experiments" in radius["locator"]["quote"]
    assert "43 scheduled runs linked" in radius["note"]

    calibration = equipment["analytical_calibration"]
    assert calibration["locator"]["quote"] == ANALYTICAL_QUOTE
    assert "Applicability unresolved" in calibration["note"]

    assert "Refuted alternative" in extract_text
    assert "r = sqrt(A/(4 pi))" in extract_text
    assert "factor 2e6" in extract_text

    facts = {item["name"]: item for item in document["benches"][0]["other_facts"]}
    assert "Ga and Cu dissolved into Pt metal" in facts["collector_loop_metal_content"]["value"]["state"]["value"]["categorical"]
    _assert_glass_loop_comparison(facts)
    assert facts["collector_oxidation_state"]["value"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }
    assert facts["oxygen_escaped"]["value"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }


def _assert_glass_loop_comparison(facts: dict) -> None:
    glass = facts["glass_residue_loop_material_comparison"]
    assert glass["unit"] == "qualitative"
    assert glass["value"]["state"]["value"]["categorical"] == (
        "Table 2 quenched-glass analyses: P09/06/18b (Pt loop) has 439.9 ppm Ga "
        "and 40.5 ppm Cu; P09/06/18a (Re loop) has 409.4 ppm Ga and 11.2 ppm Cu. "
        "Section 4.3 compares these similar glass concentrations to argue that "
        "partitioning into the wire has no resolvable effect on melt depletion."
    )
    locator = glass["value"]["locator"]
    assert locator["page"] == 54
    assert locator["table"] == "2"
    assert "quenched glass" in locator["note"]
    assert "not from wire analyses" in locator["note"]
    assert "Section 4.3" in locator["note"]
    assert "collector_ga_ppm" not in facts
    assert "collector_cu_ppm" not in facts


def test_sossi_table2_gas_values_and_mutation_gate() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    document = yaml.load(text, Loader=_UniqueKeyLoader)
    _assert_sossi_gas(document)

    mutated = deepcopy(document)
    experiment = next(e for e in mutated["experiments"] if _run_slug(e) == "1m-ps4")
    co = experiment["pressure_environment"]["sweep_gas"]["state"]["value"]["components"][0]
    co["flow_sccm"]["value"] = "67"
    with pytest.raises(AssertionError):
        _assert_sossi_gas(mutated)


def test_sossi_area_arithmetic_and_mutation_gate() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    document = yaml.load(text, Loader=_UniqueKeyLoader)
    _assert_derived_area(document)

    mutated = deepcopy(document)
    mutated["experiments"][0]["sample"]["surface_area_m2"]["state"]["value"]["point"] = "3e-5"
    with pytest.raises(AssertionError):
        _assert_derived_area(mutated)


def test_sossi_root_context_and_analysis_basis_are_pinned() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    document = yaml.load(text, Loader=_UniqueKeyLoader)
    _assert_source_root_context(document, text)


def test_sossi_glass_loop_attribution_rejects_mutation() -> None:
    document = yaml.load(EXTRACT.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    facts = {item["name"]: item for item in document["benches"][0]["other_facts"]}
    _assert_glass_loop_comparison(facts)

    mutated = deepcopy(facts)
    categorical = mutated["glass_residue_loop_material_comparison"]["value"]["state"]["value"]["categorical"]
    mutated["glass_residue_loop_material_comparison"]["value"]["state"]["value"]["categorical"] = categorical.replace("439.9", "999999")
    with pytest.raises(AssertionError):
        _assert_glass_loop_comparison(mutated)


def test_sossi_fields_survive_isolated_migration(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-012-sossi-2019.yaml")
    assert len(result.observations) == 612
    assert len(result.experiments) == 44
    assert len(result.benches) == 1
    assert result.validation is not None
    assert len(result.validation.hard_issues) == 154

    experiment = result.experiments[
        "10.1016/j.gca.2019.06.021::experiment::sossi-2019-run-2m-15-07-16d"
    ]
    area = experiment.sample.surface_area_m2
    assert area is not None and float(area.state.value.point) == pytest.approx(2e-5)
    assert area.inference is not None and area.inference.output_unit == "m2"
    gas = experiment.pressure_environment.sweep_gas
    assert gas.locator is not None and gas.locator.table == "2"
    components = {component.species: component for component in gas.state.value.components}
    assert components["CO"].flow_sccm.is_unknown
    assert float(components["CO2"].flow_sccm.value) == pytest.approx(200)
    assert components["CO"].mole_fraction.reason == "not_published"
    assert components["CO2"].partial_pressure_Pa.reason == "not_published"

    bench = result.benches["10.1016/j.gca.2019.06.021::bench::gero-open-furnace"]
    facts = {fact.name: fact for fact in bench.other_facts}
    assert "Ga and Cu dissolved into Pt metal" in facts["collector_loop_metal_content"].value.state.value.categorical
    assert facts["collector_oxidation_state"].value.state.reason == "not_published"
    assert facts["oxygen_escaped"].value.state.reason == "not_published"

    observation = next(
        observation
        for observation in result.observations.values()
        if observation.experiment_id == experiment.experiment_id
        and observation.point_conditions
        and "sweep_gas_CO2_flow_sccm" in observation.point_conditions
    )
    observed_area = observation.point_conditions["sample_surface_area_m2"]
    assert float(observed_area.state.value.point) == pytest.approx(2e-5)
    observed_flow = observation.point_conditions["sweep_gas_CO2_flow_sccm"]
    assert float(observed_flow.state.value) == pytest.approx(200)
    assert observed_flow.locator is not None and observed_flow.locator.table == "2"
