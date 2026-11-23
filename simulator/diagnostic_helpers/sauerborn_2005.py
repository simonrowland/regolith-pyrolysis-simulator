"""Pinned Sauerborn (2005) Fig. 5.55 traces from corpus snapshot a820a14c7."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from simulator.corpus_paths import regolith_corpus_root
from simulator.diagnostic_helpers.reproduction_compare import (
    ComparisonRecord,
    TimeSeriesComparisonRefusal,
    _compare_pointwise_observations,
    _normalise_observation_points,
    _normalise_runtime_samples,
)


SAUERBORN_NORMALIZED_BASE_PRESSURE_MBAR = 1e-7
SAUERBORN_FIG555_FILES = {
    "CaO": (
        "tables/kems-035-sauerborn-2005/fig5-55-CaO.csv",
        "ca13182ab4c5f881d983eaa506eca68dbaeb00aa7701f48f055527ec28cabd79",
    ),
    "SiO2": (
        "tables/kems-035-sauerborn-2005/fig5-55-SiO2.csv",
        "0be4b2d57f805d5bf0faedd023475dcabb6ef2c8f9990c0cc7f9d13c5fb377bb",
    ),
    "ZnO": (
        "tables/kems-035-sauerborn-2005/fig5-55-ZnO.csv",
        "847948c13bf1b8f99c230453d67b52ab3c34d028451ec0e5e860cf2b39290582",
    ),
    "ohne_Probe": (
        "tables/kems-035-sauerborn-2005/fig5-55-ohne_Probe.csv",
        "f999729173846d0d1383d882a03af1c1bec7979b9bda061baa67f1ca51b0950b",
    ),
}


class SauerbornCorpusError(ValueError):
    """Raised when a pinned Sauerborn Fig. 5.55 file cannot be read."""


def load_sauerborn_fig555_path_series(
    corpus_root: str | Path | None = None,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Load pinned path rows as elapsed seconds and partial pressure in mbar."""

    root = regolith_corpus_root() if corpus_root is None else Path(corpus_root)
    series: dict[str, tuple[dict[str, Any], ...]] = {}
    for trace, (relative_path, expected_sha256) in SAUERBORN_FIG555_FILES.items():
        path = root / relative_path
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise SauerbornCorpusError(
                f"required Sauerborn Fig. 5.55 file is unavailable: {path}"
            ) from exc

        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise SauerbornCorpusError(
                f"Sauerborn Fig. 5.55 SHA-256 mismatch for {trace}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

        try:
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""))
            path_rows = []
            for row in reader:
                point_type = str(row.get("point_type") or "")
                if not point_type.startswith("path_"):
                    continue
                if row.get("trace") != trace or row.get("units") != "mbar":
                    raise SauerbornCorpusError(
                        f"unexpected trace or units in Sauerborn Fig. 5.55 file: {path}"
                    )
                path_rows.append(
                    {
                        "time_s": float(row["time_s"]),
                        "value_mbar": float(row["value"]),
                        "point_type": point_type,
                        "time_half_width_s": float(row["time_half_width_s"]),
                        "value_lower_mbar": float(row["value_lower"]),
                        "value_upper_mbar": float(row["value_upper"]),
                    }
                )
        except (csv.Error, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, SauerbornCorpusError):
                raise
            raise SauerbornCorpusError(
                f"cannot parse Sauerborn Fig. 5.55 path rows from {path}: {exc}"
            ) from exc
        if not path_rows:
            raise SauerbornCorpusError(
                f"Sauerborn Fig. 5.55 file has no path rows: {path}"
            )
        series[trace] = tuple(path_rows)
    return series


def compare_sauerborn_o2_partial_pressure_time_series(
    *,
    case_id: str,
    source_id: str,
    observable_id: str,
    observation_points: Sequence[Mapping[str, Any]],
    runtime_pressure_samples: Sequence[Mapping[str, Any]] | None,
    pre_high_temperature_base_mbar: float,
    evidence_scope: str,
    source_locator: Mapping[str, Any] | str,
    recipe: Mapping[str, Any],
    observation: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> tuple[ComparisonRecord, ...]:
    """Normalize a QMS pressure trace by its own base, then compare points.

    Times are seconds and pressures are mbar. The pre-high-temperature base is
    supplied by the runtime; this function does not infer it from the peak.
    """

    if not runtime_pressure_samples:
        raise TimeSeriesComparisonRefusal(
            "qms_po2_series_missing",
            "runtime exposes no QMS-cross O2 partial-pressure samples",
        )

    base_pressure_mbar = float(pre_high_temperature_base_mbar)
    if not math.isfinite(base_pressure_mbar) or base_pressure_mbar <= 0.0:
        raise ValueError(
            "pre-high-temperature base pressure must be finite and positive"
        )
    scale = SAUERBORN_NORMALIZED_BASE_PRESSURE_MBAR / base_pressure_mbar

    normalized_runtime = []
    for index, sample in enumerate(runtime_pressure_samples):
        if not isinstance(sample, Mapping):
            raise ValueError(f"runtime_pressure_samples[{index}] must be a mapping")
        pressure_mbar = sample.get("value_mbar")
        normalized_runtime.append(
            {
                "time_s": float(sample["time_s"]),
                "value": (
                    None
                    if pressure_mbar is None
                    else float(pressure_mbar) * scale
                ),
            }
        )
    samples = _normalise_runtime_samples(
        normalized_runtime,
        coordinate_key="time_s",
        coordinate_unit="s",
    )

    normalized_observations = [
        {
            "time_s": float(point["time_s"]),
            "expected_value": point["value_mbar"],
            "expected_uncertainty": None,
            "source_locator": {
                "point_type": point["point_type"],
                "time_half_width_s": point["time_half_width_s"],
                "digitization_bounds_mbar": {
                    "lower": point["value_lower_mbar"],
                    "upper": point["value_upper_mbar"],
                },
            },
        }
        for point in observation_points
    ]
    observations = _normalise_observation_points(
        normalized_observations,
        coordinate_key="time_s",
        ungraded=True,
    )
    # p. 62 gives no per-point instrument uncertainty, so report residuals
    # without grading them as matches or mismatches.
    records, _ = _compare_pointwise_observations(
        observations,
        samples,
        case_id=case_id,
        source_id=source_id,
        observable_id=observable_id,
        species="O2",
        coordinate_key="time_s",
        units="mbar",
        evidence_scope=evidence_scope,
        source_locator=source_locator,
        recipe=recipe,
        observation=observation,
        runtime=runtime,
        coordinate_unit="s",
        ungraded=True,
    )
    return records
