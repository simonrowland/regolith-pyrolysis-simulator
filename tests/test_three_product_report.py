"""Tests for E6a north-star product-class classifier.

Pins:
1. The expanded output shape preserves the original 5 canonical
   buckets (metals+O2 / silica / mixed glass / rump / unclassified)
   and adds product-specific convenience views.
2. Metal/O2 sums match the canonical METAL_PRODUCT_SPECIES list.
3. Stage 3 capture reads SiO + SiO2 collected_kg.
4. Defensive: missing methods / empty product_ledger don't raise.
5. End-to-end on real sim — full classification round-trips
   product_ledger() without losing or duplicating mass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.accounting.queries import TERMINAL_RUMP_CLASS_TOLERANCE_PCT
from simulator.backends import BackendSelectionPolicy
from simulator.condensation_routing import (
    CONDENSATION_COPRODUCT_STAGE,
    DESIGNATED_STAGE,
)
from simulator.session import SimSession, SimSessionConfig
from simulator.three_product_report import (
    METAL_PRODUCT_SPECIES,
    O2_PRODUCT_SPECIES,
    PURE_SILICA_GLASS_MIN_PURITY_WT_PCT,
    PURE_SILICA_GLASS_SPECIES,
    _qualified_si_only_clause,
    classify_products,
)
from simulator.three_product_report_markdown import (
    format_three_product_markdown,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# d-398 foreign co-deposits that are not condenser routing keys. The routed
# species come from DESIGNATED_STAGE and CONDENSATION_COPRODUCT_STAGE.
_STAGE_3_FOREIGN_OXIDES = ('FeO', 'K2O', 'MgO', 'Na2O')
_TRACE_STAGE3_MASSES_KG = (1.0e-10, 1.0e-15)


def _stage_3_deposit_species() -> tuple[str, ...]:
    return tuple(sorted(
        set(DESIGNATED_STAGE)
        | set(CONDENSATION_COPRODUCT_STAGE)
        | set(_STAGE_3_FOREIGN_OXIDES)
    ))


def _stage_3_foreign_species() -> tuple[str, ...]:
    silica_bearing = set(PURE_SILICA_GLASS_SPECIES) | {'Si'}
    return tuple(
        species for species in _stage_3_deposit_species()
        if species not in silica_bearing
    )


def _stage_3_sim(*, collected: dict, snapshots: list):
    return SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[
            None, None, None, SimpleNamespace(collected_kg=dict(collected)),
        ]),
        record=SimpleNamespace(snapshots=snapshots),
        atom_ledger=None,
    )


def _load(name: str) -> dict:
    with (DATA_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def _config(**overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": "lunar_mare_low_ti",
        "feedstocks": _load("feedstocks.yaml"),
        "setpoints": _load("setpoints.yaml"),
        "vapor_pressures": _load("vapor_pressures.yaml"),
        "campaign": "C2A",
        "backend_name": "internal-analytical",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


# ---------------------------------------------------------------------------
# 1. Output shape
# ---------------------------------------------------------------------------

def test_classifier_returns_documented_5_bucket_shape():
    """The classifier MUST return the documented expanded dict while
    preserving the original 5 canonical buckets for downstream
    consumers (E6b runner CLI, web UI, log scrapers)."""
    session = SimSession().start(_config(campaign="C2A"))
    result = classify_products(session.simulator)
    canonical_buckets = {
        'metals_plus_O2',
        'pure_silica_glass',
        'industrial_mixed_glass',
        'refractory_ceramic_rump',
        'unclassified',
    }
    additive_buckets = {
        'ingots_metals',
        'unrecovered_metal_phase',
        'oxygen',
        'glass',
        'captured_volatiles',
        'process_inventory_spent_reductant',
    }
    assert canonical_buckets <= result.keys()
    assert set(result.keys()) == canonical_buckets | additive_buckets
    # Each bucket carries a ``class_total_kg`` (or for unclassified,
    # ``total_kg``) so the operator can sum across classes.
    for bucket in (
        'metals_plus_O2', 'pure_silica_glass',
        'industrial_mixed_glass', 'refractory_ceramic_rump',
        'ingots_metals', 'unrecovered_metal_phase', 'oxygen', 'glass',
        'captured_volatiles',
    ):
        assert 'class_total_kg' in result[bucket]
        assert isinstance(result[bucket]['class_total_kg'], float)
        assert result[bucket]['class_total_kg'] >= 0.0


# ---------------------------------------------------------------------------
# 2. Metals + O2 species coverage
# ---------------------------------------------------------------------------

def test_metals_plus_o2_uses_canonical_species_list():
    """The metals product class iterates METAL_PRODUCT_SPECIES
    (canonical list). O2 is read directly. Sanity check that the
    canonical lists match what the docstring + CLAUDE.md describe."""
    assert 'Na' in METAL_PRODUCT_SPECIES
    assert 'Fe' in METAL_PRODUCT_SPECIES
    assert 'Si' in METAL_PRODUCT_SPECIES
    assert 'Al' in METAL_PRODUCT_SPECIES
    # SiO is NOT here — it's a gas-phase silicate oxide, mapped to
    # the silica-glass class.
    assert 'SiO' not in METAL_PRODUCT_SPECIES
    assert 'SiO2' not in METAL_PRODUCT_SPECIES
    # O2 is its own surface.
    assert O2_PRODUCT_SPECIES == ('O2',)


def test_metals_total_excludes_o2():
    """``metals_total_kg`` carries the metal-only sum; ``O2_kg`` is
    separate. ``class_total_kg`` = metals + O2 (the full class 1
    bookkeeping per CLAUDE.md § 5)."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'Fe': 5.0, 'Na': 1.0, 'O2': 2.0,
            'SomeOxide': 0.5,  # → unclassified
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['metals_plus_O2']['metals_total_kg'] == 6.0
    assert result['metals_plus_O2']['O2_kg'] == 2.0
    assert result['metals_plus_O2']['class_total_kg'] == 8.0


# ---------------------------------------------------------------------------
# 3. Pure silica glass: Stage 3 capture
# ---------------------------------------------------------------------------

