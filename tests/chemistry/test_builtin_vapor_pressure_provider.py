"""Tests for the BuiltinVaporPressureProvider — first intent flip of
\\goal BUILTIN-ENGINE-EXTRACTION (#7).

Covers:

* Unit: the provider returns the same vapor pressures as the legacy
  :meth:`EquilibriumMixin._internal_analytical_equilibrium` for a known composition + T.
* Unit: the kernel filter actually scopes the provider's account view to
  the single declared account (``process.cleaned_melt``).
* Unit: capability profile declares ``VAPOR_PRESSURE`` only and is
  authoritative for it.
* Shadow parity: across a multi-step simulation run on lunar + Mars +
  asteroid feedstocks, the legacy ``_internal_analytical_equilibrium`` and the kernel
  dispatch agree species-by-species within 1e-9 Pa (relative + absolute
  floor). This is the parity gate that justified the flip; it stays in
  the suite as a regression guard against future intent flips that touch
  the same call site.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from engines.builtin import vapor_pressure as vapor_pressure_module
from engines.builtin._common import composition_wt_pct_from_account_view
from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG,
    VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS,
    VaporPressureComputationError,
    VaporPressureRangeError,
    _ELLINGHAM_THERMO,
    reconstructed_vapor_pressure_authority_limit,
    require_antoine_source_certified_temperature,
)
from simulator.equilibrium import EquilibriumMixin
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_fit_extrapolation,
    ellingham_fit_range_K,
    ellingham_segment_for_temperature,
)
from simulator.chemistry import ellingham_graph
from simulator.chemistry import melt_activity
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    MELT_OXIDE_IDEAL_ASSERTION_TIER,
    MELT_OXIDE_IDEAL_SOLUTION_MODEL,
    MELT_OXIDE_TABLE_GAMMA_MODEL,
    melt_oxide_activity,
    single_cation_mole_fractions,
    table_gamma_effective,
)
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    IntentRequest,
    ProviderRegistry,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.fe_redox import kress91_ferrous_feo_activity
from simulator.state import CampaignPhase, DecisionType
from tests.chemistry.conftest import _build_sim


_VP_TOLERANCE_REL = 1e-9
_VP_TOLERANCE_ABS_PA = 3e-7
_CA_RANGE_EXTRAPOLATION_T_K = 1713.0

# ---------------------------------------------------------------------------
# Ellingham flat-table provenance pins.
#
# Production ``ELLINGHAM_THERMO`` / ``_ELLINGHAM_THERMO`` is a multi-source
# keying/stoichiometry table. Authoritative dG(T) lives on
# ``ELLINGHAM_FIT_SEGMENTS``. This suite freezes each source group separately
# so (a) an undeclared new row fails the union equality, and (b) each row's
# source is readable at the assert site rather than being pasted into a
# misnamed "JANAF" mirror.
#
# Tuple shape: (dH_f kJ/mol_O2, dS_f kJ/(mol*K)/mol_O2, n_M, n_ox).
# ---------------------------------------------------------------------------

# Pin: v1c high-T linear refit of Chase 1998 NIST-JANAF 4th multiphase rows
# (Na-014, K-012, Fe-020, Cr-014, Mg-008, Ca-027, Al-096, O-043/Ti, Si, Mn-008
# era flat compatibility). Mn's *segmented* dG authority later moved onto a
# Pankratz primary-refit in ELLINGHAM_FIT_SEGMENTS; the flat row here remains
# the legacy Mn(l) keying tuple.
_V1C_JANAF_ELLINGHAM_ROWS: dict[str, tuple[float, float, float, float]] = {
    "Na": (-1135.130, -0.537417, 4, 2),
    "K": (-975.838, -0.520580, 4, 2),
    "Fe": (-538.946, -0.125272, 2, 2),
    "Cr": (-748.076, -0.168676, 4 / 3, 2 / 3),
    "Mg": (-1342.444, -0.336009, 2, 2),
    "Ca": (-1285.155, -0.222295, 2, 2),
    "Al": (-1126.073, -0.218805, 4 / 3, 2 / 3),
    "Ti": (-939.632, -0.177149, 1, 1),
    "Si": (-910.940, -0.182400, 1, 1),
    "Mn": (-794.540, -0.165650, 2, 2),
}

# Pin: t-006 / REF-058 Mah & Pankratz 1976 USBM Bulletin 668 NiO(s) formation
# table (Ni(s,l)+1/2 O2 = NiO(s), per-O2 conversion). Not JANAF; not CEA
# (NiO condensed is absent from the in-repo CEA extract).
_USBM_B668_ELLINGHAM_ROWS: dict[str, tuple[float, float, float, float]] = {
    "Ni": (-465.852, -0.167751, 2, 2),
}

# Pin: t-548 (326cca7) CEA primary-refit flat keying rows. Coefficients are
# the rounded mid-window ELLINGHAM_FIT_SEGMENTS pieces (endpoint-continuous
# linear fits through true NASA-9 CEA ΔG at segment endpoints). Source:
# data/literature/extracts/nasa-cea-thermo.yaml (McBride/Zehe/Gordon NASA
# TP-2002-211556 thermo.inp). Re-derived 2026-08-09 in
# docs-private/research/2026-08-09-b156-ellingham-mirror/.
#   Zr: Zr(b)+O2->ZrO2(II) 1445–2125 K piece
#   P:  (4/5)P(L)+O2->(1/5)P4O10(L) 1100–1650 K piece
#   Rb: 4 Rb(g)+O2->2 Rb2O(L) 1512.5–1650 K piece
#   Cs: 4 Cs(g)+O2->2 Cs2O(L) 1375–1650 K piece
_T548_CEA_PRIMARY_ELLINGHAM_ROWS: dict[str, tuple[float, float, float, float]] = {
    "Zr": (-1080.503, -0.173901, 1, 1),
    "Rb": (-852.249, -0.449638, 4, 2),
    "Cs": (-873.813, -0.458111, 4, 2),
    "P": (-584.140, -0.171105, 4 / 5, 1 / 5),
}

# Union of every declared source pin. Must equal production exactly: a new
# production row without a pin here, or a pin without a production row, fails.
_DECLARED_ELLINGHAM_PROVENANCE: dict[str, tuple[float, float, float, float]] = {
    **_V1C_JANAF_ELLINGHAM_ROWS,
    **_USBM_B668_ELLINGHAM_ROWS,
    **_T548_CEA_PRIMARY_ELLINGHAM_ROWS,
}


def test_ellingham_table_matches_declared_provenance():
    """No row enters production ELLINGHAM_THERMO without a declared, pinned source.

    Groups are frozen per source (v1c JANAF / USBM B668 / t-548 CEA primary).
    Their union must equal the production flat table exactly so provenance
    drift is visible at the assert site and an undeclared row still fails.
    """
    # Groups must be pairwise-disjoint — a species in two pins is a bug in
    # the freeze, not an allowed multi-source claim at this layer.
    janaf_keys = set(_V1C_JANAF_ELLINGHAM_ROWS)
    usbm_keys = set(_USBM_B668_ELLINGHAM_ROWS)
    cea_keys = set(_T548_CEA_PRIMARY_ELLINGHAM_ROWS)
    assert janaf_keys.isdisjoint(usbm_keys)
    assert janaf_keys.isdisjoint(cea_keys)
    assert usbm_keys.isdisjoint(cea_keys)
    assert janaf_keys | usbm_keys | cea_keys == set(_DECLARED_ELLINGHAM_PROVENANCE)

    assert _ELLINGHAM_THERMO == _DECLARED_ELLINGHAM_PROVENANCE
    assert EquilibriumMixin._ELLINGHAM_THERMO == _DECLARED_ELLINGHAM_PROVENANCE


@pytest.mark.parametrize(
    (
        "species",
        "fit_range_max_K",
        "expected_1600C",
        "expected_1800C",
        "expected_1900C",
    ),
    [
        ("Na", 2600.0, -157.82, -65.74, -19.70),
        ("K", 2000.0, -9.06, 84.50, 131.26),
        ("Fe", 2600.0, -310.43, -289.09, -278.42),
        ("Mn", 2600.0, -484.25, -451.12, -434.56),
        ("Cr", 2600.0, -431.82, -397.96, -379.93),
        ("Si", 2600.0, -575.46, -537.21, -518.08),
    ],
)
def test_cf2lite_janaf_mbar_species_match_extended_anchor_window(
    species: str,
    fit_range_max_K: float,
    expected_1600C: float,
    expected_1800C: float,
    expected_1900C: float,
) -> None:
    # CF-2-lite anchors: NIST-JANAF/Chase 1998 table IDs cited in
    # simulator.chemistry.ellingham_thermo. Values are copied here so this is
    # an external-anchor check, not helper self-parity.
    assert ellingham_fit_range_K(species)[1] == pytest.approx(fit_range_max_K)
    assert ellingham_delta_g_kj_per_mol_o2(
        species,
        1600.0 + 273.15,
    ) == pytest.approx(expected_1600C, abs=0.02)
    assert ellingham_delta_g_kj_per_mol_o2(
        species,
        1800.0 + 273.15,
    ) == pytest.approx(expected_1800C, abs=0.02)
    assert ellingham_delta_g_kj_per_mol_o2(
        species,
        1900.0 + 273.15,
    ) == pytest.approx(expected_1900C, abs=0.02)


def test_cf2lite_ellingham_segments_use_phase_correct_high_t_basis() -> None:
    assert "Fe(gamma)" in ellingham_segment_for_temperature("Fe", 1600.0).phase_basis
    assert "Fe(delta)" in ellingham_segment_for_temperature("Fe", 1800.0).phase_basis
    assert "Fe(l)" in ellingham_segment_for_temperature("Fe", 1873.15).phase_basis
    assert "Si(s)" in ellingham_segment_for_temperature("Si", 1600.0).phase_basis
    assert "Si(l)" in ellingham_segment_for_temperature("Si", 1873.15).phase_basis
    assert "Mn(l)" in ellingham_segment_for_temperature("Mn", 1873.15).phase_basis
    assert "Cr(s)" in ellingham_segment_for_temperature("Cr", 2129.0).phase_basis
    assert "Cr(l)" in ellingham_segment_for_temperature("Cr", 2131.0).phase_basis
    assert ellingham_delta_g_kj_per_mol_o2("Cr", 2190.0) == pytest.approx(
        -376.818710,
        abs=1e-6,
    )

    extrapolation = ellingham_fit_extrapolation(
        1000.0,
        species="Mn",
        consumer="test",
    )
    assert extrapolation is not None
    assert extrapolation["authority_flag"] == ELLINGHAM_AUTHORITY_LIMIT_FLAG


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_only_vapor_pressure_intent(vapor_pressure_data):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.VAPOR_PRESSURE})
    assert profile.is_authoritative_for == frozenset(
        {ChemistryIntent.VAPOR_PRESSURE}
    )
    # No other intent is authorised.
    for intent in ChemistryIntent:
        if intent is ChemistryIntent.VAPOR_PRESSURE:
            assert profile.is_authoritative(intent)
        else:
            assert not profile.is_authoritative(intent)


def test_provider_declares_only_cleaned_melt_account(vapor_pressure_data):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    profile = provider.capability_profile()
    assert profile.declared_accounts == frozenset({"process.cleaned_melt"})


def _ca_range_extrapolation_request() -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"CaO": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=_CA_RANGE_EXTRAPOLATION_T_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )


def _mg_vapor_request_at_T_K(temperature_K: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"MgO": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )


def _mn_vapor_request_at_T_K(temperature_K: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"MnO": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )


class _CaOnlyMelt:
    temperature_C = _CA_RANGE_EXTRAPOLATION_T_K - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"CaO": 100.0}


class _MgOnlyMelt:
    def __init__(self, temperature_K: float):
        self.temperature_C = temperature_K - 273.15
        self.p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"MgO": 100.0}


class _MnOnlyMelt:
    temperature_C = 2000.0 - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"MnO": 100.0}


class _MnAboveNbpMelt:
    temperature_C = 2400.0 - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"MnO": 100.0}


class _FeOnlyHighTMelt:
    temperature_C = 1600.0
    p_total_mbar = 1e-3
    melt_fO2_log = -9.0

    def composition_wt_pct(self):
        return {"FeO": 100.0}


class _FeBeyondEllinghamMelt:
    temperature_C = 2250.0 - 273.15
    p_total_mbar = 1e-3
    melt_fO2_log = -9.0

    def composition_wt_pct(self):
        return {"FeO": 100.0}


class _SiOnlyMelt:
    temperature_C = 1900.0 - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"SiO2": 100.0}


def _si_only_vapor_request_at_T_K(temperature_K: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )


def _si_only_transport_redox_request(
    *,
    transport_pO2_bar: float,
    intrinsic_fO2_log: float,
) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=_SiOnlyMelt.temperature_C,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        control_inputs={
            "pO2_bar": transport_pO2_bar,
            "intrinsic_fO2_log": intrinsic_fO2_log,
        },
    )


def _fe_redox_request(
    *,
    species_formula_registry,
    intrinsic_fO2_log: float | None,
    fO2_log: float | None = -9.0,
) -> IntentRequest:
    controls = {"pO2_bar": 1e-9}
    if intrinsic_fO2_log is not None:
        controls["intrinsic_fO2_log"] = intrinsic_fO2_log
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "SiO2": 5.0,
                    "MgO": 1.0,
                    "CaO": 1.0,
                    "Al2O3": 1.0,
                    "FeO": 1.0,
                    "Fe2O3": 0.5,
                }
            },
            species_formula_registry=species_formula_registry,
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=fO2_log,
        control_inputs=controls,
    )


_COMPOSITION_SENSITIVITY_BASE_MOL = {
    "SiO2": 1.0,
    "Al2O3": 0.2,
    "CaO": 0.2,
    "Na2O": 0.05,
    "K2O": 0.05,
    "MgO": 0.4,
    "FeO": 0.3,
}

_DEMARIA_12022_WT_PCT = {
    "SiO2": 44.5,
    "TiO2": 1.5,
    "Al2O3": 13.5,
    "FeO": 16.5,
    "MgO": 9.0,
    "CaO": 11.0,
    "Na2O": 0.36,
    "K2O": 0.068,
    "MnO": 0.20,
    "P2O5": 0.10,
    "Cr2O3": 0.35,
}

_DEMARIA_12022_PO2_TABLE_ATM = {
    1396.0: 5.54e-9,
    1475.0: 4.96e-8,
}


def _demaria_12022_log10_po2_bar(temperature_K: float) -> float:
    """Log-linear interpolation of DeMaria 1971 Table 1 O2 pressures."""

    lo_T, hi_T = sorted(_DEMARIA_12022_PO2_TABLE_ATM)
    lo_log = math.log10(_DEMARIA_12022_PO2_TABLE_ATM[lo_T] * 1.01325)
    hi_log = math.log10(_DEMARIA_12022_PO2_TABLE_ATM[hi_T] * 1.01325)
    return lo_log + (float(temperature_K) - lo_T) * (hi_log - lo_log) / (
        hi_T - lo_T
    )


def _wt_pct_to_mol_account(wt_pct: dict[str, float]) -> dict[str, float]:
    from simulator.state import MOLAR_MASS

    return {
        oxide: float(wt) / MOLAR_MASS[oxide] * 1000.0
        for oxide, wt in wt_pct.items()
        if wt > 0.0
    }


def _composition_sensitivity_request(
    account_mol: dict[str, float],
) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(account_mol)},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-9.0,
        control_inputs={
            "pO2_bar": 1e-3,
            "intrinsic_fO2_log": -9.0,
        },
    )


@pytest.mark.parametrize(
    "oxide,species",
    [
        ("Na2O", "Na"),
        ("K2O", "K"),
        ("MgO", "Mg"),
        ("FeO", "Fe"),
        ("SiO2", "SiO"),
    ],
)
def test_runtime_p_eq_numerator_moves_with_parent_oxide_composition(
    vapor_pressure_data,
    oxide,
    species,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    low_account = dict(_COMPOSITION_SENSITIVITY_BASE_MOL)
    high_account = dict(_COMPOSITION_SENSITIVITY_BASE_MOL)
    low_account[oxide] *= 0.25
    high_account[oxide] *= 4.0

    low_result = provider.dispatch(_composition_sensitivity_request(low_account))
    high_result = provider.dispatch(_composition_sensitivity_request(high_account))
    low_diag = low_result.diagnostic or {}
    high_diag = high_result.diagnostic or {}
    low_p_eq = low_diag["vapor_pressures_Pa"][species]
    high_p_eq = high_diag["vapor_pressures_Pa"][species]
    low_provenance = low_diag["vapor_pressure_numerator_provenance"][species]
    high_provenance = high_diag["vapor_pressure_numerator_provenance"][species]

    assert high_p_eq > low_p_eq
    assert low_provenance["P_eq_Pa"] == pytest.approx(low_p_eq)
    assert high_provenance["P_eq_Pa"] == pytest.approx(high_p_eq)
    assert high_provenance["activity_factor"] > low_provenance["activity_factor"]
    assert high_provenance["pressure_kind"] == "effective_equilibrium"


def test_mg_high_t_uses_gas_fugacity_after_te_gas_rail_demotion(
    vapor_pressure_data,
):
    """b-147: TE gas_rail demoted; high-T Mg uses JANAF gas_fugacity Pref_GF."""
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    account = dict(_COMPOSITION_SENSITIVITY_BASE_MOL)
    request = _composition_sensitivity_request(account)
    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}

    gas_rail = vapor_pressure_data["metals"]["Mg"]["gas_rail_standard_reaction"]
    assert gas_rail.get("status") == "dormant_non_authoritative"
    assert gas_rail.get("authoritative") is False

    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Mg"]
    assert provenance["metal_standard_state"] == "gas"
    assert provenance["pressure_rail"] == "gas_fugacity"
    assert "P_reference_Antoine_Pa" not in provenance
    assert "gas_standard_fugacity" in provenance["source_label"]
    # activity_factor already folds a_oxide * fO2 stoichiometry for gas_fugacity.
    assert provenance["P_eq_Pa"] == pytest.approx(
        provenance["P_standard_Pa"] * provenance["activity_factor"],
        rel=1e-9,
    )
    assert provenance["P_eq_Pa"] > 0.0


def test_ellingham_graph_mg_matches_pref_gf_after_te_gas_rail_demotion(
    vapor_pressure_data,
):
    """b-147: graph path uses Pref_GF; dormant TE Pref_GR must not be selected."""
    temperature_K = 1773.15
    pO2_bar = 1e-3
    a_oxide = 0.5
    import math as _math
    from simulator.chemistry.ellingham_thermo import (
        ellingham_segment_for_temperature,
    )

    # Expected: JANAF gas-metal Pref_GF * a * (pO2/pO2_ref)^n
    seg = ellingham_segment_for_temperature("Mg", temperature_K)
    assert "Mg(g)" in seg.phase_basis
    dG = seg.delta_g_kJ_per_mol_O2(temperature_K)
    R = 8.314462618
    P0 = vapor_pressure_module.ELLINGHAM_STANDARD_PRESSURE_PA
    P_ref_gf = P0 * _math.exp(dG * 1000.0 / (R * temperature_K)) ** (
        1.0 / seg.n_M
    )
    expected = P_ref_gf * (a_oxide ** 1.0) * (pO2_bar / 1.0) ** (-0.5)
    pressure = ellingham_graph.effective_equilibrium_pressure_Pa(
        "Mg",
        temperature_K,
        pO2_bar,
        vapor_pressure_data=vapor_pressure_data,
        a_oxide=a_oxide,
    )
    # rel=1e-5: graph and closed-form Pref_GF share the same segments; residual
    # is float/root path, not Pref_GR class (~0.5 dex).
    assert pressure == pytest.approx(expected, rel=1e-5)

    # Dormant TE Pref_GR path must differ (was low by ~0.54 dex).
    gas_rxn = vapor_pressure_data["metals"]["Mg"]["gas_rail_standard_reaction"]
    antoine = gas_rxn["antoine"]
    P_ref_gr = 10.0 ** (
        float(antoine["A"])
        - float(antoine["B"]) / (temperature_K + float(antoine["C"]))
    )
    gr_path = P_ref_gr * (a_oxide ** 1.0) * (pO2_bar / 1.0) ** (-0.5)
    assert abs(_math.log10(pressure / gr_path)) > 0.3


def test_condensed_basis_ellingham_pressure_uses_raoult_psat(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    account = dict(_COMPOSITION_SENSITIVITY_BASE_MOL)
    request = _composition_sensitivity_request(account)
    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}

    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
    root = provenance["raw_metal_activity_root"]
    assert provenance["metal_standard_state"] == "condensed"
    assert provenance["pressure_rail"] == "condensed_raoult_psat"
    assert provenance["P_eq_Pa"] == pytest.approx(
        min(root, 1.0) * provenance["P_reference_Antoine_Pa"]
    )


def test_neutral_total_pressure_does_not_change_vapor_equilibrium_peq(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    account = _wt_pct_to_mol_account(_DEMARIA_12022_WT_PCT)
    pressure_sweep_bar = (1e-12, 1e-6, 0.005, 0.01, 0.015, 1.0)
    p_eq_by_pressure: dict[float, dict[str, float]] = {}

    for pressure_bar in pressure_sweep_bar:
        request = IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": account},
                species_formula_registry={},
            ),
            temperature_C=1300.0,
            pressure_bar=pressure_bar,
            fO2_log=-9.0,
            control_inputs={"pO2_bar": 1e-9, "intrinsic_fO2_log": -9.0},
        )
        result = provider.dispatch(request)
        vapor_pressures = result.diagnostic["vapor_pressures_Pa"]
        p_eq_by_pressure[pressure_bar] = {
            species: vapor_pressures[species]
            for species in ("Fe", "SiO", "Na")
        }

    # Kress91 pressure terms stay inside pressure-sensitive redox splits;
    # neutral pN2 overhead is transport only and must not perturb
    # equilibrium/activity P_eq.
    reference = p_eq_by_pressure[pressure_sweep_bar[0]]
    for pressure_bar in pressure_sweep_bar[1:]:
        assert p_eq_by_pressure[pressure_bar] == reference


def test_grounded_melt_activity_coefficients_match_single_cation_sources():
    expected = {
        "Na2O": ("NaO0.5", 1.0e-3),
        "K2O": ("KO0.5", 3.5e-5),
        "CaO": ("CaO", 1.2e-2),
        "Al2O3": ("AlO1.5", 0.322),
        "SiO2": ("SiO2", 1.0),
        "TiO2": ("TiO2", 1.60),
        "Cr2O3": ("CrO1.5", 31.1),
        "MgO": ("MgO", 1.0),
        "MnO": ("MnO", 1.90),
    }

    for parent_oxide, (component, gamma) in expected.items():
        coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS[parent_oxide]
        assert coeff.single_cation_component == component
        assert coeff.gamma == pytest.approx(gamma)
        assert "DOI" in coeff.citation
    k_coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS["K2O"]
    assert k_coeff.valid_range_K == (1500.0, 1500.0)
    assert k_coeff.anchor_T_K == pytest.approx(1500.0)
    assert "DeMaria" in k_coeff.citation
    na_coeff = MELT_OXIDE_ACTIVITY_COEFFICIENTS["Na2O"]
    assert na_coeff.valid_range_K == (1673.0, 1673.0)
    assert na_coeff.anchor_T_K == pytest.approx(1673.0)


def test_k_standard_reaction_term_reconstructs_liquid_ko05_source_rows(
    vapor_pressure_data,
):
    row = vapor_pressure_data["metals"]["K"]
    coeff = row["antoine"]
    reaction = row["reaction"]

    assert row["fit_target"] == "standard_reaction_term"
    assert reaction["formula"] == "KO0.5(l) -> K(g) + 0.25 O2(g)"
    assert "Lamoreaux & Hildenbrand 1984 Tables 2/4" in reaction["basis"]
    assert "10.1063/1.555706" in reaction["basis"]
    assert row["oxide_activity_exponent"] == pytest.approx(1.0)
    assert row["pO2_exponent"] == pytest.approx(-0.25)

    for source_row in reaction["source_table_values"]:
        temperature_K = source_row["T_K"]
        fit_log10_p_pa = coeff["A"] - coeff["B"] / (
            temperature_K + coeff["C"]
        )
        assert fit_log10_p_pa == pytest.approx(
            source_row["fit_log10_P_K_ref_Pa"],
            abs=1e-6,
        )
        assert fit_log10_p_pa == pytest.approx(
            source_row["log10_P_K_ref_Pa"],
            abs=0.001,
        )

    assert reaction["fit_residual_dex"]["max_abs"] == pytest.approx(
        0.000223,
        abs=1e-6,
    )
    heldout = reaction["heldout_demaria_comparison"]
    assert heldout[2]["T_K"] == pytest.approx(1428.571429)
    assert heldout[2]["residual_dex_model_minus_demaria"] == pytest.approx(
        1.241499,
        abs=1e-6,
    )


def test_k_wall_condensation_uses_pure_component_sidecar(vapor_pressure_data):
    row = vapor_pressure_data["metals"]["K"]

    runtime_coeff, runtime_block = vapor_pressure_module.vapor_pressure_antoine_coefficients(
        row,
        temperature_K=1429.0,
    )
    wall_coeff, wall_block = vapor_pressure_module.wall_condensation_antoine_coefficients(
        row,
        temperature_K=1429.0,
    )

    assert runtime_block == "antoine"
    assert wall_block == "pure_component_antoine"
    assert runtime_coeff is row["antoine"]
    assert wall_coeff is row["pure_component_antoine"]


def test_cro2_wall_condensation_has_no_pure_component_proxy(
    vapor_pressure_data,
):
    row = vapor_pressure_data["oxide_vapors"]["CrO2"]

    runtime_coeff, runtime_block = vapor_pressure_module.vapor_pressure_antoine_coefficients(
        row,
        temperature_K=1523.15,
    )
    wall_coeff, wall_block = vapor_pressure_module.wall_condensation_antoine_coefficients(
        row,
        temperature_K=1523.15,
    )

    assert runtime_block == "antoine"
    assert runtime_coeff == {}
    assert wall_block == "pure_component_antoine"
    assert wall_coeff == {}


def test_sodium_pure_component_fit_rejects_nonphysical_pole_branch(
    vapor_pressure_data,
):
    row = vapor_pressure_data["metals"]["Na"]
    pure = row["pure_component_antoine"]

    assert 400.0 + pure["C"] < 0.0
    assert vapor_pressure_module._pure_segment_usable(pure, 400.0) is False
    assert vapor_pressure_module._pure_segment_usable(pure, 924.0) is True


@pytest.mark.parametrize("temperature_K", [400.0, 410.0])
def test_sodium_provider_omits_nonphysical_pole_branch(
    vapor_pressure_data,
    temperature_K,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Na2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert "Na" not in result.diagnostic["vapor_pressures_Pa"]


def test_phosphorus_hot_train_scope_survives_compatibility_projection(
    vapor_pressure_data,
):
    oxide_vapors = vapor_pressure_data["oxide_vapors"]
    for carrier in ("PO", "PO2", "P2", "P4", "P4O6", "P4O10"):
        assert oxide_vapors[carrier]["hot_train_applicability"] == "stage0_only"

    # 2026-08-05 b-133 adjudication: the P2O5_gas tombstone is RESTORED
    # (the MC-4 wave-1B reactivation was wrong); the exact-key tombstone
    # survives the compatibility projection into retired_tombstones.
    assert "P2O5_gas" not in oxide_vapors
    legacy_locations = [
        name
        for name, section in vapor_pressure_data.items()
        if isinstance(section, dict) and "P2O5_gas" in section
    ]
    assert legacy_locations == ["retired_tombstones"]
    retired = vapor_pressure_data["retired_tombstones"]["P2O5_gas"]
    assert retired["hot_train_applicability"] == "not_applicable"


def test_compiled_p_carrier_provenance_records_intrinsic_melt_fo2(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data.catalog_payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"P2O5": 0.01, "SiO2": 0.99}
            },
            species_formula_registry={},
        ),
        temperature_C=1400.0,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": 1e-2,
            "intrinsic_fO2_log": -11.0,
            "process_phase": "stage0",
        },
    )

    result = provider.dispatch(request)
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["PO"]

    assert result.status == "ok"
    assert provenance["pO2_bar"] == pytest.approx(1e-11)
    assert provenance["oxygen_fugacity_channel"] == "intrinsic_melt"


def test_demaria_1971_k_validation_case_uses_measured_table1_po2(
    vapor_pressure_data,
):
    account = _wt_pct_to_mol_account(_DEMARIA_12022_WT_PCT)
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    temperature_K = 1429.0
    measured_po2_bar = 10.0 ** _demaria_12022_log10_po2_bar(temperature_K)
    measured_p_k_pa = (10.0 ** -8.8) * 101_325.0

    def modeled_p_k(pO2_bar: float) -> float:
        request = IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": account},
                species_formula_registry={},
            ),
            temperature_C=temperature_K - 273.15,
            pressure_bar=1e-6,
            control_inputs={
                "pO2_bar": pO2_bar,
                "intrinsic_fO2_log": math.log10(pO2_bar),
            },
        )
        return provider.dispatch(request).diagnostic["vapor_pressures_Pa"]["K"]

    modeled_measured_po2 = modeled_p_k(measured_po2_bar)
    modeled_floor_po2 = modeled_p_k(1e-9)
    residual_dex = math.log10(modeled_measured_po2 / measured_p_k_pa)
    floor_delta_dex = math.log10(modeled_floor_po2 / modeled_measured_po2)

    assert math.log10(measured_po2_bar) == pytest.approx(-7.853, abs=0.002)
    # Constant table gamma (no T*/T scaling); residual matches the
    # DeMaria-held-out constant-gamma baseline.
    assert residual_dex == pytest.approx(1.241, abs=0.005)
    assert abs(residual_dex) > 1.0
    assert floor_delta_dex == pytest.approx(0.288, abs=0.005)


def test_single_cation_mole_fraction_uses_mol_ledger_not_wt_proxy():
    account = {"Na2O": 0.25, "SiO2": 1.0, "Al2O3": 0.25}

    fractions = single_cation_mole_fractions(account)
    activity = melt_oxide_activity("Na2O", account)

    assert fractions["Na2O"] == pytest.approx(0.25)
    assert fractions["Al2O3"] == pytest.approx(0.25)
    assert fractions["SiO2"] == pytest.approx(0.5)
    assert activity is not None
    expected_gamma = table_gamma_effective(1.0e-3, 0.25)
    assert expected_gamma == pytest.approx(1.0e-3)
    assert activity.effective_gamma == pytest.approx(expected_gamma)
    assert activity.activity == pytest.approx(expected_gamma * 0.25)
    assert activity.activity_model == MELT_OXIDE_TABLE_GAMMA_MODEL


@pytest.mark.parametrize("oxide", ["Cr2O3", "MnO", "TiO2", "Na2O"])
def test_melt_activity_normalizes_pure_raoultian_component_to_unity(oxide):
    activity = melt_oxide_activity(oxide, {oxide: 1.0})

    assert activity is not None
    assert activity.x_single_cation == pytest.approx(1.0)
    assert activity.activity == pytest.approx(1.0)
    provenance = activity.provenance()
    assert provenance["melt_oxide_activity_reference_state"] == (
        "single_cation_Raoultian_pure_liquid_reference"
    )
    assert "melt_parent_oxide_activity" not in provenance
    assert "melt_oxide_activity_authority_status" not in provenance
    assert "melt_oxide_gamma_valid_range_K" not in provenance


@pytest.mark.parametrize("oxide", ["Cr2O3", "MnO", "TiO2", "Na2O"])
def test_melt_activity_is_continuous_into_pure_raoultian_endpoint(oxide):
    near_pure = melt_oxide_activity(
        oxide,
        {},
        cation_mol_fraction={oxide: 1.0 - 1.0e-9},
    )
    pure = melt_oxide_activity(oxide, {oxide: 1.0})

    assert near_pure is not None and pure is not None
    assert near_pure.activity == pytest.approx(1.0, abs=2.0e-9)
    assert pure.activity == pytest.approx(1.0)
    assert abs(pure.activity - near_pure.activity) < 2.0e-9


def test_melt_activity_uses_constant_table_gamma_for_mixed_melts():
    activity = melt_oxide_activity("Cr2O3", {"Cr2O3": 0.01, "SiO2": 0.99})

    assert activity is not None
    assert activity.x_single_cation == pytest.approx(0.019801980198019802)
    # Mid-range: constant table gamma (no pseudo-binary (1-X)^2 curvature).
    expected_gamma = table_gamma_effective(31.1, activity.x_single_cation)
    assert expected_gamma == pytest.approx(31.1)
    assert activity.effective_gamma == pytest.approx(expected_gamma)
    assert activity.activity == pytest.approx(
        expected_gamma * activity.x_single_cation
    )
    assert activity.activity_model == MELT_OXIDE_TABLE_GAMMA_MODEL


def test_k_table_gamma_is_temperature_independent_but_domain_labeled():
    """Constant-gamma path: numeric a is T-independent; domain status still labels OOD.

    The reverted pseudo-binary T*/T scaling must not move mid-range activity.
    anchor_T_K remains for b-121 domain authority only.
    """

    fractions = {"K2O": 0.01, "SiO2": 0.99}
    cold = melt_oxide_activity(
        "K2O", {}, cation_mol_fraction=fractions, temperature_K=1350.0
    )
    anchor = melt_oxide_activity(
        "K2O", {}, cation_mol_fraction=fractions, temperature_K=1500.0
    )
    hot = melt_oxide_activity(
        "K2O", {}, cation_mol_fraction=fractions, temperature_K=1950.0
    )
    pure_hot = melt_oxide_activity(
        "K2O", {}, cation_mol_fraction={"K2O": 1.0}, temperature_K=1950.0
    )

    assert cold is not None and anchor is not None and hot is not None
    # No T*/T scaling: same mid-range activity at every T.
    assert cold.activity == pytest.approx(anchor.activity)
    assert hot.activity == pytest.approx(anchor.activity)
    assert pure_hot is not None
    assert pure_hot.activity == pytest.approx(1.0)
    assert hot.provenance()["gamma_domain_authority"]["authority_status"] == (
        "out_of_gamma_domain"
    )
    assert cold.provenance()["gamma_domain_authority"]["authority_status"] == (
        "out_of_gamma_domain"
    )
    assert anchor.provenance()["gamma_domain_authority"]["authority_status"] == (
        "in_domain"
    )


def test_melt_activity_endmember_shell_is_local_only():
    """Continuity shell must not move lunar mid-range; pure endmember stays continuous."""

    lunar_ca = melt_oxide_activity(
        "CaO", {}, cation_mol_fraction={"CaO": 0.1156}
    )
    assert lunar_ca is not None
    assert lunar_ca.effective_gamma == pytest.approx(0.012)
    assert lunar_ca.activity == pytest.approx(0.012 * 0.1156)

    # Just inside the shell floor (X=0.99): still exact table gamma.
    at_floor = melt_oxide_activity(
        "Cr2O3", {}, cation_mol_fraction={"Cr2O3": 0.99}
    )
    assert at_floor is not None
    assert at_floor.effective_gamma == pytest.approx(31.1)
    assert at_floor.activity == pytest.approx(31.1 * 0.99)

    # Deep in shell: continuous into pure (no 31.1x cliff).
    near = melt_oxide_activity(
        "Cr2O3", {}, cation_mol_fraction={"Cr2O3": 1.0 - 1.0e-9}
    )
    pure = melt_oxide_activity("Cr2O3", {"Cr2O3": 1.0})
    assert near is not None and pure is not None
    assert near.activity == pytest.approx(1.0, abs=2.0e-9)
    assert pure.activity == pytest.approx(1.0)
    # Pre-continuity cliff magnitude: gamma*X at near-pure was ~31.1.
    assert abs(near.activity - 31.1 * (1.0 - 1.0e-9)) > 30.0


def test_missing_gamma_row_is_explicit_status_bearing_ideal_assertion():
    activity = melt_oxide_activity(
        "Fe2O3", {"Fe2O3": 0.25, "SiO2": 0.75}, temperature_K=1873.15
    )

    assert activity is not None
    assert activity.activity_model == MELT_OXIDE_IDEAL_SOLUTION_MODEL
    assert activity.evidence_tier == MELT_OXIDE_IDEAL_ASSERTION_TIER
    assert activity.warning is not None
    assert "declared_ideal_solution_activity_assertion" in activity.warning
    assert activity.provenance()["melt_oxide_activity_warning"] == activity.warning


def test_metal_vapor_activity_gamma_is_linear_for_alkalis_and_refractory_species(
    vapor_pressure_data,
    monkeypatch,
):
    account = {
        "SiO2": 1.0,
        "Na2O": 0.08,
        "K2O": 0.08,
        "TiO2": 0.08,
        "Cr2O3": 0.04,
        "MgO": 0.4,
    }
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(account)},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-9.0,
        control_inputs={"pO2_bar": 1.0, "intrinsic_fO2_log": -9.0},
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    grounded = provider.dispatch(request).diagnostic
    grounded_pressures = grounded["vapor_pressures_Pa"]
    grounded_provenance = grounded["vapor_pressure_numerator_provenance"]

    idealized = dict(MELT_OXIDE_ACTIVITY_COEFFICIENTS)
    for oxide in ("Na2O", "K2O", "TiO2", "Cr2O3"):
        idealized[oxide] = replace(idealized[oxide], gamma=1.0)
    monkeypatch.setattr(
        melt_activity,
        "MELT_OXIDE_ACTIVITY_COEFFICIENTS",
        idealized,
    )
    ideal_pressures = provider.dispatch(request).diagnostic["vapor_pressures_Pa"]

    assert ideal_pressures["Na"] / grounded_pressures["Na"] == pytest.approx(
        1.0 / grounded_provenance["Na"]["melt_oxide_effective_gamma"],
        rel=1e-9,
    )
    assert ideal_pressures["K"] / grounded_pressures["K"] == pytest.approx(
        1.0 / grounded_provenance["K"]["melt_oxide_effective_gamma"],
        rel=1e-9,
    )
    assert grounded_pressures["Cr"] / ideal_pressures["Cr"] == pytest.approx(
        grounded_provenance["Cr"]["melt_oxide_effective_gamma"],
        rel=1e-9,
    )
    assert grounded_provenance["Na"]["melt_oxide_activity"] == pytest.approx(
        grounded_provenance["Na"]["melt_oxide_effective_gamma"]
        * grounded_provenance["Na"]["melt_oxide_X_single_cation"]
    )
    assert grounded_provenance["Na"]["alphamelts_cross_check_status"] == (
        "inconclusive_no_activities"
    )

    ti_request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(account)},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-9.0,
        control_inputs={"pO2_bar": 1e-9, "intrinsic_fO2_log": -9.0},
    )
    monkeypatch.setattr(
        melt_activity,
        "MELT_OXIDE_ACTIVITY_COEFFICIENTS",
        MELT_OXIDE_ACTIVITY_COEFFICIENTS,
    )
    ti_grounded = provider.dispatch(ti_request).diagnostic["vapor_pressures_Pa"]
    monkeypatch.setattr(
        melt_activity,
        "MELT_OXIDE_ACTIVITY_COEFFICIENTS",
        idealized,
    )
    ti_ideal = provider.dispatch(ti_request).diagnostic["vapor_pressures_Pa"]
    assert ti_grounded["Ti"] / ti_ideal["Ti"] == pytest.approx(
        grounded_provenance["Ti"]["melt_oxide_effective_gamma"],
        rel=1e-9,
    )


def test_sio_pure_limit_uses_single_cation_activity_reference_pressure(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = _si_only_vapor_request_at_T_K(1923.15)

    result = provider.dispatch(request)
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["SiO"]

    assert result.diagnostic["activities"]["SiO"] == pytest.approx(1.0)
    assert provenance["activity_factor"] == pytest.approx(1.0)
    assert result.diagnostic["vapor_pressures_Pa"]["SiO"] == pytest.approx(
        provenance["P_reference_Antoine_Pa"]
    )


def test_sio_standard_reaction_term_matches_source_thermochemistry(
    vapor_pressure_data,
):
    sio_row = vapor_pressure_data["oxide_vapors"]["SiO"]
    # Independently regenerated from VapoRock's JANAF SiO/O2 multi-interval
    # Shomate terms plus ThermoEngine v1.0 liquid-SiO2 mu0 over the full
    # process envelope. Source values are test anchors, not values calculated
    # from the runtime Antoine coefficients.
    source_points = (
        (1400.0, -6.904421868),
        (1500.0, -4.953131396),
        (1600.0, -3.250905047),
        (1700.0, -1.753948057),
        (1800.0, -0.427731189),
        (1900.0, 0.754969595),
        (2000.0, 1.815905539),
        (2100.0, 2.772661024),
        (2200.0, 3.639609037),
        (2273.15, 4.223822537),
    )

    assert sio_row["fit_target"] == "standard_reaction_term"
    assert sio_row["confidence_tier"] == "moderate"
    assert sio_row["valid_range_K"] == [1400, 2273.15]
    reaction = sio_row["reaction"]
    assert reaction["formula"] == "SiO2(l) -> SiO(g) + 0.5 O2(g)"
    assert "JANAF SiO(g)/O2(g)" in reaction["basis"]
    assert "ThermoEngine v1.0" in reaction["basis"]
    assert reaction["fit_residual_dex"]["max_abs"] == pytest.approx(0.001630)

    coeff = sio_row["antoine"]
    assert reaction["source_table_log10_P_ref_Pa"] == [
        list(point) for point in source_points
    ]
    for temperature_K, source_log10_p_ref in source_points:
        fit_log10_p_ref = coeff["A"] - coeff["B"] / (
            temperature_K + coeff["C"]
        )
        assert fit_log10_p_ref == pytest.approx(source_log10_p_ref, abs=0.002)

    # In-domain sanity: 2023.15 K reproduces the ~111 Pa standard-term anchor.
    heldout = reaction["heldout_2023_15_K"]
    fit_2023 = coeff["A"] - coeff["B"] / (2023.15 + coeff["C"])
    assert fit_2023 == pytest.approx(heldout["fit_log10_P_ref_Pa"], abs=1e-6)
    assert 10.0 ** fit_2023 == pytest.approx(heldout["fit_P_ref_Pa"], rel=1e-6)
    assert heldout["fit_P_ref_Pa"] == pytest.approx(110.8, abs=0.1)


def test_mg_gas_standard_pressure_rises_with_temperature(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    low = provider.dispatch(_mg_vapor_request_at_T_K(1700.0))
    high = provider.dispatch(_mg_vapor_request_at_T_K(1900.0))

    assert high.diagnostic["vapor_pressures_Pa"]["Mg"] > (
        low.diagnostic["vapor_pressures_Pa"]["Mg"]
    )
    # b-147: high-T path is JANAF gas_fugacity (TE gas_rail dormant).
    assert high.diagnostic["vapor_pressure_numerator_provenance"]["Mg"][
        "pressure_rail"
    ] == "gas_fugacity"


def test_na_standard_reaction_term_reconstructs_liquid_nao05_source_rows(
    vapor_pressure_data,
):
    """K-style live recon: L&H NaO0.5 source rows reconstruct with recon_err ≈ 0."""

    row = vapor_pressure_data["metals"]["Na"]
    coeff = row["antoine"]
    reaction = row["reaction"]

    assert row["fit_target"] == "standard_reaction_term"
    assert reaction["formula"] == "NaO0.5(l) -> Na(g) + 0.25 O2(g)"
    assert "Lamoreaux & Hildenbrand 1984 Tables 2/4" in reaction["basis"]
    assert "10.1063/1.555706" in reaction["basis"]
    assert row["oxide_activity_exponent"] == pytest.approx(1.0)
    assert row["pO2_exponent"] == pytest.approx(-0.25)
    assert "declared_compensation" not in row
    assert "pressure_bracket" not in row
    assert row["coherent_pair"]["gamma"] == pytest.approx(1.0e-3)
    assert row["shadow_bracket"]["status"] == "status_bearing_non_authoritative"
    assert row["shadow_bracket"]["full_vaporock_Pa"] == pytest.approx(0.002032)

    for source_row in reaction["source_table_values"]:
        temperature_K = source_row["T_K"]
        fit_log10_p_pa = coeff["A"] - coeff["B"] / (
            temperature_K + coeff["C"]
        )
        assert fit_log10_p_pa == pytest.approx(
            source_row["fit_log10_P_Na_ref_Pa"],
            abs=1e-6,
        )
        assert fit_log10_p_pa == pytest.approx(
            source_row["log10_P_Na_ref_Pa"],
            abs=0.001,
        )

    assert reaction["fit_residual_dex"]["max_abs"] == pytest.approx(
        0.000118,
        abs=1e-6,
    )
    heldout = reaction["heldout_demaria_comparison"]
    anchor = next(r for r in heldout if r["sample"] == "12022" and r["T_K"] == 1429.0)
    assert anchor["residual_dex_model_minus_demaria"] == pytest.approx(
        -0.3527,
        abs=1e-3,
    )
    assert all(r.get("partial_melt") is True for r in heldout)


def test_na_coherent_pair_and_constant_gamma_golden(
    vapor_pressure_data,
):
    """t-383 coherent pair: L&H standard_reaction_term + constant table gamma."""

    na_row = vapor_pressure_data["metals"]["Na"]
    assert na_row["authority_class"] == "uncertified"
    assert na_row["fit_target"] == "standard_reaction_term"
    assert na_row["pseudo_antoine_status"] == "inactive_provenance_only"
    assert na_row["coherent_pair"]["standard"] == (
        "lamoreaux_hildenbrand_1984_liquid_nao0_5"
    )
    assert na_row["shadow_bracket"]["full_vaporock_Pa"] == pytest.approx(0.002032)

    account = _wt_pct_to_mol_account(_DEMARIA_12022_WT_PCT)
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    temperature_K = 1429.0
    pO2_bar = 10.0 ** _demaria_12022_log10_po2_bar(temperature_K)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": account},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": pO2_bar,
            "intrinsic_fO2_log": math.log10(pO2_bar),
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}

    # Golden tooth: constant table gamma on L&H Pref (P UP vs retired Chase).
    p_na = diagnostic["vapor_pressures_Pa"]["Na"]
    assert p_na == pytest.approx(0.035201224843865266, rel=1e-9)
    # Sign story: UP vs retired Chase pin 0.02684167312949837 (+0.118 dex).
    assert p_na > 0.02684167312949837

    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Na"]
    assert provenance["authority_class"] == "uncertified"
    assert provenance["pseudo_antoine_status"] == "inactive_provenance_only"
    assert provenance["pressure_rail"] == "liquid_oxide_standard_reaction"
    assert provenance["pO2_exponent"] == pytest.approx(-0.25)
    assert provenance["shadow_bracket"]["full_vaporock_Pa"] == pytest.approx(
        0.002032
    )
    # 1429 K is outside gamma_domain_K=[1673,1673] → DRIFT-HIGH suffix.
    assert diagnostic["vapor_pressures_source"]["Na"] == (
        "builtin_authoritative:standard_reaction_term:out_of_gamma_domain"
    )

    species_authority = diagnostic["species_authority"]["Na"]
    assert species_authority["authority_class"] == "uncertified"
    assert species_authority["coherent_pair"]["gamma"] == pytest.approx(1.0e-3)
    assert species_authority["shadow_bracket"]["status"] == (
        "status_bearing_non_authoritative"
    )


def test_demaria_1971_na_heldout_per_sample_residuals(
    vapor_pressure_data,
):
    """Per-sample DeMaria binding; residuals reported inside pre-registered σ at anchor.

    The misbound 1538 K / flat-1396-K-pO2 / 3.2e-2 Pa cross-sample gap pin is
    deleted (t-383 Step 2/3). Residuals are never fitted away (ADR-001).
    """

    account = _wt_pct_to_mol_account(_DEMARIA_12022_WT_PCT)
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    temperature_K = 1429.0
    pO2_bar = 10.0 ** _demaria_12022_log10_po2_bar(temperature_K)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": account},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": pO2_bar,
            "intrinsic_fO2_log": math.log10(pO2_bar),
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    p_na = result.diagnostic["vapor_pressures_Pa"]["Na"]
    # Extract line_anchor_1429K for 12022 circles (upper line).
    measured_p_na = 7.93e-2
    residual_dex = math.log10(p_na / measured_p_na)
    # Pre-registered digitization σ = 0.30 dex/pt; anchor residual near that band.
    assert residual_dex == pytest.approx(-0.3527, abs=0.01)
    assert abs(residual_dex) < 0.40
    # fO2 slope −0.25 preserved (monatomic ν).
    p_floor = 1e-9
    request_floor = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": account},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": p_floor,
            "intrinsic_fO2_log": math.log10(p_floor),
        },
    )
    p_na_floor = provider.dispatch(request_floor).diagnostic[
        "vapor_pressures_Pa"
    ]["Na"]
    slope = math.log10(p_na_floor / p_na) / math.log10(p_floor / pO2_bar)
    assert slope == pytest.approx(-0.25, abs=1e-6)


def test_sio_authority_class_emitted_with_authoritative_source_label(
    vapor_pressure_data,
):
    """SiO: machine-readable authority_class demotes builtin_authoritative source."""

    sio_row = vapor_pressure_data["oxide_vapors"]["SiO"]
    assert sio_row["authority_class"] == "uncertified"

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    result = provider.dispatch(_si_only_vapor_request_at_T_K(1900.0))
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}

    source = diagnostic["vapor_pressures_source"]["SiO"]
    assert source.startswith("builtin_authoritative:")
    assert "standard_reaction_term" in source

    provenance = diagnostic["vapor_pressure_numerator_provenance"]["SiO"]
    assert provenance["authority_class"] == "uncertified"
    species_authority = diagnostic["species_authority"]["SiO"]
    assert species_authority["authority_class"] == "uncertified"
    # Fail-open tooth: source token alone is not certification.
    assert source != "certified"
    assert "authority_class" in provenance


def test_pairing_metals_authority_class_emitted(
    vapor_pressure_data,
):
    """Ca/Mg/Al/Ti/Cr/Mn pairing rails: authority_class on every changed metal."""

    pairing = ("Ca", "Mg", "Al", "Ti", "Cr", "Mn")
    for species in pairing:
        assert vapor_pressure_data["metals"][species]["authority_class"] == (
            "uncertified"
        )

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    # 2000 K hits Mg/Ca gas rails and Al/Cr/Mn liquid-oxide rails; Ti needs
    # its liquid-oxide band start (1941 K).
    account = {
        "SiO2": 1.0,
        "MgO": 0.4,
        "CaO": 0.3,
        "Al2O3": 0.2,
        "TiO2": 0.1,
        "Cr2O3": 0.05,
        "MnO": 0.05,
    }
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": account},
            species_formula_registry={},
        ),
        temperature_C=2000.0 - 273.15,
        pressure_bar=1e-6,
        control_inputs={
            "pO2_bar": 1e-9,
            "intrinsic_fO2_log": -9.0,
        },
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}
    sources = diagnostic["vapor_pressures_source"]
    provenance = diagnostic["vapor_pressure_numerator_provenance"]
    species_authority = diagnostic["species_authority"]

    emitted = [sp for sp in pairing if sp in provenance]
    assert emitted, "expected at least one pairing metal pressure entry"
    for species in emitted:
        source = sources[species]
        assert source.startswith("builtin_authoritative:"), species
        assert provenance[species]["authority_class"] == "uncertified", species
        assert species_authority[species]["authority_class"] == "uncertified", (
            species
        )


def test_cro2_non_authoritative_class_survives_authoritative_provider_label(
    vapor_pressure_data,
):
    """CrO2 standard-reaction term remains explicitly non-authoritative."""

    cro2_row = vapor_pressure_data["oxide_vapors"]["CrO2"]
    assert cro2_row["authority_class"] == "analytical_non_authoritative"
    assert cro2_row["valid_range_K"] == [1400.0, 2273.15]

    provider = BuiltinVaporPressureProvider(vapor_pressure_data.catalog_payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"Cr2O3": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1800.0 - 273.15,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}

    source = diagnostic["vapor_pressures_source"]["CrO2"]
    assert source.startswith("builtin_authoritative:")
    assert "standard_reaction_term" in source
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["CrO2"]
    assert provenance["authority_class"] == "analytical_non_authoritative"
    assert diagnostic["species_authority"]["CrO2"]["authority_class"] == (
        "analytical_non_authoritative"
    )


def test_fe_degraded_authority_class_and_typed_degraded_flag(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Fe degraded path: authority_class + typed degraded_activity_basis flag."""

    fe_row = vapor_pressure_data["metals"]["Fe"]
    assert fe_row["authority_class"] == "uncertified"
    assert fe_row["pseudo_antoine_status"] == "inactive_dormant"

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    no_channel = _fe_redox_request(
        species_formula_registry=sim.species_formula_registry,
        intrinsic_fO2_log=None,
        fO2_log=-4.0,
    )
    result = provider.dispatch(no_channel)
    assert result.status == "ok"
    diagnostic = result.diagnostic or {}

    source = diagnostic["vapor_pressures_source"]["Fe"]
    assert source.startswith("builtin_authoritative:")
    provenance = diagnostic["vapor_pressure_numerator_provenance"]["Fe"]
    # Typed degraded flag class (feo_weight_fraction) + matrix authority class.
    assert provenance["degraded_activity_basis"] == "feo_weight_fraction"
    assert provenance["activity_basis"] == "feo_weight_fraction"
    assert provenance["authority_class"] == "uncertified"
    assert diagnostic["species_authority"]["Fe"]["authority_class"] == (
        "uncertified"
    )
    assert fe_row.get("pseudo_antoine_status") == "inactive_dormant"
    assert any(
        "degraded_activity_basis=feo_weight_fraction" in warning
        for warning in result.warnings
    )


