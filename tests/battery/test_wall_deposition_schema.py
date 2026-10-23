"""Chunk 1 of d-275: wall-deposition observables and their row contract."""

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import MetricOperation, Quantity, Rail
from simulator.battery.identity import (
    Exposure,
    IdentityEqualKind,
    SweepIdentity,
    WallIdentity,
    identity_equal,
    profile_for,
    validate_quantity_profile,
)
from simulator.battery.records import Apparatus, ApparatusGeometry, Located, Locator, State, Value
from simulator.battery.score import metric_operation, rail_for_quantity
from simulator.battery.validity import run_validity_gates, wall_deposition_input_gate
from simulator.battery.migrate import _geometry_from_plain
from tests.battery import factories as F


@pytest.mark.parametrize(
    ("quantity", "unit"),
    [
        (Quantity.WALL_DEPOSIT_AREAL_MASS, "kg_per_m2"),
        (Quantity.WALL_DEPOSITION_FLUX, "kg_per_m2_per_s"),
        (Quantity.WALL_DEW_TEMPERATURE, "K"),
    ],
)
def test_d275_wall_observables_have_units_and_route(quantity, unit):
    from simulator.battery.enums import QUANTITY_UNITS

    assert QUANTITY_UNITS[quantity] == unit
    assert rail_for_quantity(quantity, species_formula="SiO") is Rail.WALL_DEPOSITION


def test_d275_areal_wall_observables_do_not_require_sample_mass():
    for quantity in (
        Quantity.WALL_DEPOSIT_AREAL_MASS,
        Quantity.WALL_DEPOSITION_FLUX,
    ):
        identity = replace(
            F.wall_deposit_identity(),
            quantity=quantity,
            sample_mass_kg=None,
            per=State.not_applicable("wall deposition has no per basis"),
        )
        profile = profile_for(identity)
        assert "sample_mass_kg" not in profile.required
        assert validate_quantity_profile(identity).kind is IdentityEqualKind.EQUAL


def test_d275_legacy_wall_deposit_mass_keeps_its_extensive_semantics():
    identity = F.wall_deposit_identity()
    profile = profile_for(identity)

    assert rail_for_quantity(Quantity.WALL_DEPOSIT_MASS) is Rail.WALL_DEPOSITION
    assert "sample_mass_kg" in profile.required
    assert metric_operation(Quantity.WALL_DEPOSIT_MASS) is MetricOperation.RELATIVE


def test_d275_beerkens_rate_conversion():
    # 0.38 mg cm^-2 h^-1 × 0.01 kg m^-2 per (mg cm^-2) ÷ 3600 s h^-1.
    converted = Decimal("0.38") * Decimal("0.01") / Decimal("3600")
    assert abs(converted - Decimal("1.06e-6")) < Decimal("5e-9")


def _located(value):
    return Located(State.of(value), Locator(page=1, table="1"))


def _complete_mass_inputs():
    identity = replace(
        F.wall_deposit_identity(),
        quantity=Quantity.WALL_DEPOSIT_AREAL_MASS,
        sample_mass_kg=None,
        per=State.not_applicable("wall deposition has no per basis"),
        sweep_gas=State.of(
            SweepIdentity(
                "N2", State.of(Decimal("10")), State.of(Decimal("100"))
            )
        ),
        exposure=State.of(
            Exposure(State.of(Decimal("0.02")), State.of(Decimal("3600")))
        ),
        wall=State.of(
            WallIdentity(
                State.of(Decimal("1200")),
                State.of("SiO2"),
                State.of(Decimal("0.02")),
                State.of("duct-1"),
            )
        ),
    )
    geometry = ApparatusGeometry(
        pipe_inner_diameter_m=_located(Decimal("0.1")),
        segment_length_m=_located(Decimal("0.2")),
        segment_position_m=_located(Decimal("0.3")),
        flow_orientation=_located("upstream_to_downstream"),
        line_of_sight_view_factor=_located(Value.point_of(Decimal("0.5"))),
    )
    experiment = replace(
        F.tabulation_experiment(),
        apparatus=Apparatus(geometry=geometry),
    )
    conditions = {
        "vapour_source_kind": _located("melt"),
        "gas_temperature_K": _located(Decimal("1500")),
    }
    return identity, conditions, experiment


