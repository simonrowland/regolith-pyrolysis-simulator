"""Regression pin for O'Neill & Eggins (2002) printed FeO uncertainties."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re

import yaml

from simulator.battery.enums import Quantity, UncertaintyKind
from simulator.battery.migrate import Migrator
from simulator.battery.score import _printed_uncertainty_band


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/oneill-2002-feo-activity-coefficients-cmas.yaml"


def test_feo_table_7_rows_keep_their_own_printed_one_sigma():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_observation = document["species"]["FeO"]["observations"][0]
    source_rows = source_observation["values"]["series"]
    composition_basis_note = source_observation["values"]["composition_basis_note"]
    assert "Table 4" in composition_basis_note
    assert "wt.% is inferred" in composition_basis_note
    source_uncertainty = source_observation["uncertainty"]
    assert source_uncertainty["note"].startswith(
        "Errors are one standard deviation and do not include systematic errors, "
        "e.g., in the standard state thermodynamic data."
    )
    assert source_uncertainty["locator"]["page"] == 13
    assert source_uncertainty["locator"]["published_page"] == 163
    assert source_uncertainty["locator"]["table"] == "7"

    migrator = Migrator(root=ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        and observation.identity.species.formula == "FeO"
    ]

    assert len(source_rows) == 19
    assert len(observations) == 19
    bands = []
    for row, observation in zip(source_rows, observations, strict=True):
        printed = row["gamma_as_printed"]
        match = re.fullmatch(r"\s*\d+(?:\.(\d+))?\s+\((\d+)\)\s*", printed)
        assert match is not None
        digits = len(match.group(1) or "")
        expected_sigma = Decimal(match.group(2)).scaleb(-digits)
        assert observation.uncertainty.kind is UncertaintyKind.PRINTED
        assert observation.uncertainty.verbatim["sigma"] == row["sigma"]
        assert Decimal(observation.uncertainty.verbatim["sigma"]) == expected_sigma
        assert observation.uncertainty.verbatim["confidence_level"] == "one_sigma"
        assert observation.identity.temperature_K.value == Decimal("1673.15")
        assert (
            observation.point_conditions["temperature_K"].state.value
            == Decimal("1673.15")
        )
        assert observation.point_conditions["temperature_K"].locator is not None
        assert observation.point_conditions["composition"].state.is_value
        assert observation.point_conditions["composition"].locator is not None
        resolution = _printed_uncertainty_band(
            observation.identity.quantity.value,
            observation.uncertainty,
            observation.value.point,
            source_observation=observation,
        )
        assert resolution.refusal is None
        assert resolution.band is not None
        assert resolution.band.rule.startswith("printed_1sigma:")
        bands.append(resolution.band.value)

        old_units_observation = replace(
            observation,
            derivation=replace(
                observation.derivation,
                output_unit=(
                    "dimensionless activity coefficient; "
                    "Table 4 composition values interpreted as wt.%"
                ),
            ),
        )
        old_units = _printed_uncertainty_band(
            old_units_observation.identity.quantity.value,
            old_units_observation.uncertainty,
            old_units_observation.value.point,
            source_observation=old_units_observation,
        )
        assert old_units.band is None
        assert old_units.refusal["reason"] == "printed_uncertainty_unit_unmapped"

    assert len(bands) == 19
    assert bands[:3] == [
        Decimal("0.006877170700136778177155847580"),
        Decimal("0.01462079132824429539788449331"),
        Decimal("0.007325359695819540850617842192"),
    ]
