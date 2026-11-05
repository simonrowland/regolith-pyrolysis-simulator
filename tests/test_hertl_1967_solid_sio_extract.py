from __future__ import annotations

from copy import deepcopy
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import REPO_ROOT
from simulator.battery.validate import validate_corpus
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


EXTRACT_NAME = "hertl-1967-solid-sio-disproportionation-vaporization.yaml"
EXTRACT = REPO_ROOT / "data/literature/extracts" / EXTRACT_NAME
SOURCE_ID = "hertl-1967-solid-sio-disproportionation-vaporization"
SCHEMA_GAP_IDS = {
    "hertl_1967_sio_vaporization_heat_this_work",
    "hertl_1967_quoted_vaporization_enthalpy_range",
    "hertl_1967_quoted_geld_kochnev_vaporization_heat",
    "hertl_1967_quoted_schafer_hornle_vaporization_heat",
    "hertl_1967_apparent_disproportionation_activation_energy",
}


def _document() -> dict:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_hertl_values_figure_legends_and_row_counts_match_render() -> None:
    document = _document()
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []

    observations = document["species"]["SiO"]["observations"]
    context = document["species"]["SiO"]["context"]
    by_id = {row["observation_id"]: row for row in observations + context}
    assert len(observations) == 5
    assert len(context) == 13
    assert len(by_id) == 18
    assert all(
        row["locator"]["page"] == 3
        and row["locator"]["published_page"] == 380
        for row in observations
    )

    # The target article has six figures and no printed data tables.
    figures = [row for row in context if row["type"] == "figure_record"]
    legends = [row for row in context if row["type"] == "figure_legend"]
    assert len(figures) == 6
    assert {row["locator"]["figure"] for row in figures} == {
        "1", "2", "3", "4", "5", "6"
    }
    assert len(legends) == 2
    assert all("table" not in row["locator"] for row in observations + context)

    method_context = by_id["hertl_1967_vaporization_methods_and_apparatus"]["values"]
    assert by_id["hertl_1967_vaporization_methods_and_apparatus"]["locator"] == {
        "page": 1,
        "published_page": 378,
        "pdf_page_index": 0,
        "section": "Abstract",
    }
    assert method_context["vaporization_temperature_range_C_as_published"] == [
        1005, 1475
    ]
    assert method_context["disproportionation_temperature_range_C_as_published"] == [
        1000, 1440
    ]
    characterization = by_id["hertl_1967_starting_material_characterization"]
    assert characterization["locator"] == {
        "page": 1,
        "published_page": 378,
        "pdf_page_index": 0,
        "section": "II. Experimental Procedure",
    }
    assert characterization["values"]["cation_impurity_levels_as_printed"] == {
        "basis": "oxide",
        "ranges": [
            {"range": "0.01 to 0.1%", "oxides": ["Fe2O3", "Al2O3"]},
            {"range": "0.005 to 0.05%", "oxides": ["MgO", "MnO2"]},
            {
                "range": "0.001 to 0.01%",
                "oxides": ["B2O3", "SnO2", "NiO", "CuO", "ZrO2"],
            },
        ],
    }
    assert (
        characterization["values"]["BET_particle_surface_area_m2_per_g_as_published"]
        == 1.4
    )
    assert characterization["values"]["mean_particle_diameter_as_printed"] == "2.0μ"
    vaporization_setup = by_id[
        "hertl_1967_vaporization_measurement_and_atmosphere"
    ]
    assert vaporization_setup["locator"]["page"] == 2
    assert vaporization_setup["locator"]["published_page"] == 379
    assert vaporization_setup["values"]["vaporization_sample_holder_as_printed"].endswith(
        "11.90 cm² at the furnace hot spot; weight loss measured from the reaction boat after a run."
    )
    disprop_setup = by_id["hertl_1967_disproportionation_apparatus_and_procedure"]
    assert disprop_setup["locator"]["page"] == 2
    assert "18 by 2 in." in disprop_setup["values"][
        "disproportionation_apparatus_as_printed"
    ]
    assert disprop_setup["values"]["orifice_dimensions_as_printed"][
        "orifice_as_printed"
    ] == "a gasket-sealed orifice in the end cap"
    assert disprop_setup["values"]["orifice_dimensions_as_printed"][
        "diameter"]["state"]["tag"] == "unknown"
    assert disprop_setup["values"]["clausing_factor_as_printed"]["state"][
        "tag"
    ] == "unknown"

    numeric_results = by_id["hertl_1967_disproportionation_numeric_results"]
    assert numeric_results["locator"] == {
        "page": 3,
        "published_page": 380,
        "pdf_page_index": 2,
        "section": "IV. Disproportionation Experiments, Results",
    }
    numeric_values = numeric_results["values"]
    assert numeric_values["charge_weight_change_bound_mg_as_published"] == 1.5
    assert (
        numeric_values["reaction_temperature_attainment_time_as_printed"]
        == "~1 min"
    )
    assert (
        numeric_values[
            "imperceptible_disproportionation_below_temperature_as_printed"
        ]
        == "about 1000°C"
    )
    assert numeric_values["parallel_experiment_temperature_C_as_published"] == 1010
    assert [
        [
            (stage["temperature_C"], stage.get("duration_as_printed"))
            for stage in sequence["stages"]
        ]
        for sequence in numeric_values["temperature_sequences_as_printed"]
    ] == [
        [
            (1180, "long enough to reach its terminal disproportionation value"),
            (1390, "1 min"),
        ],
        [(1380, "5 min"), (1195, "16 hr")],
    ]
    assert numeric_values["superficial_oxidation_temperature_C_as_published"] == 800
    assert numeric_values["silicon_globule_temperature_C_as_published"] == 1440
    assert numeric_values["silicon_melting_point_C_as_published"] == 1412

    fig1_legend = by_id["hertl_1967_fig1_marker_legend"]["values"][
        "marker_legend_as_printed"
    ]
    assert fig1_legend == {
        "●": "1-hr run at 1200°C with constant evacuation of system",
        "○": "4-hr run at 1474°C in 1 atm argon",
    }
    fig5_legend = by_id["hertl_1967_fig5_marker_legend"]["values"][
        "marker_legend_as_printed"
    ]
    assert by_id["hertl_1967_fig5_marker_legend"]["locator"]["page"] == 3
    assert fig5_legend == {
        "△": "1440°C",
        "×": "1330°C",
        "○": "1210°C",
        "●": "1070°C (3600 sec)",
    }

    pinned = {
        row["observation_id"]: row["values"]
        for row in observations
    }
    assert pinned["hertl_1967_sio_vaporization_heat_this_work"][
        "value_as_published"
    ] == 72.5
    assert pinned["hertl_1967_quoted_vaporization_enthalpy_range"][
        "value_range_as_published"
    ] == {"kind": "interval", "low": 58, "high": 95}
    assert pinned["hertl_1967_quoted_geld_kochnev_vaporization_heat"][
        "value_as_published"
    ] == 76
    assert pinned["hertl_1967_quoted_schafer_hornle_vaporization_heat"][
        "value_as_published"
    ] == 77
    assert pinned["hertl_1967_apparent_disproportionation_activation_energy"][
        "value_as_published"
    ] == 21.0
    assert pinned["hertl_1967_apparent_disproportionation_activation_energy"][
        "value_in_discussion_as_published"
    ] == 21
    assert by_id[
        "hertl_1967_fig6_arrhenius_terminal_disproportionation_figure_only"
    ]["values"]["printed_fit_label_as_published"] == "E_a = 21.0 kcal/mole"
    this_work = by_id["hertl_1967_sio_vaporization_heat_this_work"]
    assert this_work["locator"]["page"] == 3
    assert this_work["point_conditions"]["temperature"]["locator"] == {
        "page": 2,
        "published_page": 379,
        "pdf_page_index": 1,
        "figure": "4",
    }
    assert pinned["hertl_1967_apparent_disproportionation_activation_energy"][
        "discussion_locator"]["page"] == 4

    # The superscript references on 58 and 95 are not decimal digits.
    assert by_id["hertl_1967_quoted_vaporization_enthalpy_range"]["values"][
        "value_range_text_as_published"
    ] == "about 58⁵ to 95⁷ kcal/mole"

    figure3 = by_id["hertl_1967_fig3_sio_loss_vs_argon_flow_figure_only"]
    assert figure3["values"]["T_C"] == 1427
    assert figure3["values"]["total_pressure_atm"] == 1
    assert figure3["values"]["flow_rate_result_as_published"].startswith(
        "Rate increased with increasing flow up to about 1000 ml/min"
    )
    fig5 = by_id["hertl_1967_fig5_disproportionation_vs_time_figure_only"]
    assert fig5["locator"]["published_page"] == 380
    assert fig5["values"]["charge_mass_g_as_printed"] == 0.250
    assert fig5["values"]["percent_to_Si_mass_mg_as_printed"] == 79.5
    fig6_conditions = by_id[
        "hertl_1967_fig6_arrhenius_terminal_disproportionation_figure_only"
    ]["values"]["point_conditions"]
    assert "total_pressure_atm" not in fig6_conditions
    assert "total_pressure_Pa" not in fig6_conditions
    assert all(
        "fO2_Pa" not in (row.get("point_conditions") or {})
        and "fO2_control" not in (row.get("point_conditions") or {})
        for row in observations + figures
    )

    changed = deepcopy(document)
    changed["species"]["SiO"]["observations"][0]["values"][
        "value_as_published"
    ] = 73.0
    assert check_all_fidelity_samples_match(changed, label=str(EXTRACT))


