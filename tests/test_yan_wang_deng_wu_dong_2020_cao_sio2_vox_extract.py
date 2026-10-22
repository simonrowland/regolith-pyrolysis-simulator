from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/yan-wang-deng-wu-dong-2020-cao-sio2-vox-gibbs-energy.yaml"
)
SOURCE_ID = "yan-wang-deng-wu-dong-2020-cao-sio2-vox-gibbs-energy"
TABLE1_LIQUID_GRID = """
101|25/70/5|33.2 ± 0.4|58.1 ± 0.6|4.7 ± 0.2|96.0 ± 0.4|34.6/60.5/4.9
102|22.5/70/7.5|31.6 ± 0.2|59.1 ± 0.6|8.0 ± 0.2|98.7 ± 0.8|32.0/59.9/8.1
103|30/67.5/2.5|35.2 ± 0.6|61.8 ± 0.7|2.1 ± 0.1|99.0 ± 0.5|35.5/62.4/2.1
104|20/50/30|30.8 ± 0.6|54.1 ± 0.2|14.2 ± 0.2|99.1 ± 0.5|31.2/54.4/14.4
105|10/70/20|28.9 ± 1.1|53.9 ± 1.6|15.3 ± 0.1|98.1 ± 1.5|29.7/54.3/16.0
106|10/45/45|30.4 ± 0.7|53.7 ± 0.7|14.2 ± 0.3|98.3 ± 0.6|30.8/54.2/15.0
107|10/50/40|31.0 ± 0.3|52.8 ± 21|12.8 ± 0.6|96.6 ± 1.8|32.3/54.5/13.2
108|15/45/40|29.8 ± 0.3|51.6 ± 1.7|14.0 ± 0.9|95.4 ± 0.7|31.4/53.8/14.8
109|10/40/50|31.9 ± 1.5|51.9 ± 0.9|15.0 ± 0.8|98.8 ± 0.3|32.0/52.7/15.3
110|40/30/30|44.5 ± 0.4|31.2 ± 0.3|22.1 ± 0.3|97.8 ± 0.6|45.5/31.9/22.6
111|25/35/40|35.0 ± 0.7|47.7 ± 1.1|12.3 ± 1.4|95.0 ± 0.4|36.6/49.9/13.5
112|30/30/40|41.5 ± 0.8|40.9 ± 0.7|12.7 ± 1.0|95.1 ± 0.6|43.7/43.0/13.4
113|35/25/40|42.7 ± 1.1|29.3 ± 0.6|22.1 ± 1.5|94.1 ± 0.5|45.4/31.1/23.5
114|25/25/50|41.5 ± 0.5|41.8 ± 0.7|14.5 ± 1.2|97.8 ± 0.6|42.4/42.7/14.9
115|60/30/10|51.3 ± 0.6|26.5 ± 0.2|19.2 ± 0.6|97.0 ± 0.2|52.3/27.7/20.0
116|52.5/27.5/20|50.0 ± 0.6|28.0 ± 0.3|20.4 ± 0.6|98.4 ± 0.6|50.8/28.4/20.8
117|55/35/10|53.4 ± 0.1|34.7 ± 0.3|8.9 ± 0.1|97.0 ± 0.4|55.0/35.8/9.2
118|60/37.5/2.5|51.2 ± 1.2|40.2 ± 0.4|3.6 ± 0.2|95.0 ± 0.7|53.9/42.3/3.8
119|62.5/32.5/5|51.3 ± 0.6|33.7 ± 0.4|12.2 ± 0.4|97.2 ± 0.9|52.5/34.3/13.2
120|20/5/75|50.8 ± 1.5|21.2 ± 1.7|26.9 ± 1.7|98.9 ± 1.5|50.5/22.7/26.8
121|35/5/60|49.0 ± 1.8|22.3 ± 1.1|25.4 ± 1.00|96.7 ± 0.3|49.1/24.1/26.8
122|30/10/60|49.0 ± 1.1|21.1 ± 1.4|26.4 ± 1.3|96.5 ± 2.8|50.0/22.8/27.2
123|40/5/55|49.4 ± 1.0|21.4 ± 0.7|26.0 ± 0.8|96.8 ± 0.8|50.2/22.3/27.2
124|40/10/50|49.0 ± 0.6|22.3 ± 0.3|25.3 ± 1.5|96.6 ± 0.8|50.0/23.3/26.6
125|52.5/25/22.5|49.5 ± 0.9|25.8 ± 1.3|21.5 ± 2.5|96.8 ± 2.4|51.3/26.6/22.1
126|50/10/40|50.0 ± 0.9|24.7 ± 1.1|22.4 ± 1.5|97.1 ± 1.9|52.0/25.3/22.7
127|60/25/15|50.7 ± 0.4|24.2 ± 0.6|21.4 ± 0.9|96.3 ± 0.7|52.0/25.3/22.7
128|55/25/20|50.5 ± 0.6|24.4 ± 0.6|21.6 ± 0.6|96.5 ± 0.6|51.6/25.4/23.0
129|45/50/5|44.1 ± 0.6|48.6 ± 0.6|4.5 ± 0.5|97.2 ± 1.6|45.4/49.7/4.9
"""
TABLE1_SOLID_GRID = """
101|SiO2|0.1/95.9/0|96.0|0.1/99.9/0
102|SiO2|0.2/94.7/0.1|95.0|0.2/99.7/0.1
103|SiO2|0.2/97.0/0|97.2|0.2/99.8/0
104|SiO2|0/99.5/0.3|99.8|0/99.7/0.3
104|V2O3|0.3/0.1/99.0|99.4|0.3/0.1/99.6
105|SiO2|0.1/96.9/0.2|97.2|0.9/97.5/1.6
105|V2O3|0.4/0.1/105.1|105.6|0.4/0.1/99.5
106|SiO2|0/98.6/0.4|99.0|0/99.6/0.4
106|V2O3|0.4/0.3/105.9|106.6|0.4/0.3/99.3
107|SiO2|0.1/97.1/0.2|97.4|0.1/99.7/0.2
107|V2O3|0.6/0.5/96.5|97.6|0.7/0.6/98.7
108|SiO2|0.1/96.7/0.2|97.0|0.1/99.7/0.2
108|V2O3|0.4/0/96.3|96.7|0.5/0/99.5
109|SiO2|0/97.6/0.2|97.8|0/99.8/0.2
109|V2O3|0.3/0.1/103.3|103.7|0.3/0.1/99.6
110|V2O3|0.4/0.2/105.1|105.7|0.4/0.1/99.5
111|V2O3|0.3/0.1/105.9|106.3|0.4/0.1/99.5
112|V2O3|0.4/0/101.1|101.5|0.4/0/99.6
113|V2O3|0.4/0.1/103.4|103.9|0.4/0.1/99.5
114|V2O3|0.4/0/101.8|102.2|0.4/0/99.6
115|Ca2Si1−δVδO4|65.0 ± 1.0/31.7 ± 0.5/3.3 ± 0.2|100.0 ± 1.3|64.9/31.8/3.3
116|Ca2Si1−δVδO4|66.2 ± 0.8/31.8 ± 0.2/3.4 ± 0.3|101.4 ± 0.8|65.4/31.3/3.3
117|Ca2Si1−δVδO4|67.9 ± 0.1/33.7 ± 0.1/1.9 ± 0.1|103.5 ± 0.2|65.6/32.5/1.9
118|Ca2Si1−δVδO4|67.2 ± 0.7/33.3 ± 0.4/0.6 ± 0.1|100.1 ± 2.3|66.9/32.5/0.6
119|Ca2Si1−δVδO4|66.4 ± 0.6/33.4 ± 0.6/2.2 ± 0.1|102.0 ± 0.7|65.1/32.8/2.1
120|CaV1−δSiδO3|42.5 ± 0.1/2.8 ± 0.4/52.9 ± 1.0|98.2 ± 0.7|43.4/3.1/53.5
120|V2O3|0.1 ± 0.1/0/95.8 ± 0.8|95.9 ± 0.8|0.1/0/99.9
121|CaV1−δSiδO3|41.4 ± 0.5/1.1 ± 0.1/54.2 ± 1.0|96.7 ± 0.9|42.8/1.3/55.9
121|V2O3|0.8 ± 0.9/0/104.8 ± 0.8|105.6 ± 1.2|0.6/0/99.4
122|CaV1−δSiδO3|42.7 ± 0.1/4.0 ± 0.3/49.6 ± 1.4|96.3 ± 1.5|44.4/4.2/51.4
122|V2O3|0.7/1.6/97.0|99.3|0.8/1.6/97.6
123|CaV1−δSiδO3|42.2 ± 0.9/2.3 ± 1.0/52.5 ± 1.4|97.0 ± 1.7|43.5/2.9/53.6
123|V2O3|0.3/0.2/100.1|100.6|0.3/0.8/98.9
124|CaV1−δSiδO3|42.7 ± 0.1/4.0 ± 0.3/48.9 ± 0.2|95.6 ± 0.5|44.6/4.2/51.2
124|V2O3|1.9/0.1/103.1|105.1|1.8/0.1/98.1
125|CaV1−δSiδO3|41.8 ± 0.6/3.9 ± 0.1/51.2 ± 0.9|96.9 ± 1.0|42.9/3.9/53.2
125|Ca2Si1−δVδO4|65.8 ± 0.4/31.2 ± 0.6/4.1 ± 0.1|101.1 ± 0.5|64.9/31.1/4.0
126|CaV1−δSiδO3|40.7 ± 0.6/0.5 ± 0.4/55.4 ± 0.4|96.6 ± 0.5|42.6/0.9/56.5
126|Ca2Si1−δVδO4|65.1 ± 1.0/30.4 ± 0.5/3.9 ± 0.5|99.4 ± 0.6|65.0/31.0/4.0
127|CaV1−δSiδO3|41.4 ± 1.1/3.3 ± 0.6/52.3 ± 0.6|97.0 ± 1.1|43.2/4.2/52.6
127|Ca2Si1−δVδO4|65.0 ± 0.8/29.7 ± 1.2/4.2 ± 0.1|98.9 ± 1.1|65.7/30.0/4.3
128|CaV1−δSiδO3|41.0 ± 1.1/3.5 ± 0.5/52.1 ± 1.1|96.5 ± 0.9|42.3/3.9/53.8
128|Ca2Si1−δVδO4|64.6 ± 0.7/30.3 ± 1.2/2.9 ± 0.2|97.8 ± 2.0|65.8/31.3/2.9
"""


