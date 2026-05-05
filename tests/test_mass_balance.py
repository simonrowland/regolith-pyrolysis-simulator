import pytest

from simulator.mass_balance import MassBalance
from simulator.state import CondensationTrain, MeltState, ProcessInventory


def test_mass_balance_counts_process_inventory_without_o2_double_count():
    melt = MeltState(composition_kg={"SiO2": 800.0})
    melt.update_total_mass()
    train = CondensationTrain.create_default()
    train.stages[6].collected_kg["O2"] = 7.0
    inventory = ProcessInventory(
        stage0_products_kg={"H2O": 50.0},
        metal_alloy_kg={"Fe": 10.0},
        terminal_slag_components_kg={"ZrO2": 2.0},
        residual_components_kg={"unsupported": 5.0},
    )
    balance = MassBalance()
    balance.set_inputs(874.0, {"K": 3.0})

    result = balance.check(
        melt,
        train,
        oxygen_kg=7.0,
        inventory=inventory,
        additive_inventory_kg={"K": 3.0},
    )

    assert result["mass_out"] == pytest.approx(877.0)
    assert result["condensed"] == pytest.approx(0.0)
    assert result["oxygen"] == pytest.approx(7.0)
    assert result["error_pct"] == pytest.approx(0.0)


def test_product_summary_sums_duplicate_volatile_species():
    train = CondensationTrain.create_default()
    train.stages[3].collected_kg["H2O"] = 2.0
    train.volatiles_collected_kg["H2O"] = 3.0

    products = MassBalance().product_summary(train, oxygen_kg=1.0)

    assert products["H2O"] == pytest.approx(5.0)
    assert products["O2"] == pytest.approx(1.0)
