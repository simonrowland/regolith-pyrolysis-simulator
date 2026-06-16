"""Tests for the BuiltinVaporPressureProvider — first intent flip of
\\goal BUILTIN-ENGINE-EXTRACTION (#7).

Covers:

* Unit: the provider returns the same vapor pressures as the legacy
  :meth:`EquilibriumMixin._stub_equilibrium` for a known composition + T.
* Unit: the kernel filter actually scopes the provider's account view to
  the single declared account (``process.cleaned_melt``).
* Unit: capability profile declares ``VAPOR_PRESSURE`` only and is
  authoritative for it.
* Shadow parity: across a multi-step simulation run on lunar + Mars +
  asteroid feedstocks, the legacy ``_stub_equilibrium`` and the kernel
  dispatch agree species-by-species within 1e-9 Pa (relative + absolute
  floor). This is the parity gate that justified the flip; it stays in
  the suite as a regression guard against future intent flips that touch
  the same call site.
"""

from __future__ import annotations

import copy
import math

import pytest

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    ELLINGHAM_FIT_RANGE_K,
    _ELLINGHAM_THERMO,
)
from simulator.equilibrium import EquilibriumMixin
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    IntentRequest,
    ProviderRegistry,
)
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.state import CampaignPhase, DecisionType
from tests.chemistry.conftest import _build_sim


_VP_TOLERANCE_REL = 1e-9
_VP_TOLERANCE_ABS_PA = 1e-9
_CA_RANGE_EXTRAPOLATION_T_K = 2000.0

_V1C_JANAF_ELLINGHAM = {
    "Na": (-1135.130, -0.537417, 4, 2),
    "K": (-975.838, -0.520580, 4, 2),
    "Fe": (-538.946, -0.125272, 2, 2),
    "Cr": (-748.076, -0.168676, 4 / 3, 2 / 3),
    "Mg": (-1342.444, -0.336009, 2, 2),
    "Ca": (-1285.155, -0.222295, 2, 2),
    "Al": (-1126.073, -0.218805, 4 / 3, 2 / 3),
    "Ti": (-939.632, -0.177149, 1, 1),
    "Si": (-910.940, -0.182400, 1, 1),
    # Mn updated 0.5.2 (2026-05-27) to a proper high-T linear refit
    # anchored on Mn(l) above the solid→liquid transition at 1517 K
    # (Chase 1998, Mn-008 + phase transition data). See
    # simulator/equilibrium.py::_ELLINGHAM_THERMO for the full
    # rationale.
    "Mn": (-794.540, -0.165650, 2, 2),
}


def test_ellingham_table_matches_v1c_janaf_refit():
    assert _ELLINGHAM_THERMO == _V1C_JANAF_ELLINGHAM
    assert EquilibriumMixin._ELLINGHAM_THERMO == _V1C_JANAF_ELLINGHAM


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


class _CaOnlyMelt:
    temperature_C = _CA_RANGE_EXTRAPOLATION_T_K - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"CaO": 100.0}


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


class _SiOnlyMelt:
    temperature_C = 1900.0 - 273.15
    p_total_mbar = 1e-3

    def composition_wt_pct(self):
        return {"SiO2": 100.0}


class _LegacyFallbackStub(EquilibriumMixin):
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
    assert tuple(extrapolation["fit_range_K"]) == ELLINGHAM_FIT_RANGE_K
    assert any(
        "Fe Ellingham JANAF high-T fit extrapolated beyond fit_range_K"
        in warning
        for warning in result.warnings
    )


def test_legacy_fallback_marks_metal_antoine_range_extrapolation(
    vapor_pressure_data,
):
    assert vapor_pressure_data["metals"]["Ca"]["valid_range_K"] == [1115, 1757]
    result = _LegacyFallbackStub(vapor_pressure_data)._stub_equilibrium()

    assert result.vapor_pressures_Pa["Ca"] > 0.0
    assert (
        result.vapor_pressures_source["Ca"]
        == "builtin_fallback:pure_component_first_principles:extrapolated_beyond_valid_range_K"
    )
    assert any(
        "Ca metal Antoine fit extrapolated beyond valid_range_K" in warning
        for warning in result.warnings
    )


