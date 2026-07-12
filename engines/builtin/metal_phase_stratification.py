"""Authoritative mol-native disposition of metal-phase staging into two alloys."""

from __future__ import annotations

import math

from engines.builtin._common import (
    build_atom_balance_proof,
    diagnostic_control_audit,
    reject_wrong_intent,
    unpack_controls,
)
from simulator.account_ids import (
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
    METAL_PHASE_ACCOUNT,
)
from simulator.chemistry.kernel.capabilities import CapabilityProfile, ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult, LedgerTransitionProposal
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.material_densities import (
    alloy_density_kg_m3,
    alloy_density_uncertainty_relative_fraction,
    buoyancy_verdict,
    resolve_melt_density_kg_m3,
)
from simulator.metal_stratification import (
    BOTTOM_POOL_SPECIES,
    FLOAT_LAYER_SPECIES,
    first_order_transfer_fraction,
    target_pool,
)


class BuiltinMetalPhaseStratificationProvider(ChemistryProvider):
    """Pure-move provider; no atoms are created and no extraction tap is fired."""

    name = "builtin-metal-phase-stratification"
    DECLARED_ACCOUNTS = frozenset({
        METAL_PHASE_ACCOUNT,
        METAL_BOTTOM_POOL_ACCOUNT,
        METAL_FLOAT_LAYER_ACCOUNT,
    })

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.name,
            intents=frozenset({ChemistryIntent.METAL_PHASE_STRATIFICATION}),
            is_authoritative_for=frozenset({
                ChemistryIntent.METAL_PHASE_STRATIFICATION,
            }),
            declared_accounts=self.DECLARED_ACCOUNTS,
            consumes_fO2=False,
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        from simulator.accounting.formulas import resolve_species_formula

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.METAL_PHASE_STRATIFICATION
        )
        if wrong_intent is not None:
            return wrong_intent
        controls = unpack_controls(request)
        mode = str(controls.get("mode") or "stratify")
        if mode not in {"stratify", "restore_staging"}:
            raise ValueError("mode must be 'stratify' or 'restore_staging'")
        k_mix_per_hr = float(controls.get("k_mix_per_hr", 0.0))
        dt_hr = float(controls.get("dt_hr", 1.0))
        mix_fraction = first_order_transfer_fraction(k_mix_per_hr, dt_hr)

        account_view = request.account_view.accounts
        before = {
            account: {
                str(species): max(0.0, float(amount))
                for species, amount in dict(account_view.get(account, {})).items()
                if float(amount) > 0.0
            }
            for account in sorted(self.DECLARED_ACCOUNTS)
        }
        after = {account: dict(species_mol) for account, species_mol in before.items()}
        staging = after[METAL_PHASE_ACCOUNT]
        bottom = after[METAL_BOTTOM_POOL_ACCOUNT]
        floating = after[METAL_FLOAT_LAYER_ACCOUNT]
        classified = BOTTOM_POOL_SPECIES | FLOAT_LAYER_SPECIES

        assigned_mol: dict[str, dict[str, float]] = {
            "bottom_pool": {},
            "float_layer": {},
        }
        transferred_mol: dict[str, dict[str, float]] = {
            "float_to_bottom": {},
            "bottom_to_float": {},
        }
        carried_mol: dict[str, dict[str, float]] = {
            "bottom_pool": {},
            "float_layer": {},
        }
        si_destination_buoyancy: dict[str, float | str] = {}
        if mode == "restore_staging":
            # Premise: stratification is diagnostic-only in this gate, so the
            # next physics tick must see the legacy staging account unchanged.
            # Algebra: staging' = staging + bottom + float; pools' = 0. Unit
            # check: mol + mol = mol. Sanity: each species total is invariant.
            for pool in (bottom, floating):
                for species, amount in tuple(pool.items()):
                    staging[species] = staging.get(species, 0.0) + amount
                pool.clear()
        else:
            raw_prior = controls.get("prior_pool_mol")
            prior = raw_prior if isinstance(raw_prior, dict) else {}
            prior_bottom = (
                prior.get("bottom_pool", {})
                if isinstance(prior.get("bottom_pool", {}), dict)
                else {}
            )
            prior_float = (
                prior.get("float_layer", {})
                if isinstance(prior.get("float_layer", {}), dict)
                else {}
            )
            for species in sorted(classified):
                if (
                    bottom.get(species, 0.0) > 0.0
                    or floating.get(species, 0.0) > 0.0
                ):
                    continue
                current = staging.get(species, 0.0)
                old_bottom = float(prior_bottom.get(species, 0.0))
                old_float = float(prior_float.get(species, 0.0))
                if (
                    not math.isfinite(old_bottom)
                    or not math.isfinite(old_float)
                    or old_bottom < 0.0
                    or old_float < 0.0
                ):
                    raise ValueError(
                        f"prior_pool_mol[{species!r}] must be finite and non-negative"
                    )
                old_total = old_bottom + old_float
                if current <= 0.0 or old_total <= 0.0:
                    continue
                # Premise: legacy physics temporarily sees pooled metal in one
                # staging account, so pool-specific consumption is unknowable.
                # Algebra: surviving pool_i = old_pool_i *
                # min(1,current/old_total), while max(0,current-old_total)
                # remains a new birth. Unit check:
                # mol*dimensionless=mol. Sanity: 50/50 Si stays 50/50 before
                # the next ODE step; a 10% aggregate loss removes 10% from each.
                survival = min(1.0, current / old_total)
                carry_bottom = old_bottom * survival
                carry_float = old_float * survival
                if carry_bottom > 0.0:
                    bottom[species] = carry_bottom
                    carried_mol["bottom_pool"][species] = carry_bottom
                if carry_float > 0.0:
                    floating[species] = carry_float
                    carried_mol["float_layer"][species] = carry_float
                remaining = current - carry_bottom - carry_float
                if remaining > 1e-15:
                    staging[species] = remaining
                else:
                    staging.pop(species, None)

            # Same-hour Fe birth counts as an available scavenger, but Si is
            # born at the near-neutral interface and reaches Fe only through
            # k_mix. This preserves OFF -> top-Si and ON -> FeSi-bottom.
            for species in sorted(set(staging) & classified):
                amount = staging.pop(species, 0.0)
                birth_pool = (
                    "bottom_pool"
                    if species in BOTTOM_POOL_SPECIES
                    else "float_layer"
                )
                destination = bottom if birth_pool == "bottom_pool" else floating
                destination[species] = destination.get(species, 0.0) + amount
                assigned_mol[birth_pool][species] = amount

            candidate_bottom = dict(bottom)
            candidate_bottom["Si"] = (
                candidate_bottom.get("Si", 0.0) + floating.get("Si", 0.0)
            )
            if candidate_bottom.get("Si", 0.0) > 0.0:
                temperature_K = float(controls.get(
                    "temperature_K", float(request.temperature_C) + 273.15
                ))
                melt_density, _ = resolve_melt_density_kg_m3(
                    controls.get("melt_density_kg_m3")
                )
                candidate_density = alloy_density_kg_m3(
                    candidate_bottom, temperature_K
                )
                si_destination_buoyancy = buoyancy_verdict(
                    candidate_density,
                    melt_density,
                    alloy_uncertainty_relative_fraction=(
                        alloy_density_uncertainty_relative_fraction(candidate_bottom)
                    ),
                )

            for species in sorted(classified):
                target = target_pool(
                    species,
                    si_destination_verdict=str(
                        si_destination_buoyancy.get("verdict", "float")
                    ),
                )
                if target == "bottom_pool":
                    moved = floating.get(species, 0.0) * mix_fraction
                    if moved > 0.0:
                        floating[species] -= moved
                        bottom[species] = bottom.get(species, 0.0) + moved
                        transferred_mol["float_to_bottom"][species] = moved
                elif target == "float_layer":
                    moved = bottom.get(species, 0.0) * mix_fraction
                    if moved > 0.0:
                        bottom[species] -= moved
                        floating[species] = floating.get(species, 0.0) + moved
                        transferred_mol["bottom_to_float"][species] = moved

        debits: dict[str, dict[str, float]] = {}
        credits: dict[str, dict[str, float]] = {}
        for account in sorted(self.DECLARED_ACCOUNTS):
            species_names = set(before[account]) | set(after[account])
            for species in sorted(species_names):
                delta = after[account].get(species, 0.0) - before[account].get(species, 0.0)
                if abs(delta) <= 1e-15:
                    continue
                side = credits if delta > 0.0 else debits
                side.setdefault(account, {})[species] = abs(delta)

        control_audit = diagnostic_control_audit(request, include_fO2=False)
        diagnostic = {
            "schema": "metal_phase_stratification_v1",
            "status": "diagnostic_only_no_tap_gate",
            "mode": mode,
            "k_mix_per_hr": k_mix_per_hr,
            "dt_hr": dt_hr,
            "transfer_fraction": mix_fraction,
            "si_destination_buoyancy": si_destination_buoyancy,
            "assigned_mol": assigned_mol,
            "carried_mol": carried_mol,
            "transferred_mol": transferred_mol,
            "unclassified_staging_mol": dict(staging),
            "pool_mol_after": {
                "bottom_pool": dict(bottom),
                "float_layer": dict(floating),
            },
        }
        if not debits and not credits:
            return IntentResult(
                intent=ChemistryIntent.METAL_PHASE_STRATIFICATION,
                status="ok",
                transition=None,
                control_audit=control_audit,
                diagnostic=diagnostic,
            )
        proof = build_atom_balance_proof(
            debits,
            credits,
            request.account_view.species_formula_registry,
            resolve_species_formula,
        )
        return IntentResult(
            intent=ChemistryIntent.METAL_PHASE_STRATIFICATION,
            status="ok",
            transition=LedgerTransitionProposal(
                debits=debits,
                credits=credits,
                reason="metal_phase_stratification_diagnostic",
                atom_balance_proof=proof,
            ),
            control_audit=control_audit,
            diagnostic=diagnostic,
        )
