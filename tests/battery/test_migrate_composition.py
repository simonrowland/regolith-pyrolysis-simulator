"""Bulk-property rows carry a composition map, not a single-species formula."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as migrate_module
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    EvidenceClass,
    NoticeKind,
    Phase,
    RefusalReason,
    ValueKind,
)
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _catalogue_composition_located_from_values,
    _composition_located_from_values,
    _declared_wt_composition_point_map,
    _initial_oxide_map_from_values,
    _oxide_map_from_mapping,
    _sample_from_plain,
    migrate,
    oxide_molar_mass,
    sample_from_equipment,
    to_plain,
    wt_pct_to_mole_fraction,
    wt_pct_to_mole_fraction_derivation,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    Composition,
    as_decimal,
)
from tests.battery.test_migrate import _migrate_real_extract, _write_min_tree
from tests.battery.factories import tabulation_experiment


_OBS_ROOTS = (
    REPO_ROOT / "data" / "literature" / "extracts-v2",
    REPO_ROOT / "data" / "literature" / "observations-v2",
)
_BULK_QUANTITIES = {
    "mass_loss_fraction",
    "mass_loss_fraction_vs_T",
    "yield_fraction",
    "o2_yield",
    "evolved_gas_yield",
}
_SAMPLE_CODE_FORMULA = re.compile(r"(?i)^(MLS[-_]?|MS)\d")
_MARKOVA_VI_PREFIX = (
    "kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite"
)
_SWEPT_COMPOSITION_MAPS = (
    ("Zhang", "kems-006-zhang-2021", "starting_glass_wt_pct", "Rb2O", 12),
    ("LPI", "lpi-lunar-soils", "major_oxide_wt_pct", "S", 4),
    ("Britt", "britt-2019-asteroid-simulants", "composition_wt_pct", "FeOT", 3),
    ("Hendrix", "hendrix-2024-reactivity-reduced-simulants", "composition_wt_pct", "SO3", 2),
    ("Flemetakis", "ta-flemetakis-2024", "composition_wt_pct", "B2O3", 1),
)
_MLS_BULK_ID_PREFIXES = {
    "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_table1_fresnel_pyrolysis_log::T=1747.15:row=mls-1a:",
    "kems-038-matchett-2006::matchett_2006_table3_fresnel_pyrolysis_log::T=1747.15:row=mls-1a:",
    "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction",
}


def test_wt_percent_conversion_handles_swept_species_and_feot_alias() -> None:
    wt = {
        "SiO2": Decimal("50"),
        "Rb2O": Decimal("10"),
        "SO3": Decimal("5"),
        "S": Decimal("2"),
        "B2O3": Decimal("3"),
        "FeOT": Decimal("30"),
    }

    converted = wt_pct_to_mole_fraction(wt)
    components = dict(converted.components)

    assert set(components) == {"SiO2", "Rb2O", "SO3", "S", "B2O3", "FeO"}
    assert "FeOT" not in components
    assert oxide_molar_mass("FeOT") == oxide_molar_mass("FeO")
    assert abs(sum(components.values()) - Decimal("1")) < Decimal("1e-27")


@pytest.mark.parametrize("iron_oxide", ("FeO", "Fe2O3"))
def test_wt_percent_conversion_refuses_ambiguous_feot_and_iron_oxide(
    iron_oxide: str,
) -> None:
    with pytest.raises(ValueError, match=rf"FeOT with {iron_oxide}"):
        wt_pct_to_mole_fraction(
            {
                "SiO2": Decimal("50"),
                "FeOT": Decimal("30"),
                iron_oxide: Decimal("20"),
            }
        )


def test_wt_percent_conversion_refuses_unknown_formula_species() -> None:
    with pytest.raises(ValueError, match="XxO"):
        wt_pct_to_mole_fraction(
            {"SiO2": Decimal("50"), "MgO": Decimal("40"), "XxO": Decimal("10")}
        )


def test_wt_percent_conversion_ignores_printed_total() -> None:
    composition = wt_pct_to_mole_fraction(
        {
            "SiO2": Decimal("50"),
            "MgO": Decimal("49"),
            "sum_wt_pct": Decimal("99"),
        }
    )

    assert composition == wt_pct_to_mole_fraction(
        {"SiO2": Decimal("50"), "MgO": Decimal("49")}
    )


def test_values_with_unsupported_wt_component_do_not_yield_partial_map() -> None:
    refusal: list[str] = []
    assert _initial_oxide_map_from_values(
        {
            "composition_wt_pct": {
                "SiO2": Decimal("50"),
                "MgO": Decimal("40"),
                "XxO": Decimal("10"),
            }
        },
        refusal_reason=refusal,
    ) is None
    assert refusal and "XxO" in refusal[0]


def test_flat_composition_with_unsupported_numeric_key_is_refused() -> None:
    refusal: list[str] = []
    assert _initial_oxide_map_from_values(
        {
            "composition_wt_pct": {
                "SiO2": Decimal("50"),
                "MgO": Decimal("40"),
                "XxO": Decimal("10"),
            }
        },
        refusal_reason=refusal,
    ) is None
    assert refusal and "XxO" in refusal[0]


def test_sample_from_equipment_withholds_unsupported_numeric_component() -> None:
    sample = sample_from_equipment(
        {},
        values={
            "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
            "locator": {"table": "1"},
            "composition_wt_pct": {
                "SiO2": Decimal("50"),
                "MgO": Decimal("40"),
                "XxO": Decimal("10"),
                "sum_wt_pct": Decimal("100"),
            },
        },
    )

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == {
        "MgO": "40",
        "XxO": "10",
        "SiO2": "50",
        "sum_wt_pct": "100",
    }
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_unknown
    assert "XxO" in sample.initial_composition.state.reason


def test_sample_without_composition_locator_keeps_unsupported_key_reason() -> None:
    sample = sample_from_equipment(
        {},
        values={
            "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
            "composition_wt_pct": {
                "SiO2": Decimal("50"),
                "MgO": Decimal("40"),
                "XxO": Decimal("10"),
            },
        },
    )

    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_unknown
    assert "XxO" in sample.initial_composition.state.reason


def test_noncomposition_values_are_not_scanned_or_refused_as_oxides() -> None:
    values = {
        "T_end_C_as_printed": Decimal("1250"),
        "flow_rate_sccm_as_printed": Decimal("60"),
        "pressure_mbar_as_printed": Decimal("1.2"),
        "ramp_C_per_min_as_printed": Decimal("10"),
        "composition_id": "CI",
        "sample_code": "MLS-1A",
    }
    refusal: list[str] = []

    assert migrate_module._initial_oxide_map_from_values(
        values, refusal_reason=refusal
    ) is None
    assert refusal == []

    sample = sample_from_equipment({}, values=values)
    assert sample.printed_composition is None
    assert sample.initial_composition is None
    assert not hasattr(migrate_module, "_is_formula_component_key")


def test_plante_hastie_fit_coefficients_are_not_composition_evidence() -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-027-plante-hastie-1983.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    values = next(
        observation["values"]
        for species in doc["species"].values()
        for observation in species["observations"]
        if observation["observation_id"] == "plante_hastie_1983_fig19_nabo2_logP_ls"
    )
    assert values["A"] == 4.09
    assert values["B"] == 11900.0
    assert "composition_analytical_Na2O_wt_pct" not in values

    refusal: list[str] = []
    assert _initial_oxide_map_from_values(values, refusal_reason=refusal) is None
    assert refusal == []

    migrator = Migrator()
    migrator._migrate_extract(path)
    source_experiment = migrator.result.experiments[
        "10.6028/nbs.ir.83-2731::experiment::plante-1983-model-assay"
    ]
    assert source_experiment.sample.initial_composition is None


def test_hastie_typed_initial_charge_reaches_experiment_sample() -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-020-hastie-1981-nbsir.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for item in document["species"]["K2O"]["observations"]
        if item["observation_id"] == "hastie_1981_fig11_k1_slag_k2o_gamma_digitized"
    )
    assert observation["values"]["composition_role"]["value"] == "initial_charge_only"
    selected = sample_from_equipment(
        None,
        values=observation["values"],
        locator=migrate_module.locator_from_mapping(observation["locator"]),
    )
    assert selected.initial_composition is not None

    migrator = Migrator()
    migrator._migrate_extract(path)
    migrator.finalize()
    experiment = migrator.result.experiments[
        "10.6028/nbs.ir.81-2279::experiment::hastie-1981-k1-kms"
    ]

    actual = experiment.sample.initial_composition
    assert actual is not None and actual.state.is_value
    assert dict(actual.state.value.components) == dict(
        selected.initial_composition.state.value.components
    )


def test_volovetskii_starting_glass_material_state_is_initial() -> None:
    path = REPO_ROOT / "data/literature/extracts/volovetskii-2012-iron-redox.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    observation = next(
        item
        for species in document["species"].values()
        for item in species["observations"]
        if item["observation_id"] == "volo12_p9_initial_fe_redox"
    )
    assert observation["values"]["composition_material_state"] == "starting_glass"
    selected = sample_from_equipment(
        None,
        values=observation["values"],
        locator=migrate_module.locator_from_mapping(observation["locator"]),
    )
    expected = selected.initial_composition
    assert expected is not None and not expected.state.is_value
    assert "unsupported numeric component(s): Cl" in expected.state.reason

    migrator = Migrator()
    migrator._migrate_extract(path)
    migrator.finalize()
    row = next(
        row
        for row in migrator.result.observations.values()
        if row.observation_id.endswith("volo12_p9_initial_fe_redox")
    )
    actual = migrator.result.experiments[row.experiment_id].sample.initial_composition

    assert actual is not None and actual.state == expected.state


def _plante_analytical_composition(doc: dict) -> tuple[dict[str, Decimal], object]:
    table1 = next(
        item
        for item in doc["experiments"]
        if item["experiment_id"] == "kms-vacuum-glass-series"
    )["sample"]["printed_composition"]["state"]["value"]
    analytical_wt = {
        name: as_decimal(value) for name, value in table1["analytical"].items()
    }
    return analytical_wt, wt_pct_to_mole_fraction(analytical_wt)


def _assert_plante_samples_use_analyzed_composition(
    result, expected_composition
) -> None:
    for experiment_id, bench in (
        ("kms-vacuum-glass-series", "kms"),
        ("tms-n2-glass-series", "tms"),
    ):
        migrated_id = next(
            key
            for key in result.experiments
            if key.endswith(f"::experiment::{experiment_id}")
        )
        experiment = result.experiments[migrated_id]
        assert experiment.bench_id.endswith(f"::bench::{bench}")
        initial = experiment.sample.initial_composition
        assert initial is not None and initial.state.is_value
        assert initial.locator is not None
        actual = dict(initial.state.value.components)
        expected = dict(expected_composition.components)
        assert set(actual) == set(expected) and len(actual) == 15
        assert all(
            abs(actual[name] - expected[name]) < Decimal("1e-11")
            for name in expected
        ), f"{experiment_id} sample composition differs from Table 1 analytical values"
        assert "analy" in initial.locator.note.lower()


def _assert_plante_observations_use_analyzed_composition(
    result, expected_composition
) -> None:
    tied_rows = {
        "plante_hastie_1983_table1_snw_glass_composition": "plante-1983-model-assay",
        "plante_hastie_1983_table3_mass_loss": "kms-vacuum-glass-series",
        "plante_hastie_1983_nabo2_activity_approx": "kms-vacuum-glass-series",
        "plante_hastie_1983_nabo2_activity_quoted": "kms-vacuum-glass-series",
        "plante_hastie_1983_libo2_activity_approx": "kms-vacuum-glass-series",
        "plante_hastie_1983_libo2_activity_quoted": "kms-vacuum-glass-series",
        "plante_hastie_1983_csbo2_activity_1200K": "kms-vacuum-glass-series",
        "plante_hastie_1983_csbo2_activity_quoted": "kms-vacuum-glass-series",
        "plante_hastie_1983_ru_re_sr_undetected": "kms-vacuum-glass-series",
        "plante_hastie_1983_o2_kms_figure_only": "kms-vacuum-glass-series",
        "plante_hastie_1983_o2_figure_only_quoted": "kms-vacuum-glass-series",
    }
    expected = dict(expected_composition.components)
    for source_id, experiment_id in tied_rows.items():
        observation = result.observations[
            f"kems-027-plante-hastie-1983::{source_id}"
        ]
        assert observation.experiment_id.endswith(
            f"::experiment::{experiment_id}"
        ), f"{source_id} is not independently bound to its source experiment"
        composition = observation.identity.composition
        assert composition is not None and composition.is_value, source_id
        actual = dict(composition.value.components)
        assert len(actual) == 15 and set(actual) == set(expected), source_id
        assert all(
            abs(actual[name] - expected[name]) < Decimal("1e-11")
            for name in expected
        ), f"{source_id} composition differs from Table 1 analytical values"
        if source_id in {
            "plante_hastie_1983_ru_re_sr_undetected",
            "plante_hastie_1983_detection_limits_quoted",
        }:
            phase = observation.identity.species.phase
            assert phase.is_value and phase.value is Phase.G, source_id


def test_plante_section_4_1_detection_limits_have_no_vacuum_conditions() -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-027-plante-hastie-1983.yaml"
    migrator = Migrator()
    migrator._migrate_extract(path)

    observation = migrator.result.observations[
        "kems-027-plante-hastie-1983::plante_hastie_1983_detection_limits_quoted"
    ]
    experiment = migrator.result.experiments[observation.experiment_id]

    assert not experiment.experiment_id.endswith(
        "::experiment::kms-vacuum-glass-series"
    )
    assert not (
        experiment.method.is_value
        and experiment.method.value.value == "knudsen_effusion"
    )
    sweep_gas = experiment.pressure_environment.sweep_gas.state
    assert not (
        sweep_gas.is_value and sweep_gas.value.species in {None, "none", "vacuum"}
    )
    regime = experiment.pressure_environment.regime.regime_class
    assert not (regime.is_value and regime.value.value == "molecular")


@pytest.mark.parametrize(
    ("experiment_id", "bench"),
    (
        ("kms-vacuum-glass-series", "kms"),
        ("tms-n2-glass-series", "tms"),
    ),
)
def test_plante_glass_initials_use_analyzed_printed_map_by_name(
    monkeypatch, experiment_id: str, bench: str
) -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-027-plante-hastie-1983.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    analytical_wt, expected = _plante_analytical_composition(doc)
    table1 = next(
        row
        for species in doc["species"].values()
        for row in species["observations"]
        if row["observation_id"] == "plante_hastie_1983_table1_snw_glass_composition"
    )
    assert table1["values"]["composition_wt_pct"] == {
        name: float(value) for name, value in analytical_wt.items()
    }

    convert = migrate_module.wt_pct_to_mole_fraction
    seen: list[dict[str, Decimal]] = []

    def record_map(wt):
        if "Li2O" in wt:
            seen.append({name: as_decimal(value) for name, value in wt.items()})
        return convert(wt)

    monkeypatch.setattr(migrate_module, "wt_pct_to_mole_fraction", record_map)
    migrator = Migrator()
    migrator._migrate_extract(path)
    migrated_id = next(
        key
        for key in migrator.result.experiments
        if key.endswith(f"::experiment::{experiment_id}")
    )
    experiment = migrator.result.experiments[migrated_id]

    assert experiment.bench_id.endswith(f"::bench::{bench}")
    assert analytical_wt in seen
    _assert_plante_samples_use_analyzed_composition(migrator.result, expected)
    _assert_plante_observations_use_analyzed_composition(migrator.result, expected)


def test_plante_fig19_points_keep_pooled_sample_unknown_and_gas_phase() -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-027-plante-hastie-1983.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    migrator = Migrator()
    migrator._migrate_extract(path)
    source_points = {
        row["observation_id"]: row["values"]["points"]
        for species in doc["species"].values()
        for row in species["observations"]
        if row["observation_id"]
        in {
            "plante_hastie_1983_fig19_nabo2_logP_ls",
            "plante_hastie_1983_fig19_libo2_logP_ls",
            "plante_hastie_1983_fig19_csbo2_logP_ls",
        }
    }
    fit_points = [
        observation
        for observation in migrator.result.observations.values()
        if "::T=" in observation.observation_id
        and any(
            name in observation.observation_id
            for name in (
                "fig19_nabo2_logP_ls",
                "fig19_libo2_logP_ls",
                "fig19_csbo2_logP_ls",
            )
        )
    ]

    assert len(fit_points) == 25
    for source_id, points in source_points.items():
        migrated = [
            observation
            for observation in fit_points
            if f"::{source_id}::T=" in observation.observation_id
        ]
        assert len(migrated) == len(points)
        expected_temperatures = {as_decimal(point["T_K"]) for point in points}
        assert all("T_C" not in point for point in points)
        assert {
            observation.identity.temperature_K.value
            for observation in migrated
            if observation.identity.temperature_K is not None
            and observation.identity.temperature_K.is_value
        } == expected_temperatures
        for observation in migrated:
            temperature = (observation.point_conditions or {}).get("temperature_K")
            assert temperature is not None and temperature.state.is_value
            assert temperature.state.value in expected_temperatures
            assert temperature.locator is not None
    assert all(
        observation.identity.species.phase.is_value
        and observation.identity.species.phase.value is Phase.G
        for observation in fit_points
    )
    assert all(
        observation.identity.composition is None
        or observation.identity.composition.is_unknown
        for observation in fit_points
    )
    assert all(
        "without per-point sample labels" in observation.locator.note
        for observation in fit_points
    )
    assert all(
        not observation.experiment_id.endswith(
            ("::experiment::kms-vacuum-glass-series", "::experiment::tms-n2-glass-series")
        )
        for observation in fit_points
    )
    o2_figure = migrator.result.observations[
        "kems-027-plante-hastie-1983::plante_hastie_1983_o2_figure_only_quoted"
    ]
    assert o2_figure.value.kind is ValueKind.UNAVAILABLE
    assert o2_figure.admission.status is AdmissionStatus.PENDING
    assert o2_figure.evidence.class_.is_value
    assert o2_figure.evidence.class_.value is EvidenceClass.FIGURE_ONLY
    assert o2_figure.identity.species.phase.is_value
    assert o2_figure.identity.species.phase.value is Phase.G


def test_composition_mass_percent_keeps_feot_until_owner_conversion() -> None:
    ambiguous: list[str] = []
    assert migrate_module._oxide_map_from_mapping(
        {
            "composition_mass_percent": {
                "SiO2": Decimal("50"),
                "FeOT": Decimal("20"),
                "FeO": Decimal("30"),
            }
        },
        refusal_reason=ambiguous,
    ) is None
    assert ambiguous == ["ambiguous total iron components: FeOT with FeO"]

    wt = migrate_module._oxide_map_from_mapping(
        {
            "composition_mass_percent": {
                "SiO2": Decimal("50"),
                "FeOT": Decimal("20"),
                "MgO": Decimal("30"),
            }
        }
    )
    assert wt is not None and set(wt) == {"SiO2", "FeOT", "MgO"}
    converted = wt_pct_to_mole_fraction(wt)
    assert "FeO" in dict(converted.components)
    derivation = wt_pct_to_mole_fraction_derivation(wt, None)
    assert "FeOT (total iron) treated as FeO; printed key FeOT" in derivation.inputs


def test_declared_composition_map_refuses_combined_alkali_label_by_name() -> None:
    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/engelschion-2020-eac1a-simulant.yaml").read_text()
    )
    values = next(
        item["values"]
        for species in source["species"].values()
        for category in ("observations", "context")
        for item in species.get(category, [])
        if item["observation_id"] == "engelschion_2020_eac_xrf_composition"
    )
    refusal: list[str] = []
    assert _oxide_map_from_mapping(
        {"composition_wt_pct": values["composition_wt_pct"]},
        refusal_reason=refusal,
    ) is None
    assert refusal == [
        "printed oxide wt% map has unsupported numeric component(s): Na2O_plus_K2O"
    ]


def test_merge_preserves_selected_initial_without_incoming_initial() -> None:
    path = REPO_ROOT / "data/literature/extracts/kems-010-richter-2007.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw_experiment = next(
        item for item in source["experiments"] if item["experiment_id"] == "cai-r3-12"
    )
    sample = replace(
        tabulation_experiment().sample,
        initial_composition=migrate_module._composition_located_from_plain(
            raw_experiment["sample"]["initial_composition"]
        ),
        printed_composition=migrate_module._located_from_plain(
            raw_experiment["sample"]["printed_composition"], lambda value: value
        ),
    )
    existing = replace(
        tabulation_experiment("richter-cai-r3-12"), sample=sample
    )
    starting = existing.sample.initial_composition
    assert starting is not None and starting.state.is_value
    incoming = replace(sample, initial_composition=None)

    merged = migrate_module._merge_experiment_lab_params(
        existing,
        incoming,
        existing.apparatus,
        existing.pressure_environment,
    )

    assert merged.sample.initial_composition == starting


def test_noncomposition_values_in_britt_plante_and_van_limpt_are_ignored() -> None:
    cases = (
        ("britt-2019-asteroid-simulants", "britt_2019_tga_ega_thermal_program"),
        ("kems-042-plante-1979", "plante1979_table2_s1123_r030_quoted"),
        ("kems-200-ueshima-1983", "ueshima_1983_femo_table2_epma"),
        (
            "kems-046-van-limpt-2007",
            "van_limpt_2007_setup_c_and_sodium_conditions",
        ),
    )
    for source, observation_id in cases:
        doc = yaml.safe_load(
            (REPO_ROOT / "data/literature/extracts" / f"{source}.yaml").read_text()
        )
        values = next(
            observation["values"]
            for species in (doc.get("species") or {}).values()
            for observation in (species.get("observations") or [])
            if observation.get("observation_id") == observation_id
        )
        refusal: list[str] = []
        if observation_id == "plante1979_table2_s1123_r030_quoted":
            assert _initial_oxide_map_from_values(
                values,
                refusal_reason=refusal,
                declared_map=migrate_module._declared_wt_composition_point_map(
                    values, None
                ),
            ) == {"K2O": Decimal("21.14"), "SiO2": Decimal("78.86")}
            located = _composition_located_from_values(values, None)
            assert located is not None and located.state.is_value
            assert located.inference is not None
            assert located.inference.relation == (
                "SiO2_wt_pct=100-K2O_wt_pct;wt_pct_to_mole_fraction"
            )
        else:
            assert _initial_oxide_map_from_values(
                values, refusal_reason=refusal
            ) is None
            assert refusal == [], observation_id


@pytest.mark.parametrize("include_parent_map", [False, True])
def test_markova_point_unsupported_map_refuses_without_parent_fallback(
    include_parent_map: bool,
) -> None:
    from simulator.battery.waypoints import normalized_composition
    from tests.battery.test_waypoints import _bench

    path = REPO_ROOT / "data/literature/extracts/kems-025-markova-1983.yaml"
    doc = migrate_module.load_yaml(path)
    raw = next(
        observation
        for body in doc["species"].values()
        for observation in body.get("observations", [])
        if any(
            "composition_wt_pct" in point
            for point in observation.get("values", {}).get("points", [])
        )
    )
    point = next(
        point
        for point in raw["values"]["points"]
        if "composition_wt_pct" in point
    )
    if include_parent_map:
        raw["values"]["composition_wt_pct"] = dict(
            point["composition_wt_pct"]
        )
    else:
        raw["values"].pop("composition_wt_pct", None)
    point["composition_wt_pct"] = dict(
        point["composition_wt_pct"], XxO=10
    )
    prefix = f"{doc['source_id']}::{raw['observation_id']}"
    target_t = migrate_module.as_decimal(point["T_C"]) + Decimal("273.15")
    migrator = Migrator(REPO_ROOT)
    original_load_yaml = migrate_module.load_yaml
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            migrate_module,
            "load_yaml",
            lambda candidate: doc if candidate == path else original_load_yaml(candidate),
        )
        migrator._migrate_extract(path)

    row = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.startswith(prefix)
        and observation.identity.temperature_K.value == target_t
    )
    composition = row.identity.composition
    assert composition is not None and composition.is_unknown
    assert "XxO" in composition.reason
    printed = row.point_conditions["printed_composition"]
    assert printed.state.is_value and printed.state.value["XxO"] == Decimal("10")
    condition = row.point_conditions["composition"]
    assert condition.state.is_unknown and "XxO" in condition.state.reason
    waypoint = normalized_composition(
        migrator.result.experiments[row.experiment_id], _bench(), row
    )
    assert waypoint.selected is None
    assert waypoint.absence is not None


def test_markova_point_refusal_mutation_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping the child refusal restores the parent's accepted composition."""
    from simulator.battery.waypoints import normalized_composition
    from tests.battery.test_waypoints import _bench

    path = REPO_ROOT / "data/literature/extracts/kems-025-markova-1983.yaml"
    doc = migrate_module.load_yaml(path)
    raw = next(
        observation
        for body in doc["species"].values()
        for observation in body.get("observations", [])
        if any(
            "composition_wt_pct" in point
            for point in observation.get("values", {}).get("points", [])
        )
    )
    point = next(
        point
        for point in raw["values"]["points"]
        if "composition_wt_pct" in point
    )
    raw["values"]["composition_wt_pct"] = dict(point["composition_wt_pct"])
    point["composition_wt_pct"] = dict(point["composition_wt_pct"], XxO=10)
    original_parse = migrate_module._oxide_map_from_mapping

    def discard_refusal(obj, *, refusal_reason=None, declared_map=False):
        return original_parse(obj, declared_map=declared_map)

    monkeypatch.setattr(migrate_module, "_oxide_map_from_mapping", discard_refusal)
    original_load_yaml = migrate_module.load_yaml
    monkeypatch.setattr(
        migrate_module,
        "load_yaml",
        lambda candidate: doc if candidate == path else original_load_yaml(candidate),
    )
    target_t = migrate_module.as_decimal(point["T_C"]) + Decimal("273.15")
    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(path)
    prefix = f"{doc['source_id']}::{raw['observation_id']}"
    row = next(
        observation
        for observation in migrator.result.observations.values()
        if observation.observation_id.startswith(prefix)
        and observation.identity.temperature_K.value == target_t
    )
    assert row.identity.composition.is_value
    assert row.identity.composition.value == wt_pct_to_mole_fraction(
        raw["values"]["composition_wt_pct"]
    )
    assert normalized_composition(
        migrator.result.experiments[row.experiment_id], _bench(), row
    ).selected is not None


