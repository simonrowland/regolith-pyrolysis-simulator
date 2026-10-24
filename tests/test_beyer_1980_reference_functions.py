"""Audit the documented, unstored Beyer Gibbs-function column."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import NoticeKind, Phase, Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.records import Notice
from simulator.battery.score import (
    FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND,
    FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
    flagged_strata,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/beyer-1980-potassium-metasilicate-disilicate-ri8410.yaml"


def test_beyer_table4_gibbs_function_is_derivable_from_stored_columns():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    note = document["notes"][0]
    assert "derivable from the stored S and H_T-H_298" in note["text"]
    assert note["locator"]["table"] == "4"
    assert note["locator"]["column"] == "-(G-H298)/T"

    printed_rows = document["species"]["K2SiO3"]["observations"][0]["values"]["series"]
    printed = {Decimal(str(row["T_K"])): row["neg_gibbs_over_T"] for row in printed_rows}

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    stored = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == document["source_id"]
        and observation.locator.table == "4"
        and observation.identity.species.formula == "K2SiO3"
    ]

    for temperature in (Decimal("300"), Decimal("500"), Decimal("1000")):
        row = next(row for row in printed_rows if Decimal(str(row["T_K"])) == temperature)
        entropy = next(
            observation
            for observation in stored
            if observation.identity.quantity.value is Quantity.S
            and observation.identity.temperature_K.value == temperature
        )
        enthalpy = next(
            observation
            for observation in stored
            if observation.identity.quantity.value is Quantity.H_MINUS_H298
            and observation.identity.temperature_K.value == temperature
        )
        # Migration stores S in J/(mol K) and H-H298 in kJ/mol.
        recomputed_cal = (
            entropy.value.point - enthalpy.value.point * Decimal(1000) / temperature
        ) / Decimal("4.184")
        assert abs(recomputed_cal - Decimal(str(row["neg_gibbs_over_T"]))) <= Decimal("0.005")


def test_beyer_table2_entropy_is_stored_and_h_minus_h298_is_absent():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    table2 = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == document["source_id"]
        and observation.locator.table == "2"
    ]
    entropies = [
        observation
        for observation in table2
        if observation.identity.quantity.value is Quantity.S
    ]
    assert len(entropies) == 37
    assert all(observation.value.kind.value == "point" for observation in entropies)

    entropy_298 = next(
        observation
        for observation in entropies
        if observation.identity.temperature_K.value == Decimal("298.15")
    )
    assert entropy_298.value.point == Decimal("190.5812")

    assert not any(
        observation.identity.quantity.value is Quantity.H_MINUS_H298
        for observation in table2
    )


def test_beyer_flagged_points_are_split_from_main_series_and_noticed():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    source_observations = {
        observation["observation_id"]: observation
        for group in species.values()
        for observation in group["observations"]
    }

    def main_temperatures(observation_id):
        return {
            Decimal(str(row["T_K"]))
            for row in source_observations[observation_id]["values"].get("series", [])
        }

    for observation_id, flagged in {
        "bey80_k2si2o5_heat_content_crystal_table3": ("503.0", "852.6", "1309.1"),
        "bey80_k2si2o5_heat_content_glass_table3": ("805.4", "903.2", "956.0"),
        "bey80_k2sio3_heat_content_direct": ("1195.0",),
        "bey80_k2sio3_reference_table4": ("1150", "1200", "1249"),
        "bey80_k2si2o5_reference_high_liquid_table5": ("1500", "1550", "1600"),
        "bey80_k2si2o5_reference_glass_table6": ("800", "850", "900"),
    }.items():
        assert not {
            Decimal(temperature) for temperature in flagged
        } & main_temperatures(observation_id)

    # S and H aliases must also stay out of the main arrays after each split.
    for observation_id, flagged in {
        "bey80_k2si2o5_reference_high_liquid_S_table5": ("1500", "1550", "1600"),
        "bey80_k2si2o5_reference_high_liquid_H_table5": ("1500", "1550", "1600"),
        "bey80_k2si2o5_reference_glass_S_table6": ("800", "850", "900"),
        "bey80_k2si2o5_reference_glass_H_table6": ("800", "850", "900"),
        "bey80_k2sio3_entropy_table4": ("1150", "1200", "1249"),
        "bey80_k2sio3_enthalpy_table4": ("1150", "1200", "1249"),
    }.items():
        assert not {
            Decimal(temperature) for temperature in flagged
        } & main_temperatures(observation_id)

    # K2Si2O5 crystal at 1195 K has no Table 3 footnote and remains in the series.
    assert Decimal("1195.0") in main_temperatures(
        "bey80_k2si2o5_heat_content_crystal_table3"
    )

    table10_main = source_observations[
        "bey80_k2si2o5_oxide_formation_g_table10"
    ]["values"]["series"]
    at_867 = [row for row in table10_main if Decimal(str(row["T_K"])) == 867]
    assert len(at_867) == 1
    assert Decimal(str(at_867[0]["delta_fG"])) == Decimal("-79.8")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    stored = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == document["source_id"]
    ]
    assert len(stored) == 628
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert all(observation.value.kind.value != "unavailable" for observation in stored)

    def assert_noticed(table, formula, phase, quantity, temperature, kind):
        matches = [
            observation
            for observation in stored
            if observation.locator.table == table
            and observation.identity.species.formula == formula
            and observation.identity.species.phase.value is phase
            and observation.identity.quantity.value is quantity
            and observation.identity.temperature_K.value == Decimal(temperature)
            and any(notice.kind is kind for notice in observation.notices)
        ]
        assert len(matches) == 1, (table, formula, phase, quantity, temperature)
        return matches[0]

    out_of_band_points = [
        ("3", "K2Si2O5", Phase.CR, Quantity.H_MINUS_H298, "1309.1", "298-1258 K"),
        ("3", "K2Si2O5", Phase.GLASS, Quantity.H_MINUS_H298, "805.4", "298-753.2 K"),
        ("3", "K2Si2O5", Phase.GLASS, Quantity.H_MINUS_H298, "903.2", "298-753.2 K"),
        ("3", "K2Si2O5", Phase.GLASS, Quantity.H_MINUS_H298, "956.0", "298-753.2 K"),
        ("3", "K2SiO3", Phase.CR, Quantity.H_MINUS_H298, "1195.0", "298-1103.3 K"),
    ]
    for table, formula, phase, quantity, temperature, band in out_of_band_points:
        observation = assert_noticed(
            table,
            formula,
            phase,
            quantity,
            temperature,
            NoticeKind.OUT_OF_CERTIFIED_BAND,
        )
        notice = next(
            notice
            for notice in observation.notices
            if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        )
        assert notice.band == band
        assert FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND in flagged_strata(
            observation.notices
        )
        assert FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT not in flagged_strata(
            observation.notices
        )

    for temperature in ("503.0", "852.6"):
        observation = next(
            item
            for item in stored
            if item.locator.table == "3"
            and item.identity.species.formula == "K2Si2O5"
            and item.identity.species.phase.value is Phase.CR
            and item.identity.quantity.value is Quantity.H_MINUS_H298
            and item.identity.temperature_K.value == Decimal(temperature)
        )
        assert observation.admission.status.value == "admitted"
        assert flagged_strata(observation.notices) == ()

    source_inconsistency = Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.H_MINUS_H298,),
        reason="source_internally_inconsistent: legacy stratum remains diagnostic",
        origin="test:legacy-source-inconsistency",
    )
    assert flagged_strata((source_inconsistency,)) == (
        FLAGGED_STRATUM_SOURCE_INTERNALLY_INCONSISTENT,
    )

    for table, formula, phase, temperatures in (
        ("4", "K2SiO3", Phase.CR, ("1150", "1200", "1249")),
        ("5", "K2Si2O5", Phase.L, ("1500", "1550", "1600")),
        ("6", "K2Si2O5", Phase.GLASS, ("800", "850", "900")),
    ):
        for temperature in temperatures:
            for quantity in (Quantity.CP, Quantity.S, Quantity.H_MINUS_H298):
                observation = assert_noticed(
                    table,
                    formula,
                    phase,
                    quantity,
                    temperature,
                    NoticeKind.OUT_OF_CERTIFIED_BAND,
                )
                notice = next(
                    notice
                    for notice in observation.notices
                    if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
                )
                assert "extrapolated" in notice.reason
                assert FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND in flagged_strata(
                    observation.notices
                )
                assert notice.band == {
                    "4": "298-1100 K",
                    "5": "1318-1450 K",
                    "6": "298-750 K",
                }[table]

    misprint = assert_noticed(
        "10",
        "K2Si2O5",
        Phase.CR,
        Quantity.DELTA_FG,
        "867",
        NoticeKind.PROBABLE_SOURCE_MISPRINT,
    )
    assert misprint.value.point == Decimal("-329.6992")
    assert any(
        notice.kind is NoticeKind.PROBABLE_SOURCE_MISPRINT
        and "printed value is retained" in notice.reason
        for notice in misprint.notices
    )

    noticed = [
        notice
        for observation in stored
        for notice in observation.notices
        if notice.kind
        in {
            NoticeKind.OUT_OF_CERTIFIED_BAND,
            NoticeKind.SOURCE_DISAGREEMENT,
            NoticeKind.PROBABLE_SOURCE_MISPRINT,
        }
        and observation.locator.table in {"3", "4", "5", "6", "10"}
    ]
    assert len(noticed) == 33
