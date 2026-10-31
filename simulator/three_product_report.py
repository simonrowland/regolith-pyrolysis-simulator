"""E6a (north-star product-class classifier, 0.5.4.1, 2026-05-28).

Maps the simulator's per-species product output onto the four
north-star product classes documented in
``CLAUDE.md § 5 — Product classes``:

  1. METALS + O₂                — Na/K/Fe/Mg/Si/Ti/Al/Ca/Cr/Mn/Ni/Co
                                  metal alloys + accumulated O₂.
  2. PURE SILICA GLASS          — SiO landed on Stage 3 fused-silica
                                  baffles on-demand via gas-cover switch.
  3. INDUSTRIAL MIXED GLASS     — Si-bearing residual melt if the
                                  recipe taps before the SiO release
                                  window (early-tap option).
  4. REFRACTORY CERAMIC RUMP    — Ca / REE / refractory oxides + any
                                  Al that didn't get thermited. By
                                  physics, not by recipe choice.

This module is DIAGNOSTIC ONLY: it reads the AtomLedger via the
canonical surfaces (``product_ledger`` + ``train.stages`` +
``_terminal_rump_by_species``) and projects onto a single dict; it
does NOT mutate any account.

Public contract: the original five canonical buckets
(``metals_plus_O2``, ``pure_silica_glass``,
``industrial_mixed_glass``, ``refractory_ceramic_rump``,
``unclassified``) remain stable. PROD-1 added additive convenience
views (``ingots_metals``, ``oxygen``, ``glass``,
``captured_volatiles``, ``process_inventory_spent_reductant``) for
product-specific UI/report slices without removing or reshaping the
canonical buckets.

E6a scope: the classifier function. E6b (deferred) is the runner CLI
+ markdown report wrapping E6a.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from simulator.account_ids import (
    C7_AL_CREDIT_ACCOUNT,
    CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    CONDENSATION_RETAINED_HOLDUP_ACCOUNT,
    EXTRACTED_METAL_PRODUCT_ACCOUNTS,
    METAL_PHASE_ACCOUNTS,
    OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
    OXYGEN_CAPTURED_ACCOUNTS,
    OXYGEN_STORED_ACCOUNTS,
    OXYGEN_VENTED_ACCOUNTS,
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
    TERMINAL_DRAIN_TAP_ACCOUNT,
)
from simulator.accounting.completeness import (
    METAL_PRODUCT_SPECIES,
    O2_PRODUCT_SPECIES,
    PURE_SILICA_GLASS_SPECIES,
    product_class_availability,
    product_class_target_species,
)
from simulator.accounting.exceptions import AccountingError
from simulator.accounting.queries import (
    AccountingQueries,
    CONDENSATION_TRAIN_ACCOUNT,
    PRODUCT_LEDGER_ACCOUNTS,
    TERMINAL_ESCAPE_ACCOUNT,
    TERMINAL_RUMP_CLASS_TOLERANCE_PCT,
    TERMINAL_RUMP_REFRACTORY_OXIDES,
    TERMINAL_RUMP_SILICATE_RESIDUAL,
    TERMINAL_RUMP_UNEXTRACTED_METALS,
)
from simulator.condensation_routing import (
    PRODUCT_DESTINATIONS,
    STAGE_KEY_BY_IDENTITY,
    accepted_species_for_stage_key,
)
from simulator.vapour_rail.instrumentation import (
    VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE,
    VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING,
    vapour_carrier_authority_status,
)

CAPTURED_VOLATILE_ACCOUNTS: tuple[str, ...] = (
    'process.condensation_train',
)
"""Captured volatile accounts; terminal escape is an offgas_vented loss."""

PROCESS_INVENTORY_SPENT_REDUCTANT_ACCOUNTS: tuple[str, ...] = (
    SPENT_REDUCTANT_RESIDUE_ACCOUNT,
)
"""Melt-resident reductant residue from reagent we dosed, not native rump."""

TERMINAL_RUMP_REPORT_CLASSES: tuple[str, ...] = (
    'refractory_oxides',
    'silicate_residual',
    'unextracted_metals',
    'other',
)
"""Operator-facing terminal-rump split from AccountingQueries.

