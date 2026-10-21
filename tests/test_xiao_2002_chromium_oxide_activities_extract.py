"""Render-grid, source-asset, and migration checks for Xiao et al. (2002)."""

from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, AssetRole, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "xiao-2002-chromium-oxide-activities"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
RENDER_GRID = ROOT / f"tests/fixtures/literature_extracts/{SID}-render-grid.csv"
ASSET_ID = f"tables:{SID}"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _typed_by_sample(document: dict, species: str, quantity: str) -> dict[tuple[str, str], tuple[dict, dict]]:
    rows = {}
    for observation in document["species"][species]["observations"]:
        values = observation["values"]
        if values.get("quantity") != quantity:
            continue
        table = str(observation["locator"].get("table"))
        for point in values.get("points", ()):
            sample = str(point.get("sample_as_published") or point.get("row_as_published"))
            rows[(table, sample)] = (observation, point)
    return rows


def _context_by_sample(document: dict) -> dict[str, dict]:
    rows = {}
    for container in document["species"].values():
        for record in container.get("context", ()):
            values = record.get("values", {})
            sample = values.get("sample_as_published")
            if sample is not None and record["observation_id"].startswith("xiao_2002_table_"):
                rows[str(sample)] = record
    return rows


def _assert_render_grid_matches(document: dict, grid: list[dict[str, str]]) -> None:
    valence = _typed_by_sample(document, "Cr", "valence_ratio")
    cro_activity = _typed_by_sample(document, "CrO", "activity")
    cro_gamma = _typed_by_sample(document, "CrO", "activity_coefficient")
    cro15_activity = _typed_by_sample(document, "CrO1.5", "activity")
    cro15_gamma = _typed_by_sample(document, "CrO1.5", "activity_coefficient")
    auxiliary = _context_by_sample(document)

    assert len(grid) == 69, "Table I has 48 samples and Table II has 21 samples"
    assert sum(row["table"] == "I" for row in grid) == 48
    assert sum(row["table"] == "II" for row in grid) == 21
    assert len(valence) == 69
    assert len(cro_activity) == len(cro_gamma) == 48
    assert len(cro15_activity) == len(cro15_gamma) == 48

    for rendered in grid:
        table, sample = rendered["table"], rendered["sample"]
        observation, point = valence[(table, sample)]
        expected_composition = {
            oxide: rendered[oxide] for oxide in ("CaO", "SiO2", "CrO", "CrO1.5")
        }
        assert point["sample_as_published"] == sample
        assert point["value"] == rendered["Cr2+/T_Cr"]
        assert point["row_as_published"] == sample
        assert point["quench_statement"]["preservation"] == "not_stated"
        assert point["composition_as_published"]["components"] == expected_composition
        assert observation["locator"]["page"] == int(rendered["page"])
        assert observation["locator"]["row"] == sample

        context = auxiliary[sample]["values"]
        assert context["x_as_published"] == rendered["x"]
        assert context["B_as_published"] == rendered["B"]
        if table == "I":
            assert context["EMF_as_published"] == rendered["EMF"]
            for rows, column, field in (
                (cro_activity, "a(CrO)", "activity"),
                (cro_gamma, "gamma(CrO)", "gamma"),
                (cro15_activity, "a(CrO1.5)", "activity"),
                (cro15_gamma, "gamma(CrO1.5)", "gamma"),
            ):
                activity_observation, activity_point = rows[(table, sample)]
                assert activity_point[field] == rendered[column]
                assert activity_point["composition_as_published"]["components"] == expected_composition
                assert activity_observation["locator"]["row"] == sample
                assert activity_observation["standard_state_as_printed"] == (
                    "Pure liquid CrO" if "CrO1.5" not in column else "Pure solid CrO1.5"
                )
        else:
            assert "EMF_as_published" not in context
            auxiliary_record = auxiliary[sample]
            assert auxiliary_record["units"] == "dimensionless as printed"
            assert "No quantity token represents the composition index x or basicity B" in (
                context["schema_gap"]
            )


