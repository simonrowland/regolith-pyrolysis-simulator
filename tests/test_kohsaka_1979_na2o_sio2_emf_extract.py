from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract, _series_census
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kohsaka-1979-na2o-sio2-emf.yaml"
SOURCE_ID = "kohsaka-1979-na2o-sio2-emf"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _by_id(rows, observation_id: str):
    return next(row for row in rows if row.observation_id.endswith(observation_id))


def _source_row(document: dict, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"]["Na2O"]["observations"]
        if row["observation_id"] == observation_id
    )


def test_render_pins_and_mutation_detection() -> None:
    document = _document()
    assert len(document["species"]["Na2O"]["observations"]) == 95
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    pins = (
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r02_c03", "333.0", "1505", "0.775"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r12_c13", "-134.7", "1427", "0.55"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r16_c07", "-244.8", "1425", "0.45"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t02_r03_c04", "-8.6", "1450", "75.0"),
    )
    observations = document["species"]["Na2O"]["observations"]
    for observation_id, emf_or_enthalpy, temperature, composition in pins:
        row = _source_row(document, observation_id)
        assert row["values"]["value"] == emf_or_enthalpy
        assert row["values"]["T_K"] == temperature
        locator = row["locator"]
        assert locator["page"] == 3
        assert locator["table"] in {"1", "2"}
        condition = row["point_conditions"]["temperature_K"]
        assert condition["state"]["value"]["point"] == temperature
        assert condition["locator"]["page"] == 3
        comp = row["point_conditions"]["composition"]["state"]["value"]["components"]
        assert dict(comp)["SiO2"] == (composition if row["locator"]["table"] == "1" else "0.750")

    mutated = copy.deepcopy(document)
    _source_row(mutated, pins[0][0])["values"]["value"] = "333.1"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 1 cell")


def test_isolated_real_migration_preserves_conditions_and_schema_refusals(tmp_path: Path) -> None:
    document = _document()
    source_rows = document["species"]["Na2O"]["observations"]
    for source_row in source_rows:
        for condition_name in ("total_pressure_Pa", "fO2_log"):
            assert source_row["point_conditions"][condition_name]["state"]["tag"] == "unknown"
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 95

    rows = list(result.observations.values())
    unavailable = [row for row in rows if row.value.kind.value == "unavailable"]
    assert len(unavailable) == 85
    expected_enthalpy_values = {
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r02_c04": Decimal("68.2"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r03_c04": Decimal("-8.6"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r04_c04": Decimal("-17.2"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r05_c04": Decimal("-25.1"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r06_c04": Decimal("-18.4"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r07_c04": Decimal("-23.8"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r08_c04": Decimal("0"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r09_c04": Decimal("46.9"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r10_c04": Decimal("38.1"),
        "kohsaka_1979_na2o_sio2_emf_p003_t02_r11_c04": Decimal("35.7"),
    }
    enthalpy_rows = [
        row
        for row in rows
        if row.identity.quantity.is_value
        and row.identity.quantity.value.value == "partial_molar_enthalpy"
    ]
    assert len(enthalpy_rows) == 10
    assert all(row.value.kind.value == "point" for row in enthalpy_rows)
    assert {
        row.observation_id.rsplit("::", 1)[-1]: row.value.point for row in enthalpy_rows
    } == expected_enthalpy_values
    assert all(row.admission.status.value == "rejected" for row in enthalpy_rows)
    assert all(row.identity.species.phase.is_value for row in rows)
    assert all(row.identity.species.phase.value.value == "l" for row in rows)
    for row in rows:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.identity.composition.is_value
        assert row.point_conditions["composition"].state.is_value
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert (
            row.identity.fO2_Pa is None
            or row.identity.fO2_Pa.is_unknown
            or row.identity.fO2_Pa.tag.value == "not_applicable"
        )

    expected_quantity_unknown = {
        f"{SOURCE_ID}::{observation_id}"
        for observation_id in (
            [
                f"kohsaka_1979_na2o_sio2_emf_p003_t01_r{row:02d}_c{column:02d}"
                for row, columns in (
                    (2, (3, 5, 7, 9, 11, 13)), (3, (3, 5)),
                    (4, (3, 5, 7, 9, 11, 13)), (5, (3,)),
                    (6, (3, 5, 7, 9, 11, 13)), (7, (3, 5, 7, 9, 11, 13)),
                    (8, (3, 5, 7, 9, 11, 13)), (9, (3, 5)),
                    (10, (3, 5, 7, 9, 11, 13)), (11, (3, 5)),
                    (12, (3, 5, 7, 9, 11, 13)),
                    (13, (3, 5, 7, 9, 11, 13)), (14, (3,)),
                    (15, (3, 5, 7, 9, 11, 13)), (16, (3, 5, 7)),
                )
                for column in columns
            ]
            + [
                f"kohsaka_1979_na2o_sio2_emf_p003_t02_r{row:02d}_c{column:02d}"
                for row in range(2, 12)
                for column in (2, 3)
            ]
        )
    }
    assert {
        row.observation_id
        for row in rows
        if not row.identity.quantity.is_value
    } == expected_quantity_unknown
    assert not any(row.identity.species.phase.is_unknown for row in rows)

    for observation_id, temperature, siO2 in (
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r02_c03", "1505", "0.775"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r12_c13", "1427", "0.55"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t01_r16_c07", "1425", "0.45"),
        ("kohsaka_1979_na2o_sio2_emf_p003_t02_r03_c04", "1450", "0.750"),
    ):
        row = _by_id(rows, observation_id)
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.point_conditions["temperature_K"].state.value == Decimal(temperature)
        assert row.point_conditions["temperature_K"].locator.page == 3
        composition = row.identity.composition.value.as_map()
        assert composition["SiO2"] == Decimal(siO2)
        assert row.point_conditions["composition"].state.value.as_map()["SiO2"] == Decimal(siO2)

    table2_enthalpy = _by_id(rows, "kohsaka_1979_na2o_sio2_emf_p003_t02_r03_c04")
    assert table2_enthalpy.identity.quantity.value.value == "partial_molar_enthalpy"
    assert table2_enthalpy.derived_from
    assert table2_enthalpy.value.kind.value == "point"
    assert table2_enthalpy.admission.status.value == "rejected"

    literature_root = tmp_path / "tree" / "data" / "literature"
    census, mismatches, n_numeric, n_unavailable, per_source = _series_census(
        literature_root / "extracts",
        literature_root / "extracts-v2",
    )
    assert census == {}
    assert mismatches == []
    assert n_numeric == 0
    assert n_unavailable == 0
    assert per_source.get(SOURCE_ID, {}) == {}