def test_plante_declared_map_mutation_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping declaration at the composition owner erases Plante lineage."""
    doc = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/kems-042-plante-1979.yaml").read_text()
    )
    values = next(
        observation["values"]
        for species in doc["species"].values()
        for observation in species["observations"]
        if observation["observation_id"]
        == "plante1979_table2_s1123_r030_quoted"
    )
    original_reader = migrate_module._initial_oxide_map_from_values

    def drop_declaration(values, *, refusal_reason=None, declared_map=False):
        return original_reader(
            values, refusal_reason=refusal_reason, declared_map=False
        )

    monkeypatch.setattr(
        migrate_module, "_initial_oxide_map_from_values", drop_declaration
    )
    assert _composition_located_from_values(values, None) is None


def test_wt_composition_declaration_requires_a_component_map() -> None:
    yakovlev = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/kems-029-yakovlev-shornikov-2011.yaml").read_text()
    )
    vapor_values = next(
        observation["values"]
        for species in yakovlev["species"].values()
        for observation in species["observations"]
        if observation["observation_id"]
        == "yakovlev_shornikov_2011_complementary_vapor_wt_pct"
    )
    assert not _declared_wt_composition_point_map(
        vapor_values, "wt percent as published"
    )

    nasa = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/nasa-tm-2024-simulant-guide.yaml").read_text()
    )
    table_values = next(
        observation["values"]
        for observation in nasa["species"]["lunar_regolith_simulants"]["observations"]
        if observation["observation_id"] == "nasa_tm_2024_table6_apollo_bulk_chemistry"
    )
    assert _declared_wt_composition_point_map(table_values, "wt.% as printed")


def test_wt_percent_derivation_ignores_printed_totals() -> None:
    derivation = wt_pct_to_mole_fraction_derivation(
        {
            "SiO2": Decimal("50"),
            "MgO": Decimal("49"),
            "Total": Decimal("99"),
        },
        None,
    )

    assert "M_Total_g_mol" not in dict(derivation.parameters)


def test_nested_and_flat_composition_maps_skip_mass_loss_conditions() -> None:
    components = {"SiO2": Decimal("50"), "MgO": Decimal("40")}
    assert _initial_oxide_map_from_values(
        {"composition_wt_pct": {**components, "T_C": 0, "mass_loss_wt_pct": 10}}
    ) == components
    assert _initial_oxide_map_from_values(
        {**components, "mass_loss_wt_pct": 10}, declared_map=True
    ) == components


def test_declared_map_skips_sample_codes_flags_and_condition_fields() -> None:
    components = {"SiO2": Decimal("50"), "MgO": Decimal("40")}

    assert _initial_oxide_map_from_values(
        {
            "composition_wt_pct": components,
            "MS123": Decimal("10"),
            "is_initial": Decimal("1"),
            "T_C": Decimal("0"),
        }
    ) == components


def test_bulk_observation_withholds_unsupported_composition_with_key_reason(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(
        tmp_path,
        {
            "schema_version": "literature_extract.v1",
            "source_id": "fixture-source",
            "source": {
                "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
                "doi": "10.1234/FIXTURE",
            },
            "extraction": {
                "method": "unit_test",
                "date": "2026-09-19",
                "worker": "pytest",
            },
            "review_status": "draft",
            "fidelity_samples": [],
            "species": {
                "MLS-1A": {
                    "observations": [
                        {
                            "observation_id": "unsupported-composition",
                            "type": "measurement",
                            "locator": {"table": "1", "page": 3},
                            "phase": "mixed_simulants",
                            "units": "%",
                            "values": {
                                "quantity": "mass_loss_fraction",
                                "method_class": "measured_direct",
                                "mass_loss_fraction": 0.2,
                                "composition_wt_pct": {
                                    "SiO2": 50,
                                    "MgO": 40,
                                    "XxO": 10,
                                },
                            },
                        }
                    ]
                }
            },
        },
    )
    result = migrate(root, write=False)
    observation = result.observations[
        "fixture-source::unsupported-composition"
    ]

    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    assert "XxO" in observation.identity.composition.reason


def test_nonbulk_observation_identity_keeps_unknown_component_reason(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(
        tmp_path,
        {
            "schema_version": "literature_extract.v1",
            "source_id": "fixture-source",
            "source": {
                "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
                "doi": "10.1234/FIXTURE",
            },
            "extraction": {
                "method": "unit_test",
                "date": "2026-09-19",
                "worker": "pytest",
            },
            "review_status": "draft",
            "fidelity_samples": [],
            "species": {
                "SiO2": {
                    "observations": [
                        {
                            "observation_id": "nonbulk-unsupported-composition",
                            "type": "measurement",
                            "locator": {"table": "1", "page": 3},
                            "phase": "silicate_melt",
                            "units": "dimensionless",
                            "values": {
                                "quantity": "activity",
                                "method_class": "measured_direct",
                                "activity": 0.2,
                                "T_K": 1400,
                                "composition_wt_pct": {
                                    "SiO2": 50,
                                    "MgO": 40,
                                    "XxO": 10,
                                },
                            },
                        }
                    ]
                }
            },
        },
    )
    result = migrate(root, write=False)
    observation = result.observations[
        "fixture-source::nonbulk-unsupported-composition"
    ]

    assert observation.identity.composition is not None
    assert observation.identity.composition.is_unknown
    assert "XxO" in observation.identity.composition.reason


def test_feot_conversion_notice_is_pinned_to_observation_locator(tmp_path: Path) -> None:
    root = _write_min_tree(
        tmp_path,
        {
            "schema_version": "literature_extract.v1",
            "source_id": "fixture-source",
            "source": {
                "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
                "doi": "10.1234/FIXTURE",
            },
            "extraction": {
                "method": "unit_test",
                "date": "2026-09-19",
                "worker": "pytest",
            },
            "review_status": "draft",
            "fidelity_samples": [],
            "species": {
                "SiO2": {
                    "observations": [
                        {
                            "observation_id": "feot-notice",
                            "type": "measurement",
                            "locator": {"table": "3", "page": 9},
                            "phase": "silicate_melt",
                            "units": "%",
                            "values": {
                                "quantity": "mass_loss_fraction",
                                "method_class": "measured_direct",
                                "mass_loss_fraction": 0.2,
                                "T_K": 1400,
                                "composition_wt_pct": {
                                    "SiO2": 50,
                                    "MgO": 40,
                                    "FeOT": 10,
                                },
                            },
                        }
                    ]
                }
            },
        },
    )
    observation = migrate(root, write=False).observations[
        "fixture-source::feot-notice"
    ]

    assert observation.identity.composition is not None
    assert observation.identity.composition.is_value
    assert "FeO" in dict(observation.identity.composition.value.components)
    notice, = (
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
    )
    assert notice.reason == (
        "FeOT (total iron) treated as FeO for conversion; printed key FeOT"
    )
    assert notice.affected_quantities == (observation.identity.quantity.value,)
    assert notice.origin == observation.observation_id
    assert observation.locator is not None
    assert observation.locator.table == "3"
    assert observation.locator.page == 9


def test_flat_point_composition_ignores_temperature_and_loss_fields() -> None:
    assert _initial_oxide_map_from_values(
        {
            "points": [
                {
                    "composition_wt_pct": {
                        "SiO2": Decimal("50"),
                        "MgO": Decimal("40"),
                    },
                    "T_C": Decimal("0"),
                    "mass_loss_pct": Decimal("0"),
                }
            ]
        }
    ) == {"SiO2": Decimal("50"), "MgO": Decimal("40")}


def test_mass_percent_composition_keeps_printed_map_and_derives_moles() -> None:
    raw = {
        "initial_composition": {
            "locator": {"table": "2", "page": 14},
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["MgO", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == {"SiO2": "60", "MgO": "40"}
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert sample.initial_composition.inference is not None
    assert sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"
    assert dict(sample.initial_composition.state.value.components) == dict(
        wt_pct_to_mole_fraction({"SiO2": Decimal("60"), "MgO": Decimal("40")}).components
    )


def test_printed_wt_percent_replaces_a_stale_pretyped_initial_composition() -> None:
    printed = {"SiO2": "50", "FeOT": "30", "SO3": "20"}
    raw = {
        "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
        "printed_composition": {
            "state": {"tag": "value", "value": printed},
            "locator": {"note": "Printed starting material composition"},
        },
        "initial_composition": {
            "basis": "printed_oxides",
            "amount_basis": "mole_fraction",
            "components": [["SiO2", "1"]],
        },
    }

    sample = _sample_from_plain(raw)
    expected = wt_pct_to_mole_fraction(
        {key: Decimal(value) for key, value in printed.items()}
    )

    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_value
    assert sample.initial_composition.inference is not None
    assert sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"
    assert dict(sample.initial_composition.state.value.components) == dict(expected.components)


@pytest.mark.parametrize("duplicate_name", ("SiO2", " SiO2 "))
def test_duplicate_mass_percent_pairs_are_refused_without_collapsing(
    duplicate_name: str,
) -> None:
    sample = _sample_from_plain(
        {
            "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
            "printed_composition": {
                "locator": {"note": "Printed starting material composition"},
                "state": {
                    "tag": "value",
                    "value": {
                        "basis": "printed_oxides",
                        "amount_basis": "mass_percent",
                        "components": [
                            ["SiO2", "60"],
                            [duplicate_name, "10"],
                            ["MgO", "30"],
                        ],
                    },
                }
            }
        }
    )

    assert sample.initial_composition is not None
    assert not sample.initial_composition.state.is_value
    assert "duplicate component key(s): SiO2" in sample.initial_composition.state.reason


@pytest.mark.parametrize(
    ("source", "expected_experiments"),
    (
        ("britt-2019-asteroid-simulants", 6),
        ("hendrix-2024-reactivity-reduced-simulants", 3),
    ),
)
def test_real_pretyped_experiment_initials_match_printed_composition_owner(
    source: str, expected_experiments: int
) -> None:
    migrator = Migrator()
    migrator._migrate_extract(REPO_ROOT / "data/literature/extracts" / f"{source}.yaml")
    experiments = [
        experiment
        for experiment in migrator.result.experiments.values()
        if experiment.sample.printed_composition is not None
        and experiment.sample.initial_composition is not None
        and experiment.sample.printed_composition.state.is_value
    ]

    assert len(experiments) == expected_experiments
    for experiment in experiments:
        printed = experiment.sample.printed_composition.state.value
        initial = experiment.sample.initial_composition
        assert initial is not None and initial.state.is_value
        expected = wt_pct_to_mole_fraction(
            {key: as_decimal(value) for key, value in printed.items()}
        )
        assert dict(initial.state.value.components) == pytest.approx(
            dict(expected.components), abs=Decimal("1e-12")
        ), experiment.experiment_id


def test_swept_source_maps_refused_to_converted_and_populate_identity() -> None:
    migrator = Migrator()
    for _label, source, _field, _species, _count in _SWEPT_COMPOSITION_MAPS:
        migrator._migrate_extract(REPO_ROOT / "data/literature/extracts" / f"{source}.yaml")

    for label, source, field, species, expected_count in _SWEPT_COMPOSITION_MAPS:
        doc = yaml.safe_load(
            (REPO_ROOT / "data/literature/extracts" / f"{source}.yaml").read_text()
        )
        converted = 0
        refused = 0
        feot_derivations = 0
        for body in (doc.get("species") or {}).values():
            for raw_observation in (body.get("observations") or []):
                values = raw_observation.get("values") or {}
                raw_composition = values.get(field)
                if not isinstance(raw_composition, dict) or species not in raw_composition:
                    continue
                refusal: list[str] = []
                wt = _initial_oxide_map_from_values(
                    {field: raw_composition}, refusal_reason=refusal
                )
                if wt is None:
                    refused += 1
                    continue
                expected = wt_pct_to_mole_fraction(wt)
                observation_prefix = (
                    f"{source}::{raw_observation.get('observation_id')}"
                )
                rows = [
                    observation
                    for observation_id, observation in migrator.result.observations.items()
                    if observation_id == observation_prefix
                    or observation_id.startswith(f"{observation_prefix}::")
                ]
                assert rows, observation_prefix
                for observation in rows:
                    composition = observation.identity.composition
                    assert composition is not None and composition.is_value
                    assert composition.value == expected, observation.observation_id
                    if "FeOT" in raw_composition:
                        assert observation.identity.quantity.is_unknown
                        assert not any(
                            notice.kind is NoticeKind.TOTAL_IRON_REPORTED_AS_FEO
                            for notice in observation.notices
                        )
                        printed_composition = (observation.point_conditions or {}).get(
                            "composition"
                        )
                        assert printed_composition is not None
                        assert printed_composition.inference is not None
                        assert (
                            "FeOT (total iron) treated as FeO; printed key FeOT"
                            in printed_composition.inference.inputs
                        )
                        feot_derivations += 1
                converted += 1
        assert (refused, converted) == (0, expected_count), label
        if label == "Britt":
            assert feot_derivations == expected_count


def test_untyped_printed_composition_note_does_not_create_initial_composition() -> None:
    raw = {
        "printed_composition": {
            "locator": {
                "table": "2",
                "page": 14,
                "note": "Printed starting material composition",
            },
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["MgO", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == {"SiO2": "60", "MgO": "40"}
    assert sample.initial_composition is None


def test_sample_catalogue_composition_is_typed_proxy_not_printed() -> None:
    values = {
        "composition_from_sample_catalog": True,
        "composition_source": (
            "Lunar Sample Compendium entry 10017. "
            "Selection rule: first complete named whole-sample analysis."
        ),
        "sample_oxide_composition_wt_pct": {"SiO2": 50, "CaO": 50},
    }

    located = _catalogue_composition_located_from_values(values, None)
    assert located is not None
    composition = located.state.value
    assert composition.basis == "sample_catalog_proxy"
    assert composition.proxy_flag == "composition_from_sample_catalog"
    assert composition.proxy_source.startswith("Lunar Sample Compendium")
    assert composition.analysis_selection_rule == (
        "first complete named whole-sample analysis"
    )

    sample = sample_from_equipment({}, values=values)
    assert sample.printed_composition is None
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.value.proxy_flag == (
        "composition_from_sample_catalog"
    )


def _located_initial_composition(
    components: dict[str, Decimal | str],
    *,
    amount_basis: AmountBasis,
    proxy_flag: str | None = None,
    proxy_source: str | None = None,
    analysis_selection_rule: str | None = None,
):
    payload = {
        "basis": "test_initial_composition",
        "amount_basis": amount_basis.value,
        "components": [[oxide, str(amount)] for oxide, amount in components.items()],
    }
    if proxy_flag is not None:
        payload["proxy_flag"] = proxy_flag
    if proxy_source is not None:
        payload["proxy_source"] = proxy_source
    if analysis_selection_rule is not None:
        payload["analysis_selection_rule"] = analysis_selection_rule
    return migrate_module._composition_located_from_plain(payload)


def _merge_sample_initials(existing_initial, incoming_initial):
    experiment = tabulation_experiment("initial-authority-merge")
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample, initial_composition=existing_initial
        ),
    )
    merged = migrate_module._merge_experiment_lab_params(
        experiment,
        replace(experiment.sample, initial_composition=incoming_initial),
        experiment.apparatus,
        experiment.pressure_environment,
    )
    return merged.sample.initial_composition


def test_equivalent_initial_inventories_keep_higher_authority_owner() -> None:
    starting_glass = {
        "composition_material_state": "starting_glass",
        "composition_wt_pct": {"SiO2": 60, "MgO": 40},
    }
    catalogue = {
        "composition_from_sample_catalog": True,
        "composition_source": (
            "Lunar Sample Compendium entry 10017. "
            "Selection rule: first complete named whole-sample analysis."
        ),
        "sample_oxide_composition_wt_pct": {"SiO2": 60, "MgO": 40},
    }
    existing_initial = sample_from_equipment(
        {}, values=starting_glass
    ).initial_composition
    incoming_initial = sample_from_equipment({}, values=catalogue).initial_composition
    assert existing_initial is not None and incoming_initial is not None
    assert not migrate_module._initial_compositions_conflict(
        existing_initial, incoming_initial
    )
    mass_percent_initial = replace(
        existing_initial,
        state=replace(
            existing_initial.state,
            value=Composition(
                basis="different_engine_label",
                components=(("SiO2", Decimal("60")), ("MgO", Decimal("40"))),
                amount_basis=AmountBasis.MASS_PERCENT,
            ),
        ),
    )
    assert not migrate_module._initial_compositions_conflict(
        existing_initial, mass_percent_initial
    )
    mole_inventory_initial = replace(
        existing_initial,
        state=replace(
            existing_initial.state,
            value=Composition(
                basis="molar_inventory",
                components=tuple(
                    (oxide, amount * Decimal("10"))
                    for oxide, amount in existing_initial.state.value.components
                ),
                amount_basis=AmountBasis.MOL_INVENTORY,
            ),
        ),
    )
    assert not migrate_module._initial_compositions_conflict(
        existing_initial, mole_inventory_initial
    )

    experiment = tabulation_experiment("equivalent-owner-initials")
    experiment = replace(
        experiment,
        sample=replace(
            experiment.sample, initial_composition=existing_initial
        ),
    )
    migrator = Migrator()
    migrator.result.experiments[experiment.experiment_id] = experiment

    merged = migrator._ensure_experiment(
        work_id=experiment.work_id,
        experiment_id=experiment.experiment_id,
        locator=experiment.locator,
        method=experiment.method,
        equipment={},
        values=catalogue,
    )

    initial = merged.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert initial == existing_initial
    assert not any(
        "initial_composition_conflict" in issue.detail
        for issue in migrator.result.registry_issues
    )

    measured_initial = replace(
        existing_initial,
        state=replace(
            existing_initial.state,
            value=replace(
                existing_initial.state.value,
                analysis_selection_rule=(
                    "directly measured starting-material analysis"
                ),
            ),
        ),
    )
    assert _merge_sample_initials(incoming_initial, existing_initial) == incoming_initial
    assert _merge_sample_initials(existing_initial, measured_initial) == measured_initial

    initial_charge = _located_initial_composition(
        {"SiO2": "0.5015472170832676561113000772", "MgO": "0.4984527829167323438886999228"},
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag=INITIAL_CHARGE_ONLY_PROXY_FLAG,
    )
    assert _merge_sample_initials(incoming_initial, initial_charge) == initial_charge

    generic_proxy = _located_initial_composition(
        {"SiO2": "0.5015472170832676561113000772", "MgO": "0.4984527829167323438886999228"},
        amount_basis=AmountBasis.MOLE_FRACTION,
        proxy_flag="model_proxy",
        proxy_source="model estimate",
    )
    assert _merge_sample_initials(existing_initial, generic_proxy) == existing_initial

    tied_initial = replace(
        existing_initial,
        state=replace(
            existing_initial.state,
            value=replace(
                existing_initial.state.value,
                proxy_source="extra provenance without higher authority",
            ),
        ),
    )
    assert _merge_sample_initials(existing_initial, tied_initial) == existing_initial


def test_absolute_mole_inventories_compare_amount_and_prefer_absolute() -> None:
    fraction = _located_initial_composition(
        {"SiO2": "0.6", "MgO": "0.4"},
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    same_absolute_inventory = _located_initial_composition(
        {"SiO2": "0.6", "MgO": "0.4"},
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    tenfold_absolute_inventory = _located_initial_composition(
        {"SiO2": "6", "MgO": "4"},
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    assert not migrate_module._initial_compositions_conflict(
        fraction, tenfold_absolute_inventory
    )
    assert _merge_sample_initials(fraction, tenfold_absolute_inventory) == (
        tenfold_absolute_inventory
    )
    assert migrate_module._initial_compositions_conflict(
        same_absolute_inventory, tenfold_absolute_inventory
    )

    rounded_absolute_inventory = _located_initial_composition(
        {"SiO2": "0.6000000000002", "MgO": "0.4"},
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    assert not migrate_module._initial_compositions_conflict(
        same_absolute_inventory, rounded_absolute_inventory
    )


def test_initial_composition_component_keys_must_match() -> None:
    omitted_trace = _located_initial_composition(
        {"SiO2": "1"}, amount_basis=AmountBasis.MOLE_FRACTION
    )
    stated_trace = _located_initial_composition(
        {"SiO2": "1", "Na2O": "1e-13"},
        amount_basis=AmountBasis.MOLE_FRACTION,
    )
    stated_zero = _located_initial_composition(
        {"SiO2": "1", "Na2O": "0"},
        amount_basis=AmountBasis.MOLE_FRACTION,
    )

    assert migrate_module._initial_compositions_conflict(
        omitted_trace, stated_trace
    )
    assert not migrate_module._initial_compositions_conflict(
        stated_zero, stated_trace
    )


def test_conflicting_owner_selected_initials_are_withheld_with_migration_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_initial = sample_from_equipment(
        {},
        values={
            "composition_from_sample_catalog": True,
            "composition_source": "first catalogue source",
            "sample_oxide_composition_wt_pct": {"SiO2": 60, "MgO": 40},
        },
    ).initial_composition
    incoming_initial = sample_from_equipment(
        {},
        values={
            "composition_from_sample_catalog": True,
            "composition_source": "second catalogue source",
            "sample_oxide_composition_wt_pct": {"SiO2": 55, "MgO": 45},
        },
    ).initial_composition
    assert old_initial is not None and incoming_initial is not None
    basis_conflict = replace(
        old_initial,
        state=replace(
            old_initial.state,
            value=replace(old_initial.state.value, basis="other_engine_basis"),
        ),
    )
    # Engine-basis labels are metadata; the physical maps are identical.
    assert not migrate_module._initial_compositions_conflict(
        old_initial, basis_conflict
    )

    experiment_id = "catalogue-initial-conflict"
    registry_printed = migrate_module._located_printed_and_initial(
        {"SiO2": Decimal("70"), "MgO": Decimal("30")}, None
    )[0]
    row_printed = migrate_module._located_printed_and_initial(
        {"SiO2": Decimal("40"), "MgO": Decimal("60")}, None
    )[0]
    assert registry_printed is not None and row_printed is not None
    existing = tabulation_experiment(experiment_id)
    existing = replace(
        existing,
        sample=replace(
            existing.sample,
            initial_composition=old_initial,
            printed_composition=registry_printed,
        ),
    )
    migrator = Migrator()
    migrator.result.experiments[existing.experiment_id] = existing
    migrator._registry_printed_composition_experiments.add(experiment_id)
    current_sample = {
        "value": replace(
            existing.sample,
            initial_composition=incoming_initial,
            printed_composition=row_printed,
        )
    }
    monkeypatch.setattr(
        migrate_module,
        "sample_from_equipment",
        lambda *args, **kwargs: current_sample["value"],
    )
    merged = migrator._ensure_experiment(
        work_id=existing.work_id,
        experiment_id=experiment_id,
        locator=existing.locator,
        method=existing.method,
        equipment={},
    )

    assert merged.sample.initial_composition is None
    conflict = next(
        issue
        for issue in migrator.result.registry_issues
        if "initial_composition_conflict" in issue.detail
    )
    assert conflict.path == (
        "experiment[catalogue-initial-conflict].sample.initial_composition"
    )
    assert conflict.reason is RefusalReason.INVALID_SOURCE
    assert "first catalogue source" in conflict.detail
    assert "second catalogue source" in conflict.detail
    assert "basis=" in conflict.detail
    assert "amount_basis=" in conflict.detail
    assert "proxy_flag='composition_from_sample_catalog'" in conflict.detail

    current_sample["value"] = replace(
        existing.sample,
        initial_composition=old_initial,
        printed_composition=row_printed,
    )
    later = migrator._ensure_experiment(
        work_id=existing.work_id,
        experiment_id=experiment_id,
        locator=existing.locator,
        method=existing.method,
        equipment={},
    )
    assert later.sample.initial_composition is None
    assert len(
        [
            issue
            for issue in migrator.result.registry_issues
            if "initial_composition_conflict" in issue.detail
        ]
    ) == 1


def test_bischof_measured_and_nominal_initials_remain_conflicted() -> None:
    source = REPO_ROOT / "data/literature/extracts/kems-137-bischof-2023.yaml"
    migrator = Migrator()
    migrator._migrate_extract(source)
    migrator.finalize()

    expected_runs = {"an-di-1-low", "an-di-2-low", "an-di-3-high"}
    conflicts = [
        issue
        for issue in migrator.result.registry_issues
        if "initial_composition_conflict" in issue.detail
    ]
    assert len(conflicts) == 3
    assert {
        run
        for run in expected_runs
        if any(f"::{run}]" in issue.path for issue in conflicts)
    } == expected_runs
    assert all(issue.reason is RefusalReason.INVALID_SOURCE for issue in conflicts)
    assert all(
        experiment.sample.initial_composition is None
        for experiment_id, experiment in migrator.result.experiments.items()
        if any(experiment_id.endswith(f"::{run}") for run in expected_runs)
    )


def test_ambiguous_mass_percent_initial_preserves_print_and_withholds_canonical() -> None:
    raw = {
        "initial_composition": {
            "locator": {"table": "2", "page": 14},
            "state": {
                "tag": "value",
                "value": {
                    "basis": "printed_oxides",
                    "amount_basis": "mass_percent",
                    "components": [["SiO2", "60"], ["Cl", "40"]],
                },
            },
        }
    }

    sample = _sample_from_plain(raw)

    assert sample.printed_composition is not None
    assert sample.printed_composition.state.value == raw["initial_composition"]["state"]["value"]
    assert sample.initial_composition is not None
    assert sample.initial_composition.state.is_unknown


def _iter_store_observations() -> list[dict]:
    rows: list[dict] = []
    for folder in _OBS_ROOTS:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.yaml")):
            raw = path.read_bytes()
            if b"mass_loss_fraction" not in raw and b"yield_fraction" not in raw and b"o2_yield" not in raw:
                continue
            doc = yaml.safe_load(raw.decode("utf-8"))
            if not isinstance(doc, dict):
                continue
            for obs in doc.get("observations") or []:
                if isinstance(obs, dict):
                    rows.append(obs)
    return rows


def _quantity(obs: dict) -> str | None:
    ident = obs.get("identity") or {}
    q = ident.get("quantity") or {}
    if isinstance(q, dict) and q.get("tag") == "value":
        return q.get("value")
    return None


def _formula(obs: dict) -> str:
    ident = obs.get("identity") or {}
    species = ident.get("species") or {}
    return str(species.get("formula") or "")


def _state_value(payload: object) -> object:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state") if "state" in payload else payload
    if not isinstance(state, dict) or state.get("tag") != "value":
        return None
    return state.get("value")


def _printed_wt_map(obs: dict) -> dict[str, Decimal]:
    pc = obs.get("point_conditions") or {}
    raw = _state_value(pc.get("printed_composition"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Decimal] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = as_decimal(value)
        except (TypeError, ValueError):
            continue
    return out


def _identity_composition_components(obs: dict) -> dict[str, Decimal]:
    ident = obs.get("identity") or {}
    raw = _state_value(ident.get("composition"))
    if not isinstance(raw, dict):
        return {}
    components = raw.get("components") or ()
    out: dict[str, Decimal] = {}
    for item in components:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out[str(item[0])] = as_decimal(item[1])
    return out


def _markova_vi_extract() -> dict:
    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-19", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "SiO2": {
                "observations": [
                    {
                        "observation_id": "markova_1984_table2_residual_melt_VI_lherzolite",
                        "type": "rate_series",
                        "locator": {"table": "2", "published_page": 510, "pdf_page_index": 2},
                        "phase": "residual_silicate_melt_composition_VI_lherzolite",
                        "regime": "kems_effusion",
                        "units": "wt percent as published",
                        "values": {
                            "quantity": "residual_melt_composition_vs_T_and_mass_loss",
                            "method_class": "measured_direct",
                            "composition_id": "VI",
                            "composition_name": "lherzolite",
                            "points": [
                                {
                                    "T_C": 0,
                                    "mass_loss_pct": 0.0,
                                    "SiO2": 44.02,
                                    "Al2O3": 4.56,
                                    "FeO": 8.04,
                                    "MgO": 36.44,
                                    "CaO": 6.95,
                                    "T_C_is_initial_composition": True,
                                },
                                {
                                    "T_C": 2207,
                                    "mass_loss_pct": 95.51,
                                    "SiO2": 0.0,
                                    "Al2O3": 79.28,
                                    "FeO": 0.08,
                                    "MgO": 0.0,
                                    "CaO": 21.64,
                                    "T_K": 2480.15,
                                    "T_C_is_initial_composition": False,
                                },
                            ],
                        },
                    }
                ]
            }
        },
    }


def test_migrate_lifts_markova_shaped_oxide_map(tmp_path: Path) -> None:
    root = _write_min_tree(tmp_path, _markova_vi_extract())
    result = migrate(root, write=False)
    t0, later = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith(
            "fixture-source::markova_1984_table2_residual_melt_VI_lherzolite::"
        )
    ]
    assert t0.identity.species.formula == "unknown"
    printed = t0.point_conditions["printed_composition"].state.value
    assert as_decimal(printed["SiO2"]) == as_decimal("44.02")
    assert as_decimal(printed["MgO"]) == as_decimal("36.44")
    ident = t0.identity.composition
    assert ident is not None and ident.is_value
    assert ident.value.amount_basis is AmountBasis.MOLE_FRACTION
    experiment = result.experiments[t0.experiment_id]
    assert experiment.sample.printed_composition is not None
    assert as_decimal(experiment.sample.printed_composition.state.value["SiO2"]) == as_decimal(
        "44.02"
    )
    assert experiment.sample.initial_composition is not None
    assert experiment.sample.initial_composition.inference is not None
    assert experiment.sample.initial_composition.inference.relation == "wt_pct_to_mole_fraction"
    later_printed = later.point_conditions["printed_composition"].state.value
    assert as_decimal(later_printed["SiO2"]) == as_decimal("0.0")
    assert later_printed != printed


def test_migrate_keeps_ilmenite_and_drops_sample_code(tmp_path: Path) -> None:
    extract = {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-19", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "MLS-1A": {
                "observations": [
                    {
                        "observation_id": "table1",
                        "type": "rate_series",
                        "locator": {"table": "1", "page": 3},
                        "phase": "mixed_simulants",
                        "regime": "solar_fresnel_vacuum_pyrolysis",
                        "units": "%",
                        "values": {
                            "quantity": "vacuum_pyrolysis_experiment_summary",
                            "method_class": "measured_direct",
                            "tests": [
                                {
                                    "test": "2b",
                                    "sample": "FeTiO3",
                                    "mass_loss_pct": 16.0,
                                    "Tmax_C": 800,
                                },
                                {
                                    "test": "11",
                                    "sample": "MLS-1a",
                                    "mass_loss_pct": 10.1,
                                    "Tmax_C": 1474,
                                },
                            ],
                        },
                    }
                ]
            }
        },
    }
    root = _write_min_tree(tmp_path, extract)
    result = migrate(root, write=False)
    ilmenite, mls = [
        obs
        for oid, obs in result.observations.items()
        if oid.startswith("fixture-source::table1::")
    ]
    assert ilmenite.identity.species.formula == "FeTiO3"
    assert mls.identity.species.formula == "unknown"


def test_markova_vi_lherzolite_carries_printed_five_oxide_composition() -> None:
    """Table 2 T=0 recast is a 5-oxide lherzolite, not species SiO2.

    Fail-proof: the current store files the series under SiO2 with
    identity.composition unknown, so this is red until the oxide map is
    lifted onto printed_composition / mole-fraction Composition.
    """

    store = _OBS_ROOTS[0] / "kems-026-markova-1984.yaml"
    assert store.is_file(), "migrated Markova extract-v2 is required"
    doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    rows = [
        obs
        for obs in (doc.get("observations") or [])
        if str(obs.get("observation_id") or "").startswith(_MARKOVA_VI_PREFIX)
        and _quantity(obs) == "mass_loss_fraction"
    ]
    assert rows, "Markova VI residual-melt mass-loss points missing from store"
    t0 = next(
        (obs for obs in rows if str(obs.get("observation_id") or "").endswith("::point:0")),
        None,
    )
    assert t0 is not None, "Markova VI T=0 point missing"
    assert _formula(t0) != "SiO2", (
        "bulk lherzolite mass-loss is still asserted as species SiO2"
    )
    printed = _printed_wt_map(t0)
    assert printed, "T=0 printed wt% map is missing from point_conditions"
    assert set(printed) >= {"SiO2", "Al2O3", "FeO", "MgO", "CaO"}
    assert as_decimal(printed["SiO2"]) == as_decimal("44.02")
    assert as_decimal(printed["MgO"]) == as_decimal("36.44")
    total = sum(printed.values())
    assert as_decimal("99.5") <= total <= as_decimal("100.5"), total
    mole = _identity_composition_components(t0)
    assert mole, "initial mole-fraction Composition is missing on identity"
    converted = wt_pct_to_mole_fraction(printed)
    converted_map = dict(converted.components)
    for oxide, x in mole.items():
        assert abs(x - converted_map[oxide]) < as_decimal("1e-12"), oxide
    m_sio2 = oxide_molar_mass("SiO2")
    m_mgo = oxide_molar_mass("MgO")
    assert m_sio2 == as_decimal(str(ATOMIC_WEIGHTS_G_PER_MOL["Si"] + 2 * ATOMIC_WEIGHTS_G_PER_MOL["O"]))
    expected_ratio = (printed["SiO2"] / m_sio2) / (printed["MgO"] / m_mgo)
    got_ratio = mole["SiO2"] / mole["MgO"]
    assert abs(got_ratio - expected_ratio) < as_decimal("1e-12")
    later = max(
        rows,
        key=lambda obs: Decimal(
            re.search(
                r"::T=([0-9.]+):",
                str(obs.get("observation_id") or ""),
            ).group(1)
        )
        if re.search(r"::T=([0-9.]+):", str(obs.get("observation_id") or ""))
        else Decimal("-1"),
    )
    assert later is not t0
    later_printed = _printed_wt_map(later)
    assert later_printed
    assert later_printed != printed, "ramp residual composition was collapsed into one map"


def test_markova_1984_mass_loss_identity_uses_its_own_panel_initial_charge() -> None:
    """Cumulative mass loss uses each panel's own T=0 charge as X0."""

    store = _OBS_ROOTS[0] / "kems-026-markova-1984.yaml"
    doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    panels = {
        "VI_lherzolite": {"SiO2": "44.02", "MgO": "36.44"},
        "V_alumina_basalt": {"SiO2": "52.32", "Al2O3": "19.13"},
    }

    for panel, signature in panels.items():
        prefix = f"kems-026-markova-1984::markova_1984_table2_residual_melt_{panel}"
        rows = [
            obs
            for obs in (doc.get("observations") or [])
            if str(obs.get("observation_id") or "").startswith(prefix)
            and _quantity(obs) == "mass_loss_fraction"
        ]
        assert len(rows) == 12, (panel, len(rows))
        t0 = next(
            obs
            for obs in rows
            if str(obs.get("observation_id") or "").endswith("::point:0")
        )
        charge = _printed_wt_map(t0)
        for oxide, expected in signature.items():
            assert charge[oxide] == as_decimal(expected), (panel, oxide, charge)
        expected_x0 = dict(wt_pct_to_mole_fraction(charge).components)
        for row in rows:
            identity_x0 = _identity_composition_components(row)
            assert identity_x0, (panel, row.get("observation_id"))
            assert identity_x0.keys() == expected_x0.keys(), (panel, row.get("observation_id"))
            assert all(
                abs(identity_x0[oxide] - value) < as_decimal("1e-12")
                for oxide, value in expected_x0.items()
            ), (panel, row.get("observation_id"), identity_x0)


