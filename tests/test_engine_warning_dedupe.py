from __future__ import annotations

import logging
import warnings
from types import SimpleNamespace

import pytest

from simulator import engine_local_config
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


def _emit_every_warning(
    key: str,
    message: str,
    *,
    category: type[Warning] = UserWarning,
    stacklevel: int = 2,
) -> None:
    del key
    warnings.warn(message, category, stacklevel=stacklevel + 1)


def test_vaporock_fallback_dedupe_keeps_result_and_counts_fifty_calls(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine_local_config, "_WARNING_REPEAT_COUNTS", {}, raising=False
    )
    monkeypatch.setattr(
        engine_local_config,
        "warn_once_with_summary",
        _emit_every_warning,
        raising=False,
    )

    def project():
        backend = AlphaMELTSBackend()
        backend._vaporock_helper = SimpleNamespace(
            is_available=lambda: True,
            equilibrate=lambda **_kwargs: SimpleNamespace(
                status="not_converged",
                vapor_pressures_Pa={},
                warnings=("forced empty result",),
            ),
        )
        backend._vapor_pressure_table = {
            "K": {
                "fit_target": "pure_component_psat",
                "antoine": {"A": 5.0, "B": 0.0, "C": 0.0},
            },
        }
        return backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={"K2O": 100.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            pressure_bar=1.0,
            activities={"K2O": 0.5},
        )

    with warnings.catch_warnings(record=True) as before_warnings:
        warnings.simplefilter("always")
        before = [project() for _ in range(50)]
    assert len(before_warnings) == 50

    dedupe = engine_local_config.warn_once_with_summary
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    monkeypatch.setattr(engine_local_config, "warn_once_with_summary", dedupe)
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")
    with warnings.catch_warnings(record=True) as after_warnings:
        warnings.simplefilter("always")
        after = [project() for _ in range(50)]

    assert len(after_warnings) == 1
    assert "VapoRock returned no usable vapor pressures" in str(
        after_warnings[0].message
    )
    assert after == before

    engine_local_config._flush_warning_repeat_summaries()

    assert [record.getMessage() for record in caplog.records] == [
        "simulator.melt_backend.alphamelts.vaporock_fallback: "
        "49 repeats suppressed"
    ]


def test_warning_dedupe_keeps_pressure_results_and_notices_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine_local_config, "_WARNING_REPEAT_COUNTS", {}, raising=False
    )
    monkeypatch.setattr(
        engine_local_config,
        "warn_once_with_summary",
        _emit_every_warning,
        raising=False,
    )

    def project():
        backend = AlphaMELTSBackend()
        pressures = backend._activities_times_antoine(
            3000.0,
            {"TiO2": 0.6, "MnO": 0.4},
            {"TiO2": 60.0, "MnO": 40.0},
            pO2_bar=1e-31,
        )
        sources = backend._antoine_vapor_pressure_source_by_species(
            "alphamelts_python_api", pressures
        )
        diagnostics = backend._vapor_pressure_diagnostics(
            {}, pressures, sources
        )
        return pressures, sources, diagnostics

    with warnings.catch_warnings(record=True) as before_warnings:
        warnings.simplefilter("always")
        before = [project() for _ in range(2)]
    assert any(
        "Ti melt-source reaction: out_of_range_conservative_continuation"
        in str(item.message)
        for item in before_warnings
    )
    assert any(
        "Mn melt-source reaction: out_of_range_conservative_continuation"
        in str(item.message)
        for item in before_warnings
    )

    dedupe = engine_local_config.warn_once_with_summary
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    monkeypatch.setattr(engine_local_config, "warn_once_with_summary", dedupe)
    with warnings.catch_warnings(record=True) as after_warnings:
        warnings.simplefilter("always")
        after = [project() for _ in range(2)]

    assert after_warnings
    assert [item for item in after_warnings if "pO2_outside_evaluator_band" in str(item.message)]
    assert [item for item in after_warnings if "Ti melt-source reaction" in str(item.message)]
    assert [item for item in after_warnings if "Mn melt-source reaction" in str(item.message)]
    assert before == after
    diagnostics = after[0][2]
    notices = diagnostics["pO2_floor_inversion_notices_by_species"]
    assert notices["Ti"]["pO2_bar_used"] == 1e-31
    assert notices["Mn"]["pO2_bar_used"] == 1e-31
