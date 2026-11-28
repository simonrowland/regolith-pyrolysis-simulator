"""Melt fO2 is bounded below by condensed-metal saturation (d-469).

The Fe number is ``fe_metal_saturation_log10_fO2_bar``. The Si number
is the Ellingham owner's ΔG° on the ruling's formation line, not a
copy of ``metal_saturation_floor``. Pure-Si saturation is the crystal
Antoine in ``data/vapor_pressures.yaml`` evaluated by
``_antoine_log10_pressure``.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.antoine import _antoine_log10_pressure
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.ellingham_thermo import (
    _ellingham_oxide_activity_reference,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_segment_for_temperature,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import fe_metal_saturation_log10_fO2_bar
from simulator.metal_saturation_floor import (
    _oxide_liquid_standard_log10_factor_per_oxide,
    apply_melt_metal_saturation_floor,
    melt_metal_saturation_floor_log10_fO2_bar,
)
from simulator.oxygen_buffers import feo_iw_log10_fO2_bar
from simulator.physical_constants import GAS_CONSTANT, J_PER_KJ
from simulator.vapour_rail.catalog import vapor_pressure_legacy_view


_T_K = 1423.15
_A_SIO2 = 0.41
_DATA = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
# Post-shuttle melt: SiO2 is still a major oxide, FeO is gone, and Cr,
# Ti, Mn, Ni, and P are absent so the Si couple is the one that binds.
# Al, Ca, Mg, and Na are in the melt and are not saturation couples at
# this temperature (Mg and Na are gas on the Ellingham owner).
_SHUTTLE_MOL = {
    "SiO2": 7.5,
    "Al2O3": 1.5,
    "CaO": 2.2,
    "MgO": 2.5,
    "Na2O": 1.3,
}


def _si_line_from_ellingham(temperature_K: float, a_sio2: float) -> float:
    """Ruling's Si line from the Ellingham owner's ΔG°, not the floor body."""

    delta_g_j = ellingham_delta_g_kj_per_mol_o2("Si", temperature_K) * J_PER_KJ
    return (
        delta_g_j / (GAS_CONSTANT * temperature_K * math.log(10.0))
        + math.log10(a_sio2)
    )


def _si_liquid_standard_line(temperature_K: float, a_sio2: float) -> float:
    """Convert crystalline Si Ellingham K to the melt's liquid activity basis."""

    return _si_line_from_ellingham(temperature_K, a_sio2) + (
        _oxide_liquid_standard_log10_factor_per_oxide(
            "SiO2(II)", temperature_K
        )
    )


def test_fe_floor_binds_while_feo_is_present() -> None:
    floor, metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"FeO": 0.45, "SiO2": _A_SIO2},
    )
    assert metal == "Fe"
    assert floor == pytest.approx(
        fe_metal_saturation_log10_fO2_bar(_T_K, a_feo_stoich=0.45),
    )
    assert floor > _si_line_from_ellingham(_T_K, _A_SIO2)


def test_si_floor_binds_once_feo_activity_collapses() -> None:
    floor, metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"FeO": 1.0e-6, "SiO2": _A_SIO2},
    )
    assert metal == "Si"
    assert floor == pytest.approx(_si_liquid_standard_line(_T_K, _A_SIO2))
    assert floor > fe_metal_saturation_log10_fO2_bar(
        _T_K,
        a_feo_stoich=1.0e-6,
    )
    # 1423.15 K is the solid Si / SiO2(II) segment, 11.8 dex below IW,
    # wider than the liquid-line 9–10 dex claim.
    pure, pure_metal = melt_metal_saturation_floor_log10_fO2_bar(
        _T_K,
        {"SiO2": 1.0},
    )
    assert pure_metal == "Si"
    assert pure == pytest.approx(_si_liquid_standard_line(_T_K, 1.0))
    assert pure < feo_iw_log10_fO2_bar(_T_K) - 11.0
    # 1800 K is Si(l) + O2 -> SiO2(l), about 8.4 dex below IW.
    liquid, liquid_metal = melt_metal_saturation_floor_log10_fO2_bar(
        1800.0,
        {"SiO2": 1.0},
    )
    assert liquid_metal == "Si"
    iw_1800 = feo_iw_log10_fO2_bar(1800.0)
    assert iw_1800 - 10.0 < liquid < iw_1800 - 8.0