def _table1_liquid_rows(document: dict[str, object]) -> dict[str, dict[tuple[str, str], dict[str, object]]]:
    species = document["species"]
    assert isinstance(species, dict)
    result: dict[str, dict[tuple[str, str], dict[str, object]]] = {}
    for body in species.values():
        if not isinstance(body, dict):
            continue
        for row in body.get("observations", []):
            if row["locator"].get("table") != "1":
                continue
            values = row["values"]
            sample = str(row["experiment"]).rsplit("_", 1)[-1]
            result.setdefault(sample, {})[(str(values["method_class"]), str(values["formula"]))] = row
    return result


def _assert_rendered_table1_grid(
    document: dict[str, object], grid: str = TABLE1_LIQUID_GRID
) -> None:
    expected_rows = [line.split("|") for line in grid.strip().splitlines()]
    actual = _table1_liquid_rows(document)
    assert len(actual) == 29
    assert set(actual) == {fields[0] for fields in expected_rows}
    formulas = ("CaO", "SiO2", "V2O3")
    for fields in expected_rows:
        sample, initial_text, cao, sio2, v2o3, total, normalized_text = fields
        assert len(fields) == 7
        assert len(actual[sample]) == 6
        initial = initial_text.split("/")
        normalized = normalized_text.split("/")
        measured = (cao, sio2, v2o3)
        for formula, cell, normalized_cell, initial_value in zip(
            formulas, measured, normalized, initial, strict=True
        ):
            direct = actual[sample][("measured_direct", formula)]
            reduced = actual[sample][("measured_reduced", formula)]
            for row in (direct, reduced):
                values = row["values"]
                assert values["phase_label"] == "melt"
                assert values["basis"] == "wt_percent"
                assert values["T_C"] == "1599.85"
                assert row["locator"]["page"] == (
                    6 if int(sample) <= 105 else 7 if int(sample) <= 119 else 8
                )
                composition = values["point_conditions"]["composition"]["state"]["value"]
                assert composition["components"] == [
                    ["CaO", initial[0]], ["SiO2", initial[1]], ["V2O3", initial[2]]
                ]
            printed_value, _, printed_sigma = cell.partition(" ± ")
            assert direct["values"]["component_amount"] == printed_value
            assert direct["uncertainty"]["sigma"] == printed_sigma
            assert direct["values"]["printed_sum"] == total.partition(" ± ")[0]
            assert direct["values"]["printed_sum_as_published"] == total
            assert reduced["values"]["component_amount"] == normalized_cell
            assert "uncertainty" not in reduced

    species = document["species"]
    assert isinstance(species, dict)
    solid_rows = {
        (str(row["values"]["sample"]), str(row["values"]["formula_as_printed"])): row
        for body in species.values()
        if isinstance(body, dict)
        for row in body.get("context", [])
        if row["locator"].get("table") == "1"
    }
    expected_solids = [line.split("|") for line in TABLE1_SOLID_GRID.strip().splitlines()]
    assert len(solid_rows) == 43
    assert len(expected_solids) == 43
    for sample, formula, measured_text, total, normalized_text in expected_solids:
        row = solid_rows[(sample, formula)]
        values = row["values"]
        assert values["measured_components_as_printed"] == [
            [oxide, value]
            for oxide, value in zip(("CaO", "SiO2", "V2O3"), measured_text.split("/"), strict=True)
        ]
        assert values["measured_total_as_printed"] == total
        assert values["normalized_components_as_printed"] == [
            [oxide, value]
            for oxide, value in zip(("CaO", "SiO2", "V2O3"), normalized_text.split("/"), strict=True)
        ]