class _LegacyInternalAnalyticalModel(EquilibriumMixin):
    def __init__(self, vapor_pressure_data, melt=None):
        self.vapor_pressures = vapor_pressure_data
        self.melt = melt or _CaOnlyMelt()

    def _commanded_pO2_bar(self):
        return 1e-9

    def _compute_intrinsic_melt_fO2(self):
        return -9.0


def test_metal_antoine_range_extrapolation_is_diagnostic(
    vapor_pressure_data,
):
    assert vapor_pressure_data["metals"]["Ca"]["valid_range_K"] == [1115, 1757]
    assert (
        vapor_pressure_data["metals"]["Ca"]["pure_component_antoine"]["valid_range_K"]
        == [1254, 1712]
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    result = provider.dispatch(_ca_range_extrapolation_request())

    assert result.status == "ok"
    assert result.diagnostic["vapor_pressures_Pa"]["Ca"] > 0.0
    extrapolation = result.diagnostic[
        "extrapolated_beyond_valid_range_K"
    ]["Ca"]
    assert extrapolation["temperature_K"] == pytest.approx(
        _CA_RANGE_EXTRAPOLATION_T_K
    )
    assert tuple(extrapolation["valid_range_K"]) == (1254.0, 1712.0)
    assert any(
        "Ca metal Antoine fit extrapolated beyond valid_range_K" in warning
        for warning in result.warnings
    )


def test_mg_reconstructed_bridge_derivation_and_declared_bounds(
    vapor_pressure_data,
):
    mg_data = vapor_pressure_data["metals"]["Mg"]
    mg_coeff = mg_data["pure_component_antoine"]
    assert mg_coeff["source_certified_range_K"] == [701, 1361]
    assert mg_coeff["valid_range_K"] == [701, 1361]
    gas_rail = mg_data["gas_rail_standard_reaction"]
    # b-147: gas_rail retained as dormant provenance; bridge upper is Pref_GF.
    assert gas_rail.get("status") == "dormant_non_authoritative"
    assert gas_rail["source_certified_range_K"] == [1366, 2273.15]
    segment = mg_data["reconstructed_vapor_pressure_segment"]
    assert segment["authority_status"] == (
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS
    )
    assert segment["authority_flag"] == (
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG
    )

    lower = reconstructed_vapor_pressure_authority_limit(
        "Mg", mg_data, 1361.0, consumer="test"
    )
    upper = reconstructed_vapor_pressure_authority_limit(
        "Mg", mg_data, 1366.0, consumer="test"
    )
    assert lower is not None
    assert upper is not None
    assert lower["pressure_Pa"] == pytest.approx(100000.0)
    # Upper anchor is Pref_GF at 1366 K (not dormant TE Pref_GR).
    expected_upper = 6.020045850530698e-13
    assert upper["pressure_Pa"] == pytest.approx(expected_upper, rel=1e-14)
    assert "Pref_GF" in segment["provenance"]
    assert "b-147" in segment["provenance"]


@pytest.mark.parametrize("temperature_K", [1361.0, 1361.171])
def test_mg_reconstructed_bridge_emits_typed_provider_diagnostic(
    vapor_pressure_data,
    temperature_K,
):
    """Bridge applies on the condensed rail below boil (≤1361 K certified side).

    b-147: above Mg boil (1363.15 K) Builtin uses gas_fugacity (TE gas_rail
    dormant), so reconstructed-segment diagnostics are condensed-rail only.
    """
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    result = provider.dispatch(_mg_vapor_request_at_T_K(temperature_K))

    assert result.status == "ok"
    assert result.diagnostic["vapor_pressures_Pa"]["Mg"] > 0.0
    assert "Mg" not in result.diagnostic["extrapolated_beyond_valid_range_K"]
    assert "reconstructed_vapor_pressure_segment" in (
        result.diagnostic["vapor_pressures_source"]["Mg"]
    )
    authority = result.diagnostic["vapor_pressure_authority"]
    assert authority["status"] == "authority_limited"
    assert authority[VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG] is True
    limit = authority["authority_limits"]["Mg"]
    assert limit["authority_status"] == (
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS
    )
    assert limit[VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert tuple(limit["segment_range_K"]) == (1361.0, 1366.0)
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["Mg"]
    assert provenance[VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG] is True
    assert provenance["P_reference_Antoine_Pa"] == pytest.approx(
        limit["pressure_Pa"]
    )


@pytest.mark.parametrize("temperature_K", [1363.15, 1366.0])
def test_mg_above_boil_uses_gas_fugacity_not_reconstructed_bridge(
    vapor_pressure_data,
    temperature_K,
):
    """b-147: at/above boil the demoted gas_rail falls through to gas_fugacity."""
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )
    assert result.status == "ok"
    assert result.diagnostic["vapor_pressures_Pa"]["Mg"] > 0.0
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["Mg"]
    assert provenance["pressure_rail"] == "gas_fugacity"
    assert "gas_standard_fugacity" in result.diagnostic["vapor_pressures_source"][
        "Mg"
    ]


@pytest.mark.parametrize("temperature_K", [1360.999, 1366.001])
def test_mg_reconstructed_bridge_flag_absent_outside_segment(
    vapor_pressure_data,
    temperature_K,
):
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )

    authority = result.diagnostic["vapor_pressure_authority"]
    assert authority["status"] == "authoritative"
    assert authority[VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG] is False
    assert authority["authority_limits"] == {}
    assert "reconstructed_vapor_pressure_segment" not in (
        result.diagnostic["vapor_pressures_source"]["Mg"]
    )


