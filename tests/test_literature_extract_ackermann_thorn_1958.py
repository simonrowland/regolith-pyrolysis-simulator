from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import migrate as battery_migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "data/literature/extracts/ackermann-thorn-1958-volatile-oxides-anl5824.yaml"

TABLE_I_RENDER = [
    ("Al2O3", "tungsten", "2544", "1.23 ± 0.01"),
    ("Al2O3", "tungsten", "2582", "1.16 ± 0.01"),
    ("Al2O3", "tungsten", "2635", "1.13 ± 0.01"),
    ("Al2O3", "tantalum", "2523", "0.19 ± 0.01"),
    ("MgO", "tungsten", "2045", "0.66 ± 0.01"),
    ("UO2", "tungsten", "1600 to 2500", "≥50"),
    ("UO2", "tantalum", "2100 to 2500", "≥50"),
    ("ThO2", "tungsten", "2765", "≥50"),
    ("ThO2", "tantalum", "2753", "3.06"),
    ("BeO", "tungsten", "2462", "0.92"),
    ("ZrO2", "tungsten", "2600", "≥50"),
    ("ZrO2", "tantalum", "2560", "0.89 ± 0.02"),
]

TABLE_II_RENDER = [
    ("10.4", "1800", "2005", "8.458", "5.0727", "4.988"),
    ("8.8", "2100", "1977", "6.091", "5.2153", "5.058"),
    ("8.6", "4500", "1922", "2.739", "5.5624", "5.203"),
    ("9.7", "4740", "1925", "2.934", "5.5325", "5.195"),
    ("6.2", "1800", "1956", "4.980", "5.3028", "5.112"),
    ("7.3", "900", "2019", "11.91", "4.9241", "4.953"),
    ("8.0", "1841", "1975", "6.311", "5.1999", "5.063"),
    ("7.8", "1200", "1996", "9.494", "5.0255", "5.010"),
]

TABLE_III_RENDER = [
    ("2526", "1.7", "*", "3600", "1.90 × 10^-6 atm", "2.54 × 10^-2 cm^2"),
    ("2434", "1.2", "*", "7200", "6.56 × 10^-7 atm", "2.54 × 10^-2 cm^2"),
    ("2745", "13.0", "*", "1800", "3.02 × 10^-5 atm", "2.54 × 10^-2 cm^2"),
    ("2632", "11.3", "**", "3600", "9.43 × 10^-6 atm", "3.46 × 10^-2 cm^2"),
]


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate_extract(path):
    migrator = battery_migrate.Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(path)
    migrator.finalize()
    return migrator.result


def _observations_by_id(result):
    return {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }


def _assert_formation_limits_are_refused(result, document):
    observations = _observations_by_id(result)
    for species, observation_id, printed_bound in (
        ("ThO", "ackermann_thorn_1958_tho_formation_at_2500k", "≥ approximately −53 kcal/mole^-1"),
        ("UO", "ackermann_thorn_1958_uo_formation_lower_limit_at_2500k", "≥ −42 kcal mole^-1"),
    ):
        source = next(
            row
            for row in document["species"][species]["observations"]
            if row["observation_id"] == observation_id
        )
        assert observations[observation_id].value.kind.value == "unavailable"
        assert observations[observation_id].value.point is None
        assert source["values"]["admission_status"] == "typed_refusal"
        assert source["values"]["status"] == "typed_refusal"
        assert "delta_fG" not in source["values"]
        assert source["values"]["bound_as_printed"] == printed_bound


def _table_i_grid(document):
    record = next(
        row
        for row in document["species"]["Al2O"]["context"]
        if row["observation_id"] == "ackermann_thorn_1958_table_i_al2o3_products"
    )
    return [
        (
            row["oxide"],
            row["cell"],
            row.get("T_K", row.get("T_range_K_as_printed")),
            row["weight_ratio_as_printed"],
        )
        for row in record["values"]["rows_as_printed"]
    ]


