"""Stage-4 alkali freeze-out cartridge: equilibrium fraction, Stokes cut, swing."""

from __future__ import annotations

import math

import pytest

from engines.builtin.sodium_saturation import sodium_saturation_block
from simulator.account_ids import (
    ALKALI_CARTRIDGE_ACCOUNT,
    ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT,
)
from simulator.accounting.ledger import AtomLedger
from simulator.condensation import (
    CondensationModel,
    alkali_cartridge_phase,
    alkali_cartridge_swing_moves,
    baffle_impaction_efficiency,
    cartridge_aerosol_efficiency,
    cartridge_capture_fraction,
    cunningham_slip_correction,
    equilibrium_capture_fraction,
    single_row_impaction_efficiency,
    sodium_melting_temperature_K_from_row,
    stokes_number,
    vapour_entu_capture_fraction,
)
from simulator.condensation_routing import K_TRAP_STAGE_KEY
from simulator.config import load_config_bundle
from simulator.core import (
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PyrolysisSimulator,
)
from simulator.state import MOLAR_MASS

_TRAIN = "process.condensation_train"


def _sodium_melting_K() -> float:
    bundle = load_config_bundle()
    row = bundle.vapor_pressures["metals"]["Na"]
    block = sodium_saturation_block(row)
    assert block is not None
    melting_K = sodium_melting_temperature_K_from_row(row)
    assert melting_K == pytest.approx(float(block["melting_temperature_K"]))
    return float(melting_K)


def test_equilibrium_fraction_limits():
    assert equilibrium_capture_fraction(0.0, 100.0) == 1.0
    assert equilibrium_capture_fraction(100.0, 100.0) == 0.0
    assert equilibrium_capture_fraction(250.0, 100.0) == 0.0
    assert equilibrium_capture_fraction(50.0, 100.0) == pytest.approx(0.5)
    assert equilibrium_capture_fraction(10.0, 0.0) == 0.0


def test_vapour_entu_limits():
    assert vapour_entu_capture_fraction(1.0, 0.0) == 0.0
    assert vapour_entu_capture_fraction(0.0, 10.0) == 0.0
    assert vapour_entu_capture_fraction(0.4, math.inf) == pytest.approx(0.4)
    large = vapour_entu_capture_fraction(1.0, 40.0)
    assert large == pytest.approx(1.0)
    assert 0.0 < vapour_entu_capture_fraction(1.0, 1.0) < 1.0


def test_stokes_cut_limits():
    assert cunningham_slip_correction(0.0) == 1.0
    assert cunningham_slip_correction(1.0) > 1.0
    assert single_row_impaction_efficiency(0.0) == 0.0
    assert single_row_impaction_efficiency(1.0) == pytest.approx(0.5)
    assert single_row_impaction_efficiency(math.inf) == 1.0
    assert baffle_impaction_efficiency(1.0, 1) == pytest.approx(
        single_row_impaction_efficiency(1.0)
    )
    many = baffle_impaction_efficiency(1.0, 20)
    assert many > baffle_impaction_efficiency(1.0, 1)
    assert many <= 1.0
    assert baffle_impaction_efficiency(0.0, 8) == 0.0

    quiet = stokes_number(
        droplet_diameter_m=1.0e-6,
        droplet_density_kg_m3=900.0,
        gas_velocity_m_s=0.0,
        baffle_spacing_m=0.01,
        gas_viscosity_pa_s=1.8e-5,
        cunningham=1.0,
    )
    assert quiet == 0.0
    assert cartridge_aerosol_efficiency(
        droplet_diameter_m=1.0e-6,
        droplet_density_kg_m3=900.0,
        baffle_spacing_m=0.01,
        passage_count=3,
        gas_velocity_m_s=0.0,
        gas_viscosity_pa_s=1.8e-5,
        mean_free_path_m=1.0e-5,
    ) == 0.0
    slipped = cartridge_aerosol_efficiency(
        droplet_diameter_m=1.0e-6,
        droplet_density_kg_m3=900.0,
        baffle_spacing_m=1.0e-4,
        passage_count=1,
        gas_velocity_m_s=50.0,
        gas_viscosity_pa_s=1.8e-5,
        mean_free_path_m=1.0e-4,
    )
    continuum = cartridge_aerosol_efficiency(
        droplet_diameter_m=1.0e-6,
        droplet_density_kg_m3=900.0,
        baffle_spacing_m=1.0e-4,
        passage_count=1,
        gas_velocity_m_s=50.0,
        gas_viscosity_pa_s=1.8e-5,
        mean_free_path_m=0.0,
    )
    assert slipped > continuum


