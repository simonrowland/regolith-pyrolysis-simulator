import importlib

import pytest

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.accounting import AccountingError
from simulator.equilibrium import EquilibriumMixin
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult, InternalAnalyticalBackend
from simulator.state import MOLAR_MASS


def _required_attr(module_name, attr_name):
    module = importlib.import_module(module_name)
    assert hasattr(module, attr_name), (
        f"{module_name}.{attr_name} is required by the backend adapter "
        "contract"
    )
    return getattr(module, attr_name)


class RecordingInternalAnalyticalBackend(InternalAnalyticalBackend):
    def __init__(self):
        self.calls = []

    def initialize(self, config):
        return True

    def is_available(self):
        return True

    def equilibrate(
        self, temperature_C, composition_mol, fO2_log=-9.0, pressure_bar=1e-6,
        species_formula_registry=None,
    ):
        self.calls.append(
            {
                "temperature_C": temperature_C,
                "composition_mol": dict(composition_mol),
                "fO2_log": fO2_log,
                "pressure_bar": pressure_bar,
            }
        )
        composition_mol["SiO2"] = 0.0
        composition_mol["FeO"] = 1000.0
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            liquid_fraction=1.0,
            liquid_composition_wt_pct={"SiO2": 0.0, "FeO": 100.0},
        )

    def get_vapor_species(self):
        return []


class AtomDeltaBackend(RecordingInternalAnalyticalBackend):
    def __init__(self, MaterialLot, LedgerTransition):
        super().__init__()
        self.MaterialLot = MaterialLot
        self.LedgerTransition = LedgerTransition

    def equilibrate(
        self, temperature_C, composition_mol, fO2_log=-9.0, pressure_bar=1e-6,
        species_formula_registry=None,
    ):
        result = super().equilibrate(
            temperature_C,
            composition_mol,
            fO2_log=fO2_log,
            pressure_bar=pressure_bar,
        )
        result.ledger_transition = self.LedgerTransition(
            name="factsage_equilibrium_phase_update",
            debits=(
                self.MaterialLot(
                    "process.cleaned_melt",
                    {"FeO": MOLAR_MASS["FeO"]},
                    source="backend atom delta",
                ),
            ),
            credits=(
                self.MaterialLot(
                    "process.metal_phase",
                    {"Fe": MOLAR_MASS["Fe"]},
                    source="backend atom delta",
                ),
                self.MaterialLot(
                    "process.overhead_gas",
                    {"O2": 0.5 * MOLAR_MASS["O2"]},
                    source="backend atom delta",
                ),
            ),
            reason="backend result supplies explicit atom delta",
        )
        return result


class TerminalizingBackend(AtomDeltaBackend):
    def equilibrate(
        self, temperature_C, composition_mol, fO2_log=-9.0, pressure_bar=1e-6,
        species_formula_registry=None,
    ):
        result = super().equilibrate(
            temperature_C,
            composition_mol,
            fO2_log=fO2_log,
            pressure_bar=pressure_bar,
        )
        result.ledger_transition = self.LedgerTransition(
            name="factsage_equilibrium_phase_update",
            debits=(
                self.MaterialLot(
                    "process.cleaned_melt",
                    {"FeO": MOLAR_MASS["FeO"]},
                    source="backend atom delta",
                ),
            ),
            credits=(
                self.MaterialLot(
                    "terminal.drain_tap_material",
                    {"Fe": MOLAR_MASS["Fe"]},
                    source="backend atom delta",
                ),
                self.MaterialLot(
                    "terminal.oxygen_melt_offgas_stored",
                    {"O2": 0.5 * MOLAR_MASS["O2"]},
                    source="backend atom delta",
                ),
            ),
            reason="backend result illegally terminalizes material",
        )
        return result


def _sim(backend):
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Backend adapter contract",
                "composition_wt_pct": {"SiO2": 60.0, "FeO": 40.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )


def test_internal_analytical_backend_receives_mol_kernel_payload():
    backend = RecordingInternalAnalyticalBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)

    assert hasattr(sim, "atom_ledger"), (
        "backend mol payload must derive from sim.atom_ledger"
    )
    sim._get_equilibrium()

    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["temperature_C"] == pytest.approx(25.0)
    assert call["composition_mol"]["SiO2"] == pytest.approx(
        600.0 / (MOLAR_MASS["SiO2"] / 1000.0))
    assert call["composition_mol"]["FeO"] == pytest.approx(
        400.0 / (MOLAR_MASS["FeO"] / 1000.0))
    assert call["fO2_log"] == pytest.approx(sim.melt.fO2_log)
    assert call["pressure_bar"] == pytest.approx(0.0)
    assert sim.melt.composition_kg["SiO2"] == pytest.approx(600.0)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(400.0)


