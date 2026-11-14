from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_utils import isolated_root
from tools.validate_literature_extracts import validate_extract_document


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/nagahara-1993-sio2-evaporation-equilibrium-vacuum.yaml"
)
EXPECTED_CONTEXT_IDS = {
    "nagahara_1993_experimental_conditions_and_sample",
    "nagahara_1993_c_capsule_vs_mo_equilibrium_rate",
    "nagahara_1993_vacuum_vs_equilibrium_rate",
    "nagahara_1993_prior_vacuum_equilibrium_rate_ratio",
    "nagahara_1993_h2_1e7_bar_rate_comparison",
    "nagahara_1993_h2_1e5_bar_rate_comparison",
    "nagahara_1993_h2_rate_time_behavior",
    "nagahara_1993_figure_1_figure_only",
    "nagahara_1993_figure_2_figure_only",
}


def _assert_source_pins(document: dict) -> None:
    assert document["species"]["SiO2"]["observations"] == []
    context = document["species"]["SiO2"]["context"]
    assert len(context) == 9
    by_id = {row["observation_id"]: row for row in context}
    assert set(by_id) == EXPECTED_CONTEXT_IDS

    conditions = by_id["nagahara_1993_experimental_conditions_and_sample"]["values"]
    assert conditions["experimental_temperature_C"] == [1600, 1700]
    assert conditions["vacuum_chamber_base_pressure_bar_bounds_as_printed"] == [
        "10^-11",
        "10^-9",
    ]
    assert conditions["starting_material_as_printed"] == (
        "The starting material is a single crystal of natural quartz."
    )
    assert conditions["sample_mass_as_printed"] == (
        "About 10mg of powdered sample was used for each experiment."
    )

    c_vs_mo = by_id["nagahara_1993_c_capsule_vs_mo_equilibrium_rate"]["values"]
    assert c_vs_mo["rate_factor"] == 3.5
    assert c_vs_mo["rate_factor_as_printed"] == "about 3.5 times more rapid"

    vacuum = by_id["nagahara_1993_vacuum_vs_equilibrium_rate"]["values"]
    assert vacuum["gas_system"] == "vacuum"
    assert vacuum["rate_factor"] == 0.02
    assert vacuum["abstract_comparison_as_printed"] == (
        "about two orders of magnitude smaller than equilibrium"
    )
    assert "total_pressure" not in vacuum

    prior = by_id["nagahara_1993_prior_vacuum_equilibrium_rate_ratio"]["values"]
    assert prior["rate_factor_range"] == ["0.011", "0.015"]
    assert prior["temperature_C_range"] == ["1560", "1685"]

    low_h2 = by_id["nagahara_1993_h2_1e7_bar_rate_comparison"]["values"]
    high_h2 = by_id["nagahara_1993_h2_1e5_bar_rate_comparison"]["values"]
    assert low_h2["gas_system"] == high_h2["gas_system"] == "H2"
    assert low_h2["hydrogen_pressure_bar"] == 1.0e-7
    assert high_h2["hydrogen_pressure_bar"] == 1.0e-5
    assert low_h2["rate_relation_as_printed"] == "nearly the same as the rate in vacuum"
    assert high_h2["rate_factor_range"] == ["3", "4"]
    exact_sample = (
        "The starting material is a single crystal of natural quartz. "
        "About 10mg of powdered sample was used for each experiment."
    )
    assert c_vs_mo["sample_as_printed"] == exact_sample
    assert vacuum["sample_as_printed"] == exact_sample
    assert low_h2["sample_as_printed"] == exact_sample
    assert high_h2["sample_as_printed"] == exact_sample
    assert high_h2["rate_factor_comparator_note"].startswith(
        "not named in the detailed sentence"
    )
    assert "total_pressure" not in low_h2
    assert "total_pressure" not in high_h2

    figure1 = by_id["nagahara_1993_figure_1_figure_only"]["values"]
    figure2 = by_id["nagahara_1993_figure_2_figure_only"]["values"]
    expected_figure1_legend = [
        "solid circle: 1600°C equilibrium",
        "cross: 1600°C equilibrium in C-capsule",
        "diamond: 1600°C free evaporation",
        "circle: 1600°C in 10^-7 bar hydrogen gas",
        "square: 1600°C in 10^-5 bar hydrogen gas",
        "plus: 1700°C equilibrium in C-capsule",
        "triangle: 1700°C free evaporation",
    ]
    assert figure1["plotted_series_conditions_as_printed"] == expected_figure1_legend
    assert figure1["evidence_class"] == figure2["evidence_class"] == "figure_only"
    assert figure1["point_values"] == figure2["point_values"] == "not transcribed"
    assert figure2["hydrogen_evaporation_log_log_slope_as_printed"] == "1/2"

    hydrogen_comparison = by_id["nagahara_1993_h2_rate_time_behavior"]["values"]
    assert hydrogen_comparison["time_relation_as_printed"] == (
        "In hydrogen gas, the evaporated fraction is not a linear function of time. "
        "In hydrogen gas, evaporated fraction becomes smaller than that in equilibrium, "
        "and the difference from equilibrium value becomes larger with time."
    )
    assert hydrogen_comparison["hydrogen_vs_higher_total_pressure_statement_as_printed"] == (
        "Contrary to higher total pressure, vaporization is more effective in hydrogen gas."
    )
    assert hydrogen_comparison["overall_equilibrium_vacuum_hydrogen_comparison_as_printed"] == (
        "The present study suggests that evaporation rate of minerals in hydrogen gas is "
        "neither of that in equilibrium, that in vacuum, nor that in between them."
    )

    sample_by_id = {row["observation_id"]: row for row in document["fidelity_samples"]}
    for observation_id, field, expected, expected_page in (
        ("nagahara_1993_c_capsule_vs_mo_equilibrium_rate", "rate_factor", 3.5, 1),
        ("nagahara_1993_vacuum_vs_equilibrium_rate", "rate_factor", 0.02, 1),
        (
            "nagahara_1993_prior_vacuum_equilibrium_rate_ratio",
            "rate_factor_range",
            ["0.011", "0.015"],
            1,
        ),
        (
            "nagahara_1993_h2_1e7_bar_rate_comparison",
            "hydrogen_pressure_bar",
            1.0e-7,
            2,
        ),
        (
            "nagahara_1993_h2_1e5_bar_rate_comparison",
            "rate_factor_range",
            ["3", "4"],
            2,
        ),
        (
            "nagahara_1993_figure_1_figure_only",
            "plotted_series_conditions_as_printed",
            expected_figure1_legend,
            2,
        ),
        (
            "nagahara_1993_figure_2_figure_only",
            "hydrogen_evaporation_log_log_slope_as_printed",
            "1/2",
            2,
        ),
    ):
        row = by_id[observation_id]
        sample = sample_by_id[observation_id]
        assert row["values"][field] == expected
        assert sample["field"] == field
        assert sample["value"] == expected
        assert sample["locator"]["page"] == expected_page
        assert row["locator"] == sample["locator"]


