from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import MethodToken, Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


EXTRACT = REPO_ROOT / "data/literature/extracts/park-2021-vanadium-thermodynamics-thesis.yaml"
SOURCE_ID = "park-2021-vanadium-thermodynamics-thesis"

# Independent transcription of every Table 9 row, checked against the 600 dpi
# render of physical PDF page 80.
TABLE9_GRID = """
CSV01|1.04|3.92|1873|3.242e-9|14.58
CSV02|0.93|3.91|1873|3.242e-9|13.23
CSV03|0.84|3.91|1873|3.242e-9|12.19
CSV04|0.76|3.90|1873|3.242e-9|11.13
CSV05|0.68|3.89|1873|3.242e-9|9.78
CSV06|0.84|1.94|1873|3.242e-9|9.28
CSV07|0.84|5.90|1873|3.242e-9|13.41
CSV08|1.04|3.92|1873|3.124e-7|5.98
CSV09|0.93|3.91|1873|3.124e-7|5.82
CSV10|0.84|3.91|1873|3.124e-7|5.83
CSV11|0.76|3.90|1873|3.124e-7|5.67
CSV12|0.68|3.89|1873|3.124e-7|5.47
CSV13|0.84|3.91|1823|3.205e-9|9.28
CSV14|0.84|3.91|1773|3.127e-9|7.25
"""

# Independent transcription of the sample-specific nominal charge table,
# also checked against the 600 dpi render of physical PDF page 75.
TABLE4_GRID = """
CSV01|50.0|45.0|5.0|1.111|1873|263|37|3.242e-9
CSV02|47.5|47.5|5.0|1.000|1873|263|37|3.242e-9
CSV03|45.0|50.0|5.0|0.900|1873|263|37|3.242e-9
CSV04|42.5|52.5|5.0|0.810|1873|263|37|3.242e-9
CSV05|40.0|55.0|5.0|0.727|1873|263|37|3.242e-9
CSV06|46.2|51.3|2.5|0.900|1873|263|37|3.242e-9
CSV07|43.8|48.7|7.5|0.900|1873|263|37|3.242e-9
CSV08|50.0|45.0|5.0|1.111|1873|126|174|3.124e-7
CSV09|47.5|47.5|5.0|1.000|1873|126|174|3.124e-7
CSV10|45.0|50.0|5.0|0.900|1873|126|174|3.124e-7
CSV11|42.5|52.5|5.0|0.810|1873|126|174|3.124e-7
CSV12|40.0|55.0|5.0|0.727|1873|126|174|3.124e-7
CSV13|45.0|50.0|5.0|0.900|1823|244|56|3.205e-9
CSV14|45.0|50.0|5.0|0.900|1773|217|83|3.127e-9
"""


def _table_rows(document: dict[str, object], table: str) -> list[dict[str, object]]:
    species = document["species"]
    assert isinstance(species, dict)
    body = species["V"]
    assert isinstance(body, dict)
    return [
        row
        for row in body["context"]
        if row["locator"].get("table") == table
    ]


def _assert_table9_grid(document: dict[str, object], grid: str = TABLE9_GRID) -> None:
    expected = [line.split("|") for line in grid.strip().splitlines()]
    species = document["species"]
    assert isinstance(species, dict)
    rows = [
        row
        for row in species["V"]["observations"]
        if row["locator"].get("table") == "9"
    ]
    assert len(expected) == len(rows) == 14
    actual_by_sample = {row["experiment"].rsplit("_", 1)[-1].upper(): row for row in rows}
    assert set(actual_by_sample) == {fields[0] for fields in expected}

    charge_rows = {
        fields[0]: fields
        for fields in (line.split("|") for line in TABLE4_GRID.strip().splitlines())
    }
    pO2_pa = {
        "3.242e-9": "0.000328495650",
        "3.124e-7": "0.0316539300",
        "3.205e-9": "0.000324746625",
        "3.127e-9": "0.000316843275",
    }
    # Keep the expected pressure constants explicit so the test does not repeat
    # a production unit-conversion formula.
    for sample, basicity, vox, temperature, pO2, v_percent in expected:
        row = actual_by_sample[sample]
        values = row["values"]
        charge = charge_rows[sample]
        assert row["phase"] == "cr"
        assert row["locator"]["pdf_page_index"] == 79
        assert row["locator"]["page"] == 80
        assert values["component_amount"] == str(Decimal(v_percent) / Decimal("100"))
        assert values["component_amount_as_published"] == f"{v_percent} mol%"
        assert values["phase_label"] == "metal"
        assert values["basis"] == "mole_fraction"
        assert values["T_C"] == str(Decimal(temperature) - Decimal("273.15"))
        conditions = values["point_conditions"]
        oxygen = conditions["pO2_Pa"]
        assert oxygen["state"]["value"] == pO2_pa[pO2]
        assert oxygen["locator"]["page"] == 80
        composition = conditions["composition"]
        assert composition["locator"]["page"] == 75
        assert composition["state"]["value"]["components"] == [
            ["CaO", charge[1]], ["SiO2", charge[2]], ["V2O3", charge[3]]
        ]
        assert row["note"].startswith(f"Table 9 reports {v_percent} mol% V")


