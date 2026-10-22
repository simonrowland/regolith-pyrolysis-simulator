from pathlib import Path

import yaml


EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/gibson-1971-lunar-gas-release.yaml"
)


def test_gibson_1971_render_pins_and_printed_row_counts():
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = extract["species"]
    observations = [
        row
        for species_data in species.values()
        for row in species_data["observations"]
    ]

    mass_loss = [
        row
        for row in species["lunar_samples"]["observations"]
        if row["type"] == "mass_loss"
    ]
    assert len(mass_loss) == 5
    assert sum(len(row["values"]["points"]) for row in mass_loss) == 10
    assert [
        (row["observation_id"], point["T_C"], point["mass_loss_wt_pct"])
        for row in mass_loss
        for point in row["values"]["points"]
        if row["observation_id"] in {
            "gibson_1971_10086_weight_loss",
            "gibson_1971_12022_weight_loss",
        }
    ] == [
        ("gibson_1971_10086_weight_loss", "1000", "0.13"),
        ("gibson_1971_10086_weight_loss", "1400", "1.41"),
        ("gibson_1971_12022_weight_loss", "1000", "0.038"),
        ("gibson_1971_12022_weight_loss", "1400", "0.51"),
    ]
    assert all(row["locator"]["pdf_page_index"] == 9 for row in mass_loss)

    residue = [
        row
        for row in observations
        if row.get("values", {}).get("quantity") == "residue_component_composition"
    ]
    assert len(residue) == 15
    assert all(row["locator"]["pdf_page_index"] == 10 for row in residue)
    assert {
        (row["values"]["analyte"], row["values"]["series"][0]["residue_ppm"])
        for row in residue
        if row["observation_id"] in {
            "gibson_1971_10073_k_residue_ppm",
            "gibson_1971_10017_na_residue_ppm",
            "gibson_1971_12022_gd_residue_ppm",
        }
    } == {("K", "385"), ("Na", "500"), ("Gd", "7.85")}


def test_gibson_1971_real_migration_preserves_values_and_run_conditions():
    from decimal import Decimal

    from tests.battery.factories import migrate_one_extract

    migrator = migrate_one_extract(Path(__file__).resolve().parents[1], EXTRACT)
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 25

    pins = {
        "gibson_1971_10086_weight_loss::T=1273.15:h=": Decimal("0.0013"),
        "gibson_1971_10073_k_residue_ppm": Decimal("385"),
        "gibson_1971_12022_gd_residue_ppm": Decimal("7.85"),
    }
    for prefix, expected in pins.items():
        observation = next(
            value
            for key, value in observations.items()
            if key.endswith(prefix) or prefix in key
        )
        assert observation.value.point == expected

    for observation in observations.values():
        assert observation.identity.temperature_K.is_value
        assert observation.identity.total_pressure_Pa.is_value
        assert observation.identity.total_pressure_Pa.value == Decimal(
            "0.0002666447368421052631578947368"
        )
