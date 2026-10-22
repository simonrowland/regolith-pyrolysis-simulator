from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.migrate import fe3_fe2_ratio_from_ferric_fraction
from tests.battery.factories import migrate_one_extract

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/leister-ehrt-2000-polyvalent-redox.yaml"

# Independent transcription of Table 1 from the 600 dpi PDF page 5 render.
RENDER_GRID = [
    (1, "Glass A", 1600, "Air", 80, 20),
    (1, "Glass B", 1600, "Air", 76, 24),
    (1, "Glass C", 1600, "Air", 87, 13),
    (2, "Glass A", 1600, "Air", 71, 29),
    (2, "Glass B", 1600, "Air", 55, 45),
    (2, "Glass C", 1600, "Air", 81, 19),
    (3, "Glass A", 1800, "Ar", 40, 60),
    (3, "Glass B", 1800, "Ar", 40, 60),
    (3, "Glass C", 1800, "Ar", 78, 22),
    (4, "Glass B", 2000, "Ar", 25, 75),
    (4, "Glass C", 2000, "Ar", 75, 25),
    (5, "Glass B", 1800, "H2/N2", 9, 91),
    (5, "Glass C", 1800, "H2/N2", 10, 90),
]


def _load(path: Path = EXTRACT):
    return yaml.safe_load(path.read_text())


def _transcribed_grid(source):
    points = [
        point
        for observation in source["species"]["Fe"]["observations"]
        for point in observation["values"]["points"]
        if "table_row_as_published" in point
    ]
    points.sort(
        key=lambda point: (
            point["table_row_as_published"],
            point["sample_id_as_published"],
        )
    )
    return [
        (
            point["table_row_as_published"],
            point["sample_id_as_published"],
            point["T_C"],
            point["atmosphere_as_published"],
            point["Fe3_over_FeT_percent_as_published"],
            point["Fe2_over_FeT_percent_as_published"],
        )
        for point in points
    ]


def test_render_grid_and_missing_cells_match_independent_transcription():
    source = _load()
    assert _transcribed_grid(source) == RENDER_GRID
    missing = source["species"]["Fe"]["context"][0]["values"]["cells"]
    assert [(cell["row"], cell["sample_id_as_published"], cell["printed_value"]) for cell in missing] == [
        (4, "Glass A", "-"),
        (5, "Glass A", "-"),
    ]


def test_untyped_valence_tables_keep_each_rendered_cell():
    source = _load()
    expected = {
        "V": [
            (1600, "Air", [95, 5, 0], [84, 16, 0], [93, 7, 0]),
            (1600, "Ar", [89, 11, 0], [54, 46, 0], [73, 22, 5]),
            (1800, "Ar", [81, 19, 0], [29, 71, 0], [66, 28, 6]),
            (2000, "Ar", [63, 27, 0], [22, 78, 0], [64, 29, 7]),
            (1600, "red.", [52, 48, 0], ["<0.5", 28, 72], ["<2", 28, 72]),
        ],
        "Cr": [
            ("1600 (1700, C)", 1600, 1600, 1700, "Air", [40, 60], [6, 94], [3, 97, 0]),
            (1600, 1600, 1600, 1600, "Ar", [10, 90], [4, 96], ["<1", ">99", 0]),
            (1800, 1800, 1800, 1800, "Ar", [7, 93], ["<1", ">99"], [0, 97, 3]),
            (2000, 2000, 2000, 2000, "Ar", [5, 95], ["<1", ">99"], [0, 92, 8]),
            (1800, 1800, 1800, 1800, "H2/N2", [0, 100], [0, 100], [0, 62, 38]),
        ],
        "Ti": [
            (1600, "Air", [100, 0], [100, 0], [100, 0]),
            (1600, "Ar", [100, 0], [100, 0], [100, 0]),
            (1800, "Ar", [100, 0], [100, 0], [">99", "<1"]),
            (2000, "Ar", [100, 0], [">99", "<1"], [95, 5]),
            (1800, "H2/N2", [96, 4], [93, 7], [89, 11]),
        ],
    }
    v_points = source["species"]["V"]["context"][0]["values"]["points"]
    assert [
        (
            row["T_C"], row["atmosphere_as_published"],
            row["values_percent"]["A"], row["values_percent"]["B"], row["values_percent"]["C"],
        )
        for row in v_points
    ] == expected["V"]

    cr_points = source["species"]["Cr"]["context"][0]["values"]["points"]
    assert [
        (
            row.get("row_as_printed", row.get("T_C")), row.get("A_T_C", row.get("T_C")),
            row.get("B_T_C", row.get("T_C")), row.get("C_T_C", row.get("T_C")),
            row["atmosphere_as_published"], row["values_percent"]["A"],
            row["values_percent"]["B"], row["values_percent"]["C"],
        )
        for row in cr_points
    ] == expected["Cr"]

    ti_points = source["species"]["Ti"]["context"][0]["values"]["points"]
    assert [
        (
            row["T_C"], row["atmosphere_as_published"],
            row["values_percent"]["A"], row["values_percent"]["B"], row["values_percent"]["C"],
        )
        for row in ti_points
    ] == expected["Ti"]
    assert all(
        source["species"][formula]["context"][0]["values"]["candidate_quantity_tokens"]
        == ["fe3_fe2_ratio"]
        for formula in ("V", "Cr", "Ti")
    )
    v_points = source["species"]["V"]["context"][0]["values"]["points"]
    assert v_points[0]["prose_duplicate_locator"]["page"] == 5
    assert v_points[0]["row_note"].endswith("this table cell is the same result.")
    assert v_points[3]["prose_duplicate_locator"]["page"] == 5
    assert "22:78" in v_points[3]["row_note"]
    assert "<0.5%" in v_points[4]["row_note"] and "less than 1%" in v_points[4]["row_note"]
    cr_prose = next(
        record
        for record in source["species"]["Cr"]["context"]
        if record["observation_id"] == "leister_2000_cr_prose_summaries"
    )
    assert [
        summary.get("printed_amount")
        for summary in cr_prose["values"]["printed_summaries"]
        if "printed_amount" in summary
    ] == ["≥90%", "nearly 100%"]
    assert cr_prose["values"]["printed_summaries"][1]["approximate"] is True
    cr_table = source["species"]["Cr"]["context"][0]["values"]["points"][0]
    assert cr_table["prose_duplicate_locator"]["page"] == 7
    assert "40%" in cr_table["row_note"]
    ti_prose = next(
        record
        for record in source["species"]["Ti"]["context"]
        if record["observation_id"] == "leister_2000_ti_prose_fraction"
    )
    assert ti_prose["values"]["printed_amount"] == "1-5%"
    assert ti_prose["locator"]["page"] == 8