def test_aerosol_does_not_double_count_vapour():
    assert cartridge_capture_fraction(0.2, 0.8, 1.0, wall_is_fog=False) == 0.2
    assert cartridge_capture_fraction(0.2, 0.8, None, wall_is_fog=True) == 0.2
    assert cartridge_capture_fraction(0.2, 0.8, 0.0, wall_is_fog=True) == 0.2
    assert cartridge_capture_fraction(0.2, 0.8, 1.0, wall_is_fog=True) == pytest.approx(0.8)


def test_shipped_cartridge_wall_and_melt_out_come_from_setpoints():
    train = CondensationTrain.create_default()
    trap = next(stage for stage in train.stages if stage.stage_key == K_TRAP_STAGE_KEY)
    assert trap.label == "Alkali freeze-out cartridge"
    assert trap.temp_range_C == (40.0, 40.0)
    assert trap.sub_order == 1
    assert trap.melt_out_temperature_C == 120.0
    melting_K = _sodium_melting_K()
    phase, notices = alkali_cartridge_phase(
        "Na",
        trap.melt_out_temperature_C,
        sodium_melting_temperature_K=melting_K,
    )
    assert phase == "liquid"
    assert notices == ()
    cold, cold_notices = alkali_cartridge_phase(
        "Na",
        melting_K - 273.15 - 1.0,
        sodium_melting_temperature_K=melting_K,
    )
    assert cold == "solid"
    assert cold_notices == ()
    potassium, potassium_notices = alkali_cartridge_phase(
        "K",
        trap.melt_out_temperature_C,
        sodium_melting_temperature_K=melting_K,
    )
    assert potassium == "liquid"
    assert potassium_notices == (
        "potassium_melting_point_unavailable_owner_liquid_rule",
    )
    solid, solid_notices = alkali_cartridge_phase(
        "Mg",
        trap.melt_out_temperature_C,
        sodium_melting_temperature_K=melting_K,
    )
    assert solid == "solid"
    assert solid_notices == ()
    held, held_notices = alkali_cartridge_phase(
        "Na", None, sodium_melting_temperature_K=melting_K,
    )
    assert held == "hold"
    assert held_notices == ("melt_out_temperature_missing",)


def test_swing_closes_mass_and_keeps_solids_off_the_alkali_train():
    melting_K = _sodium_melting_K()
    captured = {"Na": 1.0, "K": 0.4, "Mg": 0.2, "MgO": 0.1, "Ca": 0.05}
    ledger = AtomLedger(initial_balances={_TRAIN: dict(captured)})
    before = {
        species: kg
        for account in ledger.kg_by_account()
        for species, kg in ledger.kg_by_account(account).items()
    }
    moves, notices = alkali_cartridge_swing_moves(
        captured,
        melt_out_temperature_C=120.0,
        sodium_melting_temperature_K=melting_K,
    )
    assert "potassium_melting_point_unavailable_owner_liquid_rule" in notices
    for move in moves:
        ledger.move(
            move.name,
            move.debit_account,
            move.credit_account,
            {move.species: move.kg},
            reason=move.reason,
            amalgamated_pool=move.species in {"Na", "K", "Mg"},
        )
    cartridge = ledger.kg_by_account(ALKALI_CARTRIDGE_ACCOUNT)
    assert not any(kg > 1e-12 for kg in cartridge.values())
    train = ledger.kg_by_account(_TRAIN)
    residue = ledger.kg_by_account(ALKALI_CARTRIDGE_SOLID_RESIDUE_ACCOUNT)
    assert train.get("Na", 0.0) == pytest.approx(captured["Na"])
    assert train.get("K", 0.0) == pytest.approx(captured["K"])
    for species in ("Mg", "MgO", "Ca"):
        assert train.get(species, 0.0) == pytest.approx(0.0)
        assert residue.get(species, 0.0) == pytest.approx(captured[species])
    after = {
        species: kg
        for account in ledger.kg_by_account()
        for species, kg in ledger.kg_by_account(account).items()
    }
    assert set(after) == set(before)
    for species, kg in before.items():
        assert after[species] == pytest.approx(kg)
    liquid = train.get("Na", 0.0) + train.get("K", 0.0)
    solid = sum(residue.values())
    assert liquid + solid == pytest.approx(sum(captured.values()))


