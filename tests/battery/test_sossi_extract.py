from __future__ import annotations

from copy import deepcopy
from math import isclose, pi, sqrt
from pathlib import Path

import pytest

from tests.battery.test_migrate import _migrate_real_extract
from tools.lineage_wave_check import _load_source_extract


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
    _assert_collector_facts(facts)
    assert facts["collector_oxidation_state"]["value"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }
    assert facts["oxygen_escaped"]["value"]["state"] == {
        "tag": "unknown",
        "reason": "not_published",
    }


def _assert_collector_facts(facts: dict) -> None:
    numeric = {
        "collector_fe_loss": ("percent Fe loss", 11, {
            "kind": "point", "point": "5", "approximate": True,
        }),
        "collector_ag_to_pt_ratio": ("dimensionless Ag/Pt ratio", 12, {
            "kind": "bound", "bound_operator": "<", "bound_value": "2e-4",
        }),
        "collector_pb_to_pt_ratio": ("dimensionless Pb/Pt ratio", 12, {
            "kind": "bound", "bound_operator": "<", "bound_value": "7e-4",
        }),
        "collector_ga_to_pt_ratio": ("dimensionless Ga/Pt ratio", 12, {
            "kind": "bound", "bound_operator": "<", "bound_value": "7e-4",
        }),
        "collector_ga_to_pt_ratio_2m_17_07_16c": (
            "dimensionless Ga/Pt ratio", 12,
            {"kind": "interval", "interval_low": "2.3e-3", "interval_high": "2.5e-3"},
        ),
        "collector_cu_to_pt_ratio": ("dimensionless Cu/Pt ratio", 12, {
            "kind": "interval", "interval_low": "1.8e-3", "interval_high": "7.6e-3",
        }),
        "collector_cu_diffusion_depth": ("μm", 12, {
            "kind": "point", "point": "9", "approximate": True,
        }),
        "collector_wire_diameter": ("mm", 12, {
            "kind": "point", "point": "0.1",
        }),
        "collector_wire_segment_mass": ("mg", 12, {
            "kind": "point", "point": "0.84", "approximate": True,
        }),
        "collector_wire_mass_fraction": ("percent of sample mass", 12, {
            "kind": "point", "point": "7", "approximate": True,
        }),
        "collector_cu_budget": ("percent Cu budget", 12, {
            "kind": "point", "point": "9", "approximate": True,
        }),
    }
    for name, (unit, page, expected) in numeric.items():
        fact = facts[name]
        assert fact["unit"] == unit
        assert fact["value"]["state"] == {"tag": "value", "value": expected}
        assert fact["value"]["locator"]["page"] == page

    glass = {
        "glass_ga_concentration_p09_06_18b": ("ppm Ga", "439.9", "P09/06/18b", "Pt-loop"),
        "glass_ga_concentration_p09_06_18a": ("ppm Ga", "409.4", "P09/06/18a", "Re-loop"),
        "glass_cu_concentration_p09_06_18b": ("ppm Cu", "40.5", "P09/06/18b", "Pt-loop"),
        "glass_cu_concentration_p09_06_18a": ("ppm Cu", "11.2", "P09/06/18a", "Re-loop"),
    }
    for name, (unit, point, run, loop) in glass.items():
        fact = facts[name]
        assert fact["unit"] == unit
        assert fact["value"]["state"] == {
            "tag": "value", "value": {"kind": "point", "point": point},
        }
        locator = fact["value"]["locator"]
        assert locator["page"] == 54 and locator["table"] == "2"
        assert run in locator["note"] and loop in locator["note"]
        assert "quenched-glass" in locator["note"]
        assert "no resolvable effect" in locator["note"]
    assert "collector_ga_ppm" not in facts
    assert "collector_cu_ppm" not in facts

    for name, relation, output_unit in (
        ("collector_cu_diffusion_depth", "diffusion_depth_from_coefficient_and_duration", "μm"),
        ("collector_wire_segment_mass", "author_calculated_wire_segment_mass_from_geometry", "mg"),
        ("collector_wire_mass_fraction", "wire_segment_mass_fraction_of_sample", "percent of sample mass"),
        ("collector_cu_budget", "wire_cu_budget_from_affected_zone_assumption", "percent Cu budget"),
    ):
        located = facts[name]["value"]
        assert located["inference"]["relation"] == relation
        assert located["inference"]["output_unit"] == output_unit
        assert "Evidence class: author_estimate" in located["locator"]["note"]
    for name in ("collector_wire_segment_mass", "collector_wire_mass_fraction"):
        assert facts[name]["value"]["inference"]["raw_inputs"]["state"] == {
            "tag": "value", "value": "not_printed",
        }

    ag_note = facts["collector_ag_to_pt_ratio"]["value"]["locator"]["note"]
    assert "1M-PS4 (1400 degC, 15 min, logfO2=-8.00)" in ag_note
    assert "1M-PS5 (1400 degC, 15 min, logfO2=-10.01)" in ag_note
    assert "2M-15-07-16f (1550 degC, 15 min, logfO2=-0.68)" in ag_note


def test_sossi_table2_gas_values_and_mutation_gate() -> None:
    document = _load_source_extract(EXTRACT)
    _assert_sossi_gas(document)

    mutated = deepcopy(document)
    experiment = next(e for e in mutated["experiments"] if _run_slug(e) == "1m-ps4")
    co = experiment["pressure_environment"]["sweep_gas"]["state"]["value"]["components"][0]
    co["flow_sccm"]["value"] = "67"
    with pytest.raises(AssertionError):
        _assert_sossi_gas(mutated)


def test_sossi_area_arithmetic_and_mutation_gate() -> None:
    document = _load_source_extract(EXTRACT)
    _assert_derived_area(document)

    mutated = deepcopy(document)
    mutated["experiments"][0]["sample"]["surface_area_m2"]["state"]["value"]["point"] = "3e-5"
    with pytest.raises(AssertionError):
        _assert_derived_area(mutated)


def test_sossi_root_context_and_analysis_basis_are_pinned() -> None:
    text = EXTRACT.read_text(encoding="utf-8")
    document = _load_source_extract(EXTRACT)
    _assert_source_root_context(document, text)


def test_sossi_collector_numbers_reject_mutation() -> None:
    document = _load_source_extract(EXTRACT)
    facts = {item["name"]: item for item in document["benches"][0]["other_facts"]}
    _assert_collector_facts(facts)

    for name in ("glass_ga_concentration_p09_06_18b", "collector_cu_diffusion_depth"):
        mutated = deepcopy(facts)
        value = mutated[name]["value"]["state"]["value"]
        value["point"] = "999999"
        with pytest.raises(AssertionError):
            _assert_collector_facts(mutated)


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
