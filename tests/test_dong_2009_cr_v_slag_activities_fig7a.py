from __future__ import annotations

import csv
import hashlib
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator
from simulator.corpus_paths import regolith_corpus_root


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts-v2/dong-2009-cr-v-slag-activities.yaml"
CORPUS_BRANCH = "w1119dong-fig7a"
CORPUS_COMMIT = "af89e74f4052a01a0d034c72c7e0486362d83904"
DIGITIZATION_PATH = "tables/dong-2009-cr-v-slag-activities/f7a.csv"
DIGITIZATION_SHA256 = "f731888428e30c6acfbfc28239c32144181ab22a7cf90d0d29c9878c721dcbff"
DIGITIZATION = regolith_corpus_root() / DIGITIZATION_PATH


def _rows(document: dict) -> dict[str, dict]:
    return {
        point["row_as_digitized"]: point
        for observation in document["species"]["CrO"]["observations"]
        for point in observation["values"]["points"]
    }


def _assert_digitization_pins(document: dict, digitization: dict[str, dict]) -> None:
    points = _rows(document)
    assert points.keys() == digitization.keys()
    for row_id, csv_row in digitization.items():
        assert points[row_id]["x_CrO"] == csv_row["x_CrO"]
        assert points[row_id]["activity"] == csv_row["activity_CrO"]
        assert points[row_id]["digitization_uncertainty"]["x_CrO"].removeprefix(
            "±"
        ) == csv_row["x_uncertainty"]
        assert points[row_id]["digitization_uncertainty"]["activity"].removeprefix(
            "±"
        ) == csv_row["activity_uncertainty"]


def _load_digitization() -> dict[str, dict]:
    if not DIGITIZATION.is_file():
        pytest.skip(
            f"corpus artifact {DIGITIZATION_PATH} is unavailable; expected branch "
            f"{CORPUS_BRANCH} at {CORPUS_COMMIT}"
        )
    payload = DIGITIZATION.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == DIGITIZATION_SHA256
    return {
        row["row_id"]: row
        for row in csv.DictReader(payload.decode("utf-8").splitlines())
    }


def test_figure_digitization_pins_all_csv_values_and_detects_each_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    digitization = _load_digitization()
    assert len(digitization) == 9
    _assert_digitization_pins(document, digitization)

    for row_id in digitization:
        for field in ("x_CrO", "activity"):
            mutated = deepcopy(document)
            point = _rows(mutated)[row_id]
            point[field] = str(Decimal(point[field]) + Decimal("0.0001"))
            with pytest.raises(AssertionError):
                _assert_digitization_pins(mutated, digitization)


def _assert_source_identity_and_page_locators(document: dict) -> None:
    assert "doi" not in document["source"]
    assert "pp. 327–336" in document["source"]["citation"]
    assert "fidelity_samples" not in document
    source_files = document["source_files"]
    assert source_files["corpus_repo"] == "regolith-corpus"
    assert source_files["corpus_commit"]["value"] == CORPUS_COMMIT
    assert source_files["files"][0]["path"] == DIGITIZATION_PATH
    assert source_files["files"][0]["sha256"]["value"] == DIGITIZATION_SHA256

    observations = document["species"]["CrO"]["observations"]
    reason = observations[0]["standard_state"]["state"]["reason"]
    assert "Cr(Pt)(s) + 1/2 O2(g) = CrO(l)" in reason
    assert "does not state whether CrO(l) is pure" in reason
    for observation in observations:
        assert observation["standard_state"]["locator"] == {
            "page": 3,
            "published_page": 329,
            "pdf_page_index": 2,
            "equation": "1",
        }

    experiment = document["experiments"][0]["locator"]
    assert (
        experiment["page"],
        experiment["published_page"],
        experiment["pdf_page_index"],
    ) == (4, 330, 3)
    points = _rows(document)
    for point in points.values():
        conditions = point["point_conditions"]
        assert conditions["gas_mix"]["locator"]["published_page"] == 327
        assert conditions["Cr2O3_reagent"]["locator"]["published_page"] == 329
        assert conditions["base_slag_system"]["locator"] == {
            "page": 5,
            "published_page": 331,
            "pdf_page_index": 4,
            "section": "Results — Equilibrium Time",
        }


