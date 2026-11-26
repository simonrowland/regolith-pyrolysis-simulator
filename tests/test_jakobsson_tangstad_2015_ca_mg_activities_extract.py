"""Independent render-grid and isolated migration checks for Jakobsson and Tangstad (2015)."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery import score as battery_score
from simulator.battery.enums import MethodToken, Quantity, Rail
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import validate_extract_document

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/jakobsson-tangstad-2015-ca-mg-activities.yaml"

# Independent transcription from the 600 dpi PDF renders of physical pages 2 and 4.
# Each tuple carries the printed sample ID, complete slag composition, holding time,
# concentration, and 95 pct confidence-interval half-width.
RENDER_GRID = {
    "I": [
        ["CS1", "0.6", "63.2", "36.3", "355", "173"],
        ["CS2", "3", "62.3", "37.4", "393", "162"],
        ["CS3", "6", "60.3", "39.4", "840", "276"],
        ["CS4", "9", "57.7", "42.1", "630", "117"],
        ["CS5", "6", "62.1", "37.7", "644", "214"],
        ["CS6", "6", "53.8", "45.9", "1061", "151"],
        ["CS7", "6", "48.6", "51.1", "3723", "634"],
        ["CS8", "3", "47.7", "51.9", "3903", "144"],
        ["CS9", "6", "46.3", "53.4", "4504", "1192"],
        ["CS10", "3", "44.3", "55.3", "7422", "1348"],
        ["CS11", "6", "43.1", "56.6", "14141", "469"],
        ["CS12", "18", "68.4", "31.2", "372", "63"],
        ["CS13", "6", "42.6", "56.8", "17638", "1450"],
        ["FS5", "6", "48.2", "51.3", "3006", "217"],
    ],
    "II": [
        ["MS1", "1", "65.3", "34.0", "0.41", "466", "173"],
        ["MS2", "2", "64.1", "35.2", "0.42", "586", "234"],
        ["MS3", "3", "63.8", "35.4", "0.42", "747", "76"],
        ["MS4", "6", "62.7", "36.6", "0.43", "1044", "175"],
        ["MS5", "9", "58.6", "40.6", "0.5", "1533", "318"],
        ["MS6", "6", "58.9", "40.5", "0.43", "1059", "212"],
        ["MS7", "6", "60.4", "38.6", "0.58", "818", "50"],
        ["MS8", "12", "62.2", "37", "0.58", "791", "75"],
    ],
    "III": [
        ["CMS1", "54.5", "38.6", "6.7", "734", "125", "293", "69"],
        ["CMS2", "49.6", "37.0", "13.2", "1726", "191", "1354", "204"],
        ["CMS3", "46.8", "36.7", "16.3", "3231", "436", "2846", "383"],
        ["CMS4", "45.1", "41.0", "13.6", "6264", "511", "3862", "316"],
        ["CMS5", "43.7", "52.0", "4.1", "13076", "1389", "1690", "321"],
        ["CMS6", "53.9", "16.8", "29.1", "265", "26", "1551", "216"],
        ["CMS7", "44.5", "46.2", "9.0", "8208", "986", "3632", "328"],
        ["CMS8", "49.8", "32.0", "18.0", "1710", "247", "2032", "40"],
        ["CMS9", "46.9", "44.8", "8.0", "3139", "1059", "975", "129"],
        ["CMS10", "47.6", "25.8", "26.4", "2010", "107", "5113", "487"],
        ["CMS11", "50.1", "18.0", "31.7", "656", "127", "3433", "420"],
        ["CMS12", "52.7", "9.3", "37.7", "155", "55", "2090", "234"],
        ["CMS13", "46.5", "34.4", "18.9", "5691", "703", "5373", "326"],
        ["CMS14", "45.4", "33.7", "20.7", "6215", "826", "7445", "794"],
        ["CMS15", "57.8", "32.6", "9.4", "346", "19", "282", "29"],
        ["CMS16", "61.8", "28.3", "9.6", "342", "28", "324", "42"],
        ["CMS17", "58.6", "22.0", "19.2", "327", "96", "733", "223"],
        ["CMS18", "57.0", "23.2", "19.6", "397", "69", "775", "142"],
        ["CMS19", "58.9", "11.3", "29.6", "92", "2", "905", "51"],
        ["CMS20", "61.3", "10.0", "28.4", "138", "4", "721", "166"],
    ],
}

RENDER_OUTLIERS = {
    ("CMS8", "Mg"): "2882",
    ("CMS17", "Mg"): "1848",
    ("CMS20", "Ca"): "873",
}

TABLE_FOOTNOTE_MARKERS = {
    ("I", "CS6"): "*",
    ("I", "CS7"): "*",
    ("I", "CS8"): "*",
    ("I", "CS11"): "*",
    ("I", "CS12"): "**",
    ("II", "MS6"): "*",
    ("II", "MS7"): "**",
    ("III", "CMS8"): "Mg*",
    ("III", "CMS17"): "Mg*",
    ("III", "CMS20"): "Ca*",
}

TABLE_LINE_RANGES = {
    ("I", "CS1"): "66-66", ("I", "CS2"): "67-67", ("I", "CS3"): "68-68",
    ("I", "CS4"): "69-69", ("I", "CS5"): "70-70", ("I", "CS6"): "71-71",
    ("I", "CS7"): "72-72", ("I", "CS8"): "73-73", ("I", "CS9"): "74-74",
    ("I", "CS10"): "75-75", ("I", "CS11"): "76-76", ("I", "CS12"): "77-77",
    ("I", "CS13"): "78-78", ("I", "FS5"): "79-79",
    **{("II", f"MS{i}"): f"{181 + i}-{181 + i}" for i in range(1, 9)},
    **{("III", f"CMS{i}"): f"{199 + i}-{199 + i}" for i in range(1, 21)},
}

FIGURE9_READS = {
    "MgO": [
        ("rein_chipman", "Rein and Chipman", "quoted_attributed", "prior_source_replot_not_held_out", ["0.15", "0.13", "0.12"]),
        ("morita", "Morita et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.18", "0.15", "0.13"]),
        ("kambayashi_kato", "Kambayashi and Kato", "quoted_attributed", "prior_source_replot_not_held_out", ["0.17", "0.14", "0.12"]),
        ("jung", "Jung et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.18", "0.14", "0.12"]),
        ("zaitsev", "Zaitsev et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.17", "0.14", "0.12"]),
        ("present_work", "Present work", "model_derived", "held_out_present_work", ["0.16", "0.13", "0.13"]),
    ],
    "SiO2": [
        ("rein_chipman", "Rein and Chipman", "quoted_attributed", "prior_source_replot_not_held_out", ["0.70", "0.86", "0.95"]),
        ("morita", "Morita et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.73", "0.90", "0.97"]),
        ("kambayashi_kato", "Kambayashi and Kato", "quoted_attributed", "prior_source_replot_not_held_out", ["0.72", "0.88", "0.96"]),
        ("jung", "Jung et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.70", "0.89", "0.98"]),
        ("zaitsev", "Zaitsev et al.", "quoted_attributed", "prior_source_replot_not_held_out", ["0.71", "0.88", "0.97"]),
        ("present_work", "Present work", "model_derived", "held_out_present_work", ["0.84", "0.97", "0.995"]),
    ],
}
FIGURE9_X = ["0.49", "0.53", "0.55"]

FIGURE10C_READS = [
    ("present_work", "Present work", "0.05", "model_derived", "held_out_present_work"),
    ("present_work", "Present work", "0.1", "model_derived", "held_out_present_work"),
    ("present_work", "Present work", "0.15", "model_derived", "held_out_present_work"),
    ("present_work", "Present work", "0.2", "model_derived", "held_out_present_work"),
    ("present_work", "Present work", "0.3", "model_derived", "held_out_present_work"),
    ("present_work", "Present work", "0.5", "model_derived", "held_out_present_work"),
    ("rein_chipman", "Rein and Chipman", "0.06", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("rein_chipman", "Rein and Chipman", "0.1", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("rein_chipman", "Rein and Chipman", "0.2", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("rein_chipman", "Rein and Chipman", "0.5", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("morita", "Morita et al.", "0.1", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("morita", "Morita et al.", "0.2", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("morita", "Morita et al.", "0.3", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("morita", "Morita et al.", "0.5", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.05", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.17", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.15", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.2", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.3", "quoted_attributed", "prior_source_replot_not_held_out"),
    ("jung", "Jung et al.", "0.5", "quoted_attributed", "prior_source_replot_not_held_out"),
]


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_all_locators_have_text_ranges(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "locator" and isinstance(child, dict):
                assert "line_range" in child
            _assert_all_locators_have_text_ranges(child)
    elif isinstance(value, list):
        for child in value:
            _assert_all_locators_have_text_ranges(child)


def _assert_render_grid_matches(document, grid):
    rows_by_id = {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body["observations"]
    }
    for table, rows in grid.items():
        for cells in rows:
            sample = cells[0]
            if table == "I":
                hold, sio2, cao, ca, ca_unc = cells[1:]
                compositions = {"SiO2": sio2, "CaO": cao}
                analytes = [("Ca", ca, ca_unc)]
                page, pdf_index, folio = 2, 1, 596
            elif table == "II":
                hold, sio2, mgo, cao, mg, mg_unc = cells[1:]
                compositions = {"SiO2": sio2, "MgO": mgo, "CaO": cao}
                analytes = [("Mg", mg, mg_unc)]
                page, pdf_index, folio = 4, 3, 598
            else:
                sio2, cao, mgo, ca, ca_unc, mg, mg_unc = cells[1:]
                hold = None
                compositions = {"SiO2": sio2, "CaO": cao, "MgO": mgo}
                analytes = [("Ca", ca, ca_unc), ("Mg", mg, mg_unc)]
                page, pdf_index, folio = 4, 3, 598
            for element, amount, uncertainty in analytes:
                obs_id = f"jakobsson_2015_{table.lower()}_{sample.lower()}_{element.lower()}_in_silicon"
                row = rows_by_id[obs_id]
                values = row["values"]
                assert values["value"] == int(amount)
                assert values["value_as_published"] == amount
                assert values["formula"] == element
                assert values["component_as_published"] == element
                assert values["T_K"] == "1873" and values["T_C"] == "1600"
                assert row["uncertainty"]["value"] == int(uncertainty)
                assert values["table_footnote_marker"] == TABLE_FOOTNOTE_MARKERS.get((table, sample))
                assert row["locator"] == {
                    "page": page,
                    "published_page": folio,
                    "pdf_page_index": pdf_index,
                    "table": table,
                    "row": sample,
                    "line_range": TABLE_LINE_RANGES[(table, sample)],
                    "column": f"{element} (mg/kg)",
                }
                conditions = row["point_conditions"]
                assert conditions["sample_id"]["state"]["value"] == sample
                assert conditions["temperature_K"]["state"]["value"] == "1873"
                assert conditions["temperature_K"]["locator"] == {
                    "page": 1,
                    "published_page": 595,
                    "pdf_page_index": 0,
                    "section": "Experimental",
                    "line_range": "48-50",
                }
                assert conditions["composition"]["state"]["value"] == {
                    "amount_basis": "mass_percent",
                    "components": compositions,
                }
                if hold is not None:
                    assert conditions["equilibration_time_h"]["state"]["value"] == hold
                should_refuse = sample in {"CS1", "CS2", "MS1", "MS2", "MS3"}
                assert values["admission_status"] == ("typed_refusal" if should_refuse else "admitted")


def test_independent_render_grid_binds_every_reported_cell_and_mutations_fail():
    document = _document()
    _assert_render_grid_matches(document, RENDER_GRID)

    mutated_grid = deepcopy(RENDER_GRID)
    mutated_grid["III"][13][4] = "6216"
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    row = next(
        item
        for item in mutated_extract["species"]["CaO"]["observations"]
        if item["observation_id"] == "jakobsson_2015_iii_cms14_ca_in_silicon"
    )
    row["values"]["value"] += 1
    with pytest.raises(AssertionError):
        _assert_render_grid_matches(mutated_extract, RENDER_GRID)


def test_all_typed_rows_migrate_with_species_temperature_and_conditions():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    records = list(migrator.result.observations.values())
    assert len(records) == 74
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    migrated = {record.observation_id.split("::", 1)[1]: record for record in records}
    document = _document()
    typed_rows = [
        row
        for body in document["species"].values()
        for row in body["observations"]
    ]
    assert len(typed_rows) == len(records)
    for row in typed_rows:
        value = row["values"]
        formula = value.get("formula") or row.get("formula")
        assert formula == value["component_as_published"]
        record = migrated[row["observation_id"]]
        assert record.identity.species.formula == formula
        method = migrator.result.experiments[record.experiment_id].method.value
        expected_method = (
            MethodToken.UNSPECIFIED
            if "figure9_" in row["observation_id"]
            else MethodToken.QUENCH_EQUILIBRATION
        )
        assert method is expected_method
        assert record.identity.temperature_K is not None and record.identity.temperature_K.is_value
        assert record.identity.temperature_K.value == Decimal("1873")
        assert record.point_conditions is not None
        assert all(condition.locator is not None for condition in record.point_conditions.values())
        if value["quantity"] == "equilibrium_phase_composition":
            for key in ("temperature_K", "composition", "sample_id"):
                assert key in record.point_conditions
            temperature = record.point_conditions["temperature_K"]
            assert temperature.state.is_value and temperature.state.value == Decimal("1873")
            assert temperature.locator is not None
            assert temperature.locator.pdf_page_index == 0
            assert temperature.locator.line_range == "48-50"
            composition = record.point_conditions["composition"]
            assert composition.state.is_value and composition.locator is not None
            assert record.point_conditions["sample_id"].state.value is not None
        elif value["quantity"] == "activity":
            assert record.identity.quantity.is_value
            assert record.identity.quantity.value is Quantity.ACTIVITY
            assert record.identity.reference_state is not None and record.identity.reference_state.is_value
            assert record.identity.per is not None and record.identity.per.is_value
            temperature = record.point_conditions["temperature_K"]
            assert temperature.state.is_value and temperature.state.value == Decimal("1873")
            assert temperature.locator is not None and temperature.locator.pdf_page_index in {5, 6, 8, 9}
            composition = record.point_conditions["composition"]
            assert composition.locator is not None
            if "figure9_" in row["observation_id"]:
                assert composition.state.is_value

    outlier_rows = {
        (row["values"]["sample_id"], row["values"]["formula"]): row
        for row in typed_rows
        if row["values"].get("replicate_as_published") == "excluded outlier"
    }
    assert set(outlier_rows) == set(RENDER_OUTLIERS)
    for key, printed in RENDER_OUTLIERS.items():
        row = outlier_rows[key]
        assert row["values"]["value_as_published"] == printed
        assert row["values"]["admission_status"] == "typed_refusal"
        assert "exclude it from the reported mean" in row["values"]["refusal_reason"]

    for species, body in document["species"].items():
        figures = [item for item in body["context"] if item["type"] == "figure_record"]
        for figure in figures:
            obs_id = figure["observation_id"]
            digitisation = figure["values"]["digitisation"]
            if "figure_9_" in obs_id:
                assert "present-work curve reads" in digitisation
                assert figure["values"]["figure_read_point_count"] == 3
                assert figure["values"]["context_point_count"] == 15
            elif obs_id == "jakobsson_2015_figure_10_panel_c_mgo_activity":
                assert "retained as context" in digitisation
                assert figure["values"]["figure_read_point_count"] == 0
                assert figure["values"]["context_point_count"] == 20
            elif obs_id == "jakobsson_2015_figure_10_panel_a_sio2_activity":
                assert "0.1–0.9" in digitisation
                assert "deferred" in digitisation
            else:
                assert digitisation == "deferred to a figure pass"
            assert "points" not in figure["values"]
            assert figure["values"]["species"]
            assert figure["values"]["phase"]
            assert figure["values"]["temperature_as_printed"]
            assert figure["values"]["axes_as_printed"]
            assert figure["values"]["caption"]
            assert figure["values"]["series"]
            assert all("marker_count" in item for item in figure["values"]["series"])


def test_table_compositions_match_independent_mole_fraction_conversion():
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {record.observation_id.split("::", 1)[1]: record for record in migrator.result.observations.values()}
    for table, rows in RENDER_GRID.items():
        for cells in rows:
            sample = cells[0]
            if table == "I":
                composition = {"SiO2": Decimal(cells[2]), "CaO": Decimal(cells[3])}
                elements = ["Ca"]
            elif table == "II":
                composition = {"SiO2": Decimal(cells[2]), "MgO": Decimal(cells[3]), "CaO": Decimal(cells[4])}
                elements = ["Mg"]
            else:
                composition = {"SiO2": Decimal(cells[1]), "CaO": Decimal(cells[2]), "MgO": Decimal(cells[3])}
                elements = ["Ca", "Mg"]
            for element in elements:
                obs_id = f"jakobsson_2015_{table.lower()}_{sample.lower()}_{element.lower()}_in_silicon"
                actual = migrated[obs_id].point_conditions["composition"].state.value
                assert actual == wt_pct_to_mole_fraction(composition)


def test_explicit_activity_values_remain_printed_and_have_the_stated_reference():
    document = _document()
    activities = {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body["observations"]
        if row["values"]["quantity"] == "activity"
    }
    text_activities = {key: value for key, value in activities.items() if "_text_" in key}
    assert len(text_activities) == 3
    assert text_activities["jakobsson_2015_text_sio2_activity_at_ca2sio4_saturation"]["values"]["activity_as_published"] == "0.0724"
    assert text_activities["jakobsson_2015_text_cao_activity_at_ca2sio4_saturation"]["values"]["activity_as_published"] == "3.55e-2"
    assert text_activities["jakobsson_2015_text_cao_activity_at_sio2_saturation"]["values"]["activity_as_published"] == "0.00260"
    assert text_activities["jakobsson_2015_text_sio2_activity_at_ca2sio4_saturation"]["locator"]["page"] == 6
    assert text_activities["jakobsson_2015_text_cao_activity_at_ca2sio4_saturation"]["locator"]["page"] == 7
    assert text_activities["jakobsson_2015_text_cao_activity_at_sio2_saturation"]["locator"]["page"] == 7

    context = [item for body in document["species"].values() for item in body["context"]]
    ca_gamma = next(item for item in context if item["observation_id"] == "jakobsson_2015_text_ln_henrian_gamma_ca_schema_gap")
    mg_gamma = next(item for item in context if item["observation_id"] == "jakobsson_2015_text_ln_henrian_gamma_mg_schema_gap")
    assert ca_gamma["locator"]["page"] == 7
    assert ca_gamma["locator"]["line_range"] == "427-437"
    assert mg_gamma["locator"]["page"] == 9
    assert mg_gamma["locator"]["line_range"] == "563-569"

    figure_records = {
        item["observation_id"]: item
        for item in context
        if item["type"] == "figure_record"
    }
    figure_pages_and_lines = [
        (1, 3, "142-144"),
        (2, 3, "160-163"),
        (3, 3, "171-174"),
        (4, 3, "172-174"),
        (5, 5, "288-294"),
        (6, 7, "387-389"),
        (7, 7, "385-386"),
        (8, 8, "542-543"),
        (9, 9, "555-609"),
        (10, 10, "611-615"),
    ]
    for figure, page, line_range in figure_pages_and_lines:
        matching = [item for obs_id, item in figure_records.items() if f"figure_{figure}_" in obs_id]
        assert matching
        assert all(item["locator"]["page"] == page for item in matching)
        assert all(item["locator"]["line_range"] == line_range for item in matching)
    for figure in (2, 3, 6, 7):
        matching = [item for obs_id, item in figure_records.items() if f"figure_{figure}_" in obs_id]
        assert all("series-specific temperatures" in item["values"]["temperature_as_printed"] for item in matching)


def test_figure_activity_reads_pin_every_series_label_and_standard_state():
    document = _document()
    rows = {
        row["observation_id"]: row
        for body in document["species"].values()
        for row in body["observations"]
    }
    figure_records = {
        item["observation_id"]: item
        for body in document["species"].values()
        for item in body["context"]
        if item["type"] == "figure_record"
    }
    figure10a = figure_records["jakobsson_2015_figure_10_panel_a_sio2_activity"]
    figure10a_present_series = next(
        series for series in figure10a["values"]["series"]
        if series["name"] == "present-work activity contours"
    )
    assert figure10a_present_series["evidence_class"] == "model_derived"
    coverage = {
        item["field_path"]: item
        for item in document["fidelity_samples"]
        if "field_path" in item
    }
    for row in rows.values():
        if row["values"].get("source_relation") != "held_out_present_work":
            continue
        observation_id = row["observation_id"]
        common_locator = row["locator"]
        species = row["values"]["formula"]
        assert all(condition["locator"] is common_locator for condition in row["point_conditions"].values())
        assert row["measurement_method"]["locator"] is common_locator
        assert row["uncertainty"]["locator"] is common_locator
        for field in ("values", "point_conditions"):
            entry = coverage[f"species.{species}.observations[{observation_id}].{field}"]
            assert entry["locator"] is common_locator
            if field == "point_conditions":
                assert all(condition["locator"] == common_locator for condition in entry["value"].values())

    figure9_scored_count = 0
    figure9_context_count = 0
    figure9_references = {
        "rein_chipman": "[15]",
        "morita": "[2]",
        "kambayashi_kato": "[31]",
        "jung": "[33]",
        "zaitsev": "[32]",
    }
    for species, series_rows in FIGURE9_READS.items():
        panel = "upper" if species == "SiO2" else "lower"
        axis = f"a_{species}(s)"
        figure_id = f"jakobsson_2015_figure_9_{species.lower()}_activity"
        figure = figure_records[figure_id]
        present_series = next(series for series in figure["values"]["series"] if series["name"] == "present work")
        assert present_series["evidence_class"] == "model_derived"
        assert present_series["role"] == "scored present-work observations"
        context_reads = {
            item["observation_id"]: item
            for item in figure["values"]["prior_series_context_reads"]
        }
        for slug, name, evidence, source_relation, activities in series_rows:
            for x, activity in zip(FIGURE9_X, activities, strict=True):
                observation_id = (
                    f"jakobsson_2015_figure9_{species.lower()}_{slug}_x{x.replace('.', '')}"
                )
                expected_locator = {
                    "page": 9,
                    "published_page": 603,
                    "pdf_page_index": 8,
                    "figure": "9",
                    "panel": panel,
                    "row": f"{name} at x_SiO2={x}",
                    "column": axis,
                    "line_range": "555-609",
                }
                if slug == "present_work":
                    row = rows[observation_id]
                    figure9_scored_count += 1
                    assert row["locator"] == expected_locator
                    assert row["values"]["activity_as_published"] == activity
                    assert row["values"]["digitisation"] == "figure-read"
                    assert row["values"]["digitisation_uncertainty"] == {
                        "activity": "±0.01 dimensionless activity units",
                        "x_SiO2": "±0.001 mole fraction",
                    }
                    assert row["values"]["method_class"] == evidence
                    assert row["values"]["source_relation"] == source_relation
                    assert "Raoultian pure solid" in row["values"]["standard_state_as_printed"]
                    assert row["uncertainty"]["value"] == 0.01
                    components = row["point_conditions"]["composition"]["state"]["value"]["components"]
                    assert dict(components) == {
                        "SiO2": x,
                        "MgO": format(1 - float(x), ".2f"),
                    }
                    assert row["measurement_method"]["state"]["tag"] == "value"
                    assert len(row["derived_from"]) == 5
                    thermo = row["values"]["thermochemical_sources_as_printed"]
                    assert len(thermo) == 4
                    assert any(item["source_citation_as_printed"] == "Barin [24]" for item in thermo)
                    assert any(item["source_citation_as_printed"] == "NIST–JANAF [23]" for item in thermo)
                    assert any(item.get("delta_G_standard_as_printed") == "−1326.229 kJ/mol" for item in thermo)
                    assert any(item.get("delta_G_standard_as_printed") == "−345.887 kJ/mol" for item in thermo)
                    assert any(item.get("delta_G_standard_as_printed") == "−575.914 kJ/mol" for item in thermo)
                    mg_silicate = next(item for item in thermo if item.get("oxide_as_printed") == "Mg2SiO4(s)")
                    assert mg_silicate["formation_basis_as_printed"] == "from elements at 1873 K (1600 °C)"
                    assert mg_silicate["saturation_reaction_as_printed"]["state"]["tag"] == "unknown"
                    assert any(item.get("reaction_as_printed") == "2Mg(l) + O2(g) = 2MgO(s) [Eq. 20]" for item in thermo)
                else:
                    context = context_reads[observation_id]
                    figure9_context_count += 1
                    assert context["locator"] == expected_locator
                    assert context["activity_as_published"] == activity
                    assert context["source_relation"] == source_relation
                    assert context["series_reference_as_printed"] == figure9_references[slug]
                    assert context["temperature_K"]["state"]["tag"] == "unknown"
                    expected_uncertainty = (
                        "±0.03 dimensionless activity units"
                        if species == "SiO2" and x in {"0.53", "0.55"}
                        else "±0.01 dimensionless activity units"
                    )
                    assert context["digitisation_uncertainty"]["activity"] == expected_uncertainty
                    assert context["digitisation_uncertainty"]["x_SiO2"] == "±0.001 mole fraction"
                    components = context["binary_composition_as_digitised"]["components"]
                    assert dict(components) == {
                        "SiO2": x,
                        "MgO": format(1 - float(x), ".2f"),
                    }
                    if slug == "kambayashi_kato":
                        assert "1973 K" in context["temperature_K"]["state"]["reason"]
                        assert "shifted" in context["temperature_K"]["state"]["reason"]
    assert figure9_scored_count == 6
    assert figure9_context_count == 30

    figure10c_figure = figure_records["jakobsson_2015_figure_10_panel_c_mgo_activity"]
    present_series = next(
        series for series in figure10c_figure["values"]["series"]
        if series["name"] == "present-work activity contours"
    )
    assert present_series["evidence_class"] == "model_derived"
    assert present_series["role"] == (
        "present-work activity labels retained as context without ternary composition"
    )
    figure10c_context = {
        item["observation_id"]: item
        for item in figure10c_figure["values"]["prior_series_context_reads"]
    }
    assert len(figure10c_context) == 20
    references = {
        "present_work": "present study",
        "rein_chipman": "[15]",
        "morita": "[34]",
        "jung": "[10]",
    }
    present_work_thermo = None
    for slug, name, activity, evidence, source_relation in FIGURE10C_READS:
        observation_id = f"jakobsson_2015_figure10c_mgo_{slug}_{activity.replace('.', '')}"
        label = {
            "present_work": activity,
            "rein_chipman": f"[{activity}]",
            "morita": f"({activity})",
            "jung": f"<{activity}>",
        }[slug]
        context = figure10c_context[observation_id]
        assert context["series_as_printed"] == name
        assert context["series_reference_as_printed"] == references[slug]
        assert context["activity_as_published"] == activity
        assert context["series_label_as_printed"] == label
        assert context["source_relation"] == source_relation
        assert context["evidence_class"] == evidence
        assert context["temperature_K"]["state"]["value"] == "1873"
        assert context["locator"]["page"] == 10
        assert context["locator"]["published_page"] == 604
        assert context["locator"]["pdf_page_index"] == 9
        assert context["locator"]["figure"] == "10"
        assert context["locator"]["panel"] == "c"
        assert context["locator"]["row"] == f"{name} contour label {label}"
        assert context["locator"]["line_range"] == "611-660"
        if slug == "present_work":
            assert "point_conditions" not in context
            assert "ternary_composition" not in context
            assert "Raoultian pure solid MgO(s)" in context["standard_state_as_printed"]
            assert context["digitisation"] == "printed activity label; composition not assigned"
            thermo = context["thermochemical_sources_as_printed"]
            if present_work_thermo is None:
                present_work_thermo = thermo
            else:
                assert thermo is present_work_thermo
            assert len(thermo) == 8
            formation = next(
                item for item in thermo
                if item.get("source_citation_as_printed") == "Barin [24]"
                and item.get("oxide_as_printed") == "Ca2SiO4(s)"
            )
            assert formation["delta_G_standard_as_printed"] == "−1573.218 kJ/mol"
            assert formation["formation_basis_as_printed"] == "from elements at 1873 K (1600 °C)"
            assert formation["reaction_as_printed"]["state"]["tag"] == "unknown"
            eq16 = next(
                item for item in thermo
                if item.get("reaction_as_printed") == "Ca2SiO4(s) = 2CaO(s) + SiO2(s) [Eq. 16]"
            )
            assert eq16["equilibrium_constant_as_printed"] == "K2 = 9.10 × 10^-5"
            assert eq16["delta_G_standard_as_printed"]["state"]["tag"] == "unknown"
            assert eq16["delta_G_standard_as_printed"] != formation["delta_G_standard_as_printed"]
            eq18 = next(item for item in thermo if item.get("reaction_as_printed") == "2Ca(l) + O2(g) = 2CaO(s) [Eq. 18]")
            assert eq18["delta_G_standard_as_printed"] == "−869.028 kJ/mol"
            eq20 = next(item for item in thermo if item.get("reaction_as_printed") == "2Mg(l) + O2(g) = 2MgO(s) [Eq. 20]")
            assert eq20["delta_G_standard_as_printed"] == "−784.402 kJ/mol"
        else:
            assert "not a verified on-contour coordinate" in context["ternary_composition"]["state"]["reason"]
    expected_figure10c_context_ids = {
        f"jakobsson_2015_figure10c_mgo_{slug}_{activity.replace('.', '')}"
        for slug, _name, activity, _evidence, _source_relation in FIGURE10C_READS
    }
    assert set(figure10c_context) == expected_figure10c_context_ids


def test_figure_activity_observations_reach_melt_activity_rail_and_context_mutation_fails(monkeypatch):
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {
        record.observation_id.split("::", 1)[1]: record
        for record in migrator.result.observations.values()
    }
    figure_rows = [
        record
        for record in migrator.result.observations.values()
        if "figure9_" in record.observation_id
    ]
    assert len(figure_rows) == 6
    assert {record.observation_id.split("::", 1)[1] for record in figure_rows} == {
        oid for oid in migrated if "figure9_" in oid
    }

    for record in figure_rows:
        assert record.identity.quantity.is_value
        assert record.identity.quantity.value is Quantity.ACTIVITY
        assert battery_score.rail_for_quantity(record.identity.quantity.value) is Rail.MELT_ACTIVITY

    document = _document()
    context_ids = {
        point["observation_id"]
        for body in document["species"].values()
        for figure in body["context"]
        if figure["type"] == "figure_record"
        for point in figure["values"].get("prior_series_context_reads", [])
    }
    assert len(context_ids) == 50
    context_candidate_ids = {f"{EXTRACT.stem}::{oid}" for oid in context_ids}
    figure10c = next(
        figure
        for body in document["species"].values()
        for figure in body["context"]
        if figure.get("observation_id") == "jakobsson_2015_figure_10_panel_c_mgo_activity"
    )
    figure10c_present_ids = {
        point["observation_id"]
        for point in figure10c["values"]["prior_series_context_reads"]
        if point["series_as_printed"] == "Present work"
    }
    assert len(figure10c_present_ids) == 6
    assert figure10c_present_ids <= context_ids
    assert not (figure10c_present_ids & set(migrated))
    context = battery_score.ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations=migrator.result.observations,
    )
    candidates = {row.observation_id for row in battery_score.comparison_candidates(context)}

    def assert_no_context_activities(candidate_ids):
        assert not (candidate_ids & context_candidate_ids)

    assert_no_context_activities(candidates)
    expected_present_ids = {row.observation_id for row in figure_rows}
    assert expected_present_ids <= candidates
    assert not (candidates & {f"{EXTRACT.stem}::{oid}" for oid in figure10c_present_ids})

    # Inject a candidate under a composition-unknown Fig. 10(c) ID; the invariant
    # must fail if a context label reaches comparison_candidates.
    context_id = sorted(figure10c_present_ids)[0]
    context_candidate_id = f"{EXTRACT.stem}::{context_id}"
    mutated_observation = replace(
        figure_rows[0],
        observation_id=context_candidate_id,
    )
    mutated_observations = dict(migrator.result.observations)
    mutated_observations[context_candidate_id] = mutated_observation
    mutated_context = battery_score.ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations=mutated_observations,
    )
    real_comparison_candidates = battery_score.comparison_candidates
    with monkeypatch.context() as mutation:
        mutation.setattr(
            battery_score,
            "comparison_candidates",
            lambda _context: real_comparison_candidates(mutated_context),
        )
        mutated_candidates = {
            row.observation_id
            for row in battery_score.comparison_candidates(context)
        }
        assert context_candidate_id in mutated_candidates
        with pytest.raises(AssertionError):
            assert_no_context_activities(mutated_candidates)


def test_extract_validator_covers_every_scored_leaf_and_reports_no_uncovered_observation():
    document = _document()
    _assert_all_locators_have_text_ranges(document)
    fidelity_warnings = []
    assert validate_extract_document(
        document,
        path=EXTRACT,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        fidelity_warnings=fidelity_warnings,
        check_fidelity_match=True,
    ) == []
    assert not [warning for warning in fidelity_warnings if warning.kind == "uncovered_observation"]