These distinguish the by-physics refractory floor from the failure-mode
target residue left unextracted, while keeping unclassified rump mass visible.
"""

REFRACTORY_CERAMIC_RUMP_ELEMENTS: tuple[str, ...] = (
    'Ca', 'REE',
)
"""Product class 4: by-physics rump. Ca + REEs + refractory oxides
that don't volatilize at any furnace-survivable T. ``Al`` is
NOT here even though Al2O3 can land in the rump — Al-as-rump
depends on whether C6 thermite fired. Use the ``has_al_thermite``
flag on the classifier output to disambiguate. The full element
ladder is queried via ``ExtractionMixin._RUMP_ELEMENT_SPECIES``
which carries Si / Al / Mg / Ti routing too."""


def classify_products(sim, *, early_tap_mode: bool = False) -> dict[str, Any]:
    """Project a PyrolysisSimulator's product surface onto the four
    north-star product classes.

    Args:
        sim: A ``PyrolysisSimulator`` (after ``run_to_completion`` or
            at any mid-campaign tick). Reads:
            - ``sim.product_ledger()`` — AtomLedger-projected
              per-species kg
            - ``sim.train.stages`` — UI projection per condensation
              stage
            - ``sim._terminal_rump_by_species()`` if available, for
              the rump composition
        early_tap_mode: When ``True``, the classifier reports the
            current ``process.cleaned_melt`` residual as the
            ``industrial_mixed_glass`` product class — the operator
            has decided to tap the melt before the C5/C6 sequence
            (the early-tap product class 3 per CLAUDE.md § 5).
            When ``False`` (the default), the mixed-glass bucket is
            zeroed; mid-run readings of ``cleaned_melt`` are NOT
            product output (the melt is sitting in the crucible
            waiting for the next campaign).

            Per evening-4commits review P2 #2 (2026-05-28): the
            initial E6a implementation treated any non-zero
            ``cleaned_melt`` as mixed-glass output, reproducing
            "1000 kg" at C2A/C5 hour 4 — wrong semantic. Adding
            this explicit operator-intent gate prevents the false
            attribution.

    Returns a dict with the following structure:
        {
            'metals_plus_O2': {
                'metals_kg': {species: kg, ...},
                'metals_total_kg': float,
                'O2_kg': float,
                'O2_partition_kg': {bin: kg, ...},
                'class_total_kg': float,
            },
            'ingots_metals': {
                'species_kg': {species: kg, ...},
                'class_total_kg': float,
            },
            'oxygen': {
                'O2_kg': float,
                'partition_kg': {bin: kg, ...},
                'class_total_kg': float,
            },
            'pure_silica_glass': {
                'stage_3_capture_kg': float | None,
                'stage_3_kg_by_species': {species: kg, ...},
                'class_total_kg': float | None,
                'flag': {status, authority, band, reason},  # when flagged
            },
            'glass': {
                'species_kg': {species: kg, ...},
                'class_total_kg': float | None,
                'pure_silica_glass_kg': float | None,
                'industrial_mixed_glass_kg': float,
            },
            'industrial_mixed_glass': {
                'mixed_melt_residual_kg': float,
                'early_tap_mode': bool,
                'note': 'present only if recipe tapped early',
                'class_total_kg': float,
            },
            'captured_volatiles': {
                'kg_by_species': {species: kg, ...},
                'class_total_kg': float,
            },
            'process_inventory_spent_reductant': {
                'kg_by_species': {species: kg, ...},
                'class_total_kg': float,
                'account': 'process.spent_reductant_residue',
                'disposition': 'process_inventory_spent_reductant',
            },
            'refractory_ceramic_rump': {
                'rump_kg_by_species': {species: kg, ...},
                'rump_total_kg': float,
                'rump_refractory_oxides_kg': float,
                'rump_silicate_residual_kg': float,
                'rump_unextracted_metals_kg': float,
                'rump_other_kg': float,
                'ree_enrichment_extent': dict,
                'class_total_kg': float,
            },
            'unclassified': {
                'kg_by_species': {species: kg, ...},
                'total_kg': float,
            },
        }

    The 'unclassified' bin catches anything the classifier didn't
    map to one of the four classes — operator visibility that the
    project's species → product-class mapping is incomplete (e.g.,
    if a future feedstock introduces a new condensable like S2 or
    H2O carry-over).
    """
    products = sim.product_ledger() if hasattr(sim, 'product_ledger') else {}
    train_stages = list(
        getattr(getattr(sim, 'train', None), 'stages', []) or []
    )
    # ----- Class 1: metals + O2 -----
    metal_accounting = _metal_product_accounting(sim, products=products)
    cleanup_native_kg = metal_accounting['cleanup_native_kg']
    metals_kg = metal_accounting['species_kg']
    metal_sources_by_species = metal_accounting['source_accounts_by_species']
    unrecovered_metal_phase = metal_accounting['unrecovered_metal_phase_kg']
    unrecovered_metal_sources = metal_accounting[
        'unrecovered_metal_phase_sources_by_species'
    ]
    metals_total_kg = float(sum(metals_kg.values()))
    oxygen_partition = _oxygen_partition_kg(sim)
    o2_kg = float(oxygen_partition.get('total', products.get('O2', 0.0)))
    metals_plus_o2_total = float(metals_total_kg + o2_kg)

    # ----- Class 2: pure silica glass (Stage 3 capture) -----
    stage_3_kg_by_species: dict[str, float] = {}
    stage_3_unavailable_reason: str | None = 'stage_3_missing'
    if len(train_stages) > 3 and train_stages[3] is not None:
        stage_3 = train_stages[3]
        collected = getattr(stage_3, 'collected_kg', None)
        if isinstance(collected, Mapping):
            stage_3_unavailable_reason = None
        elif collected is None:
            stage_3_unavailable_reason = 'stage_3_collection_missing'
        else:
            stage_3_unavailable_reason = 'stage_3_collection_malformed'
    else:
        collected = None
    if stage_3_unavailable_reason is None:
        for species in PURE_SILICA_GLASS_SPECIES:
            kg = float(collected.get(species, 0.0))
            if kg > 0.0:
                stage_3_kg_by_species[species] = kg
    stage_3_capture_kg = (
        float(sum(stage_3_kg_by_species.values()))
        if stage_3_unavailable_reason is None
        else None
    )
    saw_oxygen_hold = False
    saw_release_capture = False
    every_capture_followed_release_switch = True
    silica_certification_flag: dict[str, Any] | None = None
    for snapshot in getattr(getattr(sim, 'record', None), 'snapshots', ()) or ():
        gas = getattr(snapshot, 'c2a_staged_gas', {}) or {}
        if gas.get('gas_cover_mode') == 'po2_hold':
            saw_oxygen_hold = True
        capture = getattr(snapshot, 'condensed_by_stage_species_delta', {}) or {}
        if not any(capture.get((3, species), 0.0) > 0.0
                   for species in PURE_SILICA_GLASS_SPECIES):
            continue
        release_capture = (
            saw_oxygen_hold
            and gas.get('stage_name') == 'sio_window'
            and gas.get('gas_cover_mode') == 'pn2_sweep'
        )
        saw_release_capture = saw_release_capture or release_capture
        every_capture_followed_release_switch = (
            every_capture_followed_release_switch and release_capture
        )
        if not release_capture:
            continue
        authority = getattr(
            getattr(snapshot, 'evap_flux', None), 'carrier_authority_by_species', {}
        ) or {}
        authority_record = authority.get('SiO')
        authority_status = vapour_carrier_authority_status(
            authority_record, expected_species_id='SiO'
        )
        if authority_status == VAPOUR_CARRIER_AUTHORITY_AUTHORITATIVE:
            continue

        record = authority_record if isinstance(authority_record, Mapping) else {}
        extra = record.get('extra')
        extra = extra if isinstance(extra, Mapping) else {}
        band = record.get('certified_band')
        if band is None:
            band = record.get('valid_range_K')
        if band is None:
            band = record.get('certification_ceiling')
        reason = (
            record.get('reason')
            or record.get('original_reason')
            or record.get('refusal_code')
            or extra.get('reason')
            or extra.get('activity_reason')
            or 'missing SiO carrier evidence'
        )
        flag_status = (
            'flagged prediction'
            if authority_status == VAPOUR_CARRIER_AUTHORITY_STATUS_BEARING
            else 'unavailable'
        )
        if flag_status == 'unavailable' or silica_certification_flag is None:
            silica_certification_flag = {
                'status': flag_status,
                'authority': record.get('authority_level') or authority_status,
                'band': band,
                'reason': str(reason),
            }

    # Product classification is derived from the executed release route;
    # evidence authority certifies that mass but never erases a prediction.
    silica_is_product = (
        saw_release_capture and every_capture_followed_release_switch
    )
    if stage_3_unavailable_reason is not None:
        silica_product_kg: float | None = None
        reported_stage_3_capture_kg: float | None = None
        silica_certification_flag = {
            'status': 'unavailable',
            'authority': 'unavailable',
            'band': None,
            'reason': stage_3_unavailable_reason,
        }
    elif not silica_is_product:
        silica_product_kg: float | None = 0.0
        reported_stage_3_capture_kg: float | None = stage_3_capture_kg
    else:
        silica_product_kg = stage_3_capture_kg
        reported_stage_3_capture_kg = stage_3_capture_kg
    reported_stage_3_species = (
        stage_3_kg_by_species if reported_stage_3_capture_kg is not None else {}
    )

    # ----- Captured volatiles -----
    # The condenser ledger can contain metal and silica species as well as
    # ordinary volatiles. Their accepted product classes are handled above;
    # any unaccepted condenser mass remains off-spec in the product story.
    captured_volatiles_kg_by_species = _ledger_species_kg(
        sim,
        CAPTURED_VOLATILE_ACCOUNTS,
        exclude_species=(
            set(O2_PRODUCT_SPECIES)
            | set(METAL_PRODUCT_SPECIES)
            | set(PURE_SILICA_GLASS_SPECIES)
        ),
    )
    captured_volatiles_total_kg = float(
        sum(captured_volatiles_kg_by_species.values())
    )

    # ----- Process inventory: spent reductant residue -----
    spent_reductant_kg_by_species = _spent_reductant_residue_kg(sim)
    spent_reductant_total_kg = float(sum(spent_reductant_kg_by_species.values()))

    # ----- Class 4: refractory ceramic rump -----
    rump_kg_by_species: dict[str, float] = {}
    rump_method = getattr(sim, '_terminal_rump_by_species', None)
    rump_inventory_computed = False
    if callable(rump_method):
        try:
            rump_kg_by_species = {
                str(species): float(kg)
                for species, kg in (rump_method() or {}).items()
                if float(kg) > 0.0
            }
            rump_inventory_computed = bool(rump_kg_by_species)
        except (TypeError, ValueError):
            rump_kg_by_species = {}
    rump_total_kg = float(sum(rump_kg_by_species.values()))
    rump_by_class_kg = _terminal_rump_by_class_kg(
        sim,
        rump_kg_by_species=rump_kg_by_species,
    )
    ree_enrichment_extent = _terminal_ree_enrichment_extent(sim)

    # ----- Class 3: industrial mixed glass (early-tap option) -----
    # Detected by presence of bulk Si-bearing melt mass left in the
    # cleaned_melt account WHEN THE OPERATOR HAS DECLARED EARLY-TAP
    # INTENT (the ``early_tap_mode`` arg). Pre-evening-review the
    # bucket counted any cleaned_melt at any tick — reproduced 1000
    # kg at C2A/C5 hour 4, which is the entire melt sitting in the
    # crucible waiting for C5/C6, NOT a "mixed glass product".
    mixed_melt_residual_kg = 0.0
    if early_tap_mode and hasattr(sim, 'atom_ledger'):
        try:
            cleaned_melt = sim.atom_ledger.project_account_kg(
                'process.cleaned_melt'
            )
            mixed_melt_residual_kg = float(
                sum(kg for kg in (cleaned_melt or {}).values()
                    if float(kg) > 0.0)
            )
        except (AttributeError, TypeError, ValueError):
            mixed_melt_residual_kg = 0.0
    glass_class_total_kg = (
        None
        if silica_product_kg is None
        else silica_product_kg + mixed_melt_residual_kg
    )
    has_metal_product = bool(metals_kg)
    has_oxygen_product = (
        any(amount > 0.0 for amount in oxygen_partition.values())
        or 'O2' in products
    )
    has_silica_capture = bool(stage_3_kg_by_species)
    has_silica_product = silica_is_product and has_silica_capture
    has_mixed_glass_inventory = early_tap_mode and bool(mixed_melt_residual_kg)
    computed_product_parts = {
        'metals_plus_O2': has_metal_product or has_oxygen_product,
        'ingots_metals': has_metal_product,
        'oxygen': has_oxygen_product,
        'pure_silica_glass': has_silica_capture,
        'glass': has_silica_capture or has_mixed_glass_inventory,
        'refractory_ceramic_rump': rump_inventory_computed,
        'captured_volatiles': bool(captured_volatiles_kg_by_species),
    }
    if early_tap_mode:
        computed_product_parts['industrial_mixed_glass'] = (
            has_mixed_glass_inventory
        )
    computed_mass_by_class = {
        'metals_plus_O2': {
            **({'metals_total_kg': metals_total_kg} if has_metal_product else {}),
            **({'O2_kg': o2_kg} if has_oxygen_product else {}),
        },
        'ingots_metals': dict(metals_kg) if has_metal_product else {},
        'oxygen': {'O2_kg': o2_kg} if has_oxygen_product else {},
        'pure_silica_glass': (
            {'qualified_capture_kg': silica_product_kg}
            if has_silica_capture else {}
        ),
        'glass': {
            **(
                {'pure_silica_glass_kg': silica_product_kg}
                if has_silica_capture else {}
            ),
            **(
                {'industrial_mixed_glass_kg': mixed_melt_residual_kg}
                if has_mixed_glass_inventory else {}
            ),
        },
        'refractory_ceramic_rump': (
            {'refractory_oxides_kg': rump_by_class_kg['refractory_oxides']}
            if rump_inventory_computed else {}
        ),
        'captured_volatiles': (
            dict(captured_volatiles_kg_by_species)
            if captured_volatiles_kg_by_species else {}
        ),
    }
    if early_tap_mode:
        computed_mass_by_class['industrial_mixed_glass'] = (
            {'mixed_melt_residual_kg': mixed_melt_residual_kg}
            if has_mixed_glass_inventory else {}
        )

    # ----- Unclassified bin -----
    classified_species: set[str] = (
        set(metals_kg.keys())
        | set(cleanup_native_kg.keys())
        | {'O2'}
        | set(stage_3_kg_by_species.keys())
        | set(captured_volatiles_kg_by_species.keys())
        | set(spent_reductant_kg_by_species.keys())
        | set(rump_kg_by_species.keys())
    )
    unclassified: dict[str, float] = dict(cleanup_native_kg)
    for species, kg in products.items():
        if species in classified_species:
            continue
        try:
            value = float(kg)
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            unclassified[species] = value
    unclassified_total = float(sum(unclassified.values()))

    silica_bucket: dict[str, Any] = {
        'stage_3_capture_kg': reported_stage_3_capture_kg,
        'stage_3_kg_by_species': reported_stage_3_species,
        'class_total_kg': silica_product_kg,
    }
    if silica_certification_flag is not None and (
        silica_is_product or stage_3_unavailable_reason is not None
    ):
        silica_bucket['flag'] = silica_certification_flag

    result = {
        'metals_plus_O2': {
            'metals_kg': metals_kg,
            'source_accounts_by_species': metal_sources_by_species,
            'metals_total_kg': metals_total_kg,
            'O2_kg': o2_kg,
            'O2_partition_kg': oxygen_partition,
            'class_total_kg': metals_plus_o2_total,
        },
        'ingots_metals': {
            'species_kg': metals_kg,
            'source_accounts_by_species': metal_sources_by_species,
            'class_total_kg': metals_total_kg,
        },
        'unrecovered_metal_phase': {
            'species_kg': unrecovered_metal_phase,
            'source_accounts_by_species': unrecovered_metal_sources,
            'class_total_kg': float(sum(unrecovered_metal_phase.values())),
        },
        'oxygen': {
            'O2_kg': o2_kg,
            'partition_kg': oxygen_partition,
            'class_total_kg': o2_kg,
        },
        'pure_silica_glass': silica_bucket,
        'glass': {
            'species_kg': reported_stage_3_species if silica_is_product else {},
            'class_total_kg': glass_class_total_kg,
            'pure_silica_glass_kg': silica_product_kg,
            'industrial_mixed_glass_kg': mixed_melt_residual_kg,
        },
        'industrial_mixed_glass': {
            'mixed_melt_residual_kg': mixed_melt_residual_kg,
            'early_tap_mode': bool(early_tap_mode),
            'note': (
                'present only when operator declared early-tap '
                'via early_tap_mode=True; zero by default — '
                'mid-run cleaned_melt is NOT a product class'
            ),
            'class_total_kg': mixed_melt_residual_kg,
        },
        'captured_volatiles': {
            'kg_by_species': captured_volatiles_kg_by_species,
            'class_total_kg': captured_volatiles_total_kg,
        },
        'process_inventory_spent_reductant': {
            'kg_by_species': spent_reductant_kg_by_species,
            'class_total_kg': spent_reductant_total_kg,
            'account': SPENT_REDUCTANT_RESIDUE_ACCOUNT,
            'disposition': 'process_inventory_spent_reductant',
        },
        'refractory_ceramic_rump': {
            'rump_kg_by_species': rump_kg_by_species,
            'rump_total_kg': rump_total_kg,
            'rump_refractory_oxides_kg': rump_by_class_kg['refractory_oxides'],
            'rump_silicate_residual_kg': rump_by_class_kg['silicate_residual'],
            'rump_unextracted_metals_kg': (
                rump_by_class_kg['unextracted_metals']
            ),
            'rump_other_kg': rump_by_class_kg['other'],
            'ree_enrichment_extent': ree_enrichment_extent,
            'class_total_kg': rump_by_class_kg['refractory_oxides'],
        },
        'unclassified': {
            'kg_by_species': unclassified,
            'total_kg': unclassified_total,
        },
    }
    affected_buckets = [
        ('metals_plus_O2', product_class_target_species('metals_plus_O2'),
         ('class_total_kg', 'metals_total_kg', 'O2_kg'), ('metals_kg',)),
        ('ingots_metals', product_class_target_species('ingots_metals'),
         ('class_total_kg',), ('species_kg',)),
        ('oxygen', product_class_target_species('oxygen'),
         ('class_total_kg', 'O2_kg'), ()),
        ('pure_silica_glass',
         product_class_target_species('pure_silica_glass'),
         ('class_total_kg', 'stage_3_capture_kg'), ('stage_3_kg_by_species',)),
        ('glass', product_class_target_species(
            'glass', early_tap_mode=early_tap_mode
        ),
         ('class_total_kg', 'pure_silica_glass_kg',
          'industrial_mixed_glass_kg'), ('species_kg',)),
        ('refractory_ceramic_rump',
         product_class_target_species('refractory_ceramic_rump'),
         ('class_total_kg', 'rump_total_kg', 'rump_silicate_residual_kg',
          'rump_unextracted_metals_kg'), ('rump_kg_by_species',)),
        ('captured_volatiles',
         product_class_target_species('captured_volatiles'),
         ('class_total_kg',), ('kg_by_species',)),
    ]
    if early_tap_mode:
        affected_buckets.append((
            'industrial_mixed_glass',
            product_class_target_species('industrial_mixed_glass'),
            ('class_total_kg',),
            (),
        ))
    for name, targets, quantities, species_maps in affected_buckets:
        bucket = result[name]
        availability = product_class_availability(
            sim,
            targets,
            computed_mass_by_class[name],
            has_computed_part=computed_product_parts[name],
        )
        bucket['estimated_contributions'] = availability[
            'estimated_contributions'
        ]
        if availability['status'] != 'available':
            if availability['status'] == 'partial':
                bucket['class_total_kg'] = availability['class_total_kg']
            bucket['status'] = availability['status']
            bucket['excluded'] = availability['excluded']
            if isinstance(bucket.get('flag'), Mapping):
                bucket['availability_flag'] = availability['flag']
            else:
                bucket['flag'] = availability['flag']
            if availability['status'] == 'unavailable':
                for key in quantities:
                    bucket[key] = None
                for key in species_maps:
                    bucket[key] = {}
    if result['oxygen']['class_total_kg'] is None:
        for name, key in (
            ('oxygen', 'partition_kg'),
            ('metals_plus_O2', 'O2_partition_kg'),
        ):
            result[name][key] = dict.fromkeys(oxygen_partition)
    return result


def _metal_product_accounting(sim, *, products):
    """Separate recovered metals from the ledger accounts that still hold them."""
    record = getattr(sim, 'record', None)
    initial_inventory = getattr(record, 'initial_inventory', None)
    native_metals = getattr(initial_inventory, 'metal_alloy_kg', {}) or {}
    ledger = getattr(sim, 'atom_ledger', None)
    project_account_kg = getattr(ledger, 'project_account_kg', None)

    # Lightweight classifier callers without an AtomLedger retain the former
    # aggregate projection. Production simulations always use account-backed
    # allocation below, where the product source remains explicit.
    if not callable(project_account_kg):
        cleanup_native_kg = {}
        species_kg = {}
        for species in METAL_PRODUCT_SPECIES:
            amount = max(0.0, float(products.get(species, 0.0)))
            native = min(amount, max(0.0, float(native_metals.get(species, 0.0))))
            if native > 0.0:
                cleanup_native_kg[species] = native
            if amount > native:
                species_kg[species] = amount - native
        return {
            'species_kg': species_kg,
            'source_accounts_by_species': {},
            'cleanup_native_kg': cleanup_native_kg,
            'unrecovered_metal_phase_kg': {},
            'unrecovered_metal_phase_sources_by_species': {},
        }

    source_accounts = (
        *EXTRACTED_METAL_PRODUCT_ACCOUNTS,
        *METAL_PHASE_ACCOUNTS,
        CONDENSATION_TRAIN_ACCOUNT,
        CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    )
    account_species_kg: dict[str, dict[str, float]] = {}
    for account in source_accounts:
        try:
            raw = project_account_kg(account) or {}
        except (AccountingError, AttributeError, TypeError, ValueError):
            raw = {}
        if not isinstance(raw, Mapping):
            continue
        values = {}
        for species, value in raw.items():
            try:
                amount = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(amount) and amount > 0.0:
                values[str(species)] = amount
        account_species_kg[account] = values

    stage_by_source: dict[str, dict[str, float]] = {}
    stage_collections = getattr(sim, '_stage_collection_kg_by_source', {}) or {}
    stages = list(getattr(getattr(sim, 'train', None), 'stages', ()) or ())
    allowed_stage_sources = set(METAL_PHASE_ACCOUNTS) | {
        CONDENSATION_TRAIN_ACCOUNT,
        CHROMIUM_CONDENSED_OXIDE_ACCOUNT,
    }
    for key, raw_mass in stage_collections.items():
        if not isinstance(key, tuple) or len(key) != 3:
            continue
        source_account, raw_stage_index, species = key
        source_account = str(source_account)
        species = str(species)
        if source_account not in allowed_stage_sources or species not in METAL_PRODUCT_SPECIES:
            continue
        try:
            stage = stages[int(raw_stage_index)]
            amount = float(raw_mass)
        except (IndexError, TypeError, ValueError):
            continue
        stage_key = str(getattr(stage, 'stage_key', '') or '')
        if not math.isfinite(amount) or amount <= 0.0 or not stage_key:
            continue
        if not _product_stage_accepts_species(species, stage_key):
            continue
        backing = account_species_kg.get(source_account, {}).get(species, 0.0)
        allocated = stage_by_source.setdefault(source_account, {})
        allocated_key = species
        available = max(0.0, backing - allocated.get(allocated_key, 0.0))
        accepted_kg = min(amount, available)
        if accepted_kg <= 0.0:
            continue
        allocated[allocated_key] = allocated.get(allocated_key, 0.0) + accepted_kg

    product_sources: dict[str, dict[str, float]] = {}
    cleanup_native_kg: dict[str, float] = {}
    tapped = account_species_kg.get(TERMINAL_DRAIN_TAP_ACCOUNT, {})
    for species in METAL_PRODUCT_SPECIES:
        tapped_kg = tapped.get(species, 0.0)
        native_kg = min(
            tapped_kg,
            max(0.0, float(native_metals.get(species, 0.0))),
        )
        if native_kg > 0.0:
            cleanup_native_kg[species] = native_kg
        recovered_kg = tapped_kg - native_kg
        if recovered_kg > 0.0:
            product_sources.setdefault(species, {})[
                TERMINAL_DRAIN_TAP_ACCOUNT
            ] = recovered_kg

    for account, species_values in stage_by_source.items():
        for species, amount in species_values.items():
            product_sources.setdefault(species, {})[account] = (
                product_sources.setdefault(species, {}).get(account, 0.0)
                + amount
            )

    unrecovered_sources: dict[str, dict[str, float]] = {}
    for account in METAL_PHASE_ACCOUNTS:
        balance = account_species_kg.get(account, {})
        accepted = stage_by_source.get(account, {})
        for species in METAL_PRODUCT_SPECIES:
            amount = max(0.0, balance.get(species, 0.0) - accepted.get(species, 0.0))
            if amount > 0.0:
                unrecovered_sources.setdefault(species, {})[account] = amount

    def totals_and_sources(source_map):
        totals = {
            species: float(sum(accounts.values()))
            for species, accounts in sorted(source_map.items())
            if sum(accounts.values()) > 0.0
        }
        by_species = {
            species: [
                {'account': account, 'kg': float(amount)}
                for account, amount in sorted(accounts.items())
                if amount > 0.0
            ]
            for species, accounts in sorted(source_map.items())
            if any(amount > 0.0 for amount in accounts.values())
        }
        return totals, by_species

    species_kg, source_accounts_by_species = totals_and_sources(product_sources)
    unrecovered_kg, unrecovered_source_accounts_by_species = totals_and_sources(
        unrecovered_sources
    )
    return {
        'species_kg': species_kg,
        'source_accounts_by_species': source_accounts_by_species,
        'cleanup_native_kg': cleanup_native_kg,
        'unrecovered_metal_phase_kg': unrecovered_kg,
        'unrecovered_metal_phase_sources_by_species': (
            unrecovered_source_accounts_by_species
        ),
    }


def _product_stage_accepts_species(species: str, stage_key: str) -> bool:
    if species in accepted_species_for_stage_key(stage_key):
        return True
    return any(
        destinations.get(species) == stage_key
        for destinations in PRODUCT_DESTINATIONS.values()
    )


def _terminal_ree_enrichment_extent(sim: Any) -> dict[str, Any]:
    """Expose the accounting facade's feed-to-terminal REE derivation."""
    try:
        return dict(AccountingQueries(sim).terminal_ree_enrichment_extent())
    except (AccountingError, AttributeError, TypeError, ValueError):
        return {}