def test_render_transcription_pins_all_table_cells_and_mutations_fail():
    document = _document()
    grid = _grid()
    assert check_all_fidelity_samples_match(document) == []
    _assert_render_grid_matches(document, grid)

    asset = document["source_files"]["files"][0]
    assert asset["asset_id"] == ASSET_ID
    with (ROOT / asset["path"]).open(newline="", encoding="utf-8") as stream:
        source_rows = list(csv.DictReader(stream))
    assert len(source_rows) == 48
    for rendered, source in zip((row for row in grid if row["table"] == "I"), source_rows, strict=True):
        assert source["Number"] == rendered["sample"]
        assert source["EMF (−mv)"] == rendered["EMF"].removesuffix(" mV")
        assert source["a(CrO) (liquid state)"] == rendered["a(CrO)"]
        assert source["a(CrO1.5) (solid state)"] == rendered["a(CrO1.5)"]

    changed_grid = deepcopy(grid)
    changed_grid[0]["a(CrO)"] = "0.390"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(document, changed_grid)

    changed_document = deepcopy(document)
    changed = _typed_by_sample(changed_document, "CrO", "activity")[("I", "CSC1")][1]
    changed["activity"] = "0.390"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(changed_document, grid)


def test_isolated_migration_resolves_assets_and_preserves_every_identity_axis():
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert len(result.observations) == 261
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert all(observation.admission.status.value == "admitted" for observation in result.observations.values())
    expected_species = {
        row["observation_id"]: formula
        for formula, container in document["species"].items()
        for row in container.get("observations", ())
        if row.get("values", {}).get("quantity") in {"valence_ratio", "activity", "activity_coefficient"}
    }
    expected_compositions = {
        row["observation_id"]: {
            formula: Decimal(str(amount))
            for formula, amount in row["values"]["points"][0]["composition_as_published"]["components"].items()
        }
        for container in document["species"].values()
        for row in container.get("observations", ())
        if row.get("values", {}).get("quantity") in {"valence_ratio", "activity", "activity_coefficient"}
    }

    work = next(work for work in result.works.values() if SID in work.source_ids)
    asset = next(source for source in work.source_files.files if source.asset_id == ASSET_ID)
    assert asset.role is AssetRole.TABLE_CSV
    assert asset.path == "tables/xiao-2002-chromium-oxide-activities/t1.csv"
    derived_rows = [
        observation
        for observation in result.observations.values()
        if observation.identity.quantity.value.value in {"activity", "activity_coefficient"}
    ]
    assert len(derived_rows) == 192
    assert all(observation.derived_from == (ASSET_ID,) for observation in derived_rows)
    activity_rows = [
        row
        for container in document["species"].values()
        for row in container.get("observations", ())
        if row.get("values", {}).get("quantity") == "activity"
    ]
    assert len(activity_rows) == 96
    assert all(row["values"]["method_class"] == "measured_reduced" for row in activity_rows)
    assert all(row["derived_from"] == [ASSET_ID] for row in activity_rows)
    assert all(
        all(
            term in row["derivation"]["relation"]
            for term in ("EMF E_m", "temperature T", "Eq. [1]", "physical PDF p. 3")
        )
        for row in activity_rows
    )

    required_conditions = {
        "temperature_K",
        "composition",
        "fO2_Pa",
        "fO2_control",
        "total_pressure_Pa",
        "atmosphere",
    }
    for observation in result.observations.values():
        identity = observation.identity
        raw_id = observation.observation_id.split("::", 1)[1].split("::", 1)[0]
        assert identity.species.formula == expected_species[raw_id]
        assert identity.temperature_K is not None and identity.temperature_K.is_value
        assert identity.composition is not None and identity.composition.is_value
        assert identity.composition.value.amount_basis is AmountBasis.MOL_INVENTORY
        assert identity.composition.value.basis == (
            "printed_mole_percent_as_moles_per_nominal_100_mol_inventory"
        )
        assert dict(identity.composition.value.components) == expected_compositions[raw_id]
        assert identity.fO2_Pa is not None and identity.fO2_Pa.is_unknown
        assert identity.total_pressure_Pa is not None and identity.total_pressure_Pa.is_unknown
        assert required_conditions <= set(observation.point_conditions or {})
        point_composition = observation.point_conditions["composition"]
        assert point_composition.state.is_value
        assert point_composition.state.value.amount_basis is AmountBasis.MOL_INVENTORY
        assert dict(point_composition.state.value.components) == expected_compositions[raw_id]
        assert observation.value.kind is ValueKind.POINT

    for observation in derived_rows:
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value.value == "measured_reduced"


