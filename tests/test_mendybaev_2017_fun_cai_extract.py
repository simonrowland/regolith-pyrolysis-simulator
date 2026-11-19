"""Table 1 residue-composition pins for Mendybaev et al. (2017)."""

import csv
import hashlib
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.corpus_paths import regolith_corpus_root
from tools import locator_page_audit, validate_literature_extracts as vle
from tools.locator_page_audit import collect_locator_records


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2017-fun-cai-lab-evaporation.yaml"
LOCATOR_AUDIT = ROOT / "data/literature/extracts/audits/mendybaev-2017-fun-cai-lab-evaporation.locator-page-audit.csv"
TABLE_TRANSCRIPTION = Path(
    "tables/mendybaev-2017-fun-cai-lab-evaporation/"
    "mendybaev-2017-fun-cai-lab-evaporation-table1-residue-oxides.tsv"
)
OXIDES = {"MgO", "Al2O3", "SiO2", "CaO"}
FUNC_STARTING_COMPOSITION = {
    "MgO": Decimal("37.9"),
    "Al2O3": Decimal("11.6"),
    "SiO2": Decimal("42.9"),
    "CaO": Decimal("7.6"),
}
SOURCE_PAGE_PINS = {
    12: "Because CaO and Al2O3 do not evaporate from the melt at the conditions of the experiments",
}
EXPECTED_SKIP_PATHS = {
    "experiments.0.locator",
}
EXPECTED_CONVERSION_PHRASES = {
    **{
        f"experiments.{index}.pressure_environment.total_pressure_Pa.locator": "10^-9 bars"
        for index in range(1, 10)
    },
    **{
        f"experiments.{index}.pressure_environment.pumping.base_pressure_Pa.locator": "10^-7 bars"
        for index in range(1, 10)
    },
}
WRONG_PAGE_LOCATOR_PATHS = [
    "benches.0.identity.ref.locator",
    "benches.0.geometry.locator",
    *[
        f"experiments.{index}.{field}.locator"
        for index in range(1, 10)
        for field in (
            "thermal_schedule.total_duration_s",
            "apparatus.geometry.cell_or_crucible_material",
            "pressure_environment.sweep_gas",
        )
    ],
]
CONVERSION_TOKEN_MUTATIONS = [
    *[
        pytest.param(
            f"experiments.{index}.pressure_environment.total_pressure_Pa.locator",
            "10^-9",
            True,
            id=f"total-pressure-with-paragraph-{index}",
        )
        for index in range(1, 10)
    ],
    *[
        pytest.param(
            f"experiments.{index}.pressure_environment.pumping.base_pressure_Pa.locator",
            "10^-7",
            False,
            id=f"pump-down-conversion-only-{index}",
        )
        for index in range(1, 10)
    ],
]


def _migrate(path: Path = EXTRACT) -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator


def _residues(migrator: Migrator):
    return [
        item
        for item in migrator.result.observations.values()
        if item.identity.quantity.is_value
        and item.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]


def _named_values(value: object, name: str) -> list[object]:
    if isinstance(value, dict):
        matches = [value[name]] if name in value else []
        return matches + [
            match
            for child in value.values()
            for match in _named_values(child, name)
        ]
    if isinstance(value, list):
        return [match for child in value for match in _named_values(child, name)]
    return []


def _locator_at(document: dict, path: str) -> dict:
    value = document
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _require_source_pdf() -> Path:
    pdf_path = locator_page_audit.source_pdf_path()
    if not pdf_path.is_file():
        pytest.skip(f"corpus PDF unavailable: {pdf_path}")
    return pdf_path


def _run_real_pdf_audit(tmp_path: Path, monkeypatch, document: dict | None = None):
    _require_source_pdf()
    extract = EXTRACT
    if document is not None:
        extract = tmp_path / "mutated-extract.yaml"
        extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    receipt = tmp_path / "locator-page-audit.csv"
    monkeypatch.setattr(locator_page_audit, "ROOT", tmp_path)
    monkeypatch.setattr(locator_page_audit, "EXTRACT", extract)
    monkeypatch.setattr(locator_page_audit, "RECEIPT", receipt)

    result = locator_page_audit.audit()
    with receipt.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return result, rows, receipt


