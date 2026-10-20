from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    EvidenceClass,
    Quantity,
    ValueKind,
    ValenceValueForm,
)
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "jin-2022-cl-cr-sulfur-glass-solubility"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"


def test_table4_cr_vi_percentages_migrate_as_reduced_fraction_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text())
    source_observation = document["species"]["Cr"]["observations"][0]
    assert source_observation["values"]["method_class"] == "measured_reduced"
    assert source_observation["values"]["value_form"] == "fraction"
    assert source_observation["values"]["value_as_published"] == "percent"
    raw_inputs = source_observation["derivation"]["raw_inputs"]
    assert raw_inputs["state"]["value"] == "not_printed"
    assert raw_inputs["locator"]["page"] == 6

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    rows = list(migrator.result.observations.values())
    assert len(rows) == 4
    assert not migrator.result.validation.hard_issues
    assert {row.value.point for row in rows} == {
        Decimal("0.577"),
        Decimal("0.731"),
        Decimal("0.833"),
        Decimal("0.776"),
    }
    for row in rows:
        assert row.value.kind is ValueKind.POINT
        assert row.identity.quantity.value is Quantity.VALENCE_RATIO
        assert row.identity.species.formula == "Cr"
        assert row.identity.temperature_K.value == Decimal("1423.15")
        assert row.identity.valence_basis.value.value_form is ValenceValueForm.FRACTION
        assert row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert row.derivation is not None
        assert row.derivation.output_unit == "percent"
        assert row.derivation.raw_inputs is not None
        assert row.derivation.raw_inputs.state.value.value == "not_printed"
        assert row.point_conditions["atmosphere"].state.value == "air"
        assert row.point_conditions["quench"].state.value == "water quench"
        assert row.notices == ()
