import math

import pytest

from engines.builtin.metal_phase_stratification import (
    BuiltinMetalPhaseStratificationProvider,
)
from simulator.account_ids import (
    METAL_BOTTOM_POOL_ACCOUNT,
    METAL_FLOAT_LAYER_ACCOUNT,
    METAL_PHASE_ACCOUNT,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel.planner import ChemistryKernel
from simulator.chemistry.kernel.registry import ProviderRegistry
from simulator.metal_stratification import (
    first_order_transfer_fraction,
    k_mix_from_axial_stirring,
)


def _request(
    accounts,
    *,
    k_mix_per_hr,
    dt_hr=1.0,
    mode="stratify",
    prior_pool_mol=None,
):
    return IntentRequest(
        intent=ChemistryIntent.METAL_PHASE_STRATIFICATION,
        account_view=ProviderAccountView(
            accounts=accounts,
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=1.0,
        control_inputs={
            "mode": mode,
            "k_mix_per_hr": k_mix_per_hr,
            "dt_hr": dt_hr,
            "temperature_K": 1873.15,
            "melt_density_kg_m3": 2700.0,
            "prior_pool_mol": prior_pool_mol or {},
        },
    )


def _after_mol(accounts, proposal):
    after = {account: dict(species) for account, species in accounts.items()}
    for account, species_mol in proposal.debits.items():
        for species, amount in species_mol.items():
            after.setdefault(account, {})[species] = (
                after.get(account, {}).get(species, 0.0) - amount
            )
    for account, species_mol in proposal.credits.items():
        for species, amount in species_mol.items():
            after.setdefault(account, {})[species] = (
                after.get(account, {}).get(species, 0.0) + amount
            )
    return after


def _species_total(accounts, species):
    return sum(float(pool.get(species, 0.0)) for pool in accounts.values())


def test_stirring_on_scavenges_si_to_fe_bottom_and_conserves_each_species():
    accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": 1.0, "Si": 2.0, "Al": 1.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(accounts, k_mix_per_hr=math.log(100.0))
    )
    after = _after_mol(accounts, result.transition)

    assert after[METAL_BOTTOM_POOL_ACCOUNT]["Fe"] == pytest.approx(1.0)
    assert after[METAL_BOTTOM_POOL_ACCOUNT]["Si"] == pytest.approx(1.98)
    assert after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(0.02)
    assert after[METAL_FLOAT_LAYER_ACCOUNT]["Al"] == pytest.approx(1.0)
    for species in ("Fe", "Si", "Al"):
        assert _species_total(after, species) == pytest.approx(
            _species_total(accounts, species), abs=1e-14
        )


def test_stirring_off_leaves_new_si_in_float_layer_for_top_product_strategy():
    accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": 1.0, "Si": 2.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(accounts, k_mix_per_hr=0.0)
    )
    after = _after_mol(accounts, result.transition)
    assert after[METAL_BOTTOM_POOL_ACCOUNT].get("Si", 0.0) == 0.0
    assert after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(2.0)


def test_without_fe_si_remains_float_even_at_large_k_mix():
    accounts = {
        METAL_PHASE_ACCOUNT: {"Si": 2.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(accounts, k_mix_per_hr=100.0)
    )
    after = _after_mol(accounts, result.transition)
    assert after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(2.0)
    assert after[METAL_BOTTOM_POOL_ACCOUNT].get("Si", 0.0) == 0.0


def test_trace_fe_does_not_pull_a_buoyant_si_destination_to_bottom():
    accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": 1e-11, "Si": 1.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(accounts, k_mix_per_hr=100.0)
    )
    after = _after_mol(accounts, result.transition)

    assert result.diagnostic["si_destination_buoyancy"]["verdict"] != "sink"
    assert after[METAL_BOTTOM_POOL_ACCOUNT].get("Si", 0.0) == 0.0
    assert after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(1.0)


@pytest.mark.parametrize("scale", [1e-13, 1.0, 1e13])
def test_si_routing_is_invariant_to_uniform_inventory_scale(scale):
    accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": scale, "Si": 2.0 * scale},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(accounts, k_mix_per_hr=math.log(2.0))
    )
    after = _after_mol(accounts, result.transition)

    assert result.diagnostic["si_destination_buoyancy"]["verdict"] == "sink"
    assert after[METAL_BOTTOM_POOL_ACCOUNT]["Si"] / (2.0 * scale) == pytest.approx(
        0.5
    )