def test_native_cleanup_is_inventory_but_extracted_same_species_remains_product():
    native_metals = {'Fe': 2.0, 'Ni': 3.0}
    sim = SimpleNamespace(
        product_ledger=lambda: {'Fe': 5.0, 'Ni': 3.0, 'Co': 0.5},
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(metal_alloy_kg=native_metals),
        ),
    )

    result = classify_products(sim)

    assert result['metals_plus_O2']['metals_kg'] == {'Fe': 3.0, 'Co': 0.5}
    assert result['ingots_metals']['class_total_kg'] == 3.5
    assert result['unclassified']['kg_by_species'] == native_metals
    assert result['unclassified']['total_kg'] == 5.0
    assert result['ingots_metals']['class_total_kg'] + result['unclassified']['total_kg'] == 8.5


def test_stage_accepted_metals_are_products_and_phase_remainder_is_inventory():
    from simulator.accounting.queries import CONDENSATION_TRAIN_ACCOUNT

    from simulator.three_product_report import _metal_product_accounting

    class Ledger:
        accounts = {
            'terminal.drain_tap_material': {'Fe': 2.0},
            CONDENSATION_TRAIN_ACCOUNT: {'Fe': 1.5, 'Na': 0.6, 'K': 0.2},
            'process.metal_phase': {'Si': 5.0, 'Fe': 3.0},
            'process.metal_phase_float_layer': {'Al': 0.75},
        }

        def project_account_kg(self, account):
            return dict(self.accounts.get(account, {}))

    stage_keys = [
        'stage_0_hot_duct',
        '',
        'stage_2_cr_oxide_harvest',
        '',
        'stage_4_alkali_mg_cyclone',
        'stage_5_vortex_dust_filter',
        'stage_6_turbine_compressor',
        'stage_7_accumulator',
        'stage_4b_k_trap',
    ]
    sim = SimpleNamespace(
        atom_ledger=Ledger(),
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(metal_alloy_kg={}),
        ),
        train=SimpleNamespace(
            stages=[
                SimpleNamespace(stage_key=key, stage_number=(4 if i == 8 else i))
                for i, key in enumerate(stage_keys)
            ],
        ),
        _stage_collection_kg_by_source={
            (CONDENSATION_TRAIN_ACCOUNT, 1, 'Fe'): 1.5,
            (CONDENSATION_TRAIN_ACCOUNT, 4, 'Na'): 0.6,
            (CONDENSATION_TRAIN_ACCOUNT, 8, 'K'): 0.2,
            ('process.metal_phase', 3, 'Si'): 1.0,
        },
    )

    result = _metal_product_accounting(sim, products={})

    assert result['species_kg'] == {
        'Fe': 3.5,
        'K': 0.2,
        'Na': 0.6,
        'Si': 1.0,
    }
    assert result['source_accounts_by_species']['Fe'] == [
        {'account': 'process.condensation_train', 'kg': 1.5},
        {'account': 'terminal.drain_tap_material', 'kg': 2.0},
    ]
    assert result['unrecovered_metal_phase_kg'] == {
        'Al': 0.75,
        'Fe': 3.0,
        'Si': 4.0,
    }
    assert result['unrecovered_metal_phase_sources_by_species']['Al'] == [
        {'account': 'process.metal_phase_float_layer', 'kg': 0.75},
    ]
    classification = classify_products(sim)
    assert classification['unrecovered_metal_phase'] == {
        'species_kg': {'Al': 0.75, 'Fe': 3.0, 'Si': 4.0},
        'source_accounts_by_species': {
            'Al': [
                {'account': 'process.metal_phase_float_layer', 'kg': 0.75},
            ],
            'Fe': [{'account': 'process.metal_phase', 'kg': 3.0}],
            'Si': [{'account': 'process.metal_phase', 'kg': 4.0}],
        },
        'class_total_kg': 7.75,
    }


def test_unrecovered_only_metal_is_not_duplicated_by_aggregate_projection():
    sim = SimSession().start(_config(campaign='C0')).simulator
    sim.atom_ledger.load_external_mol(
        'process.metal_phase_float_layer',
        {'Al': 4.0},
        material_origin='feedstock',
    )

    account_projection = sim.atom_ledger.project_account_kg(
        'process.metal_phase_float_layer'
    )
    aggregate_projection = sim.product_ledger()
    assert aggregate_projection['Al'] == account_projection['Al']

    result = classify_products(sim)
    assert result['unrecovered_metal_phase']['species_kg']['Al'] == (
        account_projection['Al']
    )
    assert result['unclassified']['kg_by_species'].get('Al', 0.0) == 0.0


def test_mixed_source_aluminum_inventory_is_reconciled_by_account():
    from simulator.account_ids import (
        METAL_FLOAT_LAYER_ACCOUNT,
        TERMINAL_DRAIN_TAP_ACCOUNT,
    )
    from simulator.accounting.queries import CONDENSATION_TRAIN_ACCOUNT

    sim = SimSession().start(_config(campaign='C0')).simulator
    for account, amount_mol in (
        (TERMINAL_DRAIN_TAP_ACCOUNT, 1.0),
        (CONDENSATION_TRAIN_ACCOUNT, 2.0),
        (METAL_FLOAT_LAYER_ACCOUNT, 3.0),
    ):
        sim.atom_ledger.load_external_mol(
            account, {'Al': amount_mol}, material_origin='feedstock',
        )

    source_kg = {
        account: sim.atom_ledger.project_account_kg(account)['Al']
        for account in (
            TERMINAL_DRAIN_TAP_ACCOUNT,
            CONDENSATION_TRAIN_ACCOUNT,
            METAL_FLOAT_LAYER_ACCOUNT,
        )
    }
    inventory_kg = sim.product_ledger()['Al']
    result = classify_products(sim)

    assert result['ingots_metals']['species_kg']['Al'] == source_kg[
        TERMINAL_DRAIN_TAP_ACCOUNT
    ]
    assert result['unrecovered_metal_phase']['species_kg']['Al'] == source_kg[
        METAL_FLOAT_LAYER_ACCOUNT
    ]
    assert result['unclassified']['kg_by_species']['Al'] == source_kg[
        CONDENSATION_TRAIN_ACCOUNT
    ]
    assert sum((
        result['ingots_metals']['species_kg']['Al'],
        result['unrecovered_metal_phase']['species_kg']['Al'],
        result['unclassified']['kg_by_species']['Al'],
    )) == pytest.approx(inventory_kg, rel=0.0, abs=1e-15)


