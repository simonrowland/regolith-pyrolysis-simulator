import importlib.util
import json
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.melt_backend.melt_envelope import (
    MELT_EXTRAPOLATION_ENVELOPE_FIELDS,
    MeltEnvelopeValidationError,
    consume_melt_extrapolation_envelope,
    melt_extrapolation_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_shadow_matrix_consumes_and_persists_complete_melt_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    mod = _load_script("vaporock_antoine_shadow_matrix")
    monkeypatch.setattr(
        mod,
        "alphamelts_condensed_phase_pressure_bar",
        lambda pressure_bar, **_kwargs: pressure_bar,
    )
    monkeypatch.setattr(
        mod,
        "annotate_alphamelts_reference_pressure",
        lambda result, **_kwargs: result,
    )

    class FakeAlpha:
        _mode = "fake"

        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def equilibrate(**_kwargs):
            return SimpleNamespace(
                status="ok",
                warnings=[],
                liquid_composition_wt_pct={"SiO2": 100.0},
                activity_coefficients={"SiO2": 1.0},
            )

        @staticmethod
        def _activities_times_antoine(*_args):
            return {"Na": 2.0}

        @staticmethod
        def _melt_has_antoine_vapor_precursor(*_args):
            return True

        @staticmethod
        def _load_vapor_pressure_table():
            return {}

    class FakeVapoRock:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def equilibrate(**_kwargs):
            return SimpleNamespace(
                status="ok",
                warnings=[],
                vapor_pressures_Pa={"Na": 4.0},
            )

    cell = mod.Cell(
        profile_id="offline-test",
        profile_path=tmp_path / "profile.yaml",
        feedstock_id="test-feedstock",
        composition_wt_pct={"SiO2": 100.0},
        composition_digest="composition-digest",
        t_k=1800.0,
        fO2_log10_bar=-8.0,
        pressure_bar=0.01,
        pressure_context={"source": "test"},
        temperature_source="test",
    )
    rows = mod.solve_cell(
        alpha=FakeAlpha(),
        vaporock=FakeVapoRock(),
        alpha_ready=True,
        vaporock_ready=True,
        cell=cell,
        large_delta_log10=2.0,
    )
    assert rows
    for row in rows:
        assert set(MELT_EXTRAPOLATION_ENVELOPE_FIELDS) <= set(
            row.melt_diagnostic
        )
        assert row.melt_diagnostic["instrument_status"] == (
            "status_bearing_non_authoritative"
        )
        consume_melt_extrapolation_envelope(
            row.melt_diagnostic,
            temperature_K=cell.t_k,
        )

    unavailable_rows = mod.solve_cell(
        alpha=FakeAlpha(),
        vaporock=FakeVapoRock(),
        alpha_ready=False,
        vaporock_ready=True,
        cell=cell,
        large_delta_log10=2.0,
    )
    assert all(
        row.flags == ("cell_failed:engine_unavailable:alphamelts",)
        for row in unavailable_rows
    )
    for row in unavailable_rows:
        consume_melt_extrapolation_envelope(
            row.melt_diagnostic,
            temperature_K=cell.t_k,
        )

    store = mod.ShadowStore(tmp_path / "shadow.db")
    engine_versions = {"alphamelts": "fake", "vaporock": "fake"}
    data_digests = {"profile": "digest"}
    stored = store.upsert_rows(
        cell=cell,
        rows=rows,
        backend="offline-test",
        engine_versions=engine_versions,
        repo_version="test",
        data_digests=data_digests,
        git_dirty=False,
        created_at="2026-08-11T00:00:00+00:00",
    )
    assert stored
    cell_key = mod.make_cell_key(
        cell=cell,
        backend="offline-test",
        engine_versions=engine_versions,
        repo_version="test",
        data_digests=data_digests,
    )
    replayed = mod.ShadowStore(store.path).rows_for_cell(cell_key, ("Na",))
    replayed_diagnostic = json.loads(replayed[0]["melt_diagnostic_json"])
    consume_melt_extrapolation_envelope(
        replayed_diagnostic,
        temperature_K=cell.t_k,
    )

    del replayed_diagnostic["constants_version"]
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE shadow_rows SET melt_diagnostic_json = ? "
            "WHERE cell_key = ? AND species = 'Na'",
            (json.dumps(replayed_diagnostic), cell_key),
        )
    with pytest.raises(MeltEnvelopeValidationError, match="partial H2"):
        store.rows_for_cell(cell_key, ("Na",))


