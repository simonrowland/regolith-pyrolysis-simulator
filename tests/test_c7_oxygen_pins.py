"""C7 exact production receipts, not experimental validation claims.

R4 recaptures the pinned rounding-level SI gas-constant physics move.
The original source controls and strict hexadecimal assertions remain. Existing independent stoichiometry,
JANAF, redox-refusal and thermodynamic tests remain the physics checks.
"""
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path

import pytest

from simulator.battery.oxygen_balance import _solve_vacuum_oxygen_balance
from simulator.chemistry.langmuir_knudsen import grounded_alpha
from simulator.chemistry.offgas_fo2 import co2_dissociation_partials_bar
from simulator.gas_balance import FiniteCapacity, partition_melt_oxygen
from simulator.refractory_vaporization import solve_congruent_vaporization
from tests.battery.test_residue_oxygen_balance import (
    _engine_models, _hashimoto_start, _with_parent_alpha, _runtime_catalog_rows,
)

FIXTURE = Path(__file__).parent / "fixtures/c7_oxygen_pins.json"
FROZEN = Path(__file__).parent / "fixtures/c7_frozen_source_inputs.json"


def hex_tree(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): hex_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [hex_tree(item) for item in value]
    return value


def d142_receipt(engine, alpha):
    model, channels = _engine_models()[engine]
    channels = _with_parent_alpha(channels, alpha)
    return hex_tree({
        "channels": channels,
        "balance": _solve_vacuum_oxygen_balance(
            model, channels, temperature_K=_hashimoto_start()[0],
        ),
    })


def refractory_receipt(material, mode):
    return hex_tree(solve_congruent_vaporization(
        material, 1800.0, oxygen_mode=mode,
        **({"imposed_pO2_pa": 0.001} if mode == "imposed" else {}),
    ))


def source_receipt():
    from tests.test_overhead_accounting import _gas_train_sim
    sim = _gas_train_sim()
    rows = _runtime_catalog_rows()
    return hex_tree({s: sim._evaporation_stoich(s, rows[s]) for s in (
        "SiO", "Fe", "Na", "K", "Mg", "SiO2_gas", "Na2O_gas", "MgO_gas",
    )})


def co2_receipt(mode):
    kwargs = ({"total_pressure_bar": 0.006, "p_inert_initial_bar": 0.0008}
              if mode == "fixed_P" else {})
    return hex_tree(co2_dissociation_partials_bar(1800.0, 0.005, 0.0002, **kwargs))


def partition_receipt(external):
    return hex_tree(partition_melt_oxygen(
        bled_o2_mol=0.4, overhead_o2_mol=1.0,
        external_o2_holdup_mol=external, capacity=FiniteCapacity(0.005),
        dt_hr=0.25, p_o2_Pa=2.0, k_relief_kg_hr_Pa=0.001,
        p_open_Pa=1.0, molar_mass_kg_mol=0.031998,
    ))


def frozen_receipt(row):
    # Saved source controls from the four named frozen research trajectories;
    # re-enter the existing production provider, retaining raw zero vs floor.
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
    from simulator.chemistry.kernel.dto import ProviderAccountView
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(accounts={"process.cleaned_melt": {}}, species_formula_registry={}),
        temperature_C=row["T_K"] - 273.15, pressure_bar=row["P_Pa"] / 100000.0,
        control_inputs=row["controls"],
    )
    result = BuiltinEvaporationFluxProvider().dispatch(request)
    return hex_tree({"raw_pO2_bar": row["physical_bulk_pO2_bar"],
                     "effective_pO2_bar": row["controls"]["pO2_bar"],
                     "result": result.diagnostic})


def redox_receipt(monkeypatch, capacity):
    from tests.test_overhead_accounting import _gas_train_sim
    sim = _gas_train_sim()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim._sync_oxygen_reservoir_mirror()
    monkeypatch.setattr(sim, "_melt_redox_capacity_mol_per_ln_fO2", lambda **_: capacity)
    result = sim._apply_oxygen_reservoir_redox_source_terms(
        {"test_oxidant_source": 1.0}, exchange_direction="c7_pin",
    )
    from tests.magemin_test_support import close_test_owned_magemin_backend
    close_test_owned_magemin_backend(sim)
    return hex_tree({name: getattr(result, name) for name in (
        "melt_intrinsic_fO2_log", "redox_source_terms_applied",
        "redox_source_skip_reason", "redox_source_skipped_terms_mol_o2_equiv",
        "redox_source_refusal_context", "exchange_direction",
    )})


