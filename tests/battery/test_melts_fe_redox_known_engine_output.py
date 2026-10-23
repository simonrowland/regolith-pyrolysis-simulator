"""Fe3+/Fe2+ from one captured alphaMELTS stdout and one ThermoEngine liquid.

Captured 2026-10-22 at 1400 C, 1 bar, absolute log10(fO2/bar) = -6, on the
bulk in recovery24_known_melts_fe_output.json. alphaMELTS 2.3.1 printed the
liquid line. ThermoEngine MELTSv1.0.2 returned the liquid oxide weights.
The battery value is the molar Fe3+/Fe2+ ratio, not Fe3Fet_Liq.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from simulator.battery.enums import Engine
from simulator.battery.migrate import fe3_fe2_ratio_from_oxide_wt_pct
from simulator.battery.score import predict_with_engine
from tests.battery.test_redox_extract_scoring import _borisov_row4

_FIXTURE = Path(__file__).with_name("recovery24_known_melts_fe_output.json")


def _load():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _parse_alphamelts(fixture: dict):
    from simulator.melt_backend.alphamelts import (
        AlphaMELTSBackend,
        AlphaMELTSSubprocessRunMode,
    )

    backend = AlphaMELTSBackend()
    return backend._parse_single_point_stdout(
        fixture["alphamelts_stdout"],
        requested_temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-6.0,
        total_input_kg=0.1,
        run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
        system_output=fixture["alphamelts_system_main"],
        fO2_constraint={"path": "Absolute", "offset": -6.0},
    )


class _Replay:
    def __init__(self, result):
        self.result = result

    def equilibrate(self, **_kwargs):
        return self.result


def _score(monkeypatch, result, engine: Engine):
    def _open(name):
        return SimpleNamespace(
            name=name,
            available=True,
            unavailable_reason=None,
            backend=_Replay(result),
            supports_intrinsic_fo2=(name == "thermoengine"),
            identity=None,
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    return predict_with_engine(engine, _borisov_row4(), isolated=False)


def test_alphamelts_stdout_liquid_split_scores_molar_fe_ratio(monkeypatch):
    fixture = _load()
    parsed = _parse_alphamelts(fixture)
    liquid = parsed.liquid_composition_wt_pct
    # The printed liquid line is the engine output. 2.02 and 9.13 are those
    # tokens, not a substituted pair.
    assert "2.02" in fixture["alphamelts_stdout"]
    assert "9.13" in fixture["alphamelts_stdout"]
    assert liquid["Fe2O3"] == 2.02
    assert liquid["FeO"] == 9.13
    assert parsed.status == "ok"

    prediction = _score(monkeypatch, parsed, Engine.ALPHAMELTS)
    expected = fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal(str(liquid["Fe2O3"])),
        Decimal(str(liquid["FeO"])),
    )
    assert expected == Decimal(fixture["alphamelts_expected_ratio"])
    assert prediction.value == expected
    assert prediction.refusal_reason is None
    assert prediction.unit == "dimensionless"
    assert prediction.coefficient_sources == ("alphamelts",)


def test_thermoengine_liquid_split_scores_molar_fe_ratio(monkeypatch):
    from engines.alphamelts.thermoengine import ThermoEngineTransport
    from simulator.melt_backend.alphamelts import activity_from_chem_potential

    fixture = _load()
    liquid = dict(fixture["thermoengine_liquid_composition_wt_pct"])
    transport = ThermoEngineTransport(
        model_name="MELTSv1.0.2",
        activity_converter=activity_from_chem_potential,
    )
    split = transport._fe_redox_split(liquid)
    captured = fixture["thermoengine_fe_redox_split"]
    assert split["FeO_wt_pct"] == liquid["FeO"]
    assert split["Fe2O3_wt_pct"] == liquid["Fe2O3"]
    assert split["FeO_wt_pct"] == captured["FeO_wt_pct"]
    assert split["Fe2O3_wt_pct"] == captured["Fe2O3_wt_pct"]
    assert split["Fe3Fet_Liq"] == captured["Fe3Fet_Liq"]

    result = SimpleNamespace(
        status="ok",
        warnings=[],
        diagnostics={},
        liquid_fraction=1.0,
        liquid_composition_wt_pct=liquid,
        fe_redox_split=split,
        activity_coefficients={},
        vapor_pressures_Pa={},
    )
    prediction = _score(monkeypatch, result, Engine.THERMOENGINE)
    expected = fe3_fe2_ratio_from_oxide_wt_pct(
        Decimal(str(split["Fe2O3_wt_pct"])),
        Decimal(str(split["FeO_wt_pct"])),
    )
    assert expected == Decimal(fixture["thermoengine_expected_ratio"])
    assert prediction.value == expected
    # Fe3Fet_Liq is Fe3+/total Fe. The observation basis is Fe3+/Fe2+.
    assert prediction.value != Decimal(str(split["Fe3Fet_Liq"]))
    assert prediction.refusal_reason is None
    assert prediction.unit == "dimensionless"
    assert prediction.coefficient_sources == ("thermoengine",)
