"""IMCC-only empirical residual runner."""

from __future__ import annotations

import contextlib
import io
import json
import math
import sys
from pathlib import Path

import yaml

from simulator.melt_backend.imcc_sf04.bench import (
    _single_cation_gas_activities,
    composition_wt_pct_for_point,
    load_bench_set,
    main,
    render_report,
    run_bench,
)

DATAPACK_PATH = Path("data/melt_activity/imcc/imcc-sf04-v1.0.2.json")

# In-domain CMAS slag, 8-parent IMCC basis (missing parents are zero).
_CMAS_WT = {
    "SiO2": 50.0,
    "CaO": 20.0,
    "MgO": 15.0,
    "Al2O3": 15.0,
}


def _write_fixture(path: Path, points: list[dict], compositions: dict | None = None) -> Path:
    payload = {
        "schema_version": "melt-activity-bench.v1",
        "title": "inline IMCC bench runner fixture",
        "compositions": compositions
        or {
            "cmas_in_domain": {
                "material_class": "cmas_slag",
                "composition_wt_pct": dict(_CMAS_WT),
            }
        },
        "points": points,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _three_point_fixture(path: Path) -> Path:
    return _write_fixture(
        path,
        [
            {
                "id": "ok_sio2_1800",
                "population": "inline_ok",
                "composition_id": "cmas_in_domain",
                "material_class": "cmas_slag",
                "temperature_K": 1800.0,
                "parent_oxide": "SiO2",
                "species": "SiO",
                "observable": "activity",
                "measured": 0.2,
                "units": "dimensionless",
                "score": True,
                "convention": (
                    "a(SiO2) on the parent-oxide formula-unit basis against "
                    "a pure-liquid-SiO2 standard state"
                ),
            },
            {
                "id": "ood_t500",
                "population": "inline_ood",
                "composition_id": "cmas_in_domain",
                "material_class": "cmas_slag",
                "temperature_K": 500.0,
                "parent_oxide": "SiO2",
                "species": "SiO",
                "observable": "activity",
                "measured": 0.2,
                "units": "dimensionless",
                "score": True,
                "convention": "out-of-domain temperature probe",
            },
            {
                "id": "refused_held",
                "population": "inline_refused",
                "composition_id": "cmas_in_domain",
                "material_class": "cmas_slag",
                "temperature_K": 1800.0,
                "parent_oxide": "SiO2",
                "species": "SiO",
                "observable": "activity",
                "measured": 0.2,
                "units": "dimensionless",
                "score": False,
                "dropped_reason": "held from scoring for the unit test",
                "convention": "held point; must not enter RMSE",
            },
        ],
    )


def test_load_bench_set_preserves_yaml_12_chemistry_identifiers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bench.yaml"
    path.write_text(
        """\
schema_version: melt-activity-bench.v1
compositions:
  no:
    composition_wt_pct:
      SiO2: 100.0
points:
  - id: no
    composition_id: no
    species: NO
    enabled: true
""",
        encoding="utf-8",
    )

    fixture = load_bench_set(path)
    point = fixture["points"][0]

    assert "no" in fixture["compositions"]
    assert point["id"] == "no"
    assert point["composition_id"] == "no"
    assert point["species"] == "NO"
    assert point["enabled"] is True
    assert composition_wt_pct_for_point(point, fixture["compositions"]) == {
        "SiO2": 100.0
    }


def test_run_bench_ok_out_of_domain_and_refused(tmp_path: Path) -> None:
    fixture = _three_point_fixture(tmp_path / "bench.yaml")
    report = run_bench(fixture, DATAPACK_PATH)

    by_id = {row.point_id: row for row in report.points}
    assert set(by_id) == {"ok_sio2_1800", "ood_t500", "refused_held"}

    ok = by_id["ok_sio2_1800"]
    assert ok.status == "ok"
    assert ok.predicted is not None and ok.predicted > 0.0
    assert ok.residual == math.log10(ok.predicted / ok.measured)
    assert ok.ratio == ok.predicted / ok.measured

    ood = by_id["ood_t500"]
    assert ood.status == "out_of_domain"
    assert ood.predicted is None
    assert ood.residual is None
    assert "outside the declared domain" in ood.reason

    refused = by_id["refused_held"]
    assert refused.status == "refused"
    assert refused.residual is None
    assert "held from scoring" in refused.reason


def test_refused_point_is_counted_and_excluded_from_rmse(tmp_path: Path) -> None:
    fixture = _three_point_fixture(tmp_path / "bench.yaml")
    report = run_bench(fixture, DATAPACK_PATH)

    assert report.n == 3
    assert report.n_ok == 1
    assert report.status_counts["ok"] == 1
    assert report.status_counts["out_of_domain"] == 1
    assert report.status_counts["refused"] == 1
    assert report.status_counts["not_converged"] == 0
    assert report.status_counts["unsupported_observable"] == 0

    ok = next(row for row in report.points if row.status == "ok")
    assert ok.residual is not None
    assert report.rmse == math.sqrt(ok.residual * ok.residual)
    assert report.median_abs_residual == abs(ok.residual)

    refused = next(row for row in report.points if row.status == "refused")
    assert refused.residual is None
    # Flattening RMSE by dropping the refusal would require it not to appear
    # in N / status_counts; both stay visible.
    assert report.n == report.n_ok + report.status_counts["refused"] + report.status_counts[
        "out_of_domain"
    ]


def test_unsupported_observable_is_not_coerced(tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path / "bench.yaml",
        [
            {
                "id": "flux_not_coerced",
                "population": "inline_unsupported",
                "composition_id": "cmas_in_domain",
                "material_class": "cmas_slag",
                "temperature_K": 1800.0,
                "parent_oxide": "SiO2",
                "species": "SiO",
                "observable": "evaporation_flux",
                "measured": 1.0e-6,
                "units": "mol_m-2_s-1",
                "score": True,
                "convention": "must not be treated as activity or pressure",
            }
        ],
    )
    report = run_bench(fixture, DATAPACK_PATH)
    assert report.n == 1
    assert report.n_ok == 0
    assert report.points[0].status == "unsupported_observable"
    assert report.points[0].predicted is None
    assert report.points[0].residual is None
    assert report.rmse is None
    assert report.status_counts["unsupported_observable"] == 1


def test_activity_prediction_matches_harness_imcc_engine(tmp_path: Path) -> None:
    from benchmarks.melt_activity_benchmark import ImccEngine, _prediction_for_point

    fixture = _three_point_fixture(tmp_path / "bench.yaml")
    report = run_bench(fixture, DATAPACK_PATH)
    ok = next(row for row in report.points if row.point_id == "ok_sio2_1800")

    engine = ImccEngine("imcc-published", DATAPACK_PATH, published=True)
    result = engine.evaluate(_CMAS_WT, 1800.0, None)
    predicted, reason = _prediction_for_point(
        {
            "observable": "activity",
            "parent_oxide": "SiO2",
            "species": "SiO",
            "temperature_K": 1800.0,
            "measured": 0.2,
            "composition_wt_pct": _CMAS_WT,
        },
        result,
    )
    assert reason == ""
    assert predicted is not None
    assert ok.predicted == predicted
    assert ok.residual == math.log10(predicted / 0.2)


def test_single_cation_conversion_matches_harness() -> None:
    from benchmarks.melt_activity_benchmark import _single_cation_gas_activities as harness_convert

    parent = {"SiO2": 0.25, "Al2O3": 0.04, "Na2O": 1.0e-8, "K2O": 4.0e-10, "MgO": 0.1}
    assert _single_cation_gas_activities(parent) == harness_convert(parent)


def test_filters_and_limit(tmp_path: Path) -> None:
    fixture = _three_point_fixture(tmp_path / "bench.yaml")
    only_ok = run_bench(fixture, DATAPACK_PATH, populations=["inline_ok"])
    assert only_ok.n == 1
    assert only_ok.points[0].point_id == "ok_sio2_1800"

    limited = run_bench(fixture, DATAPACK_PATH, limit=2)
    assert limited.n == 2
    assert [row.point_id for row in limited.points] == ["ok_sio2_1800", "ood_t500"]

    by_species = run_bench(fixture, DATAPACK_PATH, species=["SiO"])
    assert by_species.n == 3


def test_text_and_json_render(tmp_path: Path) -> None:
    fixture = _three_point_fixture(tmp_path / "bench.yaml")
    report = run_bench(fixture, DATAPACK_PATH)
    text = render_report(report)
    assert "N=3" in text
    assert "N_ok=1" in text
    assert "refused=1" in text
    assert "out_of_domain=1" in text
    assert "ok_sio2_1800" in text

    stdout = io.StringIO()
    rc = main(
        [str(fixture), str(DATAPACK_PATH), "--json"],
        out=stdout,
    )
    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["n"] == 3
    assert payload["n_ok"] == 1
    assert payload["status_counts"]["refused"] == 1
    ids = {row["point_id"] for row in payload["points"]}
    assert ids == {"ok_sio2_1800", "ood_t500", "refused_held"}


class _BlockImport:
    """Meta-path finder that makes a package look uninstalled.

    Raising from ``find_spec`` (rather than returning None) is what makes the
    import fail instead of falling through to the real finders.
    """

    def __init__(self, prefixes: tuple[str, ...]) -> None:
        self._prefixes = prefixes

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102, ANN001
        for prefix in self._prefixes:
            if fullname == prefix or fullname.startswith(prefix + "."):
                raise ImportError(
                    f"{fullname} blocked: simulating an extracted checkout"
                )
        return None


@contextlib.contextmanager
def _without_the_simulator_gas_layer():
    """Approximate a standalone IMCC checkout: no simulator policy importable.

    Already-imported submodules are evicted from ``sys.modules`` first, or the
    deferred imports would be satisfied from cache and the block would be a
    no-op that quietly passes.
    """
    blocked = ("simulator.accounting", "simulator.diagnostic_helpers")
    saved = {k: v for k, v in sys.modules.items() if k.startswith(blocked)}
    for key in saved:
        del sys.modules[key]
    finder = _BlockImport(blocked)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


def _gas_point(**overrides) -> dict:
    point = {
        "id": "pp_sio_1800",
        "population": "inline_gas",
        "composition_id": "cmas_in_domain",
        "material_class": "cmas_slag",
        "temperature_K": 1800.0,
        "parent_oxide": "SiO2",
        "species": "SiO",
        "observable": "partial_pressure",
        "measured": 1.0,
        "units": "Pa",
        "score": True,
        "fO2_bar": 1.0e-10,
        "convention": "p(SiO) against an independent fO2 pin",
    }
    point.update(overrides)
    return point


def test_bench_still_runs_without_the_simulator_gas_layer(tmp_path: Path) -> None:
    """The extraction contract, tested by behaviour rather than by AST.

    A standalone IMCC checkout has no ``simulator.accounting`` or
    ``simulator.diagnostic_helpers``. The activity path is the engine's own
    output and must keep scoring; the partial_pressure path borrows the
    simulator's shared gas layer and must degrade to a TYPED REFUSAL -- a
    traceback there would make the package unusable once lifted out.

    The control matters: the same gas point scores ``ok`` with the layer
    present (see ``test_gas_point_scores_when_the_layer_is_present``), so a
    refusal here is caused by the block and not by the fixture being unscorable
    for some unrelated reason.
    """
    fixture = _write_fixture(
        tmp_path / "bench.yaml",
        [
            {
                "id": "act_sio2_1800",
                "population": "inline_ok",
                "composition_id": "cmas_in_domain",
                "material_class": "cmas_slag",
                "temperature_K": 1800.0,
                "parent_oxide": "SiO2",
                "species": "SiO",
                "observable": "activity",
                "measured": 0.2,
                "units": "dimensionless",
                "score": True,
                "convention": "a(SiO2), parent-oxide formula-unit basis",
            },
            _gas_point(),
        ],
    )

    with _without_the_simulator_gas_layer():
        report = run_bench(fixture, DATAPACK_PATH)

    by_id = {row.point_id: row for row in report.points}

    activity_row = by_id["act_sio2_1800"]
    assert activity_row.status == "ok", (
        "the activity path must not depend on simulator policy; "
        f"got {activity_row.status}: {activity_row.reason}"
    )
    assert activity_row.predicted is not None
    assert math.isfinite(float(activity_row.predicted))

    gas_row = by_id["pp_sio_1800"]
    assert gas_row.status == "refused"
    assert gas_row.predicted is None
    assert "shared gas layer refused" in gas_row.reason

    # And the refusal stays out of the score, like every other refusal.
    assert report.n_ok == 1
    assert report.status_counts["refused"] == 1


def test_gas_point_scores_when_the_layer_is_present(tmp_path: Path) -> None:
    """Control for the test above -- without it, a refusal proves nothing."""
    fixture = _write_fixture(tmp_path / "bench.yaml", [_gas_point()])
    report = run_bench(fixture, DATAPACK_PATH)
    row = report.points[0]
    assert row.status == "ok", f"control point should score in-tree: {row.reason}"
    assert row.predicted is not None and row.predicted > 0.0