def test_c0_native_cleanup_does_not_produce_ingots_before_any_step():
    sim = SimSession().start(_config(campaign='C0')).simulator
    result = classify_products(sim)

    assert sim.record.initial_inventory.metal_alloy_kg['Ni'] > 0.0
    assert sim.record.initial_inventory.metal_alloy_kg['Co'] > 0.0
    assert result['ingots_metals']['class_total_kg'] == 0.0
    for species, kg in sim.record.initial_inventory.metal_alloy_kg.items():
        assert result['unclassified']['kg_by_species'][species] == pytest.approx(kg)

def test_silica_glass_reads_stage_3_collected_kg():
    """The Stage 3 fused-silica baffles surface is
    ``train.stages[3].collected_kg``. The classifier sums SiO + SiO2
    entries there."""
    stage_3 = SimpleNamespace(collected_kg={'SiO': 3.0, 'SiO2': 1.0})
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[None, None, None, stage_3]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['pure_silica_glass']['stage_3_capture_kg'] == 4.0
    assert result['pure_silica_glass']['stage_3_kg_by_species'] == {
        'SiO': 3.0, 'SiO2': 1.0,
    }


def test_silica_glass_capture_is_unavailable_when_stage_3_is_missing():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)

    silica = result['pure_silica_glass']
    assert silica['stage_3_capture_kg'] is None
    assert silica['class_total_kg'] is None
    assert silica['flag']['status'] == 'unavailable'
    assert 'stage_3_missing' in silica['flag']['reason']
    assert 'owner product spec pending' in silica['flag']['reason']
    assert silica['stage_3_kg_by_species'] == {}
    assert result['glass']['class_total_kg'] is None
    assert result['glass']['pure_silica_glass_kg'] is None


@pytest.mark.parametrize(
    ('stage_3', 'reason'),
    [
        (SimpleNamespace(), 'stage_3_collection_missing'),
        (SimpleNamespace(collected_kg=None), 'stage_3_collection_missing'),
        (SimpleNamespace(collected_kg=[]), 'stage_3_collection_malformed'),
    ],
)
def test_silica_glass_capture_is_unavailable_when_stage_3_collection_is_unknown(
    stage_3, reason
):
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[None, None, None, stage_3]),
        atom_ledger=None,
    )
    result = classify_products(sim)

    silica = result['pure_silica_glass']
    assert silica['stage_3_capture_kg'] is None
    assert silica['class_total_kg'] is None
    assert silica['flag']['status'] == 'unavailable'
    assert reason in silica['flag']['reason']
    assert result['glass']['pure_silica_glass_kg'] is None


def test_silica_glass_capture_is_zero_when_stage_3_exists_with_no_collection():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(
            stages=[None, None, None, SimpleNamespace(collected_kg={})],
        ),
        atom_ledger=None,
    )
    result = classify_products(sim)

    silica = result['pure_silica_glass']
    assert silica['stage_3_capture_kg'] == 0.0
    assert 'owner product spec pending' in silica['flag']['reason']
    assert result['pure_silica_glass']['stage_3_kg_by_species'] == {}


def test_glass_aggregate_keeps_computed_zero_when_capture_is_unqualified():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[None, None, None, SimpleNamespace(
            collected_kg={'SiO2': 4.5, 'Na2O': 0.1},
        )]),
        record=SimpleNamespace(snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={
                (3, 'SiO2'): 4.5,
                (3, 'Na2O'): 0.1,
            },
        )]),
        _last_evaporation_flux_diagnostic={
            'missing_transport_parameters': {
                'Si': {
                    'policy': 'fail_loud_missing_transport_parameters',
                    'refusal_type': 'EvaporationFluxConfigurationError',
                    'reason': 'missing Si transport row',
                    'parent_oxide': 'SiO2',
                    'status': 'unavailable',
                },
            },
        },
    )

    result = classify_products(sim)

    silica = result['pure_silica_glass']
    assert silica['stage_3_capture_kg'] == 4.5
    assert silica['class_total_kg'] == 0.0
    assert silica['status'] == 'partial'
    glass = result['glass']
    assert glass['class_total_kg'] == 0.0
    assert glass['pure_silica_glass_kg'] == 0.0
    assert glass['species_kg'] == {}
    assert glass['status'] == 'partial'
    assert glass['excluded'] == silica['excluded']


def test_silica_qualifies_each_snapshot_and_keeps_exclusions_local():
    snapshots = [
        SimpleNamespace(
            c2a_staged_gas={
                'stage_name': 'C4',
                'gas_cover_mode': 'pn2_sweep',
            },
            condensed_by_stage_species_delta={
                (3, 'SiO2'): 2.0,
                (3, 'Si'): 0.5,
            },
        ),
        SimpleNamespace(
            c2a_staged_gas={
                'stage_name': 'post_mg',
                'gas_cover_mode': 'vacuum',
            },
            condensed_by_stage_species_delta={
                (3, 'SiO2'): 3.0,
                (3, 'Na2O'): 0.1,
            },
        ),
    ]
    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[None, None, None, SimpleNamespace(
            collected_kg={'SiO2': 5.0, 'Si': 0.5, 'Na2O': 0.1},
        )]),
        record=SimpleNamespace(snapshots=snapshots),
    )

    result = classify_products(sim)

    silica = result['pure_silica_glass']
    assert silica['stage_3_capture_kg'] == 5.0
    assert silica['stage_3_kg_by_species'] == {'SiO2': 5.0, 'Si': 0.5}
    assert silica['class_total_kg'] == 2.0
    assert result['glass']['class_total_kg'] == 2.0
    assert result['glass']['species_kg'] == {'SiO2': 2.0}
    assert silica['flag']['status'] == 'flagged product spec pending'
    assert 'owner product spec pending' in silica['flag']['reason']
    assert 'Stage 3 Si coproduct 0.5 kg (reported separately)' in (
        silica['flag']['reason']
    )
    assert 'Stage 3 snapshot 1 is non-product' in silica['flag']['reason']
    assert 'purity_below_threshold' in silica['flag']['reason']