def test_nagahara_1993_context_matches_render_and_migrates_without_scored_rows(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_pins(document)
    assert validate_extract_document(
        document,
        expected_source_id="nagahara-1993-sio2-evaporation-equilibrium-vacuum",
    ) == []

    root = isolated_root(tmp_path, document)
    extract = root / "data/literature/extracts" / f"{document['source_id']}.yaml"
    migrator = Migrator(root, index={}, aliases={})
    migrator._migrate_extract(extract)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 0
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.context_by_work
    migrated_context = [
        row for rows in result.context_by_work.values() for row in rows
    ]
    assert {row["observation_id"] for row in migrated_context} == EXPECTED_CONTEXT_IDS
    assert len(migrated_context) == 9
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 0
    assert {
        row.observation_id
        for row in result.observations.values()
        if row.quantity.is_unknown
    } == set()
    assert {
        row.observation_id
        for row in result.observations.values()
        if row.identity.species.phase.is_unknown
    } == set()


def test_nagahara_1993_fidelity_pins_reject_rate_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_pins(document)

    mutated = deepcopy(document)
    row = next(
        row
        for row in mutated["species"]["SiO2"]["context"]
        if row["observation_id"] == "nagahara_1993_vacuum_vs_equilibrium_rate"
    )
    row["values"]["rate_factor"] = 0.2
    with pytest.raises(AssertionError):
        _assert_source_pins(mutated)
