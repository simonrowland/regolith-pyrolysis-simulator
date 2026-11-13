"""Wall-saturation law: mass action on resolved Gibbs terms, not a melt curve.

The SiO Gibbs ratios are the NASA values quoted in the b-1188 design at
1323.15 K (NG-1135, NG-1858, NG-1864, NG-0937). Expected pressures are those
published values, and the mass-action identity uses
``reaction_equilibrium_constant`` on the same rows.
"""

from __future__ import annotations

import math

import pytest

from simulator.vapour_rail.nasa_cea import (
    WALL_STANDARD_PRESSURE_PA,
    WallCondensedTerm,
    WallSaturationLawRefusal,
    WallSaturationReaction,
    operative_wall_saturation,
    reaction_equilibrium_constant,
    wall_saturation_pressure_pa,
)

# NASA G°/(RT) at 1323.15 K, design section 4.1.
_T_K = 1323.15
_G_SIO = -37.23464072
_G_SI = -4.28282613
_G_SIO2 = -92.94099129
_G_O2 = -27.38464409
_P_STD = WALL_STANDARD_PRESSURE_PA


def _sio_disp() -> WallSaturationReaction:
    return WallSaturationReaction(
        reaction_id="disproportionation",
        nu_vapour=2.0,
        g_vapour=_G_SIO,
        condensed=(
            WallCondensedTerm("Si", 1.0, _G_SI),
            WallCondensedTerm("SiO2", 1.0, _G_SIO2),
        ),
    )


def _sio_ox() -> WallSaturationReaction:
    return WallSaturationReaction(
        reaction_id="oxidative",
        nu_vapour=1.0,
        g_vapour=_G_SIO,
        condensed=(WallCondensedTerm("SiO2", 1.0, _G_SIO2),),
        nu_O2=0.5,
        g_oxygen=_G_O2,
    )


def _mass_action_residual(
    reaction: WallSaturationReaction,
    pressure_Pa: float,
    pO2_Pa: float | None,
) -> float:
    """(p_v/P°)^ν_v · (pO2/P°)^ν_O2 · K − 1, with K from the K owner."""

    terms: list[tuple[float, float]] = [(-reaction.nu_vapour, _G_SIO)]
    for product in reaction.condensed:
        terms.append((product.stoichiometry, float(product.g_over_RT)))
    if reaction.nu_O2 != 0.0:
        terms.append((-reaction.nu_O2, _G_O2))
    equilibrium_K = reaction_equilibrium_constant(terms, T_K=_T_K)
    ratio = (pressure_Pa / _P_STD) ** reaction.nu_vapour
    if reaction.nu_O2 != 0.0:
        assert pO2_Pa is not None and pO2_Pa > 0.0
        ratio *= (pO2_Pa / _P_STD) ** reaction.nu_O2
    return ratio * equilibrium_K - 1.0


def test_sio_disproportionation_matches_published_nasa_pressure() -> None:
    pressure_Pa = wall_saturation_pressure_pa(_sio_disp(), T_K=_T_K)
    assert pressure_Pa == pytest.approx(1.14529, rel=1e-5)
    assert _mass_action_residual(_sio_disp(), pressure_Pa, None) == pytest.approx(
        0.0, abs=1e-9
    )


def test_sio_oxidative_pressures_and_crossover() -> None:
    p_floor_Pa = 1e-9 * _P_STD
    p_parcel_Pa = 94.0
    at_floor = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=p_floor_Pa
    )
    at_parcel = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=p_parcel_Pa
    )
    assert at_floor == pytest.approx(1.793e-9, rel=1e-3)
    assert at_parcel == pytest.approx(1.849e-12, rel=1e-3)
    assert _mass_action_residual(_sio_ox(), at_floor, p_floor_Pa) == pytest.approx(
        0.0, abs=1e-6
    )

    # Below the Si/SiO2 buffer, disproportionation is the lower Psat.
    # The published crossover is 2.450e-27 bar.
    p_cross_Pa = 2.450e-27 * _P_STD
    below = operative_wall_saturation(
        (_sio_disp(), _sio_ox()),
        T_K=_T_K,
        pO2_Pa=p_cross_Pa / 10.0,
    )
    above = operative_wall_saturation(
        (_sio_disp(), _sio_ox()),
        T_K=_T_K,
        pO2_Pa=p_cross_Pa * 10.0,
    )
    assert below.reaction_id == "disproportionation"
    assert below.products_mol_per_mol_vapour == (("Si", 0.5), ("SiO2", 0.5))
    assert below.o2_mol_per_mol_vapour == 0.0
    assert above.reaction_id == "oxidative"
    assert above.products_mol_per_mol_vapour == (("SiO2", 1.0),)
    assert above.o2_mol_per_mol_vapour == pytest.approx(0.5)


def test_missing_po2_refuses_even_when_a_zero_oxygen_reaction_exists() -> None:
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        operative_wall_saturation((_sio_disp(), _sio_ox()), T_K=_T_K, pO2_Pa=None)
    assert exc_info.value.reason == "wall_pO2_required"
    # The disproportionation reaction on its own does not read pO2.
    alone = operative_wall_saturation((_sio_disp(),), T_K=_T_K, pO2_Pa=None)
    assert alone.pressure_Pa == pytest.approx(1.14529, rel=1e-5)


def test_po2_limits_match_the_signed_oxygen_coefficient() -> None:
    oxidative_at_zero = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=0.0
    )
    assert math.isinf(oxidative_at_zero)
    reductive = WallSaturationReaction(
        reaction_id="reductive",
        nu_vapour=1.0,
        g_vapour=_G_SIO,
        condensed=(WallCondensedTerm("SiO2", 1.0, _G_SIO2),),
        nu_O2=-0.25,
        g_oxygen=_G_O2,
    )
    assert wall_saturation_pressure_pa(reductive, T_K=_T_K, pO2_Pa=0.0) == 0.0
    high = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=1.0e8
    )
    low = wall_saturation_pressure_pa(
        _sio_ox(), T_K=_T_K, pO2_Pa=1.0e-8
    )
    assert high < low


def test_invalid_po2_is_a_typed_refusal() -> None:
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        wall_saturation_pressure_pa(_sio_ox(), T_K=_T_K, pO2_Pa=-1.0)
    assert exc_info.value.reason == "wall_pO2_invalid"
    with pytest.raises(WallSaturationLawRefusal) as exc_info:
        wall_saturation_pressure_pa(_sio_ox(), T_K=_T_K, pO2_Pa=math.nan)
    assert exc_info.value.reason == "wall_pO2_invalid"


def test_perturbing_one_gibbs_term_moves_psat() -> None:
    baseline = wall_saturation_pressure_pa(_sio_disp(), T_K=_T_K)
    shifted = WallSaturationReaction(
        reaction_id="disproportionation",
        nu_vapour=2.0,
        g_vapour=_G_SIO + 0.1,
        condensed=(
            WallCondensedTerm("Si", 1.0, _G_SI),
            WallCondensedTerm("SiO2", 1.0, _G_SIO2),
        ),
    )
    assert wall_saturation_pressure_pa(shifted, T_K=_T_K) != pytest.approx(baseline)
