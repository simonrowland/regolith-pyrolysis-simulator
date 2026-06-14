"""R0 by-group Stage-0 foulant reporting tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.queries import AccountingQueries
from simulator.backends import StubBackend
from simulator.core import PyrolysisSimulator
from simulator.stage0_foulant_report_markdown import (
    format_stage0_foulant_report_markdown,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class _LedgerStub:
    registry: dict[str, Any] = {}

    def mol_by_account(self) -> dict:
        return {}


def _sim_with_diagnostics(*diagnostics: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        atom_ledger=_LedgerStub(),
        _stage0_foulant_diagnostics=list(diagnostics),
    )


def _load_batch_sim(
    feedstock_key: str,
    *,
    vapor_pressure_data: dict,
    feedstocks_data: dict,
    setpoints_data: dict,
    diagnostics_enabled: bool,
) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints_data,
        feedstocks_data,
        vapor_pressure_data,
    )
    sim._foulant_diagnostics_enabled = diagnostics_enabled
    sim.load_batch(feedstock_key, mass_kg=1000.0)
    return sim


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_stage0_foulant_partition_mass_closure_raises_on_gap():
    sim = _sim_with_diagnostics({
        "reaction_family": "volatilization",
        "carrier": "NaCl",
        "feed_kg": 1.0,
        "cumulative_escaped_frac": 0.2,
        "cumulative_retained_frac": 0.7,
        "wall_deposit_frac": 0.0,
    })

    with pytest.raises(AccountingError, match="mass does not close"):
        AccountingQueries(sim).stage0_foulant_partition_by_group()


def test_stage0_foulant_reporting_read_only_and_golden_neutral(
):
    vapor_pressure_data = _load_yaml("vapor_pressures.yaml")
    feedstocks_data = _load_yaml("feedstocks.yaml")
    setpoints_data = _load_yaml("setpoints.yaml")
    sim_on = _load_batch_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
        diagnostics_enabled=True,
    )
    sim_off = _load_batch_sim(
        "ci_carbonaceous_chondrite",
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
        diagnostics_enabled=False,
    )
    before = sim_on.atom_ledger.mol_by_account()

    partition = AccountingQueries(sim_on).stage0_foulant_partition_by_group()
    format_stage0_foulant_report_markdown(partition)

    assert sim_on.atom_ledger.mol_by_account() == before
    assert sim_on.atom_ledger.mol_by_account() == sim_off.atom_ledger.mol_by_account()


def test_stage0_foulant_renderer_prints_provenance_and_clear_steps():
    partition = {
        "trapped_gasses": {
            "escaped_kg": 0.0,
            "retained_kg": 0.0,
            "wall_deposit_kg": 0.0,
            "rump_kg": 0.0,
            "burned_kg": 1.0,
            "residual_interval": None,
        },
        "refractory_carbon": {
            "escaped_kg": 0.0,
            "retained_kg": 0.5,
            "wall_deposit_kg": 0.0,
            "rump_kg": 0.0,
            "burned_kg": 0.0,
            "residual_interval": {"low_kg": 0.0, "high_kg": 0.5},
        },
        "other_mineral_contaminant": {
            "escaped_kg": 0.1,
            "retained_kg": 0.0,
            "wall_deposit_kg": 0.0,
            "rump_kg": 0.0,
            "burned_kg": 0.0,
            "residual_interval": None,
        },
    }
    verdicts = {
        "verdict_a": {
            "flags": [
                {
                    "property": "liquidus",
                    "level": "INFO",
                    "metric": "delta_T_frac_of_T_in_C",
                    "grounded": True,
                    "correctable": True,
                    "noise_floor_status": "proposed",
                    "contaminant": "NaCl",
                    "perturbation_before": 0.1,
                    "perturbation_after": 0.0,
                },
                {
                    "property": "redox",
                    "level": "NOTICE",
                    "metric": "delta_log10_fO2",
                    "grounded": False,
                    "correctable": False,
                    "noise_floor_status": "noise_floor_ungrounded",
                    "contaminant": "C",
                    "perturbation_before": 0.4,
                    "perturbation_after": 0.2,
                },
                {
                    "property": "bulk_sum_closure",
                    "level": "WARNING",
                    "metric": "dropped_component_mass_fraction",
                    "grounded": True,
                    "correctable": False,
                    "noise_floor_status": "proposed",
                    "contaminant": "MAGEMin",
                    "perturbation_before": 0.03,
                    "perturbation_after": 0.0,
                },
            ],
            "step_resolved": [
                {
                    "hour": 2,
                    "flags": [
                        {
                            "property": "liquidus",
                            "level": "INFO",
                            "metric": "delta_T_frac_of_T_in_C",
                            "grounded": True,
                            "correctable": True,
                            "noise_floor_status": "proposed",
                            "cleared": True,
                            "clear_hour": 2,
                        }
                    ],
                }
            ],
        },
        "verdict_b": {
            "backend_status": "ok",
            "layer_a_state": "in_domain",
            "stripped_domain_valid": True,
            "hard_gate_failed": False,
        },
    }

    rendered = format_stage0_foulant_report_markdown(partition, verdicts=verdicts)

    assert "rung=INFO" in rendered
    assert "rung=NOTICE" in rendered
    assert "rung=WARNING" in rendered
    assert "grounded=true" in rendered
    assert "correctable=false" in rendered
    assert "provenance=noise_floor_ungrounded" in rendered
    assert "liquidus CLEAR" in rendered
    assert "clear_hour=2" in rendered
    assert "(no species above the noise floor)" not in rendered


def test_stage0_foulant_partition_splits_carbon_across_three_groups():
    sim = _sim_with_diagnostics({
        "reaction_family": "partition_carbon",
        "carrier": "carbonaceous_organic",
        "feed_kg": 30.0,
        "declared_c_mol": 30.0,
        "labile_mol": 10.0,
        "refractory_mol": 5.0,
        "carbonate_mol": 15.0,
        "labile_extent": 1.0,
        "refractory_interval": {
            "low": 0.2,
            "high": 1.0,
            "reason": "UNGROUNDABLE_PROCESS_EXTENT",
        },
    })

    partition = AccountingQueries(sim).stage0_foulant_partition_by_group()

    assert partition["trapped_gasses"]["burned_kg"] == pytest.approx(10.0)
    assert partition["refractory_carbon"]["retained_kg"] == pytest.approx(5.0)
    assert partition["other_mineral_contaminant"]["rump_kg"] == pytest.approx(15.0)


def test_stage0_foulant_group_totals_match_reaction_family_axis():
    sim = _sim_with_diagnostics(
        {
            "reaction_family": "volatilization",
            "carrier": "NaCl",
            "feed_kg": 2.0,
            "cumulative_escaped_frac": 0.25,
            "cumulative_retained_frac": 0.75,
            "wall_deposit_frac": 0.25,
        },
        {
            "reaction_family": "partition_carbon",
            "carrier": "carbonaceous_organic",
            "feed_kg": 3.0,
            "declared_c_mol": 3.0,
            "labile_mol": 1.0,
            "refractory_mol": 1.0,
            "carbonate_mol": 1.0,
            "labile_extent": 1.0,
            "refractory_interval": {"low": 1.0, "high": 1.0},
        },
    )

    partition = AccountingQueries(sim).stage0_foulant_partition_by_group()

    for payload in partition.values():
        family_total = sum(payload["reaction_family_totals_kg"].values())
        assert family_total == pytest.approx(payload["closure"]["source_debited_kg"])
        assert payload["closure"]["error_kg"] == pytest.approx(0.0)


def test_stage0_foulant_hourly_by_group_reads_snapshot_delta_accounts():
    registry = SimpleNamespace(
        alias_to_carrier={"H2O": "H2O", "h2o": "H2O"},
        carriers={"H2O": SimpleNamespace(group="trapped_gasses")},
    )
    sim = SimpleNamespace(
        atom_ledger=_LedgerStub(),
        _stage0_foulant_diagnostics=[],
        _load_foulant_registry_cached=lambda: registry,
    )
    snapshot = SimpleNamespace(
        evap_flux=SimpleNamespace(species_kg_hr={"H2O": 0.25}),
        wall_deposit_by_segment_species_delta={("cold_wall", "H2O"): 0.1},
    )

    hourly = AccountingQueries(sim).stage0_foulant_hourly_by_group(snapshot)

    assert hourly["trapped_gasses"]["escaped_kg"] == pytest.approx(0.25)
    assert hourly["trapped_gasses"]["wall_deposit_kg"] == pytest.approx(0.1)
