"""One surface oxygen potential for both gases of an element (ruling d-099).

MO(g) ⇌ M(g) + ½O2 at the evaporating surface is
p(M) / p(MO) = K / sqrt(pO2_surface). K is the production thermo evaluated
at that one potential. A second reservoir must not enter the mass action.
A pO2 folded onto the physical envelope leaves a receipt that names the raw
value. A channel potential whose plane is not the term's plane is refused.
"""

from __future__ import annotations

import math

import pytest

from simulator.config import load_config_bundle
from simulator.physical_constants import MELT_DISSOCIATION_PO2_MAX_BAR
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
    FluxActivationContext,
    PressureRefusal,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.vapour_rail.channels import (
    REACTION_PLANE_MELT_INTERFACE,
    REACTION_PLANE_TRANSPORT_HEADSPACE,
    ChannelEvaluationError,
    channel_linear_mass_action_factor,
    channel_log10_contribution,
    compile_o2_channel_term,
    o2_potential_from_pO2_bar,
)
from simulator.vapour_rail.request import VapourResolveState

_CLOSED = "closed"
_IMPOSED = "imposed"

_PAIRS = (("Cs", "CsO"), ("Rb", "RbO"), ("Ge", "GeO"))
_PARENTS = {"Cs2O": 1.0, "Rb2O": 1.0, "GeO2": 1.0}
_TEMPERATURE_K = 1523.15
# Two in-envelope surfaces. The other reservoir differs, so a split input
# cannot accidentally equal the single-plane equilibrium.
_SURFACES_BAR = (1.0e-9, 1.0e-4)
_DISTRACTOR_BAR = 1.0e-6


@pytest.fixture(scope="module")
def production_catalog():
    payload = load_config_bundle().vapor_pressures.catalog_payload
    return compile_vapour_rail_catalog(payload)


def _pressure_pa(answer, species_id: str) -> float:
    assert not answer.is_refused, (
        f"{species_id} refused: {answer.refusal_code} "
        f"{answer.extra.get('detail')}"
    )
    assert not isinstance(answer.pressure, PressureRefusal)
    return float(answer.pressure.pa)


def _resolved_ratio(catalog, *, mode: str, surface_bar: float) -> dict[tuple[str, str], float]:
    if mode == _CLOSED:
        intrinsic, imposed = surface_bar, _DISTRACTOR_BAR
    else:
        intrinsic, imposed = _DISTRACTOR_BAR, surface_bar
    batch = catalog.resolve_batch(
        {"process.cleaned_melt": dict(_PARENTS)},
        VapourResolveState(
            temperature_K=_TEMPERATURE_K,
            process_phase="hot_train",
            fO2_bar=imposed,
            source_reaction_fO2_bar=intrinsic,
            extras={"oxygen_potential_mode": mode},
        ),
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )
    ratios = {}
    for metal, oxide in _PAIRS:
        metal_pa = _pressure_pa(batch.channel(metal), metal)
        oxide_pa = _pressure_pa(batch.channel(oxide), oxide)
        ratios[(metal, oxide)] = metal_pa / oxide_pa
    return ratios


def _thermo_ratio(catalog, *, surface_bar: float) -> dict[tuple[str, str], float]:
    """Production equilibrium constant at one potential, activity cancelled.

    Both gases of a pair share a parent and an activity exponent, so
    p(M) / p(MO) at one pO2 is K / sqrt(pO2) with that activity removed.
    """

    ratios = {}
    for metal, oxide in _PAIRS:
        metal_ev = catalog.species[metal].evaluator
        oxide_ev = catalog.species[oxide].evaluator
        assert metal_ev.activity_exponent == pytest.approx(
            oxide_ev.activity_exponent
        )
        assert metal_ev.pO2_exponent - oxide_ev.pO2_exponent == pytest.approx(
            -0.5
        )
        metal_pa = metal_ev.evaluate(
            _TEMPERATURE_K,
            source_activity=1.0,
            pO2_bar=surface_bar,
        ).pressure_pa
        oxide_pa = oxide_ev.evaluate(
            _TEMPERATURE_K,
            source_activity=1.0,
            pO2_bar=surface_bar,
        ).pressure_pa
        ratios[(metal, oxide)] = metal_pa / oxide_pa
    return ratios


@pytest.mark.parametrize("mode", [_CLOSED, _IMPOSED])
@pytest.mark.parametrize("surface_bar", _SURFACES_BAR)
def test_sibling_gases_share_one_surface_oxygen_potential(
    production_catalog,
    mode: str,
    surface_bar: float,
) -> None:
    assert surface_bar != _DISTRACTOR_BAR
    expected = _thermo_ratio(production_catalog, surface_bar=surface_bar)
    resolved = _resolved_ratio(
        production_catalog,
        mode=mode,
        surface_bar=surface_bar,
    )
    for pair in _PAIRS:
        assert resolved[pair] == pytest.approx(expected[pair], rel=1.0e-9)


def test_two_surfaces_follow_the_half_oxygen_equilibrium(production_catalog) -> None:
    low, high = _SURFACES_BAR
    low_ratio = _resolved_ratio(
        production_catalog,
        mode=_CLOSED,
        surface_bar=low,
    )
    high_ratio = _resolved_ratio(
        production_catalog,
        mode=_CLOSED,
        surface_bar=high,
    )
    scale = math.sqrt(high / low)
    for pair in _PAIRS:
        assert low_ratio[pair] / high_ratio[pair] == pytest.approx(scale, rel=1.0e-9)


def test_ceiling_fold_receipt_names_the_raw_po2(production_catalog) -> None:
    raw = 1484.085485991506
    evaluation = production_catalog.species["CsO"].evaluator.evaluate(
        _TEMPERATURE_K,
        source_activity=1.0,
        pO2_bar=raw,
    )
    notice = evaluation.extrapolation_notice
    assert notice is not None
    assert notice["was_clamped"] is True
    assert notice["pO2_bar_input"] == pytest.approx(raw)
    assert notice["pO2_bar_used"] == pytest.approx(MELT_DISSOCIATION_PO2_MAX_BAR)


def test_channel_refuses_a_potential_on_the_wrong_plane() -> None:
    term = compile_o2_channel_term(
        signed_nu_o2=0.25,
        target_nu=1.0,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
    )
    potential = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-8,
        temperature_K=_TEMPERATURE_K,
        reaction_plane=REACTION_PLANE_TRANSPORT_HEADSPACE,
    )
    with pytest.raises(ChannelEvaluationError):
        channel_log10_contribution(term, potential)
    with pytest.raises(ChannelEvaluationError):
        channel_linear_mass_action_factor(term, potential)

    same_plane = o2_potential_from_pO2_bar(
        pO2_bar=1.0e-8,
        temperature_K=_TEMPERATURE_K,
        reaction_plane=REACTION_PLANE_MELT_INTERFACE,
        pO2_reference_bar=1.0,
    )
    assert channel_log10_contribution(term, same_plane) == pytest.approx(
        -0.25 * math.log10(1.0e-8)
    )