@pytest.mark.parametrize("temperature_K", [2273.151])
def test_mg_provider_accepts_process_cap_under_gas_fugacity(
    vapor_pressure_data,
    temperature_K,
):
    """b-147: with TE gas_rail dormant, high-T uses JANAF gas_fugacity.

    Ellingham gas-metal segments cover to 2600 K, so the historical
    gas_rail upper bound 2273.15 is no longer a hard refuse for the live
    high-T path. Process-cap T just above 2273.15 remains OK.
    """
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )
    assert result.status == "ok"
    assert result.diagnostic["vapor_pressures_Pa"]["Mg"] > 0.0
    assert (
        result.diagnostic["vapor_pressure_numerator_provenance"]["Mg"][
            "pressure_rail"
        ]
        == "gas_fugacity"
    )


def test_mg_source_range_guard_refuses_below_total_certified_envelope(
    vapor_pressure_data,
):
    with pytest.raises(
        VaporPressureRangeError,
        match=r"source_certified_range_K=\[701, 2273\.15\]",
    ):
        require_antoine_source_certified_temperature(
            "Mg",
            vapor_pressure_data["metals"]["Mg"],
            "pure_component_antoine",
            700.0,
            consumer="test",
        )


@pytest.mark.parametrize("temperature_K", [701.0, 2273.15])
def test_mg_provider_accepts_total_certified_envelope_endpoints(
    vapor_pressure_data,
    temperature_K,
):
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )

    assert result.status == "ok"
    assert result.diagnostic["vapor_pressure_authority"][
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG
    ] is False