def test_markova_1983_rows_use_each_points_own_printed_composition(
    tmp_path: Path,
) -> None:
    source_path = (
        REPO_ROOT
        / "data"
        / "literature"
        / "extracts"
        / "kems-025-markova-1983.yaml"
    )
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    expected_by_temperature: dict[Decimal, dict[str, Decimal]] = {}
    for block in (source.get("species") or {}).values():
        for source_observation in block.get("observations") or []:
            values = source_observation.get("values") or {}
            for point in values.get("points") or []:
                printed = point.get("composition_wt_pct")
                if not isinstance(printed, dict):
                    continue
                temperature_k = as_decimal(point["T_C"]) + as_decimal("273.15")
                expected_by_temperature[temperature_k] = {
                    str(oxide): as_decimal(value)
                    for oxide, value in printed.items()
                }
    assert len(expected_by_temperature) == 25

    result = _migrate_real_extract(tmp_path, source_path.name, write=False)
    rows = [
        to_plain(observation)
        for observation in result.observations.values()
        if observation.point_conditions
        and "printed_composition" in observation.point_conditions
    ]
    assert len(rows) == 25
    assert {
        row["identity"]["quantity"]["value"] for row in rows
    } == {"p_partial"}

    mismatches: list[str] = []
    for row in rows:
        identity = row["identity"]
        temperature_k = as_decimal(identity["temperature_K"]["value"])
        expected_wt = expected_by_temperature[temperature_k]
        printed_wt = _printed_wt_map(row)
        if printed_wt != expected_wt:
            mismatches.append(f"{row['observation_id']}: printed map differs from source")
            continue
        expected_x = dict(wt_pct_to_mole_fraction(expected_wt).components)
        actual_x = _identity_composition_components(row)
        if actual_x.keys() != expected_x.keys() or any(
            abs(actual_x[oxide] - value) >= as_decimal("1e-12")
            for oxide, value in expected_x.items()
        ):
            mismatches.append(
                f"{row['observation_id']}: identity does not match its printed map"
            )
    assert not mismatches, f"{len(mismatches)} composition mismatches: {mismatches}"


