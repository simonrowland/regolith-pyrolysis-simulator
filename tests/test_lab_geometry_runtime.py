from __future__ import annotations

import copy
import math

import pytest

from engines.builtin.condensation_route import BuiltinCondensationRouteProvider
from simulator.accounting import AtomLedger, LedgerTransition
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import CondensationModel, knudsen_regime_diagnostic
from simulator.core import PyrolysisSimulator
from simulator.lab_schedule import LabScheduleValidationError, normalize_lab_schedule
from simulator.equipment import EquipmentDesigner
from simulator.lab_geometry import LabGeometryError, parse_lab_geometry
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.optimize.evalspec import EvalSpec, cache_key, current_code_version
from simulator.runner import _wall_deposit_mol_by_species, _wall_deposit_report_kg
from simulator.runner import PyrolysisRun
from simulator.state import (
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)
from simulator.trace import wall_deposit_by_segment_species_kg


def robinot_geometry_fixture() -> dict:
    """
    Robinot anchor #1 geometry capture before coding:

    | item | stated by Robinot/finding | runtime disposition |
    | glass reactor | stated | chamber material fact; dimensions unstated |
    | water-cooled steel holder | stated | holder surface, temperature/area assumptions |
    | refrigerated Cu condenser above reactor | stated | condenser surface, temperature/area assumptions |
    | porous SS filter before pump | stated | filter surface, temperature/area assumptions |
    | window deposit location | stated by per-location species | window surface, area assumption |
    | condenser/window/filter temps | missing | sensitivity-marked assumptions |
    | per-location masses, view factors, sticking | missing | sensitivity-marked assumptions |
    """

    return {
        "id": "robinot_2026_geometry_test",
        "scale": "gram_lab",
        "equipment_sizing": "lab_fixed_geometry",
        "sample": {"mass_g": 3.38},
        "surfaces": [
            {
                "id": "holder",
                "role": "holder",
                "area_m2": 0.00024,
                "temperature_C": 25.0,
                "view_factor_from_melt": 0.12,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "holder_area_sweep",
                "extraction_note": "Robinot holder stated; area/T/view factor assumed for sensitivity sweep",
                "equivalent_diameter_m": 0.018,
            },
            {
                "id": "window",
                "role": "window",
                "area_m2": 0.00018,
                "temperature_C": 80.0,
                "view_factor_from_melt": 0.08,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "window_area_sweep",
                "extraction_note": "Window deposit location stated; area/T/view factor assumed",
                "equivalent_diameter_m": 0.018,
            },
            {
                "id": "condenser",
                "role": "condenser",
                "area_m2": 0.0005,
                "temperature_C": 5.0,
                "view_factor_from_melt": 0.55,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "condenser_area_sweep",
                "extraction_note": "Refrigerated Cu condenser stated; geometry values assumed",
                "equivalent_diameter_m": 0.02,
            },
            {
                "id": "filter",
                "role": "filter",
                "area_m2": 0.00032,
                "temperature_C": 20.0,
                "view_factor_from_melt": 0.25,
                "line_of_sight_to_melt": False,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "filter_area_sweep",
                "extraction_note": "Porous SS filter stated; blocked LOS and area assumed",
                "equivalent_diameter_m": 0.016,
            },
            {
                "id": "chamber_wall",
                "role": "chamber_wall",
                "area_m2": 0.0012,
                "temperature_C": 80.0,
                "view_factor_from_melt": 0.0,
                "line_of_sight_to_melt": False,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "chamber_wall_area_sweep",
                "extraction_note": "Glass chamber wall stated; geometry values assumed",
                "equivalent_diameter_m": 0.03,
            },
        ],
    }


