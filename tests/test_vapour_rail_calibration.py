"""VR-10 warm calibration runner + progressive-validation reports.

Acceptance (DECOMPOSITION VR-10 / DESIGN-REV5 §5.2–5.5):

* Runner uses VR-5 warm pool only over 1350–1950 K with held-out
  formulation / T / fO2 cells, censored sub-floor observations, fixed
  family/parameter caps, and independent anchors.
* Report includes per-row pending/validated state, remaining pending set,
  source-selectable/refused fractions, downstream error budget, and
  boundary statistics.
* Only the reviewed sidecar enters runtime data; raw SQLite is never read
  at runtime; no new cache layer.

Golden-neutral / offline. Live VapoRock is not required: warm-pool
contracts are tested with fakes.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import math
import sys
from pathlib import Path

import pytest
import yaml

from simulator.melt_backend.vaporock import (
    VAPOROCK_T_MAX_K,
    VAPOROCK_T_MIN_K,
    VapoRockBackend,
)
from simulator.vapour_rail import calibration as calib
from simulator.vapour_rail.calibration import (
    DEFAULT_CALIBRATION_SPECIES,
    DEFAULT_INDEPENDENT_ANCHORS,
    DEFAULT_P_FLOOR_PA,
    DEFAULT_SIDECAR_PATH,
    FROZEN_ANALYTICAL_FAMILIES,
    CalibrationResearchStore,
    CalibrationRunnerError,
    CalibrationSidecarError,
    HoldoutSplit,
    ObservationKind,
    RowValidationState,
    assert_no_runtime_sqlite_reader,
    boundary_faces_for_domain,
    build_calibration_cells,
    build_per_row_states,
    build_progressive_validation_report,
    build_sidecar_document,
    censor_pressure,
    default_holdout_plan,
    default_simple_melt_corpus,
    derive_error_budget,
    evaluate_boundary_jumps,
    evaluate_cell,
    load_vapour_rail_calibration_sidecar,
    require_warm_pool_backend,
    run_calibration_campaign,
    temperature_grid_K,
    write_sidecar,
)


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Corpus + domain
# ---------------------------------------------------------------------------


def test_temperature_grid_covers_vaporock_domain_and_user_story_slice():
    grid = temperature_grid_K()
    assert grid[0] == VAPOROCK_T_MIN_K == 1350.0
    assert grid[-1] == VAPOROCK_T_MAX_K == 1950.0
    # 50 K steps on the endpoints.
    assert 1400.0 in grid
    assert 1900.0 in grid
    assert 1573.15 in grid  # 1300 °C user-story slice
    # No out-of-domain points.
    assert all(VAPOROCK_T_MIN_K <= t <= VAPOROCK_T_MAX_K for t in grid)


def test_temperature_grid_refuses_outside_vaporock_domain():
    with pytest.raises(ValueError, match="VapoRock domain"):
        temperature_grid_K(t_min=1000.0, t_max=1950.0)
    with pytest.raises(ValueError, match="VapoRock domain"):
        temperature_grid_K(t_min=1350.0, t_max=10000.0)


def test_simple_melt_corpus_has_binaries_ternary_and_integration():
    corpus = default_simple_melt_corpus()
    roles = {f.role for f in corpus}
    assert roles == {"binary", "ternary", "integration"}
    families = {f.family for f in corpus}
    assert "SiO2-MgO" in families
    assert "SiO2-Na2O" in families
    assert "SiO2-MgO-FeO" in families
    assert "lunar_mare_integration" in families
    # Every composition is non-empty oxide mol map.
    for form in corpus:
        assert form.composition_mol
        assert all(v > 0.0 for v in form.composition_mol.values())


def test_holdout_plan_splits_formulation_T_and_fo2():
    cells = build_calibration_cells()
    splits = {c.split for c in cells}
    assert HoldoutSplit.TRAIN in splits
    assert HoldoutSplit.HOLDOUT_FORMULATION in splits
    assert HoldoutSplit.HOLDOUT_T in splits
    assert HoldoutSplit.HOLDOUT_FO2 in splits

    plan = default_holdout_plan()
    assert plan.held_out_formulation_family == "SiO2-Na2O"
    assert "IW+1" in plan.held_out_fo2_labels

    # Complete formulation family is held out.
    na_cells = [c for c in cells if c.formulation.family == "SiO2-Na2O"]
    assert na_cells
    assert all(c.split is HoldoutSplit.HOLDOUT_FORMULATION for c in na_cells)

    # Held-out T cells (that are not already formulation-holdout).
    t_hold = [
        c
        for c in cells
        if c.split is HoldoutSplit.HOLDOUT_T
    ]
    assert t_hold
    assert all(
        any(
            math.isclose(c.temperature_K, t, abs_tol=1e-9)
            for t in plan.held_out_temperatures_K
        )
        for c in t_hold
    )


# ---------------------------------------------------------------------------
# Censored observations + family caps + anchors
# ---------------------------------------------------------------------------


def test_censor_sub_floor_is_interval_not_point_or_log0():
    obs = censor_pressure(0.0, species="SiO")
    assert obs.kind is ObservationKind.CENSORED_SUB_FLOOR
    assert obs.pressure_Pa is None
    assert obs.log10_pressure_Pa is None
    assert obs.p_floor_Pa == DEFAULT_P_FLOOR_PA

    obs_neg = censor_pressure(-1.0, species="Fe")
    assert obs_neg.kind is ObservationKind.CENSORED_SUB_FLOOR
    assert obs_neg.log10_pressure_Pa is None

    obs_tiny = censor_pressure(DEFAULT_P_FLOOR_PA / 10.0, species="Na")
    assert obs_tiny.kind is ObservationKind.CENSORED_SUB_FLOOR

    # Floor itself is censored, never promoted to a point at the floor.
    obs_floor = censor_pressure(DEFAULT_P_FLOOR_PA, species="K")
    assert obs_floor.kind is ObservationKind.CENSORED_SUB_FLOOR


def test_censor_point_observation_emits_log10():
    obs = censor_pressure(1.0e-5, species="SiO")
    assert obs.kind is ObservationKind.POINT
    assert obs.pressure_Pa == pytest.approx(1.0e-5)
    assert obs.log10_pressure_Pa == pytest.approx(math.log10(1.0e-5))


def test_frozen_families_match_probe_po2_exponents_and_caps():
    expected = {
        "SiO": -0.5,
        "Fe": -0.5,
        "Mg": -0.5,
        "Na": -0.25,
        "K": -0.25,
        "O": 0.5,
        "O2": 1.0,
    }
    for species, exponent in expected.items():
        spec = FROZEN_ANALYTICAL_FAMILIES[species]
        assert spec.pO2_exponent == exponent
        assert spec.max_parameters == 3
        assert spec.coefficient_names == ("A", "B", "C")
        assert len(spec.coefficient_names) == spec.max_parameters


def test_independent_anchors_cover_every_calibration_species_and_never_certify():
    by_species = {a.species for a in DEFAULT_INDEPENDENT_ANCHORS}
    assert by_species == set(DEFAULT_CALIBRATION_SPECIES)
    assert all(a.may_certify is False for a in DEFAULT_INDEPENDENT_ANCHORS)
    # Distinct kinds present (not model-model only).
    kinds = {a.kind for a in DEFAULT_INDEPENDENT_ANCHORS}
    assert "kems" in kinds or "langmuir" in kinds
    assert "janaf_nist" in kinds or "pure_component" in kinds or "paper_wolfe" in kinds


def test_per_row_state_stays_pending_without_full_ladder():
    rows = build_per_row_states()
    assert rows
    assert all(r.validation_status is RowValidationState.PENDING for r in rows)
    assert all(r.flip_blockers for r in rows)
    assert all(r.independent_anchor_ids for r in rows)

    # Explicit promotion still blocked when ladder incomplete.
    blocked = build_per_row_states(promoted_validated={"SiO": True})
    sio = next(r for r in blocked if r.species == "SiO")
    assert sio.validation_status is RowValidationState.PENDING
    assert "promotion_blocked_by_ladder" in sio.flip_blockers


def test_per_row_state_validates_only_with_full_ladder():
    rows = build_per_row_states(
        holdout_accepted={"SiO": True},
        boundary_accepted={"SiO": True},
        error_budget_accepted={"SiO": True},
        promoted_validated={"SiO": True},
    )
    sio = next(r for r in rows if r.species == "SiO")
    assert sio.validation_status is RowValidationState.VALIDATED
    assert sio.may_flip_validated is True
    assert sio.flip_blockers == ()


# ---------------------------------------------------------------------------
# Error budget + boundary statistics
# ---------------------------------------------------------------------------


def test_error_budget_derivation_hkl_linear_and_limiting_cases():
    budget = derive_error_budget(0.30)
    assert budget.log10_pressure_threshold_dex == pytest.approx(
        math.log10(1.30)
    )
    assert "HKL" in budget.algebra
    assert "ε_J" in budget.limiting_check or "epsilon" in budget.limiting_check.lower()

    zero = derive_error_budget(0.0)
    assert zero.log10_pressure_threshold_dex == 0.0

    unit = derive_error_budget(1.0)
    assert unit.log10_pressure_threshold_dex == pytest.approx(math.log10(2.0))


def test_boundary_statistics_admissibility_vs_budget():
    budget = derive_error_budget(0.30)
    faces = boundary_faces_for_domain()
    assert any(f["boundary"] == "T_min" for f in faces)
    assert any(f["boundary"] == "T_max" for f in faces)
    assert any(f["boundary"] == "user_story_1300C" for f in faces)

    ok = evaluate_boundary_jumps(
        species="SiO",
        interior_log10_P=1.0,
        boundary_log10_P=1.0 + budget.log10_pressure_threshold_dex * 0.5,
        boundary="T_max",
        error_budget=budget,
    )
    assert ok.admissible is True
    assert ok.abs_delta_log10_P is not None

    bad = evaluate_boundary_jumps(
        species="SiO",
        interior_log10_P=1.0,
        boundary_log10_P=1.0 + budget.log10_pressure_threshold_dex * 2.0,
        boundary="T_max",
        error_budget=budget,
    )
    assert bad.admissible is False


# ---------------------------------------------------------------------------
# Progressive-validation report surface
# ---------------------------------------------------------------------------


def test_progressive_validation_report_has_required_sections():
    cells = build_calibration_cells()
    report = build_progressive_validation_report(
        calibration_id="vr10-test",
        cells=cells,
        include_rail_pending=True,
    )
    payload = report.as_dict()
    assert payload["calibration_id"] == "vr10-test"
    assert payload["certifies"] is False
    assert payload["authority"] == "diagnostic_only"
    assert payload["per_row_state"]
    assert payload["remaining_pending"]
    assert "fraction_selectable" in payload["source_selection_fractions"]
    assert "fraction_refused" in payload["source_selection_fractions"]
    assert "log10_pressure_threshold_dex" in payload["error_budget"]
    assert "boundary_statistics" in payload
    assert payload["cell_counts"]["planned_cells"] == len(cells)
    # JSON serializable.
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Warm pool only + research store + sidecar
# ---------------------------------------------------------------------------


def test_require_warm_pool_refuses_cold_backend():
    backend = VapoRockBackend()
    # Cold / unavailable.
    with pytest.raises(CalibrationRunnerError, match="not available"):
        require_warm_pool_backend(backend)

    backend._available = True
    backend._warm_pool = None
    with pytest.raises(CalibrationRunnerError, match="warm pool"):
        require_warm_pool_backend(backend)


def test_evaluate_cell_requires_warm_pool_and_censors(monkeypatch):
    backend = VapoRockBackend()
    backend._available = True
    backend._warm_pool = object()  # truthy stand-in

    class _Result:
        status = "ok"
        warnings = []
        vapor_pressures_Pa = {
            "SiO": 1.0e-4,
            "Fe": 0.0,  # censored
            "Na": DEFAULT_P_FLOOR_PA / 2.0,
        }

    def fake_equilibrate(**kwargs):
        assert "temperature_C" in kwargs
        return _Result()

    monkeypatch.setattr(backend, "equilibrate", fake_equilibrate)
    cells = build_calibration_cells()
    cell = cells[0]
    out = evaluate_cell(
        backend,
        cell,
        species=("SiO", "Fe", "Na"),
    )
    assert out["status"] == "ok"
    kinds = {o.species: o.kind for o in out["observations"]}
    assert kinds["SiO"] is ObservationKind.POINT
    assert kinds["Fe"] is ObservationKind.CENSORED_SUB_FLOOR
    assert kinds["Na"] is ObservationKind.CENSORED_SUB_FLOOR


def test_research_store_roundtrip_and_digest(tmp_path: Path):
    store_path = tmp_path / "cells.sqlite"
    cells = build_calibration_cells()[:3]
    with CalibrationResearchStore(store_path) as store:
        store.set_meta("calibration_id", "vr10-test")
        store.set_meta("warm_pool_only", "true")
        store.set_meta("cache_layer", "none")
        for cell in cells:
            obs = [
                censor_pressure(1.0e-6, species="SiO"),
                censor_pressure(0.0, species="Fe"),
            ]
            store.insert_cell(cell, status="ok", observations=obs)
        digest = store.digest()
        assert len(digest) == 64
        assert store.get_meta("calibration_id") == "vr10-test"
        assert store.get_meta("cache_layer") == "none"

    # Second open sees the same rows.
    with CalibrationResearchStore(store_path) as store:
        assert store.digest() == digest


def test_run_calibration_campaign_with_fake_warm_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    backend = VapoRockBackend()
    backend._available = True
    backend._warm_pool = object()

    class _Result:
        status = "ok"
        warnings: list[str] = []
        vapor_pressures_Pa = {sid: 1.0e-5 for sid in DEFAULT_CALIBRATION_SPECIES}

    monkeypatch.setattr(backend, "equilibrate", lambda **kw: _Result())
    # Tiny cell set for speed.
    cells = build_calibration_cells()[:4]
    report = run_calibration_campaign(
        store_path=tmp_path / "run.sqlite",
        calibration_id="vr10-fake",
        backend=backend,
        cells=cells,
        close_backend=False,
    )
    assert report.calibration_id == "vr10-fake"
    assert report.cell_counts["evaluated_cells"] == 4
    assert report.cell_counts["ok"] == 4
    assert report.per_row_state
    assert report.source_selection_fractions["fraction_selectable"] == pytest.approx(
        1.0
    )
    assert (tmp_path / "run.sqlite").is_file()


def test_sidecar_loader_accepts_checked_in_runtime_yaml():
    payload = load_vapour_rail_calibration_sidecar(DEFAULT_SIDECAR_PATH)
    assert payload["kind"] == "vapour_rail_calibration"
    assert payload["schema_version"] == 1
    assert payload["certifies"] is False
    assert payload["authority"] == "diagnostic_only"
    assert payload["raw_store"]["runtime_readable"] is False
    assert payload["performance"]["cache_layer"] in (None, "none", False)
    assert payload["performance"]["execution"] == "vaporock_warm_pool_only"
    assert set(payload["frozen_families"]) == set(DEFAULT_CALIBRATION_SPECIES)
    assert set(payload["parameter_caps"]) == set(DEFAULT_CALIBRATION_SPECIES)
    # All scaffold rows pending — no silent promotion.
    statuses = {r["validation_status"] for r in payload["per_row_state"]}
    assert statuses == {"pending_validation"}


def test_sidecar_rejects_runtime_readable_sqlite_and_cache_layer(tmp_path: Path):
    base = build_sidecar_document(
        calibration_id="bad",
        raw_store_digest=None,
        raw_store_path=None,
    )
    bad_runtime = dict(base)
    bad_runtime["raw_store"] = {
        **base["raw_store"],
        "runtime_readable": True,
    }
    path = tmp_path / "bad_runtime.yaml"
    path.write_text(yaml.safe_dump(bad_runtime))
    with pytest.raises(CalibrationSidecarError, match="runtime_readable"):
        load_vapour_rail_calibration_sidecar(path)

    bad_cache = dict(base)
    bad_cache["performance"] = {
        **base["performance"],
        "cache_layer": "vaporock_result_cache",
    }
    path2 = tmp_path / "bad_cache.yaml"
    path2.write_text(yaml.safe_dump(bad_cache))
    with pytest.raises(CalibrationSidecarError, match="cache"):
        load_vapour_rail_calibration_sidecar(path2)

    certifying = dict(base)
    certifying["certifies"] = True
    path3 = tmp_path / "cert.yaml"
    path3.write_text(yaml.safe_dump(certifying))
    with pytest.raises(CalibrationSidecarError, match="certify"):
        load_vapour_rail_calibration_sidecar(path3)


def test_write_sidecar_roundtrip(tmp_path: Path):
    doc = build_sidecar_document(
        calibration_id="vr10-roundtrip",
        raw_store_digest="abc",
        raw_store_path="/tmp/offline.sqlite",
    )
    path = tmp_path / "vapour_rail_calibration.yaml"
    write_sidecar(path, doc)
    loaded = load_vapour_rail_calibration_sidecar(path)
    assert loaded["calibration_id"] == "vr10-roundtrip"
    assert loaded["raw_store"]["digest"] == "abc"
    assert loaded["raw_store"]["runtime_readable"] is False


def test_runtime_sidecar_loader_source_does_not_open_sqlite():
    source = inspect.getsource(load_vapour_rail_calibration_sidecar)
    assert "sqlite3" not in source
    assert "CalibrationResearchStore" not in source
    assert_no_runtime_sqlite_reader(source_text=source)


def test_no_new_cache_layer_tokens_in_calibration_module():
    source = Path(calib.__file__).read_text()
    # The module must document the ban, not implement a cache.
    assert "no VapoRock result/calibration cache" in source or (
        "No VapoRock result" in source
    )
    forbidden_impl = (
        "class VapoRockResultCache",
        "class CalibrationCache",
        "lru_cache(maxsize",
        "RESULT_CACHE",
    )
    for token in forbidden_impl:
        assert token not in source, f"forbidden cache token {token!r}"


# ---------------------------------------------------------------------------
# CLI smoke (report-only, no VapoRock)
# ---------------------------------------------------------------------------


def test_cli_report_only_writes_artifacts(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "vapour_rail_calibration_runner",
        ROOT / "scripts" / "vapour_rail_calibration_runner.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    out = tmp_path / "report"
    draft = tmp_path / "draft.yaml"
    rc = mod.main(
        [
            "--report-only",
            "--calibration-id",
            "vr10-cli-smoke",
            "--output-dir",
            str(out),
            "--write-draft-sidecar",
            str(draft),
        ]
    )
    assert rc == 0
    assert (out / "progressive_validation_report.json").is_file()
    assert (out / "progressive_validation_report.md").is_file()
    payload = json.loads((out / "progressive_validation_report.json").read_text())
    assert payload["calibration_id"] == "vr10-cli-smoke"
    assert payload["per_row_state"]
    assert "error_budget" in payload
    assert draft.is_file()
    loaded = load_vapour_rail_calibration_sidecar(draft)
    assert loaded["approval"] == "draft_unreviewed"


def test_package_exports_calibration_surface():
    import simulator.vapour_rail as vr

    assert hasattr(vr, "load_vapour_rail_calibration_sidecar")
    assert hasattr(vr, "build_progressive_validation_report")
    assert hasattr(vr, "FROZEN_ANALYTICAL_FAMILIES")
