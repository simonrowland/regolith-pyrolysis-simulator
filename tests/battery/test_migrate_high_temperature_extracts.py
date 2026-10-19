from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import Quantity, ValueKind
from tools.validate_literature_extracts import check_all_fidelity_samples_match


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGET_SIDS = (
    "kems-015-hashimoto-1983",
    "kems-022-demaria-1971",
    "kems-027-plante-hastie-1983",
    "wimpenny-2019-zn-isotope-evaporation-extreme-t",
    "nakano-hashimoto-2020-bubbles-to-chondrites-i",
    "kems-031-halwax-2024",
    "kems-133-costa-2017",
    "kems-025-markova-1983",
    "kems-007-costa-2015",
)


@pytest.fixture(scope="module")
def migrated_targets():
    extract_dir = _REPO_ROOT / "data" / "literature" / "extracts"
    extracts = [extract_dir / f"{sid}.yaml" for sid in _TARGET_SIDS]
    with patch.object(migrate_module, "discover_extracts", return_value=extracts):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


@pytest.fixture(scope="module")
def crown_high_t_migrated():
    extract_dir = _REPO_ROOT / "data" / "literature" / "extracts"
    extracts = [
        extract_dir / "kems-029-yakovlev-shornikov-2011.yaml",
        extract_dir / "yakovlev-2024-cai-vaporization.yaml",
    ]
    with patch.object(migrate_module, "discover_extracts", return_value=extracts):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


def _source_observations(result, source_id: str):
    return [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
    ]


def _assert_printed_point(
    result,
    *,
    source_id: str,
    id_fragment: str,
    temperature_K: str,
    value: str,
) -> None:
    observations = [
        observation
        for observation in _source_observations(result, source_id)
        if id_fragment in observation.observation_id
        and observation.value.kind is ValueKind.POINT
        and observation.value.point == Decimal(value)
    ]
    assert observations, (source_id, id_fragment, temperature_K, value)
    point = observations[0]
    temperature = point.point_conditions["temperature_K"]
    assert temperature.state.is_value
    assert temperature.state.value == Decimal(temperature_K)
    assert temperature.locator is not None


def test_yield_table_items_recognizes_numeric_mass_loss_runs() -> None:
    runs = [
        {"Tmax_C": 2183, "mass_loss_wt_pct": 97.4},
        {"Tmax_C": 2928, "mass_loss_wt_pct": "?"},
    ]
    assert migrate_module._yield_table_items({"runs": runs}) == ("runs", runs)
    assert migrate_module._yield_table_items(
        {"runs": [{"Tmax_C": 2183, "mass_loss_wt_pct": "?"}]}
    ) == (None, None)
    assert migrate_module._yield_table_items(
        {"runs": [{"T_C": 1700, "FeO_wt_pct": 27.92}]}
    ) == (None, None)


@pytest.mark.parametrize(
    ("source_id", "id_fragment", "temperature_K", "value"),
    [
        (
            "kems-015-hashimoto-1983",
            "hashimoto_1983_table3_residue_composition_series::T=1973.15:row=c7",
            "1973.15",
            "15.53",
        ),
        (
            "kems-022-demaria-1971",
            "demaria_1971_o_atomic_psat_table5::T=2015.0:row=12022",
            "2015.0",
            "0.0093219",
        ),
        (
            "wimpenny-2019-zn-isotope-evaporation-extreme-t",
            "wimpenny_2019_table1_experimental_zn_rows::rhyolite-soil-1::T=2073.15",
            "2073.15",
            "0.3",
        ),
        (
            "kems-133-costa-2017",
            "costa_2017_solidus",
            "2050.0",
            "2050.0",
        ),
        (
            "kems-025-markova-1983",
            "markova_1983_fe_psat_figs_figure_only::T=1984.47",
            "1984.47",
            "0.9110692037171052631578947367",
        ),
        (
            "kems-007-costa-2015",
            "costa_2015_fe_partial_pressure_ir_digitized::T=2014.83",
            "2014.83",
            "1.214395462383",
        ),
    ],
)
def test_existing_high_temperature_points_keep_printed_value_and_temperature(
    migrated_targets, source_id, id_fragment, temperature_K, value
) -> None:
    _assert_printed_point(
        migrated_targets,
        source_id=source_id,
        id_fragment=id_fragment,
        temperature_K=temperature_K,
        value=value,
    )