def _terminal_rump_by_class_kg(
    sim: Any,
    *,
    rump_kg_by_species: Mapping[str, float],
) -> dict[str, float]:
    values = {key: 0.0 for key in TERMINAL_RUMP_REPORT_CLASSES}
    class_method = getattr(sim, '_terminal_rump_by_class', None)
    if callable(class_method):
        try:
            raw_values = dict(class_method() or {})
        except (TypeError, ValueError):
            raw_values = {}
        for key in TERMINAL_RUMP_REPORT_CLASSES:
            try:
                kg = float(raw_values.get(key, 0.0))
            except (TypeError, ValueError):
                kg = 0.0
            if kg > 0.0:
                values[key] = kg
        _raise_if_rump_class_total_mismatches_total(values, rump_kg_by_species)
        return values

    values = _terminal_rump_classes_from_species(rump_kg_by_species)
    _raise_if_rump_class_total_mismatches_total(values, rump_kg_by_species)
    return values


def _terminal_rump_classes_from_species(
    species_kg: Mapping[str, float],
) -> dict[str, float]:
    classes = {key: 0.0 for key in TERMINAL_RUMP_REPORT_CLASSES}
    for species, kg in species_kg.items():
        if species in TERMINAL_RUMP_REFRACTORY_OXIDES:
            key = 'refractory_oxides'
        elif species in TERMINAL_RUMP_SILICATE_RESIDUAL:
            key = 'silicate_residual'
        elif species in TERMINAL_RUMP_UNEXTRACTED_METALS:
            key = 'unextracted_metals'
        else:
            key = 'other'
        classes[key] += float(kg)
    return classes