def test_pseudo_antoine_refit_consumes_complete_melt_envelope(
    monkeypatch: pytest.MonkeyPatch,
):
    mod = _load_script("vaporock_pseudo_antoine_refit")

    class FakeResult:
        class _Loc:
            @staticmethod
            def __getitem__(_name: str):
                return SimpleNamespace(iloc=[-3.0])

        loc = _Loc()

    class FakeSystem:
        @staticmethod
        def set_melt_comp(_composition):
            return None

        @staticmethod
        def eval_gas_abundances(_temperature_k: float, _logfo2: float):
            return FakeResult()

    monkeypatch.setattr(
        mod,
        "vaporock",
        SimpleNamespace(
            System=FakeSystem,
            redox_buffer=lambda _temperature_k, _buffer: -8.0,
        ),
    )
    logfo2, pressures, diagnostic = mod.vaporock_pressures_pa(
        {"SiO2": 100.0},
        1800.0,
        ("Na", "SiO"),
    )
    assert logfo2 == -8.0
    assert pressures == {"Na": 100.0, "SiO": 100.0}
    assert set(MELT_EXTRAPOLATION_ENVELOPE_FIELDS) <= set(diagnostic)
    assert diagnostic["instrument_status"] == (
        "status_bearing_non_authoritative"
    )
    consume_melt_extrapolation_envelope(
        diagnostic,
        temperature_K=1800.0,
    )


def test_pseudo_antoine_refit_diagnostics_do_not_change_fitted_rows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    mod = _load_script("vaporock_pseudo_antoine_refit")
    args = Namespace(
        feedstocks=["lunar_mare_low_ti"],
        t_min=1800.0,
        t_max=1820.0,
        t_step=20.0,
        species=["Na"],
        c_bound=1000.0,
        json=True,
    )
    monkeypatch.setattr(mod, "parse_args", lambda: args)
    monkeypatch.setattr(mod, "fallback_activity_term", lambda **_kwargs: 1.0)
    monkeypatch.setattr(
        mod,
        "current_coefficients",
        lambda _data, _species: (1.0, 2.0, 3.0),
    )
    monkeypatch.setattr(
        mod,
        "residual_stats",
        lambda coefficients, _samples: {
            "max_abs_dex": float(coefficients[0]),
            "rmse_dex": float(coefficients[1]),
        },
    )
    monkeypatch.setattr(
        mod,
        "fit_minimax",
        lambda _samples, *, c_bound: (0.25, (0.2, 0.1, c_bound / 10.0)),
    )
    include_extra = {"value": False}

    def fake_pressures(_composition, temperature_k, _species):
        diagnostic = melt_extrapolation_diagnostic(
            temperature_k,
            "MELTS-v1.0",
        )
        if include_extra["value"]:
            diagnostic["research_note"] = "cache-inert-report-only"
        return -8.0, {"Na": 100.0}, diagnostic

    monkeypatch.setattr(mod, "vaporock_pressures_pa", fake_pressures)

    assert mod.main() == 0
    baseline = json.loads(capsys.readouterr().out)
    include_extra["value"] = True
    assert mod.main() == 0
    annotated = json.loads(capsys.readouterr().out)

    assert annotated["rows"] == baseline["rows"]
    assert annotated["grid"] == baseline["grid"]
    assert all(
        "research_note" not in row
        for row in baseline["melt_extrapolation_diagnostics"]
    )
    assert all(
        row["research_note"] == "cache-inert-report-only"
        for row in annotated["melt_extrapolation_diagnostics"]
    )


def test_pseudo_antoine_refit_failure_preserves_validated_melt_envelope(
    monkeypatch: pytest.MonkeyPatch,
):
    mod = _load_script("vaporock_pseudo_antoine_refit")

    class FailingSystem:
        @staticmethod
        def set_melt_comp(_composition):
            return None

        @staticmethod
        def eval_gas_abundances(_temperature_k: float, _logfo2: float):
            raise RuntimeError("forced upstream failure")

    monkeypatch.setattr(
        mod,
        "vaporock",
        SimpleNamespace(
            System=FailingSystem,
            redox_buffer=lambda _temperature_k, _buffer: -8.0,
        ),
    )

    with pytest.raises(mod.VapoRockRefitCellError) as exc_info:
        mod.vaporock_pressures_pa(
            {"SiO2": 100.0},
            1800.0,
            ("Na",),
        )

    assert exc_info.value.status == "refused"
    assert "forced upstream failure" in str(exc_info.value)
    consume_melt_extrapolation_envelope(
        exc_info.value.diagnostic,
        temperature_K=1800.0,
    )