def dynamic_surface_geometry_fixture() -> dict:
    return {
        "id": "dynamic_surface_temperature_geometry",
        "scale": "gram_lab",
        "equipment_sizing": "lab_fixed_geometry",
        "surfaces": [
            {
                "id": "holder",
                "role": "holder",
                "area_m2": 0.002,
                "temperature_profile": "holder_profile",
                "view_factor_from_melt": 0.5,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "holder_surface_temperature_sweep",
                "extraction_note": "Synthetic holder for surface T(t) resolver",
                "equivalent_diameter_m": 0.02,
            },
            {
                "id": "condenser",
                "role": "condenser",
                "area_m2": 0.002,
                "temperature_profile": "condenser_profile",
                "view_factor_from_melt": 0.5,
                "line_of_sight_to_melt": True,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "condenser_surface_temperature_sweep",
                "extraction_note": "Synthetic condenser for surface T(t) resolver",
                "equivalent_diameter_m": 0.02,
            },
        ],
    }


def dynamic_lab_schedule(*, holder_then_condenser: bool = True) -> dict:
    holder_profile = ((0.0, 25.0), (1.0, 25.0), (2.0, 1500.0))
    condenser_profile = ((0.0, 1500.0), (1.0, 1500.0), (2.0, 25.0))
    if not holder_then_condenser:
        holder_profile, condenser_profile = condenser_profile, holder_profile
    return {
        "id": "dynamic_surface_temperature_schedule",
        "duration_h": 2.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "assumption_with_sensitivity_marker",
        "interpolation_citation_id": "test",
        "interpolation_extraction_note": "Synthetic declared surface temperatures",
        "furnace_ceiling_C": 1800.0,
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 1700.0, "unit": "C"},
            {"t_h": 2.0, "value": 1700.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 13.0, "unit": "mbar"},
            {"t_h": 2.0, "value": 13.0, "unit": "mbar"},
        ],
        "gas_boundary": {
            "background_gas": {
                "species": "Ar",
                "mole_fraction": 1.0,
                "source_class": "literature_sidecar",
                "source_ref": "test",
            },
            "imposed_flow": {
                "value": 0.3,
                "unit": "NL_min",
                "source_class": "literature_sidecar",
                "source_ref": "test",
            },
            "pressure_control": {
                "mode": "flow_through_with_pump",
                "source_class": "literature_sidecar",
                "source_ref": "test",
            },
        },
        "surface_temperature_C": {
            "holder_profile": [
                {"t_h": t_h, "value": value, "unit": "C"}
                for t_h, value in holder_profile
            ],
            "condenser_profile": [
                {"t_h": t_h, "value": value, "unit": "C"}
                for t_h, value in condenser_profile
            ],
        },
    }


def _surface_temperature_evalspec(schedule: dict) -> EvalSpec:
    return EvalSpec(
        recipe_id="surface-temperature-test",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id="lunar_mare_low_ti",
        profile_id="surface-temperature-profile",
        fidelity="fast",
        code_version=current_code_version(),
        data_digests={
            "feedstocks": "feedstock-digest",
            "foulant_thermo": "foulant-thermo-digest",
            "materials": "materials-digest",
            "profile": "profile-digest",
            "setpoints": "setpoints-digest",
            "species_catalog": "species-catalog-digest",
            "vapor_pressures": "vapor-digest",
        },
        campaign="C2A",
        hours=2,
        mass_kg=1000.0,
        backend_name="stub",
        lab_schedule=normalize_lab_schedule(schedule),
    )


def _surface_temperature_rows(schedule: dict) -> list[dict]:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=2,
        mass_kg=1000.0,
        backend_name="stub",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=schedule,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )
    session = run._start_session()
    sim = session.simulator
    sim._calculate_evaporation = lambda _equilibrium: EvaporationFlux(
        species_kg_hr={"SiO": 0.02},
        total_kg_hr=0.02,
    )
    sim._apply_analytic_evaporation_depletion = lambda flux: flux
    return [
        session.advance().per_hour_summary
        for _ in range(2)
    ]


def _surface_sio_delta(row: dict, surface_id: str) -> float:
    species_kg = row.get("wall_deposit_delta_kg", {}).get(surface_id, {})
    return sum(
        float(species_kg.get(species, 0.0))
        for species in ("SiO", "Si", "SiO2", "FeSi")
    )


def test_profile_only_geometry_stays_refused_without_surface_schedule() -> None:
    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(dynamic_surface_geometry_fixture())

    assert excinfo.value.code == "missing_lab_surface_temperature"


