from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.furnace_materials import (
    InterfaceChemistryRefusal,
    load_interface_chemistry,
    load_interface_invariant_table,
    require_interface_porosity,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.liner_corrosion import (
    LinerCorrosionRefusal,
    MageminLinerSampler,
    TierAInvariantSampler,
    contaminated_interface_composition,
    contamination_mixing_fractions,
    corrosion_campaign_recurrence,
    magemin_request_cache_key,
    post_onset_dissolution_ratio,
    reacted_layer_depths,
    tier_a_first_liquid_onset,
)
from simulator.melt_backend.liquidus import (
    LIQUIDUS_LIQUID_EPSILON,
    LIQUIDUS_SOLID_EPSILON,
)


def test_magemin_request_cache_key_covers_every_equilibrium_input():
    composition = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    key = magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct=composition,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
    )

    assert key == magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct={"CaO": 20.0, "SiO2": 50.0, "Al2O3": 30.0},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
    )
    for changed in (
        {"engine": "magemin:other"},
        {"version": 2},
        {"composition_wt_pct": {**composition, "CaO": 21.0, "SiO2": 49.0}},
        {"temperature_C": 1501.0},
        {"pressure_bar": 2.0e-6},
        {"fO2_log": -8.0},
    ):
        request = {
            "engine": "magemin:ig",
            "version": 1,
            "composition_wt_pct": composition,
            "temperature_C": 1500.0,
            "pressure_bar": 1.0e-6,
            "fO2_log": -9.0,
            **changed,
        }
        assert magemin_request_cache_key(**request) != key

    # Composition, temperature, and fO2 use six decimal places; pressure uses
    # eight significant digits so low vacuum values do not round to zero.
    assert key == magemin_request_cache_key(
        engine="magemin:ig",
        version=1,
        composition_wt_pct={
            "SiO2": 50.0000001,
            "Al2O3": 29.9999999,
            "CaO": 20.0,
        },
        temperature_C=1500.0000001,
        pressure_bar=1.00000001e-6,
        fO2_log=-9.00000001,
    )


class _FakeMageminBackend:
    def __init__(self, liquid_fraction=None):
        self.calls = []
        self.initialized = 0
        self.closed = 0
        self.liquid_fraction = liquid_fraction or (
            lambda composition: min(1.0, composition.get("Na2O", 0.0) / 20.0)
        )

    def initialize(self, config):
        self.initialized += 1
        assert config == {"database": "ig"}
        return True

    def equilibrate(
        self,
        temperature_C,
        *,
        pressure_bar,
        fO2_log,
        projected_oxide_wt_pct,
    ):
        self.calls.append(
            (temperature_C, pressure_bar, fO2_log, dict(projected_oxide_wt_pct))
        )
        return SimpleNamespace(
            status="ok",
            liquid_fraction=self.liquid_fraction(projected_oxide_wt_pct),
            phase_masses_kg={"liq": 1.0},
            phase_compositions={"liq": dict(projected_oxide_wt_pct)},
            warnings=[],
        )

    def close(self):
        self.closed += 1

    def find_liquidus_solidus(self, *args, **kwargs):
        raise AssertionError("liner adapter must use equilibrate(), never the finder")


def test_magemin_adapter_gates_before_engine_and_checks_phase_closure():
    backend = _FakeMageminBackend()
    sampler = MageminLinerSampler(backend)
    with pytest.raises(LinerCorrosionRefusal) as refused:
        sampler({"Al2O3": 60.0, "CaO": 40.0}, 1500.0, 1.0e-6)
    assert refused.value.reason == "liner_first_melt_engine_out_of_domain"
    assert backend.calls == []
    assert backend.initialized == 0

    composition = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    assert sampler(composition, 1500.0, 1.0e-6) == 0.0
    assert sampler(composition, 1500.0, 1.0e-6) == 0.0
    assert backend.initialized == 1
    assert len(backend.calls) == 1
    assert backend.calls[0][:3] == (1500.0, 1.0e-6, -9.0)

    bad_closure = _FakeMageminBackend()
    bad_closure.equilibrate = lambda *args, **kwargs: SimpleNamespace(
        status="ok",
        liquid_fraction=0.5,
        phase_masses_kg={"liq": 1.0},
        phase_compositions={"liq": {"SiO2": 100.0}},
        warnings=[],
    )
    with pytest.raises(LinerCorrosionRefusal) as closure:
        MageminLinerSampler(bad_closure)(composition, 1500.0, 1.0e-6)
    assert closure.value.reason == "engine_phase_closure_failed"