def test_composition_fallback_keeps_typed_values_broad_and_plain_maps_scoped(tmp_path):
    document = _document()
    valence = next(
        row
        for container in document["species"].values()
        for row in container.get("observations", ())
        if row["values"].get("quantity") == "valence_ratio"
    )
    point = valence["values"]["points"][0]
    plain_components = {
        "CaO": "54.3",
        "SiO2": "36.9",
        "CrO": "6.2",
        "CrO1.5": "2.6",
    }
    point["point_conditions"]["composition"]["state"]["value"] = plain_components
    point.pop("composition_as_published")

    density = deepcopy(valence)
    density["observation_id"] += "_density_fallback"
    density["values"]["quantity"] = "density"
    density["values"].pop("valence_basis")
    density["units"] = "kg/m3"
    density_point = density["values"]["points"][0]
    density_point["value"] = "2500"
    density_point.pop("Cr2plus_over_total_Cr_as_published", None)

    activity = next(
        row
        for container in document["species"].values()
        for row in container.get("observations", ())
        if row["values"].get("quantity") == "activity"
    )
    activity_point = activity["values"]["points"][0]
    activity_point.pop("composition_as_published")
    activity_plain = deepcopy(activity)
    activity_plain["observation_id"] += "_plain_map_fallback"
    activity_plain_point = activity_plain["values"]["points"][0]
    activity_plain_point["point_conditions"]["composition"]["state"]["value"] = plain_components
    activity_plain_point.pop("composition_as_published", None)

    # Reuse the source's typed condition for the activity case, while testing
    # plain-map fallback for valence and density.
    document["species"]["Cr"]["observations"].append(density)
    document["species"]["CrO"]["observations"].append(activity_plain)
    altered_extract = tmp_path / "xiao-composition-fallback.yaml"
    altered_extract.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(altered_extract)
    migrator.finalize()

    observations = migrator.result.observations
    valence_result = next(
        item
        for key, item in observations.items()
        if valence["observation_id"] in key
    )
    density_result = next(
        item
        for key, item in observations.items()
        if density["observation_id"] in key
    )
    activity_result = next(
        item
        for key, item in observations.items()
        if activity["observation_id"] in key
    )
    activity_plain_result = next(
        item
        for key, item in observations.items()
        if activity_plain["observation_id"] in key
    )

    assert valence_result.identity.composition is not None
    assert valence_result.identity.composition.is_value
    assert density_result.identity.composition is not None
    assert density_result.identity.composition.is_value
    assert activity_result.identity.composition is not None
    assert activity_result.identity.composition.is_value
    assert (
        activity_plain_result.identity.composition is None
        or activity_plain_result.identity.composition.is_unknown
    )



def test_context_and_figure_records_keep_only_unscored_content():
    document = _document()
    valence_rows = [
        row
        for container in document["species"].values()
        for row in container.get("observations", ())
        if row.get("values", {}).get("quantity") == "valence_ratio"
    ]
    caveat = (
        "For the slag with high concentration of chromium oxide, it is not appropriate to conclude and extend "
        "the results to the molten slag because of the precipitation of quenched crystals and the presence of "
        "metal droplets in the slag."
    )
    assert valence_rows
    for row in valence_rows:
        assert caveat in row["quench_note"]
        assert "physical PDF page 5, pdf_page_index 4, published page 599" in row["quench_note"]
        assert all(
            point["quench_statement"]["preservation"] == "not_stated"
            for point in row["values"]["points"]
        )
    contexts = [
        record
        for container in document["species"].values()
        for record in container.get("context", ())
    ]
    figures = [record for record in contexts if record.get("values", {}).get("digitisation")]
    assert len(figures) == 13
    assert {record["values"]["digitisation"] for record in figures} == {"deferred to a figure pass"}
    assert all(record["values"].get("numeric_points") == [] for record in figures)
    for record in figures:
        assert record["locator"].get("figure")
        assert record["values"].get("quantity")
        assert "caption" in record["values"]
        assert "species_as_printed" in record["values"]
        assert "phase" in record
        assert "temperature_as_printed" in record["values"]
        assert "axes_as_printed" in record["values"]
        assert record["values"].get("attribution")
        assert "series" in record["values"]
        for series in record["values"]["series"]:
            assert "marker_count" in series
    for table in ("III", "IV"):
        record = next(
            row for row in contexts if row["observation_id"].endswith(f"table_{table.lower()}_optimized_interaction_energy_parameters")
            or row["observation_id"].endswith("table_iv_standard_state_conversion_gibbs_energy_relations")
        )
        assert record["values"]["method_class"] == "model_derived"