def test_surface_temperature_schedule_requires_lab_geometry() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1000.0,
        backend_name="stub",
        lab_schedule=dynamic_lab_schedule(),
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )

    with pytest.raises(LabGeometryError) as excinfo:
        run._start_session()

    assert excinfo.value.code == "lab_surface_temperature_schedule_without_geometry"


def test_surface_temperature_profile_key_is_required() -> None:
    schedule = dynamic_lab_schedule()
    schedule["surface_temperature_C"]["holder"] = schedule[
        "surface_temperature_C"
    ].pop("holder_profile")
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1000.0,
        backend_name="stub",
        setpoints_patch={"lab_geometry": dynamic_surface_geometry_fixture()},
        lab_schedule=schedule,
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
    )

    with pytest.raises(
        LabScheduleValidationError,
        match="lab_schedule_missing_surface_temperature: holder_profile",
    ):
        run._start_session()


def test_declared_surface_temperature_schedule_moves_runtime_deposits() -> None:
    rows = _surface_temperature_rows(dynamic_lab_schedule())

    assert _surface_sio_delta(rows[0], "holder") > (
        _surface_sio_delta(rows[0], "condenser")
    )
    assert _surface_sio_delta(rows[1], "condenser") > (
        _surface_sio_delta(rows[1], "holder")
    )


def test_surface_temperature_schedule_is_behavioral_cache_determinant() -> None:
    base_schedule = dynamic_lab_schedule(holder_then_condenser=True)
    mutant_schedule = dynamic_lab_schedule(holder_then_condenser=False)

    assert cache_key(_surface_temperature_evalspec(base_schedule)) != cache_key(
        _surface_temperature_evalspec(mutant_schedule)
    )

    base_rows = _surface_temperature_rows(base_schedule)
    mutant_rows = _surface_temperature_rows(mutant_schedule)
    assert _surface_sio_delta(base_rows[0], "holder") > (
        _surface_sio_delta(base_rows[0], "condenser")
    )
    assert _surface_sio_delta(mutant_rows[0], "condenser") > (
        _surface_sio_delta(mutant_rows[0], "holder")
    )


def test_gram_lab_equipment_bypass_uses_declared_lab_surface_area() -> None:
    raw = robinot_geometry_fixture()
    geometry = parse_lab_geometry(raw)

    design = EquipmentDesigner().design_for_batch(
        1000.0,
        {},
        lab_geometry=geometry,
    )

    declared_area = sum(row["area_m2"] for row in raw["surfaces"])
    assert design.lab_geometry_id == raw["id"]
    assert design.batch_mass_kg == pytest.approx(0.00338)
    assert design.pipe.surface_area_m2 == pytest.approx(declared_area)
    assert design.pipe.diameter_m == pytest.approx(0.016)
    assert design.pipe.diameter_m < 0.12
    assert design.pipe.length_m == pytest.approx(
        declared_area / (math.pi * design.pipe.diameter_m)
    )


def test_declared_lab_pipe_diameter_maps_to_pipe_segment() -> None:
    raw = robinot_geometry_fixture()
    raw["surfaces"] = [raw["surfaces"][0]]
    raw["surfaces"][0]["equivalent_diameter_m"] = 0.012

    geometry = parse_lab_geometry(raw)
    segment = geometry.to_pipe_segments()[0]

    assert segment.inner_diameter_m == pytest.approx(0.012)
    assert segment.length_m == pytest.approx(
        raw["surfaces"][0]["area_m2"] / (math.pi * 0.012)
    )


@pytest.mark.parametrize("diameter_m", [0.0, -0.01, 1.0e-12])
def test_lab_surface_equivalent_diameter_poison_pairs_are_named_refusals(
    diameter_m: float,
) -> None:
    raw = copy.deepcopy(robinot_geometry_fixture())
    raw["surfaces"][0]["equivalent_diameter_m"] = diameter_m

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == "invalid_lab_geometry_pipe_diameter"


def test_zero_lab_surface_area_is_named_refusal() -> None:
    raw = copy.deepcopy(robinot_geometry_fixture())
    raw["surfaces"][0]["area_m2"] = 0.0

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == "invalid_lab_geometry_positive_value"
    assert "area_m2" in str(excinfo.value)