def test_restore_staging_reverses_diagnostic_pool_disposition_exactly():
    accounts = {
        METAL_PHASE_ACCOUNT: {"unclassified": 0.25},
        METAL_BOTTOM_POOL_ACCOUNT: {"Fe": 1.0, "Si": 1.98},
        METAL_FLOAT_LAYER_ACCOUNT: {"Al": 1.0, "Si": 0.02},
    }
    result = BuiltinMetalPhaseStratificationProvider().dispatch(
        _request(
            accounts,
            k_mix_per_hr=0.0,
            dt_hr=0.0,
            mode="restore_staging",
        )
    )
    after = _after_mol(accounts, result.transition)

    assert after[METAL_BOTTOM_POOL_ACCOUNT] == pytest.approx({"Fe": 0.0, "Si": 0.0})
    assert after[METAL_FLOAT_LAYER_ACCOUNT] == pytest.approx({"Al": 0.0, "Si": 0.0})
    assert after[METAL_PHASE_ACCOUNT] == pytest.approx(
        {"unclassified": 0.25, "Fe": 1.0, "Al": 1.0, "Si": 2.0}
    )
    for species in ("unclassified", "Fe", "Si", "Al"):
        assert _species_total(after, species) == pytest.approx(
            _species_total(accounts, species), abs=1e-14
        )


def test_first_order_fraction_has_exact_limits_and_no_overshoot():
    assert first_order_transfer_fraction(0.0, 1.0) == 0.0
    assert first_order_transfer_fraction(math.log(2.0), 1.0) == pytest.approx(0.5)
    assert 0.9999 < first_order_transfer_fraction(100.0, 1.0) <= 1.0


def test_stirring_knob_maps_off_to_floor_and_default_on_to_99pct_contact():
    k_off = k_mix_from_axial_stirring(0.0)
    k_default = k_mix_from_axial_stirring(6.0)

    assert k_off == pytest.approx(0.0001)
    assert first_order_transfer_fraction(k_off, 1.0) == pytest.approx(
        1.0 - math.exp(-0.0001)
    )
    assert first_order_transfer_fraction(k_default, 1.0) == pytest.approx(0.99)


def test_multi_hour_ode_preserves_prior_partition_across_staging_restore():
    provider = BuiltinMetalPhaseStratificationProvider()
    first_accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": 1.0, "Si": 2.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }
    first = provider.dispatch(
        _request(first_accounts, k_mix_per_hr=math.log(2.0))
    )
    first_after = _after_mol(first_accounts, first.transition)
    prior = {
        "bottom_pool": first_after[METAL_BOTTOM_POOL_ACCOUNT],
        "float_layer": first_after[METAL_FLOAT_LAYER_ACCOUNT],
    }
    restored_accounts = {
        METAL_PHASE_ACCOUNT: {"Fe": 1.0, "Si": 2.0},
        METAL_BOTTOM_POOL_ACCOUNT: {},
        METAL_FLOAT_LAYER_ACCOUNT: {},
    }

    second = provider.dispatch(
        _request(
            restored_accounts,
            k_mix_per_hr=math.log(2.0),
            prior_pool_mol=prior,
        )
    )
    second_after = _after_mol(restored_accounts, second.transition)

    assert first_after[METAL_BOTTOM_POOL_ACCOUNT]["Si"] == pytest.approx(1.0)
    assert first_after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(1.0)
    assert second_after[METAL_BOTTOM_POOL_ACCOUNT]["Si"] == pytest.approx(1.5)
    assert second_after[METAL_FLOAT_LAYER_ACCOUNT]["Si"] == pytest.approx(0.5)
    assert _species_total(second_after, "Si") == pytest.approx(2.0, abs=1e-14)


def test_pool_move_lands_only_through_kernel_commit_batch():
    ledger = AtomLedger()
    ledger.load_external_mol(METAL_PHASE_ACCOUNT, {"Fe": 1.0, "Si": 2.0})
    provider = BuiltinMetalPhaseStratificationProvider()
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.METAL_PHASE_STRATIFICATION])
    kernel = ChemistryKernel(ledger, registry, species_formula_registry={})

    result = kernel.dispatch(
        ChemistryIntent.METAL_PHASE_STRATIFICATION,
        temperature_C=1600.0,
        pressure_bar=1.0,
        control_inputs={
            "k_mix_per_hr": math.log(100.0),
            "dt_hr": 1.0,
            "temperature_K": 1873.15,
            "melt_density_kg_m3": 2700.0,
        },
    )
    assert ledger.mol_by_account(METAL_PHASE_ACCOUNT)["Fe"] == 1.0
    assert result.transition is not None

    kernel.commit_batch(ChemistryIntent.METAL_PHASE_STRATIFICATION, result.transition)
    assert ledger.mol_by_account(METAL_PHASE_ACCOUNT).get("Fe", 0.0) == 0.0
    assert ledger.mol_by_account(METAL_BOTTOM_POOL_ACCOUNT)["Fe"] == pytest.approx(1.0)
    assert ledger.mol_by_account(METAL_BOTTOM_POOL_ACCOUNT)["Si"] == pytest.approx(1.98)
    assert ledger.mol_by_account(METAL_FLOAT_LAYER_ACCOUNT)["Si"] == pytest.approx(0.02)
    ledger.assert_balanced()


def test_k_mix_stir_command_refuses_non_finite():
    import math
    import pytest
    from simulator.metal_stratification import k_mix_from_axial_stirring

    # NaN must not silently read as stirring-OFF (settling floor); it is an
    # invalid command, matching the finite-boundary behavior of infinity.
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            k_mix_from_axial_stirring(bad)
