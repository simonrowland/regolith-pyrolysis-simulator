from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import QUANTITY_UNITS, Phase, Quantity, ValueKind
from simulator.battery.migrate import (
    Migrator,
    PA_PER_ATM_DEC,
    _log_fO2_to_pa,
    experiment_from_plain,
    observation_from_plain,
    work_from_plain,
)
from simulator.battery.validate import validate_corpus
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = Path(
    "data/literature/extracts/schreiber-haskin-1976-plsc7-chromium-in-basalts.yaml"
)
FAS = {
    "SiO2": "53.9 wt%",
    "MgO": "29.3 wt%",
    "Al2O3": "10.7 wt%",
    "CaO": "5.7 wt%",
    "Cr2O3": "0.5 wt%",
}
# Independent transcription of the Figure 4 caption and Table 3 on rendered pages 9 and 16.
FIG4 = {
    "schreiber_1976_fig4_YG_CrVI_fraction": ("0.10", "0", "10%"),
    "schreiber_1976_fig4_YG_CrIII_fraction": ("0.90", "0", "90%"),
    "schreiber_1976_fig4_G_CrIII_fraction": ("0.90", "-2.6", "90%"),
    "schreiber_1976_fig4_G_CrII_fraction": ("0.10", "-2.6", "10%"),
    "schreiber_1976_fig4_BG_CrIII_fraction": ("0.55", "-5.2", "55%"),
    "schreiber_1976_fig4_BG_CrII_fraction": ("0.45", "-5.2", "45%"),
    "schreiber_1976_fig4_B_CrIII_fraction": ("0.20", "-7.9", "20%"),
    "schreiber_1976_fig4_B_CrII_fraction": ("0.80", "-7.9", "80%"),
}
TABLE2_RENDER_GRID = [
    ("Cr3+-Cr2+", "Ca-Mg-Al-silicates", "38 ± 20", "A"),
    ("Cr3+-Cr2+", "Ca-silicates", "76 ± 40", "B"),
    ("Cr3+-Cr2+", "Ca, Mg, Ba-phosphates", "32 ± 20", "C"),
    ("Mn3+-Mn2+", "Na-disilicate", "14", "D"),
    ("Mn3+-Mn2+", "Na, Li-borates", "8-16", "E, F"),
    ("Fe3+-Fe2+", "Na-silicates", "27-38", "G, H"),
    ("Fe3+-Fe2+", "Na-phosphates", "11.5", "I"),
    ("Co3+-Co2+", "Ni-disilicate", "5", "D"),
    ("Eu3+-Eu2+", "Ca, Mg-Al-silicates", "25 ± 10", "J"),
    ("Cu2+-Cu+", "Na, Al-borates", "14.6", "K"),
    ("Cu+-Cu0", "Na, Al-borates", "54.3", "K"),
    ("Ti4+-Ti3+", "Na-disilicate", "29", "D"),
    ("Ce4+-Ce3+", "alkali silicates", "8-31", "D, L"),
    ("Ce4+-Ce3+", "alkali borates", "3-18", "L"),
]


def _assert_caption_pins(document: dict) -> None:
    observations = {
        row["observation_id"]: row
        for row in document["species"]["Cr"]["observations"]
    }
    assert set(observations) >= set(FIG4)
    for observation_id, (fraction, log_fO2, printed) in FIG4.items():
        row = observations[observation_id]
        point = row["values"]["points"][0]
        assert row["phase"] == "l"
        assert row["regime"] == "melt_equilibration_quench"
        assert point["value"] == fraction
        assert point["value_as_published"] == printed
        assert point["log_fO2"] == {"value": log_fO2, "units": "log10(atm)"}
        assert "log_pO2_as_printed" not in point
        assert "fO2_log" not in point["point_conditions"]
        assert point["point_conditions"]["printed_composition"]["state"]["value"] == FAS
        assert point["point_conditions"]["quench_state"]["state"]["value"] == "not_stated"
    table2 = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "schreiber_1976_table2_reduction_enthalpy"
    )
    table_grid = [
        (row["redox_couple"], row["melt"], row["value"], row["reference"])
        for row in table2["values"]["table_rows"]
    ]
    assert table_grid == TABLE2_RENDER_GRID


def test_render_pins_fail_when_fraction_or_oxygen_sign_changes() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_caption_pins(document)

    changed_fraction = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_fraction["species"]["Cr"]["observations"][5]["values"]["points"][0]["value"] = "0.46"
    try:
        _assert_caption_pins(changed_fraction)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed caption fraction must fail the independent render pin")

    changed_oxygen = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    changed_oxygen["species"]["Cr"]["observations"][7]["values"]["points"][0]["log_fO2"]["value"] = "7.9"
    try:
        _assert_caption_pins(changed_oxygen)
    except AssertionError:
        pass
    else:
        raise AssertionError("a positive -log pO2 magnitude must fail the oxygen sign pin")

    changed_table = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table2 = next(
        row
        for row in changed_table["species"]["Cr"]["context"]
        if row["observation_id"] == "schreiber_1976_table2_reduction_enthalpy"
    )
    table2["values"]["table_rows"][0]["value"] = "37 ± 20"
    try:
        _assert_caption_pins(changed_table)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed Table 2 value must fail the independent render pin")


