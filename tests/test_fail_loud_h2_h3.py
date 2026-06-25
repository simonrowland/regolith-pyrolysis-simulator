import json
import math
import warnings
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    BuiltinVaporPressureProvider,
    HighUncertaintyVaporPressureFallbackWarning,
    VaporPressureComputationError,
)
from simulator.accounting import (
    AccountingError,
    AtomLedger,
    LedgerTransition,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.runner import (
    EngineBugAbort,
    _latest_mass_balance_pct,
    _sio_tsweep_row,
    _sio_wall_sweep_row,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _minimal_sio_report() -> dict[str, object]:
    return {
        "sio_yield_pct_of_feedstock": 1.25,
        "sio_to_silica_fume_kg": {},
        "wall_deposit_kg": {},
        "sio_evolved_kg": 0.0,
    }


def test_sio_yield_diagnostics_require_snapshot_mass_balance_key() -> None:
    with pytest.raises(EngineBugAbort, match="mass_balance_key_missing_in_snapshot"):
        _latest_mass_balance_pct({"per_hour_summary": [{"T_C": 1400.0}]})


def test_sio_yield_diagnostics_reject_non_numeric_mass_balance_value() -> None:
    # runner.py:741 — a present-but-non-numeric balance must fail loud, not be
    # coerced or silently skipped.
    with pytest.raises(
        EngineBugAbort, match="mass_balance_key_non_numeric_in_snapshot"
    ):
        _latest_mass_balance_pct({"per_hour_summary": [{"mass_balance_pct": "abc"}]})


def test_sio_yield_diagnostics_reject_nonfinite_mass_balance_value() -> None:
    # runner.py:747 — a present, numeric, but non-finite balance (inf/nan) is a
    # corrupt diagnostic, not a real 0%/perfect close.
    with pytest.raises(
        EngineBugAbort, match="mass_balance_key_nonfinite_in_snapshot"
    ):
        _latest_mass_balance_pct(
            {"per_hour_summary": [{"mass_balance_pct": math.inf}]}
        )


@pytest.mark.parametrize(
    "result",
    [
        {"per_hour_summary": {"mass_balance_pct": 0.0}},
        {"per_hour_summary": [None]},
    ],
)
def test_sio_yield_diagnostics_name_malformed_mass_balance_snapshots(
    result: dict[str, object],
) -> None:
    with pytest.raises(EngineBugAbort, match="mass_balance_snapshot_malformed"):
        _latest_mass_balance_pct(result)


def test_sio_sweep_rows_require_diagnostic_mass_balance_key() -> None:
    with pytest.raises(EngineBugAbort, match="mass_balance_key_missing_in_snapshot"):
        _sio_tsweep_row(
            cell_id="cell",
            t_low_c=1100.0,
            t_hold_c=1500.0,
            ramp_c_per_hr=10.0,
            report=_minimal_sio_report(),
            diagnostics={},
            mass_kg=1000.0,
        )

    with pytest.raises(EngineBugAbort, match="mass_balance_key_missing_in_snapshot"):
        _sio_wall_sweep_row(
            cell_id="cell",
            feedstock_id="lunar_mare_low_ti",
            pO2_mode="no_suppress",
            pO2_mbar=None,
            liner_temperature_c=1500.0,
            report=_minimal_sio_report(),
            diagnostics={},
        )


def test_ledger_reads_project_and_assert_balanced_reject_nonfinite_balances() -> None:
    ledger = AtomLedger()
    ledger._balances["process.cleaned_melt"] = {"SiO2": math.nan}
    transition = LedgerTransition(name="noop", debits=(), credits=())

    with pytest.raises(AccountingError, match="ledger_balance_nonfinite"):
        ledger.mol_by_account("process.cleaned_melt")
    with pytest.raises(AccountingError, match="ledger_balance_nonfinite"):
        ledger.project(transition)
    with pytest.raises(AccountingError, match="ledger_balance_nonfinite"):
        ledger.assert_balanced()


def _vapor_request(
    *,
    temperature_c: float,
    pO2_bar: float,
    accounts: dict[str, dict[str, float]] | None = None,
) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts=accounts or {"process.cleaned_melt": {"SiO2": 1000.0}},
            species_formula_registry={},
        ),
        temperature_C=temperature_c,
        pressure_bar=1.0,
        control_inputs={"pO2_bar": pO2_bar},
    )


