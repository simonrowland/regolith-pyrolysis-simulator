from __future__ import annotations

import csv
import hashlib
from importlib.machinery import ModuleSpec
from pathlib import Path
import shutil
import sys
import textwrap
from types import ModuleType, SimpleNamespace

import pytest

from scripts.vapour_rail_sf04_high_t import (
    _vaporock_checkout_identity,
    compute_residual_rows,
    evaluate_vaporock,
    interpolate_log10_pressure,
    load_extract,
    pressure_series_by_species,
    write_csv,
)


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "literature"
    / "extracts"
    / "sf04-magma-companion-workbook.yaml"
)
RESIDUALS = (
    Path(__file__).resolve().parents[1]
    / "validation-data"
    / "vapour_rail_sf04_high_t_residuals.csv"
)
DECISION = (
    Path(__file__).resolve().parents[1]
    / "validation-data"
    / "vapour_rail_sf04_high_t_DECISION.md"
)
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "imcc_sf04_magma_workbook.csv"
)


class _FakeLoc:
    def __init__(self, value: float = -3.0) -> None:
        self.value = value

    def __getitem__(self, key: str) -> SimpleNamespace:
        return SimpleNamespace(iloc=[self.value])


class _FakeTable:
    def __init__(self, value: float = -3.0) -> None:
        self.loc = _FakeLoc(value)


def _write_fake_vaporock_inputs(package: Path, evaluation_body: str) -> Path:
    package.mkdir()
    (package / "data").mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    changing_file = package / "data" / "JANAF-vapor-data-full.csv"
    changing_file.write_text("original full data", encoding="utf-8")
    (package / "data" / "JANAF-vapor-data.csv").write_text(
        "original data", encoding="utf-8"
    )
    (package / "data" / "JANAF0-vapor-data.csv").write_text(
        "original zero data", encoding="utf-8"
    )
    (package / "chemistry.py").write_text("", encoding="utf-8")
    equil_source = f'''
from importlib import resources
from pathlib import Path
from types import SimpleNamespace


class _Loc:
    def __init__(self, value):
        self.value = value

    def __getitem__(self, key):
        return SimpleNamespace(iloc=[self.value])


class System:
    def __init__(self, *, vapor_database):
        assert vapor_database == "JANAF"
        self.calls = 0

    def set_melt_comp(self, composition):
        assert composition == {{"SiO2": 50.0}}

    def eval_gas_abundances(self, temperature_K, logfO2, *, P):
{textwrap.indent(textwrap.dedent(evaluation_body).strip(), "        ")}
'''.replace("ORIGINAL_DATA_PATH", repr(str(changing_file)))
    (package / "equil.py").write_text(equil_source, encoding="utf-8")
    return changing_file


def _checked_residual_row() -> dict[str, str]:
    return _checked_residual_rows()[0]


