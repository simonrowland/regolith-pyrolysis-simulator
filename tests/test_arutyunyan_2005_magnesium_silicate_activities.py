from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/arutyunyan-2005-magnesium-silicate-activities.yaml"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as vle  # noqa: E402


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_arutyunyan_2005_table_values_match_600_dpi_render_pins() -> None:
    document = _document()
    assert vle.validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id="arutyunyan-2005-magnesium-silicate-activities",
        check_fidelity_match=True,
    ) == []
    expected = {
        "MgO": ["0.181", "0.171", "0.158", "0.144", "0.129", "0.122"],
        "SiO2": ["0.693", "0.737", "0.797", "0.865", "0.951", "1.0"],
    }
    for formula, activities in expected.items():
        observations = document["species"][formula]["observations"]
        assert len(observations) == 1
        points = observations[0]["values"]["points"]
        assert len(points) == 6
        assert [point["activity_as_printed"] for point in points] == activities
        assert all(point["point_conditions"]["temperature_K"] for point in points)
        assert all(point["point_conditions"]["composition"] for point in points)

    table1 = next(
        row
        for row in document["species"]["MgO"]["context"]
        if row["observation_id"] == "arutyunyan_2005_table1_ion_ratio_fits"
    )
    fits = table1["values"]["fits"]
    assert len(fits) == 3
    assert [row["A_plus_uncertainty_as_printed"] for row in fits] == [
        "14190 ± 68",
        "2592 ± 56",
        "2905 ± 57",
    ]
    assert [row["negative_B_plus_uncertainty_as_printed"] for row in fits] == [
        "5.455 ± 0.040",
        "4.680 ± 0.040",
        "5.164 ± 0.040",
    ]
    repeatability = next(
        row
        for row in document["species"]["MgO"]["context"]
        if row["observation_id"] == "arutyunyan_2005_ionic_current_measurement_error"
    )
    error = repeatability["values"]["experimental_error"]["value"]
    assert error == {"kind": "point", "point": "1", "approximate": True}


def test_arutyunyan_2005_migration_preserves_each_activity_condition() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 12
    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions is not None
        for key in ("temperature_K", "composition", "phase_assemblage", "total_pressure_Pa"):
            assert key in conditions
            assert conditions[key].locator is not None
            assert conditions[key].locator.published_page == 391
            assert conditions[key].locator.table == "2"
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == Decimal("1873")
        assert observation.identity.temperature_K is not None
        assert observation.identity.temperature_K.value == Decimal("1873")
        composition = conditions["composition"]
        assert composition.state.is_value
        assert composition.locator.note is not None
        assert observation.locator is not None
        assert observation.locator.table == "2"
        assert observation.value.point in {
            Decimal("0.181"),
            Decimal("0.171"),
            Decimal("0.158"),
            Decimal("0.144"),
            Decimal("0.129"),
            Decimal("0.122"),
            Decimal("0.693"),
            Decimal("0.737"),
            Decimal("0.797"),
            Decimal("0.865"),
            Decimal("0.951"),
            Decimal("1.0"),
        }


def test_arutyunyan_2005_deferred_figures_are_context_only_and_pinned() -> None:
    document = _document()
    figures = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
        if row.get("type") == "figure_record"
    }
    assert set(figures) == {
        "arutyunyan_2005_fig1_ratio_temperature",
        "arutyunyan_2005_fig2_melt_composition_ratio",
        "arutyunyan_2005_fig3_oxide_formation_functions",
        "arutyunyan_2005_fig4_component_activities",
    }
    for figure in figures.values():
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["axes_as_printed"]
        assert values["series"]
        assert all("marker_count" in series for series in values["series"])
        assert "points" not in values


def test_arutyunyan_2005_fidelity_pin_rejects_a_mutated_activity() -> None:
    document = _document()
    assert vle.check_all_fidelity_samples_match(document, label="arutyunyan-2005") == []

    document["species"]["SiO2"]["observations"][0]["values"]["points"][0][
        "activity_as_printed"
    ] = "0.694"
    assert vle.check_all_fidelity_samples_match(
        document, label="arutyunyan-2005-mutated"
    )