def _raise_if_rump_class_total_mismatches_total(
    values: Mapping[str, float],
    rump_kg_by_species: Mapping[str, float],
) -> None:
    rump_total_kg = float(sum(float(kg) for kg in rump_kg_by_species.values()))
    class_total_kg = float(sum(float(kg) for kg in values.values()))
    if rump_total_kg > 0.0:
        error_pct = abs(class_total_kg - rump_total_kg) / rump_total_kg * 100.0
    else:
        error_pct = 0.0 if class_total_kg == 0.0 else float('inf')
    if error_pct > TERMINAL_RUMP_CLASS_TOLERANCE_PCT:
        raise ValueError(
            "terminal rump report class mass does not match rump total: "
            f"{class_total_kg:.15g} kg vs {rump_total_kg:.15g} kg "
            f"({error_pct:.15g} pct)"
        )


def _ledger_species_kg(
    sim: Any,
    accounts: tuple[str, ...],
    *,
    exclude_species: set[str] | None = None,
) -> dict[str, float]:
    ledger = getattr(sim, 'atom_ledger', None)
    project_account_kg = getattr(ledger, 'project_account_kg', None)
    if not callable(project_account_kg):
        return {}
    excluded = exclude_species or set()
    values: dict[str, float] = {}
    for account in accounts:
        raw = project_account_kg(account)
        if not isinstance(raw, Mapping):
            continue
        for species, kg in raw.items():
            name = str(species)
            if name in excluded:
                continue
            try:
                amount = float(kg)
            except (TypeError, ValueError):
                continue
            if amount > 0.0:
                values[name] = values.get(name, 0.0) + amount
    return dict(sorted(values.items()))


