"""A fixed-T/P/fO2 point needs ratios; transport still needs absolute charge."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import AmountBasis
from simulator.battery.generators import engine_point_requests, kems_case, vacuum_pyrolysis_preset
from simulator.battery.records import Composition, Located, Sample, State
from simulator.battery.migrate import load_yaml, _sample_from_plain
from simulator.battery.waypoints import GapReason, ReadinessStatus, WaypointAuthority
from tests.battery import factories as f
from tests.battery.test_bench_generators import case, complete_kems, complete_rps


@pytest.mark.parametrize("basis", [AmountBasis.MOLE_FRACTION, AmountBasis.MOL_INVENTORY])
def test_massless_normalized_composition(basis):
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        initial_composition=f.located(Composition("mix", (("MgO", D(".25")),
            ("SiO2", D(".75"))), basis))))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    selected = inputs.waypoints["normalized_composition"].selected
    assert selected.value == {"MgO": D(".25"), "SiO2": D(".75")}
    assert selected.authority is WaypointAuthority.DERIVED
    assert all("mass" not in path for path in selected.inputs)
    assert bool(inputs.charges) == (basis is AmountBasis.MOL_INVENTORY)
    assert all(result.payload is not None for result in engine_point_requests(inputs))


def test_massless_wt_percent_derivation():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        printed_composition=f.located({"MgO": D("40.304"), "SiO2": D("60.083")})))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    selected = inputs.waypoints["normalized_composition"].selected
    assert selected.authority is WaypointAuthority.DERIVED
    assert float(selected.value["MgO"]) == pytest.approx(.5, rel=1e-4)
    assert float(selected.value["SiO2"]) == pytest.approx(.5, rel=1e-4)
    assert not inputs.charges
    assert all(r.payload is not None for r in engine_point_requests(inputs))


def test_payload_scale_invariance():
    experiment, bench, observation = case()
    source = load_yaml(Path(__file__).resolve().parents[2] /
                       "data/literature/extracts/ta-badro-2021.yaml")
    sample = _sample_from_plain(source["experiments"][0]["sample"])
    assert not sample.mass_kg.state.is_value
    weights = source["laboratory_parameters"]["starting_composition"]["composition_wt_pct"]
    payloads = []
    for scale in (1, 10):
        scaled = replace(experiment, sample=replace(sample,
            printed_composition=replace(sample.printed_composition,
                state=State.of({species: D(weight) * scale for species, weight in weights.items()}))))
        payloads.append([r.payload for r in engine_point_requests(
            collect_consumer_inputs(scaled, bench, observation))])
    assert all(payload is not None for payload in payloads[0])
    assert payloads[0] == payloads[1]
    assert sum(payloads[0][0]["composition_mol"].values()) == pytest.approx(1)


def test_printed_inventory_precedes_wt_derivation():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=replace(experiment.sample,
        initial_composition=f.located(Composition("mix", (("MgO", D(1)),
            ("SiO2", D(3))), AmountBasis.MOL_INVENTORY))))
    selected = collect_consumer_inputs(experiment, bench, observation).waypoints["normalized_composition"].selected
    assert selected.value == {"MgO": D(".25"), "SiO2": D(".75")}
    assert selected.authority is WaypointAuthority.DERIVED


@pytest.mark.parametrize("weights", [{"MgO": 0, "SiO2": 0}, {"unknown_species": 50, "MgO": 50}])
def test_unsupported_composition_refuses_without_dropping_species(weights):
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(printed_composition=f.located(weights)))
    for result in engine_point_requests(collect_consumer_inputs(experiment, bench, observation)):
        assert result.payload is None
        assert any(g.waypoint == "normalized_composition" and g.reason is GapReason.UNSUPPORTED_PRINT_FORM
                   for g in result.readiness.gaps)


def test_no_composition_typed_refusal():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        initial_composition=Located(State.unknown("not_reported")),
        printed_composition=Located(State.unknown("not_reported"))))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert inputs.waypoints["normalized_composition"].absence.reason is GapReason.MISSING_EVIDENCE
    for result in engine_point_requests(inputs):
        assert result.payload is None
        assert any(g.waypoint == "normalized_composition" and g.reason is GapReason.MISSING_EVIDENCE
                   for g in result.readiness.gaps)


def test_massless_single_species_still_not_applicable():
    experiment, bench, observation = case(single=True)
    experiment = replace(experiment, sample=replace(experiment.sample, mass_kg=None))
    assert all(r.readiness.status is ReadinessStatus.NOT_APPLICABLE for r in
               engine_point_requests(collect_consumer_inputs(experiment, bench, observation)))


def test_transport_still_requires_absolute_mass():
    experiment, bench, observation = complete_kems()
    assert kems_case(collect_consumer_inputs(experiment, bench, observation)).payload is not None
    experiment = replace(experiment, sample=replace(experiment.sample, mass_kg=None))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert not inputs.charges
    assert kems_case(inputs).payload is None
    experiment, bench, observation, model = complete_rps()
    assert vacuum_pyrolysis_preset(collect_consumer_inputs(experiment, bench, observation),
                                   modelling_inputs=model).payload is not None
    experiment = replace(experiment, sample=replace(experiment.sample, mass_kg=None))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert not inputs.run_charges
    assert vacuum_pyrolysis_preset(inputs, modelling_inputs=model).payload is None