@pytest.mark.parametrize("diameter_m", [0.0, -0.01, 1.0e-12])
def test_knudsen_diagnostic_refuses_invalid_pipe_diameters(
    diameter_m: float,
) -> None:
    diagnostic = knudsen_regime_diagnostic(
        overhead_pressure_mbar=10.0,
        gas_temperature_C=1500.0,
        pipe_diameter_m=diameter_m,
    )

    assert diagnostic["status"] == "refused"
    assert diagnostic["reason_refused"] == "invalid_pipe_diameter"
    assert diagnostic["field"] == "pipe_diameter_m"


@pytest.mark.parametrize("diameter_m", [0.0, -0.01, 1.0e-12])
def test_condensation_operating_pipe_diameter_poison_pairs_are_named_refusals(
    diameter_m: float,
) -> None:
    model = CondensationModel(CondensationTrain.create_default())

    with pytest.raises(LabGeometryError) as excinfo:
        model.configure_operating_conditions(pipe_diameter_m=diameter_m)

    assert excinfo.value.code == "invalid_lab_geometry_pipe_diameter"


def test_knudsen_diagnostic_refuses_invalid_segment_diameter() -> None:
    diagnostic = knudsen_regime_diagnostic(
        overhead_pressure_mbar=10.0,
        gas_temperature_C=1500.0,
        pipe_diameter_m=0.12,
        pipe_segments=[
            PipeSegment(
                name="poison_segment",
                upstream_stage="stage_0",
                downstream_stage="stage_1",
                wall_temperature_C=1500.0,
                length_m=1.0,
                inner_diameter_m=1.0e-12,
            )
        ],
    )

    assert diagnostic["status"] == "refused"
    assert diagnostic["reason_refused"] == "invalid_pipe_diameter"
    assert diagnostic["field"] == "poison_segment.inner_diameter_m"
    assert diagnostic["segments"][0]["name"] == "poison_segment"


def test_unknown_lab_surface_role_is_named_refusal() -> None:
    raw = robinot_geometry_fixture()
    raw["surfaces"][0]["role"] = "mystery_baffle"

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == "unknown_lab_surface_role"
    assert "holder" in str(excinfo.value)


def test_lab_surface_provenance_gates_accept_robinot_fixture() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())

    assert geometry is not None
    assert geometry.surfaces[0].source_class == "assumption_with_sensitivity_marker"
    assert geometry.surfaces[0].sensitivity_marker == "holder_area_sweep"
    assert geometry.surfaces[0].extraction_note


@pytest.mark.parametrize(
    "mutator, refusal_code",
    [
        (
            lambda raw: raw["surfaces"][0].__setitem__("source_class", "diagram_guess"),
            "invalid_lab_geometry_source_class",
        ),
        (
            lambda raw: raw["surfaces"][0].pop("sensitivity_marker"),
            "missing_lab_geometry_sensitivity_marker",
        ),
        (
            lambda raw: raw["surfaces"][0].__setitem__("extraction_note", " "),
            "missing_lab_geometry_extraction_note",
        ),
    ],
)
def test_lab_surface_provenance_poison_pairs_are_named_refusals(
    mutator,
    refusal_code: str,
) -> None:
    raw = copy.deepcopy(robinot_geometry_fixture())
    mutator(raw)

    with pytest.raises(LabGeometryError) as excinfo:
        parse_lab_geometry(raw)

    assert excinfo.value.code == refusal_code