def _spent_reductant_residue_kg(sim: Any) -> dict[str, float]:
    getter = getattr(sim, '_spent_reductant_residue_by_species', None)
    if callable(getter):
        try:
            values = getter() or {}
        except (TypeError, ValueError):
            values = {}
        if isinstance(values, Mapping):
            return {
                str(species): float(kg)
                for species, kg in sorted(values.items())
                if float(kg) > 0.0
            }
    return _ledger_species_kg(sim, PROCESS_INVENTORY_SPENT_REDUCTANT_ACCOUNTS)


def _oxygen_partition_kg(sim: Any) -> dict[str, float]:
    partition_method = getattr(sim, '_oxygen_terminal_partition_kg', None)
    if callable(partition_method):
        try:
            partition = partition_method() or {}
        except (TypeError, ValueError):
            partition = {}
        if isinstance(partition, Mapping):
            values = {}
            for key, value in partition.items():
                amount = float(value)
                if amount >= 0.0:
                    values[str(key)] = amount
            if 'total' in values:
                return values
    record = getattr(sim, 'record', None)
    if record is None:
        return {}
    stored = float(getattr(record, 'oxygen_stored_kg', 0.0) or 0.0)
    vented = float(getattr(record, 'oxygen_vented_kg', 0.0) or 0.0)
    total = float(getattr(record, 'oxygen_total_kg', stored + vented) or 0.0)
    values = {
        'stored': stored,
        'vented': vented,
        'total': total,
    }
    return values