def test_cr_floor_converts_crystalline_k_to_liquid_activity_basis() -> None:
    temperature_K = 2156.15
    activity = 0.5
    segment = ellingham_segment_for_temperature('Cr', temperature_K)
    delta_g_j_per_mol = (
        ellingham_delta_g_kj_per_mol_o2('Cr', temperature_K) * J_PER_KJ
    )
    crystal_standard_floor = (
        delta_g_j_per_mol
        / (GAS_CONSTANT * temperature_K * math.log(10.0))
        + float(segment.n_ox) * math.log10(activity)
    )
    floor, metal = melt_metal_saturation_floor_log10_fO2_bar(
        temperature_K,
        {'Cr2O3': activity},
    )
    liquid_standard_factor = _oxide_liquid_standard_log10_factor_per_oxide(
        'Cr2O3', temperature_K
    )

    assert metal == 'Cr'
    assert floor == pytest.approx(
        crystal_standard_floor + float(segment.n_ox) * liquid_standard_factor
    )
    # Two Cr2O3 units enter the Ellingham reaction per three O2, so the
    # liquid-standard correction is 2/3 of the per-formula fusion factor.
    assert floor - crystal_standard_floor == pytest.approx(0.350, abs=0.001)


@pytest.mark.parametrize(
    ("temperature_K", "activity", "metal"),
    [
        # Ni's Ellingham row ends at 2000 K; stay inside it to exercise its
        # missing fusion-pair conversion instead of an absent floor.
        (1800.0, {"NiO": 1.0}, "Ni"),
        (2156.15, {"MnO": 1.0}, "Mn"),
    ],
)
def test_ungrounded_binding_floor_publishes_liquid_standard_caveat(
    temperature_K, activity, metal
):
    floor, notice = apply_melt_metal_saturation_floor(
        -100.0,
        temperature_K,
        activity,
    )

    assert notice is not None
    assert notice["metal"] == metal
    caveat = notice["engine_model_caveat"]
    assert caveat["type"] == "typed_notice"
    assert caveat["kind"] == "engine_model_caveat"
    assert caveat["floor_log10_fO2_bar"] == pytest.approx(floor)
    assert caveat["conversion_applied"] is False
    assert f"no JANAF fusion table pair for {metal}O" in caveat["reason"]


def test_mn_reconstructed_marker_and_floor_caveat_at_2058k() -> None:
    segment = ellingham_segment_for_temperature("Mn", 2058.0)
    reference = _ellingham_oxide_activity_reference("Mn", segment)

    assert segment.range_K[0] == pytest.approx(2058.0)
    assert reference.reconstructed_upper_mn is True
    assert reference.source_phase_marker == "MnO(lit. liquid range)"

    floor, notice = apply_melt_metal_saturation_floor(
        -100.0,
        2058.0,
        {"MnO": 1.0},
    )

    assert floor > -100.0
    assert notice is not None
    assert notice["metal"] == "Mn"
    caveat = notice["engine_model_caveat"]
    assert caveat["K_reference_phase"] == segment.phase_basis
    assert "at and above 2058 K" in caveat["reason"]
    assert caveat["floor_log10_fO2_bar"] == pytest.approx(floor)


def test_floor_does_not_catch_unrelated_janaf_value_error(monkeypatch):
    import simulator.metal_saturation_floor as floor_module

    def malformed_metadata(*_args, **_kwargs):
        raise ValueError("JANAF source hash mismatch")

    monkeypatch.setattr(
        floor_module,
        "_oxide_liquid_standard_log10_factor_per_oxide",
        malformed_metadata,
    )
    with pytest.raises(ValueError, match="source hash mismatch"):
        apply_melt_metal_saturation_floor(
            -100.0,
            1800.0,
            {"NiO": 1.0},
        )