def test_silica_census_counts_si_coproduct_in_rejected_snapshots():
    """Total Si is qualifying plus rejected; the class still excludes rejected glass."""
    from simulator.three_product_report import _stage_3_glass_snapshot_census

    snapshots = [
        SimpleNamespace(condensed_by_stage_species_delta={
            (3, 'SiO2'): 2.0,
            (3, 'Si'): 0.5,
        }),
        SimpleNamespace(condensed_by_stage_species_delta={
            (3, 'SiO2'): 1.0,
            (3, 'Si'): 0.25,
            (3, 'Na2O'): 0.2,
        }),
    ]

    census = _stage_3_glass_snapshot_census(snapshots)

    assert census['issue'] is None
    assert census['product_kg'] == pytest.approx(2.0)
    assert census['qualified_silicon_coproduct_kg'] == pytest.approx(0.5)
    assert census['silicon_coproduct_kg'] == pytest.approx(0.75)
    assert census['excluded_kg_by_reason']['purity_below_threshold'] == (
        pytest.approx(1.45)
    )

    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[None, None, None, SimpleNamespace(
            collected_kg={'SiO2': 3.0, 'Si': 0.75, 'Na2O': 0.2},
        )]),
        record=SimpleNamespace(snapshots=snapshots),
    )
    silica = classify_products(sim)['pure_silica_glass']
    assert silica['class_total_kg'] == pytest.approx(2.0)
    assert (
        'Stage 3 Si coproduct 0.75 kg (qualified 0.5 kg; reported separately)'
        in silica['flag']['reason']
    )


def test_silica_snapshot_at_98_9_percent_is_excluded_with_reason():
    assert PURE_SILICA_GLASS_MIN_PURITY_WT_PCT == 99.0
    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[None, None, None, SimpleNamespace(
            collected_kg={'SiO2': 98.9, 'Na2O': 1.1},
        )]),
        record=SimpleNamespace(snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={
                (3, 'SiO2'): 98.9,
                (3, 'Na2O'): 1.1,
            },
        )]),
    )

    silica = classify_products(sim)['pure_silica_glass']

    assert silica['class_total_kg'] == 0.0
    assert 'purity_below_threshold' in silica['flag']['reason']
    assert '98.900 wt% silica-bearing' in silica['flag']['reason']


def test_pure_silica_snapshot_outside_sio_window_counts_as_product():
    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[None, None, None, SimpleNamespace(
            collected_kg={'SiO2': 3.0},
        )]),
        record=SimpleNamespace(snapshots=[SimpleNamespace(
            c2a_staged_gas={
                'stage_name': 'C4',
                'gas_cover_mode': 'vacuum',
            },
            condensed_by_stage_species_delta={(3, 'SiO2'): 3.0},
        )]),
    )

    result = classify_products(sim)

    assert result['pure_silica_glass']['class_total_kg'] == 3.0
    assert result['glass']['class_total_kg'] == 3.0
    assert 'owner product spec pending' in result['pure_silica_glass']['flag']['reason']


def test_train_trace_silica_without_snapshots_is_not_numeric_zero():
    """Missing snapshots are a typed refusal, not a glass total of 0."""
    kg = 1.0e-10
    result = classify_products(_stage_3_sim(
        collected={'SiO2': kg},
        snapshots=[],
    ))
    silica = result['pure_silica_glass']

    assert silica['stage_3_capture_kg'] == pytest.approx(kg)
    assert silica['class_total_kg'] is None
    assert result['glass']['class_total_kg'] is None
    assert result['glass']['pure_silica_glass_kg'] is None
    assert silica['flag']['status'] == 'unavailable'
    assert 'stage_3_species_missing_from_snapshots: SiO2' in silica['flag']['reason']


@pytest.mark.parametrize('foreign', _stage_3_foreign_species())
def test_trace_pure_snapshot_does_not_qualify_when_train_omits_foreign(foreign):
    """A 1e-10 kg foreign train deposit omitted from the snapshot blocks qualification."""
    kg = 1.0e-10
    result = classify_products(_stage_3_sim(
        collected={'SiO2': kg, foreign: kg},
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={(3, 'SiO2'): kg},
        )],
    ))
    silica = result['pure_silica_glass']

    assert silica['class_total_kg'] is None
    assert result['glass']['class_total_kg'] is None
    assert silica['class_total_kg'] != kg
    assert 'stage_3_species_missing_from_snapshots: ' in silica['flag']['reason']
    assert foreign in silica['flag']['reason']
    assert silica['flag']['status'] == 'unavailable'


@pytest.mark.parametrize('species', _stage_3_deposit_species())
@pytest.mark.parametrize('kg', _TRACE_STAGE3_MASSES_KG)
@pytest.mark.parametrize('missing_side', ('snapshots', 'train'))
def test_missing_positive_stage3_species_refuses_at_every_trace_mass(
    species, kg, missing_side,
):
    """No positive Stage-3 species is reconciled to zero by absolute tolerance."""
    if missing_side == 'snapshots':
        sim = _stage_3_sim(collected={species: kg}, snapshots=[])
        token = 'stage_3_species_missing_from_snapshots'
    else:
        sim = _stage_3_sim(
            collected={},
            snapshots=[SimpleNamespace(
                condensed_by_stage_species_delta={(3, species): kg},
            )],
        )
        token = 'stage_3_species_missing_from_train'

    silica = classify_products(sim)['pure_silica_glass']

    assert silica['class_total_kg'] is None
    assert silica['class_total_kg'] != 0.0
    assert silica['flag']['status'] == 'unavailable'
    assert f'{token}: {species}' in silica['flag']['reason']


@pytest.mark.parametrize('species', PURE_SILICA_GLASS_SPECIES)
def test_agreed_trace_silica_species_still_counts(species):
    kg = 1.0e-10
    result = classify_products(_stage_3_sim(
        collected={species: kg},
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={(3, species): kg},
        )],
    ))

    assert result['pure_silica_glass']['class_total_kg'] == pytest.approx(kg)
    assert result['glass']['class_total_kg'] == pytest.approx(kg)
    assert 'stage_3_species_missing' not in result['pure_silica_glass']['flag']['reason']


@pytest.mark.parametrize('species', _stage_3_foreign_species())
def test_agreed_trace_foreign_deposit_is_excluded_not_refused(species):
    kg = 1.0e-10
    silica = classify_products(_stage_3_sim(
        collected={species: kg},
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={(3, species): kg},
        )],
    ))['pure_silica_glass']

    assert silica['class_total_kg'] == 0.0
    assert 'purity_below_threshold' in silica['flag']['reason']
    assert 'stage_3_species_missing' not in silica['flag']['reason']
    assert silica['flag']['status'] == 'flagged product spec pending'


