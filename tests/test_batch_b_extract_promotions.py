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
    rate = next(
        observation
        for observation in result.observations.values()
        if observation.observation_id.endswith(
            "::shimaoka_1991_na_run_89a_vaporization_rate"
        )
    )
    assert rate.identity.quantity.is_unknown
    assert rate.value.kind.value == "unavailable"
    assert rate.admission.status.value == "rejected"
    document = yaml.safe_load(
        (EXTRACTS / "shimaoka-nakamura-1991-iau-colloq126-79.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_rate = next(
        observation
        for observation in document["species"]["Na"]["observations"]
        if observation["observation_id"]
        == "shimaoka_1991_na_run_89a_vaporization_rate"
    )
    assert source_rate["units"] == "cm3 min-1 cm-2 as printed"
    assert source_rate["locator"]["row"] == "89A"
    assert source_rate["locator"]["column"] == "k Na"
    assert source_rate["values"]["k_cm3_min_cm2_as_printed"] == 0.000107
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
    assert len(result.observations) == 47
    assert result.validation.hard_issues == ()
    assert _point(result, "adamkovicova_1980_heat_of_fusion_at_1817K") == Decimal(
        "57.3"
    )
    assert any(
        "adamkovicova_1980_table1_solid_cooling_measured" in row.observation_id
        and row.value.point == Decimal("139.6")
        for row in result.observations.values()
    )


def test_shornikov_2000_migrates_printed_transition_temperatures() -> None:
    result = _migrate("shornikov-2000-aluminosilicate-vaporization.yaml")
    assert len(result.observations) == 46
    assert _point(result, "shornikov_2000_table1_row_01") == Decimal("1986")
    assert _point(result, "shornikov_2000_table1_row_02") == Decimal("1996")
    assert all(
        observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        for observation in result.observations.values()
    )


def test_shilov_1998_migrates_printed_po_pressures() -> None:
    result = _migrate("shilov-1998-sno-zno-p2o5-vaporization.yaml")
    assert len(result.observations) == 92
    migrated_po_rows = [
        observation
        for observation in result.observations.values()
        if "table2_PO_partial_pressures::points:" in observation.observation_id
        and observation.value.point is not None
    ]
    document = yaml.safe_load(
        (EXTRACTS / "shilov-1998-sno-zno-p2o5-vaporization.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_po = next(
        observation
        for observation in document["species"]["PO"]["observations"]
        if observation["observation_id"] == "shilov_1998_table2_PO_partial_pressures"
    )
    source_points = source_po["values"]["points"]
    assert len(migrated_po_rows) == len(source_points)
    po_by_x_sno = {
        Decimal(str(source_point["xSnO_mol_pct"])): migrated.value.point
        for source_point, migrated in zip(source_points, migrated_po_rows)
    }
    # Table 2, p. 402 prints 1.51e-7 and 1.77e-7 atm at xSnO=40.0/46.7 mol%;
    # the migrated pressure values are in Pa.
    assert po_by_x_sno[Decimal("40.0")] == Decimal("0.015300075")
    assert po_by_x_sno[Decimal("46.7")] == Decimal("0.017934525")