def test_d275_complete_wall_mass_conditions_with_locators_pass():
    identity, conditions, experiment = _complete_mass_inputs()

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert outcome.passed
    assert validate_quantity_profile(identity).kind is IdentityEqualKind.EQUAL


@pytest.mark.parametrize(
    "missing_key",
    [
        "vapour_source_kind",
        "gas_temperature_K",
    ],
)
def test_d275_missing_condition_has_typed_field_refusal(missing_key):
    identity, conditions, experiment = _complete_mass_inputs()
    conditions.pop(missing_key)

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.reason.value == "underdetermined_apparatus"
    assert outcome.primary_check == f"point_conditions.{missing_key}"


def test_d275_missing_geometry_refuses_with_its_field_name():
    identity, conditions, experiment = _complete_mass_inputs()
    geometry = replace(experiment.apparatus.geometry, segment_length_m=None)
    experiment = replace(experiment, apparatus=replace(experiment.apparatus, geometry=geometry))

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "apparatus.geometry.segment_length_m"


@pytest.mark.parametrize("identity_field", ["temperature_K", "reservoir", "composition"])
def test_d275_melt_source_refuses_each_missing_identity_input(identity_field):
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(identity, **{identity_field: State.unknown("not reported")})

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == f"identity.{identity_field}"


@pytest.mark.parametrize(
    "fO2_state",
    [None, State.unknown("not reported"), State.not_applicable("not printed")],
    ids=["missing", "unknown", "not-applicable"],
)
@pytest.mark.parametrize(
    "quantity", [Quantity.WALL_DEPOSIT_AREAL_MASS, Quantity.WALL_DEPOSITION_FLUX]
)
def test_d275_oxygen_dependent_melt_vapour_requires_fugacity(fO2_state, quantity):
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(identity, quantity=quantity, fO2_Pa=fO2_state)

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.reason.value == "underdetermined_apparatus"
    assert outcome.primary_check == "identity.fO2_Pa"
    if fO2_state is not None and fO2_state.is_not_applicable:
        assert validate_quantity_profile(identity).kind is IdentityEqualKind.INVALID_IDENTITY

    observation = replace(
        F.observation("wall-oxygen-required", experiment.experiment_id, identity, Decimal("1")),
        point_conditions=conditions,
    )
    run_outcome = run_validity_gates(experiment, observation)
    assert not run_outcome.passed
    assert run_outcome.reason.value == "underdetermined_apparatus"
    assert run_outcome.primary_check == "identity.fO2_Pa"


@pytest.mark.parametrize("formula", ["Na", "K", "Mg", "Fe"])
def test_d275_elemental_melt_vapours_require_fugacity(formula):
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(
        identity,
        species=replace(identity.species, formula=formula),
        fO2_Pa=None,
    )

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "identity.fO2_Pa"


def test_d275_nonoxygen_melt_source_does_not_require_fugacity():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(
        identity,
        species=replace(identity.species, formula="Cl"),
        fO2_Pa=None,
    )

    assert wall_deposition_input_gate(identity, conditions, experiment).passed


@pytest.mark.parametrize(
    "geometry_field",
    ["pipe_inner_diameter_m", "segment_position_m", "flow_orientation"],
)
def test_d275_each_required_geometry_input_refuses_by_field(geometry_field):
    identity, conditions, experiment = _complete_mass_inputs()
    geometry = replace(experiment.apparatus.geometry, **{geometry_field: None})
    experiment = replace(experiment, apparatus=replace(experiment.apparatus, geometry=geometry))

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == f"apparatus.geometry.{geometry_field}"