def _table_ii_grid(document):
    rows = sorted(
        (
            row
            for row in document["species"]["WO3"]["observations"]
            if row["observation_id"].startswith("ackermann_thorn_1958_table_ii_experiment_")
        ),
        key=lambda row: int(row["locator"]["row"]),
    )
    return [
        (
            row["values"]["weight_loss_of_cell_and_contents_mg_as_printed"],
            row["values"]["time_s_as_printed"],
            row["values"]["T_K"],
            row["values"]["pressure_as_printed"],
            row["values"]["negative_log10_pressure_as_printed"],
            row["values"]["10^4_over_T_as_printed"],
        )
        for row in rows
    ]


def _table_iii_grid(document):
    record = next(
        row
        for row in document["species"]["ZrO2"]["context"]
        if row["observation_id"] == "ackermann_thorn_1958_table_iii_zro2_total_volatility"
    )
    return [
        (
            row["T_K"],
            row["weight_loss_mg_as_printed"],
            row["weight_loss_footnote_marker_as_printed"],
            row["time_s_as_printed"],
            row["pressure_as_printed"],
            row["orifice_area_as_printed"],
        )
        for row in record["values"]["rows_as_printed"]
    ]


def test_render_transcriptions_pin_all_three_tables_and_cell_assignments():
    document = _document()
    table_i = _table_i_grid(document)
    table_ii = _table_ii_grid(document)
    table_iii = _table_iii_grid(document)

    assert table_i == TABLE_I_RENDER
    assert len(table_i) == 12
    assert next(
        row
        for row in document["species"]["Al2O"]["context"]
        if row["observation_id"] == "ackermann_thorn_1958_table_i_al2o3_products"
    )["values"]["printed_table_body_rows"] == 8
    assert table_ii == TABLE_II_RENDER
    assert len(table_ii) == 8
    assert table_iii == TABLE_III_RENDER
    assert len(table_iii) == 4
    table_iii_record = next(
        row
        for row in document["species"]["ZrO2"]["context"]
        if row["observation_id"] == "ackermann_thorn_1958_table_iii_zro2_total_volatility"
    )
    assert table_iii_record["values"]["pressure_footnote_marker_as_printed"] == "***"

    mutated = deepcopy(document)
    mutated_row = next(
        row
        for row in mutated["species"]["WO3"]["observations"]
        if row["observation_id"] == "ackermann_thorn_1958_table_ii_experiment_1"
    )
    mutated_row["values"]["pressure_as_printed"] = "8.459"
    with pytest.raises(AssertionError):
        assert _table_ii_grid(mutated) == TABLE_II_RENDER