def test_trace_scale_ledger_rounding_stays_distinct_from_a_missing_species():
    silica = classify_products(_stage_3_sim(
        collected={'SiO2': 1.0e-10},
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={(3, 'SiO2'): 2.0e-10},
        )],
    ))['pure_silica_glass']

    assert silica['class_total_kg'] is None
    assert 'stage_3_species_missing' not in silica['flag']['reason']
    assert (
        'Stage 3 snapshot composition does not match the train ledger for SiO2'
        in silica['flag']['reason']
    )


def test_trace_rejected_si_is_not_reported_as_equal_to_qualified_si():
    """A 1e-10 kg rejected Si coproduct is not closed against zero."""
    collected = {'SiO2': 1.0, 'Na2O': 1.0, 'Si': 1.0e-10}
    silica = classify_products(_stage_3_sim(
        collected=collected,
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={
                (3, 'SiO2'): 1.0,
                (3, 'Na2O'): 1.0,
                (3, 'Si'): 1.0e-10,
            },
        )],
    ))['pure_silica_glass']

    assert silica['class_total_kg'] == 0.0
    assert 'purity_below_threshold' in silica['flag']['reason']
    assert 'Stage 3 Si coproduct 1e-10 kg (qualified 0 kg; reported separately)' in (
        silica['flag']['reason']
    )


_INVENTED_PURITY_FAILURE = (
    'No Stage 3 snapshot met the measured silica-bearing purity threshold'
)
_ZERO_GLASS_CLASS_NOTE = (
    'Glass class mass is zero. A zero class mass does not mean the '
    'purity gate failed.'
)
_LUNAR_GLASS_KG = 8.939275771102051e-11
_MARE_QUALIFIED_SI_KG = 0.025455486114855548
_SILICA_BEARING_SPECIES = (*PURE_SILICA_GLASS_SPECIES, 'Si')
_REPORT_MASS_GRID_KG = (_LUNAR_GLASS_KG, 1.0e-15, 1.0e-12, 9.99e-10, 1.0)


def _rendered_kg(kg: float) -> str:
    return f'{kg:.3e}' if kg < 1.0 else f'{kg:.3f}'


def _classified_report(sim):
    result = classify_products(sim)
    return result, format_three_product_markdown(result)


def test_qualified_si_only_wording_has_one_owner():
    source = Path(
        classify_products.__code__.co_filename
    ).read_text()
    markdown = Path(
        format_three_product_markdown.__code__.co_filename
    ).read_text()
    assert source.count('qualified Si-only') == 1
    assert 'qualified Si-only' not in markdown
    assert _INVENTED_PURITY_FAILURE not in source
    assert _INVENTED_PURITY_FAILURE not in markdown


@pytest.mark.parametrize(
    ('glass_kg', 'qualified_silicon_kg', 'expects_clause'),
    (
        (None, 1.0, False),
        (1.0, 1.0, False),
        (0.0, 0.0, False),
        (0.0, -1.0, False),
        (2.0, _MARE_QUALIFIED_SI_KG, False),
        (0.0, _MARE_QUALIFIED_SI_KG, True),
        (0.0, _LUNAR_GLASS_KG, True),
    ),
)
def test_si_only_clause_fires_only_for_a_zero_glass_class(
    glass_kg, qualified_silicon_kg, expects_clause,
):
    clause = _qualified_si_only_clause(glass_kg, qualified_silicon_kg)
    if expects_clause:
        assert clause.startswith('; qualified Si-only ')
        assert f'{qualified_silicon_kg:.6g} kg Si' in clause
        assert 'class mass is SiO2 + SiO and is zero' in clause
    else:
        assert clause == ''


@pytest.mark.parametrize('species', _SILICA_BEARING_SPECIES)
@pytest.mark.parametrize('kg', _REPORT_MASS_GRID_KG)
@pytest.mark.parametrize('off_window', (False, True))
def test_pure_snapshot_report_keeps_the_mass_and_does_not_invent_failure(
    species, kg, off_window,
):
    """History is provenance. A pure snapshot counts, and a trace stays visible."""
    snapshot = {
        'condensed_by_stage_species_delta': {(3, species): kg},
    }
    if off_window:
        snapshot['c2a_staged_gas'] = {
            'stage_name': 'C4',
            'gas_cover_mode': 'vacuum',
        }
    result, report = _classified_report(_stage_3_sim(
        collected={species: kg},
        snapshots=[SimpleNamespace(**snapshot)],
    ))
    silica = result['pure_silica_glass']
    rendered = _rendered_kg(kg)

    assert _INVENTED_PURITY_FAILURE not in report
    assert rendered in report
    assert f'**{species}**: {rendered} kg' in report
    if species == 'Si':
        assert silica['class_total_kg'] == 0.0
        assert 'qualified Si-only' in silica['flag']['reason']
        assert 'qualified Si-only' in report
        assert _ZERO_GLASS_CLASS_NOTE in report
        assert 'purity_below_threshold' not in report
    else:
        assert silica['class_total_kg'] == pytest.approx(kg)
        assert f'Silica glass: {rendered} kg' in report
        assert 'qualified Si-only' not in report
        assert _ZERO_GLASS_CLASS_NOTE not in report


@pytest.mark.parametrize('species', _SILICA_BEARING_SPECIES)
@pytest.mark.parametrize('foreign', _stage_3_foreign_species())
@pytest.mark.parametrize('scale', (1.0, 1.0e-12))
def test_below_threshold_report_records_the_exclusion_without_a_blanket(
    species, foreign, scale,
):
    silica_kg = 98.9 * scale
    foreign_kg = 1.1 * scale
    result, report = _classified_report(_stage_3_sim(
        collected={species: silica_kg, foreign: foreign_kg},
        snapshots=[SimpleNamespace(condensed_by_stage_species_delta={
            (3, species): silica_kg,
            (3, foreign): foreign_kg,
        })],
    ))
    silica = result['pure_silica_glass']

    assert silica['class_total_kg'] == 0.0
    assert 'purity_below_threshold' in silica['flag']['reason']
    assert 'purity_below_threshold' in report
    assert 'qualified Si-only' not in report
    assert _ZERO_GLASS_CLASS_NOTE in report
    assert _INVENTED_PURITY_FAILURE not in report
    assert f'**{species}**: {_rendered_kg(silica_kg)} kg' in report


