import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator import condensation as condensation_module
from simulator.condensation import CondensationModel, KnudsenRegimeRefusal
from simulator.overhead import OverheadGasModel
from simulator.runner import _sio_wall_terminal_mol, build_sio_yield_report
from simulator.state import (
    CampaignPhase,
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sio_yield"

GOLDENS = (
    ("lunar_mare_low_ti", "lunar_mare_low_ti_c2a.json"),
    ("mars_basalt", "mars_basalt_c2a.json"),
)

# Post 2026-05-20 Antoine P_sat refit: builtin SiO fallback fitted to VapoRock,
# so evolved SiO dropped ~4700x to the activity-corrected magnitude.
# Was {lunar: 3.7303230676, mars: 3.82533227031} pre-refit.
# Post-0.5.0 (2026-05-27) MnO NIST-JANAF refit + autoreview-r8 vapor-pressure
# raise-on-unavailable: lunar shifted 0.00078662141565 -> 0.000786620599287
# (PPM-scale FP roundoff drift from the Mn entry change altering
# _stub_equilibrium iteration order); mars unchanged.
# Post-0.5.1 Phase A2 (2026-05-27) Mn high-T linear refit (Mn(l) basis):
# tiny FP roundoff again, lunar 0.000786620599287 -> 0.000786620612837;
# mars unchanged. Mn doesn't itself evaporate in this recipe band; the
# drift is purely from the Mn entry's effect on _stub_equilibrium
# dict-iteration order rounding.
# 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip exposes
# backpressure-floor physics; previously the synthetic no-headspace pO2
# floor (gas.composition['O2'] = max(..., melt.pO2_mbar)) masked the
# holdup feedback. Under PN2_SWEEP (C2A_continuous) the legacy path
# wrote a non-zero O2 composition entry derived from the
# total_evap_kg_hr / conductance pipe-pressure model; under
# finite-headspace ON, _commanded_pO2_bar reads the real overhead-gas
# O2 inventory (≈ vacuum floor 1e-9 bar in PN2_SWEEP, since C2A has
# pO2_mbar=0 and the SiO2 disproportionation O2 leaves via the
# overhead bleed). Lower effective pO2 → less SiO suppression via the
# 1/sqrt(pO2) Ellingham factor → ~2.5x more SiO evolves. Direction
# is physics-honest: PN2_SWEEP recipes ARE supposed to give vacuum-like
# SiO release; the prior legacy path silently held a synthetic O2
# floor that came from numerical artifacts of the conductance ratio,
# not from a real overhead inventory. Lunar
# 0.000786620612837 → 0.00193652062882 (~+146% relative); mars
# 0.000850874178948 → 0.00209489954469 (~+146% relative).
# 2026-06-03 extraction pO2 de-dup: VAPOR_PRESSURE owns commanded pO2 once;
# EVAPORATION_FLUX no longer reapplies an oxygen factor.  Vacuum-side C2A
# SiO yield rises by +0.0056% relative, a deliberate physics consequence.
# 2026-06-14 dense VapoRock pseudo-Antoine refit: fallback SiO residual is
# now honest against the 217-sample dense IW grid.  The new curve increases
# vacuum-side C2A SiO vs the stale fallback, while preserving mass closure.
# 2026-06-15 pure-component vapor correction: builtin metal reference pressure
# now selects grounded `pure_component_antoine` sidecars where available instead
# of legacy/pseudo `antoine` rows.  The SiO yield drop is driven by GLOBAL
# sidecar-first selection, dominated by grounded Na/K sidecars raising alkali
# vapor pressure (K_Pmax ~0.034 -> ~11089 Pa).  That raises finite-headspace pO2
# (~8.3e-5 -> ~2.1e-4 bar) and suppresses SiO via the 1/sqrt(pO2) law in
# `engines/builtin/vapor_pressure.py`.  It is not caused by the Si sidecar alone.
# Lunar 0.00118604428466 -> 0.000508314373589; mars 0.00202232236423 ->
# 0.000486760127234.  This is a deliberate physics move, not a retune; mass
# closure remains covered by `tests/test_mass_balance.py`.
# 2026-06-15 Mn/Ti Alcock/CRC grounding nudges the coupled fallback state:
# lunar 0.000508314373589 -> 0.000508314489862; mars unchanged in this gate.
# 2026-06-15 Mn/Ti Alcock source-equation refit removes sparse-anchor drift:
# lunar 0.000508314489862 -> 0.000508314500607; mars unchanged in this gate.
# 2026-06-15 Mn/Mg vapor upgrade: Mn liquid is rebased to NIST-JANAF/Chase
# and Mg is rebased from a CC estimate to the CRC/Stull source-table fit.
# Lunar SiO moves 0.000508314500607 -> 0.0005083144891 and Mars moves
# 0.000486760127234 -> 0.000486760127354. The visible golden movement is
# mostly Mg wall-deposit/fouling text; SiO movement is FP-scale.
# 2026-06-19 SSO-R R2.1b wires Kress91 Fe-redox into live a_FeO. The SiO
# fixture shift is coupled-flow scale: lunar 0.0005083144891 ->
# 0.000508314464643; Mars 0.000486760127354 -> 0.000486760105302.
# 2026-06-20 fw-vapor refix: VapoRock is diagnostic-only; the default
# C2A vapor-pressure dispatch now uses builtin-authoritative pressures.
# The NIST pure-component ranges remain the authoritative diagnostics; no
# legacy Ca row-level retuning is accepted without a named source.
# 2026-06-28 alpha-series source model removes the final source-side stir
# multiplier and adds gas/melt resistances, so less SiO reaches the downstream
# deposition chain. This is a source-rate move, not a deposition retune:
# lunar 0.000508314464643 -> 8.92476013101e-06; Mars
# 0.000486760105302 -> 6.94408791991e-06.
# 2026-06-29 SiO alpha_s(T) grounds Wetzel/Gail 2013 Arrhenius instead of the
# fixed 0.04 pin. Source alpha at the C2A SiO release T is 0.0320984652281.
# 2026-06-30 cold-wall condensation now uses the Pound 1972 unity gate below
# the evaporation-Arrhenius validity floor; evolved SiO is unchanged here, but
# more of it deposits in the cold wall/stage train instead of terminal offgas.
# 2026-07-01 pre-0.6 SiO vapor rebaseline (V25): the oxide-Antoine valid range
# is extended past 2200 K (previously the SiO row silently vanished above the
# range, zeroing SiO vapor in the hotter part of the C2A band); SiO that was
# wrongly zeroed now evolves. Lunar stays within the old valid band (FP-identical
# to <1e-12); mars 7.06398603045e-06 -> 7.06523011228e-06 (+0.018%). Direction
# is physics-honest (SiO no longer wrongly zeroed), not a retune; mass closure
# remains covered by tests/test_mass_balance.py.
# 2026-07-02 SSO-R ch2c evaporative metal/O-loss coupling: the melt
# self-oxidizes as volatiles bake out. Mars (higher alkali/volatile load)
# suppresses SiO -20% (7.06523011228e-06 -> 5.67671326661e-06); lunar
# C2A_continuous is FP-IDENTICAL (the cooler continuous dwell never enters
# the coupling regime — consistent with the staged-vs-continuous dwell
# physics). Correction-class; not a retune.
BASELINE_SIO_EVOLVED_KG = {
    "lunar_mare_low_ti": 9.11967185718e-06,
    "mars_basalt": 5.67671326661e-06,
}

# 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip +
# default `StirState(radial=1.0)` (laminar gas-side Sherwood) produces
# Stage 4 SiO carryover ABOVE Stage 3 SiO zone product — documented
# "Known limitation" in CHANGELOG 0.5.3 as a routing trade-off
# (operators bump `stir_state.radial > 1.0` for Stage 3 dominance,
# or retune Stage 3 temps). This is physics-honest: the +146% SiO
# release from synthetic→holdup pO2 saturates Stage 3's laminar Sh
# capture cap and overflows downstream. The two assertions below
# pin both:
#   1. Absolute ceiling on Stage 4 (regression catch — runaway), and
#   2. Stage 4 > Stage 3 ordering invariant (forces CHANGELOG update
#      if defaults change in a way that restores Stage 3 dominance).
# After the 2026-06-28 alpha-series source model, stage_3 is 1.69e-7 kg
# (lunar) / 1.32e-7 kg (mars), and stage_4 remains higher at 3.18e-7 kg
# (lunar) / 2.47e-7 kg (mars).
# Predecessor history (for legacy reviewers): pre-Phase-A1 values were
# 1.65257779038 / 1.69466902181 kg, sat above the legacy stage_3 ~1 kg
# magnitude; the post-flip regime is ~1.94 mg total SiO evolved
# (1000x less absolute mass, with relative routing inverted).
BASELINE_STAGE4_SIO2_KG = {
    "lunar_mare_low_ti": 0.01,  # absolute ceiling; live ~5.07e-4 kg
    "mars_basalt": 0.01,        # absolute ceiling; live ~5.49e-4 kg
}


def _assert_golden_close(actual, expected, path="root"):
    if isinstance(expected, dict):
        assert set(actual) == set(expected), path
        for key in expected:
            _assert_golden_close(actual[key], expected[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, expected_item in enumerate(expected):
            _assert_golden_close(
                actual[index], expected_item, f"{path}[{index}]")
        return
    if isinstance(expected, (int, float)):
        tolerance = max(abs(float(expected)) * 0.01, 1.0e-12)
        assert abs(float(actual) - float(expected)) <= tolerance, path
        return
    assert actual == expected, path


@pytest.mark.parametrize(("feedstock", "golden_name"), GOLDENS)
def test_sio_yield_cli_matches_golden(tmp_path, feedstock, golden_name):
    output_path = tmp_path / golden_name
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner.sio_yield",
            "--feedstock",
            feedstock,
            "--campaign",
            "C2A_continuous",
            "--hours",
            "24",
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    actual = json.loads(output_path.read_text())
    expected = json.loads((FIXTURE_DIR / golden_name).read_text())

    _assert_golden_close(actual, expected)
    # Strict-equality on a baseline float caught FP-jitter (~1e-12 absolute,
    # ~1e-9 relative) introduced by F4 rump-payload assembly + S1b shuttle
    # gate after the post-2026-05-20 refit established the baseline. The
    # numbers themselves are still physics-honest (≤5e-12 % mass closure
    # held in Review E + E2 default-on test). Loosen to relative tolerance.
    assert actual["sio_evolved_kg"] == pytest.approx(
        BASELINE_SIO_EVOLVED_KG[feedstock], rel=1e-8
    )
    assert expected["sio_evolved_kg"] == pytest.approx(
        BASELINE_SIO_EVOLVED_KG[feedstock], rel=1e-8
    )
    assert "wall_deposit_kg" in actual
    assert "fouling_rate" in actual
    placement = actual["sio_to_silica_fume_kg"]
    stage_3 = placement["stage_3_sio_zone_product"]
    stage_4 = placement["stage_4_alkali_mg_carryover"]
    assert stage_3 > 0.0
    assert stage_4 < BASELINE_STAGE4_SIO2_KG[feedstock]
    # 0.5.3 Phase A1 routing-trade-off invariant: under default
    # `StirState(radial=1.0)` laminar gas-side Sherwood + finite-headspace
    # ON, Stage 4 SiO carryover > Stage 3 SiO zone product on this
    # feedstock. Documented as "Known limitation" in CHANGELOG 0.5.3.
    # Pinned so a future defaults change (e.g., radial→2.0 globally,
    # or a Stage 3 temp retune) that restores Stage 3 dominance forces
    # both this assertion update AND a CHANGELOG entry.
    assert stage_4 > stage_3, (
        f"Phase A1 SiO routing trade-off changed: stage_3={stage_3:.3e} "
        f"stage_4={stage_4:.3e} for {feedstock}. Update CHANGELOG."
    )
    assert 0.0 <= actual["sio_yield_pct_of_feedstock"] <= 30.0
    assert actual["alpha_SiO"] == pytest.approx(0.0320984652281)
    assert actual["alpha_provenance"] == (
        "Wetzel & Gail 2013 A&A 553 A92 DOI "
        "10.1051/0004-6361/201220803; "
        "alpha_s_SiO(T)=0.52*exp(-3685/T), reaction-rate-limited"
    )
    assert "order-of-magnitude regime check" in actual["verdict"]
    assert "not 1-decade fidelity" in actual["verdict"]


def test_sio_yield_diagnostics_include_wall_sticking_alpha_notice():
    report, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=True,
    )

    assert "wall_deposit_kg" in report
    notice = diagnostics["wall_sticking_alpha_provenance_notice"]
    assert notice["severity"] == "warning"
    assert notice["code"] == "wall_deposit_sticking_alpha_uncertified"
    assert notice["source_class"] == "status_bearing_material_alpha"
    assert "cited_hkl_accommodation" in notice["source_classes"]
    assert "uncertified_na_analogy_default" in notice["source_classes"]
    assert "Mg" in notice["species"]
    assert notice["alpha_s_by_species"]["Mg"] == pytest.approx(0.2)
    assert (
        notice["grounding_target"]
        == "data/literature/vacuum_pyrolysis_sticking.yaml"
    )


def test_band_aware_hkl_route_captures_sio_in_stage_3():
    model = CondensationModel(CondensationTrain.create_default())

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )

    assert route.condensed_by_stage_species[3]["SiO"] > 0.0
    assert route.condensed_by_stage_species[4]["SiO"] < 0.95
    # Cold-wall SiO now uses the Pound 1972 unity condensation gate below the
    # Wetzel/Gail evaporation-Arrhenius validity floor, so less vapor remains
    # in offgas once it reaches cold stages.
    assert route.remaining_by_species["SiO"] == pytest.approx(
        0.04275021936015011
    )
    assert route.wall_deposit_by_species.get("SiO", 0.0) >= 0.0


def test_route_destinations_sum_to_evolved_budget():
    model = CondensationModel(CondensationTrain.create_default())
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    destinations = (
        route.condensed_for_species("SiO")
        + route.wall_deposit_by_species.get("SiO", 0.0)
        + route.remaining_by_species["SiO"]
    )
    assert destinations == pytest.approx(1.0)


def test_sio_wall_terminal_mol_sums_all_wall_si_atoms():
    # SiO is the only Si vapor species, so every Si atom on the wall -- direct
    # SiO, the disproportionation pair Si + SiO2, and further-reduced FeSi --
    # descends from one evaporated SiO. The SiO-equivalent is their Si-atom SUM
    # (Si-atom conservation for the evaporated -> destinations chain closure),
    # NOT a disproportionation-paired 2*min() that drops unpaired wall Si.
    wall_deposit = {
        "SiO": 0.25,
        "Si": 0.5,
        "SiO2": 0.5,
        "FeSi": 2.0,
    }

    assert _sio_wall_terminal_mol(wall_deposit) == pytest.approx(3.25)


def test_condensation_route_flags_metal_antoine_valid_range_extrapolation():
    ca_data = condensation_module.VAPOR_PRESSURE_DATA["metals"]["Ca"]
    assert ca_data["pure_component_antoine"]["valid_range_K"] == [1254, 1712]

    model = CondensationModel(CondensationTrain.create_default())
    melt = MeltState()
    melt.temperature_C = 1400.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"Ca": 1.0e-6}, total_kg_hr=1.0e-6),
        melt,
    )

    extrapolation = route.antoine_extrapolations["Ca"]
    assert extrapolation["temperature_K"] == pytest.approx(780.0 + 273.15)
    assert tuple(extrapolation["valid_range_K"]) == (1254.0, 1712.0)
    assert any(
        "Ca metal Antoine fit extrapolated beyond valid_range_K" in warning
        for warning in route.antoine_extrapolation_warnings
    )
    assert route.remaining_by_species["Ca"] >= 0.0


