from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import (
    AdmissionStatus,
    EvidenceClass,
    NoticeKind,
    Quantity,
    ValueKind,
)


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
        assert observation.evidence.class_.is_value
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert observation.admission.status is AdmissionStatus.ADMITTED
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
    _assert_printed_point(
        migrated_targets,
        source_id="kems-022-demaria-1971",
        id_fragment="demaria_1971_alo_psat_table4::T=2320.0",
        temperature_K="2320.0",
        value="0.10639125",
    )


def test_demaria_in_band_rows_have_thirty_points_and_twelve_context_rows(
    migrated_targets,
) -> None:
    observations = _source_observations(migrated_targets, "kems-022-demaria-1971")
    rows = []
    for observation in observations:
        if not any(
            fragment in observation.observation_id
            for fragment in (
                "demaria_1971_al2o_psat_table4",
                "demaria_1971_alo_psat_table4",
                "demaria_1971_o_atomic_psat_table5",
                "demaria_1971_sio2_sio_o2_equilibrium_table7",
                "demaria_1971_alo_al_o_kp_table8",
            )
        ):
            continue
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator is not None
        if Decimal("1950") <= temperature.state.value <= Decimal("2473"):
            rows.append(observation)

    assert len(rows) == 42
    points = [observation for observation in rows if observation.value.kind is ValueKind.POINT]
    context = [observation for observation in rows if observation.value.kind is not ValueKind.POINT]
    assert len(points) == 30
    assert len(context) == 12
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.P_PARTIAL
        and observation.evidence.class_.is_value
        and observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        and observation.admission.status is AdmissionStatus.ADMITTED
        for observation in points
    )
    assert all(observation.value.kind is ValueKind.UNAVAILABLE for observation in context)
    assert all(not observation.identity.quantity.is_value for observation in context)

    expected_temperatures = {
        "demaria_1971_al2o_psat_table4": (
            "2060", "2104", "2125", "2146", "2180", "2183", "2194",
            "2270", "2290", "2360", "2395", "2425", "2465",
        ),
        "demaria_1971_alo_psat_table4": ("2320", "2360", "2395", "2425", "2465"),
        "demaria_1971_o_atomic_psat_table5": (
            "2015", "2060", "2104", "2146", "2162", "2194", "2270",
            "2320", "2360", "2395", "2425", "2465",
        ),
        "demaria_1971_sio2_sio_o2_equilibrium_table7": (
            "1990", "2035", "2060", "2060", "2080", "2120", "2150", "2170",
        ),
        "demaria_1971_alo_al_o_kp_table8": ("2360", "2395", "2425", "2465"),
    }
    expected_counts = {
        "demaria_1971_al2o_psat_table4": 13,
        "demaria_1971_alo_psat_table4": 5,
        "demaria_1971_o_atomic_psat_table5": 12,
        "demaria_1971_sio2_sio_o2_equilibrium_table7": 8,
        "demaria_1971_alo_al_o_kp_table8": 4,
    }
    for fragment, expected in expected_temperatures.items():
        matching = [
            observation
            for observation in rows
            if fragment in observation.observation_id
        ]
        actual = sorted(
            observation.point_conditions["temperature_K"].state.value
            for observation in matching
        )
        assert actual == sorted(Decimal(value) for value in expected), fragment
        assert len(matching) == expected_counts[fragment], fragment


def test_demaria_atomic_oxygen_points_use_smales_catalogue_composition(
    migrated_targets,
) -> None:
    oxygen = [
        observation
        for observation in _source_observations(
            migrated_targets, "kems-022-demaria-1971"
        )
        if "demaria_1971_o_atomic_psat_table5" in observation.observation_id
    ]
    assert len(oxygen) == 13
    expected_sources = {
        "12022": "smales_1971_apollo_12022_bulk_composition_tables1_2_3",
        "12065": "smales_1971_apollo_12065_bulk_composition_tables1_2_3",
    }
    for observation in oxygen:
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.quantity.value is Quantity.P_PARTIAL
        assert observation.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
        assert observation.admission.status is AdmissionStatus.ADMITTED
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        assert composition.value.proxy_flag == "composition_from_sample_catalog"
        assert "depleted residue" in composition.value.analysis_selection_rule
        sample = "12022" if ":row=12022" in observation.observation_id else "12065"
        assert expected_sources[sample] in composition.value.proxy_source
        assert any(
            notice.kind is NoticeKind.COMPOSITION_FROM_SAMPLE_CATALOG
            for notice in observation.notices
        )


def test_demaria_context_rows_keep_unmapped_thermochemistry_reasons() -> None:
    extract = yaml.safe_load(
        (_REPO_ROOT / "data/literature/extracts/kems-022-demaria-1971.yaml").read_text()
    )
    species = extract["species"]
    table7 = species["SiO2"]["observations"][0]["values"]
    table8 = species["AlO"]["observations"][1]["values"]
    assert table7["condensed_phase"] == "lunar_basalt_melt"
    assert table8["condensed_phase"] == "lunar_basalt_melt"
    expected_method = "third_law_kems"
    assert table7["method_class"] == expected_method
    assert table7["evidence_class"] == "authors_third_law_from_this_work_pressures"
    assert "no current quantity token fits" in table7["context_note"].casefold()
    assert table8["method_class"] == expected_method
    assert table8["evidence_class"] == "authors_third_law_from_this_work_pressures"
    assert "no current quantity token fits kp" in table8["context_note"].casefold()
    table4 = species["Al2O"]["observations"][0]["values"]
    assert "Only intermediate-range data included by authors" in table4[
        "reliability_note"
    ]
    alo_table4 = species["AlO"]["observations"][0]["values"]
    assert "AlO NAA points mostly final-range" in alo_table4["reliability_note"]
    oxygen = species["O"]["observations"][0]["values"]
    assert oxygen["evidence_class"] == "authors_this_work"
    assert oxygen["condensed_phase"] == "lunar_basalt_melt"


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