def test_d275_missing_total_pressure_refuses_by_identity_field():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(identity, total_pressure_Pa=State.unknown("not reported"))

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "identity.total_pressure_Pa"


def test_d275_molecular_flow_requires_located_line_of_sight_factor():
    identity, conditions, experiment = _complete_mass_inputs()
    geometry = replace(experiment.apparatus.geometry, line_of_sight_view_factor=None)
    experiment = replace(experiment, apparatus=replace(experiment.apparatus, geometry=geometry))

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "apparatus.geometry.line_of_sight_view_factor"


@pytest.mark.parametrize("wall_field", ["temperature_K", "material", "location", "area_m2"])
def test_d275_missing_wall_identity_refuses_by_field(wall_field):
    identity, conditions, experiment = _complete_mass_inputs()
    wall = identity.wall.value
    identity = replace(
        identity,
        wall=State.of(replace(wall, **{wall_field: State.unknown("not reported")})),
    )

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == f"identity.wall.{wall_field}"


def test_d275_upstream_vapour_source_requires_located_pressure_and_location():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(
        identity,
        temperature_K=State.not_applicable("upstream partial-pressure source"),
        reservoir=State.not_applicable("upstream partial-pressure source"),
        composition=State.not_applicable("upstream partial-pressure source"),
        fO2_Pa=State.not_applicable("source chemistry is upstream"),
    )
    conditions["vapour_source_kind"] = _located("upstream_partial_pressure")
    conditions["source_partial_pressure_Pa"] = _located(Decimal("5"))
    conditions["source_location"] = _located("upstream-1")

    assert wall_deposition_input_gate(identity, conditions, experiment).passed
    assert validate_quantity_profile(identity).kind is IdentityEqualKind.EQUAL

    conditions.pop("source_location")
    outcome = wall_deposition_input_gate(identity, conditions, experiment)
    assert not outcome.passed
    assert outcome.primary_check == "point_conditions.source_location"


@pytest.mark.parametrize("source_field", ["source_partial_pressure_Pa", "source_location"])
def test_d275_upstream_source_refuses_each_missing_located_input(source_field):
    identity, conditions, experiment = _complete_mass_inputs()
    conditions["vapour_source_kind"] = _located("upstream_partial_pressure")
    conditions["source_partial_pressure_Pa"] = _located(Decimal("5"))
    conditions["source_location"] = _located("upstream-1")
    conditions.pop(source_field)

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == f"point_conditions.{source_field}"


@pytest.mark.parametrize("carrier_field", ["flow_sccm", "partial_pressure_Pa"])
def test_d275_carrier_requires_flow_and_pressure(carrier_field):
    identity, conditions, experiment = _complete_mass_inputs()
    sweep = identity.sweep_gas.value
    identity = replace(
        identity,
        sweep_gas=State.of(
            replace(sweep, **{carrier_field: State.unknown("not reported")})
        ),
    )

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == f"identity.sweep_gas.{carrier_field}"


def test_d275_areal_mass_requires_positive_exposure_duration():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(
        identity,
        exposure=State.of(Exposure(State.of(Decimal("0.02")), State.of(Decimal("0")))),
    )

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "identity.exposure.duration_s"


def test_d275_direct_flux_does_not_require_exposure_duration():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(identity, quantity=Quantity.WALL_DEPOSITION_FLUX, exposure=None)

    assert wall_deposition_input_gate(identity, conditions, experiment).passed


def test_d275_local_dew_pressure_does_not_require_transport_or_wall_material():
    identity = replace(
        F.wall_deposit_identity(), quantity=Quantity.WALL_DEW_TEMPERATURE, wall=None
    )
    experiment = F.tabulation_experiment()
    conditions = {"wall_species_partial_pressure_Pa": _located(Decimal("0.5"))}

    assert wall_deposition_input_gate(identity, conditions, experiment).passed


