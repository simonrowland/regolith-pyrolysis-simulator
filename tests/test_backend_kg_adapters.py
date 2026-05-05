import importlib

import pytest

from simulator.equilibrium import EquilibriumMixin
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult, StubBackend
from simulator.state import MOLAR_MASS


def _required_attr(module_name, attr_name):
    module = importlib.import_module(module_name)
    assert hasattr(module, attr_name), (
        f"{module_name}.{attr_name} is required by the backend adapter "
        "contract"
    )
    return getattr(module, attr_name)


class RecordingStubBackend(StubBackend):
    def __init__(self):
        self.calls = []

    def initialize(self, config):
        return True

    def is_available(self):
        return True

    def equilibrate(
        self, temperature_C, composition_mol, fO2_log=-9.0, pressure_bar=1e-6
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
            liquid_composition_wt_pct={"SiO2": 0.0, "FeO": 100.0},
            phase_masses_kg={"liquid": 1.0},
        )

    def get_vapor_species(self):
        return []


class AtomDeltaBackend(RecordingStubBackend):
    def __init__(self, MaterialLot, LedgerTransition):
        super().__init__()
        self.MaterialLot = MaterialLot
        self.LedgerTransition = LedgerTransition

    def equilibrate(
        self, temperature_C, composition_mol, fO2_log=-9.0, pressure_bar=1e-6
    ):
        result = super().equilibrate(
            temperature_C,
            composition_mol,
            fO2_log=fO2_log,
            pressure_bar=pressure_bar,
        )
        result.ledger_transition = self.LedgerTransition(
            name="backend_feo_delta",
            debits=(
                self.MaterialLot(
                    "process.cleaned_melt",
                    {"FeO": 71.84},
                    source="backend atom delta",
                ),
            ),
            credits=(
                self.MaterialLot(
                    "terminal.drain_tap_material",
                    {"Fe": 55.84},
                    source="backend atom delta",
                ),
                self.MaterialLot(
                    "terminal.oxygen_melt_offgas_stored",
                    {"O2": 16.0},
                    source="backend atom delta",
                ),
            ),
            reason="backend result supplies explicit atom delta",
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


def test_stub_backend_receives_mol_kernel_payload():
    backend = RecordingStubBackend()
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
    backend = RecordingStubBackend()
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
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "FeO"
    ] == pytest.approx(328.16)
    assert sim.atom_ledger.kg_by_account("terminal.drain_tap_material")[
        "Fe"
    ] == pytest.approx(55.84)
    oxygen_partition = sim._oxygen_terminal_partition_kg()
    assert oxygen_partition["total"] == pytest.approx(16.0)
    assert (
        oxygen_partition["stored"] + oxygen_partition["vented"]
    ) == pytest.approx(16.0)
    assert sim.melt.composition_kg["SiO2"] == pytest.approx(600.0)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(328.16)
    assert sim._make_snapshot().mass_balance_error_pct == pytest.approx(0.0)


def test_backend_composition_uses_empty_ledger_not_stale_melt_kg():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    backend = RecordingStubBackend()
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

    composition = sim._backend_composition_kg()

    assert composition == {}


def test_backend_composition_preserves_noncanonical_ledger_species():
    backend = RecordingStubBackend()
    sim = _sim(backend)
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "process.cleaned_melt",
        {"SO3": 1.0},
        source="backend adapter noncanonical species",
    )

    composition = sim._backend_composition_kg()

    assert composition["SO3"] == pytest.approx(1.0)


def test_equilibrium_mixin_backend_path_is_disabled():
    with pytest.raises(NotImplementedError, match="AtomLedger"):
        EquilibriumMixin()._get_equilibrium()
