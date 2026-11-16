from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
import sys

from simulator.battery import migrate


def _decode_store_file(path: Path, *, work_file: bool) -> object:
    document = migrate.load_yaml(path)
    if work_file:
        if not isinstance(document, Mapping) or "work" not in document:
            return None
        work = migrate.work_from_plain(document["work"])
        experiments = tuple(
            migrate.experiment_from_plain(raw)
            for raw in (document.get("experiments") or ())
        )
        return work, experiments
    if not isinstance(document, Mapping):
        return ()
    return tuple(
        migrate.observation_from_plain(raw)
        for raw in (document.get("observations") or ())
    )


def test_store_record_decode_reads_only_store_file_and_python_sources(
    monkeypatch,
) -> None:
    """Pin file-read dependencies before decoded records are cached."""
    root = Path(__file__).resolve().parents[2]
    samples = (
        (
            next(
                path
                for path in migrate.iter_work_store_paths(
                    root / "data" / "literature" / "works"
                )
                if path.name != "ALIASES.yaml"
            ),
            True,
        ),
        (
            migrate.iter_observation_store_paths(
                root / "data" / "literature" / "extracts-v2"
            )[0],
            False,
        ),
        (
            migrate.iter_observation_store_paths(
                root / "data" / "literature" / "observations-v2"
            )[0],
            False,
        ),
    )
    monkeypatch.setenv("REGOLITH_PARSED_YAML_CACHE", "off")
    captured: list[Path] = []
    capture_reads = False

    def audit(event: str, arguments: tuple[object, ...]) -> None:
        if not capture_reads or event != "open" or not arguments:
            return
        opened = arguments[0]
        if isinstance(opened, bytes):
            opened = os.fsdecode(opened)
        if isinstance(opened, (str, os.PathLike)):
            captured.append(Path(opened).resolve())

    sys.addaudithook(audit)
    for store_path, work_file in samples:
        captured.clear()
        capture_reads = True
        try:
            _decode_store_file(store_path, work_file=work_file)
        finally:
            capture_reads = False

        expected_store_path = store_path.resolve()
        unexpected = [
            path
            for path in captured
            if path != expected_store_path and path.suffix != ".py"
        ]
        assert expected_store_path in captured
        assert not unexpected, (
            f"decoder for {store_path} read non-source data files: {unexpected}"
        )
