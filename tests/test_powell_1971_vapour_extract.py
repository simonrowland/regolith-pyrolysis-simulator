from __future__ import annotations

from pathlib import Path

import yaml

from tests.battery import factories


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/powell-1971-alkali-sulfate-metasilicate-vapour.yaml"


def test_powell_1971_vapour_rows_and_schema_gap_set() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = factories.migrate_one_extract(
        ROOT,
        EXTRACT,
        index={EXTRACT.stem: {**extract["source"], "source_id": EXTRACT.stem}},
        aliases={},
    )
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 290
    assert len(migrator.result.queue) == 637
    assert not [
        issue
        for issue in migrator.result.validation.issues
        if issue.reason.value == "conditional_field"
    ]

    for family in extract["species"].values():
        for row in family["observations"]:
            values = row["values"]
            conditions = values["point_conditions"]
            if "T_K" in values:
                condition = conditions["temperature_K"]
            elif "T_C" in values:
                condition = conditions["T_C"]
            else:
                assert row["observation_id"].endswith("slope_enthalpy")
                condition = conditions["temperature_K"]
            assert condition["state"]["tag"] in {"value", "unknown"}
            assert condition["locator"]["page"] == row["locator"]["page"]

    quantity_unknown = {
        observation.observation_id
        for observation in observations.values()
        if observation.identity.quantity.is_unknown
    }
    expected_unknown = {
        f"{EXTRACT.stem}::{row['observation_id']}"
        for family in extract["species"].values()
        for row in family["observations"]
        if row["values"]["quantity"] in {
            "apparent_knudsen_pressure_assumed_mw",
            "torsion_effusion_total_pressure",
            "vaporisation_enthalpy_per_mole_gas_unresolved_reaction",
            "quoted_salt_vapour_pressure_unresolved_gas_species",
            "positive_equilibrium_constant_and_dissociation_degree",
        }
    }
    assert quantity_unknown == expected_unknown
    assert len(quantity_unknown) == 167
    species_absent = {
        observation_id
        for observation_id, observation in observations.items()
        if observation.identity.species is None
    }
    assert species_absent == {
        f"{EXTRACT.stem}::powell_1971_eyber_na2so4_r0{row}_log_kp"
        for row in range(1, 7)
    }
    assert all(
        not observation.identity.species.phase.is_unknown
        for observation in observations.values()
        if observation.identity.species is not None
    )

    # Render pins: PDF pages 79, 81, and 45. Values remain in their printed units/forms.
    rows = {
        row["observation_id"]: row
        for family in extract["species"].values()
        for row in family["observations"]
    }
    assert (
        rows["powell_1971_t01_r02_mass_loss_rate"]["values"]["mass_loss_rate_as_printed"]
        == "3.33 x 10-3"
    )
    assert rows["powell_1971_t01_r02_mass_loss_rate"]["locator"] == {"page": 79, "table": "1", "row": "2"}
    assert (
        rows["powell_1971_t01_r02_apparent_knudsen_pressure"]["values"][
            "apparent_pressure_as_printed"
        ]
        == "7.37 x 10-4"
    )
    assert (
        rows["powell_1971_t02_r19_apparent_knudsen_pressure"]["values"][
            "apparent_pressure_as_printed"
        ]
        == "4.90 x 10-3"
    )
    assert (
        rows["powell_1971_eyber_na2so4_r05_pressure"]["values"][
            "quoted_pressure_as_printed"
        ]
        == "0.80"
    )

    assert {
        observation_id
        for observation_id, observation in observations.items()
        if observation.identity.temperature_K is None
    } == {
        f"{EXTRACT.stem}::powell_1971_t06_r{row:02d}_slope_enthalpy"
        for row in range(1, 8)
    }  # Table 6 has high/low fit segments but no numeric temperature bounds.
    assert all(
        observation.identity.total_pressure_Pa is None
        or observation.identity.total_pressure_Pa.tag.value != "value"
        for observation in observations.values()
    )