def _checked_residual_rows() -> list[dict[str, str]]:
    with RESIDUALS.open(newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def test_sf04_companion_workbook_extract_rows_are_pinned() -> None:
    document = load_extract(EXTRACT)
    series = pressure_series_by_species(document)

    assert list(series) == ["SiO", "Fe", "Mg", "Na", "K", "O", "O2"]
    assert {
        species: [row["T_K"] for row in rows]
        for species, rows in series.items()
    } == {
        species: [1750.0, 1875.0, 1900.0, 2000.0, 2125.0, 2250.0, 2375.0, 2500.0]
        for species in series
    }
    fixture_sha256 = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert fixture_sha256 == (
        "e792f4c12a2f29778d3dc13185364022803c2b1f7bf6c469467635bab5791cbe"
    )
    assert document["source"]["checked_in_fixture_sha256"] == fixture_sha256

    with FIXTURE.open(newline="") as handle:
        fixture_rows = csv.DictReader(
            line for line in handle if not line.startswith("#")
        )
        expected = {
            (row["species"], float(row["T_K"])): {
                "T_K": float(row["T_K"]),
                "pressure_bar": float(row["workbook_pressure_bar"]),
                "log10_pressure_bar": float(row["log10p_bar"]),
            }
            for row in fixture_rows
            if row["composition_sheet"] == "tho"
            and row["species"] in series
            and float(row["T_K"])
            in {1750.0, 1875.0, 1900.0, 2000.0, 2125.0, 2250.0, 2375.0, 2500.0}
        }
    actual = {
        (species, row["T_K"]): row
        for species, rows in series.items()
        for row in rows
    }
    assert len(actual) == 56
    assert actual == expected


def test_reciprocal_temperature_interpolation_matches_table7_form() -> None:
    series = [
        {"T_K": 2000.0, "log10_pressure_bar": 4.0 - 16000.0 / 2000.0},
        {"T_K": 2200.0, "log10_pressure_bar": 4.0 - 16000.0 / 2200.0},
    ]

    value, method, bracket = interpolate_log10_pressure(series, 2100.0)

    assert value == pytest.approx(4.0 - 16000.0 / 2100.0)
    assert method == "reciprocal_T_interpolation"
    assert bracket == (2000.0, 2200.0)


def test_residual_is_vaporock_minus_sf04_and_threshold_is_inclusive() -> None:
    anchor = {
        "Na": [
            {"T_K": 1900.0, "log10_pressure_bar": -4.25},
            {"T_K": 2000.0, "log10_pressure_bar": -4.0},
        ]
    }
    model = {1900.0: {"Na": -3.75}}

    rows = compute_residual_rows(
        anchor_series=anchor,
        model_log10_pressure_bar=model,
        temperatures_K=(1900.0,),
        species=("Na",),
        threshold_dex=0.5,
        pressure_sensitivity_by_species={"Na": 1.25e-6},
    )

    assert rows == [
        {
            "temperature_K": 1900.0,
            "species": "Na",
            "log10_pressure_sf04_workbook_bar": -4.25,
            "log10_pressure_vaporock_bar": -3.75,
            "delta_log10_pressure_dex": 0.5,
            "threshold_dex": 0.5,
            "within_threshold": True,
            "recommendation_evidence": True,
            "evidence_role": "model_anchor_residual",
            "vaporock_pressure_bar": 1.0e-10,
            "pressure_sensitivity_bracket_low_bar": 1.0e-10,
            "pressure_sensitivity_bracket_high_bar": 2.0e-2,
            "max_abs_delta_delta_log10_pressure_dex": 1.25e-6,
            "vapor_database": "not_recorded",
            "vaporock_version": "not_recorded",
            "vaporock_commit": "not_recorded",
            "vaporock_checkout_state": "not_recorded",
            "vaporock_equil_py_sha256": "not_recorded",
            "vaporock_chemistry_py_sha256": "not_recorded",
            "vaporock_janaf_vapor_data_full_csv_sha256": "not_recorded",
            "vaporock_janaf_vapor_data_csv_sha256": "not_recorded",
            "vaporock_janaf0_vapor_data_csv_sha256": "not_recorded",
            "anchor_method": "workbook_exact",
            "anchor_bracket_low_K": 1900.0,
            "anchor_bracket_high_K": 1900.0,
        }
    ]


def test_csv_writer_refuses_dirty_recommendation_evidence_without_override(
    tmp_path: Path,
) -> None:
    digests = {
        "vaporock_equil_py_sha256": "a" * 64,
        "vaporock_chemistry_py_sha256": "f" * 64,
        "vaporock_janaf_vapor_data_full_csv_sha256": "b" * 64,
        "vaporock_janaf_vapor_data_csv_sha256": "c" * 64,
        "vaporock_janaf0_vapor_data_csv_sha256": "d" * 64,
    }
    rows = [
        {
            "species": "SiO",
            "within_threshold": True,
            "recommendation_evidence": True,
            "vaporock_commit": "e" * 40,
            "vaporock_checkout_state": "dirty",
            **digests,
        },
        {
            "species": "O2",
            "within_threshold": None,
            "recommendation_evidence": False,
            "vaporock_commit": "e" * 40,
            "vaporock_checkout_state": "dirty",
            **digests,
        },
    ]
    output = tmp_path / "dirty.csv"

    with pytest.raises(ValueError, match="dirty VapoRock checkout"):
        write_csv(rows, output)
    assert not output.exists()

    missing_digest_rows = [dict(row) for row in rows]
    missing_digest_rows[1].pop("vaporock_equil_py_sha256")
    missing_output = tmp_path / "missing-digest.csv"
    with pytest.raises(ValueError, match="vaporock_equil_py_sha256"):
        write_csv(
            missing_digest_rows,
            missing_output,
            allow_dirty_source=True,
        )
    assert not missing_output.exists()

    partial_chemistry_rows = [dict(row) for row in rows]
    partial_chemistry_rows[1].pop("vaporock_chemistry_py_sha256")
    partial_chemistry_output = tmp_path / "partial-chemistry-digest.csv"
    with pytest.raises(ValueError, match="vaporock_chemistry_py_sha256"):
        write_csv(
            partial_chemistry_rows,
            partial_chemistry_output,
            allow_dirty_source=True,
        )
    assert not partial_chemistry_output.exists()

    write_csv(rows, output, allow_dirty_source=True)
    with output.open(newline="") as handle:
        written = list(csv.DictReader(handle))
    assert len(written) == 2
    assert all(row["vaporock_checkout_state"] == "dirty" for row in written)
    assert all(
        all(row[field] == digest for field, digest in digests.items())
        for row in written
    )

    round_trip_output = tmp_path / "round-trip.csv"
    with pytest.raises(ValueError, match="dirty VapoRock checkout"):
        write_csv(written, round_trip_output)
    assert not round_trip_output.exists()


@pytest.mark.parametrize(
    "checkout_state",
    (None, "", "dirty ", "Dirty", "unknown"),
)
def test_csv_writer_refuses_missing_or_unknown_checkout_state(
    tmp_path: Path,
    checkout_state: str | None,
) -> None:
    row = _checked_residual_row()
    if checkout_state is None:
        row.pop("vaporock_checkout_state")
    else:
        row["vaporock_checkout_state"] = checkout_state
    output = tmp_path / "invalid-state.csv"

    with pytest.raises(ValueError, match="vaporock_checkout_state") as exc_info:
        write_csv(
            [row],
            output,
            allow_dirty_source=True,
            legacy_input=True,
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


@pytest.mark.parametrize(
    "checkout_state",
    (["clean"], {"state": "clean"}, None, 7),
)
def test_csv_writer_typed_refusal_for_non_string_checkout_state(
    tmp_path: Path,
    checkout_state: object,
) -> None:
    row = _checked_residual_row()
    row["vaporock_checkout_state"] = checkout_state
    output = tmp_path / "invalid-state-type.csv"

    with pytest.raises(ValueError, match="vaporock_checkout_state") as exc_info:
        write_csv(
            [row],
            output,
            allow_dirty_source=True,
            legacy_input=True,
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


@pytest.mark.parametrize(
    "field",
    (
        "vaporock_commit",
        "vaporock_equil_py_sha256",
        "vaporock_janaf_vapor_data_full_csv_sha256",
        "vaporock_janaf_vapor_data_csv_sha256",
        "vaporock_janaf0_vapor_data_csv_sha256",
    ),
)
def test_csv_writer_requires_commit_and_digests_for_clean_rows(
    tmp_path: Path,
    field: str,
) -> None:
    row = _checked_residual_row()
    row["vaporock_checkout_state"] = "clean"
    row.pop(field)
    output = tmp_path / "missing-provenance.csv"

    with pytest.raises(ValueError, match=field) as exc_info:
        write_csv([row], output, legacy_input=True)
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("within_threshold", None),
        ("within_threshold", ""),
    ),
)
def test_csv_writer_requires_exact_boolean_values(
    tmp_path: Path,
    field: str,
    value: str | None,
) -> None:
    row = _checked_residual_row()
    row["vaporock_checkout_state"] = "clean"
    if value is None:
        row.pop(field)
    else:
        row[field] = value
    output = tmp_path / "invalid-boolean.csv"

    with pytest.raises(ValueError, match=field) as exc_info:
        write_csv([row], output, legacy_input=True)
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


@pytest.mark.parametrize("within_threshold", (True, False, "True", "False"))
def test_csv_writer_requires_empty_threshold_for_non_evidence(
    tmp_path: Path,
    within_threshold: bool | str,
) -> None:
    row = next(
        row
        for row in _checked_residual_rows()
        if row["recommendation_evidence"] == "False"
    )
    row["vaporock_checkout_state"] = "clean"
    row["within_threshold"] = within_threshold
    output = tmp_path / "invalid-non-evidence-threshold.csv"

    with pytest.raises(ValueError, match="within_threshold") as exc_info:
        write_csv([row], output, legacy_input=True)
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


def test_csv_writer_replays_full_checked_csv_with_clean_provenance(
    tmp_path: Path,
) -> None:
    checked_rows = _checked_residual_rows()
    assert len(checked_rows) == 42
    replay = tmp_path / "replay.csv"
    write_csv(
        checked_rows,
        replay,
        allow_dirty_source=True,
        legacy_input=True,
    )
    assert replay.read_bytes() == RESIDUALS.read_bytes()

    clean_rows = [dict(row) for row in checked_rows]
    for row in clean_rows:
        row["vaporock_checkout_state"] = "clean"
    output = tmp_path / "clean.csv"

    write_csv(clean_rows, output, legacy_input=True)

    with output.open(newline="") as handle:
        written = list(csv.DictReader(handle))
    assert written == clean_rows


@pytest.mark.parametrize("append_to_checked_rows", (False, True))
def test_csv_writer_refuses_new_rows_without_chemistry_digest(
    tmp_path: Path,
    append_to_checked_rows: bool,
) -> None:
    checked_rows = _checked_residual_rows()
    new_row = dict(checked_rows[0])
    new_row["temperature_K"] = "1951.0"
    rows = [*checked_rows, new_row] if append_to_checked_rows else [new_row]
    output = tmp_path / "new-without-chemistry-digest.csv"

    with pytest.raises(
        ValueError,
        match="vaporock_chemistry_py_sha256",
    ) as exc_info:
        write_csv(rows, output, allow_dirty_source=True)
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


def test_legacy_mode_refuses_a_batch_that_is_not_wholly_legacy(
    tmp_path: Path,
) -> None:
    """legacy_input must not launder a digest-bearing new row past the exemption.

    The exemption exists only to replay the historical artifact, whose rows all
    predate the chemistry digest.  A batch mixing those rows with a row that
    *does* carry the digest is therefore not the historical artifact, and must be
    refused rather than silently exempted.
    """
    checked_rows = _checked_residual_rows()
    new_row = dict(checked_rows[0])
    new_row["temperature_K"] = "1951.0"
    new_row["vaporock_chemistry_py_sha256"] = "b" * 64
    output = tmp_path / "mixed-legacy-batch.csv"

    with pytest.raises(ValueError) as exc_info:
        write_csv(
            [*checked_rows, new_row],
            output,
            allow_dirty_source=True,
            legacy_input=True,
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not output.exists()


def test_vaporock_evaluation_uses_snapshot_if_original_changes_transiently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    changing_file = _write_fake_vaporock_inputs(
        package,
        '''
        self.calls += 1
        original = Path(ORIGINAL_DATA_PATH)
        if self.calls == 1:
            original.write_text("transient full data", encoding="utf-8")
        with resources.path(
            "vaporock.data", "JANAF-vapor-data-full.csv"
        ) as snapshot_path:
            snapshot_data = Path(snapshot_path).read_text(encoding="utf-8")
        if self.calls == 2:
            original.write_text("original full data", encoding="utf-8")
        value = -3.0 if snapshot_data == "original full data" else -5.0
        return SimpleNamespace(loc=_Loc(value))
''',
    )

    class _OriginalSystem:
        def __init__(self, *, vapor_database: str) -> None:
            assert vapor_database == "JANAF"
            self.calls = 0

        def set_melt_comp(self, composition: dict[str, float]) -> None:
            assert composition == {"SiO2": 50.0}

        def eval_gas_abundances(
            self, temperature_K: float, logfO2: float, *, P: float
        ) -> _FakeTable:
            self.calls += 1
            if self.calls == 1:
                changing_file.write_text("transient full data", encoding="utf-8")
                return _FakeTable()
            value = -5.0 if changing_file.read_text(encoding="utf-8") == (
                "transient full data"
            ) else -3.0
            changing_file.write_text("original full data", encoding="utf-8")
            return _FakeTable(value)

    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
        System=_OriginalSystem,
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    model, provenance, pressure_sensitivity = evaluate_vaporock(
        composition_wt_pct={"SiO2": 50.0},
        temperatures_K=(1900.0,),
        oxygen_series=(
            {
                "T_K": 1900.0,
                "log10_pressure_bar": -4.0,
            },
        ),
        species=("SiO",),
    )

    assert model[1900.0]["SiO"] == -3.0
    assert pressure_sensitivity["SiO"] == 0.0
    assert provenance["vaporock_janaf_vapor_data_full_csv_sha256"] == (
        hashlib.sha256(b"original full data").hexdigest()
    )
    assert changing_file.read_text(encoding="utf-8") == "original full data"


def test_vaporock_evaluation_snapshots_package_local_chemistry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    _write_fake_vaporock_inputs(
        package,
        "return SimpleNamespace(loc=_Loc(chem.VALUE))",
    )
    chemistry_source = b"VALUE = -3.0\n"
    (package / "chemistry.py").write_bytes(chemistry_source)
    equil_path = package / "equil.py"
    equil_path.write_text(
        equil_path.read_text(encoding="utf-8").replace(
            "from types import SimpleNamespace\n",
            "from types import SimpleNamespace\n\nfrom . import chemistry as chem\n",
        ),
        encoding="utf-8",
    )

    fake_vaporock = ModuleType("vaporock")
    fake_vaporock.__file__ = str(package / "__init__.py")
    fake_vaporock.__path__ = [str(package)]
    fake_vaporock.__spec__ = ModuleSpec(
        "vaporock",
        loader=None,
        origin=str(package / "__init__.py"),
        is_package=True,
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)
    monkeypatch.setitem(
        sys.modules,
        "vaporock.chemistry",
        SimpleNamespace(VALUE=-5.0),
    )

    model, provenance, pressure_sensitivity = evaluate_vaporock(
        composition_wt_pct={"SiO2": 50.0},
        temperatures_K=(1900.0,),
        oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
        species=("SiO",),
    )

    assert model[1900.0]["SiO"] == -3.0
    assert pressure_sensitivity["SiO"] == 0.0
    assert provenance["vaporock_chemistry_py_sha256"] == hashlib.sha256(
        chemistry_source
    ).hexdigest()


def test_vaporock_checkout_identity_refuses_commit_during_status_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    package.mkdir()
    (package / ".git").mkdir()
    source_path = package / "equil.py"
    source_path.touch()
    current_commit = "a" * 40
    calls: list[str] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        nonlocal current_commit
        if "rev-parse" in command:
            calls.append("head")
            return SimpleNamespace(returncode=0, stdout=f"{current_commit}\n")
        calls.append("status")
        current_commit = "b" * 40
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.subprocess.run",
        fake_run,
    )

    with pytest.raises(
        ValueError,
        match="checkout identity changed while sampling",
    ) as exc_info:
        _vaporock_checkout_identity(source_path)
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert calls == ["head", "status", "head"]


def test_vaporock_evaluation_refuses_checkout_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    marker = tmp_path / "evaluation-started"
    _write_fake_vaporock_inputs(
        package,
        f'''
        Path({str(marker)!r}).write_text("started", encoding="utf-8")
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )
    (package / ".git").mkdir()
    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        if "rev-parse" in command:
            commit = "b" * 40 if marker.exists() else "a" * 40
            return SimpleNamespace(returncode=0, stdout=f"{commit}\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.subprocess.run",
        fake_run,
    )

    with pytest.raises(ValueError, match="checkout identity changed") as exc_info:
        evaluate_vaporock(
            composition_wt_pct={"SiO2": 50.0},
            temperatures_K=(1900.0,),
            oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
            species=("SiO",),
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"


def test_vaporock_evaluation_refuses_identity_change_during_snapshot_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    evaluation_started = tmp_path / "evaluation-started-after-copy"
    _write_fake_vaporock_inputs(
        package,
        f'''
        Path({str(evaluation_started)!r}).touch()
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )
    (package / ".git").mkdir()
    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    identity_changed = tmp_path / "identity-changed-during-copy"

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        if "rev-parse" in command:
            commit = "b" * 40 if identity_changed.exists() else "a" * 40
            return SimpleNamespace(returncode=0, stdout=f"{commit}\n")
        return SimpleNamespace(returncode=0, stdout="")

    original_copyfile = shutil.copyfile

    def copyfile_then_change_identity(
        source: Path,
        destination: Path,
    ) -> str | Path:
        copied = original_copyfile(source, destination)
        if Path(source).name == "equil.py":
            identity_changed.touch()
        return copied

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.shutil.copyfile",
        copyfile_then_change_identity,
    )

    with pytest.raises(ValueError, match="checkout identity changed") as exc_info:
        evaluate_vaporock(
            composition_wt_pct={"SiO2": 50.0},
            temperatures_K=(1900.0,),
            oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
            species=("SiO",),
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert not evaluation_started.exists()


def test_vaporock_evaluation_allows_unrelated_checkout_dirtiness_at_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    evaluation_started = tmp_path / "evaluation-started"
    unrelated_file = package / "unrelated.txt"
    _write_fake_vaporock_inputs(
        package,
        f'''
        Path({str(evaluation_started)!r}).touch()
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )
    (package / ".git").mkdir()
    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    commit = "a" * 40

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout=f"{commit}\n")
        status = "?? unrelated.txt\n" if unrelated_file.exists() else ""
        return SimpleNamespace(returncode=0, stdout=status)

    original_copyfile = shutil.copyfile

    def copyfile_then_add_unrelated_file(
        source: Path,
        destination: Path,
    ) -> str | Path:
        copied = original_copyfile(source, destination)
        if Path(source).name == "equil.py":
            unrelated_file.touch()
        return copied

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.shutil.copyfile",
        copyfile_then_add_unrelated_file,
    )

    model, provenance, pressure_sensitivity = evaluate_vaporock(
        composition_wt_pct={"SiO2": 50.0},
        temperatures_K=(1900.0,),
        oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
        species=("SiO",),
    )

    assert evaluation_started.exists()
    assert model[1900.0]["SiO"] == -3.0
    assert pressure_sensitivity["SiO"] == 0.0
    assert provenance["vaporock_commit"] == commit
    assert provenance["vaporock_checkout_state"] == "dirty"


def test_vaporock_evaluation_refuses_copied_source_vector_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    evaluation_started = tmp_path / "evaluation-started"
    changing_file = _write_fake_vaporock_inputs(
        package,
        f'''
        Path({str(evaluation_started)!r}).touch()
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )
    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    original_copyfile = shutil.copyfile

    def copy_transient_bytes(source: Path, destination: Path) -> str | Path:
        if Path(source) != changing_file:
            return original_copyfile(source, destination)
        original = changing_file.read_bytes()
        changing_file.write_text("transient full data", encoding="utf-8")
        try:
            return original_copyfile(source, destination)
        finally:
            changing_file.write_bytes(original)

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.shutil.copyfile",
        copy_transient_bytes,
    )

    with pytest.raises(
        ValueError,
        match="copied VapoRock inputs differ from the pre-copy source vector",
    ) as exc_info:
        evaluate_vaporock(
            composition_wt_pct={"SiO2": 50.0},
            temperatures_K=(1900.0,),
            oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
            species=("SiO",),
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert "vaporock_janaf_vapor_data_full_csv_sha256" in str(exc_info.value)
    assert not evaluation_started.exists()


def test_vaporock_evaluation_refuses_source_vector_change_at_snapshot_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    evaluation_started = tmp_path / "evaluation-started"
    changing_file = _write_fake_vaporock_inputs(
        package,
        f'''
        Path({str(evaluation_started)!r}).touch()
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )
    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    original_copyfile = shutil.copyfile

    def copy_then_change_source(source: Path, destination: Path) -> str | Path:
        copied = original_copyfile(source, destination)
        if Path(source).name == "JANAF0-vapor-data.csv":
            changing_file.write_text("changed at cut", encoding="utf-8")
        return copied

    monkeypatch.setattr(
        "scripts.vapour_rail_sf04_high_t.shutil.copyfile",
        copy_then_change_source,
    )

    with pytest.raises(
        ValueError,
        match="VapoRock source inputs changed while snapshotting",
    ) as exc_info:
        evaluate_vaporock(
            composition_wt_pct={"SiO2": 50.0},
            temperatures_K=(1900.0,),
            oxygen_series=({"T_K": 1900.0, "log10_pressure_bar": -4.0},),
            species=("SiO",),
        )
    assert exc_info.type.__name__ == "VapoRockProvenanceError"
    assert "vaporock_janaf_vapor_data_full_csv_sha256" in str(exc_info.value)
    assert not evaluation_started.exists()


def test_snapshot_boundary_claim_is_explicitly_scoped() -> None:
    claim = evaluate_vaporock.__doc__ or ""
    decision = DECISION.read_text(encoding="utf-8")

    for text in (claim, decision):
        normalized = " ".join(text.split())
        assert "ordinary concurrent edits" in normalized
        assert "temporary directory" in normalized
    assert "ThermoEngine" in claim
    assert "outside the snapshot boundary" in claim


def test_vaporock_evaluation_aborts_transient_snapshot_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "vaporock"
    _write_fake_vaporock_inputs(
        package,
        '''
        self.calls += 1
        if self.calls == 1:
            with resources.path(
                "vaporock.data", "JANAF-vapor-data-full.csv"
            ) as changing_file:
                changing_path = Path(changing_file)
                original = changing_path.read_text(encoding="utf-8")
                changing_path.write_text(
                    "mutated snapshot data", encoding="utf-8"
                )
                try:
                    value = -5.0 if changing_path.read_text(encoding="utf-8") == (
                        "mutated snapshot data"
                    ) else -3.0
                finally:
                    changing_path.write_text(original, encoding="utf-8")
                return SimpleNamespace(loc=_Loc(value))
        return SimpleNamespace(loc=_Loc(-3.0))
''',
    )

    class _OriginalSystem:
        def __init__(self, *, vapor_database: str) -> None:
            assert vapor_database == "JANAF"

        def set_melt_comp(self, composition: dict[str, float]) -> None:
            assert composition == {"SiO2": 50.0}

        def eval_gas_abundances(
            self, temperature_K: float, logfO2: float, *, P: float
        ) -> _FakeTable:
            return _FakeTable()

    fake_vaporock = SimpleNamespace(
        __file__=str(package / "__init__.py"),
        __spec__=ModuleSpec(
            "vaporock",
            loader=None,
            origin=str(package / "__init__.py"),
        ),
        System=_OriginalSystem,
    )
    monkeypatch.setitem(sys.modules, "vaporock", fake_vaporock)

    with pytest.raises(
        RuntimeError,
        match="read-only input snapshot",
    ) as exc_info:
        evaluate_vaporock(
            composition_wt_pct={"SiO2": 50.0},
            temperatures_K=(1900.0,),
            oxygen_series=(
                {
                    "T_K": 1900.0,
                    "log10_pressure_bar": -4.0,
                },
            ),
            species=("SiO",),
        )
    assert exc_info.type.__name__ == "VapoRockInputMutationError"


def test_o_and_o2_residuals_are_not_recommendation_evidence() -> None:
    anchor = {
        "O": [{"T_K": 1900.0, "log10_pressure_bar": -5.8}],
        "O2": [{"T_K": 1900.0, "log10_pressure_bar": -4.8}],
    }
    model = {1900.0: {"O": -5.8, "O2": -4.8}}

    rows = compute_residual_rows(
        anchor_series=anchor,
        model_log10_pressure_bar=model,
        temperatures_K=(1900.0,),
        species=("O", "O2"),
        threshold_dex=0.5,
        pressure_sensitivity_by_species={"O": 0.0, "O2": 0.0},
    )
    by_species = {row["species"]: row for row in rows}

    assert all(row["within_threshold"] is None for row in rows)
    assert all(row["recommendation_evidence"] is False for row in rows)
    assert by_species["O"]["evidence_role"] == "gas_equilibrium_consistency"
    assert by_species["O2"]["evidence_role"] == "fO2_pinned_identity"


def test_interpolation_refuses_extrapolation_beyond_workbook() -> None:
    series = [
        {"T_K": 2375.0, "log10_pressure_bar": -4.0},
        {"T_K": 2500.0, "log10_pressure_bar": -3.0},
    ]

    with pytest.raises(ValueError, match="outside SF04 workbook grid"):
        interpolate_log10_pressure(series, 2501.0)


def test_checked_in_residual_table_is_the_reviewed_live_probe() -> None:
    with RESIDUALS.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 42
    assert {row["vaporock_checkout_state"] for row in rows} == {"dirty"}
    digest_fields = (
        "vaporock_equil_py_sha256",
        "vaporock_janaf_vapor_data_full_csv_sha256",
        "vaporock_janaf_vapor_data_csv_sha256",
        "vaporock_janaf0_vapor_data_csv_sha256",
    )
    for field in digest_fields:
        digests = {row[field] for row in rows}
        assert len(digests) == 1
        digest = digests.pop()
        assert len(digest) == 64
        int(digest, 16)
    assert all(float(row["vaporock_pressure_bar"]) == 1.0e-10 for row in rows)
    assert all(
        float(row["pressure_sensitivity_bracket_low_bar"]) == 1.0e-10
        and float(row["pressure_sensitivity_bracket_high_bar"]) == 2.0e-2
        for row in rows
    )
    pressure_sensitivity = {
        species: max(
            float(row["max_abs_delta_delta_log10_pressure_dex"])
            for row in rows
            if row["species"] == species
        )
        for species in {row["species"] for row in rows}
    }
    assert pressure_sensitivity == pytest.approx(
        {
            "SiO": 1.4791271718550547e-6,
            "Fe": 7.869837874707741e-7,
            "Mg": 6.622683894619286e-7,
            "Na": 8.374709512537493e-7,
            "K": 1.334582126588657e-6,
            "O": 0.0,
            "O2": 0.0,
        },
        rel=1.0e-6,
        abs=1.0e-12,
    )
    pass_counts = {}
    for temperature_K in {float(row["temperature_K"]) for row in rows}:
        temperature_rows = [
            row for row in rows if float(row["temperature_K"]) == temperature_K
        ]
        evidence_rows = [
            row
            for row in temperature_rows
            if row["recommendation_evidence"] == "True"
        ]
        pass_counts[temperature_K] = (
            sum(row["within_threshold"] == "True" for row in evidence_rows),
            len(evidence_rows),
        )
    assert pass_counts == {
        1900.0: (2, 5),
        2000.0: (3, 5),
        2100.0: (3, 5),
        2200.0: (3, 5),
        2300.0: (3, 5),
        2473.0: (3, 5),
    }
    assert hashlib.sha256(RESIDUALS.read_bytes()).hexdigest() == (
        "5109f40125c6eb0b79ba7e23b9586da5381af614c7219ce43441a53d773f57d0"
    )
