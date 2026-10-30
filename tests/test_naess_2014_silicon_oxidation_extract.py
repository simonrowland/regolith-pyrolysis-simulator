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
    14: 408,
    18: 412,
}


def _assert_render_pins(document: dict) -> None:
    for observation_id, path, expected, pdf_page, published_page in RENDER_PINS:
        record = _context(document, observation_id)
        assert _at_path(record, path) == expected
        assert record["locator"]["page"] == pdf_page
        assert record["locator"]["published_page"] == published_page
        assert record["locator"]["pdf_page_index"] == pdf_page - 1


def _assert_locator(record: dict, page: int, published_page: int) -> None:
    assert record["locator"]["page"] == page
    assert record["locator"]["published_page"] == published_page
    assert record["locator"]["pdf_page_index"] == page - 1


def _assert_review_fix_pins(document: dict) -> None:
    prose_fit = _context(document, "naess14_fig3_dry_air_regression")
    _assert_locator(prose_fit, 7, 401)
    assert "figure" not in prose_fit["locator"]
    figure_fit = prose_fit["values"]
    assert "R_squared_as_printed" not in figure_fit
    assert figure_fit["flat_plate_comparison_R_squared_as_printed"] == "0.76"
    for figure_number in (1, 2, 6):
        figure = _context(document, f"naess14_figure_{figure_number}_deferred")
        assert "non_data_annotations_as_printed" not in figure["values"]

    partial_pressure = _context(
        document, "naess14_model_derived_siO_partial_pressure_and_transport"
    )
    _assert_locator(partial_pressure, 10, 404)
    diffusion = _context(document, "naess14_model_derived_diffusion_coefficients")
    _assert_locator(diffusion, 11, 405)
    assert diffusion["values"]["SiO_diffusion_coefficient_at_1550C_as_printed"] == (
        "3.9 × 10^-4 m^2 s^-1"
    )
    assert diffusion["values"]["O2_diffusion_coefficient_at_1550C_as_printed"] == (
        "4.7 × 10^-4 m^2 s^-1"
    )
    sio_boundary = _context(
        document, "naess14_model_derived_sio_boundary_layer_thickness"
    )
    _assert_locator(sio_boundary, 11, 405)
    assert sio_boundary["values"]["SiO_boundary_layer_thickness_as_printed"] == (
        "<0.5 mm at all gas flow rates"
    )
    o2_boundary = _context(
        document, "naess14_model_derived_o2_boundary_layer_thickness"
    )
    _assert_locator(o2_boundary, 14, 408)
    assert o2_boundary["values"]["O2_boundary_layer_thickness_at_26_m_s_as_printed"] == (
        "3.25 mm"
    )

    humid_air = _context(
        document, "naess14_cfd_humid_air_oxygen_consumption_comparison"
    )
    _assert_locator(humid_air, 8, 402)
    assert humid_air["values"]["humid_air_at_16_m_s_as_printed"] == (
        "6 % versus 4 % for dry air"
    )
    assert humid_air["values"]["non_significance_note_as_printed"] == (
        "The humid-air increase was not statistically significant."
    )
    dry_air = _context(document, "naess14_cfd_oxygen_consumption_summary")
    _assert_locator(dry_air, 10, 404)
    assert "humid_air_at_16_m_s_as_printed" not in dry_air["values"]
    assert "non_significance_note_as_printed" not in dry_air["values"]
    assert dry_air["values"]["dry_air_at_5_m_s_as_printed"] == "9 % average"
    assert dry_air["values"]["dry_air_at_26_m_s_as_printed"] == "5 % average"
    assert dry_air["values"]["air_argon_1_4_at_26_m_s_as_printed"] == (
        "38 % average versus 9 % at 5 m s^-1 dry air"
    )


def test_naess_table_rows_and_render_pins() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_review_fix_pins(document)

    context = document["species"]["SiO2"]["context"]
    ids = [row["observation_id"] for row in context]
    assert len(ids) == len(set(ids))
    assert document["scope_assessment"]["context_records"] == len(context) == 47
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


@pytest.mark.parametrize(
    "regression",
    (
        "figure_r_squared",
        "figure_1_annotation",
        "figure_2_annotation",
        "figure_6_annotation",
        "r2_page",
        "diffusion_page",
        "boundary_wording",
        "o2_boundary_page",
        "humid_cfd_page",
    ),
)
def test_naess_review_fix_pins_reject_regressions(regression: str) -> None:
    document = deepcopy(_document())
    if regression == "figure_r_squared":
        _context(document, "naess14_fig3_dry_air_regression")["values"][
            "R_squared_as_printed"
        ] = "0.91"
    elif regression.startswith("figure_"):
        figure_number = regression.split("_")[1]
        _context(document, f"naess14_figure_{figure_number}_deferred")["values"][
            "non_data_annotations_as_printed"
        ] = "returned annotation"
    elif regression == "r2_page":
        _context(document, "naess14_fig3_dry_air_regression")["locator"]["page"] = 8
    elif regression == "diffusion_page":
        _context(document, "naess14_model_derived_diffusion_coefficients")["locator"][
            "page"
        ] = 10
    elif regression == "boundary_wording":
        _context(
            document, "naess14_model_derived_sio_boundary_layer_thickness"
        )["values"]["SiO_boundary_layer_thickness_as_printed"] = (
            "approximately 0.5 mm at all gas flow rates"
        )
    elif regression == "o2_boundary_page":
        _context(document, "naess14_model_derived_o2_boundary_layer_thickness")[
            "locator"
        ]["page"] = 10
    else:
        _context(
            document, "naess14_cfd_humid_air_oxygen_consumption_comparison"
        )["locator"]["page"] = 10

    with pytest.raises(AssertionError):
        _assert_review_fix_pins(document)


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
