"""Furnace Langmuir Fe parent demand uses the ledger ferric fraction.

The expected count 1.05 at q = 0.1 is the brief's independent value
(1 O per Fe at q = 0, plus half the ferric fraction). The production
resolver ``iron_mean_oxygen_per_atom`` must agree with that count.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.evaporation import EvaporationMixin
from simulator.langmuir_surface_oxygen import apply_langmuir_surface_oxygen
from simulator.vacuum_oxygen_balance import iron_mean_oxygen_per_atom

# Inside the Fe evaporation-coefficient band used by the Langmuir pins.
_T_K = 2173.15
_P_REF_BAR = 1.0e-6
_FE_PROVENANCE = {
    "Fe": {
        "pressure_rail": "gas_fugacity",
        "pO2_bar": _P_REF_BAR,
    }
}


def _close(*, q: float | None, species: dict[str, float] | None = None):
    kwargs = {}
    if q is not None:
        kwargs["fe3_over_sum_fe"] = q
    return apply_langmuir_surface_oxygen(
        species or {"Fe": 1.0},
        temperature_K=_T_K,
        provenance_by_species=_FE_PROVENANCE,
        source_reaction_fO2_bar=_P_REF_BAR,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
        **kwargs,
    )


def test_furnace_fe_demand_at_q_0_1_is_1_05_not_1() -> None:
    """Open-gate elemental Fe at ledger q = 0.1 books 1.05 O, not 1."""

    result = _close(q=0.1)
    assert result.applied is True
    assert result.surface_pO2_bar is not None
    assert result.refused_species == {}
    demand = result.parent_oxygen_demand_by_species["Fe"]
    assert demand == pytest.approx(1.05)
    assert demand != pytest.approx(1.0)
    assert demand == pytest.approx(iron_mean_oxygen_per_atom(0.1))
    assert "parent_oxygen_demand_by_species" not in result.as_diagnostic()


def test_furnace_fe_demand_at_q_0_stays_one_and_higher_q_raises_surface_po2() -> None:
    """q = 0 is the FeO limit. A higher q books more O2 and lifts pO2."""

    at_zero = _close(q=0.0)
    at_tenth = _close(q=0.1)
    assert at_zero.parent_oxygen_demand_by_species["Fe"] == pytest.approx(1.0)
    assert at_zero.parent_oxygen_demand_by_species["Fe"] == pytest.approx(
        iron_mean_oxygen_per_atom(0.0)
    )
    assert at_zero.surface_pO2_bar is not None
    assert at_tenth.surface_pO2_bar is not None
    assert at_tenth.surface_pO2_bar > at_zero.surface_pO2_bar


def test_omitted_ledger_q_keeps_the_fixed_activity_fe_demand() -> None:
    """Callers that do not pass q still invert the FeO exponent."""

    result = _close(q=None)
    assert result.parent_oxygen_demand_by_species["Fe"] == pytest.approx(1.0)


def test_fixed_valence_calcium_keeps_one_oxygen_beside_ferric_iron() -> None:
    """Ca stays on the exponent inversion when Fe uses the ledger q."""

    closed = apply_langmuir_surface_oxygen(
        {"Fe": 1.0, "Ca": 1.0},
        temperature_K=_T_K,
        provenance_by_species={
            "Fe": {
                "pressure_rail": "gas_fugacity",
                "pO2_bar": _P_REF_BAR,
            },
            "Ca": {
                "pressure_rail": "gas_fugacity",
                "pO2_bar": _P_REF_BAR,
            },
        },
        source_reaction_fO2_bar=_P_REF_BAR,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
        fe3_over_sum_fe=0.1,
    )
    assert closed.refused_species == {}
    assert closed.parent_oxygen_demand_by_species["Fe"] == pytest.approx(1.05)
    assert closed.parent_oxygen_demand_by_species["Ca"] == pytest.approx(1.0)


def test_evaporation_caller_reads_ledger_q_for_the_furnace_closure() -> None:
    """The mixin passes ledger q. Forgetting that leaves Fe demand at 1."""

    host = SimpleNamespace(
        setpoints={
            "chemistry_kernel": {
                "evaporation_series_resistance": {
                    "gas_resistance_enabled": False,
                }
            }
        },
        vapor_pressures={},
        atom_ledger=SimpleNamespace(
            project_account_mol=lambda account: (
                {"FeO": 0.9, "Fe2O3": 0.05}
                if account == "process.cleaned_melt"
                else {}
            )
        ),
    )
    result = EvaporationMixin._closed_langmuir_pressures_for_partials(
        host,
        {"Fe": 1.0},
        temperature_K=_T_K,
        overhead_partials_Pa={},
        provenance_by_species=_FE_PROVENANCE,
        source_reaction_fO2_bar=_P_REF_BAR,
        transport_pO2_bar=None,
        melt_redox_capacity_mol_per_ln_fO2=0.0,
        overhead_pressure_Pa=0.0,
    )
    assert result.applied is True
    assert result.surface_pO2_bar is not None
    assert result.refused_species == {}
    demand = result.parent_oxygen_demand_by_species["Fe"]
    assert demand == pytest.approx(1.05)
    assert demand != pytest.approx(1.0)