def test_table9_rows_match_independent_render_transcription() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_table9_grid(document)


def test_table9_grid_rejects_expected_and_extract_cell_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_grid = TABLE9_GRID.replace("14.58", "14.59", 1)
    with pytest.raises(AssertionError):
        _assert_table9_grid(document, changed_grid)

    mutated = deepcopy(document)
    sample = next(
        row
        for row in mutated["species"]["V"]["observations"]
        if row["experiment"] == "park_2021_csv01"
    )
    sample["values"]["component_amount"] = "0.1459"
    with pytest.raises(AssertionError):
        _assert_table9_grid(mutated)


def test_context_tables_retain_every_printed_row_and_cell() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table4 = _table_rows(document, "4")[0]["values"]["rows_as_printed"]
    table8 = _table_rows(document, "8")[0]["values"]["rows_as_printed"]
    table10 = _table_rows(document, "10")[0]["values"]["rows_as_printed"]
    table11 = _table_rows(document, "11")[0]["values"]["rows_as_printed"]
    table12 = _table_rows(document, "12")[0]["values"]["rows_as_printed"]
    assert len(table4) == 14
    assert len(table8) == 13
    assert len(table10) == len(table11) == len(table12) == 13
    assert table4[0]["sample"] == "CSV01" and table4[-1]["pO2_as_printed"] == "3.127 × 10^-9 atm"
    assert table8[0]["V2_plus_fraction"] == "0.0309" and table8[-1]["x_of_VOx"] == "1.869"
    assert table10[0]["a_VO1_5_x_10"] == "1.534" and table10[-1]["a_VO2_5_x_1e2"] == "0.936"
    assert table11[0]["ln_gamma_VO2_5"] == "-2.590" and table11[-1]["ln_gamma_VO"] == "1.340"
    assert table12[0]["a_VOx_x_1e2"] == "4.115" and table12[-1]["gamma_VOx"] == "0.182"
    figures = [row for row in document["species"]["V"]["context"] if row["type"] == "figure_context"]
    assert len(figures) == 29
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("marker_count_per_series" in row["values"] for row in figures)


def test_scored_table9_rows_migrate_as_points_with_printed_identity_axes() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = document["species"]["V"]["observations"]
    assert len(source_rows) == 14

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index=None, aliases={})
    migrator.finalize()
    assert not migrator.result.registry_issues
    migrated = migrator.result.observations
    assert len(migrated) == len(source_rows) == 14
    assert len(migrator.result.experiments) == 14

    for raw in source_rows:
        values = raw["values"]
        assert raw["regime"] == "quench_equilibration"
        observation = migrated[f"{SOURCE_ID}::{raw['observation_id']}"]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(values["component_amount"])
        assert not observation.notices
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == "V"
        assert observation.identity.species.phase.value is Phase.CR
        assert observation.identity.phase_role.value is PhaseRole.METAL
        assert observation.identity.subtype.value == "mole_fraction"
        assert observation.identity.temperature_K.value == Decimal(values["T_C"]) + Decimal("273.15")
        experiment = next(
            item
            for item in migrator.result.experiments.values()
            if item.experiment_id.endswith(f"::{raw['experiment']}")
        )
        assert observation.experiment_id == experiment.experiment_id
        assert experiment.method.value is MethodToken.QUENCH_EQUILIBRATION
        assert experiment.conditions["temperature_K"].state.value == Decimal(values["T_C"]) + Decimal("273.15")
        expected_pressure = Decimal(values["point_conditions"]["pO2_Pa"]["state"]["value"])
        assert observation.identity.fO2_Pa.value == expected_pressure
        assert observation.point_conditions["pO2_Pa"].state.value == expected_pressure
        assert observation.point_conditions["pO2_Pa"].locator.page == 80
        assert observation.point_conditions["composition"].locator.page == 75
        assert observation.identity.composition is not None
        assert observation.identity.composition.is_value