def test_isolated_migration_maps_printed_axes_and_keeps_unprinted_conditions_unknown():
    document = _document()
    result = _migrate_extract(EXTRACT)
    assert len(result.observations) == 18
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind.value == "point" for row in result.observations.values()) == 9
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 9
    _assert_formation_limits_are_refused(result, document)

    observations = _observations_by_id(result)
    for number, row in enumerate(TABLE_II_RENDER, start=1):
        expected_temperature = row[2]
        observation = observations[f"ackermann_thorn_1958_table_ii_experiment_{number}"]
        identity = observation.identity
        assert identity.temperature_K.is_value
        assert identity.temperature_K.value == Decimal(expected_temperature)
        assert not identity.total_pressure_Pa.is_value
        assert not identity.fO2_Pa.is_value
        assert observation.locator.page == 13
        assert observation.locator.table == "II"
    for number, expected_temperature in ((1, "2005"), (3, "1922"), (6, "2019")):
        observation = observations[f"ackermann_thorn_1958_table_ii_experiment_{number}"]
        assert observation.identity.temperature_K.value == Decimal(expected_temperature)
    assert observations["ackermann_thorn_1958_table_ii_experiment_1"].identity.composition.is_value
    assert observations["ackermann_thorn_1958_table_ii_experiment_1"].identity.composition.value.components == (
        ("MgO", Decimal("1")),
    )
    mixture = observations["ackermann_thorn_1958_table_ii_experiment_6"]
    assert not mixture.identity.composition.is_value
    mixture_source = next(
        row
        for row in _document()["species"]["WO3"]["observations"]
        if row["observation_id"] == "ackermann_thorn_1958_table_ii_experiment_6"
    )
    assert "mixture" in mixture_source["values"]["charge_as_printed"]
    assert observations["ackermann_thorn_1958_table_ii_experiment_1"].value.point == Decimal(
        "0.857006850"
    )
    assert observations["ackermann_thorn_1958_table_ii_experiment_6"].value.point == Decimal(
        "1.20678075"
    )

    fit = observations["ackermann_thorn_1958_eq_23_wo3_pressure_fit"]
    expected_parents = tuple(
        f"{document_id}::ackermann_thorn_1958_table_ii_experiment_{number}"
        for number in range(1, 9)
    )
    assert fit.derived_from == expected_parents
    assert fit.locator.page == 19
    assert fit.locator.equation == "23"

    table_i_context = next(
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row["observation_id"] == "ackermann_thorn_1958_table_i_al2o3_products"
    )
    table_i_rows = table_i_context["values"]["rows_as_printed"]
    merged_tantalum_locator = table_i_rows[3]["point_conditions"]["temperature_K"][
        "locator"
    ]
    last_row_tantalum_locator = table_i_rows[11]["point_conditions"]["temperature_K"][
        "locator"
    ]
    assert (
        merged_tantalum_locator["page"],
        merged_tantalum_locator["published_page"],
        merged_tantalum_locator["table"],
        merged_tantalum_locator["row"],
        merged_tantalum_locator["row_label"],
        merged_tantalum_locator["column"],
        merged_tantalum_locator["column_header"],
    ) == (10, 8, "I", "1", "Al2O3", "Tantalum", "T (°K)")
    assert (
        last_row_tantalum_locator["page"],
        last_row_tantalum_locator["published_page"],
        last_row_tantalum_locator["table"],
        last_row_tantalum_locator["row"],
        last_row_tantalum_locator["row_label"],
        last_row_tantalum_locator["column"],
        last_row_tantalum_locator["column_header"],
    ) == (10, 8, "I", "8", "ZrO2", "Tantalum", "T (°K)")

    extract = _document()
    assert extract["species"]["Al2O"]["observations"][0]["locator"] == {
        "page": 18,
        "published_page": 16,
        "equation": "17",
    }
    assert extract["species"]["Al2O"]["observations"][0]["values"]["functions"][
        "delta_fG_as_printed"
    ] == "−47200 − 9.27T cal/mole"
    assert extract["species"]["WO3"]["observations"][1]["values"]["functions"][
        "delta_fG_as_printed"
    ] == "−75649 − 1.650T log T + 20.20T + 38 × 10^-5 T^2 cal/mole"
    assert extract["species"]["TaO2"]["observations"][0]["locator"]["page"] == 22
    assert extract["species"]["TaO2"]["observations"][0]["values"]["functions"][
        "delta_fG_as_printed"
    ] == "−6.650T + 0.505T log T + 9.773 × 10^-4 T^2 + 721825/T − 40845 cal/mole"
    tao2_fidelity = next(
        sample
        for sample in extract["fidelity_samples"]
        if sample["observation_id"] == "ackermann_thorn_1958_eq_38_tao2_formation"
    )
    assert tao2_fidelity["value"]["delta_fG_as_printed"].startswith("−6.650T")


def test_lower_limit_point_mutation_is_caught_by_migration_assertions(tmp_path):
    document = _document()
    for species, observation_id, scalar in (
        ("ThO", "ackermann_thorn_1958_tho_formation_at_2500k", "-53"),
        ("UO", "ackermann_thorn_1958_uo_formation_lower_limit_at_2500k", "-42"),
    ):
        source = next(
            row
            for row in document["species"][species]["observations"]
            if row["observation_id"] == observation_id
        )
        values = source["values"]
        values.pop("admission_status")
        values.pop("status")
        values.pop("quantity_as_printed")
        values.pop("bound_as_printed")
        values.pop("reason")
        values["delta_fG"] = scalar
        values["bound"] = "lower_limit"

    mutant_extract = tmp_path / EXTRACT.name
    mutant_extract.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    mutant_result = _migrate_extract(mutant_extract)
    with pytest.raises(AssertionError):
        _assert_formation_limits_are_refused(mutant_result, document)


document_id = "ackermann-thorn-1958-volatile-oxides-anl5824"