@pytest.mark.parametrize("temperature_K", [1366.001, 1873.0])
def test_mg_gas_fugacity_is_independent_of_antoine_coefficients(
    vapor_pressure_data,
    temperature_K,
):
    """b-147: high-T gas_fugacity is independent of pure-component Antoine."""
    baseline_data = copy.deepcopy(vapor_pressure_data)
    no_antoine_data = copy.deepcopy(vapor_pressure_data)
    no_antoine_data["metals"]["Mg"].pop("pure_component_antoine")

    baseline = BuiltinVaporPressureProvider(baseline_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )
    without_antoine = BuiltinVaporPressureProvider(no_antoine_data).dispatch(
        _mg_vapor_request_at_T_K(temperature_K)
    )

    assert without_antoine.status == "ok"
    assert without_antoine.diagnostic["vapor_pressures_Pa"]["Mg"] == pytest.approx(
        baseline.diagnostic["vapor_pressures_Pa"]["Mg"]
    )
    provenance = without_antoine.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["Mg"]
    assert provenance["pressure_rail"] == "gas_fugacity"
    assert "P_reference_Antoine_Pa" not in provenance
    assert "gas_standard_fugacity" in provenance["source_label"]

    graph_pressure = ellingham_graph.effective_equilibrium_pressure_Pa(
        "Mg",
        temperature_K,
        1e-9,
        vapor_pressure_data=no_antoine_data,
        a_oxide=1.0,
    )
    baseline_graph_pressure = ellingham_graph.effective_equilibrium_pressure_Pa(
        "Mg",
        temperature_K,
        1e-9,
        vapor_pressure_data=baseline_data,
        a_oxide=1.0,
    )
    assert graph_pressure == pytest.approx(baseline_graph_pressure)


