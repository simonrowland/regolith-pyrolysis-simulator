"""Wolf et al. 2022 VapoRock self-parity anchors.

This cohort validates the local VapoRock adapter against VapoRock's own
published BSE model output. It intentionally calls the adapter directly rather
than the simulator evaporation path: this is a wiring/unit/fO2-convention
anchor for ``simulator/melt_backend/vaporock.py``.
"""

from __future__ import annotations

import math
from pathlib import Path
import warnings

import pytest
import yaml

from simulator.melt_backend.vaporock import VapoRockBackend


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPO_ROOT
    / "docs-private"
    / "deep-research"
    / "literature"
    / "wolf-2022-vaporock"
    / "benchmark-fixture.yaml"
)


def _load_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        return {}
    return yaml.safe_load(FIXTURE_PATH.read_text()) or {}


def _wolf_anchors() -> list[dict]:
    data = _load_fixture()
    expected = data.get("expected") or {}
    pressure_block = expected.get("vapor_partial_pressures_Pa") or {}
    anchors = pressure_block.get("anchors") or []
    return [anchor for anchor in anchors if isinstance(anchor, dict)]


def _composition_wt_pct() -> dict[str, float]:
    data = _load_fixture()
    feedstock = data.get("feedstock") or {}
    composition = feedstock.get("composition_wt_pct") or {}
    return {
        str(species): float(value)
        for species, value in composition.items()
        if float(value) > 0.0
    }


def _available_backend_or_skip() -> VapoRockBackend:
    backend = VapoRockBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("VapoRock optional dependency unavailable")
    return backend


def _error_decades(observed: float, expected: float) -> float:
    if observed <= 0.0 or expected <= 0.0:
        return math.inf
    return abs(math.log10(observed / expected))


def _format_species_result(
    species: str,
    observed: float | None,
    expected: float,
    error_decades: float,
    tolerance_decades: float,
) -> str:
    observed_text = "missing" if observed is None else f"{observed:.6e}"
    status = "pass" if error_decades <= tolerance_decades else "model-spread"
    return (
        f"{species}: status={status}, observed={observed_text} Pa, "
        f"expected={expected:.6e} Pa, err={error_decades:.3f} dec, "
        f"tol={tolerance_decades:.3f} dec"
    )


def test_wolf2022_fixture_has_real_model_anchors():
    anchors = _wolf_anchors()
    assert len(anchors) >= 3
    assert _composition_wt_pct()
    for anchor in anchors:
        assert anchor.get("T_K") is not None
        assert anchor.get("fO2", {}).get("fO2_log10_bar") is not None
        assert anchor.get("vapor_partial_pressures_Pa")
        assert anchor.get("read_method") == (
            "paper-source-csv-log10-bar-converted-to-Pa"
        )


@pytest.mark.parametrize(
    "anchor",
    _wolf_anchors(),
    ids=lambda anchor: str(anchor.get("anchor_id", "wolf-anchor")),
)
def test_vaporock_adapter_reproduces_wolf2022_bse_model_output(anchor: dict):
    backend = _available_backend_or_skip()
    composition_wt_pct = _composition_wt_pct()
    expected_pressures = {
        str(species): float(value)
        for species, value in (
            anchor.get("vapor_partial_pressures_Pa") or {}
        ).items()
    }
    tolerance_decades = float(anchor.get("tolerance_decades", 0.3))
    fO2_log = float(anchor["fO2"]["fO2_log10_bar"])
    T_K = float(anchor["T_K"])

    result = backend.equilibrate(
        T_K - 273.15,
        composition_kg=composition_wt_pct,
        fO2_log=fO2_log,
        pressure_bar=1e-12,
    )
    if result.status != "ok" or not result.vapor_pressures_Pa:
        pytest.fail(
            f"{anchor.get('anchor_id')}: blocked; adapter status="
            f"{result.status!r}, warnings={result.warnings!r}"
        )

    failures: list[str] = []
    details: list[str] = []
    for species, expected in sorted(expected_pressures.items()):
        observed = result.vapor_pressures_Pa.get(species)
        error = (
            math.inf
            if observed is None
            else _error_decades(float(observed), expected)
        )
        detail = _format_species_result(
            species,
            None if observed is None else float(observed),
            expected,
            error,
            tolerance_decades,
        )
        details.append(detail)
        if error > tolerance_decades:
            failures.append(detail)

    assert not failures, (
        f"{anchor.get('anchor_id')}: VapoRock adapter diverges from "
        f"Wolf 2022 source-data model output at fO2_log={fO2_log:.6f}. "
        "This is an adapter validation failure, not a tolerance target.\n"
        + "\n".join(details)
    )
