from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = ROOT / "data/literature/extracts"


def _migrate(name: str):
    path = EXTRACTS / name
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert validate_extract_document(
        document, expected_source_id=path.stem, check_fidelity_match=True
    ) == []
    source_id = document["source_id"]
    migrator = Migrator(
        root=ROOT,
        index={source_id: dict(document["source"], source_id=source_id)},
        aliases={},
    )
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _point(result, observation_id: str) -> Decimal:
    observation = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith(f"::{observation_id}")
    )
    assert observation.value.point is not None
    return observation.value.point


def test_di_donato_granati_2000_migrates_printed_activity_cells() -> None:
    result = _migrate("di-donato-granati-2000.yaml")
    assert len(result.observations) == 95
    assert _point(result, "di_donato_2000_t2_slag1_r1") == Decimal("0.015")
    assert _point(result, "di_donato_2000_t4_slag1_r2") == Decimal("0.078")
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in result.observations.values()
    )


def test_shults_1994_migrates_printed_pressure_and_activity_cells() -> None:
    result = _migrate("shults-1994-mullite-evaporation.yaml")
    assert len(result.observations) == 45
    assert _point(result, "shults_1994_t6_al2o3_activity_1833k") == Decimal("1.05")
    assert _point(result, "shults_1994_t6_sio2_activity_2033k") == Decimal("0.17")
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in result.observations.values()
    )


def test_ostrovski_2000_migrates_printed_activity_cells() -> None:
    result = _migrate("ostrovski-2000-cao-tio2-sio2.yaml")
    assert len(result.observations) == 40
    assert _point(result, "ostrovski_2000_table4_s7_activity") == Decimal("0.84")
    assert _point(result, "ostrovski_2000_table4_s7_activity_coefficient") == Decimal(
        "1.615"
    )
    located = [
        observation
        for observation in result.observations.values()
        if observation.point_conditions
    ]
    assert len(located) == 38
    assert all(
        observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in located
    )


def test_gaillard_2003_migrates_printed_activity_cells() -> None:
    result = _migrate("gaillard-2003-hydrous-silicic-melt-activity.yaml")
    assert len(result.observations) == 260
    assert _point(
        result, "gaillard_2003_table4_run7_a_fe2o3::point:1"
    ) == Decimal("0.102")
    assert _point(
        result, "gaillard_2003_table4_run7_a_feo::point:0"
    ) == Decimal("0.017")
    located = [
        observation
        for observation in result.observations.values()
        if observation.point_conditions
    ]
    assert len(located) == 96
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in located
    )


def test_shimaoka_nakamura_1991_migrates_table1_cells() -> None:
    result = _migrate("shimaoka-nakamura-1991-iau-colloq126-79.yaml")
    assert len(result.observations) == 30
    assert _point(result, "shimaoka_1991_na_run_89a_concentration") == Decimal(
        "2074"
    )
    assert _point(result, "shimaoka_1991_na_run_89a_vaporization_rate") == Decimal(
        "0.000107"
    )
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in result.observations.values()
    )


def test_shornikov_2013_migrates_printed_gibbs_energies() -> None:
    result = _migrate("shornikov-2013-potassium-silicate-thermodynamics.yaml")
    assert len(result.observations) == 27
    assert _point(result, "shornikov_2013_t3_solid_k2sio3") == Decimal("-1089.318")
    assert _point(result, "shornikov_2013_t3_liquid_k2sio3") == Decimal("-1099.514")
    located = [
        observation
        for observation in result.observations.values()
        if observation.point_conditions
    ]
    assert len(located) == 26
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in located
    )


def test_adamkovicova_1980_ingested_count_and_pdf_cells() -> None:
    result = _migrate("adamkovicova-1980-casio3-heat-of-fusion.yaml")
    assert len(result.observations) == 42
    document = yaml.safe_load(
        (EXTRACTS / "adamkovicova-1980-casio3-heat-of-fusion.yaml").read_text(
            encoding="utf-8"
        )
    )
    table1, table2 = document["species"]["CaSiO3"]["observations"][:2]
    # Values are unavailable in the current reader; these cells are pinned
    # independently from Table I, p. 195 and Table II, p. 196.
    assert table1["values"]["points"][0]["delta_Hcool_kJ_mol-1_as_printed"] == 139.6
    assert table2["values"]["points"][0]["delta_Hsol_kJ_mol-1_as_printed"] == 311.1