def test_legacy_mg_rails_use_reconstructed_bridge_and_ignore_gas_antoine(
    vapor_pressure_data,
):
    # Condensed-rail bridge band (certified pure-comp upper 1361 K side).
    for temperature_K in (1361.0, 1361.171):
        result = _LegacyInternalAnalyticalModel(
            vapor_pressure_data,
            melt=_MgOnlyMelt(temperature_K),
        )._internal_analytical_equilibrium()
        assert result.vapor_pressures_Pa["Mg"] > 0.0
        authority = result.diagnostics["vapor_pressure_authority"]
        assert authority[VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG] is True
        assert authority["authority_limits"]["Mg"]["authority_status"] == (
            VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_STATUS
        )

    # b-147: at/above boil, demoted gas_rail falls through to gas_fugacity
    # (no reconstructed-segment flag on the gas path).
    for temperature_K in (1363.15, 1366.0, 1366.001):
        result = _LegacyInternalAnalyticalModel(
            vapor_pressure_data,
            melt=_MgOnlyMelt(temperature_K),
        )._internal_analytical_equilibrium()
        assert result.vapor_pressures_Pa["Mg"] > 0.0
        assert result.diagnostics["vapor_pressure_authority"][
            VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG
        ] is False
        assert "gas_standard_fugacity" in result.vapor_pressures_source["Mg"]

    # Just below the reconstructed segment remains pure-comp without bridge flag.
    result = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_MgOnlyMelt(1360.999),
    )._internal_analytical_equilibrium()
    assert result.diagnostics["vapor_pressure_authority"][
        VAPOR_PRESSURE_RECONSTRUCTED_AUTHORITY_FLAG
    ] is False

    below_range = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_MgOnlyMelt(700.0),
    )._internal_analytical_equilibrium()
    assert "Mg" not in below_range.vapor_pressures_Pa
    assert any(
        "species=Mg consumer=legacy_condensed_rail" in warning
        for warning in below_range.warnings
    )

    # High-T process-cap under gas_fugacity remains live (Ellingham to 2600 K).
    high = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_MgOnlyMelt(2273.151),
    )._internal_analytical_equilibrium()
    assert high.vapor_pressures_Pa["Mg"] > 0.0
    assert "gas_standard_fugacity" in high.vapor_pressures_source["Mg"]

    baseline = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_MgOnlyMelt(1873.0),
    )._internal_analytical_equilibrium()
    no_antoine_data = copy.deepcopy(vapor_pressure_data)
    no_antoine_data["metals"]["Mg"].pop("pure_component_antoine")
    without_antoine = _LegacyInternalAnalyticalModel(
        no_antoine_data,
        melt=_MgOnlyMelt(1873.0),
    )._internal_analytical_equilibrium()

    assert without_antoine.vapor_pressures_Pa["Mg"] == pytest.approx(
        baseline.vapor_pressures_Pa["Mg"]
    )
    # b-147: high-T path is gas_fugacity (TE gas_rail dormant).
    assert "gas_standard_fugacity" in without_antoine.vapor_pressures_source["Mg"]


