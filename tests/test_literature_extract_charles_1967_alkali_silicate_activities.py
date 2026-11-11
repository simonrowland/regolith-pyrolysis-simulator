from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import QUANTITY_UNITS, Quantity, ValueKind
from simulator.battery.identity import THERMOCHEMICAL_CALORIE_J
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/charles-1967-alkali-silicate-activities.yaml"

# Independent transcriptions of every body cell on the rendered pages 632, 635,
# and 638. Each table has ten printed data rows.
TABLES = {
    "I": {
        "page": 2,
        "published_page": 632,
        "oxide": "Li2O",
        "unit": "kcal/mole",
        "rows": (
            ("1973", "0.004", "-0.00132", "0.00042", "0.0038"),
            ("1873", ".078", "-0.0114", ".0239", ".204"),
            ("1773", ".159", "-0.0213", ".0539", ".437"),
            ("1673", ".201", "-0.0314", ".0661", ".505"),
            ("1573", ".237", "-0.0415", ".0760", ".547"),
            ("1473", ".265", "-0.0516", ".0821", ".553"),
            ("1373", ".288", "-0.0619", ".0856", ".537"),
            ("1273", ".307", "-0.0726", ".0867", ".505"),
            ("1173", "(.322)*", "-0.0837", ".0850", ".456"),
            ("1073", "(.335)*", "-0.0959", ".0813", ".399"),
        ),
    },
    "II": {
        "page": 5,
        "published_page": 635,
        "oxide": "Na2O",
        "unit": "cal/mole",
        "rows": (
            ("1973", "0.0034", "-0.00132", "0.000154", "41.4"),
            ("1873", ".0427", "-0.0114", ".00756", "64.8"),
            ("1773", ".0992", "-0.0213", ".0240", "195"),
            ("1673", ".132", "-0.0314", ".0300", "230"),
            ("1573", ".158", "-0.0415", ".0333", "240"),
            ("1473", ".182", "-0.0516", ".0355", "239"),
            ("1373", ".202", "-0.0619", ".0362", "227"),
            ("1273", ".212", "-0.0672", ".0361", "218"),
            ("1173", ".220", "-0.0726", ".0356", "207"),
            ("1073", ".248", "-0.0959", ".0282", "138"),
        ),
    },
    "III": {
        "page": 8,
        "published_page": 638,
        "oxide": "K2O",
        "unit": "cal/mole",
        "rows": (
            ("1973", "0.003", "-0.00132", "0", "0"),
            ("1873", ".033", "-0.0014", ".00319", "27.3"),
            ("1773", ".065", "-0.0213", ".00788", "63.9"),
            ("1673", ".898", "-0.0314", ".00950", "72.7"),
            ("1573", ".1126", "-0.0415", ".0104", "74.8"),
            ("1473", ".1309", "-0.0516", ".00928", "62.5"),
            ("1373", ".1465", "-0.0619", ".00684", "43.0"),
            ("1273", ".160", "-0.0726", ".00315", "18.3"),
            ("1173", ".1711", "-0.0837", "-.00225", "-12.1"),
            ("1073", ".1831", "-0.0959", "-.00805", "-39.5"),
        ),
    },
}


