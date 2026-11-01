"""A fixed-T/P/fO2 point needs ratios; transport still needs absolute charge."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import AmountBasis
from simulator.battery.generators import engine_point_requests, kems_case, vacuum_pyrolysis_preset
from simulator.battery.records import Composition, Derivation, Located, Locator, Sample, State, Value
from simulator.battery.migrate import (
    load_yaml, _sample_from_plain, _wt_percent_conversion_state,
    wt_pct_to_mole_fraction_derivation,
)
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


def test_partial_printed_composition_does_not_reach_engine_point():
    experiment, bench, observation = case()
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample,
            printed_composition=f.located({"SiO2": D("45"), "MgO": D("45")}),
            initial_composition=Located(State.unknown("not reported")),
        ),
    )
    inputs = collect_consumer_inputs(experiment, bench, observation)

    waypoint = inputs.waypoints["normalized_composition"]
    assert waypoint.selected is None
    assert waypoint.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    requests = engine_point_requests(inputs)
    assert requests
    assert all(request.payload is None for request in requests)
    assert all(
        any(gap.waypoint == "normalized_composition" for gap in request.readiness.gaps)
        for request in requests
    )


def test_printed_wt_percent_outside_closure_refuses_even_with_derived_initial_composition():
    experiment, bench, observation = case()
    source = load_yaml(Path(__file__).resolve().parents[2] /
                       "data/literature/extracts/ta-badro-2021.yaml")
    sample = _sample_from_plain(source["experiments"][0]["sample"])
    assert not sample.mass_kg.state.is_value
    weights = source["laboratory_parameters"]["starting_composition"]["composition_wt_pct"]
    scale_one = replace(experiment, sample=replace(sample,
        printed_composition=replace(sample.printed_composition,
            state=State.of({species: D(weight) for species, weight in weights.items()}))))
    scale_ten_map = {species: D(weight) * 10 for species, weight in weights.items()}
    scale_ten = replace(experiment, sample=replace(sample,
        printed_composition=replace(sample.printed_composition,
            state=State.of(scale_ten_map))))
    closure = _wt_percent_conversion_state(
        scale_ten_map,
        closure_context=scale_ten_map,
        declared_component_map=scale_ten_map,
    )
    assert closure.is_unknown
    assert "sum=1000 wt% is above 102 wt%" in closure.reason

    valid_requests = engine_point_requests(
        collect_consumer_inputs(scale_one, bench, observation)
    )
    assert all(request.payload is not None for request in valid_requests)
    assert sum(valid_requests[0].payload["composition_mol"].values()) == pytest.approx(1)

    invalid_inputs = collect_consumer_inputs(scale_ten, bench, observation)
    waypoint = invalid_inputs.waypoints["normalized_composition"]
    assert waypoint.selected is None
    assert waypoint.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert "experiment.sample.printed_composition" in waypoint.absence.missing
    assert "experiment.sample.initial_composition" in waypoint.absence.missing
    invalid_requests = engine_point_requests(invalid_inputs)
    assert invalid_requests
    assert all(request.payload is None for request in invalid_requests)
    assert all(any(
        gap.waypoint == "normalized_composition"
        and gap.reason is GapReason.UNSUPPORTED_PRINT_FORM
        for gap in request.readiness.gaps
    ) for request in invalid_requests)

    # The derived initial composition is another representation of this
    # malformed recipe. Its own recorded weight inputs must also fail closure.
    invalid_initial = replace(
        sample.initial_composition,
        inference=wt_pct_to_mole_fraction_derivation(
            scale_ten_map, sample.initial_composition.locator
        ),
    )
    invalid_derived = replace(scale_ten, sample=replace(
        scale_ten.sample, initial_composition=invalid_initial
    ))
    derived_inputs = collect_consumer_inputs(invalid_derived, bench, observation)
    assert derived_inputs.waypoints["normalized_composition"].selected is None
    assert all(request.payload is None for request in engine_point_requests(derived_inputs))


def test_mass_inventory_scale_preserves_engine_composition():
    experiment, bench, observation = case()
    compositions = []
    charges = []
    for mass_kg in (D("0.001"), D("0.010")):
        sample = Sample(
            mass_kg=f.located(Value.point_of(mass_kg)),
            initial_composition=f.located(Composition(
                "mix", (("MgO", D("0.25")), ("SiO2", D("0.75"))),
                AmountBasis.MOLE_FRACTION,
            )),
        )
        inputs = collect_consumer_inputs(
            replace(experiment, sample=sample), bench, observation
        )
        requests = engine_point_requests(inputs)
        assert all(request.payload is not None for request in requests)
        compositions.append([request.payload["composition_mol"] for request in requests])
        charges.append({
            species: waypoint.selected.value.point
            for species, waypoint in inputs.charges.items()
        })
    assert compositions[0] == compositions[1]
    assert charges[1] == {species: amount * 10 for species, amount in charges[0].items()}


def test_rejected_print_does_not_veto_independent_initial_mole_fraction():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        printed_composition=f.located({"MgO": D("500"), "SiO2": D("500")}),
        initial_composition=f.located(Composition(
            "measured", (("MgO", D("0.25")), ("SiO2", D("0.75"))),
            AmountBasis.MOLE_FRACTION,
        )),
    ))

    inputs = collect_consumer_inputs(experiment, bench, observation)
    selected = inputs.waypoints["normalized_composition"].selected
    assert selected is not None
    assert selected.route == "normalized_initial_composition"
    assert selected.value == {"MgO": D("0.25"), "SiO2": D("0.75")}
    requests = engine_point_requests(inputs)
    assert all(request.payload is not None for request in requests)
    assert all(
        request.payload["composition_mol"] == {"MgO": 0.25, "SiO2": 0.75}
        for request in requests
    )


def test_closing_derived_initial_from_another_printed_column_is_usable():
    experiment, bench, observation = case()
    wt = {"MgO": D("50"), "SiO2": D("50")}
    converted = _wt_percent_conversion_state(
        wt, closure_context=wt, declared_component_map=wt
    )
    assert converted.is_value
    print_locator = Locator(published_page=105, pdf_page_index=5, table="1")
    initial_locators = (
        Locator(
            published_page=105,
            pdf_page_index=5,
            table="1",
            note="closing composition in another column",
        ),
        Locator(note="closing composition from a separate printed column"),
    )
    expected = dict(converted.value.components)

    for initial_locator in initial_locators:
        experiment_with_two_prints = replace(experiment, sample=Sample(
            mass_kg=Located(State.unknown("would_invent")),
            printed_composition=Located(
                State.of({"MgO": D("500"), "SiO2": D("500")}),
                locator=print_locator,
            ),
            initial_composition=Located(
                State.of(converted.value),
                locator=initial_locator,
                inference=wt_pct_to_mole_fraction_derivation(wt, initial_locator),
            ),
        ))
        inputs = collect_consumer_inputs(
            experiment_with_two_prints, bench, observation
        )
        selected = inputs.waypoints["normalized_composition"].selected
        assert selected is not None
        assert selected.route == "normalized_initial_composition"
        assert selected.value == expected
        requests = engine_point_requests(inputs)
        assert all(request.payload is not None for request in requests)
        assert all(
            request.payload["composition_mol"]
            == {species: float(amount) for species, amount in expected.items()}
            for request in requests
        )


def test_printed_inventory_precedes_wt_derivation():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=replace(experiment.sample,
        initial_composition=f.located(Composition("mix", (("MgO", D(1)),
            ("SiO2", D(3))), AmountBasis.MOL_INVENTORY))))
    selected = collect_consumer_inputs(experiment, bench, observation).waypoints["normalized_composition"].selected
    assert selected.value == {"MgO": D(".25"), "SiO2": D(".75")}
    assert selected.authority is WaypointAuthority.DERIVED


def test_printed_mole_fraction_precedes_wt_derivation():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        printed_composition=f.located({"MgO": D(50), "SiO2": D(50)}),
        initial_composition=f.located(Composition("mix", (("MgO", D(".25")),
            ("SiO2", D(".75"))), AmountBasis.MOLE_FRACTION))))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    selected = inputs.waypoints["normalized_composition"].selected
    assert selected.value == {"MgO": D(".25"), "SiO2": D(".75")}
    for result in engine_point_requests(inputs):
        assert result.payload is not None
        assert result.payload["composition_mol"] == {"MgO": 0.25, "SiO2": 0.75}


@pytest.mark.parametrize("extra", [{"LOI": D(20)}, {"Total": D(100)}])
def test_printed_species_dropped_by_sibling_refuses(extra):
    experiment, bench, observation = case()
    wt = {"SiO2": D(50), "MgO": D(30)}
    key = next(iter(extra))
    printed = {**wt, **extra}
    closure = _wt_percent_conversion_state(
        wt, closure_context=printed, declared_component_map=printed
    )
    if key == "LOI":
        assert closure.is_value
    else:
        # b-704 excludes the printed total and refuses the remaining 80 wt%.
        assert closure.is_unknown
        assert "kept sum=80 wt% is below 95 wt%" in closure.reason
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        printed_composition=f.located(printed),
        initial_composition=Located(
            State.of(Composition(
                "printed_oxides",
                (("SiO2", D(".5")), ("MgO", D(".5"))),
                AmountBasis.MOLE_FRACTION,
            )), locator=f.loc())))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    waypoint = inputs.waypoints["normalized_composition"]
    assert waypoint.selected is None
    assert waypoint.absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    assert waypoint.absence.missing == (f"experiment.sample.printed_composition.{key}",)
    for result in engine_point_requests(inputs):
        assert result.payload is None
        assert result.readiness.status is ReadinessStatus.GAP
        assert any(g.waypoint == "normalized_composition"
                   and g.reason is GapReason.UNSUPPORTED_PRINT_FORM
                   and any(key in path for path in g.missing)
                   for g in result.readiness.gaps)


def test_losing_single_species_charge_does_not_veto_intensive():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=Located(State.unknown("would_invent")),
        printed_composition=f.located({"MgO": D(50), "SiO2": D(50)}),
        initial_composition=Located(
            State.of(Composition("mix", (("MgO", D(1)),), AmountBasis.MOL_INVENTORY)),
            locator=f.loc(),
            inference=Derivation("wt_pct_to_mole_fraction", ("wt_pct",), (), "mol"))))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert list(inputs.charges) == ["MgO"]
    selected = inputs.waypoints["normalized_composition"].selected
    assert set(selected.value) == {"MgO", "SiO2"}
    for result in engine_point_requests(inputs):
        assert result.readiness.status is ReadinessStatus.READY
        assert result.payload is not None
        assert not any(g.reason is GapReason.SINGLE_SPECIES_CHARGE for g in result.readiness.gaps)


def test_unsupported_print_reports_refusal_not_single_species():
    experiment, bench, observation = case()
    experiment = replace(experiment, sample=Sample(
        mass_kg=experiment.sample.mass_kg,
        printed_composition=f.located({"MgO": D(50), "NotAnOxide": D(50)})))
    inputs = collect_consumer_inputs(experiment, bench, observation)
    assert list(inputs.charges) == ["MgO"]
    assert inputs.waypoints["normalized_composition"].absence.reason is GapReason.UNSUPPORTED_PRINT_FORM
    for result in engine_point_requests(inputs):
        assert result.payload is None
        assert result.readiness.status is ReadinessStatus.GAP
        assert any(g.waypoint == "normalized_composition"
                   and g.reason is GapReason.UNSUPPORTED_PRINT_FORM
                   for g in result.readiness.gaps)
        assert not any(g.reason is GapReason.SINGLE_SPECIES_CHARGE for g in result.readiness.gaps)


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