_PRODUCT_STORY_GLASS_SPECIES = frozenset({'SiO', 'SiO2'})
_PRODUCT_STORY_CAPTURED_VOLATILE_SPECIES = frozenset({'Na', 'K', 'Mg'})
_PRODUCT_STORY_CLASSIFIED_SPECIES = (
    frozenset(METAL_PRODUCT_SPECIES)
    | _PRODUCT_STORY_GLASS_SPECIES
    | _PRODUCT_STORY_CAPTURED_VOLATILE_SPECIES
)
_PRODUCT_STORY_OXYGEN_ESCAPE_ACCOUNTS = (
    *OXYGEN_VENTED_ACCOUNTS,
    OXYGEN_BUBBLER_EXTERNAL_VENTED_ACCOUNT,
)


def _merge_product_story_mass(target, species, mass):
    value = float(mass)
    if value > 0.0:
        target[str(species)] = target.get(str(species), 0.0) + value


def product_story_account_kg(sim):
    accounts = (*PRODUCT_LEDGER_ACCOUNTS, TERMINAL_ESCAPE_ACCOUNT)
    return {
        account: dict(sim.atom_ledger.project_account_kg(account) or {})
        for account in accounts
        if account != C7_AL_CREDIT_ACCOUNT
    }


def product_story_extracted_projection(
    sim,
    *,
    account_kg,
    classification=None,
):
    classification_available = classification is not None

    if classification_available:
        # The shared classifier owns recovered-product destinations and their
        # mass. The web story only partitions the remaining condenser account.
        metal_ingots = dict(
            classification.get('ingots_metals', {}).get('species_kg', {}) or {}
        )
        glass = dict(classification.get('glass', {}).get('species_kg', {}) or {})
        captured_volatiles = dict(
            classification.get('captured_volatiles', {}).get(
                'kg_by_species', {}
            ) or {}
        )
        recognized_condensate: dict[str, float] = {}
        metal_sources = classification.get('ingots_metals', {}).get(
            'source_accounts_by_species', {}
        ) or {}
        for species, sources in metal_sources.items():
            if not isinstance(sources, (list, tuple)):
                continue
            for source in sources:
                if (
                    isinstance(source, Mapping)
                    and source.get('account') == CONDENSATION_TRAIN_ACCOUNT
                ):
                    _merge_product_story_mass(
                        recognized_condensate, species, source.get('kg', 0.0)
                    )
        # These two classifier buckets are derived from the condenser ledger
        # account; their species masses are already capped by that account.
        for category in (glass, captured_volatiles):
            for species, mass in category.items():
                _merge_product_story_mass(recognized_condensate, species, mass)
        off_spec_condensate = {}
        for species, ledger_mass in account_kg.get(
            CONDENSATION_TRAIN_ACCOUNT, {}
        ).items():
            accepted = recognized_condensate.get(str(species), 0.0)
            remainder = max(0.0, float(ledger_mass) - accepted)
            _merge_product_story_mass(off_spec_condensate, species, remainder)
    else:
        # Keep raw completion usable if the shared classifier refuses an
        # incomplete ledger. This fallback is never used for a sound story.
        metal_ingots = {}
        native_metals = getattr(
            getattr(getattr(sim, 'record', None), 'initial_inventory', None),
            'metal_alloy_kg',
            {},
        ) or {}
        for account in EXTRACTED_METAL_PRODUCT_ACCOUNTS:
            for species, mass in account_kg.get(account, {}).items():
                if species not in (
                    _PRODUCT_STORY_GLASS_SPECIES
                    | _PRODUCT_STORY_CAPTURED_VOLATILE_SPECIES
                ):
                    recovered = max(
                        0.0,
                        float(mass)
                        - max(float(native_metals.get(species, 0.0)), 0.0),
                    )
                    _merge_product_story_mass(metal_ingots, species, recovered)
        glass = {}
        captured_volatiles = {}
        off_spec_condensate = {}
        condensation_stage_species = {}
        for key, mass in (
            getattr(sim, '_stage_collection_kg_by_source', {}) or {}
        ).items():
            if not isinstance(key, tuple) or len(key) != 3:
                continue
            source_account, raw_stage_index, species = key
            if source_account != CONDENSATION_TRAIN_ACCOUNT:
                continue
            try:
                stage_idx = int(raw_stage_index)
                stage = sim.train.stages[stage_idx]
                stage_number = int(getattr(stage, 'stage_number', stage_idx))
                stage_identity = (
                    stage_number,
                    int(getattr(stage, 'sub_order', 0)),
                )
                stage_key = str(
                    getattr(stage, 'stage_key', '')
                    or STAGE_KEY_BY_IDENTITY.get(stage_identity, '')
                )
            except (AttributeError, IndexError, TypeError, ValueError):
                continue
            if stage_key:
                condensation_stage_species.setdefault(str(species), []).append(
                    (stage_idx, stage_key, float(mass))
                )
        for species, ledger_mass in account_kg.get(
            CONDENSATION_TRAIN_ACCOUNT, {}
        ).items():
            routed = condensation_stage_species.get(str(species), [])
            routed_total = sum(max(0.0, mass) for _idx, _key, mass in routed)
            scale = min(1.0, float(ledger_mass) / routed_total) if routed_total else 0.0
            accounted = 0.0
            for stage_idx, stage_key, projected_mass in routed:
                mass = max(0.0, projected_mass) * scale
                accounted += mass
                stage_number = int(
                    getattr(sim.train.stages[stage_idx], 'stage_number', stage_idx)
                )
                accepted = _product_stage_accepts_species(str(species), stage_key)
                if (
                    accepted
                    and stage_number == 3
                    and species in _PRODUCT_STORY_GLASS_SPECIES
                ):
                    _merge_product_story_mass(glass, species, mass)
                elif (
                    accepted
                    and stage_number == 4
                    and species in _PRODUCT_STORY_CAPTURED_VOLATILE_SPECIES
                ):
                    _merge_product_story_mass(captured_volatiles, species, mass)
                elif (
                    (accepted or (stage_number == 4 and species == 'Ca'))
                    and species in METAL_PRODUCT_SPECIES
                ):
                    _merge_product_story_mass(metal_ingots, species, mass)
                else:
                    _merge_product_story_mass(off_spec_condensate, species, mass)
            _merge_product_story_mass(
                off_spec_condensate,
                species,
                max(0.0, float(ledger_mass) - accounted),
            )

    oxygen = {}
    oxygen_partition = {}
    for account in (*OXYGEN_STORED_ACCOUNTS, *OXYGEN_CAPTURED_ACCOUNTS):
        account_total = 0.0
        for species, mass in sim.atom_ledger.project_account_kg(account).items():
            _merge_product_story_mass(oxygen, species, mass)
            account_total += float(mass)
        if account_total > 0.0:
            oxygen_partition[account] = account_total

    return {
        'metal_ingots': metal_ingots,
        'glass': glass,
        'captured_volatiles': captured_volatiles,
        'oxygen': oxygen,
        'oxygen_partition': oxygen_partition,
        'off_spec_condensate': off_spec_condensate,
    }