def expected(family, key):
    return json.loads(FIXTURE.read_text())[family][key]


@pytest.mark.parametrize("engine", ["openimcc", "internal-analytical"])
@pytest.mark.parametrize("alpha", [1.0, 0.25])
def test_d142_hex(engine, alpha):
    assert d142_receipt(engine, alpha) == expected("d142", f"{engine}:{alpha}")


def test_battery_adapter_preserves_intrinsic_oxygen_notice():
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _internal_analytical_vapor_pressure_adapter, _new_internal_analytical_core,
        PO2_COMMANDED, Po2Request,
    )
    temperature, pressure, composition = _hashimoto_start()
    result = _internal_analytical_vapor_pressure_adapter(
        core=_new_internal_analytical_core(), temperature_C=temperature - 273.15,
        pressure_bar=pressure, composition_kg=composition, composition_mol=None,
        fO2_log=-9.0, po2_request=Po2Request(mode=PO2_COMMANDED, po2_bar=1e-5),
        include_diagnostic_shadows=False,
    )
    notice = 'no gas-side closure; published intrinsic melt oxygen potential used'
    assert notice in result.warnings
    assert notice in result.diagnostics['vapor_pressure_diagnostic']['kernel_vapor_pressure_warnings']


@pytest.mark.parametrize("material", ["Al2O3", "CaO", "MgO", "SiO2", "TiO2"])
@pytest.mark.parametrize("mode", ["self_buffered", "imposed"])
def test_refractory_hex(material, mode):
    assert refractory_receipt(material, mode) == expected("refractory", f"{material}:{mode}")


def test_source_stoichiometry_hex():
    assert source_receipt() == expected("source", "stoichiometry")


@pytest.mark.parametrize("mode", ["fixed_P", "fixed_V"])
def test_co2_hex(mode):
    assert co2_receipt(mode) == expected("co2", mode)


def test_no_co2_ar_exact_zero():
    result = co2_dissociation_partials_bar(
        1800.0, 0.0, 0.0, total_pressure_bar=0.006, p_inert_initial_bar=0.006,
    )
    assert hex_tree(result) == expected("co2", "Ar")
    assert result["p_O2_bar"] == result["p_CO_bar"] == result["p_CO2_bar"] == 0.0


@pytest.mark.parametrize("external", [0.0, 0.25, 1.0])
def test_external_origin_hex(external):
    assert partition_receipt(external) == expected("partition", str(external))


@pytest.mark.parametrize("capacity", [0.0, 2.0864e-320])
def test_redox_no_capacity_hex(monkeypatch, capacity):
    assert redox_receipt(monkeypatch, capacity) == expected("redox", str(capacity))


@pytest.mark.parametrize("species", ["SiO", "Fe", "Na", "K", "Mg", "AlO", "CrO2"])
def test_grounded_alpha_hex(species):
    assert hex_tree(grounded_alpha(species, 1700.0)) == expected("alpha", species)


@pytest.mark.parametrize("key", ["r5:mars_basalt", "r5:lunar_mare_low_ti",
                                 "green:mars_basalt", "green:lunar_mare_low_ti"])
def test_frozen_source_hex(key):
    # C7 owner ruling: r5 Mars moves gas-face/Stefan diagnostics; c7-chunk2-r2.md,
    # "Final regression and revert checks", delta-frozen-pin-summary.json.
    # C7 owner ruling: r5 lunar moves the same signed-series diagnostics (same receipt).
    # C7 owner ruling: green Mars moves the same signed-series diagnostics (same receipt).
    # C7 owner ruling: green lunar moves the same signed-series diagnostics (same receipt).
    row = json.loads(FROZEN.read_text())[key]
    assert frozen_receipt(row) == expected("frozen", key)
