from __future__ import annotations

import logging
import sys
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
    dedupe = engine_local_config.warn_once_with_summary
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

    sys.modules[AlphaMELTSBackend.__module__].__dict__.pop(
        "__warningregistry__", None
    )
    with warnings.catch_warnings(record=True) as before_warnings:
        warnings.simplefilter("always")
        before = [project() for _ in range(50)]
    assert len(before_warnings) == 50

    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    monkeypatch.setattr(engine_local_config, "warn_once_with_summary", dedupe)
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")
    with warnings.catch_warnings(record=True) as after_warnings:
        sys.modules[AlphaMELTSBackend.__module__].__dict__.pop(
            "__warningregistry__", None
        )
        warnings.simplefilter("default")
        after = [project() for _ in range(50)]

    assert len(after_warnings) == 1
    assert "VapoRock returned no usable vapor pressures" in str(
        after_warnings[0].message
    )
    assert "forced empty result" in str(after_warnings[0].message)
    assert "1600" in str(after_warnings[0].message)
    assert after == before

    engine_local_config._flush_warning_repeat_summaries()

    assert [record.getMessage() for record in caplog.records] == [
        "simulator.melt_backend.alphamelts.vaporock_fallback:nonconvergence: "
        "49 repeats suppressed"
    ]


def test_same_warning_is_visible_in_two_pytest_warns_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    key = "test.engine.order-independent"
    message = "stable warning text"

    with pytest.warns(UserWarning, match=message):
        engine_local_config.warn_once_with_summary(key, message)
    with pytest.warns(UserWarning, match=message):
        engine_local_config.warn_once_with_summary(key, message)


def test_seen_warning_still_raises_when_warning_filter_changes_to_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    key = "test.engine.warning-as-error"
    message = "stable warning text"
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default", UserWarning)
        engine_local_config.warn_once_with_summary(key, message)
        warnings.simplefilter("error", UserWarning)
        with pytest.raises(UserWarning, match=message):
            engine_local_config.warn_once_with_summary(key, message)

    assert [str(item.message) for item in caught] == [message]
    assert engine_local_config._WARNING_REPEAT_COUNTS == {key: 1}
    engine_local_config._flush_warning_repeat_summaries()
    assert caplog.records == []


def test_default_filter_dedupe_survives_warning_filter_mutation(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    key = "test.engine.filter-mutation"
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default", UserWarning)
        engine_local_config.warn_once_with_summary(
            key, "first detail at 1600 K"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
        engine_local_config.warn_once_with_summary(
            key, "second detail at 1700 K"
        )

    assert [str(item.message) for item in caught] == [
        "first detail at 1600 K"
    ]
    engine_local_config._flush_warning_repeat_summaries()
    assert [record.getMessage() for record in caplog.records] == [
        f"{key}: 1 repeats suppressed"
    ]


def test_vaporock_fallback_warning_is_deduped_per_reason_class(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engines.domain_reason import OutOfDomainReason

    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")

    def fallback(reason_code: str, detail: str):
        backend = AlphaMELTSBackend()
        backend._vaporock_helper = SimpleNamespace(
            is_available=lambda: True,
            equilibrate=lambda **_kwargs: SimpleNamespace(
                status="out_of_domain",
                vapor_pressures_Pa={},
                warnings=(detail,),
                diagnostics={"backend_status_reason": reason_code},
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

    sys.modules[AlphaMELTSBackend.__module__].__dict__.pop(
        "__warningregistry__", None
    )
    calls = (
        (OutOfDomainReason.TEMPERATURE_RANGE.value, "first detail at 1600 K"),
        (OutOfDomainReason.TEMPERATURE_RANGE.value, "different detail at 1700 K"),
        (OutOfDomainReason.LIQUID_STATE.value, "third detail at 1800 K"),
        (OutOfDomainReason.LIQUID_STATE.value, "fourth detail at 1900 K"),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for reason_code, detail in calls:
            fallback(reason_code, detail)

    assert len(caught) == 2
    messages = [str(item.message) for item in caught]
    assert any(
        f"out_of_domain:{OutOfDomainReason.TEMPERATURE_RANGE.value}" in message
        for message in messages
    )
    assert any(
        f"out_of_domain:{OutOfDomainReason.LIQUID_STATE.value}" in message
        for message in messages
    )
    assert any("first detail at 1600 K" in message for message in messages)
    engine_local_config._flush_warning_repeat_summaries()
    assert sorted(record.getMessage() for record in caplog.records) == [
        "simulator.melt_backend.alphamelts.vaporock_fallback:"
        f"out_of_domain:{OutOfDomainReason.LIQUID_STATE.value}: "
        "1 repeats suppressed",
        "simulator.melt_backend.alphamelts.vaporock_fallback:"
        f"out_of_domain:{OutOfDomainReason.TEMPERATURE_RANGE.value}: "
        "1 repeats suppressed",
    ]


def test_warning_dedupe_keeps_pressure_results_and_notices_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dedupe = engine_local_config.warn_once_with_summary
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

    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    monkeypatch.setattr(engine_local_config, "warn_once_with_summary", dedupe)
    with warnings.catch_warnings(record=True) as after_warnings:
        warnings.simplefilter("always")
        after = [project() for _ in range(2)]

    assert len(after_warnings) == len(before_warnings)
    assert any(
        "pO2_outside_evaluator_band" in str(item.message)
        for item in after_warnings
    )
    assert any(
        "Ti melt-source reaction" in str(item.message)
        for item in after_warnings
    )
    assert any(
        "Mn melt-source reaction" in str(item.message)
        for item in after_warnings
    )
    assert before == after
    diagnostics = after[0][2]
    notices = diagnostics["pO2_floor_inversion_notices_by_species"]
    assert notices["Ti"]["pO2_bar_used"] == 1e-31
    assert notices["Mn"]["pO2_bar_used"] == 1e-31
    messages = [str(item.message) for item in after_warnings]
    assert any("pO2_bar_used': 1e-31" in message for message in messages)
    assert any("3273.15 K" in message for message in messages)


def test_not_converged_vaporock_keys_include_backend_cause(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_local_config, "_WARNING_REPEAT_COUNTS", {})
    caplog.set_level(logging.WARNING, logger="simulator.engine_local_config")

    def fallback(reason: str, detail: str):
        backend = AlphaMELTSBackend()
        backend._vaporock_helper = SimpleNamespace(
            is_available=lambda: True,
            equilibrate=lambda **_kwargs: SimpleNamespace(
                status="not_converged",
                vapor_pressures_Pa={},
                warnings=(detail,),
                diagnostics={"backend_status_reason": reason},
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

    causes = (
        "speciation_no_finite_values",
        "speciation_payload_absent",
        "speciation_nonfinite_pressure",
    )
    sys.modules[AlphaMELTSBackend.__module__].__dict__.pop(
        "__warningregistry__", None
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for cause in causes:
            fallback(cause, f"{cause} at 1600 K")
        for cause in causes:
            fallback(cause, f"repeat of {cause} at 1700 K")

    messages = [str(item.message) for item in caught]
    assert len(messages) == len(causes)
    assert all(
        f"nonconvergence:{cause}" in message
        for cause, message in zip(causes, messages)
    )
    assert all(
        f"{cause} at 1600 K" in message
        for cause, message in zip(causes, messages)
    )
    engine_local_config._flush_warning_repeat_summaries()
    assert sorted(record.getMessage() for record in caplog.records) == sorted(
        "simulator.melt_backend.alphamelts.vaporock_fallback:"
        f"nonconvergence:{cause}: 1 repeats suppressed"
        for cause in causes
    )