def test_swing_partially_withdraws_mixed_origin_sio_from_the_train():
    ledger = AtomLedger()
    ledger.load_external_mol(
        _TRAIN,
        {"SiO": 1.0},
        source="feedstock SiO",
        material_origin="feedstock",
    )
    ledger.load_external_mol(
        _TRAIN,
        {"SiO": 1.0},
        source="recovered SiO",
        material_origin="reagent",
    )

    sim = object.__new__(PyrolysisSimulator)
    sim.atom_ledger = ledger
    sim.train = CondensationTrain.create_default()
    sim.vapor_pressures = load_config_bundle().vapor_pressures
    sim._retarget_stage_collection_source = lambda *_args, **_kwargs: None
    stage_idx = next(
        index for index, stage in enumerate(sim.train.stages)
        if stage.stage_key == K_TRAP_STAGE_KEY
    )
    swing_kg = 0.5 * MOLAR_MASS["SiO"] / 1000.0

    sim._swing_alkali_freeze_out_cartridge(stage_idx, {"SiO": swing_kg})
    assert ledger.kg_by_account(_TRAIN)["SiO"] == pytest.approx(
        1.5 * MOLAR_MASS["SiO"] / 1000.0
    )


def test_missing_melt_out_holds_the_mass_on_the_train():
    moves, notices = alkali_cartridge_swing_moves(
        {"Na": 1.0, "Mg": 0.2},
        melt_out_temperature_C=None,
        sodium_melting_temperature_K=_sodium_melting_K(),
    )
    assert moves == ()
    assert notices == ("melt_out_temperature_missing",)


def test_cartridge_route_flags_missing_geometry_and_potassium_solid_branch():
    train = CondensationTrain.create_default()
    model = CondensationModel(train)
    model.configure_operating_conditions(
        wall_temperature_C=900.0,
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"Na": 0.2, "K": 0.2},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in train.stages
        },
        pipe_segment_temperatures_C={
            segment.name: 900.0 for segment in model.pipe_segments
        },
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"Na": 0.5, "K": 0.3}),
        MeltState(temperature_C=1400.0),
    )
    for species, inlet in (("Na", 0.5), ("K", 0.3)):
        closed = (
            route.condensed_for_species(species)
            + route.wall_deposit_by_species.get(species, 0.0)
            + route.remaining_by_species[species]
        )
        assert closed == pytest.approx(inlet)
        detail = route.cartridge_capture_by_species[species]
        assert detail["eta_aerosol"] is None
        assert "aerosol_unevaluable_missing_baffle_spacing_m" in detail["notices"]
        assert "aerosol_unevaluable_missing_passage_count" in detail["notices"]
        assert "aerosol_unevaluable_missing_droplet_diameter_m" in detail["notices"]
        assert "aerosol_unevaluable_missing_droplet_density_kg_m3" in detail["notices"]
        assert (
            "droplet_diameter_unmeasured_nafog_cooling_rate_not_selected"
            in detail["notices"]
        )
        assert detail["eta"] == pytest.approx(detail["eta_vapour"])
        identity = (4, 1)
        booked = route.capture_kg_by_stage_identity.get(identity, {}).get(species, 0.0)
        if detail["supply_kg_hr"] > 0.0 and detail["eta"] > 0.0:
            assert booked == pytest.approx(detail["supply_kg_hr"] * detail["eta"])
    assert "no_solid_branch_predict_and_flag" in route.cartridge_capture_by_species["K"]["notices"]
    assert (
        "no_solid_branch_predict_and_flag"
        not in route.cartridge_capture_by_species["Na"]["notices"]
    )