@pytest.mark.parametrize('foreign', _stage_3_foreign_species())
@pytest.mark.parametrize('kg', (_LUNAR_GLASS_KG, 1.0e-15, 1.0))
def test_foreign_only_report_excludes_the_deposit_without_a_blanket(foreign, kg):
    result, report = _classified_report(_stage_3_sim(
        collected={foreign: kg},
        snapshots=[SimpleNamespace(
            condensed_by_stage_species_delta={(3, foreign): kg},
        )],
    ))

    assert result['pure_silica_glass']['class_total_kg'] == 0.0
    assert 'purity_below_threshold' in report
    assert 'qualified Si-only' not in report
    assert _ZERO_GLASS_CLASS_NOTE in report
    assert _INVENTED_PURITY_FAILURE not in report
    assert f'{kg:.6g} kg' in report


@pytest.mark.parametrize('foreign', _stage_3_foreign_species())
def test_si_only_snapshot_keeps_its_qualification_beside_an_exclusion(foreign):
    """The lunar shape: zero glass class, qualified Si, and a failed snapshot."""
    si_kg = _MARE_QUALIFIED_SI_KG
    result, report = _classified_report(_stage_3_sim(
        collected={'Si': si_kg, 'SiO2': 1.0, foreign: 1.0},
        snapshots=[
            SimpleNamespace(condensed_by_stage_species_delta={(3, 'Si'): si_kg}),
            SimpleNamespace(condensed_by_stage_species_delta={
                (3, 'SiO2'): 1.0,
                (3, foreign): 1.0,
            }),
        ],
    ))
    silica = result['pure_silica_glass']

    assert silica['class_total_kg'] == 0.0
    assert 'qualified Si-only' in silica['flag']['reason']
    assert f'{si_kg:.6g} kg Si' in silica['flag']['reason']
    assert 'purity_below_threshold' in silica['flag']['reason']
    assert 'qualified Si-only' in report
    assert 'purity_below_threshold' in report
    assert f'**Si**: {_rendered_kg(si_kg)} kg' in report
    assert '**SiO2**: 1.000 kg' in report
    assert _ZERO_GLASS_CLASS_NOTE in report
    assert _INVENTED_PURITY_FAILURE not in report


@pytest.mark.parametrize('species', PURE_SILICA_GLASS_SPECIES)
@pytest.mark.parametrize('foreign', _stage_3_foreign_species())
@pytest.mark.parametrize('kg', (_LUNAR_GLASS_KG, 2.0))
def test_mixed_snapshot_report_shows_the_qualifying_mass_only(
    species, foreign, kg,
):
    result, report = _classified_report(_stage_3_sim(
        collected={species: kg, foreign: kg},
        snapshots=[
            SimpleNamespace(condensed_by_stage_species_delta={(3, species): kg}),
            SimpleNamespace(condensed_by_stage_species_delta={(3, foreign): kg}),
        ],
    ))
    rendered = _rendered_kg(kg)

    assert result['pure_silica_glass']['class_total_kg'] == pytest.approx(kg)
    assert f'Silica glass: {rendered} kg' in report
    assert f'**{species}**: {rendered} kg' in report
    assert 'purity_below_threshold' in report
    assert 'qualified Si-only' not in report
    assert _ZERO_GLASS_CLASS_NOTE not in report
    assert _INVENTED_PURITY_FAILURE not in report
    if kg == _LUNAR_GLASS_KG:
        assert '8.939e-11' in report
        assert 'Silica glass: — kg' not in report


def test_empty_stage3_report_does_not_call_the_zero_a_purity_failure():
    result, report = _classified_report(_stage_3_sim(
        collected={},
        snapshots=[],
    ))

    assert result['pure_silica_glass']['class_total_kg'] == 0.0
    assert _ZERO_GLASS_CLASS_NOTE in report
    assert 'qualified Si-only' not in report
    assert 'purity_below_threshold' not in report
    assert _INVENTED_PURITY_FAILURE not in report


def _load_b1185_census():
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1] / 'scripts' / 'b1185_glass_census.py'
    )
    spec = importlib.util.spec_from_file_location('b1185_glass_census', script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_c0_glass_census_includes_every_configured_profile():
    module = _load_b1185_census()
    cases = {case['case_id']: case for case in module._c0_profiles()}
    configured = {}
    for path in sorted(module.OPTIMIZER_PROFILE_DIR.rglob('*.yaml')):
        profile = yaml.safe_load(path.read_text()) or {}
        run = profile.get('run') or {}
        if run.get('campaign') != 'C0':
            continue
        case_id = path.relative_to(module.OPTIMIZER_PROFILE_DIR).with_suffix('').as_posix()
        configured[case_id] = int(run['hours'])

    assert configured
    assert 60 in configured.values()
    assert set(cases) == set(configured)
    for case_id, hours in configured.items():
        assert cases[case_id]['hours'] == hours
        assert 'status' not in cases[case_id]


def test_refused_metallic_c0_profile_is_not_reported_as_glass_zero():
    """A run that never steps is a typed refusal, not qualified glass of 0 kg."""
    module = _load_b1185_census()
    case = next(
        item for item in module._c0_profiles()
        if item['case_id'] == 'm_type_metallic_phase'
    )
    with pytest.raises(ValueError, match='liquid_fraction_provider_failed'):
        module._run_case(**case)


def test_all_or_nothing_mutation_check_fails_the_mixed_snapshot_node(request):
    """The mutation script must run the classifier in-process (-n0)."""
    script = (
        Path(__file__).resolve().parents[1]
        / 'scripts'
        / 'check_b1185_all_or_nothing_mutation.py'
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    reporter = request.config.pluginmanager.get_plugin('terminalreporter')
    if reporter is not None:
        for line in (completed.stdout + completed.stderr).splitlines():
            reporter.write_line(line)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'PASS:' in completed.stdout
    assert 'fails with the all-or-nothing mutation' in completed.stdout


def test_captured_volatiles_include_condensation_train_account():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            project_account_kg=lambda acct: {
                'terminal.offgas': {'H2O': 1.0, 'O2': 2.0},
                'process.condensation_train': {'H2O': 3.0, 'CO': 4.0},
            }.get(acct, {})
        ),
    )

    result = classify_products(sim)

    assert result['captured_volatiles']['kg_by_species'] == {
        'CO': 4.0,
        'H2O': 3.0,
    }
    assert result['captured_volatiles']['class_total_kg'] == pytest.approx(7.0)