def test_hashimoto_residue_cells_keep_full_printed_point_compositions(
    tmp_path: Path,
) -> None:
    from simulator.battery.enums import Quantity

    source_id = "kems-015-hashimoto-1983"
    result = _migrate_real_extract(
        tmp_path,
        f"{source_id}.yaml",
        write=False,
    )
    # Hashimoto 1983, Table 3, p. 116. The two C3-(2) run observations at
    # 1700 and 1800 C are the explicitly labelled run rows; the other two
    # pins cover the remaining printed temperatures.
    expected_by_row = {
        (
            "table3_residue_composition_series_quoted__run_17c3-2::"
            "T=1973.15:row=c3-(2):"
        ): {
            "SiO2": Decimal("39.09"),
            "Al2O3": Decimal("3.57"),
            "FeO": Decimal("27.92"),
            "MgO": Decimal("26.32"),
            "CaO": Decimal("3.09"),
        },
        (
            "table3_residue_composition_series_quoted__run_18c3-2::"
            "T=2073.15:row=c3-(2):"
        ): {
            "SiO2": Decimal("45.30"),
            "Al2O3": Decimal("4.33"),
            "FeO": Decimal("12.87"),
            "MgO": Decimal("33.57"),
            "CaO": Decimal("3.93"),
        },
        "table3_residue_composition_series::T=2173.15:row=19b2:": {
            "SiO2": Decimal("37.94"),
            "Al2O3": Decimal("3.55"),
            "FeO": Decimal("29.14"),
            "MgO": Decimal("26.09"),
            "CaO": Decimal("3.29"),
        },
        "table3_residue_composition_series::T=2273.15:row=20b2:": {
            "SiO2": Decimal("40.87"),
            "Al2O3": Decimal("4.14"),
            "FeO": Decimal("20.46"),
            "MgO": Decimal("30.81"),
            "CaO": Decimal("3.72"),
        },
    }
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
        and observation.identity.quantity.is_value
        and observation.identity.quantity.value
        is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and observation.identity.species.formula == "SiO2"
    ]
    for locator, expected in expected_by_row.items():
        matching = [row for row in rows if locator in row.observation_id]
        assert len(matching) == 1, locator
        observation = matching[0]
        printed = observation.point_conditions["printed_composition"]
        assert printed.state.is_value
        actual = {
            oxide: as_decimal(str(value))
            for oxide, value in printed.state.value.items()
        }
        assert actual == expected
        starting = result.experiments[
            observation.experiment_id
        ].sample.printed_composition.state.value
        assert actual != {
            oxide: as_decimal(str(value)) for oxide, value in starting.items()
        }


