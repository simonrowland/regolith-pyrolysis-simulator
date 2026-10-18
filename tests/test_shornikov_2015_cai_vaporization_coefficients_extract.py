"""Regression pins for Shornikov (2015) CAI vaporization coefficients."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator
from simulator.battery.score import _printed_uncertainty_band


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/shornikov-2015-cai-vaporization-coefficients.yaml"
)


def test_shornikov_2015_cai_coefficients_migrate_with_located_conditions() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    assert not migrator.result.validation.hard_issues
    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 60
    table_observations = tuple(
        row
        for row in observations
        if row.locator.table in {"1", "2", "3", "4", "5"}
    )
    assert len(table_observations) == 52

    for observation in observations:
        conditions = observation.point_conditions or {}
        assert {"temperature_K", "total_pressure_Pa", "fO2_Pa"} <= set(conditions)
        assert all(conditions[key].locator is not None for key in conditions)
        temperature = conditions["temperature_K"].state
        assert temperature.is_value or temperature.is_unknown
        for key in ("total_pressure_Pa", "fO2_Pa"):
            assert conditions[key].state.is_value or conditions[key].state.is_unknown

    expected_rendered_values = {
        "shornikov_2015_cai_vaporization_coefficients_p003_t01_r02_c04": (
            Decimal("0.79")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r02_c04": (
            Decimal("0.35")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p005_t04_r03_c04": (
            Decimal("0.0223")
        ),
        "shornikov_2015_cai_vaporization_coefficients_p007_t05_r07_c08": (
            Decimal("0.66")
        ),
    }
    for observation_id, expected in expected_rendered_values.items():
        observation = next(
            row for row in observations if row.observation_id.endswith(observation_id)
        )
        assert observation.value.point == expected
        assert observation.locator.table is not None

    ranged = next(
        row
        for row in observations
        if row.observation_id.endswith("p003_t01_r02_c04")
    )
    assert ranged.identity.temperature_K.is_unknown
    temperature_interval = ranged.point_conditions["temperature_K"].state.value
    assert (temperature_interval.interval_low, temperature_interval.interval_high) == (
        Decimal("1283"), Decimal("1473")
    )
    feo_crystalline = next(
        row
        for row in observations
        if row.observation_id.endswith("abstract_feo_cr_recommendation")
    )
    assert feo_crystalline.identity.temperature_K.is_unknown
    feo_interval = feo_crystalline.point_conditions["temperature_K"].state.value
    assert (feo_interval.interval_low, feo_interval.interval_high) == (
        Decimal("1373"), Decimal("1623")
    )
    scalar = next(
        row
        for row in observations
        if row.observation_id.endswith("p004_t03_r18_c04")
    )
    required_axes = profile_for(scalar.identity).required
    assert {"temperature_K", "composition", "total_pressure_Pa", "fO2_Pa"} <= (
        required_axes
    )
    assert scalar.identity.temperature_K.value == Decimal("2318.0")
    for key in ("composition", "total_pressure_Pa", "fO2_Pa"):
        assert getattr(scalar.identity, key).is_unknown
    assert scalar.point_conditions["temperature_K"].state.value == Decimal("2318.0")

    contexts = tuple(
        context
        for rows in migrator.result.context_by_work.values()
        for context in rows
    )
    figures = tuple(
        context for context in contexts if context.get("type") == "figure_only"
    )
    assert len(figures) == 2
    for figure in figures:
        values = figure["values"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert values["quantity"]
        assert values["species_as_printed"]
        assert values["phase_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert "points" not in values
        if "panels" in values:
            assert {panel["panel"] for panel in values["panels"]} == {"a", "b"}
            series = [item for panel in values["panels"] for item in panel["series"]]
            assert len(series) == 12
        else:
            series = values["series"]
        assert all("marker_count" in item and "points" not in item for item in series)


def test_table5_melting_points_and_chase_value_migrate_separately() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = tuple(migrator.result.observations.values())

    expected = {
        "shornikov_2015_cai_table5_melting_point_cao": ("3200", "50", "5"),
        "shornikov_2015_cai_table5_melting_point_mgo": ("3100", "25", "5"),
        "shornikov_2015_cai_table5_melting_point_al2o3": ("2327", "4", "5"),
        "shornikov_2015_cai_table5_melting_point_sio2": ("1996", "5", "5"),
        "shornikov_2015_cai_table5_melting_point_tio2": ("2185", "10", "5"),
        "shornikov_2015_cai_table5_melting_point_feo": ("1650", "5", "5"),
        "shornikov_2015_cai_cao_melting_point_chase_1998": ("3172", "30", "2"),
    }
    for observation_id, (temperature, uncertainty, table) in expected.items():
        observation = next(
            row for row in observations if row.observation_id.endswith(observation_id)
        )
        assert observation.value.point == Decimal(temperature)
        assert observation.identity.subtype.value == "melting_point"
        assert observation.uncertainty.verbatim["T_m_uncertainty_K"] == int(uncertainty)
        assert observation.uncertainty.verbatim["confidence_level"] == "not_stated"
        if table == "5":
            assert observation.locator.table == table
        else:
            assert observation.locator.table is None


def test_printed_alpha_uncertainties_are_numeric_scorer_widths() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = tuple(migrator.result.observations.values())
    expected = {
        "p003_t01_r02_c04": Decimal("0.20"),
        "p003_t01_r03_c04": Decimal("0.30"),
        "p007_t05_r07_c08": Decimal("0.15"),
        "p003_t02_r09_c04": Decimal("0.20"),
        "p007_t05_r06_c08": Decimal("0.20"),
        "p004_t03_r18_c04": Decimal("0.2"),
        "p004_t03_r19_c04": Decimal("0.2"),
        "p007_t05_r05_c08": Decimal("0.02"),
        "abstract_al2o3_cr_recommendation": Decimal("0.02"),
        "p005_t04_r03_c04": Decimal("0.0030"),
        "p005_t04_r04_c04": Decimal("0.008"),
        "p005_t04_r06_c04": Decimal("0.0041"),
        "p005_t04_r07_c04": Decimal("0.0047"),
        "p005_t04_r09_c04": Decimal("0.02"),
        "p007_t05_r03_c08": Decimal("0.003"),
        "p007_t05_r04_c08": Decimal("0.02"),
        "semenov_tio2_alpha_1920K": Decimal("0.04"),
        "p007_t05_r08_c08": Decimal("0.30"),
        "abstract_feo_cr_recommendation": Decimal("0.20"),
        "feo_liquid_alpha": Decimal("0.10"),
    }
    assert len(expected) == 20
    for observation_id, width in expected.items():
        observation = next(
            row for row in observations if row.observation_id.endswith(observation_id)
        )
        uncertainty = observation.uncertainty.verbatim
        assert Decimal(str(uncertainty["value"])) == width
        assert uncertainty["units"] == "dimensionless"
        assert uncertainty["confidence_level"] == "not_stated"
        resolution = _printed_uncertainty_band(
            Quantity.EVAPORATION_COEFFICIENT_ALPHA,
            observation.uncertainty,
            Decimal("1"),
            source_observation=observation,
        )
        assert resolution.refusal is None
        assert resolution.band is not None
        assert resolution.band.value == width
        assert resolution.band.unit == "dimensionless"


def test_partial_gas_components_remain_in_the_existing_printed_field() -> None:
    """Preserve printed gas components without inventing alpha identity subtypes."""
    extract = yaml.safe_load(EXTRACT.read_text())
    expected = {
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r05_c04": "Al",
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r06_c04": "AlO",
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r07_c04": "$Al_2O$",
        "shornikov_2015_cai_vaporization_coefficients_p004_t03_r08_c04": "O",
        "shornikov_2015_cai_vaporization_coefficients_p005_t04_r03_c04": "SiO",
        "shornikov_2015_cai_vaporization_coefficients_p005_t04_r06_c04": "$SiO_{2}$",
        "shornikov_2015_cai_vaporization_coefficients_p005_t04_r07_c04": "$Si_{2}O_{2}$",
    }
    for observation_id, gas_component in expected.items():
        observation = next(
            item
            for item in extract["species"][
                "Al2O3" if "t03" in observation_id else "SiO2"
            ]["observations"]
            if item["observation_id"] == observation_id
        )
        assert observation["values"]["gas_phase_component_as_printed"] == gas_component

    hashimoto = next(
        item
        for item in extract["species"]["SiO2"]["observations"]
        if item["observation_id"].endswith("p005_t04_r10_c04")
    )
    assert hashimoto["phase"] == "l"
    assert "Table 5 prints Tmelt = 1996 ± 5 K" in hashimoto["values"]["phase_basis"]
