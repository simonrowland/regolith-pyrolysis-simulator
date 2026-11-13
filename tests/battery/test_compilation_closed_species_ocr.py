from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("name", "printed_formula"),
    [
        ("Oxygen (ideal monatomic gas)", "0"),
        ("Oxygen, ozone (ideal triatomic gas)", "O3"),
        ("Niobium carbide", "NbC.98"),
        ("Sbullet", "Sbullet"),
        ("Fe(1-x)O", "Fe(1-x)O"),
    ],
)
def test_closed_species_ocr_rule_does_not_expand_to_unlisted_names(
    name: str, printed_formula: str
) -> None:
    record = {
        "name_as_published": name,
        "formula_token": {"ocr_suspect": True},
    }

    assert (
        Migrator._normalize_compilation_formula(record, printed_formula)
        == printed_formula
    )


def test_closed_species_ocr_rule_strips_formula_markup_before_matching() -> None:
    record = {
        "name_as_published": "Oxygen",
        "formula_token": {"ocr_suspect": True},
    }

    assert (
        Migrator._normalize_compilation_formula(record, r"$0_{2}(g)$")
        == "O2"
    )


def test_closed_species_ocr_rule_requires_ocr_suspicion() -> None:
    record = {
        "name_as_published": "Oxygen",
        "formula_token": {"ocr_suspect": False},
    }

    assert Migrator._normalize_compilation_formula(record, "02") == "02"


def test_closed_species_ocr_rule_requires_printed_formula_match() -> None:
    record = {
        "name_as_published": "Oxygen",
        "formula_token": {"ocr_suspect": True},
    }

    assert Migrator._normalize_compilation_formula(record, "O") == "O"
