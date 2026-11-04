"""Render pins and isolated migration for Centolanzi and Chapman (1966)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/centolanzi-1966-tektite-vapour-pressure.yaml"
SID = "centolanzi-1966-tektite-vapour-pressure"

FIG7_RATES = {
    "centolanzi_1966_fig7_silver_areal_mass_loss_rate": (
        "silver", "open circle", "0.19", "g/cm² min", ".19"
    ),
    "centolanzi_1966_fig7_gold_areal_mass_loss_rate": (
        "gold", "open square", "0.036", "g/cm² min", ".036"
    ),
    "centolanzi_1966_fig7_tektite_areal_mass_loss_rate": (
        "tektite glass", "open diamond", "1.3 × 10^-5", "g/cm² min", "1.3x10^-5"
    ),
    "centolanzi_1966_fig7_silica_areal_mass_loss_rate": (
        "silica", "open triangle", "1.3 × 10^-5", "g/cm² min", "1.3x10^-5"
    ),
}
RATE_IF_BUBBLE_PRESSURE = "centolanzi_1966_fig7_tektite_rate_if_walter_bubble_pressure"
VAPOR_FIT = "centolanzi_1966_tektite_vapor_pressure_fit"
UPPER_BOUND = "centolanzi_1966_fig13b_tektite_vapor_pressure_upper_bound"
OLD_FIT = "centolanzi_1966_tektite_vapor_pressure_fit_old_comparison"
EXPECTED_OBSERVATION_IDS = set(FIG7_RATES) | {
    RATE_IF_BUBBLE_PRESSURE,
    VAPOR_FIT,
    UPPER_BOUND,
    OLD_FIT,
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for block in document["species"].values()
        for row in block["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert set(observations) == EXPECTED_OBSERVATION_IDS
    assert len(observations) == 8
    assert set(document["species"]) == {"unknown"}

    for observation_id, (
        material,
        marker,
        printed_rate,
        printed_unit,
        legend_value,
    ) in FIG7_RATES.items():
        row = observations[observation_id]
        assert row["locator"] == {
            "page": 5,
            "pdf_page_index": 4,
            "published_page": 1739,
            "figure": "7",
        }
        assert row["phase"] == "l"
        assert row["values"]["sample_as_printed"] == material
        assert row["values"]["marker_as_printed"] == marker
        assert row["values"]["steady_state_mass_loss_rate_as_printed"] == printed_rate
        assert row["values"]["figure_legend_value_as_printed"] == legend_value
        assert row["values"]["rate_unit_as_printed"] == printed_unit
        assert row["values"]["figure_legend_unit_as_printed"] == "gm/cm² min"
        assert row["values"]["pressure_as_printed"] == "10^-4 atm"
        assert row["point_conditions"]["temperature_K"]["state"] == {
            "tag": "value",
            "value": "1700",
        }
        assert row["point_conditions"]["temperature_K"]["locator"]["page"] == 5
        assert row["point_conditions"]["total_pressure_Pa"]["state"] == {
            "tag": "value",
            "value": "10.1325",
        }
        assert row["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 5
        assert row["point_conditions"]["atmosphere"]["state"] == {
            "tag": "value",
            "value": "argon",
        }

    legend_record = next(
        record
        for record in document["species"]["unknown"]["context"]
        if record["observation_id"] == "centolanzi_1966_fig7_printed_legend"
    )
    assert legend_record["values"]["legend_entries"] == [
        {
            "material_as_printed": "SILVER",
            "marker_as_printed": "open circle",
            "rate_as_printed": ".19",
        },
        {
            "material_as_printed": "GOLD",
            "marker_as_printed": "open square",
            "rate_as_printed": ".036",
        },
        {
            "material_as_printed": "TEKTITE",
            "marker_as_printed": "open diamond",
            "rate_as_printed": "1.3x10^-5",
        },
        {
            "material_as_printed": "SILICA",
            "marker_as_printed": "open triangle",
            "rate_as_printed": "1.3x10^-5",
        },
    ]
    assert legend_record["values"]["legend_unit_as_printed"] == "gm/cm² min"
    assert legend_record["values"]["condition_as_printed"] == "T = 1700°K; P = 10^-4 atm"

    conditional = observations[RATE_IF_BUBBLE_PRESSURE]
    assert conditional["values"]["conditional_rate_as_printed"] == "12"
    assert conditional["values"]["rate_unit_as_printed"] == "gm/cm² min"
    assert conditional["values"]["assumption_as_printed"] == (
        "If P_v = P_v(b), Walter's bubble-pressure function"
    )
    assert conditional["values"]["body_occurrence_as_printed"] == "12 g/cm² min"
    assert conditional["values"]["body_occurrence_locator"] == {
        "page": 6,
        "pdf_page_index": 5,
        "published_page": 1740,
    }
    assert conditional["point_conditions"]["temperature_K"]["state"] == {
        "tag": "value",
        "value": "1700",
    }
    assert conditional["point_conditions"]["total_pressure_Pa"]["state"] == {
        "tag": "value",
        "value": "10.1325",
    }

    fit = observations[VAPOR_FIT]
    assert fit["values"]["fit_equation_as_printed"] == (
        "P_v(atm) = exp[18.5 - 57,400/T(°K)]"
    )
    assert fit["values"]["temperature_range_as_printed"] == "1700 to 3000°K"
    assert fit["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
    assert fit["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert fit["values"]["other_printed_forms"] == [
        {
            "equation_as_printed": "P_v(atm) = exp[18.50 - 57,400/T(°K)]",
            "locator": {
                "page": 12,
                "pdf_page_index": 11,
                "published_page": 1746,
                "figure": "14",
            },
        },
        {
            "equation_as_printed": "ln P_v(atm) = -57,400/T(°K) + 18.50",
            "locator": {
                "page": 14,
                "pdf_page_index": 13,
                "published_page": 1748,
                "section": "Summary of Results and Concluding Remarks",
            },
        },
    ]

    bound = observations[UPPER_BOUND]
    assert bound["values"]["vapor_pressure_bound_as_printed"] == "<0.05 atm at 2310°K"
    assert bound["values"]["pressure_condition_as_printed"] == "P_s = 0.05 atm"
    assert bound["values"]["brightness_temperature_as_printed"] == "2150°K"
    assert bound["values"]["surface_temperature_as_printed"] == "2310°K"
    assert bound["values"]["specific_gravity_as_printed"] == "2.452"
    assert bound["point_conditions"]["temperature_K"]["state"]["value"] == "2310"
    assert bound["point_conditions"]["total_pressure_Pa"]["state"]["value"] == "5066.25"
    assert bound["point_conditions"]["total_pressure_Pa"]["locator"]["page"] == 10

    old_fit = observations[OLD_FIT]
    assert old_fit["values"]["equation_as_printed"] == "ln P_v = -57,800/T + 19.1"
    assert old_fit["values"]["attribution"] == "Chapman and Larson [1963]"
    assert old_fit["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
    assert old_fit["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
    assert all(
        "fO2_Pa" not in row.get("point_conditions", {})
        for row in observations.values()
    )

    # The independent printed-condition conversion uses the exact defined atm factor.
    assert Decimal("0.0001") * Decimal("101325") == Decimal("10.1325")
    assert Decimal("0.05") * Decimal("101325") == Decimal("5066.25")


def test_render_checked_cells_and_full_observation_row_count() -> None:
    document = _document()
    _assert_render_pins(document)
    assert document["scope_assessment"]["typed_observations"] == 8
    assert document["scope_assessment"]["context_records"] == 13


def test_mutating_an_interior_rendered_rate_or_condition_fails_the_pin() -> None:
    document = _document()
    _assert_render_pins(document)

    mutated_rate = deepcopy(document)
    _observations(mutated_rate)["centolanzi_1966_fig7_tektite_areal_mass_loss_rate"][
        "values"
    ]["steady_state_mass_loss_rate_as_printed"] = "1.4 × 10^-5"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_rate)

    mutated_condition = deepcopy(document)
    _observations(mutated_condition)["centolanzi_1966_fig7_gold_areal_mass_loss_rate"][
        "point_conditions"
    ]["total_pressure_Pa"]["state"]["value"] = "10.1326"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_condition)


def test_isolated_migration_preserves_conditions_and_typed_refusals() -> None:
    document = _document()
    _assert_render_pins(document)
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[1]: row
        for row in result.observations.values()
        if row.source_id == SID
    }
    assert set(migrated) == EXPECTED_OBSERVATION_IDS
    assert len(migrated) == 8
    assert sum(row.value.kind.value == "unavailable" for row in migrated.values()) == 8
    assert sum(row.value.kind.value != "unavailable" for row in migrated.values()) == 0

    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == EXPECTED_OBSERVATION_IDS
    assert all(row.identity.species.phase.is_value for row in migrated.values())
    assert all(row.identity.species.phase.value.value == "l" for row in migrated.values())
    assert all(row.identity.species.formula == "unknown" for row in migrated.values())
    figure7_experiments = {
        migrated[observation_id].experiment_id
        for observation_id in set(FIG7_RATES) | {RATE_IF_BUBBLE_PRESSURE}
    }
    assert len(figure7_experiments) == 1
    assert migrated[VAPOR_FIT].experiment_id not in figure7_experiments
    assert migrated[OLD_FIT].experiment_id not in figure7_experiments

    for observation_id in set(FIG7_RATES) | {RATE_IF_BUBBLE_PRESSURE}:
        row = migrated[observation_id]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal("1700")
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("10.1325")
        assert row.identity.fO2_Pa is None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator.page == 5
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].locator.page == 5
        assert row.point_conditions["atmosphere"].state.value == "argon"
        assert row.point_conditions["atmosphere"].locator.page == 3

    bound = migrated[UPPER_BOUND]
    assert bound.identity.temperature_K.is_value
    assert bound.identity.temperature_K.value == Decimal("2310")
    assert bound.identity.total_pressure_Pa.is_value
    assert bound.identity.total_pressure_Pa.value == Decimal("5066.25")
    assert bound.identity.fO2_Pa is None
    assert bound.point_conditions["temperature_K"].locator.page == 10
    assert bound.point_conditions["total_pressure_Pa"].locator.page == 10
    assert bound.admission.status.value == "rejected"

    for observation_id in (VAPOR_FIT, OLD_FIT):
        row = migrated[observation_id]
        assert row.identity.temperature_K is None or row.identity.temperature_K.is_unknown
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None
        assert "total vapour" in (row.identity.quantity.reason or "").lower()
    for observation_id in EXPECTED_OBSERVATION_IDS - {VAPOR_FIT, OLD_FIT, UPPER_BOUND} - set(FIG7_RATES):
        assert migrated[observation_id].identity.quantity.is_unknown