def _assert_source_page_index_pins(document: dict) -> None:
    with LOCATOR_AUDIT.open(encoding="utf-8", newline="") as stream:
        receipt = list(csv.DictReader(stream))
    assert receipt
    skips = {row["path"] for row in receipt if row["result"] == "SKIP_NO_PHRASE_OR_PIN"}
    assert skips == EXPECTED_SKIP_PATHS
    assert all(row["result"] == "PASS" for row in receipt if row["path"] not in skips)
    expected_pairs = [
        (
            row["path"],
            int(row["pdf_page_index"]),
            row["phrase"],
            EXPECTED_CONVERSION_PHRASES.get(row["path"], ""),
        )
        for row in receipt
    ]
    actual_pairs = [
        (
            row["path"],
            row["pdf_page_index"],
            row["phrase"],
            row["conversion_phrase"],
        )
        for row in collect_locator_records(document)
    ]
    assert actual_pairs == expected_pairs

    source_pairs = {(index, phrase) for index, phrase in SOURCE_PAGE_PINS.items()}
    audited_pairs = {(index, phrase) for _, index, phrase, _ in actual_pairs}
    assert source_pairs <= audited_pairs


def test_source_page_index_pins_and_plus_one_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_page_index_pins(document)

    mutated = deepcopy(document)
    sample_load = _named_values(mutated, "sample_load_locator")[0]
    sample_load["pdf_page_index"] += 1
    try:
        _assert_source_page_index_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("a +1 source-page mutation must fail the independent pin guard")

    mutated_ca = deepcopy(document)
    mutated_ca["species"]["Ca"]["observations"][0]["locator"]["pdf_page_index"] = 13
    try:
        _assert_source_page_index_pins(mutated_ca)
    except AssertionError:
        pass
    else:
        raise AssertionError("moving the Ca quotation to index 13 must fail its page/phrase pair")

    mutated_form = deepcopy(document)
    mutated_form["experiments"][1]["sample"]["form"]["locator"]["pdf_page_index"] = 13
    try:
        _assert_source_page_index_pins(mutated_form)
    except AssertionError:
        pass
    else:
        raise AssertionError("moving a known-value form locator from index 8 to 13 must fail")

    mutated_conversion = deepcopy(document)
    pressure_locator = mutated_conversion["experiments"][1]["pressure_environment"][
        "total_pressure_Pa"
    ]["locator"]
    pressure_locator["note"] = pressure_locator["note"].replace("10^-9", "10^-6")
    try:
        _assert_source_page_index_pins(mutated_conversion)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed conversion token must fail the receipt-pin guard")


def test_locator_page_audit_uses_configured_corpus_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tmp_path))
    assert locator_page_audit.source_pdf_path() == (
        tmp_path
        / "raw/mendybaev-2017-fun-cai-lab-evaporation/"
        "mendybaev-2017-fun-cai-lab-evaporation.pdf"
    )


