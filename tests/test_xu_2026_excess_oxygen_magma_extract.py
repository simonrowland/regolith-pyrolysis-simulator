from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import (
    Phase,
    PhaseRole,
    Quantity,
    RawInputStatus,
    UncertaintyKind,
    ValueKind,
)
from simulator.battery.identity import celsius_to_kelvin
from tests.battery.factories import migrate_one_extract
from tests.battery.literature_extract_grids import (
    assert_observation_grid_matches,
    decimal_text,
    read_observation_grid_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/xu-2026-excess-oxygen-magma.yaml"
TABLE1_GRID = ROOT / "tests/fixtures/xu-2026-table1-render-grid.tsv"
S2_GRID = ROOT / "tests/fixtures/xu-2026-table-s2-glass-grid.tsv"
GRID_FIELDS = (
    "observation_id",
    "species_formula",
    "sample_id_as_published",
    "experiment",
    "T_C",
    "total_pressure_Pa",
    "oxygen_source",
    "phase_label_as_published",
    "assemblage_as_published",
    "basis",
    "component_amount",
    "component_amount_as_published",
    "sigma",
    "page",
    "table",
    "method_class",
)
NUMERIC_FIELDS = ("T_C", "total_pressure_Pa", "component_amount", "sigma")


def _fixture(path: Path) -> list[dict[str, str]]:
    return read_observation_grid_fixture(path, NUMERIC_FIELDS)


def _extract_grid(document: dict, prefixes: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for species_formula, species in document["species"].items():
        for observation in species.get("observations", []):
            observation_id = observation["observation_id"]
            if not observation_id.startswith(prefixes):
                continue
            values = observation["values"]
            locator = observation["locator"]
            conditions = values["point_conditions"]
            rows.append(
                {
                    "observation_id": observation_id,
                    "species_formula": values.get("formula", species_formula),
                    "sample_id_as_published": values["sample_id_as_published"],
                    "experiment": observation["experiment"],
                    "T_C": decimal_text(values["T_C"]),
                    "total_pressure_Pa": decimal_text(
                        conditions["total_pressure_Pa"]["state"]["value"]["point"]
                    ),
                    "oxygen_source": values["oxygen_source_as_published"],
                    "phase_label_as_published": values["phase_label_as_published"],
                    "assemblage_as_published": values["assemblage_as_published"],
                    "basis": values["basis"],
                    "component_amount": decimal_text(values["component_amount"]),
                    "component_amount_as_published": str(values["component_amount_as_published"]),
                    "sigma": decimal_text((observation.get("uncertainty") or {}).get("sigma")),
                    "page": str(locator.get("page", "")),
                    "table": str(locator["table"]),
                    "method_class": values["method_class"],
                }
            )
    return sorted(rows, key=lambda row: row["observation_id"])


def _assert_grid(document: dict, prefixes: tuple[str, ...], fixture: list[dict[str, str]]) -> None:
    assert_observation_grid_matches(_extract_grid(document, prefixes), fixture, GRID_FIELDS)


def test_independent_render_and_workbook_grids_reject_both_mutations() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1 = _fixture(TABLE1_GRID)
    s2 = _fixture(S2_GRID)
    assert len(table1) == 273
    assert len(s2) == 1188
    _assert_grid(document, ("xu_2026_table1_",), table1)
    _assert_grid(document, ("xu_2026_s2_",), s2)

    changed_fixture = deepcopy(table1)
    changed_fixture[0]["component_amount"] = str(
        Decimal(changed_fixture[0]["component_amount"]) + 1
    )
    with pytest.raises(AssertionError):
        _assert_grid(document, ("xu_2026_table1_",), changed_fixture)

    changed_extract = deepcopy(document)
    first_id = table1[0]["observation_id"]
    first = next(
        observation
        for species in changed_extract["species"].values()
        for observation in species.get("observations", [])
        if observation["observation_id"] == first_id
    )
    first["values"]["component_amount"] = str(
        Decimal(first["values"]["component_amount"]) + 1
    )
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract, ("xu_2026_table1_",), table1)


def test_typed_glass_components_migrate_with_exact_samples_conditions_and_points() -> None:
    table1 = _fixture(TABLE1_GRID)
    s2 = _fixture(S2_GRID)
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    experiments = {row["experiment_id"]: row for row in document["experiments"]}
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.validation.hard_issues

    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == len(table1) + len(s2) + 1
    for row in table1 + s2:
        observation = observations[row["observation_id"]]
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(row["component_amount"])
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.formula == row["species_formula"]
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.identity.phase_role.value is PhaseRole.GLASS
        assert observation.identity.subtype.value == "wt_percent"
        assert observation.identity.temperature_K.value == celsius_to_kelvin(
            Decimal(row["T_C"])
        )
        assert observation.identity.total_pressure_Pa.value == Decimal(
            row["total_pressure_Pa"]
        )

        temperature = observation.point_conditions["T_C"]
        assert temperature.state.value == Decimal(row["T_C"])
        assert temperature.locator is not None
        assert temperature.locator.page == 3
        assert temperature.locator.pdf_page_index == 2

        pressure = observation.point_conditions["total_pressure_Pa"]
        pressure_value = pressure.state.value
        if hasattr(pressure_value, "point"):
            pressure_value = pressure_value.point
        assert Decimal(str(pressure_value)) == Decimal(row["total_pressure_Pa"])
        buffer_text = experiments[row["experiment"]]["fO2_control"]["buffer"]["state"]["value"]
        if row["oxygen_source"] == "PtO2":
            assert "PtO2 powder" in buffer_text
            assert "log fO2 ≈ 0" in buffer_text
        else:
            assert "No PtO2 powder" in buffer_text
        assert not observation.notices

        if row["sigma"]:
            assert observation.uncertainty.kind is UncertaintyKind.PRINTED
            assert Decimal(str(observation.uncertainty.verbatim["sigma"])) == Decimal(
                row["sigma"]
            )
            assert observation.uncertainty.verbatim["units"] == "wt.%"
        else:
            assert observation.uncertainty.kind is UncertaintyKind.NONE

        if row["table"] == "1":
            assert observation.locator.page == 3
            assert observation.locator.pdf_page_index == 2
            assert observation.locator.table == "1"
        else:
            assert observation.locator.page is None
            assert "cell " in observation.locator.record

    fe = observations["xu_2026_moessbauer_starting_glass_fe3_fet"]
    assert fe.value.kind is ValueKind.POINT
    assert fe.value.point == Decimal(7) / Decimal(3)
    assert fe.identity.quantity.value is Quantity.FE3_FE2_RATIO
    assert fe.identity.species.formula == "Fe"
    assert fe.identity.species.phase.value is Phase.GLASS
    assert fe.uncertainty.kind is UncertaintyKind.NONE
    fe_source = next(
        row
        for row in document["species"]["Fe"]["observations"]
        if row["observation_id"] == "xu_2026_moessbauer_starting_glass_fe3_fet"
    )
    assert fe_source["values"]["value_as_published"] == "70(3)%"
    assert "±0.03 applies to the ferric fraction" in fe_source["values"]["note"]
    assert fe.derivation is not None
    assert fe.derivation.raw_inputs is not None
    assert fe.derivation.raw_inputs.state.value is RawInputStatus.NOT_PRINTED
    assert fe.derivation.raw_inputs.locator is not None
    assert fe.derivation.raw_inputs.locator.pdf_page_index == 2
    assert fe.derivation.raw_inputs.locator.page == 3
    assert fe.derivation.raw_inputs.locator.section == "Mössbauer spectroscopic analysis"
    assert not fe.derived_from
    assert not fe.notices

    all_observations = list(observations.values())
    assert all(row.value.kind is ValueKind.POINT for row in all_observations)
    assert all(
        "fO2_log" not in (row.point_conditions or {}) for row in all_observations
    )

    figures = [
        record
        for species in document["species"].values()
        for record in species.get("context", [])
        if record.get("type") == "figure_record"
    ]
    assert len(figures) == 11
    assert all(record["values"]["points"] == [] for record in figures)
    assert all(
        record["values"]["digitisation"] == "deferred to a figure pass"
        for record in figures
    )
    contexts = [
        record
        for record in document["species"]["SiO2"]["context"]
        if record.get("type") == "measured_table_context"
    ]
    s3 = next(record for record in contexts if record["locator"].get("table") == "S3")
    s4 = next(record for record in contexts if record["locator"].get("table") == "S4")
    assert len(s3["values"]["displayed_cells"]) == 763
    assert len(s4["values"]["displayed_cells"]) == 785
    assert any(cell["cell"] == "AL3" for cell in s3["values"]["displayed_cells"])
    model = next(
        record
        for record in document["species"]["SiO2"]["context"]
        if record.get("type") == "model_results"
    )
    assert len(model["values"]["printed_rows"]) == 25