def test_magemin_phi_curve_is_cached_and_interpolated():
    backend = _FakeMageminBackend()
    sampler = MageminLinerSampler(backend)
    liner = {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0}
    first = sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
    )
    first_call_count = len(backend.calls)
    second = sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
    )
    assert second is first
    assert len(backend.calls) == first_call_count
    composition = {
        oxide: 0.9 * liner.get(oxide, 0.0)
        + 0.1 * first.deposit_composition_wt_pct.get(oxide, 0.0)
        for oxide in set(liner) | set(first.deposit_composition_wt_pct)
    }
    assert second(composition, 1500.0, 1.0e-6) == pytest.approx(
        min(1.0, composition.get("Na2O", 0.0) / 20.0), abs=0.02
    )
    sampler.curve(
        liner,
        {"Na": 0.1},
        temperature_C=1501.0,
        pressure_bar=1.0e-6,
    )
    assert len(backend.calls) > first_call_count


@pytest.mark.live_engine
@pytest.mark.xdist_group("magemin_fullrun_a")
def test_anorthite_first_liquid_anchor_requires_live_magemin():
    # CaAl2Si2O8 = CaO + Al2O3 + 2 SiO2. The literature melting point is
    # 1553 C. Formula weights below independently derive the oxide-basis bulk.
    cao = 40.078 + 15.999
    alumina = 2 * 26.9815385 + 3 * 15.999
    silica = 28.085 + 2 * 15.999
    total = cao + alumina + 2 * silica
    anorthite = {
        "CaO": 100.0 * cao / total,
        "Al2O3": 100.0 * alumina / total,
        "SiO2": 200.0 * silica / total,
    }
    sampler = MageminLinerSampler()
    try:
        try:
            solid = sampler(anorthite, 1500.0, 1.0e-6)
            liquid = sampler(anorthite, 1600.0, 1.0e-6)
        except LinerCorrosionRefusal as exc:
            if exc.reason == "engine_unavailable":
                pytest.skip("MAGEMin is required for the anorthite anchor")
            raise
        assert solid <= LIQUIDUS_SOLID_EPSILON
        assert liquid >= 1.0 - LIQUIDUS_LIQUID_EPSILON
    finally:
        sampler.close()


def test_mixing_closed_form_and_reacted_depth_sensitivity():
    # Five kg Na becomes 5 * M(Na2O)/(2 M(Na)) kg Na2O under the declared
    # oxidation-state assumption. The liner layer weighs rho*A*h = 2 kg.
    na_oxide_kg = 5.0 * (2 * 22.98976928 + 15.999) / (2 * 22.98976928)
    total_kg = 2.0 + na_oxide_kg
    fractions = contamination_mixing_fractions(
        {"Na": 5.0},
        density_kg_m3=2.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    assert fractions["Na"] == pytest.approx(na_oxide_kg / total_kg)
    mixed = contaminated_interface_composition(
        {"SiO2": 60.0, "Al2O3": 40.0},
        {"Na": 5.0},
        density_kg_m3=2.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
    )
    assert mixed["composition_wt_pct"]["Na2O"] == pytest.approx(
        100.0 * na_oxide_kg / total_kg
    )
    assert mixed["composition_wt_pct"]["SiO2"] == pytest.approx(
        100.0 * 1.2 / total_kg
    )
    assert reacted_layer_depths(0.03) == pytest.approx((0.003, 0.03, 0.3))


def test_post_onset_dissolution_ratio_closed_form():
    assert post_onset_dissolution_ratio(0.5) == 1.0


def _phi_from_na2o(composition, _temperature_C, _pressure_bar):
    return min(1.0, max(0.0, (composition.get("Na2O", 0.0) - 5.0) / 20.0))


def _recurrence(*, initial_na=0.0, h=1.0, inert_na=0.2, reactive_na=0.5):
    return corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {"Na": initial_na},
        {"Na": inert_na},
        {"Na": reactive_na},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=h,
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        margin_C=0.0,
        horizon=100,
        sampler=_phi_from_na2o,
    )


def test_onset_is_monotone_in_deposit_and_reacted_depth_and_bounds_ordered():
    deposits = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
    depths = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    for depth in depths:
        counts = [
            _recurrence(initial_na=initial_na, h=depth)[
                "campaigns_to_runaway"
            ]
            for initial_na in deposits
        ]
        assert all(
            left[0] >= right[0]
            for left, right in zip(counts, counts[1:])
        )
        assert all(low <= high for low, high in counts)
    for initial_na in deposits:
        counts = [
            _recurrence(initial_na=initial_na, h=depth)[
                "campaigns_to_runaway"
            ][0]
            for depth in depths
        ]
        assert all(left <= right for left, right in zip(counts, counts[1:]))


def test_recurrence_reduces_to_linear_when_sticking_branches_match():
    result = _recurrence(inert_na=0.5, reactive_na=0.5)
    assert result["campaigns_to_runaway"][0] == result["campaigns_to_runaway"][1]
    assert result["coupled_campaigns_to_runaway"] == result[
        "campaigns_to_runaway"
    ][0]


