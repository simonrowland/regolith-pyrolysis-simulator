"""F2 root B: formula resolve refuses; never zero-and-renormalize."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from engines.alphamelts.thermoengine import ThermoEngineTransport
from simulator.accounting.exceptions import AccountingError
from simulator.accounting import stage0_inventory as s0
import simulator.optimize.evaluate as ev


def test_thermoengine_oxide_mol_refuses_unresolvable() -> None:
    with pytest.raises(ValueError, match="cannot prove molar mass for oxide"):
        ThermoEngineTransport._oxide_mol(None, "NotAnOxideZZZ", 10.0)


def test_thermoengine_endmember_refuses_unresolvable() -> None:
    with pytest.raises(ValueError, match="cannot prove molar mass for endmember"):
        ThermoEngineTransport._endmember_moles_from_wt(
            None, {"BogusEndmember": 5.0}, ("BogusEndmember",)
        )


def test_stage0_species_mol_refuses_positive_kg_on_mass_fault() -> None:
    class _Bad:
        def molar_mass_kg_per_mol(self):
            raise RuntimeError("registry fault")

    with patch.object(s0, "_formula", return_value=_Bad()):
        with pytest.raises(AccountingError, match="molar mass unavailable"):
            s0._species_mol("SiO2", 1.0, None)


def test_optimize_composition_refuses_whole_map_on_hole() -> None:
    run = SimpleNamespace(simulator=SimpleNamespace(species_formula_registry={}))
    assert ev._composition_wt_pct_to_mol({"SiO2": 60.0, "BogusXYZ": 40.0}, run) == {}
    healthy = ev._composition_wt_pct_to_mol({"SiO2": 60.0, "MgO": 40.0}, run)
    assert set(healthy) == {"SiO2", "MgO"}
    assert all(v > 0.0 for v in healthy.values())


def test_mutation_proof_optimize_old_omit_keeps_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    from simulator.accounting.formulas import resolve_species_formula
    import math

    def _old(values, run_execution):
        sim = getattr(run_execution, "simulator", None)
        registry = dict(getattr(sim, "species_formula_registry", {}) or {})
        result = {}
        for species, raw_mass in values.items():
            mass_basis = float(raw_mass)
            if mass_basis <= 0.0:
                continue
            try:
                formula = resolve_species_formula(str(species), registry)
            except Exception:
                continue
            mol = mass_basis / formula.molar_mass_kg_per_mol()
            if math.isfinite(mol) and mol > 0.0:
                result[str(species)] = mol
        return result

    monkeypatch.setattr(ev, "_composition_wt_pct_to_mol", _old)
    run = SimpleNamespace(simulator=SimpleNamespace(species_formula_registry={}))
    partial = ev._composition_wt_pct_to_mol({"SiO2": 60.0, "BogusXYZ": 40.0}, run)
    assert set(partial) == {"SiO2"}