def test_markova_table2_migrates_both_quantities_and_printed_charge(tmp_path: Path) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "kems-026-markova-1984.yaml",
        use_repository_index_row=True,
    )
    prefix = "kems-026-markova-1984::markova_1984_table2_residual_melt_"
    rows = {
        observation_id: to_plain(observation)
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(prefix)
    }
    mass_loss_rows = {
        observation_id: row
        for observation_id, row in rows.items()
        if _quantity(row) == "mass_loss_fraction"
    }
    assert len(mass_loss_rows) == 24
    assert {
        row["identity"]["species"]["formula"] for row in mass_loss_rows.values()
    } == {"unknown"}
    assert sum(
        row["admission"]["status"] == "admitted"
        for row in mass_loss_rows.values()
    ) == 22
    zero_rows = [
        row
        for row in mass_loss_rows.values()
        if Decimal(row["value"]["point"]) == Decimal("0")
    ]
    assert len(zero_rows) == 2
    for row in zero_rows:
        assert row["admission"]["status"] == "pending"
        assert row["identity"]["temperature_K"]["tag"] == "unknown"
        assert "T_range_K" in row["identity"]["temperature_K"]["reason"]
    assert {
        Decimal(row["value"]["point"])
        for row in mass_loss_rows.values()
    } == {
        Decimal(value)
        for value in (
            "0",
            "0.0146", "0.0416", "0.0974", "0.218", "0.3835", "0.6521",
            "0.8605", "0.8925", "0.9181", "0.9551", "0.9834", "0.0117",
            "0.0439", "0.1441", "0.3717", "0.5353", "0.6601", "0.6935",
            "0.7133", "0.7619", "0.8368", "0.9799",
        )
    }

    residue = {
        observation_id: row
        for observation_id, row in rows.items()
        if _quantity(row) == "residue_component_composition"
    }
    assert len(residue) == 110
    printed_cells = {
        ("VI", "1748.15", "MgO"): Decimal("36.92"),
        ("VI", "2156.15", "SiO2"): Decimal("13.6"),
        ("VI", "2311.15", "MgO"): Decimal("0.0"),
        ("V", "1992.15", "MgO"): Decimal("8.11"),
    }
    for (panel, temperature, oxide), expected in printed_cells.items():
        matching = [
            row
            for observation_id, row in residue.items()
            if row["experiment_id"].endswith(f"::{panel}")
            and row["identity"]["species"]["formula"] == oxide
            and row["identity"]["temperature_K"]["value"] == temperature
        ]
        assert len(matching) == 1
        assert Decimal(matching[0]["value"]["point"]) == expected

    charges = {
        "VI": {
            "SiO2": Decimal("44.02"), "Al2O3": Decimal("4.56"),
            "FeO": Decimal("8.04"), "MgO": Decimal("36.44"),
            "CaO": Decimal("6.95"),
        },
        "V": {
            "SiO2": Decimal("52.32"), "Al2O3": Decimal("19.13"),
            "FeO": Decimal("9.37"), "MgO": Decimal("6.25"),
            "CaO": Decimal("12.93"),
        },
    }
    extract = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts/kems-026-markova-1984.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_values = {
        observation["values"]["composition_id"]: observation["values"]
        for observation in extract["species"]["SiO2"]["observations"]
        if observation["values"].get("quantity") == "residue_composition_vs_time"
    }
    for panel, charge in charges.items():
        series = source_values[panel]["series"]
        panel_rows = [
            row
            for row in mass_loss_rows.values()
            if row["experiment_id"].endswith(f"::{panel}")
        ]
        assert len(panel_rows) == len(series)
        for row, source_point in zip(panel_rows, series):
            expected = (
                charge
                if source_point.get("T_C_is_initial_composition")
                else {
                    oxide: as_decimal(source_point[f"{oxide}_wt_pct"])
                    for oxide in charge
                }
            )
            assert _printed_wt_map(row) == expected

    for panel in charges:
        for row in residue.values():
            if not row["experiment_id"].endswith(f"::{panel}"):
                continue
            temperature_c = (
                Decimal(row["identity"]["temperature_K"]["value"])
                - Decimal("273.15")
            )
            source_point = next(
                point
                for point in source_values[panel]["series"]
                if "T_C" in point
                and as_decimal(point["T_C"]) == temperature_c
            )
            expected = {
                oxide: as_decimal(source_point[f"{oxide}_wt_pct"])
                for oxide in charges[panel]
            }
            assert _printed_wt_map(row) == expected
            composition = _state_value(
                (row.get("point_conditions") or {}).get("composition")
            )
            assert composition["amount_basis"] == "mole_fraction"
            actual_moles = {
                oxide: as_decimal(amount)
                for oxide, amount in composition["components"]
            }
            assert actual_moles == dict(wt_pct_to_mole_fraction(expected).components)

    for panel, charge in charges.items():
        panel_rows = [
            row
            for row in mass_loss_rows.values()
            if row["experiment_id"].endswith(f"::{panel}")
        ]
        experiment_ids = {row["experiment_id"] for row in panel_rows}
        assert len(experiment_ids) == 1
        sample = result.experiments[next(iter(experiment_ids))].sample
        assert sample.printed_composition is not None
        assert {
            oxide: as_decimal(value)
            for oxide, value in sample.printed_composition.state.value.items()
        } == charge
        expected_identity = dict(wt_pct_to_mole_fraction(charge).components)
        panel_residue = [
            row
            for row in residue.values()
            if row["experiment_id"].endswith(f"::{panel}")
        ]
        assert len(panel_residue) == 55
        for row in panel_residue:
            assert _identity_composition_components(row) == expected_identity