def test_legacy_fallback_grounds_mn_liquid_source_band(
    vapor_pressure_data,
):
    stub = _LegacyFallbackStub(vapor_pressure_data, melt=_MnOnlyMelt())

    result = stub._stub_equilibrium()

    assert result.vapor_pressures_Pa["Mn"] > 0.0
    assert (
        result.vapor_pressures_source["Mn"]
        == "builtin_fallback:pure_component_derived_from_evaluation"
    )


def test_legacy_fallback_downgrades_mn_above_nbp_source_extrapolation(
    vapor_pressure_data,
):
    stub = _LegacyFallbackStub(vapor_pressure_data, melt=_MnAboveNbpMelt())

    result = stub._stub_equilibrium()

    assert result.vapor_pressures_Pa["Mn"] > 0.0
    assert result.vapor_pressures_source["Mn"] == (
        "builtin_fallback:pure_component_extrapolated:"
        "extrapolated_beyond_source_certified_range_K:"
        "extrapolated_beyond_valid_range_K"
    )


def test_inactive_metal_species_do_not_diverge_between_provider_and_legacy(
    vapor_pressure_data,
):
    assert (
        vapor_pressure_data["metals"]["Si"]["consumer_status"].lower()
        == "inactive"
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
        _LegacyFallbackStub(
            vapor_pressure_data,
            melt=_SiOnlyMelt(),
        )._stub_equilibrium().vapor_pressures_Pa
        or {}
    )

    assert "SiO" in kernel_vp
    assert "Si" not in kernel_vp
    assert "Si" not in legacy_vp
    assert set(legacy_vp) == set(kernel_vp)


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
        "process.metal_phase", {"Fe": 0.5}, source="test seed"
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


def test_provider_matches_legacy_stub_for_known_lunar_composition(
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

    legacy_result = sim._stub_equilibrium()
    kernel_result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=sim.melt.temperature_C,
        pressure_bar=sim.melt.p_total_mbar / 1000.0,
        control_inputs={"pO2_bar": sim._commanded_pO2_bar()},
    )
    kernel_vp = dict(
        (kernel_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
    )

    assert legacy_result.vapor_pressures_Pa, (
        "legacy stub returned no vapor pressures — the test fixture is not "
        "exercising the path it claims to cover"
    )
    for species, legacy_value in legacy_result.vapor_pressures_Pa.items():
        kernel_value = kernel_vp.get(species, 0.0)
        tol = max(
            _VP_TOLERANCE_ABS_PA,
            _VP_TOLERANCE_REL * max(abs(legacy_value), abs(kernel_value)),
        )
        assert abs(kernel_value - legacy_value) <= tol, (
            f"vapor pressure for {species!r} disagrees: legacy={legacy_value:.6g} Pa "
            f"kernel={kernel_value:.6g} Pa (tol={tol:.3g} Pa)"
        )

    assert set(kernel_vp) == set(legacy_result.vapor_pressures_Pa), (
        "legacy/kernel vapor-pressure species sets diverged: "
        f"legacy_only={set(legacy_result.vapor_pressures_Pa) - set(kernel_vp)} "
        f"kernel_only={set(kernel_vp) - set(legacy_result.vapor_pressures_Pa)}"
    )


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
        legacy_result = sim._stub_equilibrium()
        kernel_result = sim._chem_kernel.dispatch(
            ChemistryIntent.VAPOR_PRESSURE,
            temperature_C=T_C,
            pressure_bar=sim.melt.p_total_mbar / 1000.0,
            control_inputs={"pO2_bar": sim._commanded_pO2_bar()},
        )
        kernel_vp = dict(
            (kernel_result.diagnostic or {}).get("vapor_pressures_Pa") or {}
        )
        legacy_vp = dict(legacy_result.vapor_pressures_Pa or {})
        for species in set(legacy_vp) | set(kernel_vp):
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
        control_inputs={"pO2_bar": sim._commanded_pO2_bar()},
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
