"""Render pins and row-condition migration for Maeda & Sano (1982)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
SID = "maeda-sano-1982-cro-activity-coefficient-slag"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
EXTRACT_V2 = ROOT / f"data/literature/extracts-v2/{SID}.yaml"
PINNED_GAMMA = {
    "maeda_sano_1982_f14_al2o3_20_row01": "134",
    "maeda_sano_1982_f14_al2o3_10_row01": "108",
    "maeda_sano_1982_f14_al2o3_20_row02": "88",
    "maeda_sano_1982_f14_al2o3_20_row03": "61",
    "maeda_sano_1982_f14_al2o3_10_row02": "60",
    "maeda_sano_1982_f14_al2o3_10_row03": "33",
    "maeda_sano_1982_f14_al2o3_10_row04": "23",
    "maeda_sano_1982_f14_al2o3_10_row05": "12",
    "maeda_sano_1982_f11_row01": "40.1",
    "maeda_sano_1982_f11_row02": "40.2",
    "maeda_sano_1982_f11_row03": "40.1",
    "maeda_sano_1982_f11_row04": "40.2",
    "maeda_sano_1982_f11_row05": "40.1",
    "maeda_sano_1982_f11_row06": "50.1",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["CrO"]["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    rows = _rows(document)
    assert set(rows) == set(PINNED_GAMMA)
    for observation_id, expected in PINNED_GAMMA.items():
        assert rows[observation_id]["values"]["activity_coefficient"] == expected

    figure11_cr = {
        f"maeda_sano_1982_f11_row{row:02d}": value
        for row, value in enumerate(("30.1", "30.0", "30.1", "30.0", "30.1", "24.1"), 1)
    }
    for observation_id, expected in figure11_cr.items():
        assert rows[observation_id]["values"]["equilibrium_Cr_ppm_as_published"] == expected

    first = rows["maeda_sano_1982_f14_al2o3_20_row01"]
    assert first["values"]["composition_wt_pct"]["components"] == [
        ["CaO", "46.6"],
        ["MgO", "0"],
        ["Al2O3", "20"],
        ["SiO2", "33.4"],
    ]
    assert first["values"]["a_CrO_as_published"] == "3.79e-3"
    assert rows["maeda_sano_1982_f11_row06"]["values"]["a_CrO_as_published"] == "1.34e-3"


def test_render_values_are_pinned_and_mutations_are_caught() -> None:
    document = _document()
    _assert_render_pins(document)

    changed_gamma = deepcopy(document)
    _rows(changed_gamma)["maeda_sano_1982_f14_al2o3_20_row01"]["values"][
        "activity_coefficient"
    ] = "135"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_gamma)

    changed_ppm = deepcopy(document)
    _rows(changed_ppm)["maeda_sano_1982_f11_row01"]["values"][
        "equilibrium_Cr_ppm_as_published"
    ] = "30.2"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_ppm)

    changed_composition = deepcopy(document)
    _rows(changed_composition)["maeda_sano_1982_f14_al2o3_20_row01"]["values"][
        "composition_wt_pct"
    ]["components"][0][1] = "46.5"
    with pytest.raises(AssertionError):
        _assert_render_pins(changed_composition)


def test_extract_validates_and_isolated_migration_preserves_each_row_condition() -> None:
    document = _document()
    _assert_render_pins(document)
    assert validate_extract_document(document, path=EXTRACT, expected_source_id=SID) == []
    assert check_all_fidelity_samples_match(document, label=SID) == []

    v2_document = yaml.safe_load(EXTRACT_V2.read_text(encoding="utf-8"))
    assert v2_document["schema_version"] == "battery_observations.v2.1"
    assert v2_document["sources"] == [EXTRACT.relative_to(ROOT).as_posix()]
    v2_rows = {
        row["observation_id"].removeprefix(f"{SID}::"): row
        for row in v2_document["observations"]
    }
    source_rows = _rows(document)
    assert set(v2_rows) == set(source_rows)
    for observation_id, source_row in source_rows.items():
        observation = v2_rows[observation_id]
        source_values = source_row["values"]
        conditions = observation["point_conditions"]
        assert observation["value"]["point"] == source_values["activity_coefficient"]
        expected_temperature = Decimal(source_values["T_C"]) + Decimal("273.15")
        assert Decimal(observation["identity"]["temperature_K"]["value"]) == expected_temperature
        assert conditions["temperature_C"]["state"]["value"] == source_values["T_C"]
        assert conditions["composition"]["state"]["value"]["components"] == source_values[
            "composition_wt_pct"
        ]["components"]
        assert conditions["composition"]["locator"] is not None
        assert conditions["oxygen_buffer"]["state"]["value"] == (
            "a_C = 1, P_CO = 1 atm, a_Cr3C2 = 1"
        )
        assert conditions["oxygen_buffer"]["locator"]["equation"] == "10"
        assert conditions["chromium_valence"]["locator"]["section"] in {"4.2", "4.3"}
        assert conditions["chromium_valence_as_printed"]["locator"]["section"] in {
            "4.2",
            "4.3",
        }
        if source_values["T_C"] == "1500":
            assert "almost entirely Cr2+" in conditions["chromium_valence"]["state"]["value"]
            assert conditions["chromium_valence_as_printed"]["state"]["value"] == (
                "そのほとんどが Cr2+ の陽イオン形"
            )
        else:
            assert "no per-row Cr2+/Cr3+ ratio" in conditions["chromium_valence"]["state"]["value"]
            assert conditions["chromium_valence_as_printed"]["state"]["value"] == (
                "Cr2+ の陽イオン形と CrO_n^{2(n-1)-} の陰イオン形"
            )
        assert observation["provenance"]["standard_state_as_printed"] == (
            "CrO(l) = Cr(S) + 1/2 O2(g), Eq. (4)"
        )
        assert observation["provenance"]["method_as_printed"] == source_values[
            "method_as_printed"
        ]
        if source_values["T_C"] == "1650":
            assert observation["derived_from"] == [f"pdf:{SID}"]
            assert observation["derivation"]["inputs"] == [f"pdf:{SID}"]
            assert observation["derivation"]["references"] == ["Eq. (13)", "footnote 3"]
            assert observation["derivation"]["method"]["locator"]["equation"] == "13"

    assert len(v2_rows) == 14

    index = {SID: {"corpus": {"raw": {"path": document["source"]["corpus_asset"]}}}}
    migrator = Migrator(root=ROOT, index=index, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    observations = [
        row for row in result.observations.values() if row.source_id == SID
    ]
    assert len(observations) == 14

    expected_conditions = {
        "temperature_C",
        "composition",
        "oxygen_buffer",
        "oxygen_partial_pressure_atm",
        "chromium_valence",
        "chromium_valence_as_printed",
    }
    for row in observations:
        source_values = _rows(document)[row.observation_id.removeprefix(f"{SID}::")][
            "values"
        ]
        assert row.point_conditions is not None
        assert expected_conditions <= set(row.point_conditions)
        for name in expected_conditions:
            condition = row.point_conditions[name]
            assert condition.locator is not None, (row.observation_id, name)
        assert row.identity.temperature_K is not None
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value in {
            Decimal("1773.15"),
            Decimal("1923.15"),
        }
        assert row.identity.temperature_K.value == (
            Decimal(source_values["T_C"]) + Decimal("273.15")
        )
        assert row.point_conditions["temperature_C"].state.value == Decimal(
            source_values["T_C"]
        )
        assert row.point_conditions["oxygen_buffer"].state.value == (
            "a_C = 1, P_CO = 1 atm, a_Cr3C2 = 1"
        )
        assert row.identity.composition is not None
        assert row.identity.composition.is_value
        composition_condition = row.point_conditions["composition"]
        assert composition_condition.state.is_value
        assert composition_condition.state.value == row.identity.composition.value

    low_t = [row for row in observations if row.identity.temperature_K.value == Decimal("1773.15")]
    high_t = [row for row in observations if row.identity.temperature_K.value == Decimal("1923.15")]
    assert len(low_t) == 8
    assert len(high_t) == 6
    assert all(row.point_conditions["oxygen_partial_pressure_atm"].state.is_value for row in low_t)
    assert all(row.point_conditions["oxygen_partial_pressure_atm"].state.is_unknown for row in high_t)