def _crystal_si_saturation_pa(temperature_K: float) -> float:
    raw = yaml.safe_load(_DATA.read_text())
    # Schema v2 nests the Antoine under the Si family. The legacy
    # projection is the view ``wall_condensation_antoine_coefficients``
    # and the builtin provider both read.
    species = vapor_pressure_legacy_view(raw)["metals"]["Si"]
    segments = species["pure_component_antoine"]["segments"]
    crystal = next(row for row in segments if row["name"] == "crystal_janaf")
    low_K, high_K = crystal["valid_range_K"]
    assert low_K <= temperature_K <= high_K
    log10_p_pa = _antoine_log10_pressure(
        float(crystal["A"]),
        float(crystal["B"]),
        float(crystal["C"]),
        temperature_K,
    )
    return 10.0 ** log10_p_pa


def test_post_shuttle_si_pressure_stays_under_pure_si_saturation() -> None:
    raw = yaml.safe_load(_DATA.read_text())
    provider = BuiltinVaporPressureProvider(raw)
    result = provider.dispatch(IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(_SHUTTLE_MOL)},
            species_formula_registry={},
        ),
        temperature_C=_T_K - 273.15,
        pressure_bar=1.0e-6,
        control_inputs={
            "pO2_bar": 1.0e-8,
            "intrinsic_fO2_log": -40.0,
            "high_t_melt_activity": "constant_gamma",
        },
    ))
    assert str(result.status) == "ok"
    notice = result.diagnostic["melt_metal_saturation_floor_notice"]
    assert notice["type"] == "typed_notice"
    assert notice["kind"] == "melt_fO2_metal_saturation_floor:Si"
    assert notice["metal"] == "Si"
    assert notice["temperature_K"] == pytest.approx(_T_K)
    assert notice["unfloored_log10_fO2_bar"] == pytest.approx(-40.0)
    assert notice["floor_log10_fO2_bar"] > -30.0
    assert result.diagnostic[
        "melt_dissociation_pO2_clamped_to_physical_envelope"
    ] is False
    dissociation_pO2_bar = result.diagnostic["source_reaction_fO2_bar"]
    assert dissociation_pO2_bar == pytest.approx(
        10.0 ** notice["floor_log10_fO2_bar"],
    )
    assert dissociation_pO2_bar > 1.0e-30
    # The dissociation log is the liquid-standard-converted Ellingham Si
    # line at the same SiO2 activity the vapour path just published. For
    # SiO2 the formula activity is that value (one cation).
    a_sio2 = float(result.diagnostic["activities"]["Si"])
    assert notice["floor_log10_fO2_bar"] == pytest.approx(
        _si_liquid_standard_line(_T_K, a_sio2),
    )
    # a_Si = 1 on the buffer, so the corollary is P_Si <= P_sat,Si.
    # The published channel is the CEA reaction SiO2(l) -> Si(g) + O2,
    # not the JANAF crystal Antoine. At this floor those owners differ
    # by 0.137 dex: measured P_Si / P_sat = 1.371 at both a_SiO2 = 1
    # and a_SiO2 ≈ 0.42, because a_SiO2 cancels. No second pressure
    # cap is applied. The 1e-30 clamp published 65.3 Pa on this
    # composition. Both bounds below reject that, and they go red
    # when the Si couple is removed.
    p_si_pa = result.diagnostic["vapor_pressures_Pa"]["Si"]
    p_sat_pa = _crystal_si_saturation_pa(_T_K)
    assert p_si_pa < 1.0
    assert p_si_pa / p_sat_pa < 10.0
    assert any(
        str(warning).startswith("melt_fO2_metal_saturation_floor:Si")
        for warning in result.warnings
    )
