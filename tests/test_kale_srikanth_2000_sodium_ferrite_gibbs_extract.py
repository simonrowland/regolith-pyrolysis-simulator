from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Polymorph, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kale-srikanth-2000-sodium-ferrite-gibbs.yaml"
SOURCE_ID = "kale-srikanth-2000-sodium-ferrite-gibbs"
AUDIT = ROOT / "data/literature/extracts/audits/kale-srikanth-2000-sodium-ferrite-gibbs.audit.md"

TABLE_I_RENDER_ROWS = [
    ("Shaiu et al. [11]", "−171 970 − 9.456T", (795, 1048)),
    ("Yamaguchi et al. [8], 930–1047 K", "−160 200 − 3.909T", (930, 1047)),
    ("Yamaguchi et al. [8], 1047–1278 K", "−152 700 − 1.332T", (1047, 1278)),
    ("Yamaguchi et al. [8], 1278–1405 K", "−147 300 − 13.37T", (1278, 1405)),
    ("Zhang et al. [1], 635–785 K", "−237 425 + 83.1T", (635, 785)),
    ("Zhang et al. [1], 834–1004 K", "−247 086 + 89.435T‡", (834, 1004)),
    ("Dai et al. [10]", "−86 000 − 61.89T", (773, 1030)),
]


def _document() -> dict[str, object]:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_printed_table_rows_and_rendered_equations_detect_mutation() -> None:
    document = _document()
    errors = validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert errors == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    observations = document["species"]["NaFeO2"]["observations"]
    table_rows = [row for row in observations if row["locator"].get("table") == "I"]
    assert len(table_rows) == 7
    actual = [
        (
            row["locator"]["row"],
            row["values"]["equation_as_printed"],
            tuple(row["T_range_K"]),
        )
        for row in table_rows
    ]
    assert actual == TABLE_I_RENDER_ROWS
    assert all(row["locator"]["page"] == 2 for row in table_rows)
    assert all(row["units"] == "J/mol" for row in table_rows)
    assert [row["values"]["T_range_as_printed"] for row in table_rows] == [
        "795–1048 K", "930–1047 K", "1047–1278 K", "1278–1405 K",
        "635–785 K", "834–1004 K", "773–1030 K",
    ]
    assert all(
        row["values"]["formula_as_published"] == "Na2Fe2O4"
        for row in table_rows
    )
    assert all(
        row["values"]["reference_state_as_printed"] == "Na2O and Fe2O3"
        for row in table_rows
    )

    eq10 = observations[-1]
    assert eq10["locator"] == {
        "page": 4,
        "equation": "10",
        "published_page": 178,
    }
    assert eq10["values"]["equation_as_printed"] == "−232 582 + 69.61T"
    assert eq10["values"]["uncertainty_as_printed"] == "±5500 J/mol"
    assert eq10["values"]["polymorph_as_printed"] == "α-Na2Fe2O4"
    assert eq10["T_range_K"] == [930, 998]

    mutated = deepcopy(document)
    mutated["species"]["NaFeO2"]["observations"][0]["values"][
        "equation_as_printed"
    ] = "−171 971 − 9.456T"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table I")


def test_table_iii_inventory_and_transition_values_match_render() -> None:
    document = _document()
    context = document["species"]["NaFeO2"]["context"]
    table = next(
        row for row in context
        if row["observation_id"] == "kale_srikanth_1999_table3_equilibration"
    )
    rows = table["values"]["phase_equilibration_rows"]
    assert table["values"]["table_body_row_count_as_printed"] == 14
    assert len(rows) == 13
    assert [
        (row["equilibration_temperature_K"], row["equilibration_time_h"])
        for row in rows
    ] == [
        (923, 24), (948, 48), (973, 24), (998, 48), (1023, 24), (1048, 24),
        (1073, 48), (1098, 48), (1123, 48), (1148, 48), (1173, 48), (1198, 48),
        (973, 72),
    ]
    assert rows[5]["minor_phases_as_printed"] == "Na3Fe5O9, β-Na2Fe2O4"
    assert rows[6]["minor_phases_as_printed"] == "β-Na2Fe2O4"
    assert rows[12]["initial_composition_as_printed"] == "Na2CO3 + Fe2O3 (1:2)"

    boundary = next(
        row for row in context
        if row["observation_id"] == "kale_srikanth_1999_transition_context"
    )
    assert boundary["values"]["current_phase_equilibration_boundary_as_printed"] == "1048 ± 10 K"
    assert boundary["values"]["current_emf_segment_intersection_as_printed"] == "1028 ± 5 K"
    assert boundary["values"]["thery_boundary_as_cited"] == "1030 ± 5 K"


def test_zhang_cell_two_comparison_is_pinned_to_page_five_render() -> None:
    audit = AUDIT.read_text(encoding="utf-8")
    assert (
        "Zhang et al.'s liquid-sodium-reference cell “2” is ~5–10 kJ/mol more "
        "positive than both the present result and Zhang's result using "
        "α-Al2O3 + Na-β-Al2O3 as the reference electrode (absolute PDF page "
        "5, printed page 179, left column)."
    ) in audit
    assert (
        "The source's within ±5 kJ/mol agreement statement applies only to "
        "the present result versus Yamaguchi et al. [8] in the measurement "
        "range."
    ) in audit
    assert (
        "That relative band has no temperature range in the sentence, and no "
        "absolute cell-2 value or residual was calculated."
    ) in audit


def test_isolated_migration_preserves_ranges_and_refuses_only_equation_values() -> None:
    document = _document()
    source_rows = document["species"]["NaFeO2"]["observations"]
    expected_ids = {row["observation_id"] for row in source_rows}

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    assert len(result.observations) == len(source_rows) == 8

    migrated = [
        result.observations[f"{SOURCE_ID}::{row['observation_id']}"]
        for row in source_rows
    ]
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)
    assert all(row.identity.quantity.value is Quantity.DELTA_FG for row in migrated)
    assert all(row.identity.species.formula == "Na2Fe2O4" for row in migrated)
    assert all(
        row.identity.species.phase.value is Phase.CR for row in migrated
    )
    assert migrated[-1].identity.species.polymorph.value is Polymorph.ALPHA

    temperature_queue = [
        row for row in result.queue
        if "temperature_K" in row.axes
        and row.observation_id is not None
        and row.observation_id.split("::")[-1] in expected_ids
    ]
    assert len(temperature_queue) == 8
    assert {
        row.observation_id.split("::")[-1] for row in temperature_queue
    } == expected_ids

    for row in migrated:
        assert row.identity.temperature_K.is_unknown
        assert "no midpoint invented" in row.identity.temperature_K.reason
        for axis in (
            row.identity.standard_pressure_Pa,
            row.identity.total_pressure_Pa,
            row.identity.fO2_Pa,
            row.identity.composition,
        ):
            assert axis is None or not axis.is_value
        assert row.point_conditions is None

    # Delta-fG does not use composition, total-pressure, or fO2 identity axes.
    # The first three temperatures remain unknown points because only ranges
    # are printed; the exact ranges are sent to the queue without a midpoint.
    for row in migrated[:3]:
        assert row.identity.temperature_K.is_unknown
        assert row.identity.composition.is_not_applicable
        assert row.identity.total_pressure_Pa.is_not_applicable
        assert row.identity.fO2_Pa.is_not_applicable
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated) == 8
