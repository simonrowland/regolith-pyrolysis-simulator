from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery import score
from simulator.battery.enums import Phase, Quantity
from simulator.battery.fo2_ladder import has_printed_fo2
from simulator.battery.migrate import Migrator
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kennedy-1948-ferric-ferrous-silicate-melts.yaml"


def _migrate_extract(path: Path = EXTRACT):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def test_kennedy_table2_runs_migrate_with_printed_conditions_and_composition():
    source_text = EXTRACT.read_text(encoding="utf-8")
    source = yaml.safe_load(source_text)
    assert yaml_mapping_source_issues(source_text) == []
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []

    result = _migrate_extract()
    assert len(result.observations) == 22
    assert result.registry_issues == []
    assert len(result.queue) == 1
    assert result.queue[0].axes == ["phase"]
    assert result.queue[0].why == "missing phase"
    observations = list(result.observations.values())
    assert all(o.identity.quantity.value is Quantity.FE3_FE2_RATIO for o in observations)
    assert all(o.value.kind.value == "point" for o in observations)
    assert all(o.identity.temperature_K.is_value for o in observations)
    assert all(o.identity.composition.is_value for o in observations)
    assert all(o.admission.status.value == "pending" for o in observations)
    assert all(
        o.point_conditions["T_C"].state.is_value
        and o.point_conditions["T_C"].locator.pdf_page_index == 5
        and o.point_conditions["T_C"].locator.table == "II"
        for o in observations
    )
    assert all(
        o.point_conditions["fO2_control"].state.value
        == "1/5 atmosphere of oxygen"
        and o.point_conditions["fO2_control"].locator.pdf_page_index == 3
        for o in observations
    )
    assert all(
        o.identity.fO2_Pa.is_value and o.identity.fO2_Pa.value == Decimal("20265")
        for o in observations
    )
    for observation in observations:
        pressure = observation.point_conditions["fO2_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value.point == Decimal("20265")
        assert pressure.inference is not None
        assert pressure.inference.relation == "atm_to_Pa"
        assert "P_Pa = P_atm × 101325" in pressure.inference.inputs
        assert "as_published=1/5 atmosphere of oxygen" in pressure.inference.inputs
        assert "printed=point_conditions.fO2_control" in pressure.inference.inputs
        assert "convention=ideal-gas:fO2=pO2" in pressure.inference.inputs
        factor = dict(pressure.inference.parameters)["factor"]
        assert factor.state.value == Decimal("101325")
        assert factor.locator.published_page == 532
        assert pressure.locator.pdf_page_index == 3
        assert pressure.locator.published_page == 532
        assert has_printed_fo2(observation, observation.identity)
        assert score._printed_fe_redox_fo2_log(
            observation, observation.identity
        ) == Decimal("-0.6932533919222882")
    assert all(
        o.point_conditions["duration_h"].state.is_value
        and o.point_conditions["duration_h"].locator.table == "II"
        for o in observations
    )
    assert len(source["species"]["Fe"]["observations"][0]["values"]["rows"]) == 22

    def row_at(temperature: str, duration: str):
        matches = [
            observation
            for observation in observations
            if observation.point_conditions["T_C"].state.value
            == Decimal(temperature)
            and observation.point_conditions["duration_h"].state.value
            == Decimal(duration)
        ]
        assert len(matches) == 1
        return matches[0]

    transient = row_at("1300", "2")
    equilibrium = row_at("1300", "16")
    crystal_bearing = row_at("1200", "16")
    non_equilibrium = row_at("1100", "240")
    assert transient.identity.species.phase.value is Phase.L
    assert "transient" in transient.point_conditions["regime_as_printed"].state.value
    assert (
        transient.point_conditions["regime_as_printed"].locator.published_page
        == 534
    )
    assert equilibrium.identity.species.phase.value is Phase.L
    assert "equilibrium" in equilibrium.point_conditions["regime_as_printed"].state.value
    assert (
        equilibrium.point_conditions["regime_as_printed"].locator.published_page
        == 534
    )
    assert crystal_bearing.identity.species.phase.is_unknown
    assert (
        "bulk_composition_in_two_phase_region"
        in crystal_bearing.identity.species.phase.reason
    )
    assert (
        "magnetite"
        in crystal_bearing.point_conditions["phase_as_printed"].state.value
    )
    assert (
        crystal_bearing.point_conditions["phase_as_printed"].locator.published_page
        == 541
    )
    assert "transient" in crystal_bearing.point_conditions["regime_as_printed"].state.value
    assert non_equilibrium.identity.species.phase.is_unknown
    assert "not at equilibrium" in non_equilibrium.point_conditions["regime_as_printed"].state.value
    assert (
        non_equilibrium.point_conditions["phase_as_printed"].locator.published_page
        == 534
    )
    assert all(
        o.admission.status.value == "pending"
        for o in (transient, crystal_bearing, non_equilibrium)
    )

    experiment = next(iter(result.experiments.values()))
    ambient = experiment.ambient_pressure_evidence
    assert ambient is not None
    assert ambient.locator.published_page == 539
    assert ambient.locator.pdf_page_index == 10
    assert "surface exposed to the air" in ambient.quote
    assert all(
        o.identity.total_pressure_Pa.value == Decimal("101325")
        for o in observations
    )


def test_kennedy_fidelity_pin_detects_a_changed_table2_cell(tmp_path: Path):
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(source)
    row = mutated["species"]["Fe"]["observations"][0]["values"]["rows"][0]
    row["FeO_wt_pct"] = "3.33"
    path = tmp_path / EXTRACT.name
    path.write_text(yaml.safe_dump(mutated, sort_keys=False), encoding="utf-8")

    errors = validate_extract_file(path, check_fidelity_match=True)
    assert any("fidelity sample value mismatch" in error for error in errors)
