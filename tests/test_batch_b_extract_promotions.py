from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import wt_pct_to_mole_fraction
from tests.battery.factories import migrate_one_extract
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
    migrator = migrate_one_extract(
        root=ROOT,
        path=path,
        index={source_id: dict(document["source"], source_id=source_id)},
        aliases={},
    )
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
    assert result.validation.hard_issues == ()
    assert _point(result, "di_donato_2000_t2_slag1_r1") == Decimal("0.015")
    assert _point(result, "di_donato_2000_t4_slag1_r2") == Decimal("0.078")
    assert all(
        observation.identity.reference_state.is_value
        for observation in result.observations.values()
    )
    for observation_id, formula in (
        ("di_donato_2000_t2_slag1_r1", "FeO"),
        ("di_donato_2000_t4_slag1_r1", "MnO"),
        ("di_donato_2000_t5_slag3_r6", "Cr2O3"),
    ):
        observation = next(
            row
            for row in result.observations.values()
            if row.observation_id.endswith(f"::{observation_id}")
        )
        standard_state = observation.identity.reference_state.value
        assert standard_state.convention.value == "raoultian_pure_endmember"
        assert standard_state.endmember.formula == formula
        assert standard_state.endmember.phase.is_unknown
        assert standard_state.endmember.polymorph.is_unknown
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


# Printed Table 4 H2O EMPA cells. Each sample is stored on the FeO and Fe2O3
# rows, so the count is 2. These are the analytical-total water weights, not
# the separate H2O_wt_pct column.
_GAILLARD_TABLE4_H2O_EMPA = (
    ("2/1", "5.21", 2),
    ("2/3", "5.56", 2),
    ("2/5", "4.96", 2),
    ("2/7", "5.03", 2),
    ("2/8", "5.00", 2),
    ("3/1", "3.25", 2),
    ("3/10", "7.1", 2),
    ("3/11", "2.66", 2),
    ("3/12", "6.44", 2),
    ("3/13", "6.77", 2),
    ("3/2", "6.71", 2),
    ("3/3", "6.55", 2),
    ("3/4", "5.85", 2),
    ("3/5", "3.56", 2),
    ("3/6", "6.59", 2),
    ("3/7", "6.88", 2),
    ("3/8", "6.02", 2),
    ("3/9", "4.99", 2),
    ("4/1", "6.25", 2),
    ("4/2", "5.95", 2),
    ("4/3", "6.35", 2),
    ("4/4", "6.09", 2),
    ("4/5", "6.01", 2),
    ("4/6", "5.01", 2),
    ("4/7", "4.36", 2),
    ("4/8", "4.22", 2),
    ("5/1", "5.10", 2),
    ("5/2", "4.50", 2),
    ("6/1", "6.68", 2),
    ("6/11", "6.45", 2),
    ("6/12", "6.81", 2),
    ("6/2", "6.97", 2),
    ("6/3", "6.58", 2),
    ("6/4", "6.97", 2),
    ("6/5", "7.15", 2),
    ("6/6", "6.80", 2),
)

# Sample 7/1 prints H2O 0 and no H2O EMPA. The oxide weights are a closed analysis.
_GAILLARD_RUN7_SAMPLE_1_OXIDES = {
    "SiO2": Decimal("70.22"),
    "Al2O3": Decimal("14.93"),
    "FeO": Decimal("3.77"),
    "CaO": Decimal("2.50"),
    "Na2O": Decimal("3.43"),
    "K2O": Decimal("3.18"),
}


def _table4_row(table4_rows, parent_id: str, sample: str):
    matches = [
        observation
        for observation in table4_rows
        if f"{parent_id}::" in observation.observation_id
        and f":row={sample}:" in observation.observation_id
    ]
    assert len(matches) == 1
    return matches[0]


def _printed_h2o_empa(item: dict) -> str | None:
    sibling = item.get("H2O_EMPA")
    if isinstance(sibling, str) and sibling.strip() not in {"", "–", "-"}:
        return sibling
    nested = (item.get("point_conditions") or {}).get("H2O_EMPA")
    if isinstance(nested, dict):
        value = (nested.get("state") or {}).get("value")
        if isinstance(value, str) and value.strip() not in {"", "–", "-"}:
            return value
    return None


