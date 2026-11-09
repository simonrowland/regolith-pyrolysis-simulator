from pathlib import Path

from simulator.battery.migrate import Migrator
from simulator.battery.score import parse_species_formula


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pankratz_oxygen_ocr_formula_migrates_as_closed_species() -> None:
    source = (
        REPO_ROOT
        / "data/literature/compilations/pankratz-1984-usbm-b677/records/table-0127.json"
    )
    migrator = Migrator(root=REPO_ROOT)

    migrator._migrate_compilation_file(source)

    observations = list(migrator.result.observations.values())
    assert [observation.observation_id for observation in observations] == [
        "pankratz-1984-usbm-b677:table-0127:cp",
        "pankratz-1984-usbm-b677:table-0127:S",
        "pankratz-1984-usbm-b677:table-0127:H_minus_H298",
    ]
    assert {
        observation.identity.species.formula for observation in observations
    } == {"O2"}
    assert parse_species_formula("O2") is not None