def test_wall_deposit_flags_antoine_extrapolation_at_wall_temperature():
    sio_data = condensation_module.VAPOR_PRESSURE_DATA["oxide_vapors"]["SiO"]
    assert sio_data["valid_range_K"] == [1400, 2200]

    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    assert "SiO" in route.antoine_extrapolations
    assert tuple(route.antoine_extrapolations["SiO"]["valid_range_K"]) == (
        1400.0,
        2200.0,
    )
    assert any(
        "SiO metal Antoine fit extrapolated beyond valid_range_K" in warning
        and "1173.15 K" in warning
        for warning in route.antoine_extrapolation_warnings
    )


def test_cold_liner_routes_sio_to_wall_deposit_bucket():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    assert route.wall_deposit_by_species["SiO"] > 0.0
    destinations = (
        route.condensed_for_species("SiO")
        + route.wall_deposit_by_species["SiO"]
        + route.remaining_by_species["SiO"]
    )
    assert destinations == pytest.approx(1.0)


def test_wall_deposit_sticking_alpha_notice_tracks_cold_wall_gate():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    assert route.wall_deposit_by_species["SiO"] == pytest.approx(
        0.0012296595884093348,
        rel=1e-12,
    )
    assert (
        route.condensed_for_species("SiO")
        + route.wall_deposit_by_species["SiO"]
        + route.remaining_by_species["SiO"]
    ) == pytest.approx(1.0)
    notice = route.sticking_alpha_provenance_notice
    assert notice["severity"] == "warning"
    assert notice["code"] == "wall_deposit_sticking_alpha_uncertified"
    assert notice["source_class"] == "status_bearing_material_alpha"
    assert "cited_hkl_accommodation" in notice["source_classes"]
    assert "fail_closed_no_direct_sticking_coefficient" in notice["source_classes"]
    assert notice["species"] == ["SiO"]
    assert notice["alpha_s_by_species"]["SiO"] == pytest.approx(
        0.022481955557451427
    )
    assert (
        notice["alpha_s_provenance_by_species"]["SiO"]["stage_2_to_stage_3"][
            "alpha_s"
        ]
        == pytest.approx(0.0)
    )
    # Reported alphas are the wall-path values; the capture-budget path reads
    # the same literature sidecar defaults and must not be conflated with
    # material-specific wall overrides.
    assert notice["alpha_s_source"] == "_wall_alpha_s"
    assert (
        notice["capture_budget_alpha_s_source"]
        == "data/literature/vacuum_pyrolysis_sticking.yaml"
    )
    assert (
        notice["grounding_target"]
        == "data/literature/vacuum_pyrolysis_sticking.yaml"
    )