def test_migrated_fe_values_species_temperature_atmosphere_and_composition():
    source = _load()
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert len(observations) == len(RENDER_GRID) + 1
    assert all(o.identity.species.formula == "Fe" for o in observations)
    assert all(o.identity.species.phase.value.value == Phase.GLASS.value for o in observations)
    assert all(o.identity.quantity.value is Quantity.FE3_FE2_RATIO for o in observations)
    assert all(o.value.kind is ValueKind.POINT for o in observations)
    assert all(o.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for o in observations)
    assert all(not o.notices for o in observations)
    assert all(
        observation_source["derivation"]["raw_inputs"]["state"]["value"]
        == "not_printed"
        and observation_source["derivation"]["raw_inputs"]["locator"]["page"] == 2
        for observation_source in source["species"]["Fe"]["observations"]
    )

    source_points = [
        point
        for observation_source in source["species"]["Fe"]["observations"]
        for point in observation_source["values"]["points"]
    ]
    by_identity = {}
    for point, observation in zip(source_points, observations):
        atmosphere = point["atmosphere_as_published"]
        composition = observation.identity.composition
        assert composition is not None and composition.is_value
        composition_map = composition.value.as_map()
        expected_compositions = {
            "Glass A": {"Na2O": Decimal("0.33"), "SiO2": Decimal("0.67")},
            "Glass B": {"Na2O": Decimal("0.15"), "SiO2": Decimal("0.85")},
            "Glass C": {
                "CaO": Decimal("0.10"),
                "BaO": Decimal("0.10"),
                "Al2O3": Decimal("0.15"),
                "SiO2": Decimal("0.65"),
            },
        }
        assert composition_map == expected_compositions[point["sample_id_as_published"]]
        assert observation.point_conditions["T_C"].state.value == Decimal(str(point["T_C"]))
        assert observation.point_conditions["atmosphere"].state.value == atmosphere
        assert observation.point_conditions["T_C"].locator.page == point["locator"]["page"]
        assert observation.point_conditions["T_C"].locator.pdf_page_index == point["locator"]["pdf_page_index"]
        assert observation.point_conditions["atmosphere"].locator.page == point["locator"]["page"]
        expected_ratio = fe3_fe2_ratio_from_ferric_fraction(
            Decimal(str(point["Fe3_over_FeT"]))
        )
        assert observation.value.point == expected_ratio
        key = (
            point.get("table_row_as_published", point.get("source_type")),
            point["sample_id_as_published"],
        )
        assert key not in by_identity
        by_identity[key] = observation
    assert len(by_identity) == 14
    prose = next(point for point in source_points if point.get("source_type") == "prose measurement")
    assert (prose["T_C"], prose["Fe3_over_FeT_percent_as_published"], prose["printed_valence_pair"]) == (1500, 83, "83:17")


def test_one_cell_mutation_in_grid_or_extract_fails_render_pin():
    source = _load()
    mutated_grid = deepcopy(RENDER_GRID)
    mutated_grid[0] = (*mutated_grid[0][:-2], 79, 21)
    with pytest.raises(AssertionError):
        assert _transcribed_grid(source) == mutated_grid

    mutated_extract = deepcopy(source)
    mutated_extract["species"]["Fe"]["observations"][0]["values"]["points"][0][
        "Fe3_over_FeT_percent_as_published"
    ] = 79
    with pytest.raises(AssertionError):
        assert _transcribed_grid(mutated_extract) == RENDER_GRID
