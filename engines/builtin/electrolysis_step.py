"""Builtin ELECTROLYSIS_STEP provider (MRE oxide-reduction ledger update).

Kernel-registered provider that owns the ``ELECTROLYSIS_STEP`` intent per
binding spec §2 ("MRE: anode O2, cathode metals, current efficiency")
and §3 (Builtin authoritative). Mirrors the Nernst / Faraday / current-
efficiency math in :meth:`simulator.electrolysis.ElectrolysisModel.step_hour`
exactly -- this is a refactor of where the
:class:`LedgerTransitionProposal` is built, not a re-derivation of the
oxide-reduction physics (the legacy module retains the canonical
``ElectrolysisModel`` for Nernst voltage lookups, voltage sequences, and
operator-facing summaries; the provider re-uses the same graph-first E0(T)
helper, static fallback anchor, ``ELECTRONS_PER_OXIDE`` table, and partition /
Faraday / CE formulas).

The provider:

- reads ``process.cleaned_melt`` (debit source for parent oxide),
  ``process.metal_phase`` or ``process.overhead_gas`` (product credit
  matching the Ellingham metal standard state used for E0), and
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
  * ``pO2_bar`` -- evolved-O2 activity/partial pressure at the anode,
    referenced to 1 bar for the Nernst gas-product term,
  * ``gas_product_fugacity_bar`` -- optional ``{metal: bar}`` fugacity
    ratios for gas-basis cathode products; omitted metals use the 1 bar
    standard state,
  * ``melt_fO2_log`` -- diagnostic melt oxygen fugacity used only for the
    inert Kress91 Fe-redox split reported in ``diagnostic``,
  * ``dt_hr`` -- tick duration in hours (always 1.0 in the current
    simulator, passed through explicitly so the provider stays unit-
    correct if the simulator's tick ever changes -- the t_s = 3600 s
    Faraday integration scales with this).

The MRE Nernst activity uses the shared gamma-X single-cation oxide
activity on the live mol composition. The wt% projection is still computed
from the account view for current-efficiency diagnostics, matching
:meth:`MeltState.composition_wt_pct` line-for-line.
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
``process.metal_phase`` / ``process.overhead_gas`` (credit),
``terminal.oxygen_mre_anode_stored`` (credit). The MRE anode O2 is its OWN bin per binding spec §3 and
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
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ELLINGHAM_METAL_PHASE_GAS,
)
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.mre_ladder import DECOMP_VOLTAGES, mre_decomposition_voltage_reference
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET, FARADAY

MRE_CURRENT_PARTITION_SOURCE = (
    "heuristic:activity_exp_FdV_over_RT_SEL-1_not_literature_grounded"
)
MRE_CURRENT_PARTITION_CERTIFICATION = "uncertified_current_partition"
MRE_INVALID_CONTROL_REFUSAL = "invalid_electrolysis_control"
MRE_INVALID_TARGET_REFUSAL = "invalid_allowed_oxides"
MRE_INVALID_GAS_FUGACITY_REFUSAL = "invalid_gas_product_fugacity"

MRE_DECOMP_VOLTAGE_PROVENANCE = {
    "NiO": {
        "standard_voltage_V": DECOMP_VOLTAGES["NiO"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -75.258559038,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": (
            "Hemingway 1990 Am. Mineral. 75:781; Robie & Hemingway; "
            "NEA Chemical Thermodynamics of Nickel"
        ),
        "status": "cited_raw_thermo",
    },
    "Na2O": {
        "standard_voltage_V": DECOMP_VOLTAGES["Na2O"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -96.485332100,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": None,
        "status": "legacy_uncited_voltage_pending_activity_vapor_grounding",
    },
    "K2O": {
        "standard_voltage_V": DECOMP_VOLTAGES["K2O"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -96.485332100,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": None,
        "status": "legacy_uncited_voltage_pending_activity_vapor_grounding",
    },
    "FeO": {
        "standard_voltage_V": DECOMP_VOLTAGES["FeO"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -144.727998150,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "O'Neill 1988 Fe-O emf; Chase 1998/NIST-JANAF",
        "status": "cited_raw_thermo",
    },
    "Fe2O3": {
        "standard_voltage_V": DECOMP_VOLTAGES["Fe2O3"],
        "electrons_per_formula": 6,
        "delta_gf_kJ_per_mol_formula": -521.020793340,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": None,
        "status": "reference_only_uncited_legacy_not_live_full_reduction_rung",
    },
    "Cr2O3": {
        "standard_voltage_V": DECOMP_VOLTAGES["Cr2O3"],
        "electrons_per_formula": 6,
        "delta_gf_kJ_per_mol_formula": -549.966392970,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "Chase 1998/NIST-JANAF; Barin",
        "status": "cited_raw_thermo_modest_confidence",
    },
    "MnO": {
        "standard_voltage_V": DECOMP_VOLTAGES["MnO"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -202.619197410,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "Chase 1998/NIST-JANAF; Barin",
        "status": "cited_raw_thermo_modest_confidence",
    },
    "SiO2": {
        "standard_voltage_V": DECOMP_VOLTAGES["SiO2"],
        "electrons_per_formula": 4,
        "delta_gf_kJ_per_mol_formula": -559.614926180,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "Chase 1998/NIST-JANAF",
        "status": "cited_raw_thermo",
    },
    "TiO2": {
        "standard_voltage_V": DECOMP_VOLTAGES["TiO2"],
        "electrons_per_formula": 4,
        "delta_gf_kJ_per_mol_formula": -656.100258280,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "Chase 1998/NIST-JANAF; Barin",
        "status": "cited_raw_thermo",
    },
    "Al2O3": {
        "standard_voltage_V": DECOMP_VOLTAGES["Al2O3"],
        "electrons_per_formula": 6,
        "delta_gf_kJ_per_mol_formula": -1128.878385570,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": "Chase 1998/NIST-JANAF; Barin",
        "status": "cited_raw_thermo",
    },
    "MgO": {
        "standard_voltage_V": DECOMP_VOLTAGES["MgO"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -424.535461240,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": None,
        "status": "legacy_uncited_voltage_pending_thermo_source",
    },
    "CaO": {
        "standard_voltage_V": DECOMP_VOLTAGES["CaO"],
        "electrons_per_formula": 2,
        "delta_gf_kJ_per_mol_formula": -482.426660500,
        "delta_gf_relation": "DeltaGf = -E*n*F",
        "delta_gf_source": None,
        "status": "legacy_uncited_voltage_pending_thermo_source",
    },
}

for _oxide, _row in MRE_DECOMP_VOLTAGE_PROVENANCE.items():
    _reference = mre_decomposition_voltage_reference(_oxide)
    if _reference is None:
        continue
    _row["standard_voltage_V"] = _reference.voltage
    _row["standard_voltage_temperature_K"] = _reference.temperature_K
    _row["standard_voltage_authority"] = _reference.authority
    _row["standard_voltage_authoritative"] = _reference.authoritative
    _row["standard_voltage_status"] = _reference.status
    _row["ellingham_species"] = _reference.ellingham_species
    _row["delta_gf_kJ_per_mol_formula"] = (
        -_reference.voltage
        * float(_row["electrons_per_formula"])
        * FARADAY
        / 1000.0
    )
    if _reference.authoritative:
        _row["status"] = "authoritative_ellingham_graph"
        _row["delta_g_kJ_per_mol_O2"] = (
            -_reference.voltage * 4.0 * FARADAY / 1000.0
        )
        _row["delta_g_relation"] = "DeltaG_dissoc = -E*4F"
    elif _reference.authority == "ellingham_graph":
        _row["status"] = "diagnostic_ellingham_graph"
        _row["delta_g_kJ_per_mol_O2"] = (
            -_reference.voltage * 4.0 * FARADAY / 1000.0
        )
        _row["delta_g_relation"] = "DeltaG_dissoc = -E*4F"
    else:
        _row["status"] = "ellingham_fallback"


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
        "process.overhead_gas",
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
            ELECTRONS_PER_OXIDE,
            FERRIC_TO_FERROUS_ELECTRONS,
            FERRIC_TO_FERROUS_FEO_PER_FE2O3,
            FERRIC_TO_FERROUS_O2_PER_FE2O3,
            FERRIC_TO_FERROUS_REFERENCE_V,
            FERRIC_TO_FERROUS_REFERENCE_STATUS,
            MRE_MULTI_OXIDE_PARTITION_REFUSAL,
            MRE_RAW_MARGIN_REFUSAL,
            MRE_RAW_MARGIN_GUARDED_OXIDES,
            MRE_CERTIFICATION_DENYLIST_REASON,
            MRE_CERTIFICATION_EVIDENCE_CLASS,
            MRE_CURRENT_PARTITION_CERTIFICATION,
            MRE_CURRENT_PARTITION_SOURCE,
            MRE_FIXED_REDUCIBLE_OXIDES,
            MRE_NORTH_STAR_POSTURE,
            MRE_OPTIONAL_BANNER,
            CURRENT_EFFICIENCY_MODEL_ID,
            coerce_gas_product_fugacity_bar,
            current_efficiency_diagnostic,
            mre_selectivity_weight,
            uncertified_multi_oxide_partition_targets,
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

        controls = unpack_controls(request)
        validated, invalid_controls = self._validate_controls(request, controls)
        if invalid_controls:
            return self._invalid_result(
                request,
                reason=MRE_INVALID_CONTROL_REFUSAL,
                invalid=invalid_controls,
            )
        voltage_V = validated["voltage_V"]
        current_A = validated["current_A"]
        dt_hr = validated["dt_hr"]
        pO2_bar = validated["pO2_bar"]
        T_C = validated["temperature_C"]
        T_K = T_C + CELSIUS_TO_KELVIN_OFFSET
        try:
            allowed_oxides = self._coerce_allowed_oxides(
                controls.get("allowed_oxides"),
                known_oxides=frozenset(MRE_FIXED_REDUCIBLE_OXIDES),
            )
        except (TypeError, ValueError) as exc:
            return self._invalid_result(
                request,
                reason=MRE_INVALID_TARGET_REFUSAL,
                invalid={"allowed_oxides": str(exc)},
            )
        try:
            gas_product_fugacity_bar = coerce_gas_product_fugacity_bar(
                controls.get("gas_product_fugacity_bar")
            )
        except (TypeError, ValueError) as exc:
            return self._invalid_result(
                request,
                reason=MRE_INVALID_GAS_FUGACITY_REFUSAL,
                invalid={"gas_product_fugacity_bar": str(exc)},
            )
        melt_fO2_log = self._coerce_optional_float(
            controls.get("melt_fO2_log", request.fO2_log)
        )
        if melt_fO2_log is None and request.fO2_log is not None:
            melt_fO2_log = float(request.fO2_log)
        control_audit = self._build_control_audit(
            request, applied_anode_fO2_log=math.log10(pO2_bar),
        )

        # Compute kg for cap checks and FeO current-efficiency diagnostics.
        # Nernst activity below stays on the shared gamma-X single-cation mol
        # basis used by the vapor path.
        melt_mol = dict(
            request.account_view.accounts.get("process.cleaned_melt", {}) or {}
        )
        overhead_mol = {
            str(species): float(mol)
            for species, mol in (
                request.account_view.accounts.get("process.overhead_gas", {}) or {}
            ).items()
            if float(mol) > 0.0
        }
        total_overhead_mol = sum(overhead_mol.values())
        if not melt_mol:
            return self._empty_result(
                diagnostic_skipped="empty melt",
                request=request,
                applied_anode_fO2_log=math.log10(pO2_bar),
                melt_fO2_log=melt_fO2_log,
                pressure_bar=request.pressure_bar,
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
        feo_fraction = 0.0
        if total_kg > 0.0:
            feo_fraction = max(0.0, composition_kg.get("FeO", 0.0)) / total_kg

        # Result skeleton mirrors ElectrolysisModel.step_hour exactly so
        # the legacy caller's post-processing path can consume the
        # diagnostic dict as-is (used for cathode-routing deltas and
        # the effective-current Faraday-readback in _step_mre).
        diagnostic: dict[str, Any] = {
            "oxides_reduced_kg": {},
            "oxides_reduced_mol": {},
            "metals_produced_kg": {},
            "metals_produced_mol": {},
            "gas_products_produced_kg": {},
            "gas_products_produced_mol": {},
            "oxides_produced_kg": {},
            "oxides_produced_mol": {},
            "oxide_charge_electrons": {},
            "O2_produced_kg": 0.0,
            "O2_produced_mol": 0.0,
            "energy_kWh": 0.0,
            "mre_north_star_posture": MRE_NORTH_STAR_POSTURE,
            "mre_optional_banner": MRE_OPTIONAL_BANNER,
            "certification_evidence_class": MRE_CERTIFICATION_EVIDENCE_CLASS,
            "certification_allowed": False,
            "certification_denylist_reason": MRE_CERTIFICATION_DENYLIST_REASON,
            "current_partition_source": MRE_CURRENT_PARTITION_SOURCE,
            "current_partition_certified": False,
            "yield_certification": MRE_CURRENT_PARTITION_CERTIFICATION,
            "current_efficiency_model": CURRENT_EFFICIENCY_MODEL_ID,
            "current_efficiency_feo_fraction": feo_fraction,
            "current_efficiency_by_oxide": {},
            "mre_activity_model": "gamma_x_single_cation",
            "mre_oxide_activity_by_oxide": {},
            "mre_parent_activity_exponent_by_oxide": {},
            "mre_gas_product_fugacity_bar_by_oxide": {},
            "mre_gas_product_fugacity_source_by_oxide": {},
            "mre_decomposition_voltage_authority_by_oxide": {},
            "mre_decomposition_voltage_authoritative_by_oxide": {},
            "mre_decomposition_voltage_status_by_oxide": {},
            "mre_metal_product_phase_by_oxide": {},
            "mre_ellingham_phase_basis_by_oxide": {},
            "mre_raw_graph_requirement_V_by_oxide": {},
            "mre_raw_voltage_margin_V_by_oxide": {},
            "mre_raw_margin_refused_targets": {},
            "mre_phase_refused_targets": {},
            "melt_fO2_log": melt_fO2_log,
            "fe_redox_policy": str(request.fe_redox_policy),
            "fe_redox_split": self._compute_fe_redox_split_diagnostic(
                composition_kg,
                total_kg=total_kg,
                T_K=T_K,
                pressure_bar=request.pressure_bar,
                melt_fO2_log=melt_fO2_log,
            ),
            "fe2o3_fixed_full_reduction_skipped": True,
        }

        if total_kg <= 0.0 or voltage_V <= 0.0 or current_A <= 0.0:
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        # None means "no operator target rung -> reduce every reducible
        # oxide". An EMPTY list is a real selectivity filter ("operator
        # targeted a rung unreachable within the voltage cap"), NOT an
        # absent filter: it must reduce NOTHING in the fixed-oxide MRE
        # reduction loop below. A truthy `if allowed_oxides_raw:` test
        # collapses [] into None and silently WIDENS an empty filter to
        # reduce-all -- a silent fallback the mandate forbids (BUG-140).
        # (Scope: the Fe2O3 ferric->ferrous conversion further down is a
        # separate redox path that intentionally ignores this operator
        # filter, so "reduce nothing" scopes the fixed-oxide loop only.)
        # The production caller (simulator/extraction.py) early-returns on
        # the empty-sequence case today, so this is defence-in-depth that
        # pins the None-vs-empty contract correct-by-construction.
        # Find all reducible species at this voltage. Mirrors
        # ElectrolysisModel.step_hour line-for-line: same graph-first E0,
        # same Nernst formula, same 1e-6 kg gate, same gamma-X activity.
        reducible: list[tuple[str, float, float, float, str, Any]] = []
        for oxide in MRE_FIXED_REDUCIBLE_OXIDES:
            if allowed_oxides is not None and oxide not in allowed_oxides:
                continue
            if oxide not in composition_kg:
                continue
            if composition_kg.get(oxide, 0.0) < 1e-6:
                continue
            activity_reference = melt_oxide_activity(oxide, melt_mol)
            activity = (
                0.0
                if activity_reference is None
                else max(0.0, float(activity_reference.activity))
            )
            voltage_reference = mre_decomposition_voltage_reference(
                oxide,
                temperature_K=T_K,
            )
            diagnostic["mre_oxide_activity_by_oxide"][oxide] = activity
            if voltage_reference is not None:
                diagnostic["mre_decomposition_voltage_authority_by_oxide"][oxide] = (
                    voltage_reference.authority
                )
                diagnostic["mre_decomposition_voltage_authoritative_by_oxide"][oxide] = (
                    voltage_reference.authoritative
                )
                diagnostic["mre_decomposition_voltage_status_by_oxide"][oxide] = (
                    voltage_reference.status
                )
                diagnostic["mre_metal_product_phase_by_oxide"][oxide] = (
                    voltage_reference.metal_product_phase
                )
                diagnostic["mre_ellingham_phase_basis_by_oxide"][oxide] = (
                    voltage_reference.ellingham_phase_basis
                )
            metal, cations_per_oxide, _n_oxy = OXIDE_TO_METAL.get(
                oxide, ("", 1, 0)
            )
            diagnostic["mre_parent_activity_exponent_by_oxide"][oxide] = (
                cations_per_oxide
            )
            if (
                voltage_reference is not None
                and voltage_reference.metal_product_phase
                == ELLINGHAM_METAL_PHASE_GAS
            ):
                if metal in gas_product_fugacity_bar:
                    metal_fugacity_bar = gas_product_fugacity_bar[metal]
                    fugacity_source = "control_inputs.gas_product_fugacity_bar"
                elif (
                    request.pressure_bar > 0.0
                    and total_overhead_mol > 0.0
                    and overhead_mol.get(metal, 0.0) > 0.0
                ):
                    metal_fugacity_bar = (
                        request.pressure_bar
                        * overhead_mol[metal]
                        / total_overhead_mol
                    )
                    fugacity_source = (
                        "account_view.process.overhead_gas_ideal_partial_pressure"
                    )
                else:
                    # E0 for gas-basis products is referenced to pure gas at
                    # 1 bar.  With neither an explicit control nor an evolved
                    # headspace partial pressure, that standard state remains
                    # a computable non-empty boundary condition.  Preserve it
                    # with loud provenance; invalid explicit controls already
                    # refuse atomically during coercion above.
                    metal_fugacity_bar = 1.0
                    fugacity_source = "standard_state_default_1_bar"
                diagnostic["mre_gas_product_fugacity_bar_by_oxide"][oxide] = (
                    metal_fugacity_bar
                )
                diagnostic["mre_gas_product_fugacity_source_by_oxide"][oxide] = (
                    fugacity_source
                )
            else:
                metal_fugacity_bar = 1.0
            E_nernst = self._nernst_voltage(
                oxide,
                T_K,
                activity,
                gas_constant=GAS_CONSTANT,
                faraday=FARADAY,
                electrons_per_oxide=ELECTRONS_PER_OXIDE,
                oxide_to_metal=OXIDE_TO_METAL,
                pO2_bar=pO2_bar,
                metal_fugacity_bar=metal_fugacity_bar,
                metal_product_phase=(
                    None
                    if voltage_reference is None
                    else voltage_reference.metal_product_phase
                ),
            )
            fallback_margin_V = voltage_V - E_nernst
            raw_margin_V = None
            if (
                voltage_reference is not None
                and voltage_reference.raw_graph_voltage_V is not None
            ):
                # The Nernst activity/pO2 shift is identical for the selected
                # fallback and raw graph E0.  Replacing E0 therefore shifts the
                # full requirement by exactly (E0_raw - E0_fallback).
                raw_requirement_V = (
                    E_nernst
                    + voltage_reference.raw_graph_voltage_V
                    - voltage_reference.voltage
                )
                raw_margin_V = voltage_V - raw_requirement_V
                diagnostic["mre_raw_graph_requirement_V_by_oxide"][oxide] = (
                    raw_requirement_V
                )
                diagnostic["mre_raw_voltage_margin_V_by_oxide"][oxide] = raw_margin_V

            if (
                fallback_margin_V > 0.0
                and oxide in MRE_RAW_MARGIN_GUARDED_OXIDES
                and voltage_reference is not None
                and not voltage_reference.authoritative
                and raw_margin_V is not None
                and raw_margin_V <= 0.0
            ):
                diagnostic["mre_raw_margin_refused_targets"][oxide] = {
                    "fallback_requirement_V": E_nernst,
                    "fallback_margin_V": fallback_margin_V,
                    "raw_requirement_V": raw_requirement_V,
                    "raw_margin_V": raw_margin_V,
                    "voltage_status": voltage_reference.status,
                }
                continue

            if fallback_margin_V > 0.0:
                product_phase = (
                    None if voltage_reference is None
                    else voltage_reference.metal_product_phase
                )
                if product_phase not in (
                    ELLINGHAM_METAL_PHASE_CONDENSED,
                    ELLINGHAM_METAL_PHASE_GAS,
                ):
                    diagnostic["mre_phase_refused_targets"][oxide] = {
                        "reason": "mre_product_phase_missing_or_unknown",
                        "metal_product_phase": product_phase,
                        "voltage_status": (
                            None if voltage_reference is None
                            else voltage_reference.status
                        ),
                    }
                    continue
                overvoltage = fallback_margin_V
                reducible.append((
                    oxide,
                    E_nernst,
                    overvoltage,
                    activity,
                    "oxide_to_metal",
                    voltage_reference,
                ))

        if composition_kg.get("Fe2O3", 0.0) >= 1e-6:
            activity_reference = melt_oxide_activity("Fe2O3", melt_mol)
            activity = (
                0.0
                if activity_reference is None
                else max(0.0, float(activity_reference.activity))
            )
            feo_activity_reference = melt_oxide_activity("FeO", melt_mol)
            feo_activity = (
                0.0
                if feo_activity_reference is None
                else max(0.0, float(feo_activity_reference.activity))
            )
            E_ferric = self._ferric_to_ferrous_voltage(
                T_K,
                activity,
                gas_constant=GAS_CONSTANT,
                faraday=FARADAY,
                reference_V=FERRIC_TO_FERROUS_REFERENCE_V,
                electrons=FERRIC_TO_FERROUS_ELECTRONS,
                o2_per_fe2o3=FERRIC_TO_FERROUS_O2_PER_FE2O3,
                pO2_bar=pO2_bar,
                feo_activity=feo_activity,
            )
            if E_ferric < voltage_V:
                reducible.append((
                    "Fe2O3",
                    E_ferric,
                    voltage_V - E_ferric,
                    activity,
                    "ferric_to_ferrous",
                    None,
                ))

        if not reducible:
            if voltage_V > 0.0 and current_A > 0.0:
                diagnostic["energy_kWh"] = voltage_V * current_A * dt_hr / 1000.0
            status = "ok"
            if diagnostic["mre_raw_margin_refused_targets"]:
                status = "refused"
                diagnostic["reason_refused"] = MRE_RAW_MARGIN_REFUSAL
            if diagnostic["mre_phase_refused_targets"]:
                status = "refused"
                diagnostic["reason_refused"] = "mre_product_phase_mismatch_refused"
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status=status,
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        refused_targets = uncertified_multi_oxide_partition_targets(reducible)
        if refused_targets:
            diagnostic["energy_kWh"] = voltage_V * current_A * dt_hr / 1000.0
            diagnostic["reason_refused"] = MRE_MULTI_OXIDE_PARTITION_REFUSAL
            diagnostic["reducible_oxide_targets"] = refused_targets
            return IntentResult(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                status="refused",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )

        # Partition current among reducible species (selectivity:
        # weight by concentration * exp(F*dV/(R*T)), exponent capped at 3).
        # Mirrors ElectrolysisModel.step_hour. [SEL-1]
        weights: dict[str, float] = {}
        for oxide, _E, dV, a, _mode, _reference in reducible:
            weights[oxide] = mre_selectivity_weight(a, dV, T_K)
        total_weight = sum(weights.values())
        if total_weight <= 0.0:
            if voltage_V > 0.0 and current_A > 0.0:
                diagnostic["energy_kWh"] = voltage_V * current_A * dt_hr / 1000.0
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

        # Accumulators for the metal/O2 sides (multiple fixed oxides may
        # contribute to the same metal, so additive bookkeeping mirrors legacy
        # exactly). Fe2O3 is not a fixed MRE oxide in live redox mode.
        metal_mol_total: dict[str, float] = {}
        gas_mol_total: dict[str, float] = {}
        O2_mol_total = 0.0
        oxide_mol_total: dict[str, float] = {}
        billable_current_A = 0.0
        any_capped = False
        oxide_produced_mol_total: dict[str, float] = {}

        for oxide, _E, dV, _a, mode, voltage_reference in reducible:
            fraction = weights[oxide] / total_weight
            I_species = current_A * fraction

            # Current efficiency (CE-1). dV >= 0 by construction
            # (reducible filter); FeO fraction is fixed per tick.
            ce_diagnostic = current_efficiency_diagnostic(dV, feo_fraction)
            eta_CE = float(ce_diagnostic["eta"])
            diagnostic["current_efficiency_by_oxide"][oxide] = ce_diagnostic

            # Faraday's law (FARADAY-1). n electrons per formula unit.
            n_e = (
                FERRIC_TO_FERROUS_ELECTRONS
                if mode == "ferric_to_ferrous"
                else ELECTRONS_PER_OXIDE.get(oxide, 2)
            )
            M_oxide_gmol = MOLAR_MASS.get(oxide, 100.0)

            uncapped_moles_reduced = (
                I_species * eta_CE * t_s
            ) / (n_e * FARADAY)
            kg_oxide_reduced = uncapped_moles_reduced * M_oxide_gmol / 1000.0

            # Cap at melt availability. Re-derive mol from the capped
            # kg to keep mol-native bookkeeping consistent with the
            # legacy path (which capped kg first then re-derived mol).
            available_kg = composition_kg.get(oxide, 0.0)
            if available_kg < kg_oxide_reduced:
                any_capped = True
            kg_oxide_reduced = min(kg_oxide_reduced, available_kg)
            moles_reduced = kg_oxide_reduced * 1000.0 / M_oxide_gmol
            species_cap_ratio = 0.0
            if uncapped_moles_reduced > 0.0:
                species_cap_ratio = min(1.0, moles_reduced / uncapped_moles_reduced)
            billable_current_A += I_species * species_cap_ratio

            if kg_oxide_reduced <= 1e-10:
                continue

            diagnostic["oxides_reduced_kg"][oxide] = kg_oxide_reduced
            diagnostic["oxides_reduced_mol"][oxide] = moles_reduced
            diagnostic["oxide_charge_electrons"][oxide] = n_e
            oxide_mol_total[oxide] = (
                oxide_mol_total.get(oxide, 0.0) + moles_reduced
            )

            if mode == "ferric_to_ferrous":
                feo_mol = moles_reduced * FERRIC_TO_FERROUS_FEO_PER_FE2O3
                feo_kg = feo_mol * MOLAR_MASS["FeO"] / 1000.0
                diagnostic["oxides_produced_kg"]["FeO"] = (
                    diagnostic["oxides_produced_kg"].get("FeO", 0.0)
                    + feo_kg
                )
                diagnostic["oxides_produced_mol"]["FeO"] = (
                    diagnostic["oxides_produced_mol"].get("FeO", 0.0)
                    + feo_mol
                )
                oxide_produced_mol_total["FeO"] = (
                    oxide_produced_mol_total.get("FeO", 0.0) + feo_mol
                )
                O2_mol = moles_reduced * FERRIC_TO_FERROUS_O2_PER_FE2O3
                O2_kg = O2_mol * MOLAR_MASS["O2"] / 1000.0
                diagnostic["O2_produced_kg"] += O2_kg
                diagnostic["O2_produced_mol"] += O2_mol
                O2_mol_total += O2_mol
                diagnostic["fe_redox_split"] = {
                    **dict(diagnostic.get("fe_redox_split") or {}),
                    "diagnostic_only": False,
                    "consumed_by_behavior": True,
                    "behavior": "ferric_to_ferrous_mre_conversion",
                }
                diagnostic.setdefault("uncertified_yield", {})["FeO"] = {
                    "source_species": "Fe2O3",
                    "produced_species": "FeO",
                    "produced_kg": feo_kg,
                    "produced_mol": feo_mol,
                    "certification": "uncertified_ferric_to_ferrous_reference",
                    "reference_V": FERRIC_TO_FERROUS_REFERENCE_V,
                    "reference_status": FERRIC_TO_FERROUS_REFERENCE_STATUS,
                    "reason": (
                        "FERRIC_TO_FERROUS_REFERENCE_V is heuristic and "
                        "not anchored to grounded yield data"
                    ),
                }
                continue

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
            product_phase = (
                ELLINGHAM_METAL_PHASE_CONDENSED
                if voltage_reference is None
                else voltage_reference.metal_product_phase
            )
            if product_phase == ELLINGHAM_METAL_PHASE_GAS:
                diagnostic["gas_products_produced_kg"][metal] = (
                    diagnostic["gas_products_produced_kg"].get(metal, 0.0)
                    + metal_kg
                )
                diagnostic["gas_products_produced_mol"][metal] = (
                    diagnostic["gas_products_produced_mol"].get(metal, 0.0)
                    + metal_mol
                )
                gas_mol_total[metal] = (
                    gas_mol_total.get(metal, 0.0) + metal_mol
                )
            else:
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
        # Charge final depletion hours per species: a depleted species is
        # scaled by its own capped Faradaic share, while uncapped species keep
        # their full current share. Preserve exact commanded energy when no cap
        # bound.
        energy_current_A = billable_current_A if any_capped else current_A
        diagnostic["energy_kWh"] = (
            voltage_V * energy_current_A * dt_hr / 1000.0
        )

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
        if oxide_produced_mol_total:
            credits_mol["process.cleaned_melt"] = dict(oxide_produced_mol_total)
        if metal_mol_total:
            credits_mol["process.metal_phase"] = dict(metal_mol_total)
        if gas_mol_total:
            credits_mol["process.overhead_gas"] = dict(gas_mol_total)
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
        electrons_per_oxide: Mapping[str, int],
        oxide_to_metal: Mapping[str, tuple[str, int, int]],
        pO2_bar: float = 1.0,
        metal_fugacity_bar: float = 1.0,
        metal_product_phase: str | None = None,
        decomp_voltages: Mapping[str, float] | None = None,
    ) -> float:
        """Nernst-adjusted decomposition voltage.

        Mirrors :meth:`ElectrolysisModel.nernst_voltage` line-for-line:
        ``E = E0 + (RT/nF) ln(Q)``. Pure function; no provider state.
        """

        reference = None
        if decomp_voltages is None:
            reference = mre_decomposition_voltage_reference(oxide, temperature_K=T_K)
            E0 = 2.5 if reference is None else reference.voltage
        else:
            E0 = decomp_voltages.get(oxide, 2.5)
        n = electrons_per_oxide.get(oxide, 2)
        if metal_product_phase is None and reference is not None:
            metal_product_phase = reference.metal_product_phase

        # Premise: the melt model reports single-cation MOx activity, while
        # E0 and n are per parent oxide. Algebra and unit check live in the
        # shared legacy helper; the standard-state ratios make ln(Q)
        # dimensionless. Sanity: condensed monoxides at unit activities keep E0.
        from simulator.electrolysis import mre_parent_oxide_log_quotient

        log_Q = mre_parent_oxide_log_quotient(
            oxide,
            activity,
            pO2_bar,
            metal_product_phase=metal_product_phase,
            metal_fugacity_bar=metal_fugacity_bar,
            oxide_to_metal=oxide_to_metal,
        )
        return E0 + (gas_constant * T_K) / (n * faraday) * log_Q

    @staticmethod
    def _ferric_to_ferrous_voltage(
        T_K: float,
        activity: float,
        *,
        gas_constant: float,
        faraday: float,
        reference_V: float,
        electrons: int,
        o2_per_fe2o3: float,
        pO2_bar: float,
        feo_activity: float = 1.0,
    ) -> float:
        from simulator.electrolysis import mre_ferric_log_quotient

        # Fe2O3 -> 2FeO + 0.5O2. The shared helper supplies both squared
        # single-cation activity terms; all activities are dimensionless.
        log_Q = mre_ferric_log_quotient(
            activity,
            feo_activity,
            pO2_bar,
            o2_per_fe2o3=o2_per_fe2o3,
        )
        return float(reference_V) + (
            gas_constant * float(T_K)
        ) / (int(electrons) * faraday) * log_Q

    @staticmethod
    def _validate_controls(
        request: IntentRequest,
        controls: Mapping[str, Any],
    ) -> tuple[dict[str, float], dict[str, str]]:
        raw_values = {
            "voltage_V": (
                0.0
                if controls.get("voltage_V") is None
                else controls.get("voltage_V")
            ),
            "current_A": (
                0.0
                if controls.get("current_A") is None
                else controls.get("current_A")
            ),
            "dt_hr": controls.get("dt_hr", 1.0),
            "pO2_bar": controls.get("pO2_bar", 1.0),
            "temperature_C": request.temperature_C,
            "pressure_bar": request.pressure_bar,
        }
        values: dict[str, float] = {}
        invalid: dict[str, str] = {}
        for name, raw in raw_values.items():
            if isinstance(raw, bool):
                invalid[name] = "boolean_not_allowed"
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError, OverflowError):
                invalid[name] = "not_numeric"
                continue
            if not math.isfinite(value):
                invalid[name] = "not_finite"
                continue
            values[name] = value

        nonnegative = ("voltage_V", "current_A", "pressure_bar")
        for name in nonnegative:
            if name in values and values[name] < 0.0:
                invalid[name] = "must_be_nonnegative"
        if "dt_hr" in values and values["dt_hr"] < 0.0:
            invalid["dt_hr"] = "must_be_nonnegative"
        if "pO2_bar" in values and values["pO2_bar"] <= 0.0:
            invalid["pO2_bar"] = "must_be_positive"
        if (
            "temperature_C" in values
            and values["temperature_C"] <= -CELSIUS_TO_KELVIN_OFFSET
        ):
            invalid["temperature_C"] = "must_be_above_absolute_zero"
        return values, invalid

    @staticmethod
    def _coerce_allowed_oxides(
        value: Any,
        *,
        known_oxides: frozenset[str],
    ) -> set[str] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise TypeError("must be a list, tuple, set, or frozenset")
        targets: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item:
                raise TypeError("each target must be a non-empty string")
            if item not in known_oxides:
                raise ValueError(f"unknown oxide target: {item}")
            targets.add(item)
        return targets

    @staticmethod
    def _invalid_result(
        request: IntentRequest,
        *,
        reason: str,
        invalid: Mapping[str, str],
    ) -> IntentResult:
        return IntentResult(
            intent=request.intent,
            status="refused",
            transition=None,
            diagnostic={
                "reason_refused": reason,
                "invalid_controls": dict(invalid),
                "energy_kWh": 0.0,
                "oxides_reduced_mol": {},
                "metals_produced_mol": {},
                "gas_products_produced_mol": {},
                "O2_produced_mol": 0.0,
            },
        )

    @staticmethod
    def _coerce_optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(coerced):
            return None
        return coerced

    @staticmethod
    def _compute_fe_redox_split_diagnostic(
        composition_kg: Mapping[str, float],
        *,
        total_kg: float,
        T_K: float,
        pressure_bar: float,
        melt_fO2_log: float | None,
    ) -> dict[str, Any]:
        from simulator.fe_redox import (
            feot_equivalent_wt_pct,
            floor_vacuum_pressure_bar,
            kress91_split,
            melt_mol_fractions_for_kress91,
        )
        pressure_control = floor_vacuum_pressure_bar(pressure_bar)

        base: dict[str, Any] = {
            "diagnostic_only": True,
            "consumed_by_behavior": False,
            "computed_fresh_from_account_view": True,
            "temperature_K": float(T_K),
            "pressure_bar": pressure_control,
            "melt_fO2_log": melt_fO2_log,
        }
        if total_kg <= 0.0 or melt_fO2_log is None or T_K <= 0.0:
            return {
                **base,
                "status": "unavailable",
                "feot_equiv_wt_pct": 0.0,
                "fe3_over_sigma_fe": 0.0,
                "ferric_frac": 0.0,
                "ferrous_frac": 0.0,
                "fe2o3_over_feo_molar": 0.0,
                "fe2o3_equiv_wt_pct": 0.0,
                "feo_equiv_wt_pct": 0.0,
                "source": "none:missing_melt_fO2_or_composition",
            }

        comp_wt = {
            oxide: max(0.0, float(kg)) / total_kg * 100.0
            for oxide, kg in composition_kg.items()
            if kg is not None and float(kg) > 0.0
        }
        feot_wt = feot_equivalent_wt_pct(comp_wt)
        mol_fractions = melt_mol_fractions_for_kress91(comp_wt)
        if feot_wt <= 0.0 or not mol_fractions:
            return {
                **base,
                "status": "no_iron",
                "feot_equiv_wt_pct": float(feot_wt),
                "fe3_over_sigma_fe": 0.0,
                "ferric_frac": 0.0,
                "ferrous_frac": 0.0,
                "fe2o3_over_feo_molar": 0.0,
                "fe2o3_equiv_wt_pct": 0.0,
                "feo_equiv_wt_pct": 0.0,
                "source": "simulator.fe_redox:kress91_split:no_iron",
                "authoritative": False,
                "extrapolation": False,
                "high_uncertainty": False,
            }

        split = kress91_split(
            fO2_log=float(melt_fO2_log),
            mol_fractions=mol_fractions,
            T_K=float(T_K),
            pressure_bar=pressure_control,
        )
        ratio = float(split["ratio"])
        fe3 = min(1.0, max(0.0, float(split["fe3"])))
        x_fe2o3 = float(split["x_fe2o3"])
        x_feo = float(split["x_feo"])
        weighted_total = (
            mol_fractions.get("SiO2", 0.0) * 60.0843
            + mol_fractions.get("TiO2", 0.0) * 79.8788
            + mol_fractions.get("Al2O3", 0.0) * 101.961
            + mol_fractions.get("MnO", 0.0) * 70.9375
            + mol_fractions.get("MgO", 0.0) * 40.3044
            + mol_fractions.get("CaO", 0.0) * 56.0774
            + mol_fractions.get("Na2O", 0.0) * 61.9789
            + mol_fractions.get("K2O", 0.0) * 94.196
            + mol_fractions.get("P2O5", 0.0) * 141.937
            + x_fe2o3 * 159.687
            + x_feo * 71.844
        )
        if weighted_total <= 0.0:
            fe2o3_wt = 0.0
            feo_wt = 0.0
        else:
            fe2o3_wt = 100.0 * x_fe2o3 * 159.687 / weighted_total
            feo_wt = 100.0 * x_feo * 71.844 / weighted_total
        return {
            **base,
            "status": "ok",
            "feot_equiv_wt_pct": float(feot_wt),
            "fe3_over_sigma_fe": fe3,
            "ferric_frac": fe3,
            "ferrous_frac": max(0.0, 1.0 - fe3),
            "fe2o3_over_feo_molar": ratio,
            "fe2o3_equiv_wt_pct": fe2o3_wt,
            "feo_equiv_wt_pct": feo_wt,
            "source": "simulator.fe_redox:kress91_split",
            "temperature_band_case": split.get("temperature_band_case"),
            "temperature_band_status": split.get("temperature_band_status"),
            "temperature_band_source": split.get("temperature_band_source"),
            "authoritative": bool(split.get("authoritative", False)),
            "extrapolation": bool(split.get("extrapolation", False)),
            "high_uncertainty": bool(split.get("high_uncertainty", False)),
        }

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
        O2 activity is the caller-supplied ``pO2_bar`` relative to the
        1 bar standard state. T and P are passed through unchanged -- the provider does not
        compute an updated melt temperature or pressure.

        :meth:`ExtractionMixin._step_mre` pins ``request.fO2_log`` to the
        melt redox state used by Kress91 diagnostics. The electrolysis Nernst
        term still applies the anode O2 activity from ``pO2_bar``; reporting
        that applied value as ``log10(pO2_bar)`` records the intentional
        cathode/anode split for the control audit.
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
        # Note documents the deliberate anode-fO2 application so a future
        # drift between request.fO2_log and applied is explained even
        # when the validator's None-guard would silently accept it.
        return ControlAudit(
            requested=requested,
            applied=applied,
            notes=(
                "anode O2 activity comes from pO2_bar relative to 1 bar; "
                "T/P/V/I passed through unchanged",
            ),
        )

    @classmethod
    def _empty_result(
        cls,
        *,
        diagnostic_skipped: str = "",
        request: "IntentRequest | None" = None,
        applied_anode_fO2_log: float = 0.0,
        melt_fO2_log: float | None = None,
        pressure_bar: float | None = None,
    ) -> IntentResult:
        fe_redox_policy = (
            str(request.fe_redox_policy) if request is not None else "intrinsic"
        )
        T_K = (
            float(request.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
            if request is not None else 0.0
        )
        split_pressure_bar = (
            float(pressure_bar)
            if pressure_bar is not None
            else (float(request.pressure_bar) if request is not None else 1.0)
        )
        diag: dict[str, Any] = {
            "oxides_reduced_kg": {},
            "oxides_reduced_mol": {},
            "metals_produced_kg": {},
            "metals_produced_mol": {},
            "oxides_produced_kg": {},
            "oxides_produced_mol": {},
            "oxide_charge_electrons": {},
            "O2_produced_kg": 0.0,
            "O2_produced_mol": 0.0,
            "energy_kWh": 0.0,
            "current_partition_source": MRE_CURRENT_PARTITION_SOURCE,
            "current_partition_certified": False,
            "yield_certification": MRE_CURRENT_PARTITION_CERTIFICATION,
            "melt_fO2_log": melt_fO2_log,
            "fe_redox_policy": fe_redox_policy,
            "fe_redox_split": cls._compute_fe_redox_split_diagnostic(
                {},
                total_kg=0.0,
                T_K=T_K,
                pressure_bar=split_pressure_bar,
                melt_fO2_log=melt_fO2_log,
            ),
            "fe2o3_fixed_full_reduction_skipped": True,
        }
        if diagnostic_skipped:
            diag["reason_skipped"] = diagnostic_skipped
        return IntentResult(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            status="ok",
            transition=None,
            control_audit=(
                cls._build_control_audit(
                    request,
                    applied_anode_fO2_log=applied_anode_fO2_log,
                )
                if request is not None else None
            ),
            diagnostic=diag,
        )
