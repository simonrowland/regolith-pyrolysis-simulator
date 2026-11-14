from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import PhaseRole, Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "saini-2023-sulfur-vanadium-borosilicate-structure"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_borosilicate_measurements_are_preserved_and_redox_is_not_overstated() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    species = document["species"]["V"]
    context = species["context"]

    table_1 = next(row for row in context if row["observation_id"] == "saini_2023_table_1_glass_compositions")
    assert all("measured" not in row for row in table_1["values"]["sulfur_free_rows"])
    assert all("measured" not in row for row in table_1["values"]["sulfur_containing_rows"])
    typed_vectors = {}
    typed_points = []
    for formula, component_species in document["species"].items():
        for observation in component_species["observations"]:
            if observation["type"] != "equilibrium_phase_composition":
                continue
            assert observation["values"]["phase_label"] == "glass"
            assert observation["values"]["basis"] == "wt_percent"
            for point in observation["values"]["points"]:
                typed_points.append(point)
                typed_vectors.setdefault(point["sample"], {})[formula] = point["value_as_published"]

    assert [
        (sample, {formula: float(value) for formula, value in typed_vectors[sample].items()})
        for sample in ("0V", "1V", "2.5V", "5V", "7.5V", "9V")
    ] == [
        ("0V", {"Na2O": 28.95, "B2O3": 16.26, "SiO2": 46.82, "Al2O3": 7.94}),
        ("1V", {"Na2O": 28.14, "B2O3": 15.83, "SiO2": 45.52, "Al2O3": 7.73, "V2O5": 2.77}),
        ("2.5V", {"Na2O": 27.01, "B2O3": 15.16, "SiO2": 43.61, "Al2O3": 7.40, "V2O5": 6.76}),
        ("5V", {"Na2O": 25.20, "B2O3": 14.16, "SiO2": 40.73, "Al2O3": 6.92, "V2O5": 12.98}),
        ("7.5V", {"Na2O": 23.57, "B2O3": 13.24, "SiO2": 38.06, "Al2O3": 6.46, "V2O5": 18.67}),
        ("9V", {"Na2O": 22.64, "B2O3": 12.71, "SiO2": 36.56, "Al2O3": 6.20, "V2O5": 21.88}),
    ]
    assert [
        (sample, float(typed_vectors[sample]["SO3"]))
        for sample in ("1V-1.25S", "2.5V-1.75S", "5V-3S", "7.5V-6S", "9V-6.5S")
    ] == [
        ("1V-1.25S", 1.51),
        ("2.5V-1.75S", 2.06),
        ("5V-3S", 3.39),
        ("7.5V-6S", 6.47),
        ("9V-6.5S", 6.83),
    ]
    assert len(typed_points) == 59
    assert "SO3" not in typed_vectors["0V"]
    run_compositions = {}
    phase_assemblage_points = []
    for point in typed_points:
        condition = point["point_conditions"]["composition"]
        assert condition["state"]["tag"] == "value"
        assert condition["state"]["value"]["amount_basis"] == "mass_percent"
        assert condition["state"]["value"]["semantics"] == "nominal_as_printed"
        assert condition["locator"]["table"] == "1"
        run_composition = dict(condition["state"]["value"]["components"])
        prior = run_compositions.setdefault(point["sample"], run_composition)
        assert prior == run_composition
        assert point["point_conditions"]["fO2_Pa"]["state"] == {
            "tag": "unknown",
            "reason": "No oxygen fugacity or synthesis atmosphere is reported.",
        }
        phase_assemblage = point["point_conditions"].get("phase_assemblage")
        if point["sample"] == "0V":
            assert phase_assemblage is None
        elif point["sample"].endswith("S"):
            assert phase_assemblage is not None
            assert phase_assemblage["state"] == {
                "tag": "value",
                "value": "All the sulfur-containing glasses (within the solubility limit) are XRD amorphous, as shown in Fig. 8a.",
            }
            assert phase_assemblage["locator"]["figure"] == "8"
            assert phase_assemblage["locator"]["panel"] == "a"
            assert phase_assemblage["locator"]["page"] == 9
            phase_assemblage_points.append(point)
        else:
            assert phase_assemblage is not None
            assert phase_assemblage["state"] == {
                "tag": "value",
                "value": "All xV glasses have been found to be XRD amorphous, as shown in Fig. 2a.",
            }
            assert phase_assemblage["locator"]["figure"] == "2"
            assert phase_assemblage["locator"]["panel"] == "a"
            assert phase_assemblage["locator"]["page"] == 5
            phase_assemblage_points.append(point)
    assert len(phase_assemblage_points) == 55
    expected_run_compositions = {
        "0V": {
            "Na2O": "28.97", "B2O3": "16.27", "SiO2": "46.81",
            "Al2O3": "7.94", "V2O5": "0", "SO3": "0",
        },
        "1V": {
            "Na2O": "28.16", "B2O3": "15.82", "SiO2": "45.51",
            "Al2O3": "7.72", "V2O5": "2.78", "SO3": "0",
        },
        "2.5V": {
            "Na2O": "27.00", "B2O3": "15.17", "SiO2": "43.62",
            "Al2O3": "7.40", "V2O5": "6.77", "SO3": "0",
        },
        "5V": {
            "Na2O": "25.21", "B2O3": "14.16", "SiO2": "40.74",
            "Al2O3": "6.91", "V2O5": "12.98", "SO3": "0",
        },
        "7.5V": {
            "Na2O": "23.56", "B2O3": "13.23", "SiO2": "38.07",
            "Al2O3": "6.46", "V2O5": "18.68", "SO3": "0",
        },
        "9V": {
            "Na2O": "22.63", "B2O3": "12.71", "SiO2": "36.56",
            "Al2O3": "6.20", "V2O5": "21.89", "SO3": "0",
        },
        "1V-1.25S": {
            "Na2O": "26.89", "B2O3": "15.76", "SiO2": "45.35",
            "Al2O3": "7.70", "V2O5": "2.77", "SO3": "1.53",
        },
        "2.5V-1.75S": {
            "Na2O": "25.27", "B2O3": "15.10", "SiO2": "43.44",
            "Al2O3": "7.37", "V2O5": "6.74", "SO3": "2.08",
        },
        "5V-3S": {
            "Na2O": "22.38", "B2O3": "14.05", "SiO2": "40.42",
            "Al2O3": "6.86", "V2O5": "12.88", "SO3": "3.40",
        },
        "7.5V-6S": {
            "Na2O": "18.19", "B2O3": "13.04", "SiO2": "37.51",
            "Al2O3": "6.36", "V2O5": "18.41", "SO3": "6.48",
        },
        "9V-6.5S": {
            "Na2O": "16.97", "B2O3": "12.51", "SiO2": "36.00",
            "Al2O3": "6.11", "V2O5": "21.55", "SO3": "6.81",
        },
    }
    assert run_compositions == expected_run_compositions
    zero_v_point = next(point for point in typed_points if point["sample"] == "0V")
    assert "90%Pt –10%Rh crucibles" in zero_v_point["point_conditions"][
        "method_detail_as_printed"
    ]["state"]["value"]
    sulfur_point = next(point for point in typed_points if point["sample"] == "1V-1.25S")
    assert "Pt–Rh crucible was loaded" in sulfur_point["point_conditions"][
        "method_detail_as_printed"
    ]["state"]["value"]
    assert zero_v_point["point_conditions"]["method_detail_as_printed"]["locator"] == {
        "page": 2,
        "published_page": 2,
        "pdf_page_index": 1,
        "section": "2.1 Synthesis of sulfur-free glasses",
    }
    assert sulfur_point["point_conditions"]["method_detail_as_printed"]["locator"] == {
        "page": 3,
        "published_page": 3,
        "pdf_page_index": 2,
        "section": "2.2 Synthesis of sulfur-containing glasses",
    }
    assert all(
        point["point_conditions"]["T_C"]["state"]["value"] == "1200"
        and "atmosphere" not in point["point_conditions"]
        and point["point_conditions"]["T_C"]["locator"]["page"] == 2
        and point["point_conditions"]["method_detail_as_printed"]["locator"]["page"]
        == (3 if point["sample"].endswith("S") else 2)
        and point["point_conditions"]["method_detail_as_printed"]["locator"]["published_page"]
        == (3 if point["sample"].endswith("S") else 2)
        and point["point_conditions"]["method_detail_as_printed"]["locator"]["pdf_page_index"]
        == (2 if point["sample"].endswith("S") else 1)
        and point["locator"]["page"] == 3
        and point["locator"]["published_page"] == 3
        for point in typed_points
    )

    table_2 = next(row for row in context if row["observation_id"] == "saini_2023_table_2_aluminum_structure")
    assert [(row["glass_id"], row["AlIV_abundance_percent"], row["AlV_abundance_percent"]) for row in table_2["values"]["rows"]] == [
        ("0V", 97, 3),
        ("1V", 98, 2),
        ("2.5V", 98, 2),
        ("5V", 98, 2),
        ("7.5V", 98, 2),
        ("9V", 98, 2),
    ]

    table_3 = next(row for row in context if row["observation_id"] == "saini_2023_table_3_boron_vanadium_structure")
    assert [
        (row["glass_id"], row["BIII_abundance_percent"], row["BIV_abundance_percent"], row["V51_delta_CS_ppm"], row["V51_CQ_MHz"])
        for row in table_3["values"]["rows"]
    ] == [
        ("0V", 40.7, 59.3, None, None),
        ("1V", 41.8, 58.2, -565, 4.6),
        ("2.5V", 36.8, 63.2, -565, 5.6),
        ("5V", 35.4, 64.6, -564, 6.5),
        ("7.5V", 34.2, 65.8, -556, 6.5),
        ("9V", 38.1, 61.9, -560, 6.5),
    ]

    redox = next(row for row in context if row["observation_id"] == "saini_2023_vanadium_redox_xps_context")
    assert "517.3 eV" in redox["values"]["XPS_as_printed"]
    assert "no numeric V3+/V4+/V5+ fraction or ratio" in redox["reason"]
    assert len([row for row in context if row["type"] == "figure_record"]) == 10
    assert all(row["digitisation"] == "deferred to a figure pass" for row in context if row["type"] == "figure_record")
    assert all(row["values"].get("points") == [] for row in context if row["type"] == "figure_record")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert len(migrator.result.observations) == 59
    assert migrator.result.validation.hard_issues == ()
    migrated = [
        row for row in migrator.result.observations.values()
        if row.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
    ]
    assert len(migrated) == 59
    assert all(
        row.identity.temperature_K.value == Decimal("1473.15")
        for row in migrated
    )
    assert all(
        row.identity.phase_role.is_value
        and row.identity.phase_role.value is PhaseRole.GLASS
        for row in migrated
    )
    assert all(row.point_conditions["composition"].state.is_value for row in migrated)
    assert all(
        row.point_conditions["method_detail_as_printed"].state.is_value
        for row in migrated
    )
    assert all(
        row.point_conditions["fO2_Pa"].state.is_unknown
        and row.point_conditions["fO2_Pa"].state.reason
        == "No oxygen fugacity or synthesis atmosphere is reported."
        for row in migrated
    )
    assert all(
        row.point_conditions["T_C"].state.value == Decimal("1200")
        for row in migrated
    )
    migrated_phase_assemblages = [
        row for row in migrated if "phase_assemblage" in row.point_conditions
    ]
    assert len(migrated_phase_assemblages) == 55
    assert all(
        row.point_conditions["phase_assemblage"].state.is_value
        and "XRD amorphous" in row.point_conditions["phase_assemblage"].state.value
        for row in migrated_phase_assemblages
    )
    assert all(
        row.point_conditions["T_C"].locator.page == 2
        for row in migrated
    )
    assert all(row.locator.page == 3 and row.locator.published_page == 3 for row in migrated)
    expected_zero_v = wt_pct_to_mole_fraction(
        {
            "Na2O": Decimal("28.97"),
            "B2O3": Decimal("16.27"),
            "SiO2": Decimal("46.81"),
            "Al2O3": Decimal("7.94"),
            "V2O5": Decimal("0"),
            "SO3": Decimal("0"),
        }
    )
    zero_v = next(
        row
        for row in migrated
        if row.point_conditions["composition"].state.value == expected_zero_v
    )
    assert zero_v.point_conditions["composition"].state.value == expected_zero_v