@pytest.mark.parametrize(
    "mode",
    ("tie", "missing-point", "missing-coordinates", "mismatched-coordinates"),
)
def test_markova_residue_uses_its_point_map_without_parent_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from simulator.battery import migrate as migration

    original = migration.Migrator._emit_exploded_point

    def emit(self, **kwargs):
        item = kwargs["item"]["item"]
        if (
            isinstance(item, dict)
            and item.get("T_C") == 1475
            and "MgO_wt_pct" in item
            and as_decimal(str(item.get("MgO_wt_pct"))) == Decimal("36.92")
        ):
            kwargs = dict(kwargs)
            parent_values = dict(kwargs["parent_values"])
            series = list(parent_values["series"])
            if mode == "tie":
                match = next(
                    point
                    for point in series
                    if point.get("T_C") == item.get("T_C")
                    and point.get("mass_loss_pct") == item.get("mass_loss_pct")
                )
                series.append({**match, "SiO2_wt_pct": Decimal("99")})
                parent_values["series"] = series
                kwargs["parent_values"] = parent_values
            elif mode == "missing-point":
                parent_values["series"] = []
                kwargs["parent_values"] = parent_values
            elif mode == "missing-coordinates":
                item = dict(item)
                item.pop("T_C", None)
                item.pop("mass_loss_pct", None)
                kwargs["item"] = {**kwargs["item"], "item": item}
            elif mode == "mismatched-coordinates":
                item = dict(item)
                item["mass_loss_pct"] = Decimal(str(item["mass_loss_pct"])) + Decimal(
                    "1e-12"
                )
                kwargs["item"] = {**kwargs["item"], "item": item}
        return original(self, **kwargs)

    monkeypatch.setattr(migration.Migrator, "_emit_exploded_point", emit)
    result = _migrate_real_extract(
        tmp_path,
        "kems-026-markova-1984.yaml",
        use_repository_index_row=True,
    )
    matches = []
    for observation in result.observations.values():
        row = to_plain(observation)
        if (
            _quantity(row) == "residue_component_composition"
            and row["identity"]["species"]["formula"] == "MgO"
            and Decimal(row["value"]["point"]) == Decimal("36.92")
        ):
            matches.append(row)
    assert len(matches) == 1
    assert _printed_wt_map(matches[0]) == {
        "SiO2": Decimal("44.4"),
        "Al2O3": Decimal("4.62"),
        "FeO": Decimal("7.01"),
        "MgO": Decimal("36.92"),
        "CaO": Decimal("7.05"),
    }