def test_table1_liquid_grid_matches_independent_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_rendered_table1_grid(document)


def test_table1_grid_rejects_expected_and_extract_cell_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = TABLE1_LIQUID_GRID.replace("33.2 ± 0.4", "33.3 ± 0.4", 1)
    with pytest.raises(AssertionError):
        _assert_rendered_table1_grid(document, changed_grid)

    mutated = deepcopy(document)
    sample101_cao = next(
        row
        for body in mutated["species"].values()
        for row in body.get("observations", [])
        if row["observation_id"] == "yan_2020_table_1_sample_101_measured_liquid_cao"
    )
    sample101_cao["values"]["component_amount"] = "33.3"
    with pytest.raises(AssertionError):
        _assert_rendered_table1_grid(mutated)


def test_all_scored_rows_migrate_as_points_with_printed_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    source_rows = [
        (container, row)
        for container, body in species.items()
        for row in body.get("observations", [])
    ]
    assert len(source_rows) == 230

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations
    assert len(migrated) == len(source_rows)
    assert not migrator.result.registry_issues

    for container, raw in source_rows:
        values = raw["values"]
        observation = migrated[f"{SOURCE_ID}::{raw['observation_id']}"]
        quantity = values["quantity"]
        assert observation.value.kind is ValueKind.POINT, raw["observation_id"]
        assert not observation.notices, raw["observation_id"]
        raw_conditions = values.get("point_conditions", raw.get("point_conditions", {}))
        if quantity == "equilibrium_phase_composition":
            expected_value = Decimal(str(values["component_amount"]))
            assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
            assert observation.identity.species.formula == values["formula"]
            assert observation.identity.temperature_K.value == Decimal("1873")
            expected_oxygen = Decimal(str(raw_conditions["pO2_Pa"]["state"]["value"]))
            assert observation.identity.fO2_Pa.value == expected_oxygen
            assert observation.point_conditions["pO2_Pa"].state.value == expected_oxygen
            composition = observation.identity.composition
            assert composition is not None and composition.is_value, raw["observation_id"]
            printed_components = raw_conditions["composition"]["state"]["value"]["components"]
            expected_components = {
                oxide
                for oxide, amount in printed_components
                if Decimal(str(amount)) > 0
            }
            assert set(dict(composition.value.components)) == expected_components
            assert observation.identity.phase_role.value in {PhaseRole.MELT, PhaseRole.METAL}
            assert observation.identity.subtype.value == values["basis"]
        elif quantity == "delta_fG":
            expected_value = Decimal(str(values["delta_fG"])) / Decimal("1000")
        elif quantity == "activity_coefficient":
            expected_value = Decimal(str(values["activity_coefficient"]))
        else:
            raise AssertionError(f"unhandled scored quantity {quantity!r}")
        assert observation.value.point == expected_value, raw["observation_id"]

        raw_temperature = values.get(
            "T_C",
            values.get("T_K", values.get("temperature_K", raw.get("T_K"))),
        )
        if raw_temperature is not None:
            assert observation.identity.temperature_K.value == Decimal("1873")
            assert "temperature_K" in observation.point_conditions
        if "pO2_Pa" in raw_conditions:
            oxygen = raw_conditions["pO2_Pa"]["state"]["value"]
            assert observation.point_conditions["pO2_Pa"].state.value == Decimal(str(oxygen))
            assert observation.point_conditions["pO2_Pa"].locator.page == raw_conditions["pO2_Pa"]["locator"]["page"]
        assert observation.identity.species.formula == str(values.get("formula") or container)

    contexts = [row for rows in migrator.result.context_by_work.values() for row in rows]
    assert contexts
    table1_contexts = [
        row
        for row in contexts
        if row.get("locator", {}).get("table") == "1"
    ]
    assert len(table1_contexts) == 43
    assert all(
        row["values"].get("candidate_token") == "equilibrium_phase_composition"
        and len(row["values"]["measured_components_as_printed"]) == 3
        and len(row["values"]["normalized_components_as_printed"]) == 3
        for row in table1_contexts
    )
