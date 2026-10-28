"""JANAF Na-013 regressions for b-959; no golden rebaselining."""

import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from simulator.chemistry.ellingham_thermo import (
    _na_activity_reference_standard_delta_g_kj_per_mol_o2,
    ELLINGHAM_OUTSIDE_SEGMENT_REASON,
    ellingham_authority_limit,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_segments,
    ellingham_same_phase_extension,
)


@pytest.mark.parametrize(
    ("temperature_K", "expected_kj_per_mol_o2"),
    [
        # Chase 1998, JANAF Na-013, formation Gibbs column, kJ/mol Na2O:
        # 1400 K: -192.041; 1500 K: -167.596; 1600 K: -143.448.
        # Two Na2O form per O2. At 1423.15 K (1150 C), linear interpolation
        # of the printed 1400/1500 K rows, then doubling, gives -372.763965.
        (1423.15, -372.763965),
        (1405.2, -381.539720),
        (1500.0, -335.192),
        (1600.0, -286.896),
        (1800.0, -191.948),
        (2000.0, -98.976),
        (2200.0, -7.772),
        (2600.0, 169.992),
        (2700.0, 213.566),
        (2800.0, 256.820),
        (2900.0, 299.770),
        (3000.0, 342.428),
    ],
)
def test_liquid_standard_gibbs_matches_janaf_above_melting(
    temperature_K, expected_kj_per_mol_o2
):
    assert _na_activity_reference_standard_delta_g_kj_per_mol_o2(
        temperature_K
    ) == pytest.approx(expected_kj_per_mol_o2, abs=0.001)


def test_liquid_and_alpha_standard_gibbs_coexist_at_melting():
    # JANAF Na-013 and Na-014 both print ALPHA <--> LIQUID at 1405.200 K.
    # At a first-order transition the two chemical potentials coincide.
    alpha = next(
        segment
        for segment in reversed(ellingham_fit_segments("Na"))
        if "Na2O(alpha)" in segment.phase_basis
    )
    assert _na_activity_reference_standard_delta_g_kj_per_mol_o2(
        1405.2
    ) == pytest.approx(alpha.delta_g_kJ_per_mol_O2(1405.2), abs=0.001)


def test_na2o_janaf_fusion_enthalpy_and_alpha_rows():
    segments = ellingham_fit_segments("Na")
    alpha = next(s for s in segments if s.range_K[1] == 1405.2)
    liquid = next(s for s in segments if s.range_K[0] == 1405.2)
    # JANAF Na-013/Na-014 transition enthalpy increments and 1400 K
    # formation H rows give ΔH_fus=47.696 kJ/mol Na2O (see source owner).
    assert (liquid.dH_f_kJ_per_mol_O2 - alpha.dH_f_kJ_per_mol_O2) / 2 == (
        pytest.approx(47.696, abs=1e-9)
    )
    # Na-014 alpha ΔfG column: 1300 K -220.358; 1400 K -192.218 kJ/mol.
    assert ellingham_delta_g_kj_per_mol_o2("Na", 1300) == pytest.approx(
        -440.716, abs=0.001
    )
    assert ellingham_delta_g_kj_per_mol_o2("Na", 1400) == pytest.approx(
        -384.436, abs=0.001
    )
    before_1400 = next(s for s in segments if s.range_K[1] == 1400.0)
    assert before_1400.delta_g_kJ_per_mol_O2(1400.0) == pytest.approx(
        alpha.delta_g_kJ_per_mol_O2(1400.0), abs=1e-9
    )


def test_na_liquid_source_endpoint_and_flagged_extension():
    # Na-013's last printed row is 3000 K. Beyond it the prediction keeps
    # its original reason, authority level and last certified segment band.
    assert ellingham_authority_limit(3000, species="Na", consumer="test") is None
    extension = ellingham_same_phase_extension("Na", 3001)
    assert extension is not None
    assert extension["authority"] == "extrapolated"
    assert extension["segment_range_K"] == (2900.0, 3000.0)
    assert extension["original_refusal_reason"] == ELLINGHAM_OUTSIDE_SEGMENT_REASON
    assert "segment_band_K=2900-3000" in extension["notice_reason"]
    assert math.isfinite(
        _na_activity_reference_standard_delta_g_kj_per_mol_o2(3001)
    )


@pytest.mark.parametrize("temperature_K", [None, 0, -1, math.nan, math.inf])
def test_na_liquid_reference_refuses_missing_or_invalid_temperature(temperature_K):
    with pytest.raises((TypeError, ValueError)):
        _na_activity_reference_standard_delta_g_kj_per_mol_o2(temperature_K)


def test_feo_gate_at_legacy_crossover_proposes_balanced_reduction():
    from engines.builtin.metallothermic_step import BuiltinMetallothermicStepProvider
    from simulator.core import PyrolysisSimulator
    from tests.chemistry.conftest import _atom_check
    from tests.chemistry.test_builtin_metallothermic_step_provider import (
        _c3_na_feo_cleanup_request,
    )

    # Reuse the provider's controlled liquid-fraction request, resolving only
    # its formula registry. This isolates the cited FeO standard-state gate
    # from the external melt backend, which supplies that input in a full run.
    registry = PyrolysisSimulator._load_species_formula_registry()
    request = _c3_na_feo_cleanup_request(
        SimpleNamespace(species_formula_registry=registry), liquid_fraction=0.25
    )
    result = BuiltinMetallothermicStepProvider().dispatch(
        replace(request, temperature_C=1181.5)
    )
    assert result.status == "ok"
    assert result.transition is not None
    assert result.diagnostic["metal_produced_kg"] > 0.0
    _atom_check(result.transition, registry, tol=1e-9)