def test_spent_reductant_residue_surfaces_as_process_inventory_bucket():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            project_account_kg=lambda acct: {
                'process.cleaned_melt': {'CaO': 1.0},
                'process.spent_reductant_residue': {'Na2O': 2.5},
            }.get(acct, {})
        ),
        _terminal_rump_by_species=lambda: {'CaO': 1.0},
    )

    result = classify_products(sim)

    assert result['process_inventory_spent_reductant'] == {
        'kg_by_species': {'Na2O': 2.5},
        'class_total_kg': 2.5,
        'account': 'process.spent_reductant_residue',
        'disposition': 'process_inventory_spent_reductant',
    }
    assert result['refractory_ceramic_rump']['rump_kg_by_species'] == {'CaO': 1.0}
    assert 'Na2O' not in result['refractory_ceramic_rump']['rump_kg_by_species']
    assert 'Na2O' not in result['unclassified']['kg_by_species']


# ---------------------------------------------------------------------------
# 4. Refractory ceramic rump
# ---------------------------------------------------------------------------

def test_rump_reads_terminal_rump_method():
    """The classifier prefers ``_terminal_rump_by_species`` when
    available (the canonical surface). Empty dict / missing method
    is the no-rump degenerate."""
    sim_with_rump = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        _terminal_rump_by_species=lambda: {'CaO': 5.0, 'REE_oxides': 0.5},
    )
    result = classify_products(sim_with_rump)
    assert result['refractory_ceramic_rump']['rump_total_kg'] == 5.5
    assert (
        result['refractory_ceramic_rump']['rump_kg_by_species']
        == {'CaO': 5.0, 'REE_oxides': 0.5}
    )


def test_rump_ree_enrichment_extent_is_ledger_derived_not_fitted():
    class Ledger:
        @staticmethod
        def project_account_kg(account):
            return (
                {'CaO': 48.0, 'REE_oxides': 2.0}
                if account == 'process.cleaned_melt'
                else {}
            )

    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=Ledger(),
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(
                melt_oxide_kg={'CaO': 98.0, 'REE_oxides': 2.0}
            )
        ),
        _terminal_rump_by_species=lambda: {'CaO': 48.0, 'REE_oxides': 2.0},
    )

    extent = classify_products(sim)['refractory_ceramic_rump'][
        'ree_enrichment_extent'
    ]

    assert extent['source_ids'] == ['REF-056', 'REF-057']
    assert extent['ree_retention_fraction'] == pytest.approx(1.0)
    assert extent['mass_removal_extent_fraction'] == pytest.approx(0.5)
    assert extent['initial_ree_oxides_wt_pct'] == pytest.approx(2.0)
    assert extent['terminal_ree_oxides_wt_pct'] == pytest.approx(4.0)
    assert extent['ree_enrichment_factor'] == pytest.approx(2.0)


def test_rump_ree_enrichment_aggregates_split_inert_lanthanide_oxides():
    class Ledger:
        @staticmethod
        def project_account_kg(account):
            return (
                {'SiO2': 48.0, 'La2O3': 1.2, 'Ce2O3': 0.8}
                if account == 'process.cleaned_melt'
                else {}
            )

    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=Ledger(),
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(
                melt_oxide_kg={'SiO2': 98.0},
                inert_melt_components_kg={'La2O3': 1.2, 'Ce2O3': 0.8},
            )
        ),
        _terminal_rump_by_species=lambda: {
            'SiO2': 48.0,
            'La2O3': 1.2,
            'Ce2O3': 0.8,
        },
    )

    rump = classify_products(sim)['refractory_ceramic_rump']
    extent = rump['ree_enrichment_extent']

    assert rump['rump_refractory_oxides_kg'] == pytest.approx(2.0)
    assert rump['rump_silicate_residual_kg'] == pytest.approx(48.0)
    assert extent['initial_ree_oxides_kg'] == pytest.approx(2.0)
    assert extent['terminal_ree_oxides_kg'] == pytest.approx(2.0)
    assert extent['ree_retention_fraction'] == pytest.approx(1.0)
    assert extent['initial_ree_oxides_wt_pct'] == pytest.approx(2.0)
    assert extent['terminal_ree_oxides_wt_pct'] == pytest.approx(4.0)
    assert extent['ree_enrichment_factor'] == pytest.approx(2.0)


def test_rump_ree_extent_excludes_c7_credit_and_aluminate_product():
    class Ledger:
        @staticmethod
        def project_account_kg(account):
            return {
                'process.cleaned_melt': {'CaO': 48.0, 'REE_oxides': 2.0},
                'process.c7_al_credit': {'Al': 100.0},
                'terminal.slag': {'Ca3Al2O6': 25.0},
            }.get(account, {})

    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=Ledger(),
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(
                melt_oxide_kg={'CaO': 98.0, 'REE_oxides': 2.0}
            )
        ),
        _terminal_rump_by_species=lambda: {
            'CaO': 48.0,
            'REE_oxides': 2.0,
            'Al': 100.0,
            'Ca3Al2O6': 25.0,
        },
    )

    extent = classify_products(sim)['refractory_ceramic_rump'][
        'ree_enrichment_extent'
    ]

    assert extent['terminal_ree_oxides_wt_pct'] == pytest.approx(4.0)
    assert extent['mass_removal_extent_fraction'] == pytest.approx(0.5)
    assert extent['ree_enrichment_factor'] == pytest.approx(2.0)


