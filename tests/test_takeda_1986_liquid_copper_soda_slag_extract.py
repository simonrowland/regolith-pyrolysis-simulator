from __future__ import annotations

from decimal import Decimal

import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract

SOURCE_ID = "takeda-1986-liquid-copper-soda-slag"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
MAPPING = (
    REPO_ROOT
    / "data/literature/extracts/audits"
    / f"{SOURCE_ID}.mapping.yaml"
)


def test_rendered_prose_values_and_isolated_migration_are_pinned() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    source_species = document["species"]["Cu2O"]
    assert len(source_species["observations"]) == 1
    assert mapping["table_draft"]["tables_found"] == 0

    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 1
    assert len(result.queue) == 2
    queued = {entry.axes[0]: entry for entry in result.queue}
    assert set(queued) == {"total_pressure_Pa", "method"}
    assert all(
        entry.observation_id == f"{SOURCE_ID}::takeda_1986_cu2o_melting_point"
        for entry in result.queue
    )
    assert queued["total_pressure_Pa"].why == (
        "source does not state a numeric total_pressure_Pa"
    )
    assert queued["method"].why == "source does not state method"
    assert len(result.context_by_work) == 1
    assert all(row.identity.quantity.is_value for row in result.observations.values())
    assert all(
        row.identity.species.phase.is_value for row in result.observations.values()
    )

    observation = result.observations[
        f"{SOURCE_ID}::takeda_1986_cu2o_melting_point"
    ]
    assert observation.value.kind is ValueKind.POINT
    assert observation.value.point == Decimal("1503")
    assert observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    assert observation.identity.species.phase.value is Phase.CR
    assert observation.identity.temperature_K.is_not_applicable
    assert observation.locator is not None
    assert observation.locator.page == 2
    assert observation.locator.published_page == 609
    assert observation.point_conditions["temperature_K"].state.value == Decimal("1503")
    assert set(observation.point_conditions) == {"temperature_K"}
    assert (
        observation.identity.total_pressure_Pa is None
        or observation.identity.total_pressure_Pa.is_unknown
    )
    assert (
        observation.identity.fO2_Pa is None
        or observation.identity.fO2_Pa.is_not_applicable
    )
    assert (
        observation.identity.composition is None
        or observation.identity.composition.is_not_applicable
    )

    context = {
        row["observation_id"]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    assert len(context) == 21
    anchors = (
        (
            "takeda_1986_cu_cu2o_reference_oxygen_potential",
            "pO2_as_printed",
            "1.78 × 10^-4",
            3,
            610,
        ),
        (
            "takeda_1986_cuo05_equilibrium_constants_and_copper_activity",
            "equilibrium_constant_for_Oishi_data_as_printed",
            "K = 9.02",
            3,
            610,
        ),
        (
            "takeda_1986_miscibility_gap_endpoints",
            "silica_percent_where_gap_disappears_as_printed",
            "around 15%",
            4,
            611,
        ),
        (
            "takeda_1986_soda_composition_and_carbonate_ratio",
            "n_CO2_over_n_Na2X_as_printed",
            "around 0.4",
            2,
            609,
        ),
        (
            "takeda_1986_copper_solubility_and_activity_summary",
            "log_percent_Cu_vs_log_pO2_slope_as_printed",
            "1/4",
            5,
            612,
        ),
        (
            "takeda_1986_carbonate_co2_stoichiometric_reference",
            "stoichiometric_CO2_percent_as_printed",
            "41.5%",
            6,
            613,
        ),
    )
    for row_id, field, expected, page, published_page in anchors:
        row = context[row_id]
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["published_page"] == published_page

    p_o2 = context["takeda_1986_cu_cu2o_reference_oxygen_potential"]
    assert p_o2["values"]["status"] == "typed_refused_unit_unspecified"
    assert p_o2["values"]["method_class"] == "quoted_attributed"
    assert context["takeda_1986_miscibility_gap_endpoints"]["values"]["status"] == (
        "typed_refused_schema_gap"
    )
    tie_claims = context["takeda_1986_miscibility_gap_endpoints"]["values"][
        "printed_claims"
    ]
    assert tie_claims == [
        {
            "coexisting_phase": "liquid Na2CO3 melt",
            "component": "Cu2O",
            "amount_as_printed": "around 1%",
        },
        {
            "coexisting_phase": "liquid cuprous oxide",
            "component": "Na2X",
            "amount_as_printed": "around 20%",
        },
        {
            "coexisting_phase": "Na2O·SiO2 liquid",
            "component": "Cu2O",
            "amount_as_printed": "around 35 to 40%",
        },
    ]
    assert context["takeda_1986_cuo05_activity_ranges"]["values"]["status"] == (
        "typed_refused_schema_gap"
    )
    cited_diagram = context["takeda_1986_cited_binary_phase_diagram_composition"]
    assert cited_diagram["locator"]["page"] == 4
    assert cited_diagram["values"]["liquid_miscibility_gap_composition_as_printed"] == (
        "liquid SiO2 coexisting with a 30% SiO2-Cu2O melt"
    )
    figures = context["takeda_1986_figures_not_digitized"]["values"]["figures"]
    assert len(figures) == 11
    assert all("caption_as_printed" in figure for figure in figures)
