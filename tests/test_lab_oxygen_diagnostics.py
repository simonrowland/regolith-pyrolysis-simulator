from types import SimpleNamespace

import pytest

from simulator.accounting import AccountingQueries, AtomLedger
from simulator.runner import RunnerError, build_sio_yield_report


OXYGEN_CLOSURE_MAX_PCT = 5e-12


def _diagnostic_sim():
    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"SiO2": 10.0},
        source="test residual oxygen",
    )
    ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 1.0, "SiO": 2.0},
        source="test overhead oxygen",
    )
    ledger.load_external_mol(
        "terminal.offgas",
        {"O": 0.25, "CO": 0.5},
        source="test terminal offgas oxygen",
    )
    ledger.load_external_mol(
        "process.condensation_train",
        {"SiO2": 3.0, "O2": 0.1},
        source="test condensation oxygen",
    )
    ledger.load_external_mol(
        "process.wall_deposit_segment_holder",
        {"FeO": 4.0, "MgO": 5.0},
        source="test holder oxygen",
    )
    ledger.load_external_mol(
        "process.wall_deposit_segment_window",
        {"SiO2": 1.5},
        source="test window oxygen",
    )
    return SimpleNamespace(atom_ledger=ledger)


def test_lab_oxygen_atom_partition_closes_with_explicit_residual():
    partition = AccountingQueries(_diagnostic_sim()).lab_oxygen_atom_partition()

    allocated_plus_residual = (
        partition["free_analyzer_visible"]["oxygen_atom_mol"]
        + partition["overhead_vapor_bound"]["oxygen_atom_mol"]
        + partition["condensation_train"]["oxygen_atom_mol"]
        + partition["wall_deposit_segment_by_surface"][
            "total_oxygen_atom_mol"
        ]
        + partition["residual_unallocated_oxygen_atom_mol"]
    )

    assert allocated_plus_residual == pytest.approx(
        partition["total_oxygen_atom_mol"]
    )
    assert partition["free_analyzer_visible"]["oxygen_atom_mol"] == (
        pytest.approx(2.25)
    )
    assert partition["free_analyzer_visible"]["species_mol"] == {
        "O": pytest.approx(0.25),
        "O2": pytest.approx(1.0),
    }
    assert partition["overhead_vapor_bound"]["oxygen_atom_mol"] == (
        pytest.approx(2.5)
    )
    assert partition["overhead_vapor_bound"][
        "species_oxygen_atom_mol"
    ] == {
        "CO": pytest.approx(0.5),
        "SiO": pytest.approx(2.0),
    }
    assert partition["condensation_train"]["oxygen_atom_mol"] == (
        pytest.approx(6.2)
    )
    assert partition["condensation_train"]["species_oxygen_atom_mol"] == {
        "O2": pytest.approx(0.2),
        "SiO2": pytest.approx(6.0),
    }
    assert partition["closure"]["error_pct"] <= OXYGEN_CLOSURE_MAX_PCT
    assert partition["residual_unallocated_oxygen_atom_mol"] == pytest.approx(
        20.0
    )


def test_lab_oxygen_atom_partition_reports_wall_deposit_by_surface():
    partition = AccountingQueries(_diagnostic_sim()).lab_oxygen_atom_partition()
    surfaces = partition["wall_deposit_segment_by_surface"]["surfaces"]

    assert set(surfaces) == {"holder", "window"}
    assert surfaces["holder"]["account"] == (
        "process.wall_deposit_segment_holder"
    )
    assert surfaces["holder"]["oxygen_atom_mol"] == pytest.approx(9.0)
    assert surfaces["holder"]["species_oxygen_atom_mol"] == {
        "FeO": pytest.approx(4.0),
        "MgO": pytest.approx(5.0),
    }
    assert surfaces["window"]["oxygen_atom_mol"] == pytest.approx(3.0)


def test_sio_yield_report_lab_oxygen_diagnostics_disabled_keeps_report_shape():
    report = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=False,
        allow_unmeasured_alpha_fallback=True,
    )

    assert set(report) == {
        "feedstock_id",
        "campaign",
        "alpha_SiO",
        "alpha_provenance",
        "sio_evolved_kg",
        "sio_to_silica_fume_kg",
        "wall_deposit_kg",
        "fouling_rate",
        "sio_yield_pct_of_feedstock",
        "industrial_benchmark_pct",
        "verdict",
    }
    assert "lab_oxygen_atom_partition" not in report


def test_sio_yield_report_lab_oxygen_sidecar_is_explicit_opt_in():
    with pytest.raises(RunnerError):
        build_sio_yield_report(
            feedstock_id="lunar_mare_low_ti",
            hours=1,
            include_lab_oxygen_diagnostics=True,
            allow_unmeasured_alpha_fallback=True,
        )

    _, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=True,
        include_lab_oxygen_diagnostics=True,
        allow_unmeasured_alpha_fallback=True,
    )

    partition = diagnostics["lab_oxygen_atom_partition"]
    assert partition["closure"]["error_pct"] <= OXYGEN_CLOSURE_MAX_PCT
    assert "residual_unallocated_oxygen_atom_mol" in partition