def test_provider_routes_configured_lab_surface_accounts() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())
    accounts = geometry.wall_deposit_accounts
    provider = BuiltinCondensationRouteProvider(wall_deposit_accounts=accounts)
    holder_account = geometry.surfaces[0].wall_deposit_account
    condenser_account = geometry.surfaces[2].wall_deposit_account
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
                holder_account: {},
                condenser_account: {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {},
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {
                holder_account: 0.25,
                condenser_account: 0.75,
            },
            "wall_temperature_K": 1062.0,
            "wall_deposit_account_temperatures_K": {
                holder_account: 1062.0,
                condenser_account: 1062.0,
            },
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is not None
    assert "process.wall_deposit" not in result.transition.credits
    assert result.transition.credits[holder_account]["Na"] > 0.0
    assert result.transition.credits[condenser_account]["Na"] > 0.0
    assert result.diagnostic["credited_wall_deposit_accounts_kg"][
        holder_account
    ] == pytest.approx(0.0025)
    assert result.diagnostic["credited_wall_deposit_accounts_kg"][
        condenser_account
    ] == pytest.approx(0.0075)


def test_provider_wall_deposit_authority_is_instance_scoped_poison_pair() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())
    lab_model = CondensationModel(CondensationTrain.create_default())
    lab_model.configure_lab_geometry(geometry)
    holder_account = geometry.surfaces[0].wall_deposit_account
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
                holder_account: {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {},
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {holder_account: 1.0},
            "wall_temperature_K": 1062.0,
            "wall_deposit_account_temperatures_K": {holder_account: 1062.0},
            "dt_hr": 1.0,
        },
    )

    lab_provider = BuiltinCondensationRouteProvider(
        wall_deposit_accounts=lab_model.wall_deposit_accounts
    )
    lab_result = lab_provider.dispatch(request)

    assert lab_result.status == "ok"
    assert lab_result.transition is not None
    assert holder_account in lab_result.transition.credits

    industrial_model = CondensationModel(CondensationTrain.create_default())
    industrial_provider = BuiltinCondensationRouteProvider(
        wall_deposit_accounts=industrial_model.wall_deposit_accounts
    )
    poison_result = industrial_provider.dispatch(request)

    assert poison_result.status == "refused"
    assert poison_result.transition is None
    assert poison_result.diagnostic["reason_refused"] == (
        "undeclared_wall_deposit_account"
    )
    assert poison_result.diagnostic["account"] == holder_account


def test_simulator_wall_deposit_authority_does_not_cross_instances() -> None:
    holder_account = "process.wall_deposit_segment_holder"

    lab_backend = InternalAnalyticalBackend()
    lab_backend.initialize({})
    lab_sim = PyrolysisSimulator(
        lab_backend, {"lab_geometry": robinot_geometry_fixture()}, {}, {}
    )
    lab_sim._build_chemistry_kernel()
    lab_provider = lab_sim._chem_registry.authoritative_for(
        ChemistryIntent.CONDENSATION_ROUTE
    )
    assert lab_provider is not None

    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
                holder_account: {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {},
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {holder_account: 1.0},
            "wall_temperature_K": 1062.0,
            "wall_deposit_account_temperatures_K": {holder_account: 1062.0},
            "dt_hr": 1.0,
        },
    )

    lab_result = lab_provider.dispatch(request)
    assert lab_result.status == "ok"
    assert lab_result.transition is not None
    assert holder_account in lab_result.transition.credits

    industrial_backend = InternalAnalyticalBackend()
    industrial_backend.initialize({})
    industrial_sim = PyrolysisSimulator(industrial_backend, {}, {}, {})
    industrial_sim._build_chemistry_kernel()
    industrial_provider = industrial_sim._chem_registry.authoritative_for(
        ChemistryIntent.CONDENSATION_ROUTE
    )
    assert industrial_provider is not None

    poison_result = industrial_provider.dispatch(request)
    assert poison_result.status == "refused"
    assert poison_result.transition is None
    assert poison_result.diagnostic["reason_refused"] == (
        "undeclared_wall_deposit_account"
    )
    assert poison_result.diagnostic["account"] == holder_account