def test_demaria_table4_points_keep_printed_values_and_temperatures(migrated_targets) -> None:
    observations = _source_observations(migrated_targets, "kems-022-demaria-1971")
    table4 = [
        observation
        for observation in observations
        if (
            "demaria_1971_al2o_psat_table4" in observation.observation_id
            or "demaria_1971_alo_psat_table4" in observation.observation_id
        )
        and observation.value.kind is ValueKind.POINT
    ]
    assert len(table4) == 20
    for observation in table4:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
    alo_points = [
        observation
        for observation in table4
        if "demaria_1971_alo_psat_table4" in observation.observation_id
    ]
    assert len(alo_points) == 6
    assert {point.identity.species.formula for point in alo_points} == {"AlO"}
    _assert_printed_point(
        migrated_targets,
        source_id="kems-022-demaria-1971",
        id_fragment="demaria_1971_al2o_psat_table4::T=2060.0",
        temperature_K="2060.0",
        value="0.00051371775",
    )


def test_hashimoto_geometry_is_not_residue_composition(migrated_targets) -> None:
    geometry = [
        observation
        for observation in _source_observations(
            migrated_targets, "kems-015-hashimoto-1983"
        )
        if observation.observation_id.endswith(
            "hashimoto_1983_fe_fcmas_free_evap_geometry"
        )
    ]
    assert len(geometry) == 1
    quantity = geometry[0].identity.quantity
    assert (
        not quantity.is_value
        or quantity.value is not Quantity.RESIDUE_COMPONENT_COMPOSITION
    )


def test_plante_hastie_exact_aliases_keep_printed_gas_species(migrated_targets) -> None:
    points = [
        observation
        for observation in _source_observations(
            migrated_targets, "kems-027-plante-hastie-1983"
        )
        if "plante_hastie_1983_fig19_" in observation.observation_id
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.value.kind is ValueKind.POINT
    ]
    assert len(points) == 25
    assert {point.identity.species.formula for point in points} == {
        "CsBO2",
        "LiBO2",
        "NaBO2",
    }


def test_nakano_numeric_runs_emit_stable_mass_loss_points(migrated_targets) -> None:
    observations = _source_observations(
        migrated_targets, "nakano-hashimoto-2020-bubbles-to-chondrites-i"
    )
    points = [
        observation
        for observation in observations
        if "nakano_2020_mass_loss_selected_runs::T=" in observation.observation_id
        and observation.value.kind is ValueKind.POINT
    ]
    assert len(points) == 8
    assert len({observation.observation_id for observation in points}) == 8
    assert sum(point.identity.species.formula == "Lsil" for point in points) == 5
    assert sum(point.identity.species.formula == "Allende" for point in points) == 3
    for observation in points:
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert observation.identity.species.formula in {"Lsil", "Allende"}
        assert observation.point_conditions["heating_duration_s"].state.is_value
        assert observation.point_conditions["heating_duration_s"].locator is not None
        basis = observation.provenance["printed_run_basis"]
        assert basis["material"] == observation.identity.species.formula
        assert basis["temperature_basis"] == "Tmax"
        assert basis["heating_duration_s"] == observation.point_conditions[
            "heating_duration_s"
        ].state.value
        assert basis["mass_loss_basis"].startswith(
            "Mass loss includes sample, Re wire, and graphite disk"
        )
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == observation.identity.temperature_K.value
        assert temperature.locator is not None
    _assert_printed_point(
        migrated_targets,
        source_id="nakano-hashimoto-2020-bubbles-to-chondrites-i",
        id_fragment="nakano_2020_mass_loss_selected_runs::T=2456.15",
        temperature_K="2456.15",
        value="0.974",
    )


def test_halwax_figure_only_pressures_have_no_numeric_points(migrated_targets) -> None:
    observations = _source_observations(migrated_targets, "kems-031-halwax-2024")
    assert not any(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )


def test_crown_high_t_markers_keep_values_pixels_and_conditions(
    crown_high_t_migrated,
) -> None:
    by_source = {
        source_id: [
            observation
            for observation in crown_high_t_migrated.observations.values()
            if observation.source_id == source_id
            and observation.value.kind is ValueKind.POINT
            and (
                "kems_029_fig" in observation.observation_id
                or "yakovlev_2024_fig" in observation.observation_id
            )
        ]
        for source_id in (
            "kems-029-yakovlev-shornikov-2011",
            "yakovlev-2024-cai-vaporization",
        )
    }
    assert len(by_source["kems-029-yakovlev-shornikov-2011"]) == 109
    assert len(by_source["yakovlev-2024-cai-vaporization"]) == 167
    for source_id, observations in by_source.items():
        for observation in observations:
            assert observation.point_conditions is not None
            temperature = observation.point_conditions["temperature_K"]
            composition = observation.point_conditions["composition"]
            assert temperature.locator is not None
            assert composition.locator is not None
            assert observation.identity.species.formula
            if source_id == "yakovlev-2024-cai-vaporization":
                assert observation.identity.species.phase.is_value
    for observation in crown_high_t_migrated.observations.values():
        if observation.value.kind is ValueKind.POINT:
            assert observation.point_conditions is not None
            assert all(
                condition.locator is not None
                for condition in observation.point_conditions.values()
            )

    extract_dir = _REPO_ROOT / "data" / "literature" / "extracts"
    yakovlev = yaml.safe_load(
        (extract_dir / "yakovlev-2024-cai-vaporization.yaml").read_text()
    )
    ca_reads = yakovlev["species"]["Ca"]["observations"][0]["values"]["points"][:3]
    cao = yakovlev["species"]["CaO"]["observations"][0]["values"]["points"][0]
    gehlenite = yakovlev["species"]["Ca2Al2SiO7"]["observations"][0]["values"]["points"][0]
    assert [point["T_K"] for point in ca_reads] == [2039.79, 2097.08, 2141.76]
    assert [point["p_atm"] for point in ca_reads] == [1.0e-7, 3.2e-7, 5.4e-7]
    for point, pixel in zip(
        ca_reads,
        ("pixel=(1048, 1237)", "pixel=(1157, 1047)", "pixel=(1242, 958)"),
        strict=True,
    ):
        assert point["locator"]["pdf_page_index"] == 2
        assert pixel in point["locator"]["note"]
    assert "pixel=(1062, 1242)" in ca_reads[0]["locator"]["note"]
    assert "pixel=(1152, 1049)" in ca_reads[1]["locator"]["note"]
    assert "pixel=(1238, 963)" in ca_reads[2]["locator"]["note"]
    assert "read_uncertainty_dex=0.0064183 dex" in ca_reads[0]["locator"]["note"]
    assert "read_uncertainty_dex=0.0025673 dex" in ca_reads[1]["locator"]["note"]
    assert "read_uncertainty_dex=0.0064183 dex" in ca_reads[2]["locator"]["note"]
    audit = (extract_dir / "yakovlev-2024-cai-vaporization.audit.md").read_text()
    assert "| Fig. 2A | Ca | 1 | [1048, 1237] | [1062, 1242] |" in audit
    assert "| Fig. 2A | Ca | 2 | [1157, 1047] | [1152, 1049] |" in audit
    assert "| Fig. 2A | Ca | 3 | [1242, 958] | [1238, 963] |" in audit
    assert cao["activity"] == 0.0098
    assert "pixel=(2476, 1499)" in cao["locator"]["note"]
    assert "pixel=(2464, 1501)" in cao["locator"]["note"]
    assert "| Fig. 2B | CaO | 1 | [2476, 1499] | [2464, 1501] |" in audit
    assert gehlenite["activity"] == 0.053
    assert "pixel=(356, 1467)" in gehlenite["locator"]["note"]
    assert "pixel=(352, 1471)" in gehlenite["locator"]["note"]
    assert "| Fig. 3A | Ca2Al2SiO7 | 1 | [356, 1467] | [352, 1471] |" in audit
    assert check_all_fidelity_samples_match(yakovlev) == []
    mutated = deepcopy(yakovlev)
    mutated["species"]["CaO"]["observations"][0]["values"]["points"][0]["activity"] = 0.0090
    assert check_all_fidelity_samples_match(mutated)
    profile = yakovlev["extraction"]["printed_text_values"][0]["values"]
    assert yakovlev["extraction"]["printed_text_values"][0]["locator"]["pdf_page_index"] == 2
    assert yakovlev["extraction"]["printed_text_values"][0]["locator"]["published_page"] == 31
    assert [profile[key] for key in (
        "core_akermanite_wt_pct",
        "mantle_akermanite_wt_pct",
        "rim_akermanite_wt_pct",
    )] == [50.0, 20.0, 7.0]
    sample = yakovlev["experiments"][0]["sample"]
    assert sample["printed_composition"]["state"]["reason"] == (
        "CAI type B named; only a qualitative mineral profile and quoted akermanite "
        "fractions printed; no initial oxide map"
    )
    sample_profile = sample["characterization"]["state"]["value"]
    assert "Kerekgyarto et al. (2016)" in sample_profile
    assert "approximately 50 wt. %" in sample_profile
    assert "drops to 20 wt. %" in sample_profile
    assert "up to 7 wt. % in the rim zone" in sample_profile

    kems = yaml.safe_load(
        (extract_dir / "kems-029-yakovlev-shornikov-2011.yaml").read_text()
    )
    kems_fe = kems["species"]["Fe"]["observations"][0]["values"]["points"][0]
    assert kems_fe["locator"]["pdf_page_index"] == 1
    reference_gap = next(
        gap
        for gap in yakovlev["extraction"]["schema_gaps"]
        if gap.get("quantity") == "pure-compound reference activities"
    )
    reference_reason = (
        "cited collectively; values and per-activity mapping not printed in this paper"
    )
    assert reference_gap["status"] == "typed_unavailable"
    assert reference_gap["reason"] == reference_reason
    reference_citation = (
        "(Shornikov et al., 1996; Bale et al., 2016; Shornikov, 2017)"
    )
    assert reference_citation in reference_gap["source_as_printed"]

    activity_lineage = {
        "yakovlev_2024_fig2b_cao_figure_only": (
            "CaO",
            "yakovlev_2024_fig2a_ca_figure_only",
        ),
        "yakovlev_2024_fig2b_mgo_figure_only": (
            "MgO",
            "yakovlev_2024_fig2a_mg_figure_only",
        ),
        "yakovlev_2024_fig2b_al2o3_figure_only": (
            "Al2O3",
            "yakovlev_2024_fig2a_al_figure_only",
        ),
        "yakovlev_2024_fig2b_sio2_figure_only": (
            "SiO2",
            "yakovlev_2024_fig2a_sio_figure_only",
        ),
    }
    migrated_observations = _source_observations(
        crown_high_t_migrated, "yakovlev-2024-cai-vaporization"
    )
    for activity_id, (oxide, pressure_id) in activity_lineage.items():
        values = next(
            series["values"]
            for series in yakovlev["species"][oxide]["observations"]
            if series["observation_id"] == activity_id
        )
        assert values["method_class"] == "measured_reduced"
        assert values["derived_from"] == [pressure_id]
        relation = values["derivation"]["relation"]
        assert "Eqs. (1)–(3)" in relation
        assert reference_citation in relation
        assert reference_reason in relation
        migrated = next(
            observation
            for observation in migrated_observations
            if activity_id in observation.observation_id
        )
        assert migrated.derived_from
        assert all(
            parent.startswith(
                f"yakovlev-2024-cai-vaporization::{pressure_id}::T="
            )
            for parent in migrated.derived_from
        )

    kems = yaml.safe_load(
        (extract_dir / "kems-029-yakovlev-shornikov-2011.yaml").read_text()
    )
    complementary = next(
        observation
        for observation in kems["species"]["Na"]["observations"]
        if observation["observation_id"]
        == "yakovlev_shornikov_2011_complementary_vapor_wt_pct"
    )["values"]
    pressure_series = {
        "kems_029_fig1_Na_psat_digitized",
        "kems_029_fig1_SiO_psat_digitized",
        "kems_029_fig2_SiO_psat_digitized",
        "kems_029_fig1_K_psat_digitized",
        "kems_029_fig1_Fe_psat_digitized",
        "kems_029_fig2_Fe_psat_digitized",
        "kems_029_fig1_Mg_psat_digitized",
        "kems_029_fig2_Mg_psat_digitized",
        "kems_029_fig1_Al_psat_digitized",
        "kems_029_fig2_Al_psat_digitized",
        "kems_029_fig1_Ca_psat_digitized",
        "kems_029_fig2_Ca_psat_digitized",
        "kems_029_fig1_TiO_psat_digitized",
        "kems_029_fig2_TiO_psat_digitized",
        "yakovlev_shornikov_2011_CAI_SiO_highT_about_1e-6_bar",
        "yakovlev_shornikov_2011_Po2_table_bar_calculated",
    }
    assert set(complementary["derived_from"]) == pressure_series
    derivation_relation = complementary["derivation"]["relation"]
    assert "Hertz–Knudsen equation" in derivation_relation
    assert "all 16 this-work partial-pressure source rows" in derivation_relation