def _all_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_rendered_table_cells(document: dict) -> None:
    rows_by_id = _all_rows(document)
    for table, specification in TABLES.items():
        assert len(specification["rows"]) == 10
        for temperature, x1, log_activity, log_gamma, enthalpy in specification["rows"]:
            stem = f"charles_1967_table_{table.lower()}_sio2"
            locator = {
                "page": specification["page"],
                "published_page": specification["published_page"],
                "pdf_page_index": specification["page"] - 1,
                "table": table,
                "row": temperature,
            }
            activity = rows_by_id[f"{stem}_activity_{temperature}"]
            coefficient = rows_by_id[f"{stem}_activity_coefficient_{temperature}"]
            enthalpy_row = rows_by_id[f"{stem}_partial_molar_enthalpy_{temperature}"]
            composition = rows_by_id[
                f"charles_1967_table_{table.lower()}_liquidus_composition_{temperature}"
            ]

            assert activity["locator"] == coefficient["locator"] == enthalpy_row["locator"] == locator
            assert composition["locator"] == locator
            assert activity["values"]["activity"] == log_activity
            assert coefficient["values"]["gamma"] == log_gamma
            assert enthalpy_row["values"]["value"] == enthalpy
            assert all(row["values"]["x1_as_printed"] == x1 for row in (activity, coefficient, enthalpy_row))
            assert activity["values"]["T_K"] == temperature
            assert coefficient["values"]["T_K"] == temperature
            assert enthalpy_row["values"]["T_K"] == temperature
            assert composition["values"]["component_amount_as_published"] == x1
            assert activity["units"] == coefficient["units"] == "dimensionless"
            assert enthalpy_row["units"] == specification["unit"]
            assert activity["values"]["method_class"] == "model_derived"
            assert coefficient["values"]["method_class"] == "model_derived"
            assert enthalpy_row["values"]["method_class"] == "model_derived"
            assert activity["values"]["printed_form_as_published"] == "log10"
            assert coefficient["values"]["printed_form_as_published"] == "log10"
            assert activity["values"]["printed_form"] == "log10"
            assert coefficient["values"]["printed_form"] == "log10"
            assert composition["values"]["component_amount"] == format(
                Decimal(x1.strip("()*")), "f"
            )
            if table == "I":
                assert composition["values"]["method_class"] == "quoted_unattributed"
                assert "attribution" not in composition["values"]
                assert composition["values"]["reason"] == (
                    "The text calls these liquidus values but does not identify which work "
                    "supplied Table I's X1 inputs."
                )
            else:
                expected_attribution = {
                    "II": "F. C. Kracek, “System Sodium Oxide-Silica” (1930), reference [23]",
                    "III": (
                        "F. C. Kracek, N. L. Bowen, and G. W. Morey, “Equilibrium Relations "
                        "and Factors Influencing Their Determination in the System K2SiO3-SiO2” "
                        "(1937), reference [30]"
                    ),
                }[table]
                assert composition["values"]["method_class"] == "quoted_attributed"
                assert composition["values"]["attribution"] == expected_attribution
            components = composition["values"]["composition_mol"]
            assert components[specification["oxide"]] == composition["values"]["component_amount"]
            assert Decimal(components["SiO2"]) == Decimal(1) - Decimal(x1.strip("()*"))