def test_per_segment_wall_deposits_sum_to_aggregate_bucket():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    segment_total = sum(
        species_kg.get("SiO", 0.0)
        for species_kg in route.wall_deposit_by_segment_species.values()
    )
    assert segment_total == pytest.approx(
        route.wall_deposit_by_species["SiO"]
    )
    assert set(route.wall_deposit_by_segment_species).issubset({
        segment.name for segment in model.pipe_segments
    })


def test_intentional_pipe_cold_spot_flags_and_changes_wall_deposit():
    train = CondensationTrain.create_default()
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    hot = CondensationModel(train, wall_temperature_C=1500.0)
    hot.configure_operating_conditions(
        wall_temperature_C=1500.0,
        pipe_segment_temperatures_C={
            segment.name: (
                1030.0 if segment.name == "stage_0_to_stage_1" else 1800.0
            )
            for segment in hot.pipe_segments
        },
    )
    cold = CondensationModel(train, wall_temperature_C=1500.0)
    cold.configure_operating_conditions(
        wall_temperature_C=1500.0,
        pipe_segment_temperatures_C={
            segment.name: (
                900.0 if segment.name == "stage_0_to_stage_1" else 1800.0
            )
            for segment in cold.pipe_segments
        },
    )

    hot_route = hot.route(flux, melt)
    cold_route = cold.route(flux, melt)

    # Cold spots are deliberate wall-deposit accounting signals, not refusals.
    assert not hot_route.cold_spot_warnings
    assert cold_route.cold_spot_warnings
    assert "stage_0_to_stage_1" in cold_route.cold_spot_warnings[0]
    assert cold.last_cold_spot_diagnostic["has_cold_spot"] is True
    # Order-independent: a future multi-species flux must not break this lock.
    assert any(
        f["species"] == "SiO"
        for f in cold.last_cold_spot_diagnostic["findings"]
    )
    assert cold_route.wall_deposit_by_species["SiO"] < (
        hot_route.wall_deposit_by_species.get("SiO", 0.0)
    )