def test_sio_source_validated_domain_covers_process_envelope(
    vapor_pressure_data,
):
    """Branch (a): 1400-2273.15 K is source-validated, not diagnostic-limited."""
    sio_data = vapor_pressure_data["oxide_vapors"]["SiO"]
    assert sio_data["valid_range_K"] == [1400, 2273.15]
    assert "extrapolation_allowed_range_K" not in sio_data
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    in_range = provider.dispatch(_si_only_vapor_request_at_T_K(1900.0))
    mid_envelope = provider.dispatch(_si_only_vapor_request_at_T_K(2023.15))
    process_cap = provider.dispatch(_si_only_vapor_request_at_T_K(2273.15))

    assert in_range.status == "ok"
    assert mid_envelope.status == "ok"
    assert process_cap.status == "ok"
    for result in (in_range, mid_envelope, process_cap):
        assert result.diagnostic["vapor_pressures_Pa"]["SiO"] > 0.0
        assert "SiO" not in result.diagnostic["extrapolated_beyond_valid_range_K"]
        sio_source = result.diagnostic["vapor_pressures_source"]["SiO"]
        assert sio_source == "builtin_authoritative:standard_reaction_term"
        assert "extrapolated_beyond_valid_range_K" not in sio_source


def test_sio_hard_vacuum_anchor_restored_without_weight_fraction_proxy(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    lunar_mare_mol = {
        "FeO": 0.13531878,
        "MgO": 0.13157064,
        "SiO2": 0.43638096,
        "CaO": 0.29672962,
    }
    request = replace(
        _si_only_vapor_request_at_T_K(2023.15),
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": lunar_mare_mol},
            species_formula_registry={},
        ),
    )

    result = provider.dispatch(request)
    pressure = result.diagnostic["vapor_pressures_Pa"]["SiO"]
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["SiO"]

    assert 45.0 <= pressure <= 50.0
    assert provenance["melt_oxide_activity"] == pytest.approx(0.43638096)
    assert provenance["melt_oxide_activity"] != pytest.approx(0.45805455)
    # In-domain after branch (a) domain extension — ledger-eligible uncertified.
    sio_source = result.diagnostic["vapor_pressures_source"]["SiO"]
    assert sio_source == "builtin_authoritative:standard_reaction_term"
    assert "SiO" not in result.diagnostic["extrapolated_beyond_valid_range_K"]


def test_absent_metals_do_not_emit_range_warnings(vapor_pressure_data):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = _si_only_vapor_request_at_T_K(1923.15)

    result = provider.dispatch(request)

    assert result.status == "ok"
    metal_species = set(vapor_pressure_data["metals"])
    assert metal_species.isdisjoint(
        result.diagnostic["extrapolated_beyond_valid_range_K"]
    )
    assert metal_species.isdisjoint(
        result.diagnostic["ellingham_extrapolated_beyond_fit_range_K"]
    )
    assert all(
        not any(warning.startswith(f"{species} ") for warning in result.warnings)
        for species in metal_species
    )

    present_k = provider.dispatch(
        replace(
            request,
            account_view=ProviderAccountView(
                accounts={
                    "process.cleaned_melt": {"SiO2": 1.0, "K2O": 0.1},
                },
                species_formula_registry={},
            ),
        )
    )
    assert any(
        warning.startswith("K ") and "extrapolated" in warning
        for warning in present_k.warnings
    )


def test_builtin_provider_marks_sio_standard_reaction_source_authoritative(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    result = provider.dispatch(_si_only_vapor_request_at_T_K(1899.0))

    source = result.diagnostic["vapor_pressures_source"]["SiO"]
    assert source == "builtin_authoritative:standard_reaction_term"


def test_sio_oxide_vapor_extrapolation_fails_loud_beyond_process_bound(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)

    with pytest.raises(
        VaporPressureComputationError,
        match=(
            "oxide_vapor_pressure_out_of_validated_range: "
            "species=SiO .*valid_range_K=\\[1400, 2273.15\\] "
            "extrapolation_allowed_range_K=absent"
        ),
    ):
        provider.dispatch(_si_only_vapor_request_at_T_K(2273.16))


def test_ellingham_fit_band_extrapolation_is_diagnostic(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"FeO": 1.0}},
        species_formula_registry=sim.species_formula_registry,
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=view,
        temperature_C=800.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    extrapolation = result.diagnostic[
        "ellingham_extrapolated_beyond_fit_range_K"
    ]["Fe"]
    assert extrapolation["temperature_K"] == pytest.approx(1073.15)
    assert tuple(extrapolation["fit_range_K"]) == ellingham_fit_range_K("Fe")
    assert any(
        "Fe Ellingham JANAF high-T fit extrapolated beyond fit_range_K"
        in warning
        for warning in result.warnings
    )


def test_low_confidence_fe_pseudo_vaporock_fallback_is_omitted_outside_range(
    vapor_pressure_data,
):
    data = copy.deepcopy(vapor_pressure_data)
    assert "pure_component_antoine" in data["metals"]["Fe"]
    data["metals"]["Fe"].pop("pure_component_antoine")
    assert data["metals"]["Fe"]["fit_target"] == "pseudo_psat_backsolved_from_vaporock"
    assert data["metals"]["Fe"]["confidence_tier"] == "low"
    provider = BuiltinVaporPressureProvider(data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"FeO": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1800.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert "Fe" not in result.diagnostic["vapor_pressures_Pa"]
    assert any(
        "non_certifying_vapor_pressure_fallback_omitted: species=Fe"
        in warning
        for warning in result.warnings
    )


def test_low_confidence_k_pseudo_vaporock_gas_rail_ignores_condensed_fallback(
    vapor_pressure_data,
):
    data = copy.deepcopy(vapor_pressure_data)
    data["metals"]["K"]["fit_target"] = "pseudo_psat_backsolved_from_vaporock"
    data["metals"]["K"]["confidence_tier"] = "low"
    provider = BuiltinVaporPressureProvider(data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"K2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1800.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["vapor_pressures_Pa"]["K"] > 0.0
    assert not any(
        "non_certifying_vapor_pressure_fallback_omitted: species=K" in warning
        for warning in result.warnings
    )
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["K"]
    assert provenance["pressure_rail"] == "gas_fugacity"
    assert provenance["metal_standard_state"] == "gas"
    assert "P_reference_Antoine_Pa" not in provenance
    assert provenance["source_label"].startswith(
        "builtin_extrapolation_limited:gas_standard_fugacity"
    )
    assert provenance["source_label"].endswith(
        "extrapolated_beyond_ellingham_fit_range_K"
    )


def test_default_in_range_builtin_provider_keeps_fe_pseudo_fallback_and_legacy_species(
    vapor_pressure_data, monkeypatch
):
    def reject_only_interval_rows(species, row, coefficient_block):
        if not bool((row or {}).get("interval_required")):
            raise AssertionError(
                f"non-interval row was screened for certification: {species}"
            )

    monkeypatch.setattr(
        vapor_pressure_module,
        "reject_noncertifying_vapor_pressure_row",
        reject_only_interval_rows,
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"FeO": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=_FeOnlyHighTMelt.temperature_C,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    provider_result = provider.dispatch(request)
    legacy_result = _LegacyInternalAnalyticalModel(
        vapor_pressure_data, melt=_FeOnlyHighTMelt()
    )._internal_analytical_equilibrium()
    provider_vp = dict(
        (provider_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )

    assert set(provider_vp) == set(legacy_result.vapor_pressures_Pa)
    assert "Fe" in provider_vp
    for species, pressure in legacy_result.vapor_pressures_Pa.items():
        assert provider_vp[species] == pytest.approx(
            pressure, rel=_VP_TOLERANCE_REL, abs=_VP_TOLERANCE_ABS_PA
        )
    assert not any(
        "non_certifying_vapor_pressure_fallback_omitted" in warning
        for warning in provider_result.warnings
    )


def test_request_level_fo2_below_transport_floor_is_rejected(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-20.0,
        control_inputs={},
    )

    with pytest.raises(ValueError, match="fO2_log=-20.*transport model floor"):
        provider.dispatch(request)


def test_interval_required_foulant_vapor_row_is_not_certifying(
    vapor_pressure_data,
):
    naf = copy.deepcopy(vapor_pressure_data["foulant_vapor"]["NaF"])
    assert naf["interval_required"] is True
    assert naf["certified_point"] is None
    data = {"metals": {}, "oxide_vapors": {"NaF": naf}}
    provider = BuiltinVaporPressureProvider(data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"NaF": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    with pytest.raises(
        VaporPressureComputationError,
        match="non_certifying_interval_vapor_pressure: species=NaF",
    ):
        provider.dispatch(request)


def test_sio_row_peq_matches_hand_antoine_lunar_low_ti_floor_po2(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    account_view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": sim.atom_ledger.mol_by_account(
                "process.cleaned_melt"
            )
        },
        species_formula_registry=sim.species_formula_registry,
    )
    temperature_C = 1650.0
    temperature_K = temperature_C + 273.15
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=account_view,
        temperature_C=temperature_C,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    result = provider.dispatch(request)

    sio_row = vapor_pressure_data["oxide_vapors"]["SiO"]
    antoine = sio_row["antoine"]
    # Hand arithmetic from the row:
    # P_ref = 10 ** (A - B / (T_K + C)); floor pO2 is the row reference,
    # so pO2^-0.5 suppression is unity and P_eq = P_ref * a_SiO2.
    p_reference = 10 ** (
        float(antoine["A"])
        - float(antoine["B"]) / (temperature_K + float(antoine["C"]))
    )
    oxide_activity = melt_oxide_activity(
        "SiO2",
        account_view.accounts["process.cleaned_melt"],
    )
    assert oxide_activity is not None
    activity = oxide_activity.activity ** float(
        sio_row.get("oxide_activity_exponent", 1.0)
    )
    expected_p_eq = p_reference * activity
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["SiO"]

    # Absolute P_ref follows the YAML Antoine row (refit over 1400-2273.15 K);
    # do not pin a pre-refit magic number — the hand algebra is the check.
    assert p_reference > 0.0
    assert 1.0 < expected_p_eq < 10.0
    assert provenance["P_reference_Antoine_Pa"] == pytest.approx(p_reference)
    assert provenance["activity_factor"] == pytest.approx(activity)
    assert provenance["pO2_bar"] == pytest.approx(1.0e-9)
    assert result.diagnostic["vapor_pressures_Pa"]["SiO"] == pytest.approx(
        expected_p_eq
    )


def test_al2o_provider_applies_single_cation_activity_square_once(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Controlled two-component melt keeps Al2O above the provider's negligible
    # pressure cutoff while retaining a non-unity activity.
    melt_mol = {"Al2O3": 1.0, "SiO2": 1.0}
    account_view = ProviderAccountView(
        accounts={"process.cleaned_melt": melt_mol},
        species_formula_registry=sim.species_formula_registry,
    )
    production_payload = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2]
            / "data"
            / "vapor_pressures.yaml"
        ).read_text()
    )
    provider = BuiltinVaporPressureProvider(production_payload)
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=account_view,
            temperature_C=1650.0,
            pressure_bar=1.0e-6,
            control_inputs={"pO2_bar": 1.0e-9, "intrinsic_fO2_log": -9.0},
        )
    )
    oxide_activity = melt_oxide_activity("Al2O3", melt_mol)
    assert oxide_activity is not None
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["Al2O"]

    # 2 AlO1.5(l) -> Al2O(g)+O2 gives p proportional to a_AlO1.5^2.
    # Activity is dimensionless. Halving activity would quarter pressure; the
    # provider must not take sqrt(a) before the compiled evaluator squares it.
    assert provenance["activity_factor"] == pytest.approx(
        oxide_activity.activity**2
    )
    evaluator = provider._vapour_rail_catalog.evaluator_for("Al2O")
    expected = evaluator.evaluate(
        1923.15,
        source_activity=oxide_activity.activity,
        pO2_bar=1.0e-9,
    ).pressure_pa
    assert result.diagnostic["vapor_pressures_Pa"]["Al2O"] == pytest.approx(
        expected
    )


