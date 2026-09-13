"""b-491: pO2 floor inversion is a typed notice, not a silent metal pressure."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON,
    MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
    melt_dissociation_pO2_floor_inversion_notice,
    physical_melt_dissociation_pO2_bar,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.environment import (
    ASTEROID_VACUUM_FLOOR_BAR,
    DEFAULT_VACUUM_FLOOR_BAR,
)
from simulator.grind_preflight import _is_noncertifying_vapor_extrapolation
from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    MELT_DISSOCIATION_PO2_MAX_BAR,
    MELT_DISSOCIATION_PO2_MIN_BAR,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ACCOUNT = {"FeO": 0.30, "MgO": 0.20, "SiO2": 0.50, "Na2O": 0.02}
_T_K = 1700.0


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _request(*, pO2_bar: float, intrinsic_fO2_log: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(_ACCOUNT)},
            species_formula_registry={},
        ),
        temperature_C=_T_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": max(pO2_bar, DEFAULT_VACUUM_FLOOR_BAR),
            "intrinsic_fO2_log": intrinsic_fO2_log,
            "vacuum_floor_bar": DEFAULT_VACUUM_FLOOR_BAR,
        },
    )


def test_helper_flags_floor_and_skips_positive_exponent() -> None:
    notice = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=7.13e9,
        pO2_bar_used=MELT_DISSOCIATION_PO2_MIN_BAR,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=-400.0,
        was_clamped=True,
    )
    assert notice is not None
    assert notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"]["pO2_bar"] == (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )
    assert notice["was_clamped"] is True

    suppressed = melt_dissociation_pO2_floor_inversion_notice(
        species="AlO2",
        pressure_Pa=1e-20,
        pO2_bar_used=MELT_DISSOCIATION_PO2_MIN_BAR,
        pO2_exponent=0.25,
        pressure_rail="standard_reaction_term",
        fO2_log=-400.0,
        was_clamped=True,
    )
    assert suppressed is None

    in_band = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=1e-12,
        pO2_bar_used=ASTEROID_VACUUM_FLOOR_BAR,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=math.log10(ASTEROID_VACUUM_FLOOR_BAR),
        was_clamped=False,
    )
    assert in_band is None


def test_exact_log10_min_is_a_floor_even_when_not_clamped() -> None:
    pO2, clamped = physical_melt_dissociation_pO2_bar(-30.0)
    assert clamped is False
    assert pO2 <= MELT_DISSOCIATION_PO2_MIN_BAR
    notice = melt_dissociation_pO2_floor_inversion_notice(
        species="Si",
        pressure_Pa=7.13e9,
        pO2_bar_used=pO2,
        pO2_exponent=-1.0,
        pressure_rail="liquid_oxide_standard_reaction",
        fO2_log=-30.0,
        was_clamped=clamped,
    )
    assert notice is not None
    assert notice["was_clamped"] is False


def test_provider_flags_floor_inversion_and_completes() -> None:
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-400.0))
    diagnostic = result.diagnostic

    assert result.status == "ok"
    assert diagnostic["melt_dissociation_pO2_clamped_to_physical_envelope"] is True
    notices = diagnostic["pO2_floor_inversion_notices_by_species"]
    pressures = diagnostic["vapor_pressures_Pa"]
    sources = diagnostic["vapor_pressures_source"]
    provenance = diagnostic["vapor_pressure_numerator_provenance"]

    assert "Si" in notices
    assert "Mg" in notices
    assert "Na" in notices
    assert "Fe" not in notices
    assert "SiO" not in notices

    for species in ("Si", "Mg", "Na"):
        notice = notices[species]
        assert notice["reason"] == MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON
        assert notice["authority_level"] == "extrapolated"
        assert notice["certified_band"]["pO2_bar"] == (
            MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
            MELT_DISSOCIATION_PO2_MAX_BAR,
        )
        assert notice["was_clamped"] is True
        assert pressures[species] == pytest.approx(notice["pressure_Pa"])
        assert pressures[species] > 0.0
        assert provenance[species]["floor_inversion_notice"] == notice
        assert MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON in sources[species]
        assert _is_noncertifying_vapor_extrapolation(species, sources[species])

    assert pressures["Si"] >= CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert any(
        MELT_DISSOCIATION_PO2_FLOOR_INVERSION_REASON in str(item)
        for item in result.warnings
    )


def test_provider_flags_exact_minus_30_without_clamp_warning() -> None:
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(_request(pO2_bar=1e-30, intrinsic_fO2_log=-30.0))
    diagnostic = result.diagnostic

    assert result.status == "ok"
    assert diagnostic["melt_dissociation_pO2_clamped_to_physical_envelope"] is False
    assert "Si" in diagnostic["pO2_floor_inversion_notices_by_species"]
    assert diagnostic["vapor_pressures_Pa"]["Si"] > 0.0
    clamp_warnings = [
        item
        for item in result.warnings
        if "melt_dissociation_pO2_clamped_to_physical_envelope" in str(item)
    ]
    assert clamp_warnings == []


def test_si_mg_na_mass_action_ratio_at_floor_vs_1e9() -> None:
    """Hand mass-action: n_Si=-1 → 21 dex; n_Mg_eff=-0.5 → 10.5 dex; n_Na=-0.25 → 5.25 dex."""

    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    at_ref = provider.dispatch(
        _request(pO2_bar=1e-9, intrinsic_fO2_log=-9.0)
    ).diagnostic["vapor_pressures_Pa"]
    at_floor = provider.dispatch(
        _request(pO2_bar=1e-30, intrinsic_fO2_log=-400.0)
    ).diagnostic["vapor_pressures_Pa"]

    # Premise: p ∝ pO2^n. Algebra: P_floor/P_ref = (1e-30/1e-9)^n.
    # Unit: bar/bar. Sanity: n=-1 → 1e21; n=-0.5 → 1e10.5; n=-0.25 → 1e5.25.
    assert at_floor["Si"] / at_ref["Si"] == pytest.approx(1e21, rel=1e-9)
    assert at_floor["Mg"] / at_ref["Mg"] == pytest.approx(10 ** 10.5, rel=1e-9)
    assert at_floor["Na"] / at_ref["Na"] == pytest.approx(10 ** 5.25, rel=1e-6)


def test_lowest_catalog_body_floor_does_not_invert() -> None:
    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    result = provider.dispatch(
        _request(
            pO2_bar=ASTEROID_VACUUM_FLOOR_BAR,
            intrinsic_fO2_log=math.log10(ASTEROID_VACUUM_FLOOR_BAR),
        )
    )
    diagnostic = result.diagnostic
    assert result.status == "ok"
    assert diagnostic["pO2_floor_inversion_notices_by_species"] == {}
    assert diagnostic["vapor_pressures_Pa"]["Si"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert diagnostic["vapor_pressures_Pa"]["Mg"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    assert diagnostic["vapor_pressures_Pa"]["Na"] < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