def test_real_pdf_locator_audit_baseline_matches_committed_receipt(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    pdf_path = _require_source_pdf()
    expected_pdf_path = (
        locator_page_audit.regolith_corpus_root()
        / locator_page_audit.PDF_RELATIVE_PATH
    )
    assert pdf_path == expected_pdf_path
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest() == locator_page_audit.PDF_SHA256

    result, rows, receipt = _run_real_pdf_audit(tmp_path, monkeypatch)
    capsys.readouterr()

    assert result == 0
    assert len(rows) == 482
    assert sum(row["result"] == "SKIP_NO_PHRASE_OR_PIN" for row in rows) == 1
    assert sum(row["result"] == "PASS" for row in rows) == 481
    assert not any(row["result"] == "FAIL" for row in rows)
    assert [row["path"] for row in rows if row["result"] == "SKIP_NO_PHRASE_OR_PIN"] == [
        "experiments.0.locator"
    ]
    assert receipt.read_bytes() == LOCATOR_AUDIT.read_bytes()


@pytest.mark.parametrize("path", WRONG_PAGE_LOCATOR_PATHS)
def test_real_pdf_locator_audit_rejects_each_wrong_page(
    path: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locator_parent = _locator_at(document, path.removesuffix(".locator"))
    locator_parent["locator"] = deepcopy(locator_parent["locator"])
    locator_parent["locator"]["pdf_page_index"] = 13

    result, rows, _ = _run_real_pdf_audit(tmp_path, monkeypatch, document)
    capsys.readouterr()

    assert result == 1
    assert [row["path"] for row in rows if row["result"] == "FAIL"] == [path]


@pytest.mark.parametrize(
    ("path", "printed_token", "has_paragraph"), CONVERSION_TOKEN_MUTATIONS
)
def test_real_pdf_locator_audit_rejects_each_wrong_conversion_token(
    path: str,
    printed_token: str,
    has_paragraph: bool,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    locator = _locator_at(document, path)
    assert ("paragraph" in locator) is has_paragraph
    assert printed_token in locator["note"]
    locator["note"] = locator["note"].replace(printed_token, "10^-6", 1)

    result, rows, _ = _run_real_pdf_audit(tmp_path, monkeypatch, document)
    capsys.readouterr()

    assert result == 1
    assert [row["path"] for row in rows if row["result"] == "FAIL"] == [path]


def test_table_1_first_last_and_largest_two_sigma_cells_match_page() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        observation
        for observation in document["species"]["FUNC"]["observations"]
        if observation["observation_id"].endswith("table1_residue_composition")
    ]
    rows = [
        point for observation in observations for point in observation["values"]["series"]
    ]
    cells = {
        (point["sample"], column): (point[f"{column}_wt_pct"], point["sigma"])
        for point in rows
        for column in OXIDES
        if f"{column}_wt_pct" in point
    }

    assert len(cells) == 36
    transcription_samples = [
        sample
        for sample in document["fidelity_samples"]
        if sample.get("locator", {}).get("source_path") is not None
    ]
    assert len(transcription_samples) == 9
    assert {
        sample["locator"]["source_path"] for sample in transcription_samples
    } == {TABLE_TRANSCRIPTION.as_posix()}
    assert cells[("FUNC-5", "MgO")] == ("34.09", "0.10")
    assert cells[("FUNC-6", "CaO")] == ("26.75", "0.08")
    assert cells[("FUNC-4", "Al2O3")] == ("32.15", "0.54")
    assert all(
        point["locator"]["table"] == "1"
        and point["locator"]["pdf_page_index"] == 38
        and point["locator"]["row"] == point["sample"]
        and point["locator"]["column"] in OXIDES
        for point in rows
    )


def _assert_table_1_source_attestation(document: dict) -> None:
    source_samples = [
        sample
        for sample in document.get("fidelity_samples", [])
        if isinstance(sample, dict)
        and sample.get("locator", {}).get("source_path")
        == TABLE_TRANSCRIPTION.as_posix()
    ]
    source_document = {**document, "fidelity_samples": source_samples}
    warnings = vle.fidelity_source_warnings(source_document, label=EXTRACT.stem)
    incomplete = [
        warning
        for warning in warnings
        if warning.kind in {"unattested_sample", "source_blind_pin"}
    ]
    assert incomplete == [], incomplete


def test_table_1_fidelity_samples_match_corpus_transcription(monkeypatch) -> None:
    transcription_path = regolith_corpus_root() / TABLE_TRANSCRIPTION
    if not transcription_path.is_file():
        pytest.skip(f"corpus transcription unavailable: {transcription_path}")
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(regolith_corpus_root()))

    with transcription_path.open(encoding="utf-8", newline="") as stream:
        transcription = {
            (row["sample"], row["column"]): (row["value"], row["printed_2sigma"])
            for row in csv.DictReader(stream, delimiter="\t")
        }

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    transcription_samples = [
        sample
        for sample in document["fidelity_samples"]
        if sample.get("locator", {}).get("source_path") == TABLE_TRANSCRIPTION.as_posix()
    ]
    assert len(transcription_samples) == 9
    assert {
        sample["locator"]["source_sha256"] for sample in transcription_samples
    } == {hashlib.sha256(transcription_path.read_bytes()).hexdigest()}
    extract_cells = {
        (point["sample"], column): (point[f"{column}_wt_pct"], point["sigma"])
        for observation in document["species"]["FUNC"]["observations"]
        if observation["observation_id"].endswith("table1_residue_composition")
        for point in observation["values"]["series"]
        for column in OXIDES
        if f"{column}_wt_pct" in point
    }
    assert len(transcription) == 36
    assert transcription == extract_cells
    _assert_table_1_source_attestation(document)


def test_table_1_source_attestation_rejects_missing_additional_locator(
    monkeypatch,
):
    corpus_root = regolith_corpus_root()
    transcription_path = corpus_root / TABLE_TRANSCRIPTION
    if not transcription_path.is_file():
        pytest.skip(f"corpus transcription unavailable: {transcription_path}")
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(corpus_root))

    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    sample = next(
        sample
        for sample in document["fidelity_samples"]
        if sample.get("locator", {}).get("source_path")
        == TABLE_TRANSCRIPTION.as_posix()
    )
    locator = sample["locator"]
    source_path = locator["source_path"]
    source_sha256 = locator["source_sha256"]
    missing_quotes_path = "tables/missing-review-witness.tsv"
    assert not (corpus_root / missing_quotes_path).exists()
    locator["quotes_path"] = missing_quotes_path

    warnings = vle.fidelity_source_warnings(document, label=EXTRACT.stem)
    source_blind = [
        warning for warning in warnings if warning.kind == "source_blind_pin"
    ]
    assert len(source_blind) == 1 and source_blind[0].count == 1, warnings
    assert missing_quotes_path in source_blind[0].detail
    assert locator["source_path"] == source_path
    assert locator["source_sha256"] == source_sha256
    with pytest.raises(AssertionError):
        _assert_table_1_source_attestation(document)


