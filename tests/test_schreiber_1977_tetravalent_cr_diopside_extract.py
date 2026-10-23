from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import QUANTITY_UNITS, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/schreiber-1977-tetravalent-cr-diopside.yaml"
CRVI_ID = "schreiber_1977_crvi_fraction_blue_diopside_melt"
CRII_ID = "schreiber_1977_crii_fraction_blue_diopside_melt"
CRIII_ID = "schreiber_1977_criii_fraction_blue_diopside_melt"
EXPECTED_RANGE = ("0.10", "0.20")


def _assert_render_pins(document: dict) -> None:
    rows = document["species"]["Cr"]["observations"]
    assert [row["observation_id"] for row in rows] == [CRVI_ID, CRII_ID, CRIII_ID]
    crvi, crii, criii = rows
    interval = crvi["values"]["points"][0]
    assert tuple(interval["range"]) == EXPECTED_RANGE
    assert interval["approximate"] is True
    assert interval["value_as_published"] == "about 10-20 percent"
    assert crvi["phase"] == crii["phase"] == criii["phase"] == "l"
    assert all(row["regime"] == "melt_equilibration_quench" for row in rows)
    assert crii["values"]["points"][0]["value"] == "0.30"
    assert crii["values"]["points"][0]["value_as_published"] == "30 percent"
    assert criii["values"]["points"][0]["value"] == "0.70"
    assert criii["values"]["points"][0]["value_as_published"] == "70 percent"
    for row in (crii, criii):
        condition = row["values"]["points"][0]["point_conditions"]["oxygen_partial_pressure"]
        assert condition["value"] == "10^-8"
        assert condition["units"] == "atm"
    distribution = next(
        row
        for row in document["species"]["Cr"]["context"]
        if row["observation_id"] == "schreiber_1977_quoted_criii_crii_distribution_coefficient"
    )
    assert distribution["values"]["attribution"] == "Schreiber and Haskin (1976)"
    assert distribution["values"]["value_as_published"] == "about 3"
    assert "30 percent" not in distribution["values"].get("values_as_published", "")


def test_render_pins_and_fidelity_samples() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []


def test_render_pin_fails_after_source_value_mutation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    document["species"]["Cr"]["observations"][1]["values"]["points"][0]["value"] = "0.31"
    with pytest.raises(AssertionError):
        _assert_render_pins(document)


def test_isolated_migration_preserves_each_fraction_unit_and_oxygen_pa() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    by_id = {
        str(row.observation_id).split("::", 2)[1].split("::", 1)[0]: row
        for row in result.observations.values()
    }
    assert set(by_id) == {CRVI_ID, CRII_ID, CRIII_ID}
    crvi = by_id[CRVI_ID]
    assert crvi.value.kind is ValueKind.INTERVAL
    assert (str(crvi.value.interval_low), str(crvi.value.interval_high)) == EXPECTED_RANGE
    assert crvi.value.approximate is True
    assert crvi.identity.fO2_Pa.is_unknown
    assert crvi.notices == ()
    for observation_id, expected in ((CRII_ID, "0.30"), (CRIII_ID, "0.70")):
        row = by_id[observation_id]
        assert row.identity.species.formula == "Cr"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert QUANTITY_UNITS[row.identity.quantity.value] == "dimensionless"
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == Decimal(expected)
        assert row.identity.temperature_K.is_unknown
        assert row.identity.fO2_Pa.is_value
        assert row.identity.fO2_Pa.value == Decimal("0.00101325")
        assert row.notices == ()
