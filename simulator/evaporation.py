"""Evaporation and condensation-routing helpers for PyrolysisSimulator."""

from __future__ import annotations

import math
from collections import defaultdict

from simulator.accounting import AccountingError, resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from simulator.state import (
    GAS_CONSTANT,
    MOLAR_MASS,
    OXIDE_TO_METAL,
    STOICH_RATIOS,
    EvaporationFlux,
)


_DEFAULT_EVAPORATION_ALPHA = 1.0
_EVAPORATION_ALPHA_GROUPS = ("metals", "oxide_vapors")


def _load_evaporation_alpha_by_species(vapor_pressure_data: dict) -> dict[str, float]:
    """Load per-species Hertz-Knudsen alpha values from vapor pressure data."""

    alpha_by_species: dict[str, float] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if not isinstance(alpha_data, dict) or "value" not in alpha_data:
                continue
            alpha_by_species[species] = float(alpha_data["value"])
    return alpha_by_species


class EvaporationMixin:
    def _calculate_evaporation(self, equilibrium) -> EvaporationFlux:
        """
        Calculate evaporation flux using the Hertz-Knudsen-Langmuir equation.

        For each volatile species, the mass flux from the melt surface is:

            J_i = α_i × stir_factor × A_surface × (P_sat_i - P_ambient_i)
                  / √(2π × M_i × R × T)                            [HK-1]

        where:
            α_i         = evaporation coefficient (~0.1-1.0 for metals)
            stir_factor = 4-8× acceleration from induction stirring
            A_surface   = melt surface area (m²)
            P_sat_i     = saturation vapor pressure from equilibrium (Pa)
            P_ambient_i = partial pressure above the melt (Pa)
            M_i         = molar mass (kg/mol)
            R           = gas constant (J/mol·K)
            T           = temperature (K)

        The SiO suppression under pO₂ control is handled automatically:
        when pO₂ is elevated, the equilibrium vapor pressure of SiO
        drops by the factor √(pO₂), reducing the driving force.

        EVAPORATION_FLUX intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), second flip. The
        BuiltinEvaporationFluxProvider is the authoritative source for
        per-species kg/hr fluxes; this method builds the auxiliary maps
        the provider needs (vapor pressures, overhead partials, stoich,
        available oxide masses, molar masses, melt geometry) and routes
        the loop to ``kernel.dispatch(EVAPORATION_FLUX, ...)``. The
        legacy Hertz-Knudsen loop body lives inside the provider now;
        this method owns the precompute + the result projection. Shadow
        parity is the parametrised test in
        ``tests/chemistry/test_builtin_evaporation_flux_provider.py``;
        per the goal spec, the shadow comparator was removed at flip
        time (comparing against the same source is moot).

        Returns:
            EvaporationFlux with species_kg_hr dict
        """
        T_K = self.melt.temperature_C + 273.15
        flux = EvaporationFlux()

        if T_K < 400:  # Below any significant evaporation
            return flux

        vapor_pressures = dict(equilibrium.vapor_pressures_Pa or {})
        if not vapor_pressures:
            return flux

        # Precompute the auxiliary maps the provider consumes via
        # control_inputs. This keeps the provider stateless: every
        # piece of caller-owned state (yaml lookups, stoich validation,
        # available-mass cap, overhead backpressure) arrives in the
        # request, so the provider holds no simulator references.
        (
            molar_masses_kg_mol,
            stoich_by_species,
            available_oxide_kg,
        ) = self._build_evaporation_aux_maps(vapor_pressures)

        # Overhead backpressure (Pa)                       [LOOP-1]
        # Uses the previous hour's overhead partial pressures as
        # backpressure. With finite headspace enabled, gas pO2 also feeds
        # oxide-vapor reaction suppression inside the flux provider.
        overhead_partials_Pa = {
            species: self.overhead.composition.get(species, 0.0) * 100.0
            for species in vapor_pressures
        }
        intrinsic_pO2_bar = 10.0 ** self._compute_intrinsic_melt_fO2()
        gas_pO2_bar = (
            self._commanded_pO2_bar()
        )

        # F-B1: EVAPORATION_FLUX is read-only -- no commit_batch follows.
        # The dispatch-only helper centralises melt-derived T/P plumbing
        # so this call site stays in lock-step with the rest of the
        # simulator's kernel callers.
        kernel_result = self._dispatch_only(
            ChemistryIntent.EVAPORATION_FLUX,
            control_inputs={
                'vapor_pressures_Pa': vapor_pressures,
                'overhead_partials_Pa': overhead_partials_Pa,
                'gas_pO2_bar': gas_pO2_bar,
                'intrinsic_pO2_bar': intrinsic_pO2_bar,
                'molar_mass_kg_mol': molar_masses_kg_mol,
                'stoich_by_species': stoich_by_species,
                'available_oxide_kg': available_oxide_kg,
                'melt_surface_area_m2': float(self.melt.melt_surface_area_m2),
                'stir_factor': float(self.melt.stir_factor),
                'alpha': _load_evaporation_alpha_by_species(
                    self.vapor_pressures
                ),
            },
        )
        diagnostic = dict(kernel_result.diagnostic or {})
        flux_kg_hr = diagnostic.get('evaporation_flux_kg_hr') or {}
        for species, rate_kg_hr in flux_kg_hr.items():
            if rate_kg_hr > 1e-12:
                flux.species_kg_hr[species] = float(rate_kg_hr)

        flux.update_totals()
        return flux

    def _build_evaporation_aux_maps(
        self, vapor_pressures: dict,
    ) -> tuple[dict, dict, dict]:
        """Precompute per-species auxiliary inputs for EVAPORATION_FLUX.

        Returns ``(molar_masses_kg_mol, stoich_by_species,
        available_oxide_kg)``.

        These three maps are everything the kernel provider needs that
        cannot be derived from the request DTOs alone: the
        ``vapor_pressures.yaml`` payload + the simulator's stoich
        validation (which raises ``AccountingError`` -- a caller-owned
        surface that does NOT belong inside the stateless provider).

        Side effects: This method intentionally invokes
        :meth:`_evaporation_stoich` for each species, which raises
        AccountingError on missing/inconsistent metadata. Preserving
        that error surface in the caller (not the provider) matches the
        legacy behaviour exactly -- the parity tests would otherwise
        observe a different error class.
        """

        molar_masses_kg_mol: dict[str, float] = {}
        stoich_by_species: dict[str, dict] = {}
        available_oxide_kg: dict[str, float] = {}

        metals_data = self.vapor_pressures.get('metals', {}) or {}
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {}) or {}

        for species in vapor_pressures:
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            M_g_mol = sp_data.get('molar_mass_g_mol',
                                  MOLAR_MASS.get(species, 50.0))
            molar_masses_kg_mol[species] = M_g_mol / 1000.0

            parent_oxide = sp_data.get('parent_oxide', '')
            if not parent_oxide:
                raise AccountingError(
                    f"vapor species {species!r} requires parent_oxide "
                    "metadata before evaporation flux can be emitted"
                )
            stoich = self._evaporation_stoich(species, sp_data)
            stoich_by_species[species] = dict(stoich)
            available_oxide_kg[species] = self.melt.composition_kg.get(
                parent_oxide, 0.0)

        return molar_masses_kg_mol, stoich_by_species, available_oxide_kg

    def _route_to_condensation(self, evap_flux: EvaporationFlux):
        """
        Route evaporated species through the condensation train.

        Each species flows from Stage 0 (hot duct) downward through
        successive stages.  At each stage, a fraction condenses based
        on the condensation efficiency model:

            η = 1 - exp(-residence_time / τ_condensation)

        Species condense preferentially in stages where the stage
        temperature is well below the species' condensation temperature.

        The oxygen component of each evaporated metal oxide is
        released as O2 and credited to terminal oxygen storage.

        CONDENSATION_ROUTE intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), fourth flip and the
        SECOND authoritative intent in the migration. The legacy
        ``CondensationModel.route()`` still computes the per-stage
        deposition projection (η model, residence times), but the
        ledger transition that moves vapor from
        ``process.overhead_gas`` to ``process.condensation_train`` is
        now owned by the :class:`BuiltinCondensationRouteProvider` and
        committed through the kernel. The flow per species is:

        1. EVAPORATION_TRANSITION dispatched with ``remaining=rate`` --
           ALL vapor routed to ``process.overhead_gas`` (plus O2
           coproduct). No condensation_train credit from that intent.
        2. CONDENSATION_ROUTE dispatched with the per-species
           condensed_kg derived from ``route_result.remaining_by_species``
           and the available-oxide scale -- debits
           ``process.overhead_gas[species]`` and credits
           ``process.condensation_train[products]`` (with SiO
           disproportionation when sp_data declares the product map).
        3. ``_project_condensed_stage_collection`` projects the actual
           credited_condensed_kg onto stage UI bookkeeping.

        End-of-tick ledger state is identical to the pre-flip behaviour:
        between the two kernel commits the vapor passes through
        overhead_gas, but the final per-account balances match the
        legacy single-step EVAPORATION_TRANSITION exactly (verified by
        the parametrised parity test in
        ``tests/chemistry/test_builtin_condensation_route_provider.py``).
        Per the goal spec, the shadow comparator was removed at flip
        time (the parity test owns the regression surface from now on).
        """
        route_result = self.condensation_model.route(
            evap_flux, self.melt)

        metals_data = self.vapor_pressures.get('metals', {})
        oxide_vapors_data = self.vapor_pressures.get('oxide_vapors', {})

        for species, rate_kg_hr in evap_flux.species_kg_hr.items():
            sp_data = metals_data.get(species, {})
            if not sp_data:
                sp_data = oxide_vapors_data.get(species, {})

            # Pre-compute the same available_kg / scale factor the legacy
            # single-step EVAPORATION_TRANSITION applied, so the split
            # CONDENSATION_ROUTE path sees the same scaled condensed mass.
            stoich = self._evaporation_stoich(species, sp_data)
            if stoich is None:
                continue
            parent_oxide = stoich['parent_oxide']
            oxide_removed = rate_kg_hr * stoich['oxide_per_product_kg']
            available_kg = self.atom_ledger.kg_by_account(
                'process.cleaned_melt').get(parent_oxide, 0.0)
            if oxide_removed <= 1e-12 or available_kg <= 1e-12:
                continue
            scale = min(1.0, available_kg / oxide_removed)
            O2_per_product_kg = float(stoich.get('O2_per_product_kg', 0.0))
            if O2_per_product_kg < 0.0:
                required_o2_kg = rate_kg_hr * abs(O2_per_product_kg)
                available_o2_kg = self.atom_ledger.kg_by_account(
                    'process.overhead_gas').get('O2', 0.0)
                if required_o2_kg > 1e-12:
                    scale = min(
                        scale,
                        max(0.0, available_o2_kg / required_o2_kg),
                    )
            remaining_kg_hr = route_result.remaining_by_species.get(
                species, 0.0)
            if (
                remaining_kg_hr < -1e-12
                or remaining_kg_hr > rate_kg_hr + 1e-12
            ):
                raise AccountingError(
                    f"condensation route for {species!r} returned "
                    "unphysical remaining vapor mass"
                )

            # Step 1: EVAPORATION_TRANSITION (melt -> overhead_gas + O2).
            # Pass remaining=rate so the prior intent's wire-in routes
            # ALL vapor to overhead, leaving the deposit leg to
            # CONDENSATION_ROUTE.  The EVAPORATION_TRANSITION provider's
            # internal validation already rejects remaining > rate; equal
            # rates pass.
            self._credit_evaporation_transition(
                species, rate_kg_hr, rate_kg_hr, sp_data,
            )

            # Step 2: CONDENSATION_ROUTE (overhead_gas -> condensation_train).
            # condensed_kg mirrors the legacy
            # ``credited_condensed_kg = max(0.0, product_kg - remaining_kg)``
            # branch in _credit_evaporation_transition pre-flip: apply
            # the available-oxide scale, clamp negative, take the
            # vapor mass that the route said would deposit.
            condensed_kg = max(
                0.0, (rate_kg_hr - remaining_kg_hr) * scale,
            )
            credited_condensed_kg = self._dispatch_condensation_route(
                species, condensed_kg, sp_data,
            )

            # Step 3: stage UI projection (unchanged behaviour).
            product_projection = self._condensed_products_kg(
                species, credited_condensed_kg, sp_data)
            self._project_condensed_stage_collection(
                route_result, species, credited_condensed_kg,
                product_projection)

        self._sync_oxygen_kg_counters()

    def _dispatch_condensation_route(
        self,
        species: str,
        condensed_kg: float,
        sp_data: dict,
    ) -> float:
        """Dispatch CONDENSATION_ROUTE through the kernel + commit.

        F-B1 (Cluster B): the dispatch + commit interleave collapsed
        into :meth:`_dispatch_and_commit`.  The overhead ->
        condensation_train ledger write still happens ONLY through
        ``commit_batch`` (the helper's only writable path); a no-op
        dispatch (kernel returned ``transition is None``) increments
        the F-A4 ``_chem_no_op_dispatch_count`` counter so a replay
        tool can distinguish "kernel skipped" from "called and no-op".

        Returns ``credited_condensed_kg`` -- the amount of vapor
        actually deposited onto ``process.condensation_train``, used by
        the caller to drive ``_project_condensed_stage_collection``.
        """

        if condensed_kg <= 1e-12:
            return 0.0

        kernel_result = self._dispatch_and_commit(
            ChemistryIntent.CONDENSATION_ROUTE,
            control_inputs={
                'species': species,
                'condensed_kg': float(condensed_kg),
                'sp_data': dict(sp_data or {}),
                'dt_hr': 1.0,
            },
        )
        if kernel_result.transition is None:
            return 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        return float(diagnostic.get('credited_condensed_kg', 0.0))

    def _credit_evaporation_transition(
        self,
        species: str,
        rate_kg_hr: float,
        remaining_kg_hr: float,
        sp_data: dict,
    ) -> float:
        """Apply the per-species melt -> vapor transition via the kernel.

        EVAPORATION_TRANSITION intent -- kernel-authoritative.

        \\goal BUILTIN-ENGINE-EXTRACTION (#7), third flip and the FIRST
        authoritative intent in the migration. The
        BuiltinEvaporationTransitionProvider builds a
        :class:`LedgerTransitionProposal` (debit cleaned_melt, credit
        overhead_gas + condensation_train); the kernel's commit_batch
        applies it to the AtomLedger after re-validating atom balance
        and the account-filter. This method:

        1. Validates the input stoich and condensation-route output the
           same way the legacy code did (the AccountingError surface
           lives in the caller, not inside the stateless provider --
           matching the EVAPORATION_FLUX pattern).
        2. Computes the parent-oxide availability cap from the ledger
           projection (this used to live inline; the provider receives
           it via ``available_kg``).
        3. Dispatches EVAPORATION_TRANSITION through the kernel and
           commits the resulting proposal. After this flip,
           ``self.atom_ledger.apply(...)`` no longer fires from inside
           this method -- ``self._chem_kernel.commit_batch(...)`` is the
           only writable path.

        Returns:
            ``credited_condensed_kg`` -- the amount of vapor that
            condensed onto ``process.condensation_train``, used by the
            caller to drive stage-collection bookkeeping.
        """

        stoich = self._evaporation_stoich(species, sp_data)
        if stoich is None:
            return 0.0

        parent_oxide = stoich['parent_oxide']
        rate_kg_per_tick = rate_kg_hr * 1.0  # 1-hour tick (see core.py)
        oxide_removed = rate_kg_per_tick * stoich['oxide_per_product_kg']
        product_kg = rate_kg_per_tick

        if oxide_removed <= 1e-12:
            return 0.0

        available_kg = self.atom_ledger.kg_by_account(
            'process.cleaned_melt').get(parent_oxide, 0.0)
        if available_kg <= 1e-12:
            return 0.0

        # Mirror the legacy validation: the AccountingError surface is
        # owned by the caller, not the provider. The provider receives
        # already-validated inputs (this matches the EVAPORATION_FLUX
        # flip pattern in _calculate_evaporation).
        if remaining_kg_hr < -1e-12 or remaining_kg_hr > rate_kg_hr + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} returned "
                "unphysical remaining vapor mass"
            )
        scale = min(1.0, available_kg / oxide_removed)
        remaining_kg = max(0.0, remaining_kg_hr) * 1.0 * scale
        if remaining_kg > product_kg * scale + 1e-12:
            raise AccountingError(
                f"condensation route for {species!r} exceeds credited vapor"
            )

        # F-B1: dispatch + commit through the shared helper.  The
        # kernel's commit_batch path is still the ONLY writable entry
        # into the AtomLedger for EVAPORATION_TRANSITION; the helper
        # re-runs the full pre-commit validator stack inside
        # commit_batch (defence in depth).
        kernel_result = self._dispatch_and_commit(
            ChemistryIntent.EVAPORATION_TRANSITION,
            control_inputs={
                'species': species,
                'stoich': dict(stoich),
                'sp_data': dict(sp_data or {}),
                'rate_kg_hr': float(rate_kg_hr),
                'remaining_kg_hr': float(remaining_kg_hr),
                'dt_hr': 1.0,
                'available_kg': float(available_kg),
            },
        )
        if kernel_result.transition is None:
            return 0.0

        diagnostic = dict(kernel_result.diagnostic or {})
        return float(diagnostic.get('credited_condensed_kg', 0.0))

    def _condensed_products_for_vapor(
        self, species: str, condensed_kg: float, sp_data: dict
    ):
        products_mol_per_mol = self._condensation_product_mol_ratios(
            species, sp_data)
        if products_mol_per_mol is None:
            return None, {species: condensed_kg} if condensed_kg > 0.0 else {}

        vapor_formula = resolve_species_formula(
            species, self.species_formula_registry)
        vapor_mol = condensed_kg / vapor_formula.molar_mass_kg_per_mol()
        product_mol = {
            product: ratio * vapor_mol
            for product, ratio in products_mol_per_mol.items()
            if ratio * vapor_mol > 0.0
        }
        return product_mol, self._species_mol_to_kg(product_mol)

    def _condensed_products_kg(
        self, species: str, condensed_kg: float, sp_data: dict
    ) -> dict:
        _product_mol, product_kg = self._condensed_products_for_vapor(
            species, condensed_kg, sp_data)
        product_accounts = dict(
            (sp_data or {}).get('condensation_product_accounts') or {}
        )
        if product_accounts:
            product_kg = {
                product: kg
                for product, kg in product_kg.items()
                if product_accounts.get(product) != 'process.overhead_gas'
            }
        return product_kg

    def _condensation_product_mol_ratios(
        self, species: str, sp_data: dict
    ):
        ratios = sp_data.get('condensation_products_mol_per_mol_vapor')
        if ratios is None:
            declared = str(sp_data.get('condensation_product', '')).lower()
            if 'disproportion' in declared:
                raise AccountingError(
                    f"vapor species {species!r} declares condensation "
                    "disproportionation but lacks "
                    "condensation_products_mol_per_mol_vapor metadata"
                )
            return None
        if not isinstance(ratios, dict) or not ratios:
            raise AccountingError(
                f"vapor species {species!r} condensation products must be "
                "a non-empty mapping"
            )

        clean = {}
        for product, raw_ratio in ratios.items():
            ratio = float(raw_ratio)
            if ratio <= 0.0 or not math.isfinite(ratio):
                raise AccountingError(
                    f"vapor species {species!r} condensation product "
                    f"{product!r} requires a positive mol ratio"
                )
            clean[str(product)] = ratio
        self._validate_condensation_products_atoms(species, clean)
        return clean

    def _validate_condensation_products_atoms(
        self, vapor_species: str, products_mol_per_mol: dict
    ) -> None:
        debit_atoms = resolve_species_formula(
            vapor_species, self.species_formula_registry).atom_moles(1.0)
        credit_atoms = defaultdict(float)
        for product, mol in products_mol_per_mol.items():
            formula = resolve_species_formula(
                product, self.species_formula_registry)
            for element, moles in formula.atom_moles(mol).items():
                credit_atoms[element] += moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-9, abs_tol=1e-12):
                raise AccountingError(
                    f"vapor species {vapor_species!r} condensation products "
                    f"do not conserve {element} atoms"
                )

    def _species_mol_to_kg(self, species_mol: dict) -> dict:
        converted = {}
        for species, mol in species_mol.items():
            formula = resolve_species_formula(
                species, self.species_formula_registry)
            kg = float(mol) * formula.molar_mass_kg_per_mol()
            if kg > 0.0:
                converted[species] = kg
        return converted

    def _evaporation_stoich(self, species: str, sp_data: dict):
        parent_oxide = sp_data.get('parent_oxide', '')
        if not parent_oxide:
            raise AccountingError(
                f"vapor species {species!r} requires parent_oxide "
                "metadata before ledger routing"
            )

        has_oxide = sp_data.get('stoich_oxide_per_vapor') is not None
        has_o2 = sp_data.get('stoich_O2_per_vapor') is not None
        if has_oxide or has_o2:
            missing = []
            if not has_oxide:
                missing.append('stoich_oxide_per_vapor')
            if not has_o2:
                missing.append('stoich_O2_per_vapor')
            if missing:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    f"missing explicit stoich metadata: {', '.join(missing)}"
                )
            oxide_per_product = float(sp_data['stoich_oxide_per_vapor'])
            O2_per_product = float(sp_data['stoich_O2_per_vapor'])
            if oxide_per_product <= 0.0:
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "requires positive stoich_oxide_per_vapor"
                )
            if not math.isclose(
                oxide_per_product,
                1.0 + O2_per_product,
                rel_tol=1e-6,
                abs_tol=1e-9,
            ):
                raise AccountingError(
                    f"vapor species {species!r} from {parent_oxide!r} "
                    "stoich metadata must conserve mass: "
                    "stoich_oxide_per_vapor must equal "
                    "1 + stoich_O2_per_vapor"
                )
            self._validate_evaporation_stoich_atoms(
                parent_oxide,
                species,
                oxide_per_product,
                O2_per_product,
            )
            return {
                'parent_oxide': parent_oxide,
                'oxide_per_product_kg': oxide_per_product,
                'O2_per_product_kg': O2_per_product,
            }

        implied = OXIDE_TO_METAL.get(parent_oxide, ('', 0, 0))[0]
        if species != implied:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} requires "
                "explicit stoich_oxide_per_vapor and stoich_O2_per_vapor; "
                f"STOICH_RATIOS fallback only applies to elemental "
                f"{implied!r}"
            )
        fallback = STOICH_RATIOS.get(parent_oxide)
        if not fallback or fallback[0] <= 0:
            raise AccountingError(
                f"vapor species {species!r} from {parent_oxide!r} has no "
                "valid elemental stoich fallback"
            )
        kg_product_per_kg_oxide, kg_O2_per_kg_oxide = fallback
        oxide_per_product = 1.0 / kg_product_per_kg_oxide
        O2_per_product = kg_O2_per_kg_oxide / kg_product_per_kg_oxide
        if not math.isclose(
            oxide_per_product,
            1.0 + O2_per_product,
            rel_tol=1e-6,
            abs_tol=1e-9,
        ):
            raise AccountingError(
                f"STOICH_RATIOS[{parent_oxide!r}] does not conserve mass"
            )
        self._validate_evaporation_stoich_atoms(
            parent_oxide,
            species,
            oxide_per_product,
            O2_per_product,
        )
        return {
            'parent_oxide': parent_oxide,
            'oxide_per_product_kg': oxide_per_product,
            'O2_per_product_kg': O2_per_product,
        }

    def _validate_evaporation_stoich_atoms(
        self,
        parent_oxide: str,
        product_species: str,
        oxide_per_product_kg: float,
        O2_per_product_kg: float,
    ) -> None:
        debit_atoms = self._atom_moles_for_kg(
            parent_oxide, oxide_per_product_kg)
        credit_atoms = defaultdict(float)
        product_atoms = self._atom_moles_for_kg(product_species, 1.0)
        for element, moles in product_atoms.items():
            credit_atoms[element] += moles
        if O2_per_product_kg >= 0.0:
            oxygen_atoms = self._atom_moles_for_kg('O2', O2_per_product_kg)
            for element, moles in oxygen_atoms.items():
                credit_atoms[element] += moles
        else:
            oxygen_atoms = self._atom_moles_for_kg('O2', abs(O2_per_product_kg))
            for element, moles in oxygen_atoms.items():
                debit_atoms[element] = debit_atoms.get(element, 0.0) + moles

        for element in set(debit_atoms) | set(credit_atoms):
            debit = debit_atoms.get(element, 0.0)
            credit = credit_atoms.get(element, 0.0)
            if not math.isclose(debit, credit, rel_tol=1e-6, abs_tol=1e-9):
                raise AccountingError(
                    f"vapor species {product_species!r} from "
                    f"{parent_oxide!r} stoich metadata does not conserve "
                    f"{element} atoms"
                )

    def _atom_moles_for_kg(self, species: str, kg: float) -> dict:
        if kg <= 0.0:
            return {}
        formula = resolve_species_formula(
            species, self.species_formula_registry)
        species_moles = float(kg) / formula.molar_mass_kg_per_mol()
        return formula.atom_moles(species_moles)

    def _project_condensed_stage_collection(
        self, route_result, species: str, credited_condensed_kg: float,
        product_kg_by_species: dict | None = None,
    ) -> None:
        if credited_condensed_kg <= 1e-12:
            return
        product_kg_by_species = product_kg_by_species or {
            species: credited_condensed_kg}
        intended_condensed_kg = route_result.condensed_for_species(species)
        if intended_condensed_kg <= 1e-12:
            return
        scale = credited_condensed_kg / intended_condensed_kg
        product_scale = {
            product: kg / credited_condensed_kg
            for product, kg in product_kg_by_species.items()
            if kg > 1e-12
        }
        stages_by_number = {
            stage.stage_number: stage for stage in self.train.stages
        }
        for stage_number, stage_species in (
            route_result.condensed_by_stage_species.items()
        ):
            projected_kg = stage_species.get(species, 0.0) * scale
            if projected_kg <= 1e-12:
                continue
            stage = stages_by_number.get(stage_number)
            if stage is None:
                continue
            for product, product_fraction in product_scale.items():
                stage_product_kg = projected_kg * product_fraction
                if stage_product_kg <= 1e-12:
                    continue
                stage.collected_kg.update({
                    product: (
                        stage.collected_kg.get(product, 0.0)
                        + stage_product_kg)
                })

    def _update_melt_composition(self, evap_flux: EvaporationFlux):
        """Project the cleaned-melt account onto MeltState kg fields."""
        self._project_cleaned_melt_from_atom_ledger()
