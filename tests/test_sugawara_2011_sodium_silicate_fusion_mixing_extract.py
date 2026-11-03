"""Source and migration checks for Sugawara et al. (2011)."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import MethodToken, Phase, UncertaintyKind, ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "sugawara-2011-sodium-silicate-fusion-mixing"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_rows(document):
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _migrated_rows():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def _components(observation):
    return dict(observation.point_conditions["composition"].state.value.components)


def test_printed_rows_and_render_pins():
    document = _document()
    rows = _source_rows(document)
    assert rows
    for row in rows.values():
        if row["locator"].get("table") in {"1", "2"}:
            assert row["locator"].get("row") is not None
            assert row["locator"].get("page") in {2, 3}

    # Values below were read from the indicated table cells on the PDF renders.
    pins = {
        "sugawara_2011_t1_na2sio3_heat_of_fusion": (
            "value",
            "26.6",
            2,
            "1",
        ),
        "sugawara_2011_t1_na6si8o19_eutectic_enthalpy_of_mixing": (
            "enthalpy_of_mixing_kJ_mol-1_as_published",
            "-56.9",
            2,
            "1",
        ),
        "sugawara_2011_t2_sio2_liquid_heat_capacity": (
            "value",
            "81.37",
            3,
            "2",
        ),
        "sugawara_2011_t2_na6si8o19_fusion_enthalpy": (
            "value",
            "7.79",
            3,
            "2",
        ),
    }
    for observation_id, (field, expected, page, table) in pins.items():
        row = rows[observation_id]
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table

    prose_pins = {
        "sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy": ("53.2", 2),
        "sugawara_2011_p2_na2sio3_drop_calorimetry_fusion_enthalpy_37": ("52.2", 2),
        "sugawara_2011_p2_na2sio3_drop_calorimetry_fusion_enthalpy_38": ("52.0", 2),
        "sugawara_2011_p2_na2si2o5_dsc_fusion_enthalpy": ("29.0", 2),
        "sugawara_2011_p2_na2si2o5_prior_dsc_fusion_enthalpy_43": ("34.0", 2),
        "sugawara_2011_p2_na2si2o5_prior_calculated_fusion_enthalpy_44": ("37.8", 2),
        "sugawara_2011_p4_ns_weighted_mean_enthalpy_of_mixing": ("-110.2", 4),
    }
    for observation_id, (expected, page) in prose_pins.items():
        row = rows[observation_id]
        field = (
            "enthalpy_of_mixing_kJ_mol-1_as_published"
            if "weighted_mean" in observation_id
            else "value"
        )
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page

    solid_heat_capacity_equations = {
        "sugawara_2011_t2_sio2_solid_heat_capacity_equation": "Cp = 127.20 − 10.777 × 10⁻³ T + 4.3130 × 10⁵ T⁻² − 1463.9 T⁻⁰·⁵",
        "sugawara_2011_t2_na6si8o19_solid_heat_capacity_equation": "Cp = 112.78 − 1.3334 × 10⁻³ T + 2.2919 × 10⁵ T⁻² − 1089.1 T⁻⁰·⁵",
        "sugawara_2011_t2_na2si2o5_solid_heat_capacity_equation": "Cp = 108.43 + 1.5187 × 10⁻³ T + 1.6813 × 10⁵ T⁻² − 975.92 T⁻⁰·⁵",
        "sugawara_2011_t2_na2sio3_solid_heat_capacity_equation": "Cp = 99.042 + 7.6665 × 10⁻³ T + 0.3655 × 10⁵ T⁻² − 731.95 T⁻⁰·⁵",
    }
    for observation_id, equation in solid_heat_capacity_equations.items():
        row = rows[observation_id]
        assert row["values"]["equation_as_printed"] == equation
        assert row["locator"]["page"] == 3
        assert row["locator"]["table"] == "2"
        assert row["phase"] == "cr"
        assert row["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"

    solid_enthalpy_values = {
        "sugawara_2011_t2_sio2_solid_enthalpy_at_tm": "-790.589 [47]",
        "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm": "-786.070^d",
        "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm": "-761.609^d",
        "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm": "-697.782^d",
        "sugawara_2011_t2_na2o_solid_enthalpy_at_tm": "-304.711 [47]",
    }
    for observation_id, expected in solid_enthalpy_values.items():
        row = rows[observation_id]
        assert row["values"]["solid_enthalpy_at_Tm_kJ_mol_as_published"] == expected
        assert row["values"]["admission_status"] == "typed_refusal"
        assert row["locator"]["page"] == 3
        assert row["locator"]["table"] == "2"

    model = next(
        row
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "sugawara_2011_t3_gibbs_mixing_coefficients"
    )
    h2 = model["values"]["parameters"][0]
    assert h2["full_value_as_published"] == "−271.2 (117.2)"
    assert model["locator"]["page"] == 5
    assert model["locator"]["table"] == "3"

    model_fit_parents = [
        "sugawara-2011-sodium-silicate-fusion-mixing::context::sugawara_2011_t3_gibbs_mixing_coefficients",
        "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
        "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
    ]
    model_hsolid_parents = {
        "sugawara_2011_t2_na6si8o19_fusion_enthalpy": "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2si2o5_fusion_enthalpy": "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2sio3_fusion_enthalpy": "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm",
    }
    for observation_id, mixture_solid_enthalpy in model_hsolid_parents.items():
        row = rows[observation_id]
        assert row["values"]["method_class"] == "model_derived"
        expected_parents = [
            model_fit_parents[0],
            mixture_solid_enthalpy,
            "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        ]
        assert row["values"]["derived_from"] == expected_parents
        assert row["values"]["derivation"]["inputs"] == expected_parents

    t1_mixing_parents = {
        "sugawara_2011_t1_na6si8o19_eutectic_enthalpy_of_mixing": [
            "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
            "sugawara_2011_t2_sio2_fusion_enthalpy",
            "sugawara_2011_t2_na2o_fusion_enthalpy",
            "sugawara_2011_t2_sio2_liquid_heat_capacity",
            "sugawara_2011_t2_na2o_liquid_heat_capacity",
            "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
            "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        ],
        "sugawara_2011_t1_na2si2o5_enthalpy_of_mixing": [
            "sugawara_2011_t1_na2si2o5_heat_of_fusion",
            "sugawara_2011_t2_sio2_fusion_enthalpy",
            "sugawara_2011_t2_na2o_fusion_enthalpy",
            "sugawara_2011_t2_sio2_liquid_heat_capacity",
            "sugawara_2011_t2_na2o_liquid_heat_capacity",
            "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm",
            "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        ],
        "sugawara_2011_t1_na2sio3_enthalpy_of_mixing": [
            "sugawara_2011_t1_na2sio3_heat_of_fusion",
            "sugawara_2011_t2_sio2_fusion_enthalpy",
            "sugawara_2011_t2_na2o_fusion_enthalpy",
            "sugawara_2011_t2_sio2_liquid_heat_capacity",
            "sugawara_2011_t2_na2o_liquid_heat_capacity",
            "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm",
            "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        ],
    }
    for observation_id, expected_parents in t1_mixing_parents.items():
        values = rows[observation_id]["values"]
        assert values["derived_from"] == expected_parents
        assert values["derivation"]["inputs"] == expected_parents

    for observation_id, expected in solid_enthalpy_values.items():
        row = rows[observation_id]
        assert row["values"]["quantity"] == "solid_enthalpy_at_Tm_as_printed"
        expected_class = "author_derived" if observation_id in {
            "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm",
        } else "quoted_attributed"
        assert row["values"]["method_class"] == expected_class
        assert row["values"]["solid_enthalpy_at_Tm_kJ_mol_as_published"] == expected
    for observation_id in (
        "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm",
    ):
        row = rows[observation_id]
        assert row["values"]["derived_from"] == [
            "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
            "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        ]
        assert "[47] standard state value" in row["values"]["derivation"]["relation"]
        assert "[40] heat capacity" in row["values"]["derivation"]["relation"]
    weighted_parents = [
        "sugawara_2011_t1_na2sio3_enthalpy_of_mixing",
        "sugawara-2011-sodium-silicate-fusion-mixing::context::sugawara_2011_p4_weighted_mean_fan_reference",
        "sugawara-2011-sodium-silicate-fusion-mixing::context::sugawara_2011_p4_weighted_mean_naylor_reference",
        "sugawara-2011-sodium-silicate-fusion-mixing::context::sugawara_2011_p4_weighted_mean_richet_reference",
    ]
    weighted = rows["sugawara_2011_p4_ns_weighted_mean_enthalpy_of_mixing"]["values"]
    assert weighted["method_class"] == "author_derived"
    assert weighted["derived_from"] == weighted_parents
    assert weighted["derivation"]["inputs"] == weighted_parents
    references = {
        row["observation_id"]: row["values"]["reference_as_printed"]
        for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] in {item.rsplit("::", 1)[-1] for item in weighted_parents[1:]}
    }
    assert references == {
        "sugawara_2011_p4_weighted_mean_fan_reference": "Fan [10]",
        "sugawara_2011_p4_weighted_mean_naylor_reference": "Naylor [37]",
        "sugawara_2011_p4_weighted_mean_richet_reference": "Richet et al. [38]",
    }


def test_compositions_keep_printed_basis_and_migration_conditions():
    document = _document()
    source_rows = _source_rows(document)
    result = _migrated_rows()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    assert set(migrated) == set(source_rows)

    for observation_id, expected in (
        ("sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion", {"SiO2": Decimal("0.744"), "Na2O": Decimal("0.256")}),
        ("sugawara_2011_t2_na6si8o19_fusion_enthalpy", {"SiO2": Decimal("0.744"), "Na2O": Decimal("0.256")}),
        ("sugawara_2011_t1_na2si2o5_heat_of_fusion", {"SiO2": Decimal("0.664"), "Na2O": Decimal("0.333")}),
        ("sugawara_2011_t2_na2si2o5_fusion_enthalpy", {"SiO2": Decimal("0.666"), "Na2O": Decimal("0.333")}),
    ):
        source = source_rows[observation_id]
        observation = migrated[observation_id]
        assert source["point_conditions"]["composition"]["state"]["value"]["components"]
        assert _components(observation) == expected
        assert observation.identity.composition.is_value
        assert dict(observation.identity.composition.value.components) == expected

    # The printed 66.4/33.3 and 66.6/33.3 rows are not normalized to sum to 1.
    assert sum(_components(migrated["sugawara_2011_t1_na2si2o5_heat_of_fusion"]).values()) == Decimal("0.997")
    assert sum(_components(migrated["sugawara_2011_t2_na2si2o5_fusion_enthalpy"]).values()) == Decimal("0.999")

    assert not result.validation.hard_issues
    assert result.registry_issues == []
    per_gfw = (
        "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
        "sugawara_2011_t1_na2si2o5_heat_of_fusion",
        "sugawara_2011_t1_na2sio3_heat_of_fusion",
        "sugawara_2011_t2_na6si8o19_fusion_enthalpy",
        "sugawara_2011_t2_na2si2o5_fusion_enthalpy",
        "sugawara_2011_t2_na2sio3_fusion_enthalpy",
        "sugawara_2011_t2_na6si8o19_liquid_heat_capacity",
        "sugawara_2011_t2_na2si2o5_liquid_heat_capacity",
        "sugawara_2011_t2_na2sio3_liquid_heat_capacity",
    )
    for observation_id in per_gfw:
        assert migrated[observation_id].value.kind is ValueKind.UNAVAILABLE
        assert not migrated[observation_id].identity.quantity.is_value
    assert source_rows["sugawara_2011_t1_na2sio3_heat_of_fusion"]["values"]["value"] == "26.6"
    assert source_rows["sugawara_2011_t2_na6si8o19_fusion_enthalpy"]["values"]["value"] == "7.79"
    assert source_rows["sugawara_2011_t1_na2sio3_heat_of_fusion"]["values"]["formula_basis_as_printed"].startswith("1/2 Na2SiO3")
    assert source_rows["sugawara_2011_t2_na6si8o19_fusion_enthalpy"]["values"]["formula_basis_as_printed"].startswith("Na6Si8O19–SiO2")
    per_gfw_cpg = (
        "sugawara_2011_t2_na6si8o19_solid_heat_capacity_equation",
        "sugawara_2011_t2_na2si2o5_solid_heat_capacity_equation",
        "sugawara_2011_t2_na2sio3_solid_heat_capacity_equation",
    )
    for observation_id in per_gfw_cpg:
        assert migrated[observation_id].value.kind is ValueKind.UNAVAILABLE
        assert not migrated[observation_id].identity.quantity.is_value
    assert all(
        row.value.unavailable_reason
        for row in migrated.values()
        if row.value.kind is ValueKind.UNAVAILABLE
    )
    assert migrated["sugawara_2011_t2_sio2_liquid_heat_capacity"].value.point == Decimal("81.37")
    for observation_id, expected in (
        ("sugawara_2011_t1_na2si2o5_heat_of_fusion", Decimal("0.7")),
        ("sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy", Decimal("2.0")),
    ):
        assert migrated[observation_id].uncertainty.kind is UncertaintyKind.PRINTED
        assert migrated[observation_id].uncertainty.verbatim["value"] == str(expected)
    assert migrated["sugawara_2011_t1_na6si8o19_eutectic_enthalpy_of_mixing"].uncertainty.kind is UncertaintyKind.PRINTED
    assert migrated["sugawara_2011_t1_na6si8o19_eutectic_enthalpy_of_mixing"].uncertainty.verbatim["value"] == "1.3"
    for observation_id, printed_width in (
        ("sugawara_2011_t1_na2si2o5_enthalpy_of_mixing", "1.2"),
        ("sugawara_2011_t1_na2sio3_enthalpy_of_mixing", "1.5"),
    ):
        assert migrated[observation_id].uncertainty.kind is UncertaintyKind.PRINTED
        assert migrated[observation_id].uncertainty.verbatim["value"] == printed_width
    for observation_id in (
        "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
        "sugawara_2011_t1_na2si2o5_heat_of_fusion",
        "sugawara_2011_t1_na2sio3_heat_of_fusion",
        "sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy",
        "sugawara_2011_p2_na2si2o5_dsc_fusion_enthalpy",
    ):
        observation = migrated[observation_id]
        experiment = result.experiments[observation.experiment_id]
        assert experiment.method.value is MethodToken.DTA_DSC
        assert observation.evidence.class_.value.value == "measured_tabulated"
    dsc_experiment_ids = {
        migrated[observation_id].experiment_id
        for observation_id in (
            "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
            "sugawara_2011_t1_na2si2o5_heat_of_fusion",
            "sugawara_2011_t1_na2sio3_heat_of_fusion",
            "sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy",
            "sugawara_2011_p2_na2si2o5_dsc_fusion_enthalpy",
        )
    }
    dsc_experiment_id = next(iter(dsc_experiment_ids))
    assert all(
        migrated[observation_id].experiment_id == dsc_experiment_id
        for observation_id in (
            "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
            "sugawara_2011_t1_na2si2o5_heat_of_fusion",
            "sugawara_2011_t1_na2sio3_heat_of_fusion",
            "sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy",
            "sugawara_2011_p2_na2si2o5_dsc_fusion_enthalpy",
        )
    )
    quoted = migrated["sugawara_2011_p2_na2sio3_drop_calorimetry_fusion_enthalpy_37"]
    quoted_experiment = result.experiments[quoted.experiment_id]
    assert quoted.experiment_id != dsc_experiment_id
    assert not quoted_experiment.method.is_value
    assert all(
        result.experiments[experiment_id].method.value is MethodToken.DTA_DSC
        for experiment_id in dsc_experiment_ids
    )
    other_experiments = {
        row.experiment_id for observation_id, row in migrated.items()
        if observation_id not in {
            "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
            "sugawara_2011_t1_na2si2o5_heat_of_fusion",
            "sugawara_2011_t1_na2sio3_heat_of_fusion",
            "sugawara_2011_p2_na2sio3_dsc_fusion_enthalpy",
            "sugawara_2011_p2_na2si2o5_dsc_fusion_enthalpy",
        }
    }
    assert other_experiments == {quoted.experiment_id}
    assert {
        row.experiment_id for row in migrated.values()
    } == {dsc_experiment_id, quoted.experiment_id}
    assert not result.experiments[quoted.experiment_id].method.is_value
    for observation_id, page, table in (
        ("sugawara_2011_t1_na2sio3_heat_of_fusion", 2, "1"),
        ("sugawara_2011_t2_sio2_liquid_heat_capacity", 3, "2"),
        ("sugawara_2011_t2_na6si8o19_fusion_enthalpy", 3, "2"),
    ):
        assert migrated[observation_id].locator.page == page
        assert migrated[observation_id].locator.table == table
    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if not row.identity.quantity.is_value
    }
    assert quantity_unknown == {
        "sugawara_2011_t1_na6si8o19_eutectic_enthalpy_of_mixing",
        "sugawara_2011_t1_na2si2o5_enthalpy_of_mixing",
        "sugawara_2011_t1_na2sio3_enthalpy_of_mixing",
        "sugawara_2011_p4_ns_weighted_mean_enthalpy_of_mixing",
        "sugawara_2011_t2_sio2_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na6si8o19_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2si2o5_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2sio3_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na2o_solid_enthalpy_at_tm",
        "sugawara_2011_t2_na6si8o19_solid_heat_capacity_equation",
        "sugawara_2011_t2_na2si2o5_solid_heat_capacity_equation",
        "sugawara_2011_t2_na2sio3_solid_heat_capacity_equation",
        "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion",
        "sugawara_2011_t1_na2si2o5_heat_of_fusion",
        "sugawara_2011_t1_na2sio3_heat_of_fusion",
        "sugawara_2011_t2_na6si8o19_fusion_enthalpy",
        "sugawara_2011_t2_na2si2o5_fusion_enthalpy",
        "sugawara_2011_t2_na2sio3_fusion_enthalpy",
        "sugawara_2011_t2_na6si8o19_liquid_heat_capacity",
        "sugawara_2011_t2_na2si2o5_liquid_heat_capacity",
        "sugawara_2011_t2_na2sio3_liquid_heat_capacity",
    }
    assert all(
        migrated[observation_id].value.kind is ValueKind.UNAVAILABLE
        for observation_id in quantity_unknown
    )
    assert all(row.identity.species.phase.is_value for row in migrated.values())

    for observation_id, row in migrated.items():
        source = source_rows[observation_id]
        expected_phase = {"cr": Phase.CR, "l": Phase.L}[source["phase"]]
        assert row.identity.species.phase.value is expected_phase
        conditions = row.point_conditions
        assert conditions is not None
        assert {"temperature_K", "composition", "fO2_Pa"} <= set(conditions)
        assert "total_pressure_Pa" not in conditions
        assert conditions["temperature_K"].locator.page == source["locator"]["page"]
        assert conditions["composition"].locator.page == source["locator"]["page"]
        assert conditions["fO2_Pa"].state.is_unknown
        assert not _state_is_value(row.identity.total_pressure_Pa)
        assert not _state_is_value(row.identity.fO2_Pa)

    identity_axis_pins = {
        "sugawara_2011_t1_na6si8o19_eutectic_heat_of_fusion": (Decimal("1064"), Decimal("0.744")),
        "sugawara_2011_t2_na6si8o19_fusion_enthalpy": (Decimal("1164"), Decimal("0.744")),
        "sugawara_2011_t1_na2si2o5_heat_of_fusion": (Decimal("1142"), Decimal("0.664")),
    }
    for observation_id, (temperature, silica_fraction) in identity_axis_pins.items():
        row = migrated[observation_id]
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == temperature
        assert row.identity.composition.is_value
        assert dict(row.identity.composition.value.components)["SiO2"] == silica_fraction


def _state_is_value(state):
    return state is not None and state.is_value