def product_story_extracted_kg(extracted_projection):
    return sum(
        sum(float(mass) for mass in extracted_projection[key].values())
        for key in ('metal_ingots', 'glass', 'oxygen', 'captured_volatiles')
    )


def product_story_bucket(species_kg, **details):
    rounded_species = {}
    for species, mass in sorted(species_kg.items()):
        if float(mass) <= 0.0:
            continue
        displayed_mass = round(mass, 2)
        if displayed_mass != 0.0:
            rounded_species[species] = displayed_mass
    return {
        'species_kg': rounded_species,
        **{key: round(float(value), 2) for key, value in details.items()},
        'class_total_kg': round(sum(species_kg.values()), 2),
    }


def build_product_story(
    sim,
    *,
    terminal_rump_by_species,
    classification=None,
    account_kg=None,
    extracted_projection=None,
):
    if classification is None:
        classification = classify_products(sim)
    if account_kg is None:
        account_kg = product_story_account_kg(sim)
    if extracted_projection is None:
        extracted_projection = product_story_extracted_projection(
            sim,
            account_kg=account_kg,
            classification=classification,
        )
    metal_ingots = dict(extracted_projection['metal_ingots'])
    glass = dict(extracted_projection['glass'])
    captured_volatiles = dict(extracted_projection['captured_volatiles'])
    escaped_to_vacuum = {}
    unrecovered_process_inventory = {}
    unclassified = {}
    off_spec_condensate = dict(extracted_projection['off_spec_condensate'])
    wall_deposits = {}
    process_residue = {}

    for account, species_kg in account_kg.items():
        for species, mass in species_kg.items():
            if account == TERMINAL_ESCAPE_ACCOUNT:
                target = escaped_to_vacuum
            elif account in METAL_PHASE_ACCOUNTS:
                continue
            elif account in EXTRACTED_METAL_PRODUCT_ACCOUNTS:
                continue
            elif account == CONDENSATION_TRAIN_ACCOUNT:
                continue
            elif account.startswith('process.'):
                target = unrecovered_process_inventory
            elif species in _PRODUCT_STORY_CLASSIFIED_SPECIES:
                target = unrecovered_process_inventory
            else:
                target = unclassified
            _merge_product_story_mass(target, species, mass)

    native_metal_species = set(
        getattr(
            getattr(getattr(sim, 'record', None), 'initial_inventory', None),
            'metal_alloy_kg',
            {},
        ) or {}
    )
    native_unclassified = classification.get('unclassified', {})
    native_unclassified = native_unclassified.get('kg_by_species', {})
    for species, mass in native_unclassified.items():
        if species in native_metal_species:
            _merge_product_story_mass(unclassified, species, mass)

    for account in _PRODUCT_STORY_OXYGEN_ESCAPE_ACCOUNTS:
        for species, mass in sim.atom_ledger.project_account_kg(account).items():
            _merge_product_story_mass(escaped_to_vacuum, species, mass)

    for species, mass in sim.atom_ledger.project_account_kg(
        CONDENSATION_RETAINED_HOLDUP_ACCOUNT
    ).items():
        _merge_product_story_mass(unrecovered_process_inventory, species, mass)

    for species, mass in sim.atom_ledger.project_account_kg(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT
    ).items():
        _merge_product_story_mass(process_residue, species, mass)

    for account in sim.atom_ledger.mol_by_account():
        if not (
            str(account) == 'process.wall_deposit'
            or str(account).startswith('process.wall_deposit_segment_')
        ):
            continue
        for species, mass in sim.atom_ledger.project_account_kg(account).items():
            _merge_product_story_mass(wall_deposits, species, mass)

    refractory_ceramic = {}
    terminal_residue = {}
    for species, mass in terminal_rump_by_species.items():
        target = (
            refractory_ceramic
            if species in TERMINAL_RUMP_REFRACTORY_OXIDES
            else terminal_residue
        )
        _merge_product_story_mass(target, species, mass)

    oxygen = product_story_bucket(extracted_projection['oxygen'])
    oxygen['partition_kg'] = {
        key: round(float(value), 2)
        for key, value in extracted_projection['oxygen_partition'].items()
    }

    residue_bucket = product_story_bucket(
        terminal_residue,
        silicate_residual_kg=sum(
            mass for species, mass in terminal_residue.items()
            if species in TERMINAL_RUMP_SILICATE_RESIDUAL
        ),
        unextracted_metals_kg=sum(
            mass for species, mass in terminal_residue.items()
            if species in TERMINAL_RUMP_UNEXTRACTED_METALS
        ),
        other_kg=sum(
            mass for species, mass in terminal_residue.items()
            if species not in (
                TERMINAL_RUMP_SILICATE_RESIDUAL
                | TERMINAL_RUMP_UNEXTRACTED_METALS
            )
        ),
    )

    refractory_ceramic_bucket = product_story_bucket(refractory_ceramic)
    metal_ingots_bucket = product_story_bucket(metal_ingots)
    metal_sources = classification.get('ingots_metals', {})
    if isinstance(metal_sources, Mapping):
        metal_ingots_bucket['source_accounts_by_species'] = dict(
            metal_sources.get('source_accounts_by_species', {}) or {}
        )
    unrecovered_metal_sources = classification.get(
        'unrecovered_metal_phase', {}
    )
    if isinstance(unrecovered_metal_sources, Mapping):
        unrecovered_metal_phase_bucket = product_story_bucket(
            unrecovered_metal_sources.get('species_kg', {}) or {}
        )
        unrecovered_metal_phase_bucket['source_accounts_by_species'] = dict(
            unrecovered_metal_sources.get(
                'source_accounts_by_species', {}
            ) or {}
        )
    else:
        unrecovered_metal_phase_bucket = product_story_bucket({})
    try:
        refractory_ceramic_bucket['ree_enrichment_extent'] = (
            AccountingQueries(sim).terminal_ree_enrichment_extent()
        )
    except Exception:  # noqa: BLE001 -- raw product masses remain usable
        refractory_ceramic_bucket['ree_enrichment_extent'] = {}

    story = {
        'input': {
            'feedstock': sim.record.feedstock_key,
            'feedstock_label': sim.record.feedstock_label,
            'batch_mass_kg': round(float(sim.record.batch_mass_kg), 2),
        },
        'metal_ingots': metal_ingots_bucket,
        'glass': product_story_bucket(glass),
        'oxygen': oxygen,
        'captured_volatiles': product_story_bucket(captured_volatiles),
        'refractory_ceramic': refractory_ceramic_bucket,
        'terminal_residue': residue_bucket,
        'escaped_to_vacuum': product_story_bucket(escaped_to_vacuum),
        'unrecovered_metal_phase': unrecovered_metal_phase_bucket,
        'unrecovered_process_inventory': product_story_bucket(
            unrecovered_process_inventory
        ),
        'wall_deposits': product_story_bucket(wall_deposits),
        'process_residue': product_story_bucket(process_residue),
        'off_spec_condensate': product_story_bucket(off_spec_condensate),
        'unclassified': product_story_bucket(unclassified),
    }
    for name, source_name in (
        ('metal_ingots', 'ingots_metals'),
        ('glass', 'glass'),
        ('oxygen', 'oxygen'),
        ('captured_volatiles', 'captured_volatiles'),
        ('refractory_ceramic', 'refractory_ceramic_rump'),
    ):
        source = classification.get(source_name)
        if not isinstance(source, Mapping):
            continue
        estimated_contributions = source.get('estimated_contributions')
        if estimated_contributions:
            story[name]['estimated_contributions'] = list(
                estimated_contributions
            )
        availability_flag = source.get('availability_flag')
        flag = source.get('flag')
        if not isinstance(availability_flag, Mapping) and not isinstance(flag, Mapping):
            continue
        if isinstance(flag, Mapping):
            story[name]['flag'] = dict(flag)
        if isinstance(availability_flag, Mapping):
            story[name]['availability_flag'] = dict(availability_flag)
        story[name]['status'] = source.get('status') or (
            availability_flag or flag
        ).get('status')
        story[name]['excluded'] = list(source.get('excluded', ()))
        if story[name]['status'] == 'unavailable':
            story[name]['class_total_kg'] = None
    if story['oxygen'].get('class_total_kg') is None:
        story['oxygen']['partition_kg'] = dict.fromkeys(
            story['oxygen']['partition_kg']
        )
    rump = classification.get('refractory_ceramic_rump')
    if isinstance(rump, Mapping):
        estimated_contributions = rump.get('estimated_contributions')
        if estimated_contributions:
            story['terminal_residue']['estimated_contributions'] = list(
                estimated_contributions
            )
        rump_flag = rump.get('availability_flag') or rump.get('flag')
        if (
            isinstance(rump_flag, Mapping)
            and rump_flag.get('status') in {'partial', 'unavailable'}
        ):
            story['terminal_residue']['availability_flag'] = dict(rump_flag)
            story['terminal_residue']['status'] = rump_flag.get('status')
            story['terminal_residue']['excluded'] = list(rump.get('excluded', ()))
            for field, source_field in (
                ('silicate_residual_kg', 'rump_silicate_residual_kg'),
                ('unextracted_metals_kg', 'rump_unextracted_metals_kg'),
            ):
                if source_field in rump and rump[source_field] is None:
                    if rump_flag.get('status') == 'unavailable':
                        story['terminal_residue'][field] = None
                        story['terminal_residue']['class_total_kg'] = None
    return story
