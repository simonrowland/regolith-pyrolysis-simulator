"""Chunk 2: phase-home cohorts and the scalar-F switch.

The 50 kg hour is a worked example, not a runner golden. Expected
masses come from the signed contract written in this file, and oxide
moles come from kilograms divided by ``parse_formula``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.alphamelts.provider import AlphaMELTSProvider
from simulator.accounting.formulas import parse_formula
from simulator.accounting.ledger import (
    KNOWN_LEDGER_ACCOUNTS,
    KNOWN_LEDGER_ACCOUNT_PREFIXES,
    AtomLedger,
)
from simulator.accounting.oxide_assignment import PHASE_OXIDE_MASS_ABS_TOLERANCE_KG
from simulator.accounting.phase_homes import (
    MELTS_BINDING,
    REASON_LIQUID_INSUFFICIENT,
    crystal_accounts_for_binding,
    holds_positive_crystal_moles,
    locked_cohort_update,
    locked_cohorts,
    signed_locked_masses,
)
from simulator.chemistry.kernel import (
    AccountFilterViolation,
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
    ProviderRegistry,
    ProviderUnavailableError,
)
from simulator.melt_backend.base import EquilibriumResult, LiquidFractionInvalidError
from simulator.melt_backend.liquidus import LiquidusSampleError, LiquidusSolidusResult
from simulator.state import CampaignPhase
from tests.chemistry.test_evaporation_freeze_gate import _build_freeze_gate_sim
from tests.chemistry.test_partial_melt_offgassing_diagnostic import (
    _install_eligible_vapour_batch,
)
from tests.chemistry.test_t1154_chunk1_freeze_gate_pin import (
    _BULK_X,
    _FLUX_STUB_KG_HR,
    _MELT_FRACTION_F,
    _SHADOW_FULL_KG_HR,
    _VAPOR_PA,
    _install_curve,
    _shadow_rates,
)

_OLIVINE_0 = "process.crystal.melts.olivine.0"
_OLIVINE_1 = "process.crystal.melts.olivine.1"
_LIQUID = "process.cleaned_melt"


def _signed(m_locked_kg: float, m_growth_kg: float, m_eq_kg: float) -> tuple[float, float]:
    """Independent contract: dissolution and growth are never both positive."""
    dissolve = max(0.0, m_locked_kg - m_eq_kg)
    m_new = max(0.0, min(m_growth_kg, m_eq_kg - m_locked_kg))
    return dissolve, m_new


def _kg_per_mol(species: str) -> float:
    return parse_formula(species).molar_mass_kg_per_mol()


def _moles(kilograms: float, species: str) -> float:
    return kilograms / _kg_per_mol(species)


def _elements(mol_by_account: dict) -> dict[str, float]:
    totals: dict[str, float] = {}
    for species_mol in mol_by_account.values():
        for species, moles in species_mol.items():
            if float(moles) == 0.0:
                continue
            formula = parse_formula(str(species))
            for element, count in formula.elements.items():
                totals[element] = totals.get(element, 0.0) + float(count) * float(moles)
    return totals


def _apply_proposal(before: dict, proposal: LedgerTransitionProposal) -> dict:
    after = {account: dict(species) for account, species in before.items()}
    for account, species_mol in proposal.debits.items():
        bucket = after.setdefault(account, {})
        for species, moles in species_mol.items():
            bucket[species] = bucket.get(species, 0.0) - float(moles)
    for account, species_mol in proposal.credits.items():
        bucket = after.setdefault(account, {})
        for species, moles in species_mol.items():
            bucket[species] = bucket.get(species, 0.0) + float(moles)
    return after


def _strict_ledger() -> AtomLedger:
    return AtomLedger(
        allowed_accounts=KNOWN_LEDGER_ACCOUNTS,
        allowed_account_prefixes=KNOWN_LEDGER_ACCOUNT_PREFIXES,
    )


def _phase(name: str, mass_kg: float, oxide_mol: dict[str, float]) -> dict:
    return {"phase": name, "mass_kg": mass_kg, "oxide_mol": dict(oxide_mol)}


class _GrowthBackend:
    """Constant half-liquid path. The current-temperature row is 0.01 kg."""

    def __init__(self, *, refuse_spinel: bool = False) -> None:
        self._mode = "python_api"
        self.calls: list[dict] = []
        self.refuse_spinel = refuse_spinel

    def is_available(self) -> bool:
        return True

    def get_engine_version(self) -> str:
        return "chunk2-growth"

    def find_liquidus_solidus(self, **_kwargs):
        return LiquidusSolidusResult(
            liquidus_T_C=1300.0,
            solidus_T_C=1000.0,
            liquid_fraction=1.0,
            status="ok",
        )

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        temperature_C = float(kwargs["temperature_C"])
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        masses = {"liquid": 1.0}
        compositions = {"liquid": {"SiO2": 60.0, "MgO": 40.0}}
        present = ["liquid"]
        if abs(temperature_C - 1150.0) < 1e-6:
            masses = {"olivine": 0.01}
            compositions = {"olivine": {"SiO2": 40.0, "MgO": 60.0}}
            present = ["olivine"]
            if self.refuse_spinel:
                masses["spinel"] = 0.001
                compositions["spinel"] = {"fo": 100.0}
                present.append("spinel")
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=float(kwargs["pressure_bar"]),
            liquid_fraction=frac,
            liquid_composition_wt_pct={"SiO2": 60.0, "MgO": 40.0},
            phases_present=present,
            phase_masses_kg=masses,
            phase_compositions=compositions,
            fO2_log=float(kwargs["fO2_log"]),
            status="ok",
        )


class _RemeltBackend:
    """Accessible liquid is silica. The one MgO call is the locked probe."""

    def __init__(self, *, probe_solid_kg: float) -> None:
        self._mode = "python_api"
        self.calls: list[dict] = []
        self.probe_solid_kg = float(probe_solid_kg)

    def is_available(self) -> bool:
        return True

    def get_engine_version(self) -> str:
        return "chunk2-remelt"

    def find_liquidus_solidus(self, **_kwargs):
        return LiquidusSolidusResult(
            liquidus_T_C=1200.0,
            solidus_T_C=1000.0,
            liquid_fraction=1.0,
            status="ok",
        )

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        temperature_C = float(kwargs["temperature_C"])
        composition = kwargs["composition_mol_by_account"][_LIQUID]
        has_mgo = float(composition.get("MgO", 0.0) or 0.0) > 0.0
        if temperature_C <= 1000.0:
            frac = 0.0
        elif temperature_C >= 1200.0:
            frac = 1.0
        else:
            frac = (temperature_C - 1000.0) / 200.0
        masses = {"liquid": 50.0}
        compositions = {"liquid": {"SiO2": 100.0}}
        present = ["liquid"]
        if has_mgo and self.probe_solid_kg > 0.0:
            masses = {
                "liquid": 50.0,
                "olivine": self.probe_solid_kg,
            }
            compositions = {
                "liquid": {"SiO2": 100.0},
                "olivine": {"MgO": 100.0},
            }
            present = ["liquid", "olivine"]
            frac = 50.0 / (50.0 + self.probe_solid_kg)
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=float(kwargs["pressure_bar"]),
            liquid_fraction=frac,
            liquid_composition_wt_pct={"SiO2": 100.0},
            phases_present=present,
            phase_masses_kg=masses,
            phase_compositions=compositions,
            fO2_log=float(kwargs["fO2_log"]),
            status="ok",
        )

    def mgo_calls(self) -> list[dict]:
        found = []
        for call in self.calls:
            composition = call["composition_mol_by_account"][_LIQUID]
            if float(composition.get("MgO", 0.0) or 0.0) > 0.0:
                found.append(call)
        return found


def _register(backend, ledger: AtomLedger):
    provider = AlphaMELTSProvider(backend=backend)
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    return provider, kernel


def _dispatch(kernel: ChemistryKernel, temperature_C: float):
    return kernel.dispatch(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        temperature_C=temperature_C,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )


def test_signed_masses_match_the_contract_and_are_never_both_positive():
    cases = (
        (50.0, 15.0, 60.0, 0.0, 10.0),
        (50.0, 0.0, 40.0, 10.0, 0.0),
        (50.0, 0.0, 0.0, 50.0, 0.0),
        (50.0, 0.0, 50.0, 0.0, 0.0),
    )
    for m_locked, m_growth, m_eq, dissolve, m_new in cases:
        spec = _signed(m_locked, m_growth, m_eq)
        assert spec == pytest.approx((dissolve, m_new))
        assert signed_locked_masses(m_locked, m_growth, m_eq) == pytest.approx(spec)
        assert not (spec[0] > 0.0 and spec[1] > 0.0)


def test_partial_growth_opens_one_new_cohort_at_the_accessible_composition():
    m_locked, m_growth, m_eq = 50.0, 15.0, 60.0
    dissolve, m_new = _signed(m_locked, m_growth, m_eq)
    sio2 = _moles(7.5, "SiO2")
    mgo = _moles(7.5, "MgO")
    feo = _moles(60.0, "FeO")
    cohorts = locked_cohorts(
        {_OLIVINE_0: {"MgO": _moles(50.0, "MgO")}},
        MELTS_BINDING,
    )
    before = {
        _LIQUID: {"SiO2": sio2, "MgO": mgo},
        _OLIVINE_0: {"MgO": _moles(50.0, "MgO")},
    }
    update = locked_cohort_update(
        binding=MELTS_BINDING,
        liquid_oxide_mol=before[_LIQUID],
        accessible_phases=(_phase("olivine", m_growth, {"SiO2": sio2, "MgO": mgo}),),
        probe_phases=(_phase("olivine", m_eq, {"FeO": feo}),),
        locked=cohorts,
    )

    assert update.refusal_reason is None
    assert update.proposal is not None
    note = {row["phase"]: row for row in update.phases}
    assert note["olivine"]["dissolve_kg"] == pytest.approx(dissolve)
    assert note["olivine"]["m_new_kg"] == pytest.approx(m_new)
    assert _OLIVINE_0 not in update.proposal.debits
    assert _OLIVINE_0 not in update.proposal.credits
    fraction = m_new / m_growth
    credit = update.proposal.credits[_OLIVINE_1]
    assert credit["SiO2"] == pytest.approx(sio2 * fraction)
    assert credit["MgO"] == pytest.approx(mgo * fraction)
    assert "FeO" not in credit
    assert _elements(_apply_proposal(before, update.proposal)) == pytest.approx(
        _elements(before)
    )


def test_partial_dissolution_empties_the_youngest_cohort_first():
    cohorts = locked_cohorts(
        {
            _OLIVINE_0: {"SiO2": _moles(40.0, "SiO2")},
            _OLIVINE_1: {"MgO": _moles(10.0, "MgO")},
        },
        MELTS_BINDING,
    )
    before = {
        _LIQUID: {},
        _OLIVINE_0: {"SiO2": _moles(40.0, "SiO2")},
        _OLIVINE_1: {"MgO": _moles(10.0, "MgO")},
    }
    update = locked_cohort_update(
        binding=MELTS_BINDING,
        liquid_oxide_mol={},
        accessible_phases=(),
        probe_phases=(_phase("olivine", 40.0, {"SiO2": _moles(40.0, "SiO2")}),),
        locked=cohorts,
    )

    dissolve, m_new = _signed(50.0, 0.0, 40.0)
    assert update.proposal is not None
    note = {row["phase"]: row for row in update.phases}
    assert note["olivine"]["dissolve_kg"] == pytest.approx(dissolve)
    assert note["olivine"]["m_new_kg"] == pytest.approx(m_new)
    assert set(update.proposal.debits) == {_OLIVINE_1}
    assert update.proposal.debits[_OLIVINE_1]["MgO"] == pytest.approx(
        _moles(10.0, "MgO")
    )
    assert update.proposal.credits[_LIQUID]["MgO"] == pytest.approx(
        _moles(10.0, "MgO")
    )
    assert _elements(_apply_proposal(before, update.proposal)) == pytest.approx(
        _elements(before)
    )


def test_growth_the_liquid_cannot_fund_refuses_the_whole_commit():
    update = locked_cohort_update(
        binding=MELTS_BINDING,
        liquid_oxide_mol={"MgO": _moles(1.0, "MgO")},
        accessible_phases=(
            _phase("olivine", 1.0, {"SiO2": _moles(1.0, "SiO2")}),
        ),
        probe_phases=None,
        locked=(),
    )

    assert update.proposal is None
    assert update.refusal_reason == REASON_LIQUID_INSUFFICIENT


def test_dust_under_the_phase_floor_is_not_a_cohort_and_does_not_hold_f_off():
    tiny = (PHASE_OXIDE_MASS_ABS_TOLERANCE_KG * 0.5) / _kg_per_mol("MgO")
    accounts = {_OLIVINE_0: {"MgO": tiny}}
    assert holds_positive_crystal_moles(accounts) is False
    assert holds_positive_crystal_moles({_OLIVINE_0: {"MgO": 0.0}}) is False
    assert holds_positive_crystal_moles({_OLIVINE_0: {"MgO": 1.0}}) is True
    assert holds_positive_crystal_moles(
        {_OLIVINE_0: {"Not A Formula": 1.0}}
    ) is True
    assert crystal_accounts_for_binding(accounts, MELTS_BINDING) == (_OLIVINE_0,)
    assert locked_cohorts(accounts, MELTS_BINDING) == ()


def test_strict_ledger_accepts_the_crystal_prefix():
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _OLIVINE_0,
        {"MgO": 1.0},
        material_origin="feedstock",
    )
    assert ledger.mol_by_account(_OLIVINE_0)["MgO"] == pytest.approx(1.0)


def test_sync_admits_only_this_binding():
    provider = AlphaMELTSProvider(backend=None)
    provider.sync_crystal_accounts([
        _OLIVINE_0,
        "process.crystal.magemin.olivine.0",
        _LIQUID,
    ])
    assert provider.capability_profile().declared_accounts == frozenset({
        _LIQUID,
        _OLIVINE_0,
    })


def test_good_olivine_commits_a_new_cohort_and_a_bad_token_blocks_it():
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _LIQUID,
        {"SiO2": 1.0, "MgO": 1.0},
        material_origin="feedstock",
    )
    backend = _GrowthBackend()
    _provider, kernel = _register(backend, ledger)
    before = _elements(ledger.mol_by_account())

    result = _dispatch(kernel, 1150.0)

    assert result.status == "ok"
    assert result.transition is not None
    assert result.transition.reason == "phase_home"
    path = tuple(result.diagnostic["liquid_fraction_path"])
    assert len(backend.calls) == len(path) + 1
    kernel.commit_batch(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        result.transition,
    )
    expected = {
        "SiO2": _moles(0.40 * 0.01, "SiO2"),
        "MgO": _moles(0.60 * 0.01, "MgO"),
    }
    cohort = ledger.mol_by_account(_OLIVINE_0)
    liquid = ledger.mol_by_account(_LIQUID)
    assert cohort["SiO2"] == pytest.approx(expected["SiO2"])
    assert cohort["MgO"] == pytest.approx(expected["MgO"])
    assert liquid["SiO2"] == pytest.approx(1.0 - expected["SiO2"])
    assert liquid["MgO"] == pytest.approx(1.0 - expected["MgO"])
    assert _elements(ledger.mol_by_account()) == pytest.approx(before)

    refused = _strict_ledger()
    refused.load_external_mol(
        _LIQUID,
        {"SiO2": 1.0, "MgO": 1.0},
        material_origin="feedstock",
    )
    snapshot = {
        account: dict(species)
        for account, species in refused.mol_by_account().items()
    }
    bad = _GrowthBackend(refuse_spinel=True)
    provider, refused_kernel = _register(bad, refused)
    refused_result = _dispatch(refused_kernel, 1150.0)
    assert refused_result.status == "ok"
    assert refused_result.transition is None
    assert refused_result.diagnostic["backend_diagnostics"][
        "phase_home_refusal"
    ]["reason"] == "phase_home_inventory_refused"
    assert provider._crystal_accounts == set()
    assert {
        account: dict(species)
        for account, species in refused.mol_by_account().items()
    } == snapshot


def test_fifty_kilogram_remelt_uses_one_probe_and_the_hold_case_does_not():
    silica = _moles(50.0, "SiO2")
    magnesia = _moles(50.0, "MgO")

    def seed():
        ledger = _strict_ledger()
        ledger.load_external_mol(
            _LIQUID,
            {"SiO2": silica},
            material_origin="feedstock",
        )
        ledger.load_external_mol(
            _OLIVINE_0,
            {"MgO": magnesia},
            material_origin="feedstock",
        )
        return ledger

    ledger = seed()
    backend = _RemeltBackend(probe_solid_kg=0.0)
    provider, kernel = _register(backend, ledger)
    provider.sync_crystal_accounts(
        crystal_accounts_for_binding(ledger.mol_by_account(), MELTS_BINDING)
    )
    before = _elements(ledger.mol_by_account())
    result = _dispatch(kernel, 1400.0)

    assert result.status == "ok"
    assert len(backend.mgo_calls()) == 1
    assert backend.mgo_calls()[0]["temperature_C"] == pytest.approx(1400.0)
    phases = {
        row["phase"]: row
        for row in result.diagnostic["backend_diagnostics"]["phase_home"]["phases"]
    }
    assert phases["olivine"]["dissolve_kg"] == pytest.approx(_signed(50.0, 0.0, 0.0)[0])
    assert phases["olivine"]["m_new_kg"] == pytest.approx(0.0)
    assert result.transition is not None
    kernel.commit_batch(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        result.transition,
    )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(silica)
    assert ledger.mol_by_account(_LIQUID)["MgO"] == pytest.approx(magnesia)
    assert float(ledger.mol_by_account(_OLIVINE_0).get("MgO", 0.0)) == pytest.approx(
        0.0, abs=1e-9
    )
    assert holds_positive_crystal_moles(ledger.mol_by_account()) is False
    assert _elements(ledger.mol_by_account()) == pytest.approx(before)

    held = seed()
    hold_backend = _RemeltBackend(probe_solid_kg=50.0)
    hold_provider, hold_kernel = _register(hold_backend, held)
    hold_provider.sync_crystal_accounts(
        crystal_accounts_for_binding(held.mol_by_account(), MELTS_BINDING)
    )
    held_result = _dispatch(hold_kernel, 1400.0)
    assert len(hold_backend.mgo_calls()) == 1
    held_phases = {
        row["phase"]: row
        for row in held_result.diagnostic["backend_diagnostics"]["phase_home"]["phases"]
    }
    assert held_phases["olivine"]["m_eq_kg"] == pytest.approx(50.0)
    assert held_phases["olivine"]["dissolve_kg"] == pytest.approx(0.0)
    assert held_phases["olivine"]["m_new_kg"] == pytest.approx(0.0)
    assert "phase_home_refusal" not in held_result.diagnostic["backend_diagnostics"]
    assert held_result.transition is None
    assert held.mol_by_account(_OLIVINE_0)["MgO"] == pytest.approx(magnesia)
    assert held.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(silica)
    assert float(held.mol_by_account(_LIQUID).get("MgO", 0.0)) == pytest.approx(0.0)


class _AdmitDuringDispatch(ChemistryProvider):
    name = "admit-during-dispatch"

    def __init__(self) -> None:
        self.extra: set[str] = set()

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            declared_accounts=frozenset({_LIQUID, *self.extra}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.extra.add(_OLIVINE_0)
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={_LIQUID: {"SiO2": 0.25}},
                credits={_OLIVINE_0: {"SiO2": 0.25}},
                reason="phase_home",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


class _ProfileAccounts(ChemistryProvider):
    name = "profile-accounts"

    def __init__(self, accounts: frozenset[str], credit: str) -> None:
        self._accounts = accounts
        self._credit = credit

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            is_authoritative_for=frozenset({ChemistryIntent.EVAPORATION_TRANSITION}),
            declared_accounts=self._accounts,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={_LIQUID: {"SiO2": 0.25}},
                credits={self._credit: {"SiO2": 0.25}},
                reason="phase_home",
            ),
            control_audit=None,
            diagnostic={},
            warnings=(),
        )


def _evaporation_kernel(provider: ChemistryProvider) -> tuple[AtomLedger, ChemistryKernel]:
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _LIQUID,
        {"SiO2": 1.0},
        material_origin="feedstock",
    )
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.EVAPORATION_TRANSITION])
    return ledger, ChemistryKernel(ledger, registry, species_formula_registry={})


def test_dispatch_admits_a_cohort_opened_during_the_call():
    provider = _AdmitDuringDispatch()
    ledger, kernel = _evaporation_kernel(provider)
    result = kernel.dispatch(
        ChemistryIntent.EVAPORATION_TRANSITION,
        temperature_C=1400.0,
        pressure_bar=1.0,
        declared_accounts=frozenset({_LIQUID}),
    )
    kernel.commit_batch(ChemistryIntent.EVAPORATION_TRANSITION, result.transition)
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(0.75)
    assert ledger.mol_by_account(_OLIVINE_0)["SiO2"] == pytest.approx(0.25)


def test_dispatch_rejects_an_account_the_profile_never_gained():
    provider = _ProfileAccounts(
        frozenset({_LIQUID, "process.overhead_gas"}),
        _OLIVINE_0,
    )
    ledger, kernel = _evaporation_kernel(provider)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1400.0,
            pressure_bar=1.0,
        )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(1.0)
    assert _OLIVINE_0 not in ledger.mol_by_account()


def test_caller_narrowing_still_excludes_a_previously_declared_account():
    provider = _ProfileAccounts(
        frozenset({_LIQUID, "process.overhead_gas"}),
        "process.overhead_gas",
    )
    ledger, kernel = _evaporation_kernel(provider)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            ChemistryIntent.EVAPORATION_TRANSITION,
            temperature_C=1400.0,
            pressure_bar=1.0,
            declared_accounts=frozenset({_LIQUID}),
        )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(1.0)
    assert "process.overhead_gas" not in ledger.mol_by_account()


def _load_crystal(sim) -> None:
    sim.atom_ledger.load_external_mol(
        _OLIVINE_0,
        {"MgO": 1.0},
        material_origin="feedstock",
    )


def test_scalar_sites_stay_unsplit_when_a_crystal_cohort_holds_mass(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    _install_curve(sim)
    _load_crystal(sim)
    sim.melt.temperature_C = 1150.0
    assert _shadow_rates(sim, monkeypatch) == pytest.approx(_SHADOW_FULL_KG_HR)

    monkeypatch.setattr(sim, "_melt_redox_capacity_mol_per_ln_fO2", lambda **_kwargs: 12.0)
    monkeypatch.setattr(
        sim,
        "_dispatch_only",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached redox capacity scaling must not dispatch")
        ),
    )
    assert sim._melt_redox_source_capacity_mol_per_ln_fO2(
        fO2_log=-9.0,
        T_K=1150.0 + 273.15,
    ) == pytest.approx(12.0)
    assert sim._last_melt_redox_liquid_fraction_diagnostic[
        "liquid_fraction"
    ] == pytest.approx(0.5)

    _install_eligible_vapour_batch(sim)
    sim._last_vapor_pressure_diagnostic = {
        "vapor_pressure_numerator_provenance": {
            "Na": {"melt_oxide_X_single_cation": _BULK_X["Na"]},
            "K": {"melt_oxide_X_single_cation": _BULK_X["K"]},
        },
    }

    def fake_dispatch(intent, *args, **kwargs):
        del args, kwargs
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status="ok",
                diagnostic={"evaporation_flux_kg_hr": dict(_FLUX_STUB_KG_HR)},
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            return SimpleNamespace(status="ok", diagnostic={})
        raise AssertionError(f"unexpected dispatch: {intent}")

    monkeypatch.setattr(sim, "_dispatch_only", fake_dispatch)
    flux = sim._calculate_evaporation(
        EquilibriumResult(
            temperature_C=1150.0,
            pressure_bar=1e-8,
            liquid_fraction=_MELT_FRACTION_F,
            vapor_pressures_Pa=dict(_VAPOR_PA),
            diagnostics={"solidus_T_C": 1000.0, "liquidus_T_C": 1300.0},
        )
    )
    for species, stub in _FLUX_STUB_KG_HR.items():
        assert flux.species_kg_hr[species] == pytest.approx(stub)

    seen: list[float | None] = []
    monkeypatch.setattr(sim, "_transfer_condensed_species", lambda species: 0.0)
    monkeypatch.setattr(sim, "_top_up_c3_alkali_credit", lambda species: 0.0)
    monkeypatch.setattr(
        sim,
        "_shuttle_inject_K",
        lambda *, liquid_fraction=None: seen.append(liquid_fraction),
    )
    monkeypatch.setattr(
        sim,
        "_shuttle_inject_Na",
        lambda *, target_stage, liquid_fraction=None: seen.append(liquid_fraction),
    )
    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.campaign_hour = 1
    sim._step_shuttle()
    assert seen == [None, None]

    captured: list[float | None] = []

    def capture_dispatch(intent, *args, **kwargs):
        del intent, args
        captured.append(kwargs["control_inputs"]["liquid_fraction"])
        return SimpleNamespace(status="ok", diagnostic={}, transition=None)

    monkeypatch.setattr(sim, "_dispatch_only", capture_dispatch)
    campaigns = dict(sim.setpoints.get("campaigns") or {})
    c6 = dict(campaigns.get("C6") or {})
    c6["static_window_by_feedstock"] = {}
    campaigns["C6"] = c6
    sim.setpoints["campaigns"] = campaigns
    sim.thermite_Mg_inventory_kg = 5.0
    sim._step_thermite()
    assert captured == [None]


def test_phase_home_hook_does_not_count_a_null_transition_as_a_no_op(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=False,
    )
    before = sim._chem_no_op_dispatch_count

    def forbidden(*_args, **_kwargs):
        raise AssertionError("phase-home hook dispatched without a python API")

    sim._dispatch_only = forbidden
    sim._commit_phase_homes()
    assert sim._chem_no_op_dispatch_count == before

    backend = SimpleNamespace(_mode="subprocess")
    provider = AlphaMELTSProvider(backend=backend)
    sim._chem_registry.register(
        provider,
        [ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION],
    )
    sim._commit_phase_homes()
    assert sim._chem_no_op_dispatch_count == before

    backend._mode = "python_api"
    _load_crystal(sim)
    dispatched: list[tuple] = []
    reservoir_fO2 = float(sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log)

    def no_transition(intent, **kwargs):
        dispatched.append((intent, kwargs.get("fO2_log")))
        return SimpleNamespace(status="ok", transition=None, diagnostic={})

    sim._dispatch_only = no_transition
    sim._commit_phase_homes()
    assert dispatched == [
        (ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION, reservoir_fO2),
    ]
    assert _OLIVINE_0 in provider._crystal_accounts
    assert sim._chem_no_op_dispatch_count == before

    committed: list[tuple] = []

    def with_proposal(intent, **_kwargs):
        return SimpleNamespace(status="ok", transition=object(), diagnostic={})

    def capture(intent, proposal, **kwargs):
        committed.append((intent, proposal, kwargs.get("transition_source")))

    sim._dispatch_only = with_proposal
    sim._commit_proposal = capture
    sim._commit_phase_homes()
    assert committed[0][2] == "phase_home"
    assert sim._chem_no_op_dispatch_count == before

    def unavailable(*_args, **_kwargs):
        raise ProviderUnavailableError("down")

    def sample_failed(*_args, **_kwargs):
        raise LiquidusSampleError("unavailable", ("down",), {})

    def bad_fraction(*_args, **_kwargs):
        raise LiquidFractionInvalidError("liquid_fraction_invalid")

    for failing in (unavailable, sample_failed, bad_fraction):
        sim._dispatch_only = failing
        sim._commit_phase_homes()
        assert sim._chem_no_op_dispatch_count == before
        assert len(committed) == 1
