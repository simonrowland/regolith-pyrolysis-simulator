from __future__ import annotations

import pytest

from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, _c0_char_diagnostic


def _run_c0(feedstock_id: str) -> dict:
    return PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    ).run()


def test_ci_c0_reports_refractory_char_and_warns_without_lance() -> None:
    diagnostic = _run_c0("ci_carbonaceous_chondrite")["run_metadata"][
        "c0_char_diagnostic"
    ]

    assert diagnostic["status"] == "WARN"
    assert diagnostic["warning"].startswith("WARNING:")
    assert diagnostic["partition"] == {
        "feedstock_id": "ci_carbonaceous_chondrite",
        "f_refractory_organic_C": pytest.approx(0.39),
        "fraction_basis": "floor",
        "source": "REF-024 sephton_2004_murchison_hydropyrolysis",
        "regime_caveat": "H2_pyrolysis_to_520C_not_O2_bake_to_1050C",
    }
    assert diagnostic["inventory"]["refractory_char_C_mol"] == pytest.approx(
        958.7221688514541
    )
    assert diagnostic["inventory"]["refractory_char_C_kg"] == pytest.approx(
        11.515211970074814
    )
    lance = diagnostic["lance_stoichiometry"]
    assert lance["C_plus_O2_to_CO2"]["O2_required_mol"] == pytest.approx(
        diagnostic["inventory"]["refractory_char_C_mol"]
    )
    assert lance["C_plus_half_O2_to_CO"]["O2_required_mol"] == pytest.approx(
        0.5 * diagnostic["inventory"]["refractory_char_C_mol"]
    )
    assert lance["C_plus_O2_to_CO2"]["O2_required_kg"] > 0.0
    assert lance["C_plus_half_O2_to_CO"]["O2_required_kg"] > 0.0
    assert lance["C_plus_O2_to_CO2"]["absorbed_coverage_pct"] == 0.0
    assert lance["C_plus_half_O2_to_CO"]["absorbed_coverage_pct"] == 0.0
    assert (
        lance["C_plus_O2_to_CO2"]
        ["un_lanced_char_C_mol"]
        == pytest.approx(diagnostic["inventory"]["refractory_char_C_mol"])
    )
    assert lance["C_plus_half_O2_to_CO"][
        "un_lanced_char_C_mol"
    ] == pytest.approx(diagnostic["inventory"]["refractory_char_C_mol"])
    reduction = diagnostic["FeO_reduction_potential"]
    assert reduction["FeO_reducible_mol"] == pytest.approx(
        diagnostic["inventory"]["refractory_char_C_mol"]
    )
    assert reduction["melt_FeO_fraction_at_risk"] == pytest.approx(
        0.29791075166604525
    )
    assert reduction["Fe_equivalent_kg"] == pytest.approx(53.53983951950945)
    assert reduction["CO_equivalent_mol"] == pytest.approx(
        reduction["FeO_reducible_mol"]
    )


def test_ci_c0_sufficient_lance_clears_warning_on_both_oxidation_bases() -> None:
    run = PyrolysisRun(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    )
    execution = RunExecutor().execute(run._session_config())
    execution.snapshots[0].o2_bubbler_injected_kg = 40.0
    execution.snapshots[0].o2_bubbler_absorbed_kg = 40.0
    ledger_before = execution.simulator.atom_ledger.mol_by_account()
    diagnostic = _c0_char_diagnostic(
        execution.simulator,
        execution.snapshots,
        feedstock_id=run.feedstock_id,
    )

    assert diagnostic["status"] == "OK"
    assert diagnostic["warning"] is None
    lance = diagnostic["lance_stoichiometry"]
    assert lance["O2_injected_kg"] == pytest.approx(40.0)
    assert lance["O2_absorbed_kg"] == pytest.approx(40.0)
    assert lance["C_plus_O2_to_CO2"]["injected_coverage_pct"] == 100.0
    assert lance["C_plus_O2_to_CO2"]["absorbed_coverage_pct"] == 100.0
    assert lance["C_plus_O2_to_CO2"]["un_lanced_char_C_mol"] == 0.0
    assert lance["C_plus_half_O2_to_CO"]["injected_coverage_pct"] == 100.0
    assert lance["C_plus_half_O2_to_CO"]["un_lanced_char_C_mol"] == 0.0
    assert diagnostic["FeO_reduction_potential"]["FeO_reducible_mol"] == 0.0
    assert execution.simulator.atom_ledger.mol_by_account() == ledger_before


def test_ci_c0_injected_passthrough_does_not_clear_char_warning() -> None:
    run = PyrolysisRun(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
    )
    execution = RunExecutor().execute(run._session_config())
    execution.snapshots[0].o2_bubbler_injected_kg = 40.0
    execution.snapshots[0].o2_bubbler_absorbed_kg = 0.0

    diagnostic = _c0_char_diagnostic(
        execution.simulator,
        execution.snapshots,
        feedstock_id=run.feedstock_id,
    )

    lance = diagnostic["lance_stoichiometry"]["C_plus_O2_to_CO2"]
    assert lance["injected_coverage_pct"] == 100.0
    assert lance["absorbed_coverage_pct"] == 0.0
    assert lance["injected_basis_residual_char_C_mol"] == 0.0
    assert lance["un_lanced_char_C_mol"] == pytest.approx(
        diagnostic["inventory"]["refractory_char_C_mol"]
    )
    assert diagnostic["status"] == "WARN"


def test_lunar_mare_c0_has_no_char_block_or_warning() -> None:
    payload = _run_c0("lunar_mare_low_ti")

    assert "c0_char_diagnostic" not in payload["run_metadata"]
