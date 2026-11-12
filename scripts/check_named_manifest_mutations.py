#!/usr/bin/env python3
"""Verify each named-manifest test reports removed and added identities."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ManifestCase:
    name: str
    path: Path
    kind: Literal["jsonl", "yaml"]
    identity_key: str
    test_nodeid: str


CASES = (
    ManifestCase(
        "extract census",
        ROOT / "tests/manifests/literature_extract_source_ids.jsonl",
        "jsonl",
        "identity",
        "tests/test_corpus_extract_source_on_file.py::test_green_extract_source_id_count",
    ),
    ManifestCase(
        "Fegley Table 2",
        ROOT / "tests/manifests/fegley_2023_table_2_printed_rows.jsonl",
        "jsonl",
        "identity",
        "tests/test_activity_coefficient_temperature_fit.py::test_fegley_2023_table_2_printed_rows_are_pinned",
    ),
    ManifestCase(
        "Stolyarova Table 3",
        ROOT / "tests/manifests/stolyarova_table3_reference_states.jsonl",
        "jsonl",
        "identity",
        "tests/battery/test_migrate.py::test_stolyarova_table3_reference_states_are_stable",
    ),
    ManifestCase(
        "JANAF table paths",
        ROOT / "data/literature/compilations/janaf/manifest.yaml",
        "yaml",
        "table_id",
        "tests/test_janaf_compilation.py::test_every_manifest_entry_carries_formula_as_published",
    ),
    ManifestCase(
        "ATcT source records",
        ROOT / "data/literature/compilations/atct/manifest.yaml",
        "yaml",
        "record_id",
        "tests/test_atct_compilation.py::test_every_source_row_round_trips",
    ),
)


def _read(case: ManifestCase) -> tuple[Any, list[dict[str, Any]]]:
    if case.kind == "jsonl":
        entries = [
            json.loads(line)
            for line in case.path.read_text(encoding="utf-8").splitlines()
        ]
        if any(
            not isinstance(entry, dict)
            or set(entry) != {"identity", "value"}
            or not isinstance(entry["identity"], str)
            or not entry["identity"]
            for entry in entries
        ):
            raise ValueError(f"{case.path}: malformed JSONL named manifest")
        return None, entries

    payload = yaml.safe_load(case.path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"{case.path}: expected a YAML manifest with entries")
    entries = payload["entries"]
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get(case.identity_key), str)
        or not entry[case.identity_key]
        for entry in entries
    ):
        raise ValueError(f"{case.path}: malformed {case.identity_key} entries")
    return payload, entries


def _write(case: ManifestCase, payload: Any, entries: list[dict[str, Any]]) -> None:
    entries.sort(key=lambda entry: entry[case.identity_key])
    if case.kind == "jsonl":
        case.path.write_text(
            "".join(
                json.dumps(
                    entry,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
                for entry in entries
            ),
            encoding="utf-8",
        )
        return

    payload["entries"] = entries
    case.path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _run_test(
    case: ManifestCase,
    identity: str,
    mutation: str,
    *,
    env: dict[str, str] | None = None,
) -> bool:
    test_env = None if env is None else {**os.environ, **env}
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", case.test_nodeid],
            cwd=ROOT,
            env=test_env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"  FAIL {mutation}: pytest timed out")
        return False

    output = result.stdout + result.stderr
    if mutation.startswith("add"):
        expected_rows = f"added rows: ['{identity}']"
    elif mutation.startswith("remove"):
        expected_rows = f"removed rows: ['{identity}']"
    else:
        print(f"  FAIL {mutation}: unsupported mutation kind")
        return False
    named_failure = (
        result.returncode != 0
        and identity in output
        and "differs from its named manifest" in output
        and expected_rows in output
    )
    if named_failure:
        print(f"  PASS {mutation}: failure named {identity}")
        return True

    print(
        f"  FAIL {mutation}: expected a named-row failure for {identity}; "
        f"pytest exit={result.returncode}"
    )
    print("\n".join(output.splitlines()[-12:]))
    return False


def _check_case(case: ManifestCase) -> bool:
    if not case.path.is_file():
        print(f"FAIL {case.name}: manifest not found: {case.path}")
        return False

    ok = True
    with tempfile.TemporaryDirectory(prefix="named-manifest-mutation-") as scratch:
        backup = Path(scratch) / case.path.name
        shutil.copy2(case.path, backup)
        try:
            payload, entries = _read(case)
            if len(entries) < 2:
                raise ValueError(f"{case.path}: need at least two rows for mutation checks")

            removed = entries.pop(len(entries) // 2)
            removed_identity = removed[case.identity_key]
            _write(case, payload, entries)
            # Removing an expected identity simulates an extra source row.
            ok &= _run_test(case, removed_identity, "add source row")

            shutil.copy2(backup, case.path)
            payload, entries = _read(case)
            identity = "__mutation_added_row__"
            existing = {entry[case.identity_key] for entry in entries}
            suffix = 1
            while identity in existing:
                identity = f"__mutation_added_row_{suffix}__"
                suffix += 1
            if case.kind == "jsonl":
                fake = {"identity": identity, "value": None}
            else:
                fake = dict(entries[0])
                fake[case.identity_key] = identity
            entries.append(fake)
            _write(case, payload, entries)
            # Adding an expected identity simulates a missing source row.
            ok &= _run_test(case, identity, "remove source row")

            if case.name == "ATcT source records":
                shutil.copy2(backup, case.path)
                records_dir = case.path.parent / "records"
                extra_stem = "__mutation_extra_record_file__"
                extra_path = records_dir / f"{extra_stem}.json"
                suffix = 1
                while extra_path.exists():
                    extra_stem = f"__mutation_extra_record_file_{suffix}__"
                    extra_path = records_dir / f"{extra_stem}.json"
                    suffix += 1
                try:
                    extra_path.write_text("{}\n", encoding="utf-8")
                    extra_identity = extra_path.relative_to(
                        case.path.parent
                    ).as_posix()
                    ok &= _run_test(case, extra_identity, "add record file")
                finally:
                    extra_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  FAIL {case.name}: {type(exc).__name__}: {exc}")
            ok = False
        finally:
            shutil.copy2(backup, case.path)

    print(f"{'PASS' if ok else 'FAIL'} {case.name}")
    return ok


def _check_janaf_raw_file_mutations() -> bool:
    """Exercise the raw JANAF file mismatch before the index path assertions."""
    configured_root = os.environ.get("REGOLITH_CORPUS_ROOT")
    if not configured_root:
        print(
            "SKIP JANAF raw files: corpus not mounted "
            "(REGOLITH_CORPUS_ROOT is unset)"
        )
        return True

    corpus_root = Path(configured_root).expanduser()
    source_dir = corpus_root / "raw" / "janaf-nist-txt"
    if not source_dir.is_dir():
        print(
            "SKIP JANAF raw files: corpus not mounted "
            f"({source_dir} is absent)"
        )
        return True

    source_files = sorted(source_dir.glob("*.txt"))
    if not source_files:
        print(f"FAIL JANAF raw files: no .txt files in {source_dir}")
        return False

    case = ManifestCase(
        "JANAF raw file paths",
        source_dir,
        "yaml",
        "table_id",
        "tests/test_literature_index.py::test_nist_janaf_index_asset_is_the_corpus_txt_download",
    )
    ok = True
    with tempfile.TemporaryDirectory(prefix="janaf-raw-file-mutation-") as scratch:
        temp_corpus = Path(scratch) / "corpus"
        temp_dir = temp_corpus / "raw" / "janaf-nist-txt"
        temp_dir.parent.mkdir(parents=True)
        shutil.copytree(source_dir, temp_dir)
        env = {"REGOLITH_CORPUS_ROOT": str(temp_corpus)}

        removed_path = temp_dir / source_files[0].name
        removed_bytes = removed_path.read_bytes()
        try:
            removed_path.unlink()
            ok &= _run_test(case, removed_path.stem, "remove raw file", env=env)
        finally:
            removed_path.write_bytes(removed_bytes)

        fake_stem = "__mutation_added_row__"
        fake_path = temp_dir / f"{fake_stem}.txt"
        suffix = 1
        while fake_path.exists():
            fake_stem = f"__mutation_added_row_{suffix}__"
            fake_path = temp_dir / f"{fake_stem}.txt"
            suffix += 1
        try:
            fake_path.write_text("named-manifest mutation probe\n", encoding="utf-8")
            ok &= _run_test(case, fake_stem, "add raw file", env=env)
        finally:
            fake_path.unlink(missing_ok=True)

    print(f"{'PASS' if ok else 'FAIL'} JANAF raw file paths")
    return ok


def main() -> int:
    results = [_check_case(case) for case in CASES]
    results.append(_check_janaf_raw_file_mutations())
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
