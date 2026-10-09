"""Chunk 2: phase-home cohorts and the scalar-F switch.

The 50 kg hour is a worked example, not a runner golden. Expected
masses are the literals in each test. Oxide moles come from kilograms
divided by ``parse_formula``.
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
    REASON_NON_SILICATE,
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
    ProviderAccountView,
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

    def __init__(
        self,
        *,
        refuse_spinel: bool = False,
        report_activities: bool = True,
    ) -> None:
        self._mode = "python_api"
        self.calls: list[dict] = []
        self.refuse_spinel = refuse_spinel
        self.report_activities = report_activities

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
            activity_coefficients=(
                {"SiO2": 0.6} if self.report_activities else {}
            ),
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
            activity_coefficients={"SiO2": 0.6},
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
    # Rows are (locked kg, growth kg, equilibrium kg, dissolve kg, new kg).
    # A shortfall against the lock dissolves. A surplus grows, and only
    # up to the growth offer. The two moves cannot both be positive.
    cases = (
        (50.0, 15.0, 60.0, 0.0, 10.0),
        (50.0, 0.0, 40.0, 10.0, 0.0),
        (50.0, 0.0, 0.0, 50.0, 0.0),
        (50.0, 0.0, 50.0, 0.0, 0.0),
    )
    for m_locked, m_growth, m_eq, dissolve, m_new in cases:
        got = signed_locked_masses(m_locked, m_growth, m_eq)
        assert got == pytest.approx((dissolve, m_new))
        assert not (got[0] > 0.0 and got[1] > 0.0)


def test_partial_growth_opens_one_new_cohort_at_the_accessible_composition():
    # Locked 50 kg, the engine offers 15 kg, equilibrium is 60 kg.
    # Nothing dissolves. The new shell is min(15, 60-50) = 10 kg,
    # which is 10/15 of the offer: 5 kg of each oxide from 7.5 kg.
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
        accessible_phases=(_phase("olivine", 15.0, {"SiO2": sio2, "MgO": mgo}),),
        probe_phases=(_phase("olivine", 60.0, {"FeO": feo}),),
        locked=cohorts,
    )

    assert update.refusal_reason is None
    assert update.proposal is not None
    note = {row["phase"]: row for row in update.phases}
    assert note["olivine"]["dissolve_kg"] == pytest.approx(0.0)
    assert note["olivine"]["m_new_kg"] == pytest.approx(10.0)
    assert _OLIVINE_0 not in update.proposal.debits
    assert _OLIVINE_0 not in update.proposal.credits
    credit = update.proposal.credits[_OLIVINE_1]
    assert credit["SiO2"] == pytest.approx(_moles(5.0, "SiO2"))
    assert credit["MgO"] == pytest.approx(_moles(5.0, "MgO"))
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

    # Locked 40 kg SiO2 plus 10 kg MgO. Equilibrium is 40 kg and no
    # growth is offered, so 10 kg dissolves and the youngest shell empties.
    assert update.proposal is not None
    note = {row["phase"]: row for row in update.phases}
    assert note["olivine"]["dissolve_kg"] == pytest.approx(10.0)
    assert note["olivine"]["m_new_kg"] == pytest.approx(0.0)
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


def test_a_second_liquid_or_an_alloy_refuses_the_whole_commit():
    """One silicate liquid is skipped. Two liquids, or water/alloy, refuse."""
    olivine = _phase(
        "olivine",
        0.01,
        {"SiO2": _moles(0.004, "SiO2"), "MgO": _moles(0.006, "MgO")},
    )
    liquid = _phase("liquid", 0.5, {"SiO2": _moles(0.5, "SiO2")})
    cases = (
        (liquid, _phase("liquid2", 0.4, {"MgO": _moles(0.4, "MgO")}), olivine),
        (liquid, _phase("alloy1", 0.1, {"Fe": _moles(0.1, "Fe")}), olivine),
        (liquid, _phase("water1", 0.01, {"H2O": _moles(0.01, "H2O")}), olivine),
    )
    for rows in cases:
        update = locked_cohort_update(
            binding=MELTS_BINDING,
            liquid_oxide_mol={"SiO2": _moles(1.0, "SiO2"), "MgO": _moles(1.0, "MgO")},
            accessible_phases=rows,
            probe_phases=None,
            locked=(),
        )
        assert update.proposal is None
        assert update.refusal_reason == REASON_NON_SILICATE
        assert update.touched_crystal_accounts == ()


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
    state = result.diagnostic["backend_diagnostics"][
        "assemblage_thermodynamic_state"
    ]
    assert state["liquid_activities"]["SiO2"] == pytest.approx(0.6)
    assert state["oxygen_root"] == pytest.approx(-9.0)
    assert result.diagnostic["backend_diagnostics"][
        "surface_crust_not_modeled"
    ]["reason"] == "surface_crust_not_modeled"
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
    # The 50 kg MgO shell is locked, the accessible offer is empty, and
    # the probe equilibrium is 0 kg, so the whole shell dissolves.
    assert phases["olivine"]["dissolve_kg"] == pytest.approx(50.0)
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
    assert (
        "assemblage_held_from_previous_hour"
        not in held_result.diagnostic["backend_diagnostics"]
    )
    assert held_result.transition is None
    assert held.mol_by_account(_OLIVINE_0)["MgO"] == pytest.approx(magnesia)
    assert held.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(silica)
    assert float(held.mol_by_account(_LIQUID).get("MgO", 0.0)) == pytest.approx(0.0)


class _AdmitDuringDispatch(ChemistryProvider):
    name = "admit-during-dispatch"

    def __init__(self, intent: ChemistryIntent, opened: str = _OLIVINE_0) -> None:
        self._intent = intent
        self._opened = opened
        self.extra: set[str] = set()

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({self._intent}),
            is_authoritative_for=frozenset({self._intent}),
            declared_accounts=frozenset({_LIQUID, *self.extra}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.extra.add(self._opened)
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={_LIQUID: {"SiO2": 0.25}},
                credits={self._opened: {"SiO2": 0.25}},
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


def _registered_kernel(
    provider: ChemistryProvider,
    intent: ChemistryIntent,
) -> tuple[AtomLedger, ChemistryKernel]:
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _LIQUID,
        {"SiO2": 1.0},
        material_origin="feedstock",
    )
    registry = ProviderRegistry()
    registry.register(provider, [intent])
    return ledger, ChemistryKernel(ledger, registry, species_formula_registry={})


def test_dispatch_admits_a_cohort_opened_during_the_call():
    intent = ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION
    provider = _AdmitDuringDispatch(intent)
    ledger, kernel = _registered_kernel(provider, intent)
    result = kernel.dispatch(
        intent,
        temperature_C=1400.0,
        pressure_bar=1.0,
        declared_accounts=frozenset({_LIQUID}),
    )
    kernel.commit_batch(intent, result.transition)
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(0.75)
    assert ledger.mol_by_account(_OLIVINE_0)["SiO2"] == pytest.approx(0.25)


def test_dispatch_rejects_a_crystal_opened_during_evaporation():
    intent = ChemistryIntent.EVAPORATION_TRANSITION
    provider = _AdmitDuringDispatch(intent)
    ledger, kernel = _registered_kernel(provider, intent)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            intent,
            temperature_C=1400.0,
            pressure_bar=1.0,
            declared_accounts=frozenset({_LIQUID}),
        )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(1.0)
    assert _OLIVINE_0 not in ledger.mol_by_account()


@pytest.mark.parametrize(
    "opened",
    (
        "process.overhead_gas",
        "process.crystal.melts.olivine",
    ),
)
def test_dispatch_rejects_a_non_cohort_opened_during_crystallization(opened: str):
    intent = ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION
    provider = _AdmitDuringDispatch(intent, opened=opened)
    ledger, kernel = _registered_kernel(provider, intent)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            intent,
            temperature_C=1400.0,
            pressure_bar=1.0,
        )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(1.0)
    assert opened not in ledger.mol_by_account()


def test_dispatch_rejects_an_account_the_profile_never_gained():
    intent = ChemistryIntent.EVAPORATION_TRANSITION
    provider = _ProfileAccounts(
        frozenset({_LIQUID, "process.overhead_gas"}),
        _OLIVINE_0,
    )
    ledger, kernel = _registered_kernel(provider, intent)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            intent,
            temperature_C=1400.0,
            pressure_bar=1.0,
        )
    assert ledger.mol_by_account(_LIQUID)["SiO2"] == pytest.approx(1.0)
    assert _OLIVINE_0 not in ledger.mol_by_account()


def test_caller_narrowing_still_excludes_a_previously_declared_account():
    intent = ChemistryIntent.EVAPORATION_TRANSITION
    provider = _ProfileAccounts(
        frozenset({_LIQUID, "process.overhead_gas"}),
        "process.overhead_gas",
    )
    ledger, kernel = _registered_kernel(provider, intent)
    with pytest.raises(AccountFilterViolation):
        kernel.dispatch(
            intent,
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
    assert sim._assemblage_binding_active is False

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
        return SimpleNamespace(
            status="ok",
            transition=None,
            diagnostic={
                "backend_diagnostics": {
                    "phase_home_refusal": {
                        "reason": "phase_home_liquid_insufficient",
                    },
                },
            },
        )

    sim._dispatch_only = no_transition
    sim._commit_phase_homes()
    assert dispatched == [
        (ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION, reservoir_fO2),
    ]
    assert sim._phase_home_diagnostic["backend_diagnostics"][
        "phase_home_refusal"
    ]["reason"] == "phase_home_liquid_insufficient"
    assert sim._assemblage_binding_active is True
    assert sim._phase_home_diagnostic["backend_diagnostics"][
        "surface_crust_not_modeled"
    ]["reason"] == "surface_crust_not_modeled"
    assert _OLIVINE_0 in provider._crystal_accounts
    assert sim._chem_no_op_dispatch_count == before

    committed: list[tuple] = []
    projected: list[bool] = []

    def with_proposal(intent, **_kwargs):
        return SimpleNamespace(
            status="ok",
            transition=object(),
            diagnostic={
                "backend_diagnostics": {
                    "assemblage_thermodynamic_state": {
                        "liquid_activities": {"SiO2": 0.6},
                        "oxygen_root": -7.5,
                    },
                },
            },
        )

    def capture(intent, proposal, **kwargs):
        committed.append((intent, proposal, kwargs.get("transition_source")))

    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._dispatch_only = with_proposal
    sim._commit_proposal = capture
    sim._project_cleaned_melt_from_atom_ledger = lambda: projected.append(True)
    sim._commit_phase_homes()
    assert committed[0][2] == "phase_home"
    assert projected == [True]
    assert sim._assemblage_liquid_activities == {"SiO2": 0.6}
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(-7.5)
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
        assert sim._phase_home_diagnostic["backend_diagnostics"][
            "assemblage_held_from_previous_hour"
        ]["reason"] == "assemblage_held_from_previous_hour"
        assert sim._assemblage_binding_active is True


def test_a_solve_without_activities_is_not_admitted():
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _LIQUID,
        {"SiO2": 1.0, "MgO": 1.0},
        material_origin="feedstock",
    )
    snapshot = {
        account: dict(species)
        for account, species in ledger.mol_by_account().items()
    }
    _provider, kernel = _register(
        _GrowthBackend(report_activities=False),
        ledger,
    )
    result = _dispatch(kernel, 1150.0)
    assert result.status == "ok"
    assert result.transition is None
    backend = result.diagnostic["backend_diagnostics"]
    assert backend["phase_home_refusal"]["reason"] == "assemblage_state_incomplete"
    assert backend["assemblage_state_incomplete"]["reason"] == (
        "assemblage_state_incomplete"
    )
    assert "assemblage_held_from_previous_hour" not in backend
    assert {
        account: dict(species)
        for account, species in ledger.mol_by_account().items()
    } == snapshot


def test_metal_inventory_refuses_the_silicate_commit():
    provider = AlphaMELTSProvider(backend=_GrowthBackend())

    def request_for(accounts: dict) -> IntentRequest:
        return IntentRequest(
            intent=ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            account_view=ProviderAccountView(
                accounts=accounts,
                species_formula_registry={},
            ),
            temperature_C=1150.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            control_inputs={"metal_inventory_present": True},
        )

    proposal, note = provider._phase_home_transition(
        request_for({_LIQUID: {"SiO2": 1.0}}),
        SimpleNamespace(diagnostics={}, isothermal_phase_inventories=()),
    )
    assert proposal is None
    assert note["phase_home_refusal"]["reason"] == (
        "phase_home_metal_redox_unresolved"
    )
    assert "assemblage_held_from_previous_hour" not in note

    _proposal, held = provider._phase_home_transition(
        request_for({_LIQUID: {"SiO2": 1.0}, _OLIVINE_0: {"MgO": 1.0}}),
        SimpleNamespace(diagnostics={}, isothermal_phase_inventories=()),
    )
    assert held["assemblage_held_from_previous_hour"]["reason"] == (
        "assemblage_held_from_previous_hour"
    )
    assert held["surface_crust_not_modeled"]["reason"] == (
        "surface_crust_not_modeled"
    )


def test_phase_home_hook_tells_the_provider_when_metal_holds_mass(
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
    backend = SimpleNamespace(_mode="python_api")
    sim._chem_registry.register(
        AlphaMELTSProvider(backend=backend),
        [ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION],
    )
    sim.atom_ledger.load_external_mol(
        "process.metal_phase",
        {"Fe": 1.0},
        material_origin="feedstock",
    )
    seen: list[dict] = []

    def capture(intent, **kwargs):
        del intent
        seen.append(dict(kwargs.get("control_inputs") or {}))
        return SimpleNamespace(status="ok", transition=None, diagnostic={})

    sim._dispatch_only = capture
    sim._commit_phase_homes()
    assert seen == [{"metal_inventory_present": True}]


def test_assemblage_activities_survive_the_vapour_refresh(
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
    sim.melt.temperature_C = 1150.0
    sim._assemblage_binding_active = True
    sim._assemblage_liquid_activities = {"SiO2": 0.6}
    seen: list[bool] = []

    def capture(intent, **kwargs):
        del intent
        seen.append(
            kwargs["control_inputs"].get("assemblage_binding_active") is True
        )
        return SimpleNamespace(
            status="ok",
            diagnostic={"activities": {"Na": 2.0}, "vapor_pressures_Pa": {}},
        )

    sim._dispatch_only = capture
    result = EquilibriumResult(
        temperature_C=1150.0,
        pressure_bar=1.0e-8,
        liquid_fraction=0.5,
        activity_coefficients={"SiO2": 0.1},
        status="ok",
    )
    sim._refresh_vapor_pressures_from_kernel(result)
    assert seen == [True]
    assert result.activity_coefficients == {"SiO2": 0.6}


def test_vapour_dispatch_names_the_assemblage_binding():
    from pathlib import Path

    import yaml

    from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
    from simulator.melt_backend.vaporock import VAPOROCK_T_MAX_K

    payload = yaml.safe_load(
        Path("data/vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    provider = BuiltinVaporPressureProvider(payload or {})
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={
                    _LIQUID: {
                        "Na2O": 0.20,
                        "K2O": 0.08,
                        "SiO2": 5.00,
                        "FeO": 1.00,
                        "MgO": 1.00,
                        "CaO": 1.00,
                        "Al2O3": 1.00,
                        "TiO2": 0.10,
                    },
                },
                species_formula_registry={},
            ),
            temperature_C=(VAPOROCK_T_MAX_K + 0.1) - 273.15,
            pressure_bar=1.0e-6,
            control_inputs={
                "pO2_bar": 1.0e-9,
                "intrinsic_fO2_log": -10.0,
                "high_t_melt_activity": "openimcc",
                "assemblage_binding_active": True,
            },
        )
    )
    assert any(
        str(warning).startswith("assemblage_binding_active:")
        for warning in result.warnings
    )
    assert result.diagnostic.get("high_t_melt_activity") in (None, {})


def test_openimcc_is_not_evaluated_on_an_assemblage_liquid(monkeypatch):
    from engines.builtin.vapor_pressure import (
        _build_high_t_melt_activity_authority,
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("openimcc evaluated on an assemblage liquid")

    monkeypatch.setattr(
        "simulator.melt_backend.openimcc_bridge.evaluate_cleaned_melt",
        boom,
    )
    authority = _build_high_t_melt_activity_authority(
        composition_mol={"SiO2": 1.0},
        temperature_K=2000.0,
        controls={
            "high_t_melt_activity": "openimcc",
            "assemblage_binding_active": True,
        },
        below_cap_fe_activity=0.1,
        below_cap_fe_activity_basis="test",
    )
    assert authority is None


class _FullyMoltenProbe:
    """The only call is the locked probe. It reports a pure liquid."""

    def __init__(self) -> None:
        self._mode = "python_api"
        self.calls: list[dict] = []
        self.liquidus_calls = 0

    def is_available(self) -> bool:
        return True

    def get_engine_version(self) -> str:
        return "chunk2-full-remelt"

    def find_liquidus_solidus(self, **_kwargs):
        self.liquidus_calls += 1
        raise AssertionError("empty liquid must not search for a liquidus")

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        return EquilibriumResult(
            temperature_C=float(kwargs["temperature_C"]),
            pressure_bar=float(kwargs["pressure_bar"]),
            liquid_fraction=1.0,
            liquid_composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
            phases_present=["liquid"],
            phase_masses_kg={"liquid": 100.0},
            phase_compositions={"liquid": {"SiO2": 50.0, "MgO": 50.0}},
            activity_coefficients={"SiO2": 0.42, "MgO": 0.17},
            fO2_log=float(kwargs["fO2_log"]),
            status="ok",
        )


def test_an_empty_liquid_with_locked_cohorts_remelts_above_the_liquidus():
    silica = _moles(50.0, "SiO2")
    magnesia = _moles(50.0, "MgO")
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _OLIVINE_0,
        {"SiO2": silica, "MgO": magnesia},
        material_origin="feedstock",
    )
    backend = _FullyMoltenProbe()
    provider, kernel = _register(backend, ledger)
    provider.sync_crystal_accounts(
        crystal_accounts_for_binding(ledger.mol_by_account(), MELTS_BINDING)
    )
    before = _elements(ledger.mol_by_account())
    result = _dispatch(kernel, 1400.0)

    assert result.status == "ok"
    assert backend.liquidus_calls == 0
    assert len(backend.calls) == 1
    probed = backend.calls[0]["composition_mol_by_account"][_LIQUID]
    assert probed["SiO2"] == pytest.approx(silica)
    assert probed["MgO"] == pytest.approx(magnesia)
    assert backend.calls[0]["temperature_C"] == pytest.approx(1400.0)
    diagnostic = result.diagnostic["backend_diagnostics"]
    assert diagnostic["accessible_liquid_absent"] is True
    state = diagnostic["assemblage_thermodynamic_state"]
    assert state["liquid_activities"]["SiO2"] == pytest.approx(0.42)
    assert state["liquid_activities"]["MgO"] == pytest.approx(0.17)
    assert state["oxygen_root"] == pytest.approx(-9.0)
    assert result.transition is not None
    kernel.commit_batch(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        result.transition,
    )
    assert holds_positive_crystal_moles(ledger.mol_by_account()) is False
    liquid = ledger.mol_by_account(_LIQUID)
    assert liquid["SiO2"] == pytest.approx(silica)
    assert liquid["MgO"] == pytest.approx(magnesia)
    assert _elements(ledger.mol_by_account()) == pytest.approx(before)


def test_an_empty_liquid_without_cohorts_stays_out_of_domain():
    ledger = _strict_ledger()
    backend = _FullyMoltenProbe()
    _provider, kernel = _register(backend, ledger)
    result = _dispatch(kernel, 1400.0)
    assert result.status == "out_of_domain"
    assert result.transition is None
    assert backend.calls == []
    assert any("empty composition" in warning for warning in result.warnings)


def test_an_empty_liquid_probe_refuses_when_the_engine_is_down():
    silica = _moles(50.0, "SiO2")
    magnesia = _moles(50.0, "MgO")
    ledger = _strict_ledger()
    ledger.load_external_mol(
        _OLIVINE_0,
        {"SiO2": silica, "MgO": magnesia},
        material_origin="feedstock",
    )
    calls: list[dict] = []
    backend = SimpleNamespace(
        _mode="subprocess",
        equilibrate=lambda **kwargs: calls.append(kwargs),
    )
    provider = AlphaMELTSProvider(backend=backend)
    provider.sync_crystal_accounts(
        crystal_accounts_for_binding(ledger.mol_by_account(), MELTS_BINDING)
    )
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})
    result = _dispatch(kernel, 1400.0)
    assert result.status == "ok"
    assert result.transition is None
    assert calls == []
    diagnostic = result.diagnostic["backend_diagnostics"]
    assert diagnostic["phase_home_refusal"]["reason"] == "phase_home_probe_failed"
    assert diagnostic["assemblage_held_from_previous_hour"]["reason"] == (
        "assemblage_held_from_previous_hour"
    )
    assert ledger.mol_by_account(_OLIVINE_0)["SiO2"] == pytest.approx(silica)