def test_provider_refuses_unregistered_lab_surface_account() -> None:
    bad_account = "process.wall_deposit_segment_unregistered_poison"
    provider = BuiltinCondensationRouteProvider()
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
                bad_account: {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {},
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {bad_account: 1.0},
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == "undeclared_wall_deposit_account"
    assert result.diagnostic["account"] == bad_account


def test_provider_empty_credit_path_is_named_noop() -> None:
    provider = BuiltinCondensationRouteProvider()
    request = IntentRequest(
        intent=ChemistryIntent.CONDENSATION_ROUTE,
        account_view=ProviderAccountView(
            accounts={
                "process.overhead_gas": {"Na": 1.0},
                "process.condensation_train": {},
                "process.wall_deposit": {},
            },
            species_formula_registry={},
        ),
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Na",
            "condensed_kg": 0.01,
            "sp_data": {
                "condensation_products_mol_per_mol_vapor": {"Na": 0.0},
            },
            "wall_deposit_fraction": 0.0,
            "dt_hr": 1.0,
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.transition is None
    assert (
        result.diagnostic["reason_skipped"]
        == "empty condensation product credits"
    )
    assert result.diagnostic["credited_condensed_kg"] == pytest.approx(0.0)


def test_wall_allocation_uses_view_factor_and_line_of_sight() -> None:
    raw = robinot_geometry_fixture()
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_lab_geometry(parse_lab_geometry(raw))

    base = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    ).wall_deposit_by_segment_species
    assert base["condenser"]["SiO"] > base["holder"]["SiO"]

    low_view_factor = copy.deepcopy(raw)
    for surface in low_view_factor["surfaces"]:
        if surface["id"] == "condenser":
            surface["view_factor_from_melt"] = 0.01
    model.configure_lab_geometry(parse_lab_geometry(low_view_factor))
    weakened = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    ).wall_deposit_by_segment_species
    assert weakened["condenser"]["SiO"] < weakened["holder"]["SiO"]

    blocked_los = copy.deepcopy(raw)
    for surface in blocked_los["surfaces"]:
        if surface["id"] == "condenser":
            surface["line_of_sight_to_melt"] = False
    model.configure_lab_geometry(parse_lab_geometry(blocked_los))
    blocked = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    ).wall_deposit_by_segment_species
    assert "condenser" not in blocked

    invalid_view_factor = copy.deepcopy(raw)
    invalid_view_factor["surfaces"][2]["view_factor_from_melt"] = 1.1
    with pytest.raises(
        LabGeometryError,
        match="invalid_lab_geometry_view_factor",
    ):
        parse_lab_geometry(invalid_view_factor)


def test_lab_surface_deposit_accounts_conserve_and_roll_up_by_surface() -> None:
    geometry = parse_lab_geometry(robinot_geometry_fixture())
    ledger = AtomLedger(
        initial_balances={
            "process.overhead_gas": {
                "SiO": 0.0012,
                "Na": 0.00008,
            }
        }
    )
    before_kg = sum(ledger.total_kg_by_account().values())

    for account, species_kg in (
        (geometry.surfaces[0].wall_deposit_account, {"Na": 0.00002}),
        (geometry.surfaces[1].wall_deposit_account, {"SiO": 0.0002}),
        (geometry.surfaces[2].wall_deposit_account, {"SiO": 0.0007}),
        (geometry.surfaces[3].wall_deposit_account, {"SiO": 0.0003}),
        (geometry.surfaces[3].wall_deposit_account, {"Na": 0.00006}),
    ):
        transition = LedgerTransition.move(
            "toy_lab_surface_deposit",
            "process.overhead_gas",
            account,
            species_kg,
        )
        transition.validate_conservation()
        ledger.apply(transition)

    state = ledger.mol_by_account()
    after_kg = sum(ledger.total_kg_by_account().values())
    assert after_kg == pytest.approx(before_kg, abs=1.0e-15)
    assert ledger.total_kg_by_account("process.overhead_gas") == pytest.approx(0.0)

    report = _wall_deposit_report_kg(state)
    report_mol = _wall_deposit_mol_by_species(state)
    trace_surface_kg = wall_deposit_by_segment_species_kg(ledger)

    assert report["SiO"] == pytest.approx(0.0012)
    assert report["Na"] == pytest.approx(0.00008)
    assert report_mol["SiO"] > 0.0
    assert trace_surface_kg[("window", "SiO")] == pytest.approx(0.0002)
    assert trace_surface_kg[("condenser", "SiO")] == pytest.approx(0.0007)
    assert trace_surface_kg[("filter", "Na")] == pytest.approx(0.00006)
