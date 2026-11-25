"""Row-01 Chen 2022 a(MnO) probe. Prints the engine residual or the refusal.

The conversion census is tests/test_mno_solid_liquid_reference.py. This file
does not pin a residual magnitude. An engine that fails to import is recorded
and does not fail the solid-liquid conversion.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.score import predict_with_engine
from tests.test_mno_solid_liquid_reference import _MELT_ENGINES, _chen_rows


def test_chen_row01_engine_probe_prints_residuals() -> None:
    context, rows = _chen_rows()
    row = rows[0]
    experiment = context.experiments[row.experiment_id]
    measured = row.value.point
    attempted = 0
    for engine in _MELT_ENGINES:
        attempted += 1
        try:
            prediction = predict_with_engine(engine, row, experiment=experiment)
        except Exception as exc:
            print(f"ENGINE {engine.value} EXCEPTION {type(exc).__name__}: {exc}")
            continue
        if prediction.refusal_reason is not None:
            print(
                f"ENGINE {engine.value} REFUSED "
                f"{prediction.refusal_reason} {dict(prediction.refusal_detail)}"
            )
            continue
        predicted = prediction.value
        if (
            predicted is None
            or measured is None
            or predicted <= 0
            or measured <= 0
        ):
            print(f"ENGINE {engine.value} no numeric prediction value={predicted}")
            continue
        dex = (predicted / measured).ln() / Decimal(10).ln()
        print(
            f"ENGINE {engine.value} a_pred={predicted} a_meas={measured} "
            f"log10(pred/meas)={dex}"
        )
    assert attempted == len(_MELT_ENGINES)
