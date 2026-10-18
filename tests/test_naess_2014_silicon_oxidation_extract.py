from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/naess-2014-liquid-silicon-oxidation-fume-flux.yaml"


def _document() -> dict:
    loaded = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _context(document: dict, observation_id: str) -> dict:
    rows = document["species"]["SiO2"]["context"]
    matches = [row for row in rows if row["observation_id"] == observation_id]
    assert len(matches) == 1
    return matches[0]


def _at_path(value: dict, path: tuple[str, ...]) -> object:
    current: object = value
    for key in path:
        assert isinstance(current, dict)
        current = current[key]
    return current


RENDER_PINS = (
    # 600 dpi PDF p. 9, Table 4, experiment 1.
    (
        "naess14_table4_experiment_01",
        ("values", "measured_or_calculated_results", "measured_flux_JSiO2_header_scale_10^-3_mol_m^-2_s^-1"),
        "0.98",
        9,
        403,
    ),
    # 600 dpi PDF p. 9, Table 4, experiment 11.
    (
        "naess14_table4_experiment_11",
        ("values", "measured_or_calculated_results", "measured_flux_JSiO2_header_scale_10^-3_mol_m^-2_s^-1"),
        "5.99",
        9,
        403,
    ),
    # 600 dpi PDF p. 9, Table 4, experiment 17.
    (
        "naess14_table4_experiment_17",
        ("values", "measured_or_calculated_results", "measured_flux_JSiO2_header_scale_10^-3_mol_m^-2_s^-1"),
        "11.34",
        9,
        403,
    ),
    # 600 dpi PDF p. 12, Table 5, measured flux at 5 m/s.
    (
        "naess14_table5_measured_flux_by_dry_air_velocity",
        ("values", "gas_velocity_at_nozzle_exit_m_s", "5"),
        "1.2",
        12,
        406,
    ),
    # PDF p. 2 prose quotes the approximate mean diameter from prior work.
    (
        "naess14_prior_fume_characterisation_and_industrial_flux",
        ("values", "mean_diameter_as_printed"),
        "~60 nm",
        2,
        396,
    ),
    # PDF p. 11 prose quotes the industrial particle-size range from prior work.
    (
        "naess14_quoted_industrial_particle_diameter_range",
        ("values", "industrial_particle_diameter_range_as_printed"),
        "56–66 nm",
        11,
        405,
    ),
)

PUBLISHED_FOOTERS = {
    2: 396,
    3: 397,
    4: 398,
    5: 399,
    6: 400,
    7: 401,
    8: 402,
    9: 403,
    10: 404,
    11: 405,
    12: 406,
    13: 407,
    18: 412,
}


def _assert_render_pins(document: dict) -> None:
    for observation_id, path, expected, pdf_page, published_page in RENDER_PINS:
        record = _context(document, observation_id)
        assert _at_path(record, path) == expected
        assert record["locator"]["page"] == pdf_page
        assert record["locator"]["published_page"] == published_page
        assert record["locator"]["pdf_page_index"] == pdf_page - 1


def test_naess_table_rows_and_render_pins() -> None:
    document = _document()
    _assert_render_pins(document)

    context = document["species"]["SiO2"]["context"]
    ids = [row["observation_id"] for row in context]
    assert len(ids) == len(set(ids))
    for row in context:
        locator = row["locator"]
        page = locator["page"]
        assert locator["published_page"] == PUBLISHED_FOOTERS[page]
        assert locator["pdf_page_index"] == page - 1
    evidence_classes = {member.value for member in EvidenceClass}
    assert all(
        row["values"].get("method_class") in evidence_classes
        for row in context
        if row["values"].get("method_class") is not None
    )
    table4 = [row for row in context if row["observation_id"].startswith("naess14_table4_experiment_")]
    assert len(table4) == 17
    assert {row["values"]["experiment_number_as_printed"] for row in table4} == {
        str(number) for number in range(1, 18)
    }
    assert document["species"]["SiO2"]["observations"] == []

    table2 = _context(document, "naess14_table2_experimental_matrix")["values"]
    assert table2["counts_as_printed"]["Diluted 3:2"]["26"] == "2"
    assert "Table 4 prints three" in _context(
        document, "naess14_experimental_apparatus_and_common_temperature"
    )["values"]["table2_table4_diluted_3_2_count_note"]


def test_naess_figures_are_deferred_without_numeric_points() -> None:
    document = _document()
    context = document["species"]["SiO2"]["context"]
    figures = [row for row in context if row["type"] == "figure_record"]
    assert len(figures) == 7
    for number in range(1, 8):
        figure = _context(document, f"naess14_figure_{number}_deferred")
        values = figure["values"]
        assert values["quantity_as_printed"]
        assert values["species"]
        assert values["phase"]
        assert values["temperature_as_printed"]
        assert values["axes_as_printed"]
        assert values["caption_as_printed"]
        assert values["panel"]
        assert values["attribution"]
        assert values["digitisation"] == "deferred to a figure pass"
        assert isinstance(values["series"], list)
        assert all("marker_count" in series for series in values["series"])
        assert not any(key in values for key in ("points", "numeric_points", "data_points"))

    assert [row["marker_count"] for row in _context(document, "naess14_figure_3_deferred")["values"]["series"]] == [2, 2, 2]
    assert [row["marker_count"] for row in _context(document, "naess14_figure_4_deferred")["values"]["series"]] == [8, 2, 2, 2, 3]
    assert [row["marker_count"] for row in _context(document, "naess14_figure_5_deferred")["values"]["series"]] == [0, 0, 0, 0]


def test_naess_render_pin_rejects_a_changed_cell() -> None:
    document = deepcopy(_document())
    _context(document, "naess14_table4_experiment_17")["values"]["measured_or_calculated_results"][
        "measured_flux_JSiO2_header_scale_10^-3_mol_m^-2_s^-1"
    ] = "11.35"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_naess_isolated_migration_preserves_context_without_typed_substitutes(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path, "naess-2014-liquid-silicon-oxidation-fume-flux.yaml"
    )
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 0
    assert sum(len(rows) for rows in result.context_by_work.values()) == len(
        _document()["species"]["SiO2"]["context"]
    )