def test_cached_condensation_model_uses_updated_liner_temperature():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    cold_route = model.route(flux, melt)
    model.configure_operating_conditions(
        wall_temperature_C=1650.0,
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
    )
    hot_route = model.route(flux, melt)

    assert cold_route.wall_deposit_by_species["SiO"] > 0.0
    assert hot_route.wall_deposit_by_species.get("SiO", 0.0) > (
        cold_route.wall_deposit_by_species["SiO"]
    )


def test_knudsen_regime_factor_rises_toward_ballistic():
    viscous_kn = condensation_module._knudsen_number(
        pressure_pa=1000.0,
        T_K=1773.15,
        characteristic_length_m=0.12,
    )
    ballistic_kn = condensation_module._knudsen_number(
        pressure_pa=0.1,
        T_K=1773.15,
        characteristic_length_m=0.12,
    )

    assert viscous_kn < 0.01
    assert ballistic_kn > 1.0
    assert condensation_module._knudsen_regime_factor(viscous_kn) < 0.1
    assert condensation_module._knudsen_regime_factor(ballistic_kn) > 0.99


def test_low_pressure_free_molecular_regime_refuses_condensation():
    train = CondensationTrain.create_default()
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    viscous = CondensationModel(train, wall_temperature_C=1100.0)
    viscous.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
        gas_temperature_C=1100.0,
    )
    ballistic = CondensationModel(train, wall_temperature_C=1100.0)
    ballistic.configure_operating_conditions(
        overhead_pressure_mbar=1.0e-6,
        pipe_diameter_m=0.12,
        gas_temperature_C=1100.0,
    )

    viscous_route = viscous.route(flux, melt)

    assert ballistic.regime_factor > viscous.regime_factor
    assert viscous_route.knudsen_regime_diagnostic["status"] == "ok"
    with pytest.raises(KnudsenRegimeRefusal) as exc_info:
        ballistic.route(flux, melt)
    assert exc_info.value.reason == "knudsen_outside_viscous_flow"
    assert exc_info.value.diagnostic["status"] == "refused"


