from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/liu-2024-cao-al2o3-vox-phase.yaml"
)
SOURCE_ID = "liu-2024-cao-al2o3-vox-phase"
INCOMPLETE_CHARGES = {1, 2, 8, 9, 11}

# Independent transcription from the 600 dpi render of physical page 6.
TABLE3_RENDER_GRID = """sample_id|group|phase|Ca|Al|V|O
1#|1|Glass|40.58|-|26.44|32.98
2#|2|Glass|-|2.80|42.93|54.27
|2|VO2|-|-|49.57|50.43
3#|3|Glass|11.02|4.13|40.42|44.43
|3|Al2O3|-|49.06|4.56|46.38
4#|4|Glass|47.84|11.55|11.06|29.55
|4|CaO|61.69|2.34|2.21|33.76
5#|5|Glass|42.34|17.94|3.69|36.03
|5|CaO|63.14|2.83|-|34.03
|5|Ca3Al2O6|42.33|17.94|3.68|36.05
6#|6|Glass|33.79|11.79|17.50|36.92
|6|CaAl2O4|27.56|29.83|4.32|38.29
|6|CaAl4O7|16.41|39.95|-|43.64
7#|7|Glass|28.82|12.46|19.70|39.02
|7|CaAl12O19|-|47.97|-|52.03
8#|8|Glass|13.45|-|43.00|43.55
|8|VO2|-|-|51.14|48.86
9#|9|Glass|42.20|-|22.79|35.01
|9|CaO|49.54|-|2.04|48.42
10#|10|Glass|6.97|2.88|43.77|46.38
|10|VO2|-|1.50|49.46|49.04
11#|11|Glass|-|15.74|40.60|43.66
|11|Al2O3|-|48.13|5.82|46.05
12#|12|Glass|39.30|21.09|6.69|32.92
|12|CaAl2O4|27.97|32.70||39.33
13#|13|Glass|30.10|12.84|20.39|36.67
|13|CaAl4O7|17.44|41.20|-|41.37
|13|CaAl12O19|6.84|45.13|-|48.03
14#|14|Glass|25.88|5.82|30.08|38.22
|14|CaAl12O19|5.77|48.11|-|46.12
|14|Al2O3|-|42.17|-|57.83
15#|15|Glass|14.97|1.38|41.10|42.55
|15|VO2|0.94|0.35|46.21|52.49
16#|16|Glass|20.26|3.30|35.19|41.25
|16|Al2O3|0.43|49.49|4.60|45.48
17#|17|Glass|32.90|5.07|24.73|37.30
|17|CaAl4O7|16.00|37.49|-|46.51
18#|18|Glass|43.80|6.14|15.32|34.74
|18|CaO|29.67|0.79|2.57|66.97"""


def _printed_component(value: object) -> str:
    text = str(value)
    return text.removesuffix(" mass%")


def _assert_table3_matches_render(
    document: dict[str, object], grid: str = TABLE3_RENDER_GRID
) -> None:
    expected = [line.split("|") for line in grid.strip().splitlines()]
    assert expected[0] == ["sample_id", "group", "phase", "Ca", "Al", "V", "O"]
    expected = expected[1:]
    actual: dict[tuple[int, str], dict[str, str]] = {}
    species = document["species"]
    assert isinstance(species, dict)
    for body in species.values():
        if not isinstance(body, dict):
            continue
        for observation in body.get("observations", []):
            if observation["locator"].get("table") != "3":
                continue
            values = observation["values"]
            group = int(str(observation["experiment"]).rsplit("_", 1)[-1])
            row = actual.setdefault((group, "Glass"), {"sample_id": f"{group}#"})
            row[str(values["formula"])] = _printed_component(
                values["component_amount_as_published"]
            )
        for record in body.get("context", []):
            if record.get("locator", {}).get("table") != "3":
                continue
            for printed in record["values"]["rows_as_printed"]:
                group = int(printed["sample_group"])
                phase = str(printed["phase_as_printed"])
                key = (group, phase)
                assert key not in actual
                actual[key] = {
                    "sample_id": str(printed["sample_id_as_printed"]),
                    "Ca": str(printed["Ca_mass_percent"]),
                    "Al": str(printed["Al_mass_percent"]),
                    "V": str(printed["V_mass_percent"]),
                    "O": str(printed["O_mass_percent"]),
                }

    assert len(expected) == len(actual) == 39
    assert set(actual) == {(int(row[1]), row[2]) for row in expected}
    for sample_id, group_text, phase, ca, al, vanadium, oxygen in expected:
        row = actual[(int(group_text), phase)]
        assert row == {
            "sample_id": sample_id,
            "Ca": ca,
            "Al": al,
            "V": vanadium,
            "O": oxygen,
        }