def test_backend_payload_ignores_stale_mutated_meltstate_kg():
    backend = RecordingInternalAnalyticalBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.melt.composition_kg["SiO2"] = 9999.0
    sim.melt.composition_kg["FeO"] = 1.0

    sim._get_equilibrium()

    call = backend.calls[0]
    assert call["composition_mol"]["SiO2"] == pytest.approx(
        600.0 / (MOLAR_MASS["SiO2"] / 1000.0))
    assert call["composition_mol"]["FeO"] == pytest.approx(
        400.0 / (MOLAR_MASS["FeO"] / 1000.0))


def test_backend_result_applies_as_atom_delta():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    LedgerTransition = _required_attr("simulator.accounting", "LedgerTransition")
    backend = AtomDeltaBackend(MaterialLot, LedgerTransition)
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)

    sim.step()

    sim.atom_ledger.assert_balanced()
    expected_feo_remaining_kg = 400.0 - MOLAR_MASS["FeO"]
    expected_o2_kg = 0.5 * MOLAR_MASS["O2"]
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "FeO"
    ] == pytest.approx(expected_feo_remaining_kg)
    assert sim.atom_ledger.kg_by_account("process.metal_phase")[
        "Fe"
    ] == pytest.approx(MOLAR_MASS["Fe"])
    oxygen_partition = sim._oxygen_terminal_partition_kg()
    assert oxygen_partition["total"] == pytest.approx(expected_o2_kg)
    assert (
        oxygen_partition["stored"] + oxygen_partition["vented"]
    ) == pytest.approx(expected_o2_kg)
    assert sim.melt.composition_kg["SiO2"] == pytest.approx(600.0)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(
        expected_feo_remaining_kg
    )
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_backend_validated_transition_observes_reagent_provenance():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    LedgerTransition = _required_attr("simulator.accounting", "LedgerTransition")
    backend = AtomDeltaBackend(MaterialLot, LedgerTransition)
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    cleaned_melt_fe_kg = sim._account_element_kg(
        sim.atom_ledger.kg_by_account("process.cleaned_melt"),
        "Fe",
    )
    sim._non_feedstock_reagent_element_kg_by_account = {
        "process.cleaned_melt": {"Fe": cleaned_melt_fe_kg},
    }

    sim.step()

    provenance = sim._non_feedstock_reagent_element_kg_by_account
    assert provenance["process.metal_phase"]["Fe"] == pytest.approx(
        MOLAR_MASS["Fe"]
    )
    assert provenance["process.cleaned_melt"]["Fe"] == pytest.approx(
        cleaned_melt_fe_kg - MOLAR_MASS["Fe"]
    )


def test_backend_result_cannot_credit_terminal_accounts():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    LedgerTransition = _required_attr("simulator.accounting", "LedgerTransition")
    backend = TerminalizingBackend(MaterialLot, LedgerTransition)
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)

    with pytest.raises(AccountingError, match="may only touch"):
        sim._get_equilibrium()


def test_backend_composition_mol_uses_empty_ledger_not_stale_melt_kg():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    backend = RecordingInternalAnalyticalBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")

    sim.atom_ledger.transfer(
        "empty_cleaned_melt_for_backend",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                ledger_melt,
                source="backend adapter test",
            ),
        ),
        credits=(
            MaterialLot(
                "terminal.slag",
                ledger_melt,
                source="backend adapter test",
            ),
        ),
        reason="cleaned melt is legitimately empty",
    )
    sim.melt.composition_kg["SiO2"] = 600.0
    sim.melt.composition_kg["FeO"] = 400.0

    composition = sim._backend_composition_mol()

    assert composition == {}


def test_backend_composition_mol_preserves_noncanonical_ledger_species():
    backend = RecordingInternalAnalyticalBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"SO3": 1.0},
        source="backend adapter noncanonical species",
    )

    composition = sim._backend_composition_mol()

    assert composition["SO3"] == pytest.approx(
        sim.atom_ledger.mol_by_account("process.cleaned_melt")["SO3"]
    )


def test_backend_composition_mol_includes_spent_reductant_residue():
    backend = RecordingInternalAnalyticalBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    before = sim._backend_composition_mol().get("Na2O", 0.0)
    sim.atom_ledger.load_external(
        SPENT_REDUCTANT_RESIDUE_ACCOUNT,
        {"Na2O": 1.0},
        source="backend adapter spent reductant residue",
    )

    composition = sim._backend_composition_mol()

    assert composition["Na2O"] == pytest.approx(
        before + sim.atom_ledger.mol_by_account(SPENT_REDUCTANT_RESIDUE_ACCOUNT)["Na2O"]
    )


def test_equilibrium_mixin_backend_path_is_disabled():
    with pytest.raises(NotImplementedError, match="AtomLedger"):
        EquilibriumMixin()._get_equilibrium()
