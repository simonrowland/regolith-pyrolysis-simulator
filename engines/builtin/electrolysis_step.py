"""Builtin ELECTROLYSIS_STEP provider (MRE oxide-reduction ledger update).

Kernel-registered provider that owns the ``ELECTROLYSIS_STEP`` intent per
binding spec §2 ("MRE: anode O2, cathode metals, current efficiency")
and §3 (Builtin authoritative). Mirrors the Nernst / Faraday / current-
efficiency math in :meth:`simulator.electrolysis.ElectrolysisModel.step_hour`
exactly -- this is a refactor of where the
:class:`LedgerTransitionProposal` is built, not a re-derivation of the
oxide-reduction physics (the legacy module retains the canonical
``ElectrolysisModel`` for Nernst voltage lookups, voltage sequences, and
operator-facing summaries; the provider re-uses the same ``DECOMP_VOLTAGES``
+ ``ELECTRONS_PER_OXIDE`` tables and the same partition / Faraday / CE
formulas).

The provider:

- reads ``process.cleaned_melt`` (debit source for parent oxide),
  ``process.metal_phase`` (cathode product credit), and
  ``terminal.oxygen_mre_anode_stored`` (anode O2 credit) via the
  filtered :class:`ProviderAccountView`. Terminal *credits* are allowed
  through the canonical kernel commit path; terminal *debits* are
  forbidden by :meth:`AtomLedger._validate_terminal_debits`, but the
  MRE intent only ever CREDITS the anode bin -- the debit always lands
  on the silicate melt.
- reads T from ``request.temperature_C``,
- reads applied voltage / current / dt and the live cleaned-melt mol
  composition from ``request.control_inputs``:

  * ``voltage_V`` -- applied cell voltage,
  * ``current_A`` -- total cell current,
  * ``dt_hr`` -- tick duration in hours (always 1.0 in the current
    simulator, passed through explicitly so the provider stays unit-
    correct if the simulator's tick ever changes -- the t_s = 3600 s
    Faraday integration scales with this).

The wt% composition that drives the activity proxy is computed from the
account view directly (mol -> kg -> wt% on the registry's molar
masses), matching :meth:`MeltState.composition_wt_pct` line-for-line.
Stateless: the same provider instance serves every MRE hour in every
campaign.

Returns an :class:`IntentResult` with ``transition`` populated by a
:class:`LedgerTransitionProposal` and a ``diagnostic`` dict carrying
the per-oxide / per-metal / O2 kg-and-mol bookkeeping the legacy caller
needs (``oxides_reduced_kg``, ``oxides_reduced_mol``,
``metals_produced_kg``, ``metals_produced_mol``, ``O2_produced_kg``,
``O2_produced_mol``, ``energy_kWh``).  Energy stays in the diagnostic
field, NEVER in the ledger -- the simulator tracks energy separately
via :class:`EnergyTracker` and the existing ``_mre_energy_this_hr``
counter.  The provider deliberately never mutates the ledger for
energy.

Authority: authoritative for ``ELECTROLYSIS_STEP`` per binding spec §3.
This is the THIRD authoritative ledger-mutating intent in the
migration (after EVAPORATION_TRANSITION and CONDENSATION_ROUTE) --
:meth:`ChemistryKernel.commit_batch` engages atom-balance validation at
dispatch time AND again at commit time.

Account declaration: ``process.cleaned_melt`` (debit),
``process.metal_phase`` (credit), ``terminal.oxygen_mre_anode_stored``
(credit). The MRE anode O2 is its OWN bin per binding spec §3 and
AGENTS.md #6 -- distinct from ``terminal.oxygen_melt_offgas_stored``
(evaporation O2 coproduct), ``terminal.oxygen_stage0_stored`` (Stage 0
oxidative pretreatment), and any overhead-headspace transient. The
provider must NEVER credit any other O2 bin; the kernel account filter
would catch a leak but the explicit declared set is the first line of
defence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from engines.builtin._common import (
    build_atom_balance_proof,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    ControlAudit,
    IntentRequest,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.chemistry.kernel.provider import ChemistryProvider


class BuiltinElectrolysisStepProvider(ChemistryProvider):
    """Authoritative ``ELECTROLYSIS_STEP`` provider.

    See module docstring. Stateless -- per-call inputs (T, voltage,
    current, dt, melt view) arrive through :class:`IntentRequest`; the
    same instance serves every MRE tick without holding simulator
    references.
    """

    name = "builtin-electrolysis-step"

    DECLARED_ACCOUNTS = frozenset({
        "process.cleaned_melt",
        "process.metal_phase",
        "terminal.oxygen_mre_anode_stored",
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id="builtin-electrolysis-step",
            intents=frozenset({ChemistryIntent.ELECTROLYSIS_STEP}),
            is_authoritative_for=frozenset(
                {ChemistryIntent.ELECTROLYSIS_STEP}
            ),
            declared_accounts=self.DECLARED_ACCOUNTS,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        # Lazy imports: simulator.state / simulator.electrolysis /
        # simulator.accounting.formulas pull in simulator/__init__ which
        # re-enters this module during package init -- see
        # engines/builtin/__init__.py for the cycle description.
        from simulator.accounting.formulas import resolve_species_formula
        from simulator.electrolysis import (
            DECOMP_VOLTAGES,
            ELECTRONS_PER_OXIDE,
        )
        from simulator.state import (
            FARADAY,
            GAS_CONSTANT,
            MOLAR_MASS,
            OXIDE_TO_METAL,
        )

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.ELECTROLYSIS_STEP
        )
        if wrong_intent is not None:
            return wrong_intent

        # Build the audit once; every return path threads it through.
        # Anode-fO2 is fixed at log10(1.0) = 0.0 by the pure-O2 evolution
        # boundary condition (see _build_control_audit docstring).
        control_audit = self._build_control_audit(request)

        controls = unpack_controls(request)
        voltage_V = float(controls.get("voltage_V") or 0.0)
        current_A = float(controls.get("current_A") or 0.0)
        dt_hr = float(controls.get("dt_hr", 1.0))
        T_C = float(request.temperature_C)
        T_K = T_C + 273.15

        # Compute the wt% composition view from the cleaned_melt mol
        # account -- mirrors MeltState.composition_wt_pct exactly. The
        # legacy ElectrolysisModel reads activity = wt_fraction =
        # comp[oxide]/100; we reproduce that path through the registry-
        # driven mol -> kg projection so the provider stays stateless
        # and the kernel account filter is the single source of truth
        # for what the provider may see.
        melt_mol = dict(
            request.account_view.accounts.get("process.cleaned_melt", {}) or {}
        )
        if not melt_mol:
            return self._empty_result(
                diagnostic_skipped="empty melt", request=request,
            )

        registry = request.account_view.species_formula_registry
        composition_kg: dict[str, float] = {}
        total_kg = 0.0
        for species, mol in melt_mol.items():
            mol = float(mol)
            if mol <= 0.0:
                continue
            formula = resolve_species_formula(str(species), registry)
            mass_kg = mol * formula.molar_mass_kg_per_mol()
            if mass_kg <= 0.0:
                continue
            composition_kg[str(species)] = composition_kg.get(
                str(species), 0.0
            ) + mass_kg
            total_kg += mass_kg

        # Result skeleton mirrors ElectrolysisModel.step_hour exactly so
        # the legacy caller's post-processing path can consume the
        # diagnostic dict as-is (used for cathode-routing deltas and
        # the effective-current Faraday-readback in _step_mre).
        diagnostic: dict[str, Any] = {
            "oxides_reduced_kg": {},
            "oxides_reduced_mol": {},
            "metals_produced_kg": {},
            "metals_produced_mol": {},
            "O2_produced_kg": 0.0,
            "O2_produced_mol": 0.0,
            "energy_kWh": 0.0,
        }

        if total_kg <= 0.0 or voltage_V <= 0.0 or current_A <= 0.0:
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        # Find all reducible species at this voltage. Mirrors
        # ElectrolysisModel.step_hour line-for-line: same E0 table,
        # same Nernst formula, same 1e-6 kg gate, same activity proxy.
        reducible: list[tuple[str, float, float, float]] = []
        for oxide in DECOMP_VOLTAGES:
            if oxide not in composition_kg:
                continue
            if composition_kg.get(oxide, 0.0) < 1e-6:
                continue
            # Crude activity ~= wt_fraction. wt_pct/100 == wt_fraction.
            activity = (composition_kg[oxide] / total_kg)
            E_nernst = self._nernst_voltage(
                oxide,
                T_K,
                activity,
                gas_constant=GAS_CONSTANT,
                faraday=FARADAY,
                decomp_voltages=DECOMP_VOLTAGES,
                electrons_per_oxide=ELECTRONS_PER_OXIDE,
            )
            if E_nernst < voltage_V:
                overvoltage = voltage_V - E_nernst
                reducible.append((oxide, E_nernst, overvoltage, activity))

        if not reducible:
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        # Partition current among reducible species (selectivity:
        # weight by concentration * exp(overvoltage), capped at dV=3.0).
        # Mirrors ElectrolysisModel.step_hour. [SEL-1]
        weights: dict[str, float] = {}
        for oxide, _E, dV, a in reducible:
            weights[oxide] = a * math.exp(min(dV, 3.0))
        total_weight = sum(weights.values())
        if total_weight <= 0.0:
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        # Faraday integration time. dt_hr -> seconds. Mirrors the
        # legacy ``t_s = 3600.0`` exactly when dt_hr=1.0, scales for
        # future tick changes.
        t_s = 3600.0 * dt_hr

        debits_mol: dict[str, dict[str, float]] = defaultdict(dict)
        credits_mol: dict[str, dict[str, float]] = defaultdict(dict)

        # Accumulators for the metal/O2 sides (multiple oxides may
        # contribute to the same metal -- e.g. Fe from FeO + Fe2O3 in
        # sequence -- so additive bookkeeping mirrors legacy exactly).
        metal_mol_total: dict[str, float] = {}
        O2_mol_total = 0.0
        oxide_mol_total: dict[str, float] = {}

        for oxide, _E, dV, _a in reducible:
            fraction = weights[oxide] / total_weight
            I_species = current_A * fraction

            # Current efficiency (CE-1). Same clamp [0.10, 0.95] as
            # legacy. dV >= 0 by construction (reducible filter).
            eta_CE = 0.30 + 0.45 * (1.0 - math.exp(-0.5 * max(0.0, dV)))
            eta_CE = min(0.95, max(0.10, eta_CE))

            # Faraday's law (FARADAY-1). n electrons per formula unit.
            n_e = ELECTRONS_PER_OXIDE.get(oxide, 2)
            M_oxide_gmol = MOLAR_MASS.get(oxide, 100.0)

            moles_reduced = (I_species * eta_CE * t_s) / (n_e * FARADAY)
            kg_oxide_reduced = moles_reduced * M_oxide_gmol / 1000.0

            # Cap at melt availability. Re-derive mol from the capped
            # kg to keep mol-native bookkeeping consistent with the
            # legacy path (which capped kg first then re-derived mol).
            available_kg = composition_kg.get(oxide, 0.0)
            kg_oxide_reduced = min(kg_oxide_reduced, available_kg)
            moles_reduced = kg_oxide_reduced * 1000.0 / M_oxide_gmol

            if kg_oxide_reduced <= 1e-10:
                continue

            diagnostic["oxides_reduced_kg"][oxide] = kg_oxide_reduced
            diagnostic["oxides_reduced_mol"][oxide] = moles_reduced
            oxide_mol_total[oxide] = (
                oxide_mol_total.get(oxide, 0.0) + moles_reduced
            )

            metal_info = OXIDE_TO_METAL.get(oxide)
            if not metal_info:
                # No metal mapping -> skip metal/O2 credit. Mirrors
                # legacy step_hour: in the legacy path the oxide stays
                # in ``oxides_reduced_mol`` but no metal/O2 credit
                # accumulates, which would produce an unbalanced
                # ledger transition AT COMMIT TIME.  In the post-
                # kernel-flip world that hits the kernel's atom-
                # balance gate (AtomBalanceError) -- the correct
                # failure surface.  Today this branch is unreachable
                # in practice: every oxide in DECOMP_VOLTAGES has an
                # OXIDE_TO_METAL entry (verified by the pre-flip
                # ferric-oxide-after-wustite test).  A future
                # additions of an oxide to DECOMP_VOLTAGES without an
                # OXIDE_TO_METAL pair would surface here.
                continue
            metal, n_met, n_oxy = metal_info
            M_metal_gmol = MOLAR_MASS[metal]
            metal_mol = moles_reduced * n_met
            metal_kg = metal_mol * M_metal_gmol / 1000.0
            diagnostic["metals_produced_kg"][metal] = (
                diagnostic["metals_produced_kg"].get(metal, 0.0) + metal_kg
            )
            diagnostic["metals_produced_mol"][metal] = (
                diagnostic["metals_produced_mol"].get(metal, 0.0) + metal_mol
            )
            metal_mol_total[metal] = (
                metal_mol_total.get(metal, 0.0) + metal_mol
            )

            # O2 produced. n_oxy = atoms of O per oxide formula unit
            # (e.g. SiO2 -> n_oxy=2 -> 1 mol O2 per mol SiO2).
            O2_mol = moles_reduced * n_oxy / 2.0
            O2_kg = O2_mol * MOLAR_MASS["O2"] / 1000.0
            diagnostic["O2_produced_kg"] = (
                diagnostic["O2_produced_kg"] + O2_kg
            )
            diagnostic["O2_produced_mol"] = (
                diagnostic["O2_produced_mol"] + O2_mol
            )
            O2_mol_total += O2_mol

        # Energy consumed -- DIAGNOSTIC ONLY. The simulator tracks
        # energy via simulator/energy.py's EnergyTracker and the
        # _mre_energy_this_hr counter; energy is NOT a ledger account
        # and the proposal must never debit/credit anything energy-shaped.
        # Mirrors legacy: V * A * dt_hr / 1000  -> kWh.
        diagnostic["energy_kWh"] = voltage_V * current_A * dt_hr / 1000.0

        # Assemble the proposal in mol-native form.
        if not oxide_mol_total:
            # No metal-producing oxide actually reduced (e.g. only
            # entries that lack an OXIDE_TO_METAL mapping). Match the
            # legacy "no transition recorded" path.
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        debits_mol["process.cleaned_melt"] = dict(oxide_mol_total)
        if metal_mol_total:
            credits_mol["process.metal_phase"] = dict(metal_mol_total)
        if O2_mol_total > 0.0:
            credits_mol["terminal.oxygen_mre_anode_stored"] = {
                "O2": O2_mol_total,
            }

        atom_proof = build_atom_balance_proof(
            debits_mol, credits_mol, registry, resolve_species_formula,
        )

        proposal = LedgerTransitionProposal(
            debits=dict(debits_mol),
            credits=dict(credits_mol),
            reason="mre_electrolysis_reduction",
            atom_balance_proof=atom_proof,
        )

        return IntentResult(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            status="ok",
            transition=proposal,
            control_audit=control_audit,
            diagnostic=diagnostic,
        )

    # ------------------------------------------------------------------
    # Helpers (mirror ElectrolysisModel.nernst_voltage exactly + the
    # atom-balance proof shape shared with the prior authoritative
    # providers).
    # ------------------------------------------------------------------

    @staticmethod
    def _nernst_voltage(
        oxide: str,
        T_K: float,
        activity: float,
        *,
        gas_constant: float,
        faraday: float,
        decomp_voltages: Mapping[str, float],
        electrons_per_oxide: Mapping[str, int],
    ) -> float:
        """Nernst-adjusted decomposition voltage.

        Mirrors :meth:`ElectrolysisModel.nernst_voltage` line-for-line.
        Pure function; no provider state. Returns ``E0 + 1.0`` for
        essentially-depleted species (activity < 1e-10), same as legacy.
        """

        E0 = decomp_voltages.get(oxide, 2.5)
        n = electrons_per_oxide.get(oxide, 2)
        if activity <= 1e-10:
            return E0 + 1.0
        return E0 - (gas_constant * T_K) / (n * faraday) * math.log(activity)

    @staticmethod
    def _build_atom_balance_proof(
        debits: Mapping[str, Mapping[str, float]],
        credits: Mapping[str, Mapping[str, float]],
        registry: Mapping[str, Any],
        resolve_species_formula,
    ) -> dict[str, float]:
        """Delegate to the shared :func:`build_atom_balance_proof` helper.

        Atom balance for MRE: ``MO_n -> M + (n/2) O2`` -- one mol of
        ``MO_n`` carries one M atom and n O atoms; the credit side has
        one M atom (metal) + n O atoms (as n/2 mol of O2 -> n atoms).
        Net per element: 0. Multi-oxide ticks (e.g. FeO + Fe2O3 both
        active for the Fe metal credit) sum each side independently.
        """

        return build_atom_balance_proof(
            debits, credits, registry, resolve_species_formula
        )

    @staticmethod
    def _build_control_audit(
        request: "IntentRequest",
        *,
        applied_anode_fO2_log: float = 0.0,
    ) -> ControlAudit:
        """Build the ELECTROLYSIS_STEP control audit.

        The MRE cell holds the cathode at the applied voltage and strips
        anode O2 into ``terminal.oxygen_mre_anode_stored``; the anode
        gas activity is pure O2 (log10(fO2/bar) = 0.0 by construction).
        T and P are passed through unchanged -- the provider does not
        compute an updated melt temperature or pressure.

        When the caller does not pin ``request.fO2_log`` (the standard
        case from :meth:`ExtractionMixin._step_mre`), the kernel's
        :func:`validate_control_audit` ignores the fO2 field per its
        ``requested_value is None`` guard.  Reporting the applied value
        as ``0.0`` is still useful as a diagnostic / forensic record
        even when the validator skips it.
        """

        requested = {
            "temperature_C": float(request.temperature_C),
            "pressure_bar": float(request.pressure_bar),
            "fO2_log": (
                float(request.fO2_log)
                if request.fO2_log is not None
                else None
            ),
        }
        applied = dict(requested)
        applied["fO2_log"] = float(applied_anode_fO2_log)
        # Note documents the deliberate anode-fO2 fixing so a future
        # drift between request.fO2_log and applied is explained even
        # when the validator's None-guard would silently accept it.
        return ControlAudit(
            requested=requested,
            applied=applied,
            notes=(
                "anode evolves pure O2 -> applied fO2_log=0.0 by "
                "construction; T/P/V/I passed through unchanged",
            ),
        )

    @classmethod
    def _empty_result(
        cls,
        *,
        diagnostic_skipped: str = "",
        request: "IntentRequest | None" = None,
    ) -> IntentResult:
        diag: dict[str, Any] = {
            "oxides_reduced_kg": {},
            "oxides_reduced_mol": {},
            "metals_produced_kg": {},
            "metals_produced_mol": {},
            "O2_produced_kg": 0.0,
            "O2_produced_mol": 0.0,
            "energy_kWh": 0.0,
        }
        if diagnostic_skipped:
            diag["reason_skipped"] = diagnostic_skipped
        return IntentResult(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            status="ok",
            transition=None,
            control_audit=(
                cls._build_control_audit(request) if request is not None else None
            ),
            diagnostic=diag,
        )
