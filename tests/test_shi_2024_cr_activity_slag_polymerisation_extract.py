from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shi-2024-cr-activity-slag-polymerisation.yaml"
START_COMPONENTS = ["CaO", "CaF2", "MgO", "Al2O3", "SiO2", "MnO", "CrO1.5"]
START_GRID = [
    ("1", ["34.80", "5.00", "10.00", "10.00", "35.18", "5.00", "0.02"]),
    ("2", ["34.80", "5.00", "10.00", "10.00", "35.15", "5.00", "0.05"]),
    ("3", ["34.75", "5.00", "10.00", "10.00", "35.13", "5.00", "0.12"]),
    ("4", ["34.70", "5.00", "10.00", "10.00", "35.10", "5.00", "0.20"]),
    ("5", ["34.65", "5.00", "10.00", "10.00", "35.00", "5.00", "0.35"]),
    ("6", ["34.50", "5.00", "10.00", "10.00", "35.00", "5.00", "0.50"]),
    ("7", ["34.00", "5.00", "10.00", "10.00", "34.00", "5.00", "2.00"]),
]
TABLE2 = [
    ("1", "0.00035563", "0.00018353"),
    ("2", "0.00059631", "0.00013209"),
    ("3", "0.0012434", "0.00028697"),
    ("4", "0.0018547", "0.00013020"),
    ("5", "0.0032208", "0.00022581"),
    ("6", "0.0040383", "0.00029830"),
]
TABLE3 = [
    ("1", "0.0021415", "0.0076852", "6.0218", "41.875"),
    ("2", "0.0023339", "0.0083755", "3.9139", "63.410"),
    ("3", "0.0048069", "0.017250", "3.8660", "60.113"),
    ("4", "0.0060899", "0.021855", "3.2835", "167.85"),
    ("5", "0.011488", "0.041226", "3.5668", "182.57"),
    ("6", "0.012816", "0.045992", "3.1736", "154.18"),
]


def _assert_grid(actual, expected):
    assert actual == expected