def test_d275_dew_temperature_is_the_wall_temperature_value_axis():
    identity = replace(
        F.wall_deposit_identity(),
        quantity=Quantity.WALL_DEW_TEMPERATURE,
        per=State.not_applicable("wall dew temperature is not per a basis"),
        sample_mass_kg=None,
        wall=State.of(
            WallIdentity(
                State.not_applicable("wall temperature is the measured value"),
                State.of("SiO2"),
                State.of(Decimal("0.02")),
                State.of("duct-1"),
            )
        ),
    )

    assert validate_quantity_profile(identity).kind is IdentityEqualKind.EQUAL


def test_d275_dew_identity_ignores_optional_wall_axes():
    without_wall = replace(
        F.wall_deposit_identity(),
        quantity=Quantity.WALL_DEW_TEMPERATURE,
        per=State.not_applicable("wall dew temperature is not per a basis"),
        sample_mass_kg=None,
        wall=None,
    )
    with_wall = replace(
        without_wall,
        wall=State.of(
            WallIdentity(
                State.of(Decimal("1200")),
                State.of("SiO2"),
                State.of(Decimal("0.02")),
                State.of("duct-1"),
            )
        ),
    )

    assert identity_equal(without_wall, with_wall).kind is IdentityEqualKind.EQUAL


def test_d275_flux_identity_ignores_optional_exposure():
    with_exposure = replace(
        F.wall_deposit_identity(),
        quantity=Quantity.WALL_DEPOSITION_FLUX,
        per=State.not_applicable("wall deposition has no per basis"),
        sample_mass_kg=None,
    )
    without_exposure = replace(with_exposure, exposure=None)

    assert identity_equal(without_exposure, with_exposure).kind is IdentityEqualKind.EQUAL


@pytest.mark.parametrize("pressure", [None, Decimal("0")])
def test_d275_dew_requires_positive_located_local_partial_pressure(pressure):
    identity = replace(
        F.wall_deposit_identity(), quantity=Quantity.WALL_DEW_TEMPERATURE, wall=None
    )
    experiment = F.tabulation_experiment()
    conditions = (
        {}
        if pressure is None
        else {"wall_species_partial_pressure_Pa": _located(pressure)}
    )

    outcome = wall_deposition_input_gate(identity, conditions, experiment)

    assert not outcome.passed
    assert outcome.primary_check == "point_conditions.wall_species_partial_pressure_Pa"


def test_d275_wall_segment_geometry_migrates_with_locators():
    point = {"state": {"tag": "value", "value": {"kind": "point", "point": "0.2"}}, "locator": {"page": 5}}
    plain = {
        "pipe_inner_diameter_m": point,
        "segment_length_m": point,
        "segment_position_m": point,
        "line_of_sight_view_factor": point,
        "flow_orientation": {"state": {"tag": "value", "value": "downstream"}, "locator": {"page": 5}},
    }

    geometry = _geometry_from_plain(plain)

    assert geometry is not None
    for field in (
        "pipe_inner_diameter_m",
        "segment_length_m",
        "segment_position_m",
        "line_of_sight_view_factor",
        "flow_orientation",
    ):
        assert getattr(geometry, field) is not None
        assert getattr(geometry, field).locator.page == 5


def test_d275_carrier_free_is_explicit_and_located():
    identity, conditions, experiment = _complete_mass_inputs()
    identity = replace(
        identity,
        sweep_gas=State.not_applicable("carrier-free state is point_conditions"),
    )
    conditions["carrier_mode"] = _located("none")

    assert wall_deposition_input_gate(identity, conditions, experiment).passed
    assert validate_quantity_profile(identity).kind is IdentityEqualKind.EQUAL

    conditions["carrier_mode"] = Located(State.of("none"))
    outcome = wall_deposition_input_gate(identity, conditions, experiment)
    assert not outcome.passed
    assert outcome.primary_check == "point_conditions.carrier_mode"
