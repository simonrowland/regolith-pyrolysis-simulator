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
``captured_volatiles``) for product-specific UI/report slices without
removing or reshaping the canonical buckets.

E6a scope: the classifier function. E6b (deferred) is the runner CLI
+ markdown report wrapping E6a.
"""

from __future__ import annotations

from typing import Any, Mapping

# Per CLAUDE.md § 4 + § 5: the species that map cleanly to each
# product class. Some species can land in MORE THAN ONE class
# depending on recipe path (e.g. Al as metal vs Al2O3 in rump if
# C6 thermite wasn't fired); the classifier surfaces BOTH per-class
# subtotals and the gross totals so the operator can see the
# split honestly.

METAL_PRODUCT_SPECIES: tuple[str, ...] = (
    'Na', 'K', 'Fe', 'Mg', 'Si', 'Ti', 'Al', 'Ca',
    'Cr', 'Mn', 'Ni', 'Co',
)
"""Metal alloy / ingot species per CLAUDE.md § 5 product class 1.
``Si`` is here because the post-C6 / post-C5 Si lands as elemental
metal at process.metal_phase. SiO (the gas-phase silicate oxide)
maps to product class 2, NOT this list."""

O2_PRODUCT_SPECIES: tuple[str, ...] = ('O2',)
"""Terminal O₂ accumulator — part of product class 1 (the
disproportionation by-product that motivates the whole refinery)."""

CAPTURED_VOLATILE_ACCOUNTS: tuple[str, ...] = ('terminal.offgas',)
"""Terminal volatile trap accounts that should surface as product output."""

PURE_SILICA_GLASS_SPECIES: tuple[str, ...] = ('SiO', 'SiO2')
"""Product class 2: SiO landed on Stage 3 fused-silica baffles is
the canonical pure-silica output. SiO2 surfaces here when a
post-condensation handler converts the captured SiO via the
documented disproportionation route 2 SiO → SiO2(s) + Si(l)."""

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
                'stage_3_capture_kg': float,
                'stage_3_kg_by_species': {species: kg, ...},
                'class_total_kg': float,
            },
            'glass': {
                'species_kg': {species: kg, ...},
                'class_total_kg': float,
                'pure_silica_glass_kg': float,
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
            'refractory_ceramic_rump': {
                'rump_kg_by_species': {species: kg, ...},
                'rump_total_kg': float,
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
    metals_kg: dict[str, float] = {}
    for species in METAL_PRODUCT_SPECIES:
        kg = float(products.get(species, 0.0))
        if kg > 0.0:
            metals_kg[species] = kg
    metals_total_kg = float(sum(metals_kg.values()))
    oxygen_partition = _oxygen_partition_kg(sim)
    o2_kg = float(oxygen_partition.get('total', products.get('O2', 0.0)))
    metals_plus_o2_total = float(metals_total_kg + o2_kg)

    # ----- Class 2: pure silica glass (Stage 3 capture) -----
    stage_3_kg_by_species: dict[str, float] = {}
    if len(train_stages) > 3:
        stage_3 = train_stages[3]
        collected = dict(getattr(stage_3, 'collected_kg', {}) or {})
        for species in PURE_SILICA_GLASS_SPECIES:
            kg = float(collected.get(species, 0.0))
            if kg > 0.0:
                stage_3_kg_by_species[species] = kg
    stage_3_capture_kg = float(sum(stage_3_kg_by_species.values()))

    # ----- Captured volatiles -----
    captured_volatiles_kg_by_species = _ledger_species_kg(
        sim,
        CAPTURED_VOLATILE_ACCOUNTS,
        exclude_species=set(O2_PRODUCT_SPECIES),
    )
    captured_volatiles_total_kg = float(
        sum(captured_volatiles_kg_by_species.values())
    )

    # ----- Class 4: refractory ceramic rump -----
    rump_kg_by_species: dict[str, float] = {}
    rump_method = getattr(sim, '_terminal_rump_by_species', None)
    if callable(rump_method):
        try:
            rump_kg_by_species = {
                str(species): float(kg)
                for species, kg in (rump_method() or {}).items()
                if float(kg) > 0.0
            }
        except (TypeError, ValueError):
            rump_kg_by_species = {}
    rump_total_kg = float(sum(rump_kg_by_species.values()))

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
            cleaned_melt = sim.atom_ledger.kg_by_account(
                'process.cleaned_melt'
            )
            mixed_melt_residual_kg = float(
                sum(kg for kg in (cleaned_melt or {}).values()
                    if float(kg) > 0.0)
            )
        except (AttributeError, TypeError, ValueError):
            mixed_melt_residual_kg = 0.0

    # ----- Unclassified bin -----
    classified_species: set[str] = (
        set(metals_kg.keys())
        | {'O2'}
        | set(stage_3_kg_by_species.keys())
        | set(captured_volatiles_kg_by_species.keys())
        | set(rump_kg_by_species.keys())
    )
    unclassified: dict[str, float] = {}
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

    return {
        'metals_plus_O2': {
            'metals_kg': metals_kg,
            'metals_total_kg': metals_total_kg,
            'O2_kg': o2_kg,
            'O2_partition_kg': oxygen_partition,
            'class_total_kg': metals_plus_o2_total,
        },
        'ingots_metals': {
            'species_kg': metals_kg,
            'class_total_kg': metals_total_kg,
        },
        'oxygen': {
            'O2_kg': o2_kg,
            'partition_kg': oxygen_partition,
            'class_total_kg': o2_kg,
        },
        'pure_silica_glass': {
            'stage_3_capture_kg': stage_3_capture_kg,
            'stage_3_kg_by_species': stage_3_kg_by_species,
            'class_total_kg': stage_3_capture_kg,
        },
        'glass': {
            'species_kg': stage_3_kg_by_species,
            'class_total_kg': stage_3_capture_kg + mixed_melt_residual_kg,
            'pure_silica_glass_kg': stage_3_capture_kg,
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
        'refractory_ceramic_rump': {
            'rump_kg_by_species': rump_kg_by_species,
            'rump_total_kg': rump_total_kg,
            'class_total_kg': rump_total_kg,
        },
        'unclassified': {
            'kg_by_species': unclassified,
            'total_kg': unclassified_total,
        },
    }


def _ledger_species_kg(
    sim: Any,
    accounts: tuple[str, ...],
    *,
    exclude_species: set[str] | None = None,
) -> dict[str, float]:
    ledger = getattr(sim, 'atom_ledger', None)
    kg_by_account = getattr(ledger, 'kg_by_account', None)
    if not callable(kg_by_account):
        return {}
    excluded = exclude_species or set()
    values: dict[str, float] = {}
    for account in accounts:
        raw = kg_by_account(account)
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
                if amount > 0.0:
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
    return {key: value for key, value in values.items() if value > 0.0}