def test_shi_tables_migrate_printed_values_species_and_run_conditions():
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = migrator.result.observations

    assert len(migrated) == 36
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    extract_rows = {}
    for formula, block in source["species"].items():
        for item in block["observations"]:
            for point in item["values"]["points"]:
                assert point["value"] != "unavailable"
                record = next(
                    row for row in migrated.values()
                    if row.observation_id.startswith(
                        "shi-2024-cr-activity-slag-polymerisation::" + item["observation_id"] + "::"
                    )
                )
                assert record.value.kind is ValueKind.POINT
                assert record.value.point == Decimal(point["value"])
                assert record.identity.species.formula == formula
                assert record.identity.temperature_K.is_value
                assert record.identity.temperature_K.value == Decimal("1823")
                assert record.point_conditions["temperature_K"].state.value == Decimal("1823")
                oxygen = record.point_conditions["fO2_Pa"].state.value
                assert oxygen.point == Decimal("0.0000026040525")
                if item["values"]["quantity"] in {"activity", "activity_coefficient"}:
                    assert record.identity.fO2_Pa.is_value
                    assert record.identity.fO2_Pa.value == Decimal("0.0000026040525")
                    assert record.identity.composition is not None
                    assert record.identity.composition.is_value
                    assert record.point_conditions["composition"].state.value.proxy_flag == "initial_charge_only"
                    assert item["standard_state"] in {
                        "pure liquid CrO(l), explicit in Eq. (8)",
                        "pure solid CrO1.5(s), explicit in Eq. (11)",
                    }
                    reference = record.identity.reference_state
                    assert reference.is_value
                    assert reference.value.endmember.formula == formula
                    # The reference reaction explicitly prints CrO1.5(s); its
                    # solid reference is represented by the schema's crystal phase.
                    assert reference.value.endmember.phase.value.value == ("l" if formula == "CrO" else "cr")
                assert record.locator.page == item["locator"]["page"]
                assert record.point_conditions["composition"].locator.page == 3
                if item["values"]["quantity"] == "equilibrium_phase_composition":
                    assert record.identity.composition is not None
                    assert not record.identity.composition.is_value
                assert point["sample"] == item["experiment"].rsplit("_", 1)[1]
                assert point["species_as_printed"] == formula
                assert item["phase"] == "l"
                assert item["regime"] == "quench_equilibration"
                extract_rows[(item["observation_id"], point["sample"])] = (item, point)

    # Full Table 1 grid from an independent 600 dpi render transcription.
    actual_start = {}
    for sample, _ in START_GRID[:6]:
        candidates = [
            (item, point) for (oid, row_sample), (item, point) in extract_rows.items()
            if row_sample == sample and item["values"]["quantity"] == "activity"
        ]
        assert candidates
        raw = candidates[0][1]["point_conditions"]["composition"]["state"]["value"]["components"]
        actual_start[sample] = dict(raw)
    actual_start_grid = [(sample, [actual_start[sample][c] for c in START_COMPONENTS]) for sample, _ in START_GRID[:6]]
    _assert_grid(actual_start_grid, START_GRID[:6])

    row7 = next(
        item for item in source["species"]["CrO1.5"]["context"]
        if item["observation_id"] == "shi2024_table1_sample7_starting_composition_context"
    )
    actual_start_grid.append(("7", [row7["values"]["components_as_printed"][c] for c in START_COMPONENTS]))
    _assert_grid(actual_start_grid, START_GRID)

    # Table 2 mole fractions and Table 3 activities / activity coefficients,
    # independently transcribed from the 600 dpi page 4 and 5 renders.
    actual_t2 = []
    actual_t3 = []
    for sample, *_ in TABLE3:
        values = {}
        for formula, block in source["species"].items():
            for item in block["observations"]:
                point = item["values"]["points"][0]
                if point["sample"] == sample:
                    values[(formula, item["values"]["quantity"])] = point["value"]
        actual_t2.append((sample, values[("CrO", "equilibrium_phase_composition")], values[("CrO1.5", "equilibrium_phase_composition")]))
        actual_t3.append((sample, values[("CrO", "activity")], values[("CrO1.5", "activity")], values[("CrO", "activity_coefficient")], values[("CrO1.5", "activity_coefficient")]))
    _assert_grid(actual_t2, [(n, a, b) for n, a, b in TABLE2])
    _assert_grid(actual_t3, TABLE3)

    table4 = next(
        item for item in source["species"]["SiO2"]["context"]
        if item["observation_id"] == "shi2024_table4_qi_raman_characteristics_context"
    )
    assert [(row["raman_shift_cm-1"], row["Si"]) for row in table4["values"]["rows"]] == [
        ("850–880", "1"), ("900–920", "0.514"), ("950–980", "0.242"),
        ("1050–1100", "0.09"), ("1060 and 1190", "0.015"),
    ]
    table5 = next(
        item for item in source["species"]["SiO2"]["context"]
        if item["observation_id"] == "shi2024_table5_raman_deconvolution_results_context"
    )
    assert [tuple(row[key] for key in ("X_CrOx", "Q0", "Q1", "Q2", "BO_per_Si")) for row in table5["values"]["rows"]] == [
        ("0.000539", "0.352", "0.480", "0.168", "1.175"),
        ("0.000728", "0.157", "0.458", "0.385", "1.228"),
        ("0.00153", "0.373", "0.383", "0.244", "1.299"),
        ("0.00198", "0.307", "0.578", "0.115", "1.124"),
        ("0.00345", "0.348", "0.652", "0.000", "0.785"),
        ("0.00434", "0.404", "0.596", "0.000", "0.742"),
    ]

    # Mutation proof: an independent table transcription and an extract cell
    # mutation each fail the same exact grid assertion.
    changed_grid = deepcopy(TABLE3)
    changed_grid[0] = (changed_grid[0][0], "0.0021416", *changed_grid[0][2:])
    with pytest.raises(AssertionError):
        _assert_grid(actual_t3, changed_grid)
    changed_extract = deepcopy(actual_t3)
    changed_extract[2] = (changed_extract[2][0], *changed_extract[2][1:3], "3.8661", *changed_extract[2][4:])
    with pytest.raises(AssertionError):
        _assert_grid(changed_extract, TABLE3)

    assert all("points" not in item["values"] for block in source["species"].values() for item in block.get("context", []) if item.get("type") == "figure_only")
    assert {item["locator"]["figure"] for block in source["species"].values() for item in block.get("context", []) if item.get("type") == "figure_only"} == {"1", "2", "3", "4", "5", "6", "7"}