def test_tier_a_uses_invariant_table_and_refuses_uncovered_fields():
    table = {
        "schema": "liner-invariant-table-v1",
        "invariants": [
            {
                "first_liquid_temperature_C": 1553.0,
                "composition_bounds_wt_pct": {
                    "CaO": [19.0, 21.0],
                    "Al2O3": [35.0, 38.0],
                    "SiO2": [42.0, 45.0],
                },
            }
        ],
    }
    composition = {"CaO": 20.0, "Al2O3": 36.0, "SiO2": 44.0}
    assert tier_a_first_liquid_onset(
        table,
        composition,
        service_temperature_C=1600.0,
        margin_C=0.0,
    )["onset"]
    with pytest.raises(LinerCorrosionRefusal) as refused:
        tier_a_first_liquid_onset(
            {"invariants": []},
            composition,
            service_temperature_C=1600.0,
            margin_C=0.0,
        )
    assert refused.value.reason == "liner_invariant_table_missing"


def test_tier_a_invariant_signal_runs_the_campaign_recurrence_without_engine_pressure():
    table = {
        "invariants": [
            {
                "first_liquid_temperature_C": 2000.0,
                "composition_bounds_wt_pct": {"Na2O": [0.0, 9.9]},
            },
            {
                "first_liquid_temperature_C": 1400.0,
                "composition_bounds_wt_pct": {"Na2O": [9.9, 100.0]},
            },
        ]
    }
    result = corrosion_campaign_recurrence(
        {"SiO2": 50.0, "Al2O3": 30.0, "CaO": 20.0},
        {},
        {"Na": 0.1},
        {"Na": 0.2},
        density_kg_m3=1.0,
        area_m2=1.0,
        reacted_layer_depth_m=1.0,
        temperature_C=1500.0,
        pressure_bar=None,
        margin_C=0.0,
        horizon=5,
        sampler=TierAInvariantSampler(table),
    )
    assert result["campaigns_to_runaway"] == [1, 1]
    assert result["criterion_signal_at_horizon_or_onset"]["reactive_upper_bound"] == 1.0


def test_missing_composition_porosity_and_rump_artifact_are_typed():
    with pytest.raises(InterfaceChemistryRefusal) as missing:
        load_interface_chemistry(
            "missing",
            catalog={"missing": {"interface_chemistry": {"citation": "test"}}},
        )
    assert missing.value.reason == "liner_composition_missing"
    with pytest.raises(InterfaceChemistryRefusal) as generated:
        load_interface_chemistry(
            "rump",
            catalog={
                "rump": {
                    "interface_chemistry": {
                        "composition_file": "data/generated/not-created.yaml",
                        "missing_composition_reason": "rump_composition_not_generated",
                        "citation": "generated terminal rump report",
                    }
                }
            },
        )
    assert generated.value.reason == "rump_composition_not_generated"
    with pytest.raises(InterfaceChemistryRefusal) as porosity:
        require_interface_porosity({})
    assert porosity.value.reason == "liner_porosity_missing"

    with pytest.raises(InterfaceChemistryRefusal) as missing_table:
        load_interface_invariant_table(
            "tier_a",
            catalog={
                "tier_a": {
                    "interface_chemistry": {
                        "composition": {
                            "CaO": 20.0,
                            "Al2O3": 36.0,
                            "SiO2": 44.0,
                        },
                        "citation": "test invariant source",
                    }
                }
            },
        )
    assert missing_table.value.reason == "liner_invariant_table_missing"


def test_siO2_free_liner_refuses_before_magemin_returns_ok():
    backend = _FakeMageminBackend()
    with pytest.raises(LinerCorrosionRefusal) as refused:
        MageminLinerSampler(backend)({"CaO": 60.0, "Al2O3": 40.0}, 1500.0, 1.0e-6)
    assert refused.value.reason == "liner_first_melt_engine_out_of_domain"
    assert backend.calls == []


def test_hot_wall_na_alpha_one_does_not_change_equilibrium_pressure():
    from simulator.condensation import _hkl_surface_deposition_flux_mol_m2_s
    from simulator.yaml_cache import load_cached_safe_yaml

    root = Path(__file__).resolve().parents[1]
    vapor_pressure_data = load_cached_safe_yaml(
        (root / "data" / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    flux = _hkl_surface_deposition_flux_mol_m2_s(
        "Na",
        P_local_pa=10_000.0,
        T_surface_K=1500.0 + CELSIUS_TO_KELVIN_OFFSET,
        alpha_s=1.0,
        vapor_pressure_data=vapor_pressure_data,
    )
    assert flux == 0.0

