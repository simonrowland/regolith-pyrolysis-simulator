"""Shared migration and row selection for Wang Langmuir residue probes."""

from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.residue import _langmuir_residue_row_id
from simulator.battery.score import ScoreContext
from simulator.source_ids import WANG_2001_SOURCE_ID


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = WANG_2001_SOURCE_ID
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def wang_context():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = dict(migrator.result.observations)
    context = ScoreContext(
        {}, dict(migrator.result.experiments), observations, root=ROOT
    )
    return context, observations


def wang_residue_rows(observations):
    return {
        row.observation_id: row
        for row in observations.values()
        if row.source_id == SOURCE_ID
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.admission.status.value == "admitted"
    }


def wang_sc1_residue_pair(references, observations):
    reference = next(
        row
        for row in references.values()
        if row.identity.species.formula == "SiO2"
        and _langmuir_residue_row_id(row.locator) == "sc1"
    )
    anchor = next(
        row
        for row in observations.values()
        if row.experiment_id == reference.experiment_id
        and row.source_id == SOURCE_ID
        and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and _langmuir_residue_row_id(row.locator) == "sc1"
        and row.admission.status.value == "admitted"
    )
    return reference, anchor
