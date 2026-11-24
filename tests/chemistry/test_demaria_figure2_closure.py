"""De Maria Figure 2: both callable potassium points stay predicted.

The failure message carries the closure handoff, including the inner
engine refusal that the public oxygen-balance reason used to hide.
"""

from __future__ import annotations

import pytest


def test_demaria_figure2_callable_points_keep_a_positive_prediction(monkeypatch):
    from simulator.diagnostic_helpers import binary_pot_battery as pots
    from simulator.diagnostic_helpers import extract_reproduction as reproduction

    captured: list[str] = []
    real_adapter = pots._internal_analytical_vapor_pressure_adapter

    def adapter(**kwargs):
        request = kwargs.get("po2_request")
        try:
            return real_adapter(**kwargs)
        except Exception as exc:
            text = (
                f"{type(exc).__name__} code={getattr(exc, 'code', None)} "
                f"reason={getattr(exc, 'reason_code', None)} "
                f"mode={getattr(request, 'mode', None)} "
                f"cell={getattr(request, 'cell_material', None)} "
                f"detail={exc}"
            )
            captured.append(text[:360])
            raise

    monkeypatch.setattr(
        pots, "_internal_analytical_vapor_pressure_adapter", adapter
    )

    compare = reproduction._compare_point
    runtimes: list[dict] = []

    def capture(**kwargs):
        runtimes.append(dict(kwargs["runtime"]))
        return compare(**kwargs)

    monkeypatch.setattr(reproduction, "_compare_point", capture)
    row = next(
        item
        for item in reproduction.load_adopted_observations()
        if item.observation_id == "demaria_1971_k_psat_figure2_digitized"
    )
    result = reproduction.evaluate_observation(
        row,
        vapor_pressure_data=reproduction.load_vapor_pressure_data(),
    )

    def handoff(temperature: float) -> dict | None:
        for runtime in runtimes:
            if runtime.get("temperature_K") != temperature:
                continue
            return {
                "refusal": runtime.get("refusal"),
                "recipe": runtime.get("melt_recipe_source"),
                "closure": runtime.get("oxygen_closure"),
            }
        return None

    def brief(temperature: float) -> str:
        payload = handoff(temperature) or {}
        closure = payload.get("closure") or {}
        detail = closure.get("refusal_detail") or {}
        inner = detail.get("inner_detail") if isinstance(detail, dict) else None
        cell_refusal = inner.get("refusal_reason") if isinstance(inner, dict) else None
        engine_reason = inner.get("engine_reason") if isinstance(inner, dict) else None
        if isinstance(cell_refusal, str) and len(cell_refusal) > 160:
            cell_refusal = cell_refusal[:160]
        if isinstance(engine_reason, str) and len(engine_reason) > 160:
            engine_reason = engine_reason[:160]
        return (
            f"{temperature}: refusal={payload.get('refusal')} "
            f"inner={detail.get('inner_refusal_reason') if isinstance(detail, dict) else None} "
            f"cell={cell_refusal} engine_reason={engine_reason}"
        )

    assumed = [
        record for record in result.records if record.status == "assumed-input"
    ]
    missing = [
        record
        for record in result.records
        if record.status == "unsupported-speciation"
    ]
    assert len(assumed) == 2, (
        f"skip={result.skip_reason}; {brief(1366.12)}; {brief(1314.06)}; "
        f"adapter={captured}"
    )
    assert {float(record.coordinate["temperature_K"]) for record in assumed} == {
        1366.12,
        1314.06,
    }
    assert all(
        record.actual_value is not None and record.actual_value > 0
        for record in assumed
    )
    assert len(missing) == 8
    assert all(record.actual_value is None for record in missing)
    assert "melt-only oxygen balance" in (result.skip_reason or "")


def test_demaria_figure2_scored_request_carries_projection(monkeypatch) -> None:
    from simulator.battery import score as battery_score
    from simulator.battery.enums import NoticeKind
    from simulator.diagnostic_helpers import binary_pot_battery as pots

    calls: list[dict[str, float]] = []
    predictions = []
    real_equilibrate = pots.equilibrate_cell
    real_predict = battery_score.predict_with_engine

    def capture_equilibrate(handle, pot, **kwargs):
        request = kwargs.get("po2")
        if (
            getattr(handle, "name", None) == "internal-analytical"
            and getattr(request, "mode", None)
            == pots.PO2_OXYGEN_BALANCE_EFFUSION
        ):
            calls.append(dict(pot.composition_wt_pct))
        return real_equilibrate(handle, pot, **kwargs)

    def predict_in_process(*args, **kwargs):
        kwargs["isolated"] = False
        prediction = real_predict(*args, **kwargs)
        predictions.append(prediction)
        return prediction

    monkeypatch.setattr(pots, "equilibrate_cell", capture_equilibrate)
    monkeypatch.setattr(
        battery_score, "predict_with_engine", predict_in_process
    )
    test_demaria_figure2_callable_points_keep_a_positive_prediction(monkeypatch)

    assert calls
    assert all("MnO" not in composition for composition in calls)
    projected = [
        notice
        for prediction in predictions
        for notice in prediction.notices
        if notice.kind is NoticeKind.COMPOSITION_PROJECTED
    ]
    assert projected
    assert all("MnO" in notice.dropped for notice in projected)
    assert all(
        "MnO" in notice.reason and "wt_pct" in notice.reason
        for notice in projected
    )


def test_passing_mno_through_to_openimcc_breaks_the_pin(monkeypatch) -> None:
    """Removing the score-boundary projection refuses both Figure 2 points.

    The closure's internal-analytical call runs in a child, which would not
    see a parent monkeypatch. Force the same process, then keep every oxide.
    """

    from simulator.battery import score as battery_score
    from simulator.melt_backend import openimcc_bridge

    def keep_every_oxide(composition_mol):
        return dict(composition_mol), {}

    real_predict = battery_score.predict_with_engine

    def predict_in_process(*args, **kwargs):
        kwargs["isolated"] = False
        return real_predict(*args, **kwargs)

    monkeypatch.setattr(
        openimcc_bridge, "_cleaned_melt_policy", keep_every_oxide
    )
    monkeypatch.setattr(
        battery_score, "predict_with_engine", predict_in_process
    )
    with pytest.raises(AssertionError):
        test_demaria_figure2_scored_request_carries_projection(monkeypatch)