def test_builtin_vapor_pressure_raises_on_nonfinite_pressure_compute() -> None:
    provider = BuiltinVaporPressureProvider(
        {
            "metals": {},
            "oxide_vapors": {
                "SiO": {
                    "antoine": {"A": math.inf, "B": 0.0, "C": 0.0},
                    "valid_range_K": [0.0, 10_000.0],
                }
            },
        }
    )

    with pytest.raises(
        VaporPressureComputationError,
        match="vapor_pressure_nonfinite: species=SiO field=P_sat",
    ):
        provider.dispatch(
            _vapor_request(
                temperature_c=1400.0,
                pO2_bar=1.0e-9,
                accounts={"process.cleaned_melt": {}},
            )
        )


def test_builtin_vapor_pressure_commanded_extreme_pO2_floor_is_finite() -> None:
    with (DATA_DIR / "vapor_pressures.yaml").open() as handle:
        provider = BuiltinVaporPressureProvider(yaml.safe_load(handle))

    result = provider.dispatch(
        _vapor_request(
            temperature_c=3000.0,
            pO2_bar=1.0e-9,
        )
    )
    pressures = dict(result.diagnostic["vapor_pressures_Pa"])

    assert result.status == "ok"
    assert result.diagnostic["pO2_bar"] == pytest.approx(1.0e-9)
    assert all(math.isfinite(value) for value in pressures.values())


def test_builtin_vapor_pressure_warns_once_for_pseudo_curvefit() -> None:
    provider = BuiltinVaporPressureProvider(
        {
            "metals": {
                "K": {
                    "parent_oxide": "K2O",
                    "fit_target": "pseudo_psat_backsolved_from_vaporock",
                    "residual_dex": 1.4,
                    "confidence_tier": "low",
                    "antoine": {"A": 5.0, "B": 0.0, "C": 0.0},
                }
            },
            "oxide_vapors": {},
        }
    )
    request = _vapor_request(
        temperature_c=1600.0,
        pO2_bar=1.0e-9,
        accounts={"process.cleaned_melt": {"K2O": 1.0}},
    )

    with pytest.warns(
        HighUncertaintyVaporPressureFallbackWarning,
        match=(
            "HIGH-UNCERTAINTY WARNING: K vapor pressure uses a backsolved "
            "VapoRock fallback \\(curve-fit\\), NOT first-principles; "
            "residual_dex=1.4; confidence_tier=low; "
            "builtin remains authoritative; VapoRock is diagnostic-only"
        ),
    ):
        first = provider.dispatch(request)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        second = provider.dispatch(request)

    assert caught == []
    assert json.dumps(
        first.diagnostic["vapor_pressures_Pa"],
        sort_keys=True,
        separators=(",", ":"),
    ) == json.dumps(
        second.diagnostic["vapor_pressures_Pa"],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first.diagnostic["vapor_pressures_source"] == {
        "K": "builtin_authoritative:backsolved_vaporock_curve_fit"
    }


def test_builtin_vapor_pressure_uncertified_pure_component_row_is_silent() -> None:
    provider = BuiltinVaporPressureProvider(
        {
            "metals": {
                "K": {
                    "parent_oxide": "K2O",
                    "fit_target": "pure_component_psat",
                    "residual_dex": 0.01,
                    "confidence_tier": "high",
                    "antoine": {"A": 5.0, "B": 0.0, "C": 0.0},
                }
            },
            "oxide_vapors": {},
        }
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = provider.dispatch(
            _vapor_request(
                temperature_c=1600.0,
                pO2_bar=1.0e-9,
                accounts={"process.cleaned_melt": {"K2O": 1.0}},
            )
        )

    assert caught == []
    assert result.diagnostic["vapor_pressures_source"] == {
        "K": "builtin_authoritative:legacy_pure_component_estimate"
    }
