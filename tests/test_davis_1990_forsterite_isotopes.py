from __future__ import annotations

from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract


EXTRACT = (
    Path(__file__).parents[1]
    / "data/literature/extracts/davis-1990-forsterite-evaporation-isotopes.yaml"
)


def test_table1_rows_and_render_anchors_migrate_without_hard_issues(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = doc["species"]["Mg2SiO4"]["observations"]
    mass_rows = [
        row for row in observations
        if row["values"]["quantity"] == "mass_loss_fraction"
    ]
    isotope_rows = [
        row for row in observations
        if row["values"]["quantity"] == "isotope_delta"
    ]

    assert len({row["values"]["sample_row"] for row in mass_rows}) == 11
    assert len(isotope_rows) == 58
    assert len([row for row in mass_rows if row["phase"] == "cr"]) == 4
    assert len([row for row in isotope_rows if row["phase"] == "cr"]) == 16
    assert len([row for row in isotope_rows if row["phase"] == "l"]) == 42
    sigma_by_pair = {
        "17O/16O": 0.05,
        "18O/16O": 0.05,
        "25Mg/24Mg": 1.79,
        "26Mg/24Mg": 3.08,
        "29Si/28Si": 0.05,
        "30Si/28Si": 0.05,
    }
    for row in isotope_rows:
        assert row["uncertainty"]["value"] == sigma_by_pair[row["values"]["isotope_pair"]]
        assert row["uncertainty"]["confidence_level"] == "two_sigma"
        assert "delta_2sigma_permil" not in row["values"]
    assert all(
        row["values"]["isotope_delta_isotope_delta"] != 0 for row in isotope_rows
    )
    assert not any(
        row["values"]["isotope_pair"] in {"25Mg/24Mg", "29Si/28Si"}
        for row in isotope_rows
        if row["values"]["sample_row"].endswith(("01", "02", "03", "04"))
    )

    def delta(sample: str, pair: str) -> float:
        return next(
            row["values"]["isotope_delta_isotope_delta"]
            for row in isotope_rows
            if row["values"]["sample_row"] == sample and row["values"]["isotope_pair"] == pair
        )

    # Values and row/column assignments checked against the PDF page 2 render.
    assert delta("table1_row_01", "26Mg/24Mg") == -0.41
    assert delta("table1_row_05", "25Mg/24Mg") == 7.52
    assert delta("table1_row_11", "30Si/28Si") == 29.51
    assert next(
        row["values"]["mass_loss_pct"]
        for row in mass_rows
        if row["values"]["sample_row"] == "table1_row_11"
    ) == 87.8

    migrated = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert migrated.validation.hard_issues == ()
    mass_observations = [
        row
        for observation_id, row in migrated.observations.items()
        if "davis_1990_table1_row_" in observation_id
        and observation_id.endswith("_mass_loss")
    ]
    assert len(mass_observations) == 11
    source_observations = [
        row
        for row in migrated.observations.values()
        if "davis_1990_table1_row_" in row.observation_id
    ]
    assert len(source_observations) == 69
    assert all(row.identity.temperature_K.is_value for row in source_observations)
    assert all("temperature_K" in row.point_conditions for row in source_observations)
    assert all(row.identity.composition.is_value for row in mass_observations)
    assert all(
        row.identity.composition.value.basis == "printed_mole_fraction"
        for row in mass_observations
    )
    molten_delta = next(
        row
        for row in source_observations
        if row.observation_id.endswith("table1_row_05_25Mg")
    )
    assert molten_delta.identity.species.phase.is_value
    assert molten_delta.identity.species.phase.value.value == "l"
    assert molten_delta.uncertainty.kind.value == "printed"
    assert molten_delta.uncertainty.verbatim["value"] == 1.79
    assert molten_delta.uncertainty.verbatim["confidence_level"] == "two_sigma"
