from __future__ import annotations

import shutil

import pytest

from simulator.corpus_paths import regolith_corpus_root
from simulator.diagnostic_helpers.sauerborn_2005 import (
    SAUERBORN_FIG555_FILES,
    SauerbornCorpusError,
    load_sauerborn_fig555_path_series,
)


def _require_corpus_files() -> None:
    corpus_root = regolith_corpus_root()
    missing = [
        str(corpus_root / relative_path)
        for relative_path, _ in SAUERBORN_FIG555_FILES.values()
        if not (corpus_root / relative_path).is_file()
    ]
    if missing:
        pytest.skip(
            "Sauerborn Fig. 5.55 corpus files are missing: "
            f"{', '.join(missing)}"
        )


def test_reader_loads_cao_path_without_subtracting_blank() -> None:
    _require_corpus_files()
    series = load_sauerborn_fig555_path_series()

    assert set(series) == {"CaO", "SiO2", "ZnO", "ohne_Probe"}
    cao_path = series["CaO"]
    assert all(row["point_type"].startswith("path_") for row in cao_path)

    flat_top = [row for row in cao_path if 65.0 <= row["time_s"] <= 65.6]
    assert flat_top
    assert min(row["time_s"] for row in flat_top) == pytest.approx(65.02, abs=0.01)
    assert max(row["time_s"] for row in flat_top) == pytest.approx(65.59, abs=0.01)
    assert all(
        row["value_mbar"] == pytest.approx(8.23e-6, rel=0.001)
        for row in flat_top
    )
    blank_peak = max(row["value_mbar"] for row in series["ohne_Probe"])
    assert blank_peak < flat_top[0]["value_mbar"]
    after_top = next(row["value_mbar"] for row in cao_path if row["time_s"] > 68.0)
    assert after_top < flat_top[0]["value_mbar"]


@pytest.mark.parametrize("trace", tuple(SAUERBORN_FIG555_FILES))
def test_reader_rejects_a_changed_corpus_sha256(tmp_path, trace: str) -> None:
    _require_corpus_files()
    source_root = regolith_corpus_root()
    for relative_path, _ in SAUERBORN_FIG555_FILES.values():
        source = source_root / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    relative_path, _ = SAUERBORN_FIG555_FILES[trace]
    mutated_path = tmp_path / relative_path
    mutated_path.write_bytes(mutated_path.read_bytes() + b"\n")

    with pytest.raises(SauerbornCorpusError, match=f"SHA-256 mismatch for {trace}"):
        load_sauerborn_fig555_path_series(tmp_path)