def test_hertl_isolated_migration_keeps_exact_schema_gaps_and_conditions(
    tmp_path: Path,
) -> None:
    document = _document()
    result = _migrate_real_extract(tmp_path / "isolated", EXTRACT_NAME)
    migrated = {
        observation_id.rsplit("::", 1)[-1]: row
        for observation_id, row in result.observations.items()
        if observation_id.startswith(f"{SOURCE_ID}::")
    }
    assert set(migrated) == SCHEMA_GAP_IDS
    assert len(migrated) == 5
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 5

    validation = validate_corpus(
        result.works,
        result.experiments,
        result.observations,
        residuals=None,
    )
    assert validation.hard_issues == ()

    source_rows = {
        row["observation_id"]: row
        for row in document["species"]["SiO"]["observations"]
    }
    for observation_id, row in migrated.items():
        assert row.identity.quantity.is_unknown
        assert row.identity.species is not None
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value is Phase.CR
        assert row.point_conditions is not None
        assert row.point_conditions["temperature"].state.is_unknown
        assert row.point_conditions["temperature"].locator.page == source_rows[
            observation_id
        ]["point_conditions"]["temperature"]["locator"]["page"]
        assert source_rows[observation_id]["point_conditions"]["total_pressure_Pa"][
            "state"]["tag"] == "unknown"
        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" not in row.point_conditions
        assert row.identity.temperature_K is None or row.identity.temperature_K.is_unknown
        assert row.identity.total_pressure_Pa is None or row.identity.total_pressure_Pa.is_unknown
        assert row.identity.fO2_Pa is None or row.identity.fO2_Pa.is_unknown
        assert row.identity.composition is None or row.identity.composition.is_unknown

    vaporization_heat = migrated["hertl_1967_sio_vaporization_heat_this_work"]
    assert vaporization_heat.locator.page == 3
    assert vaporization_heat.locator.published_page == 380
    assert vaporization_heat.point_conditions["temperature"].locator.page == 2
    assert vaporization_heat.point_conditions["temperature"].locator.figure == "4"
    activation_energy = migrated[
        "hertl_1967_apparent_disproportionation_activation_energy"
    ]
    assert activation_energy.locator.page == 3
    assert activation_energy.locator.figure == "6"

    axes = ("composition", "total_pressure_Pa", "fO2_Pa", "temperature_K")
    print(
        "isolated migration: obs=5 hard=0 unavailable=5; identity axes for three rows:"
    )
    for observation_id in sorted(SCHEMA_GAP_IDS)[:3]:
        identity = migrated[observation_id].identity
        print(observation_id, {axis: getattr(identity, axis) for axis in axes})

    migrated_context = {
        row["context_id"].rsplit("::", 1)[-1]: row
        for rows in result.context_by_work.values()
        for row in rows
    }
    source_context = {
        row["observation_id"]: row
        for row in document["species"]["SiO"]["context"]
    }
    assert len(migrated_context) == len(document["species"]["SiO"]["context"])
    assert len(migrated_context) == 13
    assert migrated_context["hertl_1967_fig1_marker_legend"]["values"][
        "marker_legend_as_printed"
    ] == source_context["hertl_1967_fig1_marker_legend"]["values"][
        "marker_legend_as_printed"
    ]
    for context_id in (
        "hertl_1967_starting_material_characterization",
        "hertl_1967_disproportionation_numeric_results",
    ):
        assert migrated_context[context_id]["values"] == source_context[context_id][
            "values"
        ]