def test_table3_full_grid_matches_independent_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table3_matches_render(document)


def test_table3_render_grid_and_extract_cell_mutations_fail() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = TABLE3_RENDER_GRID.replace("40.58", "40.59", 1)
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(document, changed_grid)

    mutated = deepcopy(document)
    row = next(
        observation
        for body in mutated["species"].values()
        for observation in body.get("observations", [])
        if observation["observation_id"] == "liu_2024_table3_sample_01_glass_ca"
    )
    row["values"]["component_amount_as_published"] = "40.59 mass%"
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(mutated)


def test_table3_migrates_every_component_with_temperature_and_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = [
        (container, row)
        for container, body in document["species"].items()
        for row in body.get("observations", [])
        if row["locator"].get("table") == "3"
    ]
    assert len(source_rows) == 72

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    migrated = migrator.result.observations
    assert len(migrated) == len(source_rows)

    point_count = unavailable_count = 0
    for container, raw in source_rows:
        values = raw["values"]
        observation = migrated[f"{SOURCE_ID}::{raw['observation_id']}"]
        formula = str(values["formula"])
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == formula
        assert observation.identity.species.phase.value is Phase.L
        assert observation.identity.phase_role.value is PhaseRole.MELT
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value == Decimal("1773.15")
        assert observation.point_conditions["temperature_K"].state.value == Decimal("1773.15")
        assert observation.point_conditions["temperature_K"].locator.page == 3
        assert observation.identity.fO2_Pa.value == Decimal("101.325")
        assert observation.point_conditions["pO2_Pa"].state.value == Decimal("101.325")
        assert observation.point_conditions["pO2_Pa"].locator.page == 3
        assert observation.locator.page == 6
        assert raw["experiment"] == f"liu_2024_sample_{raw['observation_id'].split('_sample_')[1][:2]}"

        sample = int(raw["experiment"].rsplit("_", 1)[-1])
        initial = observation.identity.composition
        assert initial is not None
        source_composition = values["point_conditions"]["composition"]
        migrated_composition = observation.point_conditions["composition"]
        assert migrated_composition.locator.page == 3
        if sample in INCOMPLETE_CHARGES:
            assert source_composition["state"]["tag"] == "unknown"
            assert initial.is_unknown
            assert migrated_composition.state.is_unknown
            assert "partial_composition" in migrated_composition.state.reason
        else:
            assert source_composition["state"]["tag"] == "value"
            assert initial.is_unknown
            assert migrated_composition.state.is_unknown
            assert "unsupported" in migrated_composition.state.reason

        printed = str(values["component_amount_as_published"])
        if printed == "-":
            assert values["component_amount"] is None
            assert observation.value.kind is ValueKind.UNAVAILABLE
            assert "unreported, not zero" in raw["note"]
            unavailable_count += 1
        else:
            assert observation.value.kind is ValueKind.POINT
            assert observation.value.point == Decimal(str(values["component_amount"]))
            assert not observation.notices
            point_count += 1
        assert container in {"SiO2", "CaO", "Al2O3", "VO2"}

    assert (point_count, unavailable_count) == (67, 5)