@pytest.mark.parametrize("pO2_bar", [-1.0, 0.0, 1e-12])
def test_explicit_transport_po2_rejects_invalid_or_subfloor_values(
    vapor_pressure_data,
    pO2_bar,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": pO2_bar},
    )

    with pytest.raises(ValueError, match="pO2_bar"):
        provider.dispatch(request)


def test_builtin_provider_marks_pure_component_range_extrapolation(
    vapor_pressure_data,
):
    assert vapor_pressure_data["metals"]["Ca"]["valid_range_K"] == [1115, 1757]
    result = _LegacyInternalAnalyticalModel(vapor_pressure_data)._internal_analytical_equilibrium()

    assert result.vapor_pressures_Pa["Ca"] > 0.0
    assert result.vapor_pressures_source["Ca"] == (
        "builtin_authoritative:pure_component_source_equation_fit:"
        "extrapolated_beyond_valid_range_K"
    )
    assert any(
        "Ca metal Antoine fit extrapolated beyond valid_range_K" in warning
        for warning in result.warnings
    )


def test_builtin_provider_exposes_consumable_ellingham_authority_flag(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    result = provider.dispatch(_ca_range_extrapolation_request())

    authority = result.diagnostic["ellingham_authority"]
    assert authority["consumer"] == "builtin-vapor-pressure"
    assert authority["status"] == "authoritative"
    assert authority[ELLINGHAM_AUTHORITY_LIMIT_FLAG] is False
    assert authority["extrapolated_beyond_fit_range_K"] == {}
    source_label = result.diagnostic["vapor_pressures_source"]["Ca"]
    assert source_label.startswith("builtin_authoritative:")
    assert source_label.endswith(
        "extrapolated_beyond_valid_range_K"
    )


def test_legacy_fallback_keeps_fe_pure_sidecar_above_pseudo_fit_range(
    vapor_pressure_data,
):
    fe_data = vapor_pressure_data["metals"]["Fe"]
    coefficients, coefficient_block = (
        vapor_pressure_module.vapor_pressure_antoine_coefficients(
            fe_data,
            temperature_K=2250.0,
        )
    )
    assert coefficients is fe_data["pure_component_antoine"]
    assert coefficient_block == vapor_pressure_module.COEFF_BLOCK_PURE_COMPONENT
    assert (
        vapor_pressure_module.vapor_pressure_valid_range_K(
            fe_data,
            coefficient_block,
            temperature_K=2250.0,
        )
        is None
    )

    result = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_FeBeyondEllinghamMelt(),
    )._internal_analytical_equilibrium()

    assert result.vapor_pressures_Pa["Fe"] > 0.0
    assert result.vapor_pressures_source["Fe"] == (
        "builtin_authoritative:pure_component_derived_from_evaluation"
    )
    assert not any(
        "species=Fe" in warning
        for warning in result.warnings
    )


def test_legacy_fallback_grounds_mn_liquid_oxide_standard_reaction(
    vapor_pressure_data,
):
    legacy_model = _LegacyInternalAnalyticalModel(vapor_pressure_data, melt=_MnOnlyMelt())

    result = legacy_model._internal_analytical_equilibrium()

    assert result.vapor_pressures_Pa["Mn"] > 0.0
    # Pairing fix: Mn oxide-coupled path is liquid_oxide_standard_reaction.
    # Pure-component Mn sidecars remain NBP/NIST ground-truth only.
    assert "liquid_oxide_standard_reaction" in result.vapor_pressures_source["Mn"]


def test_active_provider_labels_mn_liquid_oxide_standard_reaction_end_to_end(
    vapor_pressure_data,
):
    result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        _mn_vapor_request_at_T_K(1873.15)
    )

    assert result.status == "ok"
    source = result.diagnostic["vapor_pressures_source"]["Mn"]
    assert source == "builtin_authoritative:liquid_oxide_standard_reaction"
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]["Mn"]
    assert provenance["pressure_rail"] == "liquid_oxide_standard_reaction"
    assert provenance["oxide_standard_state"] == "liquid"
    assert provenance["P_eq_Pa"] > 0.0


def test_legacy_fallback_distinguishes_pseudo_fit_from_standard_reaction_source(
    vapor_pressure_data,
):
    class _FeOnlyMelt:
        temperature_C = 1600.0
        p_total_mbar = 1e-3
        melt_fO2_log = -9.0

        def composition_wt_pct(self):
            return {"FeO": 100.0}

    metal_data = {
        "metals": {
            "Fe": {
                "parent_oxide": "FeO",
                "fit_target": "pseudo_psat_backsolved_from_vaporock",
                "residual_dex": 1.4,
                "confidence_tier": "low",
                "antoine": {"A": 5.0, "B": 0.0, "C": 0.0},
            }
        },
        "oxide_vapors": {},
    }
    metal_result = _LegacyInternalAnalyticalModel(
        metal_data,
        # t-006 rail-aware refusals: the condensed-phase pseudo case uses Fe;
        # the K gas-phase pseudo regression lives in its own test below.
        melt=_FeOnlyMelt(),
    )._internal_analytical_equilibrium()
    oxide_result = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_SiOnlyMelt(),
    )._internal_analytical_equilibrium()

    fe_source = metal_result.vapor_pressures_source["Fe"]
    assert fe_source == (
        "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit"
    )
    assert "builtin_authoritative" not in fe_source

    assert oxide_result.vapor_pressures_source["SiO"] == (
        "builtin_authoritative:standard_reaction_term"
    )


def test_builtin_provider_marks_mn_above_liquid_oxide_fit_range_extrapolation(
    vapor_pressure_data,
):
    legacy_model = _LegacyInternalAnalyticalModel(
        vapor_pressure_data,
        melt=_MnAboveNbpMelt(),
    )

    result = legacy_model._internal_analytical_equilibrium()

    assert result.vapor_pressures_Pa["Mn"] > 0.0
    # Above liquid_oxide_standard_reaction valid_range_K upper edge, label
    # still names the liquid-oxide rail (not pure-component Ellingham).
    assert "liquid_oxide_standard_reaction" in result.vapor_pressures_source["Mn"]
    assert "extrapolated_beyond_valid_range_K" in result.vapor_pressures_source["Mn"]


def test_active_si_composite_supersedes_legacy_pure_component_sidecar(
    vapor_pressure_data,
):
    assert (
        vapor_pressure_data["metals"]["Si"]["consumer_status"].lower()
        == "active"
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=_SiOnlyMelt.temperature_C,
        pressure_bar=1e-6,
        control_inputs={"pO2_bar": 1e-9},
    )

    kernel_vp = dict(
        (provider.dispatch(request).diagnostic or {}).get("vapor_pressures_Pa")
        or {}
    )
    legacy_vp = dict(
        _LegacyInternalAnalyticalModel(
            vapor_pressure_data,
            melt=_SiOnlyMelt(),
        )._internal_analytical_equilibrium().vapor_pressures_Pa
        or {}
    )

    assert "SiO" in kernel_vp
    assert kernel_vp["Si"] > 0.0
    assert legacy_vp["Si"] > kernel_vp["Si"] * 1.0e6
    assert kernel_vp["Si"] == pytest.approx(2.9468548936088294e-07)
    assert set(legacy_vp) <= set(kernel_vp)
    assert set(kernel_vp) - set(legacy_vp) == {"Si2", "Si3", "SiO2_gas"}


def test_transport_po2_and_intrinsic_melt_fo2_are_independent(
    vapor_pressure_data,
):
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    reduced_redox = _si_only_transport_redox_request(
        transport_pO2_bar=1e-6,
        intrinsic_fO2_log=-12.0,
    )
    oxidized_redox = _si_only_transport_redox_request(
        transport_pO2_bar=1e-6,
        intrinsic_fO2_log=-4.0,
    )

    assert provider._resolve_transport_pO2_bar(reduced_redox) == pytest.approx(
        1e-6
    )
    assert provider._resolve_transport_pO2_bar(oxidized_redox) == pytest.approx(
        1e-6
    )
    assert provider._resolve_intrinsic_melt_fO2_log(
        reduced_redox
    ) == pytest.approx(-12.0)
    assert provider._resolve_intrinsic_melt_fO2_log(
        oxidized_redox
    ) == pytest.approx(-4.0)

    reduced_result = provider.dispatch(reduced_redox)
    oxidized_result = provider.dispatch(oxidized_redox)
    reduced_diag = dict(reduced_result.diagnostic or {})
    oxidized_diag = dict(oxidized_result.diagnostic or {})
    reduced_vp = dict(reduced_diag.get("vapor_pressures_Pa") or {})
    oxidized_vp = dict(oxidized_diag.get("vapor_pressures_Pa") or {})

    assert reduced_diag["pO2_bar"] == pytest.approx(1e-6)
    assert oxidized_diag["pO2_bar"] == pytest.approx(1e-6)
    assert reduced_vp["SiO"] == pytest.approx(oxidized_vp["SiO"])

    lower_transport = _si_only_transport_redox_request(
        transport_pO2_bar=1e-9,
        intrinsic_fO2_log=-12.0,
    )
    assert provider._resolve_intrinsic_melt_fO2_log(
        lower_transport
    ) == pytest.approx(
        provider._resolve_intrinsic_melt_fO2_log(reduced_redox)
    )
    lower_transport_result = provider.dispatch(lower_transport)
    lower_transport_diag = dict(lower_transport_result.diagnostic or {})
    lower_transport_vp = dict(
        lower_transport_diag.get("vapor_pressures_Pa") or {}
    )

    assert lower_transport_diag["pO2_bar"] == pytest.approx(1e-9)
    assert lower_transport_vp["SiO"] > reduced_vp["SiO"]


def test_fe_activity_uses_kress91_only_with_explicit_intrinsic_channel(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    no_channel = _fe_redox_request(
        species_formula_registry=sim.species_formula_registry,
        intrinsic_fO2_log=None,
        fO2_log=-4.0,
    )
    reduced = _fe_redox_request(
        species_formula_registry=sim.species_formula_registry,
        intrinsic_fO2_log=-12.0,
    )
    oxidized = _fe_redox_request(
        species_formula_registry=sim.species_formula_registry,
        intrinsic_fO2_log=-4.0,
    )

    no_channel_result = provider.dispatch(no_channel)
    reduced_result = provider.dispatch(reduced)
    oxidized_result = provider.dispatch(oxidized)
    comp_wt = composition_wt_pct_from_account_view(
        reduced.account_view,
        "process.cleaned_melt",
    )
    static_activity = comp_wt["FeO"] / 100.0
    reduced_activity = reduced_result.diagnostic["activities"]["Fe"]
    oxidized_activity = oxidized_result.diagnostic["activities"]["Fe"]

    # Documented degraded public-caller path: wt-frac remains reachable when
    # intrinsic_fO2_log is absent, but must be typed (not silent).
    no_channel_prov = no_channel_result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["Fe"]
    assert no_channel_result.diagnostic["activities"]["Fe"] == pytest.approx(
        static_activity
    )
    assert no_channel_prov["degraded_activity_basis"] == "feo_weight_fraction"
    assert no_channel_prov["activity_basis"] == "feo_weight_fraction"
    assert any(
        "degraded_activity_basis=feo_weight_fraction" in warning
        for warning in no_channel_result.warnings
    )
    assert reduced_activity == pytest.approx(
        kress91_ferrous_feo_activity(
            comp_wt=comp_wt,
            fO2_log=-12.0,
            T_K=1773.15,
            pressure_bar=reduced.pressure_bar,
        )
    )
    assert oxidized_activity == pytest.approx(
        kress91_ferrous_feo_activity(
            comp_wt=comp_wt,
            fO2_log=-4.0,
            T_K=1773.15,
            pressure_bar=oxidized.pressure_bar,
        )
    )
    reduced_prov = reduced_result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]["Fe"]
    assert reduced_prov["activity_basis"] == "kress91_ferrous"
    assert reduced_prov["degraded_activity_basis"] is None
    assert reduced_activity > static_activity
    assert oxidized_activity < reduced_activity


