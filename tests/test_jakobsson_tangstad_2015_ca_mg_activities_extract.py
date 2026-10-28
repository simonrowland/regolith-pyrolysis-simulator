"""Independent render-grid and isolated migration checks for Jakobsson and Tangstad (2015)."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import MethodToken, Quantity
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction

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


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


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
                assert row["locator"] == {
                    "page": page,
                    "published_page": folio,
                    "pdf_page_index": pdf_index,
                    "table": table,
                    "row": sample,
                    "column": f"{element} (mg/kg)",
                }
                conditions = row["point_conditions"]
                assert conditions["sample_id"]["state"]["value"] == sample
                assert conditions["temperature_K"]["state"]["value"] == "1873"
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
    assert len(records) == 68
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
        assert migrator.result.experiments[record.experiment_id].method.value is MethodToken.QUENCH_EQUILIBRATION
        assert record.identity.temperature_K is not None and record.identity.temperature_K.is_value
        assert record.identity.temperature_K.value == Decimal("1873")
        assert record.point_conditions is not None
        if value["quantity"] == "equilibrium_phase_composition":
            for key in ("temperature_K", "composition", "sample_id"):
                assert key in record.point_conditions
            temperature = record.point_conditions["temperature_K"]
            assert temperature.state.is_value and temperature.state.value == Decimal("1873")
            assert temperature.locator is not None and temperature.locator.pdf_page_index in {1, 3}
            composition = record.point_conditions["composition"]
            assert composition.state.is_value and composition.locator is not None
            assert record.point_conditions["sample_id"].state.value is not None
        elif value["quantity"] == "activity":
            assert record.identity.quantity.is_value
            assert record.identity.quantity.value is Quantity.ACTIVITY
            assert record.identity.reference_state is not None and record.identity.reference_state.is_value
            assert record.identity.per is not None and record.identity.per.is_value

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
            assert figure["values"]["digitisation"] == "deferred to a figure pass"
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
    assert len(activities) == 3
    assert activities["jakobsson_2015_text_sio2_activity_at_ca2sio4_saturation"]["values"]["activity_as_published"] == "0.0724"
    assert activities["jakobsson_2015_text_cao_activity_at_ca2sio4_saturation"]["values"]["activity_as_published"] == "3.55e-2"
    assert activities["jakobsson_2015_text_cao_activity_at_sio2_saturation"]["values"]["activity_as_published"] == "0.00260"
    assert activities["jakobsson_2015_text_sio2_activity_at_ca2sio4_saturation"]["locator"]["page"] == 6
    assert activities["jakobsson_2015_text_cao_activity_at_ca2sio4_saturation"]["locator"]["page"] == 7
    assert activities["jakobsson_2015_text_cao_activity_at_sio2_saturation"]["locator"]["page"] == 7

    context = [item for body in document["species"].values() for item in body["context"]]
    ca_gamma = next(item for item in context if item["observation_id"] == "jakobsson_2015_text_ln_henrian_gamma_ca_schema_gap")
    mg_gamma = next(item for item in context if item["observation_id"] == "jakobsson_2015_text_ln_henrian_gamma_mg_schema_gap")
    assert ca_gamma["locator"]["page"] == 8
    assert mg_gamma["locator"]["page"] == 9

    figure_records = {
        item["observation_id"]: item
        for item in context
        if item["type"] == "figure_record"
    }
    for figure, page in [(1, 3), (2, 3), (3, 3), (4, 3), (5, 5), (6, 7), (7, 7), (8, 8), (9, 9), (10, 10)]:
        matching = [item for obs_id, item in figure_records.items() if f"figure_{figure}_" in obs_id]
        assert matching
        assert all(item["locator"]["page"] == page for item in matching)
    for figure in (2, 3, 6, 7):
        matching = [item for obs_id, item in figure_records.items() if f"figure_{figure}_" in obs_id]
        assert all("series-specific temperatures" in item["values"]["temperature_as_printed"] for item in matching)