def test_holzheid_v69_activity_does_not_gain_printed_composition(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path,
        "holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml",
        use_repository_index_row=True,
    )
    stem = "holzheid-1997-feo-nio-coo-activity-metal-saturated"
    parent_id = "holzheid_1997_table3a_ad_co_variable_mgo_1_1"
    source = yaml.safe_load(
        (REPO_ROOT / "data/literature/extracts" / f"{stem}.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_row = next(
        point
        for observation in source["species"]["CoO"]["observations"]
        if observation["observation_id"] == parent_id
        for point in observation["values"]["series"]
        if point["run"] == "V 69"
    )
    assert as_decimal(str(source_row["CoO_wt_pct"])) == as_decimal("2.03")
    assert as_decimal(str(source_row["MgO_wt_pct"])) == as_decimal("6.97")

    prefix = f"{stem}::{parent_id}::"
    matches = [
        observation
        for observation_id, observation in result.observations.items()
        if observation_id.startswith(prefix)
    ]
    assert len(matches) == 1
    assert "printed_composition" not in (matches[0].point_conditions or {})


def test_bulk_property_rows_do_not_use_sample_code_as_formula() -> None:
    """MLS-* / MS[0-9] are sample labels, not chemical formulas."""

    bad: list[str] = []
    for obs in _iter_store_observations():
        if _quantity(obs) not in _BULK_QUANTITIES:
            continue
        formula = _formula(obs)
        if formula and _SAMPLE_CODE_FORMULA.match(formula):
            bad.append(f"{obs.get('observation_id')} formula={formula}")
    assert not bad, bad[:20]


def test_mls_1a_case_duplicate_is_one_identity() -> None:
    """MLS-1a and MLS-1A are one material; bulk rows share one species formula."""

    formulas: set[str] = set()
    found: list[str] = []
    for obs in _iter_store_observations():
        oid = str(obs.get("observation_id") or "")
        if not any(oid.startswith(prefix) for prefix in _MLS_BULK_ID_PREFIXES):
            continue
        if _quantity(obs) not in _BULK_QUANTITIES:
            continue
        found.append(oid)
        formulas.add(_formula(obs))
    assert len(found) == len(_MLS_BULK_ID_PREFIXES), found
    assert all(
        any(oid.startswith(prefix) for oid in found)
        for prefix in _MLS_BULK_ID_PREFIXES
    ), found
    assert len(formulas) == 1, formulas
    formula = next(iter(formulas))
    assert formula, "MLS bulk rows have empty species.formula"
    assert not _SAMPLE_CODE_FORMULA.match(formula), formula


_PLANTE_CHARGE_WT = {"K2O": "43.94", "SiO2": "56.06"}
# The regen replaced bare ordinal experiment ids with descriptive slugs.
_PLANTE_EXPERIMENT_ID = "10.6028/nbs.sp.561v1::experiment::k2o-sio2-effusion-series"


def _plante_evolving_extract() -> dict:
    """Two Table 2 rows of one K2Si2O5 charge; residual wt% changes, charge does not."""

    charge = {"K2O": 43.94, "SiO2": 56.06}
    sample = {
        "form": "crystalline K2Si2O5",
        "printed_composition": charge,
        "locator": {
            "published_page": 276,
            "table": "2",
            "note": "Series 1104 first row; K2Si2O5 charge as loaded",
        },
    }
    equipment = {
        "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
        "cell_material": {
            "value": "platinum Knudsen cell",
            "locator": {"published_page": 267, "figure": "2"},
        },
        "sample": sample,
    }

    def row(oid: str, t_k: float, k2o: float, sio2: float) -> dict:
        return {
            "observation_id": oid,
            "type": "activity_coefficient",
            "locator": {
                "table": "2",
                "published_page": 276,
                "note": f"{oid} residual melt",
            },
            "phase": "K2O-SiO2_binary_silicate_melt",
            "regime": "kems_effusion",
            "units": "dimensionless",
            "values": {
                "quantity": "activity_vapor_reference_eq7",
                "method_class": "measured_direct",
                "activity": 1.0e-16,
                "composition_wt_pct": {"K2O": k2o, "SiO2": sio2},
                "T_K_as_published": t_k,
                "series": 1104,
            },
            "equipment": equipment,
        }

    return {
        "schema_version": "literature_extract.v1",
        "source_id": "fixture-source",
        "source": {
            "citation": "Fixture, A. (2026), Test Journal 1:1, DOI 10.1234/FIXTURE",
            "doi": "10.1234/FIXTURE",
        },
        "extraction": {"method": "unit_test", "date": "2026-09-19", "worker": "pytest"},
        "review_status": "draft",
        "fidelity_samples": [],
        "species": {
            "K2O": {
                "observations": [
                    row("plante_s1104_start", 1302.0, 43.94, 56.06),
                    row("plante_s1104_later", 1396.0, 40.36, 59.64),
                ]
            }
        },
    }


def test_migrate_plante_shaped_charge_survives_residual_evolution(tmp_path: Path) -> None:
    """Evolving composition_wt_pct is the residual; Sample keeps the loaded charge.

    Fail-proof: vocabulary maps composition_wt_pct → Sample.printed_composition,
    so two residuals conflict and the merge wipes Sample to {}.
    """

    root = _write_min_tree(tmp_path, _plante_evolving_extract())
    result = migrate(root, write=False)
    start = result.observations["fixture-source::plante_s1104_start"]
    later = result.observations["fixture-source::plante_s1104_later"]
    assert start.experiment_id == later.experiment_id
    experiment = result.experiments[start.experiment_id]
    printed = experiment.sample.printed_composition
    assert printed is not None and printed.state.is_value, (
        "Sample.printed_composition was wiped by residual composition_wt_pct"
    )
    wt = printed.state.value
    assert as_decimal(wt["K2O"]) == as_decimal("43.94")
    assert as_decimal(wt["SiO2"]) == as_decimal("56.06")
    assert experiment.sample.form is not None and experiment.sample.form.state.is_value
    assert experiment.sample.form.state.value == "crystalline K2Si2O5"
    assert experiment.sample.mass_kg is None
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert initial.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert initial.inference is not None
    assert initial.inference.relation == "wt_pct_to_mole_fraction"
    start_x = dict(start.identity.composition.value.components)
    later_x = dict(later.identity.composition.value.components)
    assert start_x != later_x


def test_registry_charge_repeated_by_row_keeps_derived_initial_composition(
    tmp_path: Path,
) -> None:
    raw = _plante_evolving_extract()
    charge = dict(_PLANTE_CHARGE_WT)
    raw["experiments"] = [
        {
            "experiment_id": "k2o-sio2-effusion-series",
            "method": "knudsen_effusion",
            "sample": {
                "composition_role": INITIAL_CHARGE_ONLY_PROXY_FLAG,
                "initial_composition": {
                    "state": {
                        "tag": "value",
                        "value": {
                            "basis": "printed_oxides",
                            "amount_basis": "mass_percent",
                            "components": [[key, value] for key, value in charge.items()],
                        },
                    },
                    "locator": {"table": "2", "published_page": 276},
                }
            },
        }
    ]
    for observation in raw["species"]["K2O"]["observations"]:
        observation["experiment"] = "k2o-sio2-effusion-series"

    result = migrate(_write_min_tree(tmp_path, raw), write=False)

    experiment_id = result.observations[
        "fixture-source::plante_s1104_start"
    ].experiment_id
    experiment = result.experiments[experiment_id]
    initial = experiment.sample.initial_composition
    assert initial is not None and initial.state.is_value
    assert initial.state.value.amount_basis is AmountBasis.MOLE_FRACTION
    assert initial.inference is not None
    assert initial.inference.relation == "wt_pct_to_mole_fraction"
    expected = wt_pct_to_mole_fraction(
        {key: as_decimal(value) for key, value in _PLANTE_CHARGE_WT.items()}
    )
    assert dict(initial.state.value.components) == dict(expected.components)


def test_plante_1979_one_k2si2o5_charge_not_eight_series_experiments() -> None:
    """Table 2 is one K2Si2O5 charge; series are analysis groups, not reloads.

    Paper p.271: crystalline K2Si2O5 starting material. p.270: composition
    from the known starting composition plus integrated ion current.
    Table 3 ranges chain 43.9–6.8 wt% K2O with no reload jump. p.272:
    'The data were broken up into eight series for analysis.'
    Fail-proof: the store currently files 383 observations on one
    experiment whose Sample is {}.
    """

    work_path = REPO_ROOT / "data" / "literature" / "works" / "10.6028_nbs.sp.561v1.yaml"
    store = _OBS_ROOTS[0] / "kems-042-plante-1979.yaml"
    assert work_path.is_file() and store.is_file()
    work_doc = yaml.safe_load(work_path.read_text(encoding="utf-8"))
    plante_experiments = [
        row
        for row in (work_doc.get("experiments") or [])
        if isinstance(row, dict)
        and str(row.get("experiment_id") or "").startswith("10.6028/nbs.sp.561v1::")
    ]
    assert [row.get("experiment_id") for row in plante_experiments] == [
        _PLANTE_EXPERIMENT_ID
    ], [row.get("experiment_id") for row in plante_experiments]
    experiment = plante_experiments[0]
    sample = experiment.get("sample") or {}
    printed = sample.get("printed_composition") or {}
    printed_state = printed.get("state") if isinstance(printed, dict) else None
    assert isinstance(printed_state, dict) and printed_state.get("tag") == "value", (
        f"sample keys: {sorted(sample)}"
    )
    wt = printed_state.get("value") or {}
    assert as_decimal(wt["K2O"]) == as_decimal(_PLANTE_CHARGE_WT["K2O"])
    assert as_decimal(wt["SiO2"]) == as_decimal(_PLANTE_CHARGE_WT["SiO2"])
    locator = printed.get("locator") or {}
    assert str(locator.get("table")) == "2"
    assert locator.get("published_page") == 276
    initial = sample.get("initial_composition") or {}
    initial_state = initial.get("state") if isinstance(initial, dict) else None
    assert isinstance(initial_state, dict) and initial_state.get("tag") == "value"
    initial_value = initial_state.get("value") or {}
    assert initial_value.get("amount_basis") == "mole_fraction"
    inference = initial.get("inference") or {}
    assert inference.get("relation") == "wt_pct_to_mole_fraction"
    converted = wt_pct_to_mole_fraction(
        {k: as_decimal(v) for k, v in _PLANTE_CHARGE_WT.items()}
    )
    got = {
        str(item[0]): as_decimal(item[1])
        for item in (initial_value.get("components") or ())
    }
    expected = dict(converted.components)
    for oxide, amount in expected.items():
        assert abs(got[oxide] - amount) < as_decimal("1e-12"), oxide
    assert sample.get("mass_kg") is None, (
        "paper does not state a loaded mass; absence must not become a measured zero"
    )
    form = sample.get("form") or {}
    form_state = form.get("state") if isinstance(form, dict) else None
    assert isinstance(form_state, dict) and form_state.get("value") == "crystalline K2Si2O5"
    obs_doc = yaml.safe_load(store.read_text(encoding="utf-8"))
    rows = [
        obs
        for obs in (obs_doc.get("observations") or [])
        if isinstance(obs, dict)
    ]
    assert len(rows) == 383
    assert {obs.get("experiment_id") for obs in rows} == {_PLANTE_EXPERIMENT_ID}
    first = next(
        obs
        for obs in rows
        if str(obs.get("observation_id") or "").endswith(
            "plante1979_table2_k2o_s1104_000_1302K"
        )
    )
    later = next(
        obs
        for obs in rows
        if str(obs.get("observation_id") or "").endswith(
            "plante1979_table2_k2o_s1104_027_1396K"
        )
    )
    assert _identity_composition_components(first)
    assert _identity_composition_components(later)
    assert _identity_composition_components(first) != _identity_composition_components(
        later
    )