def test_kress91_ferrous_feo_activity_no_iron_guards() -> None:
    assert kress91_ferrous_feo_activity(
        comp_wt={},
        fO2_log=-9.0,
        T_K=1773.15,
        pressure_bar=1e-6,
    ) == 0.0
    assert kress91_ferrous_feo_activity(
        comp_wt={"SiO2": 100.0},
        fO2_log=-9.0,
        T_K=1773.15,
        pressure_bar=1e-6,
    ) == 0.0


# ---------------------------------------------------------------------------
# 2. Kernel filter scopes the account view
# ---------------------------------------------------------------------------


def test_kernel_filters_provider_to_cleaned_melt_only(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Even when other accounts hold material, the provider must see only
    ``process.cleaned_melt`` — the kernel account filter is the enforcer."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    # Seed an unrelated account so the filter has something to drop.
    sim.atom_ledger.load_external(
        "process.metal_phase", {"Fe": 0.5}, source="test seed",
        material_origin="feedstock",
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = BuiltinVaporPressureProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    BuiltinVaporPressureProvider.dispatch = _spying_dispatch
    try:
        sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=1400.0,
            pressure_bar=1e-6,
            control_inputs={"pO2_bar": 1e-9},
        )
    finally:
        BuiltinVaporPressureProvider.dispatch = original_dispatch

    assert seen_accounts, "provider was never dispatched"
    for accounts in seen_accounts:
        assert accounts == frozenset({"process.cleaned_melt"}), (
            "kernel filter leaked an undeclared account into the provider"
        )


# ---------------------------------------------------------------------------
# 3. Provider returns the same values as the legacy stub for a known input
# ---------------------------------------------------------------------------


def test_provider_matches_legacy_internal_analytical_for_known_lunar_composition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """Direct unit-level parity: build a simulator, advance into a campaign
    where the melt has been heated above the 400 K early-exit, then assert
    every species emitted by the legacy path is reproduced by the kernel
    within tolerance."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)
    # Step a few hours to heat the melt; both paths short-circuit below
    # 400 K, so an exact match before the ramp is uninteresting.
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    while sim.melt.temperature_C < 600.0:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()

    legacy_result = sim._internal_analytical_equilibrium()
    kernel_result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        control_inputs={
            "pO2_bar": sim._headspace_transport_pO2_bar(),
            "intrinsic_fO2_log": sim.melt.melt_fO2_log,
            "process_phase": "stage0",
        },
    )
    kernel_vp = dict(
        (kernel_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )

    assert legacy_result.vapor_pressures_Pa, (
        "legacy stub returned no vapor pressures — the test fixture is not "
        "exercising the path it claims to cover"
    )
    for species, legacy_value in legacy_result.vapor_pressures_Pa.items():
        if species in _REVIEWED_ADDITIVE_CARRIERS:
            continue
        kernel_value = kernel_vp.get(species, 0.0)
        tol = max(
            _VP_TOLERANCE_ABS_PA,
            _VP_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
        )
        assert abs(kernel_value - legacy_value) <= tol, (
            f"vapor pressure for {species!r} disagrees: legacy={legacy_value:.6g} Pa "
            f"kernel={kernel_value:.6g} Pa (tol={tol:.3g} Pa)"
        )

    # The compiled rail now owns additive NASA carrier rows that the frozen
    # pre-rail stub cannot evaluate. Si is also a reviewed replacement: MC-4b
    # supersedes its pure-component sidecar with the SiO-composed melt-source
    # reaction. All unreviewed existing-species parity remains exact.
    legacy_species = set(legacy_result.vapor_pressures_Pa)
    kernel_species = set(kernel_vp)
    assert legacy_species <= kernel_species
    assert kernel_species - legacy_species <= _REVIEWED_ADDITIVE_CARRIERS
    phosphorus_carriers = {
        "PO",
        "PO2",
        "P2",
        "P4",
        "P4O6",
        "P4O10",
        # 2026-08-05 b-133 adjudication: P2O5_gas tombstone RESTORED (the
        # wave-1B reactivation was wrong) -- it is no longer a kernel carrier.
    }
    assert phosphorus_carriers <= kernel_species
    assert all(kernel_vp[species] > 0.0 for species in phosphorus_carriers)


# ---------------------------------------------------------------------------
# 4. Shadow-parity smoke run across lunar + Mars + asteroid feedstocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feedstock_key, additives_kg",
    [
        ("lunar_mare_low_ti", None),
        ("mars_basalt", {"C": 60.0}),
        ("s_type_asteroid_silicate", None),
    ],
)
def test_shadow_parity_across_short_simulation_run(
    feedstock_key,
    additives_kg,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """For each feedstock, drive the simulator through the C0-into-C2A
    handoff and assert the legacy stub and the kernel dispatch agree at
    every step within tolerance.

    This is the parity gate that justified flipping the VAPOR_PRESSURE
    intent. Keeping it in the suite catches future regressions if a
    later intent flip changes the kernel call shape.
    """

    sim = _build_sim(
        feedstock_key,
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg=additives_kg,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    worst_delta_pa = 0.0
    while not sim.is_complete() and steps < 60:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()
        steps += 1

        # Compare the legacy and kernel paths at this tick.
        T_C = sim.melt.temperature_C
        if T_C + 273.15 < 400:
            continue
        legacy_result = sim._internal_analytical_equilibrium()
        kernel_result = sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=T_C,
            pressure_bar=sim.melt.p_total_mbar / 1000.0,
            control_inputs={
                "pO2_bar": sim._headspace_transport_pO2_bar(),
                "intrinsic_fO2_log": sim.melt.melt_fO2_log,
            },
        )
        kernel_vp = dict(
            (kernel_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
        )
        legacy_vp = dict(legacy_result.vapor_pressures_Pa or {})
        kernel_only = set(kernel_vp) - set(legacy_vp)
        assert kernel_only <= _REVIEWED_ADDITIVE_CARRIERS
        parity_species = (set(legacy_vp) | set(kernel_vp)) - (
            _REVIEWED_ADDITIVE_CARRIERS
        )
        for species in parity_species:
            legacy_value = float(legacy_vp.get(species, 0.0))
            kernel_value = float(kernel_vp.get(species, 0.0))
            delta = abs(legacy_value - kernel_value)
            tol = max(
                _VP_TOLERANCE_ABS_PA,
                _VP_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
            )
            worst_delta_pa = max(worst_delta_pa, delta)
            assert delta <= tol, (
                f"parity broke for {species!r} at step {steps} "
                f"(T={T_C:.1f} C, feedstock={feedstock_key}): "
                f"legacy={legacy_value:.6g} Pa kernel={kernel_value:.6g} Pa "
                f"delta={delta:.6g} Pa tol={tol:.6g} Pa"
            )

    assert steps > 0, f"smoke run for {feedstock_key} executed zero steps"
    # Sanity: the worst-case observed delta must be at most the largest
    # tolerance band the loop allowed. This is implied by the per-tick
    # assertion but pinned explicitly so the test is self-documenting
    # about what "parity" meant numerically.
    assert worst_delta_pa <= 1.0, (
        f"worst observed parity delta {worst_delta_pa:.6g} Pa is "
        f"suspiciously large for a refactor-only change"
    )


# ---------------------------------------------------------------------------
# 5. The flip is wired: result.vapor_pressures_Pa traces back to the kernel
# ---------------------------------------------------------------------------


def test_get_equilibrium_returns_kernel_vapor_pressures(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """After a successful equilibrium call, the EquilibriumResult's
    vapor_pressures_Pa must match what the kernel dispatch would return.

    Belt-and-braces: catches a future refactor that bypasses the kernel
    in the legacy path."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    while sim.melt.temperature_C < 700.0:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()

    result = sim._get_equilibrium()
    kernel_dispatch = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        control_inputs={
            "pO2_bar": sim._headspace_transport_pO2_bar(),
            "intrinsic_fO2_log": sim.melt.melt_fO2_log,
            "process_phase": "stage0",
        },
    )
    kernel_vp = dict(
        (kernel_dispatch.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )

    # If the kernel produced any vapor pressures, the equilibrium result
    # must mirror them — this is exactly the flip.
    if kernel_vp:
        assert set(result.vapor_pressures_Pa) == set(kernel_vp)
        for species, kernel_value in kernel_vp.items():
            assert result.vapor_pressures_Pa[species] == pytest.approx(
                kernel_value
            )


# ---------------------------------------------------------------------------
# 6. The provider returns transition=None (VAPOR_PRESSURE is diagnostic)
# ---------------------------------------------------------------------------


def test_provider_emits_no_ledger_transition(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    """VAPOR_PRESSURE owns no ledger mutation — that belongs to
    EVAPORATION_TRANSITION. The provider must always leave the result
    transition None."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C0)
    decision_choice = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: "A",
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    # Heat the melt and dispatch.
    while sim.melt.temperature_C < 700.0:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = decision_choice.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        sim.step()

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        control_inputs={"pO2_bar": sim._commanded_pO2_bar()},
    )
    assert result.transition is None, (
        "VAPOR_PRESSURE is diagnostic per binding spec §3 — provider must "
        "never emit a LedgerTransitionProposal"
    )


# ---------------------------------------------------------------------------
# 7. Below 400 K, both paths return an empty vapor-pressure dict
# ---------------------------------------------------------------------------


def test_provider_short_circuits_below_400_k(vapor_pressure_data):
    """The legacy stub returns an empty result below 400 K (no
    significant evaporation). The provider must do the same so the
    pre-heat hours of every batch stay numerically identical."""

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "FeO": 1.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=view,
        temperature_C=25.0,  # Well below 400 K
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={"pO2_bar": 1e-9},
    )
    result = provider.dispatch(request)
    assert result.status == "ok"
    assert (result.diagnostic or {}).get("vapor_pressures_Pa") == {}


def test_inactive_metal_consumer_status_suppresses_builtin_fallback(
    vapor_pressure_data,
):
    data = copy.deepcopy(vapor_pressure_data)
    data["metals"]["Si"]["consumer_status"] = "inactive"
    provider = BuiltinVaporPressureProvider(data)
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={"pO2_bar": 1e-9},
    )

    inactive_result = provider.dispatch(request)
    inactive_vp = dict(
        (inactive_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )
    assert "SiO" in inactive_vp
    assert "Si" not in inactive_vp

    data["metals"]["Si"].pop("consumer_status", None)
    active_result = BuiltinVaporPressureProvider(data).dispatch(request)
    active_vp = dict(
        (active_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )
    assert active_vp.get("Si", 0.0) > 1e-15


# ---------------------------------------------------------------------------
# 8. Fail-closed on an unregistered species in process.cleaned_melt
# ---------------------------------------------------------------------------


def test_vapor_pressure_provider_raises_on_unregistered_species_in_view(
    vapor_pressure_data,
):
    """An unresolvable species in ``process.cleaned_melt`` must raise.

    The legacy provider used to ``continue`` past species whose formula
    could not be resolved, silently biasing the activity proxy by
    dropping their mass from ``total_kg``. The fail-closed behaviour
    aligns the provider with :meth:`PyrolysisSimulator._load_ledger_account`,
    which already raises :class:`AccountingError` on the same condition
    at Stage 0. Both paths into the ledger now have the same surface.
    """

    provider = BuiltinVaporPressureProvider(vapor_pressure_data)
    # registry is intentionally empty so even SiO2 has no resolvable
    # formula -- the provider must reject the view, not silently drop
    # the unknown species and emit an activity-biased result.
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"SiO2": 10.0, "UNOBTAINIUM": 1.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=view,
        temperature_C=1500.0,  # Above the 400 K early-exit
        pressure_bar=1e-6,
        fO2_log=None,
        control_inputs={"pO2_bar": 1e-9},
    )

    with pytest.raises(AccountingError):
        provider.dispatch(request)
_REVIEWED_ADDITIVE_CARRIERS = {
    "Al2",
    "Al2O2",
    "Al2O3_gas",
    "AlO2",
    "Ca2",
    "CrO",
    "CrO2",
    "TiO",
    "TiO2_gas",
    "CaO_gas",
    "AlO",
    "Al2O",
    "CrO3",
    "PO",
    "PO2",
    "P2",
    "P4",
    "P4O6",
    "P4O10",
    # 2026-08-05 MC-4b: exact gas-exchange compositions on the landed
    # K/Mg/Na/Si standard-reaction rows.  (P2O5 phase transfer was dropped
    # again by the 2026-08-05 b-133 adjudication: the P2O5_gas tombstone
    # is RESTORED, so P2O5_gas is not an additive carrier.)
    "K2",
    "K2O_gas",
    "Mg2",
    "MgO_gas",
    "Na2",
    "Na2O_gas",
    "Si",
    "Si2",
    "Si3",
    "SiO2_gas",
}
