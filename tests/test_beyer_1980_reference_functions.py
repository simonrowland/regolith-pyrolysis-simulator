"""Audit the documented, unstored Beyer Gibbs-function column."""

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


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
