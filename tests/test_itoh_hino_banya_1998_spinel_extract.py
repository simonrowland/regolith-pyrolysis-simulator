"""Migration and figure-record pins for Itoh, Hino and Ban-Ya (1998)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/itoh-hino-banya-1998-spinel.yaml"
SOURCE_ID = "itoh-hino-banya-1998-spinel"
PRINTED_LOG_KF = {
    "itoh_1998_spinel_formation_logKf_1873K": ("MgAl2O4", Decimal("-1.18")),
    "itoh_1998_calcium_aluminate_formation_logKf_1873K": ("CaAl4O7", Decimal("-1.79")),
}
FIGURE_MARKERS = {
    "1": [8, 8, 6, 4],
    "2": [4, 4, 4, 3],
    "3": [0, 0, 0, 0],
    "4": [0, 0],
    "5": [0, 0],
    "6": [0, 0],
    "7": [0, 0],
    "8": [0, 0],
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_printed_log_kf(document: dict) -> None:
    found = {}
    for species, body in document["species"].items():
        for row in body["observations"]:
            obs_id = row["observation_id"]
            if obs_id not in PRINTED_LOG_KF:
                continue
            formula, value = PRINTED_LOG_KF[obs_id]
            assert row["values"]["formula"] == formula
            assert Decimal(str(row["values"]["value"])) == value
            assert row["values"]["quantity"] == "log10_Kf"
            assert row["phase"] == "cr"
            assert row["values"]["T_K"] == "1873"
            assert row["locator"]["page"] == row["locator"]["pdf_page_index"] + 1 == 4
            found[obs_id] = species
    assert found == {
        "itoh_1998_spinel_formation_logKf_1873K": "MgO",
        "itoh_1998_calcium_aluminate_formation_logKf_1873K": "Al2O3",
    }


def test_printed_solid_formation_constants_validate_and_migrate_as_points() -> None:
    document = _document()
    assert validate_extract_document(
        document, expected_source_id=SOURCE_ID, pre_policy_ids=set(), check_fidelity_match=True
    ) == []
    _assert_printed_log_kf(document)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 2
    assert not migrator.result.validation.hard_issues
    actual = {row.observation_id.rsplit("::", 1)[-1]: row for row in observations}
    for obs_id, (formula, value) in PRINTED_LOG_KF.items():
        row = actual[obs_id]
        assert row.value.kind.value == "point"
        assert row.value.point == value
        assert row.identity.species.formula == formula
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1873")
        condition = row.point_conditions["temperature_K"]
        assert condition.state.is_value and condition.state.value == Decimal("1873")
        assert condition.locator.page == 4


def test_independent_printed_pins_detect_changed_extract_values() -> None:
    document = _document()
    _assert_printed_log_kf(document)
    changed = deepcopy(document)
    changed["species"]["MgO"]["observations"][0]["values"]["value"] = -1.17
    try:
        _assert_printed_log_kf(changed)
    except AssertionError:
        pass
    else:
        raise AssertionError("extract mutation did not disagree with the printed-value pin")


def test_figures_1_to_8_are_deferred_with_source_marker_counts() -> None:
    document = _document()
    figures = {
        record["locator"]["figure"]: record
        for body in document["species"].values()
        for record in body["context"]
        if record["type"] == "figure_record"
    }
    assert set(figures) == set(FIGURE_MARKERS)
    for number, record in figures.items():
        values = record["values"]
        assert values["caption"]
        assert record["phase"]
        assert values["species"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["series"]
        assert [series["marker_count"] for series in values["series"]] == FIGURE_MARKERS[number]
        assert values["digitisation"] == "deferred to a figure pass"
        assert "points" not in values
        assert record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1