def test_isolated_migration_preserves_fractions_units_conditions_and_quench_phase() -> None:
    migrator = Migrator(Path.cwd(), index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {
        row.observation_id.split("::", 1)[1].split("::", 1)[0]: row
        for row in migrator.result.observations.values()
        if row.source_id == EXTRACT.stem
    }
    assert not migrator.result.validation.hard_issues
    assert set(FIG4) <= set(migrated)
    for observation_id, (fraction, log_fO2, _printed) in FIG4.items():
        row = migrated[observation_id]
        assert row.identity.species.formula == "Cr"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert QUANTITY_UNITS[row.identity.quantity.value] == "dimensionless"
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(fraction)
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == _log_fO2_to_pa(
            Decimal(log_fO2), PA_PER_ATM_DEC
        )
        assert row.identity.temperature_K.value == Decimal("1773.15")
        assert row.point_conditions["printed_composition"].state.value == FAS
        assert row.point_conditions["quench_state"].state.value.value == "not_stated"
        assert row.notices == ()


REPO_ROOT = Path.cwd()
FIG5_EXTRACT = REPO_ROOT / "data/literature/extracts-v2/schreiber-haskin-1976-plsc7-chromium-in-basalts.yaml"
FIG5_SOURCE_ID = "schreiber-haskin-1976-plsc7-chromium-in-basalts"
FIG5_LEGACY_EXTRACT = REPO_ROOT / "data/literature/extracts/schreiber-haskin-1976-plsc7-chromium-in-basalts.yaml"
FIG5_PINS = {
    f"{FIG5_SOURCE_ID}::fig5_panel_c_fas_cr2_fraction::T=1500C:-logPO2=4.1": "0.26",
    f"{FIG5_SOURCE_ID}::fig5_panel_c_fas_cr2_fraction::T=1550C:-logPO2=4.8": "0.49",
    f"{FIG5_SOURCE_ID}::fig5_panel_d_fad_cr2_fraction::T=1550C:-logPO2=9.1": "1.0",
}
FIG5_MIGRATED_PINS = (("0.26", "1500", "-4.1"), ("0.49", "1550", "-4.8"), ("1.0", "1550", "-9.1"))


def _assert_fig5_pins(document: dict) -> None:
    observations = {row["observation_id"]: row for row in document["observations"]}
    for observation_id, expected in FIG5_PINS.items():
        assert observations[observation_id]["value"]["point"] == expected


def test_fig5_three_digitized_cell_pins_and_mutation() -> None:
    document = yaml.safe_load(FIG5_EXTRACT.read_text(encoding="utf-8"))
    assert len(document["observations"]) == 43
    _assert_fig5_pins(document)
    changed = yaml.safe_load(FIG5_EXTRACT.read_text(encoding="utf-8"))
    row = next(row for row in changed["observations"] if row["observation_id"] == next(iter(FIG5_PINS)))
    row["value"]["point"] = "0.25"
    try:
        _assert_fig5_pins(changed)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed Figure 5 cell must fail its independent page-image pin")


def test_fig5_v2_validation_keeps_conditions_for_all_digitized_points() -> None:
    document = yaml.safe_load(FIG5_EXTRACT.read_text(encoding="utf-8"))
    work_document = yaml.safe_load(
        (REPO_ROOT / "data/literature/works/bd5c0c2ddbf02ce3306adc1f1022cb7382273480dcceed4a670678920fe60409.yaml").read_text()
    )
    work = work_from_plain(work_document["work"])
    experiments = [experiment_from_plain(row) for row in work_document["experiments"]]
    migrated = {
        row.observation_id: row
        for row in (observation_from_plain(raw) for raw in document["observations"])
    }
    validation = validate_corpus([work], experiments, migrated)
    assert validation.hard_issues == ()
    digitized = [row for row in document["observations"] if "fig5_panel_" in row["observation_id"]]
    assert len(digitized) == 35
    for source in digitized:
        row = migrated[source["observation_id"]]
        conditions = row.point_conditions
        assert conditions is not None
        assert conditions["T_C"].state.value == Decimal(
            source["point_conditions"]["T_C"]["state"]["value"]
        )
        assert conditions["fO2_log"].state.value == Decimal(
            source["point_conditions"]["fO2_log"]["state"]["value"]
        )
        assert conditions["printed_composition"].state.is_value
        assert conditions["composition"].state.is_unknown


def test_fig5_legacy_extract_validates_and_migrates_without_hard_issues() -> None:
    legacy = yaml.safe_load(FIG5_LEGACY_EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_document(legacy, expected_source_id=FIG5_SOURCE_ID) == []
    migrator = Migrator(REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(FIG5_LEGACY_EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    migrated = [
        row
        for row in migrator.result.observations.values()
        if row.source_id == FIG5_SOURCE_ID
    ]
    for expected, temp_c, log_fO2 in FIG5_MIGRATED_PINS:
        row = next(
            item
            for item in migrated
            if item.value.point == Decimal(expected)
            and item.point_conditions["T_C"].state.value == Decimal(temp_c)
            and item.point_conditions["fO2_log"].state.value == Decimal(log_fO2)
        )
        assert row.value.point == Decimal(expected)
        assert row.point_conditions["T_C"].state.is_value
        assert row.point_conditions["fO2_log"].state.is_value
        assert row.point_conditions["printed_composition"].state.is_value