def test_liner_temperature_schedule_is_recipe_controllable():
    model = OverheadGasModel(
        {
            "liner_temperature_C": {
                "default_C": 1500.0,
                "schedule": [
                    {
                        "campaign": "C2A",
                        "from_campaign_hour": 0,
                        "to_campaign_hour": 4,
                        "start_C": 1100,
                        "end_C": 1600,
                    },
                    {
                        "campaign": "C2A",
                        "from_campaign_hour": 4,
                        "start_C": 1600,
                        "end_C": 1600,
                    },
                ],
            }
        }
    )
    melt = MeltState()
    melt.campaign = CampaignPhase.C2A

    melt.campaign_hour = 0
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1100.0)
    melt.campaign_hour = 2
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1350.0)
    melt.campaign_hour = 8
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1600.0)


def test_pipe_conductance_fail_closes_on_nonphysical_absolute_temperature():
    model = OverheadGasModel()

    assert model._pipe_conductance(100.0, -273.15) == 0.0
    assert model._pipe_conductance(100.0, -274.0) == 0.0


def test_po2_wall_sweep_mode_suppresses_first_tick_sio_release():
    """The pO2 lever suppresses SiO via the 1/sqrt(pO2) Ellingham factor.

    0.5.3 Phase A1 (2026-05-28) refactor: under finite-headspace default-on,
    the commanded-pO2 floor is restricted to actively O2-controlled
    atmospheres (CONTROLLED_O2 / CONTROLLED_O2_FLOW / O2_BACKPRESSURE) so
    an uncontrolled HARD_VACUUM / PN2_SWEEP run does not get a synthetic
    floor (per the design intent at simulator/equilibrium.py:9-12). The
    legacy ``build_sio_yield_report(pO2_mbar=1.0)`` lever wrote
    ``melt.pO2_mbar`` under the C2A PN2_SWEEP atmosphere and relied on
    the no-headspace branch's unconditional synthetic O2 floor to make
    it stick; under finite-headspace ON that floor no longer applies in
    PN2_SWEEP. Per triage doc Option 1, this test now drives the
    simulator directly with CONTROLLED_O2 atmosphere where the floor
    DOES apply, preserving the lever-suppression assertion under the
    right atmosphere semantics. Verifies the 1e-5 SiO drop still holds
    via the proper finite-headspace path.
    """

    from pathlib import Path
    import yaml
    from simulator.state import Atmosphere, CampaignPhase
    from tests.chemistry.conftest import _build_sim

    data_dir = Path(__file__).resolve().parent.parent / "data"
    vapor_pressure_data = yaml.safe_load(
        (data_dir / "vapor_pressures.yaml").read_text()
    )
    feedstocks_data = yaml.safe_load(
        (data_dir / "feedstocks.yaml").read_text()
    )
    setpoints_data = yaml.safe_load((data_dir / "setpoints.yaml").read_text())

    def _evolved_sio_kg_one_tick(*, pO2_mbar: float) -> float:
        sim = _build_sim(
            "lunar_mare_low_ti",
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
        )
        sim.start_campaign(CampaignPhase.C2A)
        # 0.5.3 Phase A1 swap: drive the lever via CONTROLLED_O2, where
        # the commanded-pO2 floor is live under finite-headspace ON.
        sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        sim.melt.pO2_mbar = float(pO2_mbar)
        sim.melt.p_total_mbar = max(sim.melt.p_total_mbar, float(pO2_mbar))
        sim.melt.temperature_C = 1500.0
        # Capture starting SiO2 inventory before one tick.
        initial_sio2_mol = float(
            sim.atom_ledger.mol_by_account(
                "process.cleaned_melt"
            ).get("SiO2", 0.0)
        )
        sim.step()
        final_sio2_mol = float(
            sim.atom_ledger.mol_by_account(
                "process.cleaned_melt"
            ).get("SiO2", 0.0)
        )
        sio_mol = max(0.0, initial_sio2_mol - final_sio2_mol)
        from simulator.state import MOLAR_MASS
        sio_molar_mass_kg_mol = MOLAR_MASS["SiO"] / 1000.0
        return sio_mol * sio_molar_mass_kg_mol

    # pO2 ~vacuum: tiny floor, near hard-vacuum suppression.
    no_suppress = _evolved_sio_kg_one_tick(pO2_mbar=1.0e-6)
    # pO2 = 1 mbar: the lever asserts the 1/sqrt(pO2) Ellingham suppression.
    o2_mode = _evolved_sio_kg_one_tick(pO2_mbar=1.0)

    # pO2 is now applied once in VAPOR_PRESSURE.  The old <1e-5 guard encoded
    # a double application: VP suppression plus a second flux-side pO2 factor.
    # One 1e-6 mbar -> 1 mbar SiO suppression is ~1e-3.
    assert o2_mode < no_suppress * 1.1e-3, (
        f"o2_mode={o2_mode}, no_suppress={no_suppress}, "
        f"ratio={o2_mode / max(no_suppress, 1e-300)}"
    )


def test_hkl_sampling_uses_actual_stage_band_not_material_defaults():
    custom_stage = CondensationStage(
        3, "Custom hot SiO stage", (1100.0, 1200.0), ["SiO"]
    )

    assert condensation_module._stage_temp_band_C(custom_stage) == (
        1100.0,
        1200.0,
    )


def test_no_antoine_species_cannot_create_unplaced_capture_budget():
    model = CondensationModel(CondensationTrain.create_default())

    route = model.route(
        EvaporationFlux(species_kg_hr={"O2": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )

    assert route.remaining_by_species["O2"] == pytest.approx(1.0)
    assert route.condensed_for_species("O2") == pytest.approx(0.0)