def test_gaillard_2003_migrates_printed_activity_cells() -> None:
    result = _migrate("gaillard-2003-hydrous-silicic-melt-activity.yaml")
    assert len(result.observations) == 260
    assert result.validation.hard_issues == ()
    table4_rows = [
        observation
        for observation in result.observations.values()
        if "gaillard_2003_table4_run" in observation.observation_id
    ]
    assert len(table4_rows) == 96
    run7_fe2o3 = _table4_row(
        table4_rows, "gaillard_2003_table4_run7_a_fe2o3", "7/1"
    )
    run7_fe2o3_second = _table4_row(
        table4_rows, "gaillard_2003_table4_run7_a_fe2o3", "7/2"
    )
    run7_feo = _table4_row(table4_rows, "gaillard_2003_table4_run7_a_feo", "7/1")
    assert run7_fe2o3.value.point == Decimal("0.099")
    assert run7_fe2o3_second.value.point == Decimal("0.102")
    assert run7_feo.value.point == Decimal("0.017")
    assert run7_fe2o3.identity.composition.is_value
    assert run7_fe2o3.identity.reference_state.is_value
    assert (
        run7_fe2o3.point_conditions["printed_composition"].state.value["FeO"]
        == "3.77"
    )
    assert "H2O_EMPA" not in run7_fe2o3.point_conditions
    dry_oxides = wt_pct_to_mole_fraction(_GAILLARD_RUN7_SAMPLE_1_OXIDES)
    assert dict(run7_fe2o3.identity.composition.value.components) == dict(
        dry_oxides.components
    )
    assert dict(run7_feo.identity.composition.value.components) == dict(
        dry_oxides.components
    )
    assert (
        run7_fe2o3.point_conditions["delta_NNO"].state.value == Decimal("7.3")
    )
    assert run7_fe2o3.point_conditions["delta_NNO"].locator is not None
    run2_feo = _table4_row(table4_rows, "gaillard_2003_table4_run2_a_feo", "2/1")
    run3_feo = _table4_row(table4_rows, "gaillard_2003_table4_run3_a_feo", "3/1")
    assert run2_feo.identity.composition.is_unknown
    assert run2_feo.point_conditions["H2O_EMPA"].state.value == Decimal("5.21")
    assert run2_feo.point_conditions["H2O_EMPA"].locator is not None
    assert run2_feo.point_conditions["printed_composition"].state.value[
        "FeO"
    ] == Decimal("8.84")
    assert run2_feo.point_conditions["printed_composition"].locator is not None
    # Sample 3/1 prints H2O_wt_pct 3.03 and analytical H2O EMPA 3.25.
    assert run3_feo.identity.composition.is_unknown
    assert run3_feo.point_conditions["H2O_EMPA"].state.value == Decimal("3.25")
    located_water = Counter()
    for observation in table4_rows:
        located = observation.point_conditions.get("H2O_EMPA")
        if located is None:
            continue
        sample = observation.observation_id.split(":row=", 1)[1].split(":", 1)[0]
        located_water[(sample, located.state.value)] += 1
    assert located_water == Counter(
        {
            (sample, Decimal(weight)): count
            for sample, weight, count in _GAILLARD_TABLE4_H2O_EMPA
        }
    )
    document = yaml.safe_load(
        (EXTRACTS / "gaillard-2003-hydrous-silicic-melt-activity.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_rows = [
        (observation["observation_id"], item)
        for species in document["species"].values()
        for observation in species["observations"]
        if "table4" in observation.get("observation_id", "")
        for item in observation["values"]["series"]
    ]
    assert len(source_rows) == 96
    hydrous_source = 0
    for parent_id, item in source_rows:
        migrated = _table4_row(table4_rows, parent_id, str(item["sample"]))
        printed_water = _printed_h2o_empa(item)
        if printed_water is None:
            assert migrated.identity.composition.is_value
            assert "H2O_EMPA" not in migrated.point_conditions
            continue
        hydrous_source += 1
        assert migrated.identity.composition.is_unknown
        assert migrated.point_conditions["H2O_EMPA"].state.value == Decimal(
            printed_water
        )
        assert migrated.point_conditions["printed_composition"].locator is not None
    assert hydrous_source == 72
    assert all(
        observation.identity.temperature_K.is_value
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        and observation.point_conditions["delta_NNO"].state.is_value
        and observation.point_conditions["delta_NNO"].locator is not None
        for observation in table4_rows
    )


def test_shimaoka_nakamura_1991_migrates_table1_cells() -> None:
    result = _migrate("shimaoka-nakamura-1991-iau-colloq126-79.yaml")
    assert len(result.observations) == 30
    assert _point(result, "shimaoka_1991_na_run_89a_concentration") == Decimal(
        "2074"
    )
    document = yaml.safe_load(
        (EXTRACTS / "shimaoka-nakamura-1991-iau-colloq126-79.yaml").read_text(
            encoding="utf-8"
        )
    )
    rates = [
        observation
        for species in document["species"].values()
        for observation in species["observations"]
        if observation.get("type") == "rate_series"
        and observation.get("locator", {}).get("table") == "1"
        and observation.get("units") == "cm3 min-1 cm-2 as printed"
    ]
    assert len(rates) == 15
    for source_rate in rates:
        observation = next(
            row
            for row in result.observations.values()
            if row.observation_id.endswith(f"::{source_rate['observation_id']}")
        )
        assert source_rate["values"]["quantity"] == "per_area_volumetric_rate"
        assert source_rate["values"]["status"] == "typed_refusal"
        assert source_rate["units"] == "cm3 min-1 cm-2 as printed"
        assert source_rate["locator"]["row"]
        assert source_rate["locator"]["column"].startswith("k ")
        assert "k_cm3_min_cm2_as_printed" in source_rate["values"]
        assert observation.identity.quantity.is_unknown
        assert observation.value.kind.value == "unavailable"
        assert observation.admission.status.value == "rejected"
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
    fusion = next(
        row
        for row in result.observations.values()
        if row.observation_id.endswith(
            "::adamkovicova_1980_heat_of_fusion_at_1817K"
        )
    )
    assert fusion.identity.species.phase.is_value
    assert fusion.identity.species.phase.value.value == "cr"
    assert fusion.identity.species.polymorph.is_value
    assert fusion.identity.species.polymorph.value.value == "cyclowollastonite"
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