def test_rump_surfaces_nonzero_other_bucket_and_mass_closes():
    """The terminal rump report must not hide the accounting ``other`` bucket."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        _terminal_rump_by_species=lambda: {
            'CaO': 4.0,
            'SiO2': 1.0,
            'Fe': 0.5,
            'NaCl': 0.25,
        },
        _terminal_rump_by_class=lambda: {
            'refractory_oxides': 4.0,
            'silicate_residual': 1.0,
            'unextracted_metals': 0.5,
            'other': 0.25,
        },
    )

    result = classify_products(sim)
    bucket = result['refractory_ceramic_rump']

    assert bucket['rump_total_kg'] == pytest.approx(5.75)
    assert bucket['class_total_kg'] == pytest.approx(4.0)
    assert bucket['rump_refractory_oxides_kg'] == pytest.approx(4.0)
    assert bucket['rump_silicate_residual_kg'] == pytest.approx(1.0)
    assert bucket['rump_unextracted_metals_kg'] == pytest.approx(0.5)
    assert bucket['rump_other_kg'] == pytest.approx(0.25)

    surfaced_total_kg = (
        bucket['rump_refractory_oxides_kg']
        + bucket['rump_silicate_residual_kg']
        + bucket['rump_unextracted_metals_kg']
        + bucket['rump_other_kg']
    )
    error_pct = abs(surfaced_total_kg - bucket['rump_total_kg']) / (
        bucket['rump_total_kg']
    ) * 100.0
    assert error_pct <= TERMINAL_RUMP_CLASS_TOLERANCE_PCT

    assert bucket['rump_refractory_oxides_kg'] > 0.0
    assert bucket['rump_silicate_residual_kg'] > 0.0
    assert bucket['rump_unextracted_metals_kg'] > 0.0
    assert bucket['rump_other_kg'] > 0.0
    assert (
        bucket['rump_refractory_oxides_kg']
        != bucket['rump_unextracted_metals_kg']
    )
    assert bucket['rump_unextracted_metals_kg'] != bucket['rump_other_kg']


def test_rump_zero_when_method_missing():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['refractory_ceramic_rump']['rump_total_kg'] == 0.0


# ---------------------------------------------------------------------------
# 5. Industrial mixed glass: early-tap detection
# ---------------------------------------------------------------------------

def test_mixed_glass_zero_by_default_even_with_cleaned_melt():
    """Per evening-4commits review P2 #2: ``cleaned_melt`` at any
    mid-run tick is NOT a mixed-glass product — it's the melt
    sitting in the crucible waiting for the next campaign. The
    classifier MUST default to zero for this bucket unless the
    operator explicitly declares early-tap intent."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            project_account_kg=lambda acct: (
                {'SiO2': 100.0, 'Al2O3': 20.0}
                if acct == 'process.cleaned_melt' else {}
            ),
        ),
    )
    result = classify_products(sim)
    # Default behaviour: mixed-glass bucket zeroed — operator hasn't
    # declared early-tap intent.
    assert (
        result['industrial_mixed_glass']['mixed_melt_residual_kg']
        == 0.0
    )
    assert (
        result['industrial_mixed_glass']['early_tap_mode'] is False
    )


def test_mixed_glass_counts_cleaned_melt_only_in_early_tap_mode():
    """With ``early_tap_mode=True``, the cleaned_melt residual IS
    the mixed-glass product (operator has chosen to tap before
    C5/C6). 100 + 20 = 120 kg residual melt → 120 kg mixed-glass
    when the explicit intent flag is on."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            project_account_kg=lambda acct: (
                {'SiO2': 100.0, 'Al2O3': 20.0}
                if acct == 'process.cleaned_melt' else {}
            ),
        ),
    )
    result = classify_products(sim, early_tap_mode=True)
    assert (
        result['industrial_mixed_glass']['mixed_melt_residual_kg']
        == 120.0
    )
    assert (
        result['industrial_mixed_glass']['early_tap_mode'] is True
    )


# ---------------------------------------------------------------------------
# 6. Defensive: empty / degenerate inputs
# ---------------------------------------------------------------------------

def test_classifier_handles_empty_product_ledger():
    """A fresh sim with no products at all returns the empty-but-
    well-formed shape — no exceptions."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['metals_plus_O2']['class_total_kg'] == 0.0
    assert result['pure_silica_glass']['stage_3_capture_kg'] is None
    assert result['pure_silica_glass']['class_total_kg'] is None
    assert 'stage_3_missing' in result['pure_silica_glass']['flag']['reason']
    assert result['glass']['pure_silica_glass_kg'] is None
    assert result['refractory_ceramic_rump']['class_total_kg'] == 0.0
    assert result['unclassified']['total_kg'] == 0.0


def test_classifier_handles_missing_product_ledger_method():
    """A sim-like object with no ``product_ledger`` method falls
    back to empty dict."""
    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    # Missing Stage 3 is unavailable; the unrelated empty classes remain zero.
    for bucket in (
        'metals_plus_O2',
        'industrial_mixed_glass', 'refractory_ceramic_rump',
    ):
        assert result[bucket]['class_total_kg'] == 0.0
    assert result['pure_silica_glass']['stage_3_capture_kg'] is None
    assert result['pure_silica_glass']['class_total_kg'] is None
    assert 'stage_3_missing' in result['pure_silica_glass']['flag']['reason']
    assert result['glass']['class_total_kg'] is None
    assert result['glass']['pure_silica_glass_kg'] is None


def test_classifier_handles_non_coercible_product_kg():
    """Defensive: a product entry with a non-coercible kg gets
    skipped, not raised."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'Fe': 5.0,
            'BadEntry': 'not a number',
            'AnotherBad': None,
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    # Fe surfaces; bad entries skipped.
    assert result['metals_plus_O2']['metals_kg']['Fe'] == 5.0
    assert 'BadEntry' not in result['unclassified']['kg_by_species']


# ---------------------------------------------------------------------------
# 7. End-to-end: real sim integration
# ---------------------------------------------------------------------------

def test_classifier_end_to_end_on_short_c2a_run():
    """Drive a SimSession through a short C2A run; classify the
    result. The output is well-formed; we don't assert specific
    masses (those depend on the recipe + are E1b territory)."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    for _ in range(4):
        sim.step()
    result = classify_products(sim)
    # All buckets present + finite.
    assert all(
        isinstance(result[bucket]['class_total_kg'], float)
        and result[bucket]['class_total_kg'] >= 0.0
        for bucket in (
            'metals_plus_O2', 'pure_silica_glass',
            'industrial_mixed_glass', 'refractory_ceramic_rump',
        )
    )


def test_classifier_unclassified_bin_catches_unknown_species():
    """Future-proofing: if a new species lands in product_ledger
    that the canonical lists don't cover, it MUST surface in the
    'unclassified' bin so operators see the mapping gap."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'NewExoticHalide': 2.5,  # not in any canonical list
            'Fe': 1.0,
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['unclassified']['kg_by_species'] == {
        'NewExoticHalide': 2.5,
    }
    assert result['unclassified']['total_kg'] == 2.5
    # Fe still maps correctly to metals.
    assert result['metals_plus_O2']['metals_kg']['Fe'] == 1.0