def test_source_identity_and_page_locators_match_the_held_conference_paper() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_identity_and_page_locators(document)

    mutations = (
        ("source", "doi", "10.2374/SRI08SP124"),
        ("source", "citation", "held paper, pp. 328–337"),
    )
    for section, key, value in mutations:
        mutated = deepcopy(document)
        mutated[section][key] = value
        with pytest.raises(AssertionError):
            _assert_source_identity_and_page_locators(mutated)

    mutated = deepcopy(document)
    mutated["source_files"]["corpus_repo"] = "regolith-pyrolysis-simulator"
    with pytest.raises(AssertionError):
        _assert_source_identity_and_page_locators(mutated)

    mutated = deepcopy(document)
    mutated["source_files"]["files"][0]["path"] = (
        "_corpus-out/tables/dong-2009-cr-v-slag-activities/f7a.csv"
    )
    with pytest.raises(AssertionError):
        _assert_source_identity_and_page_locators(mutated)

    mutated = deepcopy(document)
    mutated["source_files"]["files"][0]["sha256"]["value"] = "0" * 64
    with pytest.raises(AssertionError):
        _assert_source_identity_and_page_locators(mutated)

    mutated = deepcopy(document)
    mutated["species"]["CrO"]["observations"][0]["standard_state"]["state"][
        "reason"
    ] = "not printed"
    with pytest.raises(AssertionError):
        _assert_source_identity_and_page_locators(mutated)

    for locator_path, field, value in (
        (("experiments", 0, "locator"), "published_page", 331),
        (("species", "CrO", "observations", 0, "standard_state", "locator"), "page", 7),
        (
            ("species", "CrO", "observations", 0, "values", "points", 0, "point_conditions", "Cr2O3_reagent", "locator"),
            "published_page",
            330,
        ),
        (
            ("species", "CrO", "observations", 0, "values", "points", 0, "point_conditions", "gas_mix", "locator"),
            "published_page",
            328,
        ),
        (
            ("species", "CrO", "observations", 0, "values", "points", 0, "point_conditions", "base_slag_system", "locator"),
            "page",
            4,
        ),
    ):
        mutated = deepcopy(document)
        target = mutated
        for part in locator_path:
            target = target[part]
        target[field] = value
        with pytest.raises(AssertionError):
            _assert_source_identity_and_page_locators(mutated)


def test_isolated_migration_preserves_all_nine_located_rows() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 9

    observations = [
        row for row in result.observations.values() if row.value.kind is ValueKind.POINT
    ]
    assert len(observations) == 9
    source_points = _rows(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    assert len(source_points) == 9
    for point in source_points.values():
        conditions = point["point_conditions"]
        printed_temp_k = conditions["temperature_K"]["state"]["value"]
        temp_k = Decimal(point["T_C"]) + Decimal("273.15")
        migrated = next(
            row
            for row in observations
            if row.identity.temperature_K.is_value
            and row.identity.temperature_K.value == temp_k
            and row.value.point == Decimal(point["activity"])
        )
        assert migrated.identity.temperature_K.value == temp_k
        assert migrated.identity.reference_state.is_unknown
        assert (
            "Cr(Pt)(s) + 1/2 O2(g) = CrO(l)"
            in migrated.identity.reference_state.reason
        )
        assert migrated.point_conditions is not None
        condition_keys = (
            "temperature_K",
            "pO2_Pa",
            "x_CrO",
            "composition",
            "base_slag_system",
            "Cr2O3_reagent",
            "nominal_CrOx_loading",
            "targeted_mixture_mass",
            "gas_mix",
            "gas_mix_ratio",
            "chromium_valence_assumption",
        )
        for key in condition_keys:
            condition = conditions[key]
            assert condition["locator"]
            assert key in migrated.point_conditions
            located = migrated.point_conditions[key]
            assert located.locator is not None
            source_state = condition["state"]
            if source_state["tag"] == "value":
                assert located.state.is_value
                expected = source_state["value"]
                if key in {"temperature_K", "pO2_Pa", "x_CrO"}:
                    assert located.state.value == Decimal(expected)
                else:
                    assert located.state.value == expected
            else:
                assert located.state.is_unknown
                assert located.state.reason == source_state["reason"]
        assert migrated.point_conditions["temperature_K"].state.value == Decimal(
            printed_temp_k
        )
