"""Checks the experimental thermochemistry printed in the 2015 presentation."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.records import ValueKind

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/myers-jacobson-2015-volatile-metal-hydroxides-presentation.yaml"
EXPECTED = {
    "Si(OH)4": ("myers_jacobson_2015_presentation_sioh4_delta_fh_298", Decimal("-1351.3"), Decimal("1.7")),
    "SiO(OH)2": ("myers_jacobson_2015_presentation_siooh2_delta_fh_298", Decimal("-836"), Decimal("40")),
    "CrO2(OH)2": ("myers_jacobson_2015_presentation_cro2oh2_delta_fh_298", Decimal("-741"), Decimal("6")),
    "TiO(OH)2": ("myers_jacobson_2015_presentation_tiooh2_delta_fh_298", Decimal("-403"), Decimal("13")),
}


def _assert_printed_values(document):
    for species, (observation_id, value, sigma) in EXPECTED.items():
        observation = next(
            row
            for row in document["species"][species]["observations"]
            if row["observation_id"] == observation_id
        )
        assert Decimal(str(observation["values"]["value"])) == value
        assert Decimal(str(observation["uncertainty"]["sigma"])) == sigma


def test_printed_poster_values_migrate_as_points_with_identity_and_temperature():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_printed_values(document)
    figures = [
        row
        for container in document["species"].values()
        for row in container["context"]
        if row["type"] == "figure_only"
    ]
    assert len(figures) == 7
    assert all(row["values"]["digitisation"] == "deferred to a figure pass" for row in figures)
    assert all("points" not in row["values"] for row in figures)

    mutated = deepcopy(document)
    mutated["species"]["Si(OH)4"]["observations"][0]["values"]["value"] = -1351.2
    with pytest.raises(AssertionError):
        _assert_printed_values(mutated)

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert migrator.result.validation is not None
    assert migrator.result.validation.hard_issues == ()
    assert len(migrator.result.observations) == len(EXPECTED)

    for species, (observation_id, value, sigma) in EXPECTED.items():
        observation = next(
            row
            for row in migrator.result.observations.values()
            if row.observation_id.endswith(observation_id)
        )
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == value
        assert observation.identity.quantity.value is Quantity.DELTA_FH
        assert observation.identity.species.formula == species
        assert observation.identity.temperature_K.value == Decimal("298")
        temperature = observation.point_conditions["temperature_K"]
        assert temperature.state.value == Decimal("298")
        assert temperature.locator.page == 1
        assert temperature.locator.pdf_page_index == 0
        assert observation.uncertainty.verbatim["sigma"] == float(sigma)
        assert observation.uncertainty.verbatim["units"] == "kJ/mol"
        assert observation.notices == ()