def test_all_rendered_cells_migrate_with_exact_conditions_and_mutation_guard() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_rendered_table_cells(document)
    assert validate_extract_document(
        document, expected_source_id="charles-1967-alkali-silicate-activities"
    ) == []

    mutated = deepcopy(document)
    _all_rows(mutated)["charles_1967_table_ii_sio2_activity_coefficient_1573"]["values"]["gamma"] = ".0334"
    try:
        _assert_rendered_table_cells(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("an interior Table II render-pinned value mutation passed")

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    rows = result.observations
    assert len(rows) == 120
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert sum(row.value.kind is ValueKind.POINT for row in rows.values()) == 120
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in rows.values()) == 0
    assert QUANTITY_UNITS[Quantity.PARTIAL_MOLAR_ENTHALPY] == (
        "kJ_per_declared_mol_basis"
    )
    source_rows_by_id = _all_rows(document)
    expected_enthalpy_values = {}
    for table, specification in TABLES.items():
        for temperature, *_ in specification["rows"]:
            observation_id = (
                f"charles_1967_table_{table.lower()}_sio2_"
                f"partial_molar_enthalpy_{temperature}"
            )
            source_row = source_rows_by_id[observation_id]
            printed_value = Decimal(source_row["values"]["value"])
            if source_row["units"] == "kcal/mole":
                expected_value = printed_value * THERMOCHEMICAL_CALORIE_J
            elif source_row["units"] == "cal/mole":
                expected_value = (
                    printed_value * THERMOCHEMICAL_CALORIE_J / Decimal("1000")
                )
            else:
                raise AssertionError(
                    f"unexpected printed enthalpy unit: {source_row['units']}"
                )
            expected_enthalpy_values[observation_id] = expected_value
    assert len(expected_enthalpy_values) == 30
    assert set(expected_enthalpy_values) == {
        f"charles_1967_table_{table.lower()}_sio2_partial_molar_enthalpy_{temperature}"
        for table, specification in TABLES.items()
        for temperature, *_ in specification["rows"]
    }
    assert all(row.identity.quantity.is_value for row in rows.values())
    assert all(row.identity.species is not None and row.identity.species.phase.is_value for row in rows.values())

    migrated_by_id = {
        row.observation_id.split("::", 1)[1].split("::T=", 1)[0]: row
        for row in rows.values()
    }
    for table, specification in TABLES.items():
        for temperature, x1, log_activity, log_gamma, _ in specification["rows"]:
            stem = f"charles_1967_table_{table.lower()}_sio2"
            activity = migrated_by_id[f"{stem}_activity_{temperature}"]
            coefficient = migrated_by_id[f"{stem}_activity_coefficient_{temperature}"]
            enthalpy_row = migrated_by_id[f"{stem}_partial_molar_enthalpy_{temperature}"]
            composition = migrated_by_id[
                f"charles_1967_table_{table.lower()}_liquidus_composition_{temperature}"
            ]
            assert activity.value.kind is ValueKind.POINT
            assert activity.value.point == Decimal(10) ** Decimal(log_activity)
            assert Decimal(0) < activity.value.point <= Decimal(1)
            assert coefficient.value.kind is ValueKind.POINT
            assert coefficient.value.point == Decimal(10) ** Decimal(log_gamma)
            assert enthalpy_row.value.kind is ValueKind.POINT
            enthalpy_id = f"{stem}_partial_molar_enthalpy_{temperature}"
            assert enthalpy_row.value.point == expected_enthalpy_values[enthalpy_id]
            assert composition.value.kind is ValueKind.POINT
            assert composition.value.point == Decimal(x1.strip("()*"))
            for migrated in (activity, coefficient, enthalpy_row, composition):
                assert migrated.identity.quantity.is_value
                assert migrated.identity.species.phase.value.value == "l"
                assert migrated.identity.temperature_K.is_value
                assert migrated.identity.temperature_K.value == Decimal(temperature)
                assert migrated.point_conditions["temperature_K"].state.value == Decimal(temperature)
                assert migrated.point_conditions["temperature_K"].locator.page == specification["page"]
                assert migrated.point_conditions["temperature_K"].locator.table == table
                assert migrated.identity.composition.is_value
                expected_composition = {
                    specification["oxide"]: Decimal(x1.strip("()*")),
                    "SiO2": Decimal(1) - Decimal(x1.strip("()*")),
                }
                assert dict(migrated.identity.composition.value.components) == expected_composition
                assert migrated.point_conditions["composition"].state.value.as_map() == expected_composition
                assert migrated.point_conditions["composition"].locator.page == specification["page"]
                assert migrated.point_conditions["composition"].locator.table == table
                assert not migrated.identity.total_pressure_Pa.is_value
                assert migrated.identity.fO2_Pa is None or not migrated.identity.fO2_Pa.is_value
                pressure = migrated.point_conditions.get("total_pressure_Pa")
                oxygen = migrated.point_conditions.get("fO2_Pa")
                assert pressure is None or not pressure.state.is_value
                assert oxygen is None or not oxygen.state.is_value
            assert activity.identity.quantity.value is Quantity.ACTIVITY
            assert coefficient.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
            assert enthalpy_row.identity.quantity.value is Quantity.PARTIAL_MOLAR_ENTHALPY
            assert activity.locator.page == specification["page"]
            assert activity.locator.table == table
            assert coefficient.locator.page == specification["page"]
            assert enthalpy_row.locator.page == specification["page"]

    for table in TABLES:
        assert sum(
            key.startswith(f"charles_1967_table_{table.lower()}_")
            and "_sio2_activity_" in key
            and "_activity_coefficient_" not in key
            for key in migrated_by_id
        ) == 10

    assert sum(
        row.identity.quantity.value is Quantity.ACTIVITY for row in rows.values()
    ) == 30
    gamma_1573 = migrated_by_id[
        "charles_1967_table_ii_sio2_activity_coefficient_1573"
    ].value.point
    assert gamma_1573 == Decimal(10) ** Decimal(".0333")
    assert gamma_1573 > 1
    gamma_1973_table_iii = migrated_by_id[
        "charles_1967_table_iii_sio2_activity_coefficient_1973"
    ].value.point
    assert gamma_1973_table_iii == Decimal(1)

    # The printed references are bound on separate source records, including the
    # key alkali-oxide liquid standards needed to interpret the figure-only data.
    contexts = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    li = contexts["charles_1967_li2o_reference_state"]
    na = contexts["charles_1967_na2o_reference_state_and_figure_activity"]
    k = contexts["charles_1967_k2o_reference_state"]
    assert li["values"]["standard_state_as_printed"] == (
        "where the activity a2 is referred to pure liquid silica at the temperature of "
        "interest as the standard state (similarly, lithia activity a1 is referred to "
        "pure liquid Li2O at the temperature of interest as standard state)."
    )
    assert li["locator"]["page"] == 2 and li["locator"]["equation"] == "4"
    assert na["values"]["standard_state_as_printed"] == (
        "The present Na2O activity data (referred to pure liquid Na2O at the temperature "
        "of interest as the standard state and calculated in a somewhat indirect fashion "
        "from the Na2O-SiO2 phase diagram and volatility and cell potential data)"
    )
    assert na["locator"]["page"] == 8 and na["locator"]["figure"] == "16"
    assert k["values"]["standard_state_as_printed"] == (
        "Using Eq. (23) and the K2O modification of Eq. (3), the volatilization results "
        "in Fig. 18 may be converted to K2O activity coefficient data referred to a "
        "standard state of pure K2O(l)."
    )
    assert k["locator"]["page"] == 9 and k["locator"]["figure"] == "20"
    assert na["values"]["method_class"] == k["values"]["method_class"] == "figure_only"
    fit = contexts["charles_1967_printed_fit_equations"]["values"]
    free_energies = fit["explicit_free_energy_values_as_printed"]
    assert len(free_energies) == 2
    assert free_energies[0]["value"] == (
        "From 1000° to 1900°K, ΔF_m per mole of lithium metasilicate solution "
        "varies between -19 and -16 kcal/mole."
    )
    assert free_energies[0]["locator"]["page"] == 3
    assert free_energies[1]["value"] == (
        "Over an appreciable temperature range the values given show a negligible "
        "change with temperature and may be approximated by the value of -80 kcal/mole of glass."
    )
    assert free_energies[1]["locator"]["page"] == 9
    thermochemical_inputs = {
        item["name"]: item["value_as_printed"]
        for item in fit["thermochemical_inputs_as_printed"]
    }
    assert thermochemical_inputs == {
        "silica_heat_of_fusion_selected": "1835 cal/mole",
        "silica_heat_of_fusion_alternative": "approximately 2100 cal/mole",
        "cristobalite_melting_point_used": "1986 K",
        "silica_heat_capacity_difference_A": "-1.02",
        "silica_heat_capacity_difference_B": "1.64(10^-3)",
        "silica_heat_capacity_difference_C": "-3.45(10^6)",
        "cristobalite_tridymite_transition_temperature": "1743 K",
        "cristobalite_tridymite_transition_heat": "about 50 cal/mole",
        "solid_silica_heat_capacity": "Cp(tridymite or cristobalite) = 14.40 + 2.04(10^-3)T",
        "liquid_silica_heat_capacity": "Cp(liquid silica) = 13.38 + 3.68(10^-3)T - 3.45(10^5)T^-2",
    }
    assert all(item["locator"]["page"] == 2 for item in fit["thermochemical_inputs_as_printed"])
    assert na["T_C"] == "1200"
    equation_locators = {
        row["equation"]: row["locator"]
        for row in fit["equations_as_printed"]
    }
    assert set(equation_locators) == {"3", "4", "5", "6", "7", "8", "13", "14", "15", "20", "21", "22", "23"}
    assert all(locator.get("page") and locator.get("equation") == equation for equation, locator in equation_locators.items())
    candidates = fit["figure_only_candidates"]
    assert [item["figure"] for item in candidates] == [str(number) for number in range(1, 21)]
    assert [(item["page"], item["published_page"]) for item in candidates] == [
        (2, 632), (3, 633), (3, 633), (4, 634), (4, 634), (4, 634),
        (5, 635), (5, 635), (5, 635), (6, 636), (6, 636), (6, 636),
        (7, 637), (7, 637), (7, 637), (8, 638), (9, 639), (9, 639),
        (9, 639), (9, 639),
    ]
    assert all("numeric_points" not in item for item in candidates)


def test_compose_script_is_idempotent_on_the_committed_extract(tmp_path: Path) -> None:
    """Re-running the committed composition script must not change the extract.

    The extract names compose.py as the method that derives its binary compositions. An earlier
    version popped ``printed_form`` from the 60 log10 activity / activity-coefficient rows, so a
    re-run silently turned the migrated activities back into the printed logs (ror-chne P0,
    ror-chdy P1). Running it on a copy must leave every leaf identical.
    """
    import subprocess
    import sys

    script = ROOT / "data/literature/extracts/audits/charles-1967-alkali-silicate-activities-compose.py"
    copy = tmp_path / "charles.yaml"
    copy.write_bytes(EXTRACT.read_bytes())
    subprocess.run([sys.executable, str(script), str(copy)], check=True, cwd=ROOT, capture_output=True)
    before = yaml.safe_load(EXTRACT.read_text())
    after = yaml.safe_load(copy.read_text())
    assert after == before
