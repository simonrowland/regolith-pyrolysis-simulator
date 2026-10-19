from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from collections import Counter

from simulator.battery.migrate import Migrator
from simulator.yaml_cache import YAML12SafeLoader
from tests.test_yaml_flow_scalar_commas import _walk_mapping_source_issues
import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/rys-2007-alkali-oxide-thermodynamics.yaml"


def _point_by_observation_id(document: dict, observation_id: str, index: int) -> dict:
    for species in document["species"].values():
        for observation in species.get("observations", []):
            if observation.get("observation_id") == observation_id:
                return observation["values"]["points"][index]
    raise AssertionError(f"missing observation {observation_id}")


def test_rys_extract_values_migrate_and_conditions_reach_each_row() -> None:
    source = EXTRACT.read_text(encoding="utf-8")
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        assert node is not None
        assert _walk_mapping_source_issues(node, loader, source) == []
        document = loader.construct_document(node)
    finally:
        loader.dispose()

    table7_first = _point_by_observation_id(document, "rys_2007_table7_liquidus", 0)
    assert table7_first["T_C"] == 1080
    assert table7_first["composition_mol"] == {"Na2O": "0.9598", "SiO2": "0.0401"}
    assert table7_first["composition_mol_basis"] == "printed_mole_fraction"

    table9_first = _point_by_observation_id(document, "rys_2007_table9_liquidus", 0)
    assert table9_first["T_C"] == 1089

    appendix_na = _point_by_observation_id(
        document, "rys_2007_appendix_na_partial_pressures", 0
    )
    assert appendix_na["pressure_Pa"] == 3.966701
    assert appendix_na["T_K"] == 1137.158

    table1_nao = _point_by_observation_id(
        document, "rys_2007_appendix_nao_partial_pressures_table1", 0
    )
    assert table1_nao["pressure_Pa"] == "0.000414"
    assert table1_nao["locator"]["pdf_page_index"] == 136

    table2_na2o = _point_by_observation_id(
        document, "rys_2007_appendix_na2o_partial_pressures_table2", 0
    )
    assert table2_na2o["pressure_Pa"] == "0.00019"
    assert table2_na2o["locator"]["page"] == 125

    table4_na = _point_by_observation_id(
        document, "rys_2007_appendix_na_partial_pressures_table4", 0
    )
    assert table4_na["T_K"] == "1817.784"
    assert table4_na["pressure_Pa"] == "0.102522"
    assert table4_na["locator"]["pdf_page_index"] == 137

    table4_activity = _point_by_observation_id(
        document, "rys_2007_appendix_na2o_activity_table4", 0
    )
    assert table4_activity["activity"] == "2.38E-13"

    table10_k2o_activity = _point_by_observation_id(
        document, "rys_2007_appendix_k2o_activity_table10a", 0
    )
    assert table10_k2o_activity["activity"] == "3.85E-16"

    appendix_rows = Counter()
    for species in document["species"].values():
        for observation in species.get("observations", []):
            if not observation.get("observation_id", "").startswith("rys_2007_appendix_"):
                continue
            for point in observation.get("values", {}).get("points", []):
                appendix_rows[point["locator"]["table"]] += 1
    assert sum(appendix_rows.values()) == 888
    assert appendix_rows == Counter(
        {
            "1": 59,
            "2": 38,
            "3": 47,
            "4": 56,
            "5": 68,
            "6": 56,
            "7": 74,
            "8": 76,
            "9": 72,
            "10a": 56,
            "10b": 52,
            "11a": 62,
            "11b": 64,
            "12a": 60,
            "12b": 48,
        }
    )
    assert sum([19, 10, 14, 17, 17, 18, 20, 19, 18, 14, 14, 16, 16, 15, 12]) == 239

    table16_activity = _point_by_observation_id(
        document, "rys_2007_tables16_17_19_na2o_activity_1750k", 2
    )
    assert table16_activity["activity"] == "9.740E-12"

    figure_records = [
        observation
        for species in document["species"].values()
        for section in ("observations", "context")
        for observation in species.get(section, [])
        if observation.get("type") == "figure"
    ]
    assert len(figure_records) == 87
    for figure in figure_records:
        values = figure["values"]
        assert "quantity" in values or "printed_quantity" in values
        assert all(
            field in values for field in ("species", "phase", "temperature", "axes")
        ), figure["observation_id"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["points"] == []
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 928

    for observation in result.observations.values():
        point_conditions = observation.point_conditions or {}
        condition_temperature = point_conditions.get("temperature_K")
        identity_temperature = observation.identity.temperature_K
        assert (
            identity_temperature.tag.value == "value"
            or condition_temperature is not None
        ), observation.observation_id
        assert point_conditions, observation.observation_id
        for condition in point_conditions.values():
            assert condition.locator is not None, observation.observation_id
            assert condition.state is not None, observation.observation_id

    table7_row = next(
        row
        for row in result.observations.values()
        if "rys_2007_table7_liquidus" in row.observation_id
        and row.locator is not None
        and row.locator.table == "7"
        and row.value.point == Decimal("1353.15")
    )
    composition = dict(table7_row.identity.composition.value.components)
    assert composition == {
        "Na2O": Decimal("0.9598"),
        "SiO2": Decimal("0.0401"),
    }
    assert sum(composition.values()) == Decimal("0.9999")
