"""Golden pin of the Kress redox output and the battery-handle open block.

Committed before those statements move. The numbers are the production
resolver's output at that revision, frozen as exact decimals and notice text.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from simulator.battery.enums import Authority, Engine, ExecutionState, RefusalReason
from simulator.battery.migrate import load_yaml
from simulator.battery.score import predict_with_engine
from tests.battery.factories import migrate_one_extract
from tests.battery.test_melt_activity_requirements import _case, _composition

_KRESS_SOURCES = ("Kress and Carmichael 1991, doi:10.1007/BF00307328",)
_ROWS: dict | None = None


def _borisov_rows():
    global _ROWS
    if _ROWS is not None:
        return _ROWS
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    source = load_yaml(extract)
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    rows = {
        row.observation_id: row for row in migrator.result.observations.values()
    }
    hot = next(
        row
        for row in source["species"]["Fe"]["observations"]
        if float(row["values"]["T_C"]) > 1630.0
    )
    _ROWS = {
        "row4": rows[f"{extract.stem}::borisov_2018_table2_row_4"],
        "row155": rows[f"{extract.stem}::borisov_2018_table2_row_155"],
        "hot": rows[f"{extract.stem}::{hot['observation_id']}"],
    }
    return _ROWS


def _activity_case():
    return _case(
        composition=_composition(("SiO2", "0.5"), ("CaO", "0.3"), ("Al2O3", "0.2")),
        formula="SiO2",
    )


def test_kress_fe_redox_output_is_bit_identical():
    rows = _borisov_rows()
    row4 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, rows["row4"])
    row155 = predict_with_engine(Engine.INTERNAL_ANALYTICAL, rows["row155"])
    hot = predict_with_engine(Engine.INTERNAL_ANALYTICAL, rows["hot"])

    assert row4.value == Decimal("1.838360613009497")
    assert row155.value == Decimal("3.2770002461454593")
    assert hot.value == Decimal("0.915061835458987")
    for prediction in (row4, row155, hot):
        assert prediction.execution.state is ExecutionState.PRODUCED
        assert prediction.execution.call_evidence == "simulator.fe_redox:kress91_split"
        assert prediction.refusal_reason is None
        assert prediction.refusal_detail == {}
        assert prediction.unit == "dimensionless"
        assert prediction.coefficient_sources == _KRESS_SOURCES
        assert prediction.lineage_complete is False
        assert prediction.version == "Kress-Carmichael-1991"
        assert prediction.identity is not None
        assert prediction.identity.fO2_Pa is not None
        assert prediction.identity.fO2_Pa.value == Decimal("20892.96130854039")
        assert prediction.identity.total_pressure_Pa is not None
        assert prediction.identity.total_pressure_Pa.value == Decimal("101325")
        assert prediction.certified_band == {
            "temperature_K": (Decimal("1473.15"), Decimal("1903.15"))
        }
    assert row4.authority is Authority.BRIDGE
    assert row4.notices == ()
    assert row155.authority is Authority.BRIDGE
    assert row155.notices == ()
    assert hot.authority is Authority.EXTRAPOLATED
    assert len(hot.notices) == 1
    notice = hot.notices[0]
    assert notice.kind.value == "out_of_certified_band"
    assert notice.reason == (
        "kress91_extrapolation_experimentally_confirmed; "
        "authority=REF-001 Kress91 retained; "
        "REF-053 confirms high-T extrapolation; "
        "calibrated_temperature_C=[1200,1630]"
    )
    assert notice.origin == "simulator.fe_redox:kress91_split"
    assert notice.band == "[1473.15, 1903.15] K"
    assert notice.authority is Authority.EXTRAPOLATED

    refused = predict_with_engine(Engine.MAGEMIN, rows["row4"])
    assert refused.value is None
    assert refused.authority is Authority.REFUSED
    assert refused.execution.state is ExecutionState.UNSUPPORTED
    assert refused.execution.call_evidence is None
    assert refused.refusal_reason is RefusalReason.ENGINE_DOES_NOT_REPORT_FE_REDOX
    assert refused.refusal_detail == {
        "reason": "engine_does_not_report_fe_redox",
        "engine": "magemin",
        "detail": (
            "no fixed-T/fO2/composition ferric output without "
            "phase-equilibrium execution"
        ),
    }
    assert refused.coefficient_sources == _KRESS_SOURCES
    assert refused.lineage_complete is False
    assert refused.version is None

    conditions = dict(rows["row4"].point_conditions or {})
    conditions.pop("fO2_log", None)
    conditions.pop("fO2_Pa", None)
    missing = replace(
        rows["row4"],
        point_conditions=conditions,
        identity=replace(rows["row4"].identity, fO2_Pa=None),
    )
    missing_prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, missing)
    assert missing_prediction.value is None
    assert missing_prediction.authority is Authority.REFUSED
    assert missing_prediction.execution.state is ExecutionState.NOT_PROBED
    assert missing_prediction.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert missing_prediction.refusal_detail == {
        "reason": "missing_input",
        "input": "printed_fO2",
    }
    assert missing_prediction.coefficient_sources == _KRESS_SOURCES


def test_battery_handle_open_cache_output_is_bit_identical(monkeypatch):
    opens: list[str] = []

    def _open(name):
        opens.append(name)
        return SimpleNamespace(
            name=name,
            available=False,
            backend=None,
            unavailable_reason="binary-absent",
            identity=None,
        )

    def _cell(*_args, **_kwargs):
        raise AssertionError("an unavailable handle must not equilibrate")

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine",
        _open,
    )
    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _cell,
    )
    experiment, _bench, observation = _activity_case()
    dead = SimpleNamespace(
        name="stale", available=False, backend=None, unavailable_reason="stale"
    )
    handles = {"alphamelts": dead}
    unavailable = predict_with_engine(
        Engine.ALPHAMELTS,
        observation,
        handles=handles,
        experiment=experiment,
        isolated=False,
    )

    assert opens == ["alphamelts"]
    assert handles == {}
    assert unavailable.value is None
    assert unavailable.authority is None
    assert unavailable.execution.state is ExecutionState.ATTEMPTED_UNAVAILABLE
    assert unavailable.execution.call_evidence == "open_battery_engine:alphamelts"
    assert unavailable.refusal_reason is RefusalReason.ATTEMPTED_UNAVAILABLE
    assert unavailable.refusal_detail == {
        "reason": "unavailable",
        "engine_reason": "binary-absent",
    }
    assert unavailable.coefficient_sources == ("alphamelts",)
    assert unavailable.lineage_complete is False
    assert unavailable.version is None
    assert len(unavailable.notices) == 1
    assert unavailable.notices[0].kind.value == "input_omitted"
    assert unavailable.notices[0].reason == (
        "melt has no multivalent element (Fe, Ti, Cr, Mn, V, Eu, Ce, S, Ga) "
        "in the composition or measured species; oxygen is not an input and "
        "was omitted"
    )
    assert unavailable.notices[0].origin == "score:predict_with_engine"

    opens.clear()
    magnitude = 0.1 + 0.2

    def _ok_cell(_handle, _pot, **_kwargs):
        return SimpleNamespace(
            status="ok",
            refusal_reason=None,
            melt_activities={"SiO2": magnitude},
            melt_activity_coefficients={},
            gas_partial_pressures_Pa={},
            hostname="pin-host",
            exit_code=0,
            exit_signal=None,
            notices=[],
            authority=None,
            certified_band=None,
            engine_reason=None,
        )

    monkeypatch.setattr(
        "simulator.diagnostic_helpers.binary_pot_battery.equilibrate_cell",
        _ok_cell,
    )
    live = SimpleNamespace(
        name="alphamelts",
        available=True,
        backend=object(),
        unavailable_reason=None,
        identity=None,
        supports_intrinsic_fo2=False,
    )
    handles = {"alphamelts": live}
    scored = predict_with_engine(
        Engine.ALPHAMELTS,
        observation,
        handles=handles,
        experiment=experiment,
        isolated=False,
    )

    assert opens == []
    assert handles["alphamelts"] is live
    assert scored.value == Decimal("0.30000000000000004")
    assert scored.unit == "dimensionless"
    assert scored.authority is Authority.BRIDGE
    assert scored.execution.state is ExecutionState.PRODUCED
    assert scored.execution.call_evidence == (
        "equilibrate_cell:alphamelts:host=pin-host:exit=0"
    )
    assert scored.refusal_reason is None
    assert scored.refusal_detail == {}
    assert scored.coefficient_sources == ("alphamelts",)
    assert scored.lineage_complete is False
    assert scored.version is None
    assert len(scored.notices) == 1
    assert scored.notices[0].kind.value == "input_omitted"
    assert scored.notices[0].origin == "score:predict_with_engine"