def test_table_1_residues_migrate_with_run_charge_clock_and_conditions() -> None:
    from simulator.battery.waypoints import (
        ReadinessStatus,
        consumer_readiness,
        oxygen_condition,
    )

    migrator = _migrate()
    rows = _residues(migrator)

    assert len(rows) == 36
    assert len(migrator.result.experiments) == 10, sorted(migrator.result.experiments)
    assert not migrator.result.validation.hard_issues
    assert {row.identity.species.formula for row in rows} == OXIDES
    assert {row.identity.temperature_K.value for row in rows} == {Decimal("2173.15")}
    bench = next(iter(migrator.result.benches.values()))
    for row in rows:
        run = row.locator.row
        assert run is not None
        experiment = migrator.result.experiments[row.experiment_id]
        assert row.experiment_id.endswith(f"::experiment::{run.casefold()}")
        assert row.identity.sample_mass_kg.is_value
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("2173.15")
        assert temperature.locator is not None
        assert temperature.locator.pdf_page_index == 38
        assert temperature.locator.table == "1"
        starting = row.point_conditions["starting_composition"]
        assert starting.state.is_value
        assert starting.locator is not None
        assert starting.locator.pdf_page_index == 38
        assert starting.locator.table == "1"
        assert {
            oxide: Decimal(str(value))
            for oxide, value in starting.state.value.items()
        } == FUNC_STARTING_COMPOSITION
        elapsed = row.point_conditions["time_min"]
        assert elapsed.state.is_value
        assert elapsed.locator is not None
        assert elapsed.locator.pdf_page_index == 38
        assert elapsed.locator.table == "1"
        composition = row.point_conditions["composition"]
        assert composition.state.is_value
        assert composition.locator is not None
        assert composition.locator.pdf_page_index == 38
        assert composition.locator.table == "1"
        assert set(composition.state.value.as_map()) == OXIDES
        oxygen = oxygen_condition(experiment, bench, row).selected
        assert oxygen is not None
        assert oxygen.route == "vacuum_total_pressure_upper_bound"
        internal = next(
            readiness
            for readiness in consumer_readiness(experiment, bench, row)
            if readiness.engine == "internal-analytical"
        )
        assert internal.status is ReadinessStatus.READY
        assert not internal.gaps
        assert row.locator.pdf_page_index == 38
        assert row.locator.table == "1"

    scheduled_experiments = [
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.thermal_schedule is not None
    ]
    assert len(scheduled_experiments) == 9
    for experiment in scheduled_experiments:
        schedule = experiment.thermal_schedule
        assert schedule.method is not None
        method_locator = schedule.method.locator
        assert method_locator is not None
        assert method_locator.section == "3.1"
        assert method_locator.pdf_page_index == 8
        assert method_locator.line_range == "166-173"
        assert schedule.total_duration_s is not None
        assert schedule.total_duration_s.state.is_unknown
        assert schedule.total_duration_s.state.reason == "not_published"
        duration_locator = schedule.total_duration_s.locator
        assert duration_locator is not None
        assert duration_locator.section == "3.1"
        assert duration_locator.pdf_page_index == 8


def test_table_locators_resolve_to_the_physical_page_named_by_extract() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    page_note = re.search(
        r"PDF page (\d+) used to map Table 1 and Table 2",
        document["extraction"]["method"],
    )
    assert page_note is not None
    expected_index = int(page_note.group(1)) - 1
    table_indices: dict[str, set[int]] = {}

    def collect_table_locators(value: object) -> None:
        if isinstance(value, dict):
            locator = value.get("locator")
            if isinstance(locator, dict) and "table" in locator:
                table_indices.setdefault(str(locator["table"]), set()).add(
                    locator["pdf_page_index"]
                )
            for child in value.values():
                collect_table_locators(child)
        elif isinstance(value, list):
            for child in value:
                collect_table_locators(child)

    collect_table_locators(document)

    assert table_indices == {"1": {expected_index}, "2": {expected_index}}


def test_table_1_cell_value_and_sigma_mutations_reach_migrated_rows(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    target = next(
        point
        for observation in document["species"]["FUNC"]["observations"]
        if observation["observation_id"] == "mendybaev_2017_func5_table1_residue_composition"
        for point in observation["values"]["series"]
        if point["locator"]["column"] == "MgO"
    )
    target["MgO_wt_pct"] = "34.10"
    target["sigma"] = "0.11"
    mutated_extract = tmp_path / EXTRACT.name
    mutated_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    row = next(
        item
        for item in _residues(_migrate(mutated_extract))
        if item.experiment_id.endswith("::experiment::func-5")
        and item.identity.species.formula == "MgO"
    )
    assert row.value.point == Decimal("34.10")
    assert row.uncertainty.kind.value == "printed"
    assert row.uncertainty.verbatim["sigma"] == "0.11"
