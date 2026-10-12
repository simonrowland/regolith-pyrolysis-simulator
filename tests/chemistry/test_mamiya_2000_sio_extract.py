"""Source pins for numerical evidence retained in the Mamiya context rows."""

from __future__ import annotations

from pathlib import Path

from simulator.yaml_cache import load_cached_safe_yaml

EXTRACT = (
    Path(__file__).resolve().parents[2]
    / "data/literature/extracts/mamiya-2000-sio-disproportionation.yaml"
)


def test_mamiya_context_numbers_match_the_transcription() -> None:
    doc = load_cached_safe_yaml(EXTRACT.read_text(encoding="utf-8"))
    rows = {
        row["observation_id"]: row["values"]
        for species in doc["species"].values()
        for row in species.get("context", [])
    }

    fit = rows["mamiya_2000_fig4_12_kinetic_fit_and_activation_energy"]
    assert fit["fitted_equation_as_printed"] == "log k = 9.190 - 9884.7 x (1/T)"
    assert fit["fitted_parameter_as_printed"] == "E* = 82.2 kJ mol-1"

    times = rows["mamiya_2000_fig3_8_cristobalite_reaction_times"]["points"]
    assert [(point["T_C"], point["time_hr"]) for point in times] == [
        (1000, 20),
        (1100, 20),
        (1200, 10),
        (1300, 2),
        (1350, 1),
        (1400, 0.1),
    ]

    intensity_points = {
        temperature: rows[
            f"mamiya_2000_fig4_12_intensity_ratio_{temperature}c"
        ]["points"]
        for temperature in (900, 1000, 1100, 1200, 1300)
    }
    assert {
        temperature: [
            (point["time_hr"], point["ratio_as_digitized"])
            for point in points
        ]
        for temperature, points in intensity_points.items()
    } == {
        900: [(1, 0.8), (2, 1.6), (4, 1.9)],
        1000: [(0.1, 0.9), (0.2, 2.1), (1, 3.2), (2, 3.5), (4, 3.0)],
        1100: [(0.1, 1.5), (0.2, 3.1), (1, 3.3), (2, 4.0), (4, 3.9)],
        1200: [(0, 0.0), (0.1, 2.1), (0.2, 2.4), (1, 2.6), (2, 2.9), (4, 3.0)],
        1300: [(0.1, 3.1), (0.2, 3.2), (1, 3.6), (2, 3.7), (4, 3.5)],
    }

    rates = rows["mamiya_2000_fig4_14_rate_constants"]["points"]
    assert [(point["T_C"], point["k_as_digitized"]) for point in rates] == [
        (900, 0.58),
        (1000, 4.2),
        (1100, 6.7),
        (1200, 12),
        (1300, 20),
    ]

    tg_1420 = rows["mamiya_2000_tg_air_1420c_characterization"]
    assert tg_1420["starting_mass_mg"] == 20.70
    assert tg_1420["oxidation_mass_gain_mg"] == 7.56
    assert tg_1420["x_in_SiOx_as_printed"] == 0.9944
    assert tg_1420["oxidation_completion_temperature_C"] == 1420
    assert tg_1420["oxidation_completion_time_hr"] == 18

    tg_1030 = rows["mamiya_2000_tg_air_1030c_characterization"]
    assert tg_1030["holding_temperature_C"] == 1030
    assert tg_1030["change_almost_finished_after_hr"] == 15
    assert tg_1030["tentative_composition_as_printed"] == "SiO1.4"
    assert "not stated" in tg_1030["sample_as_printed"]

    size_1300 = rows["mamiya_2000_crystallite_size_1300c_1h"]
    assert size_1300["XRD_mean_size_nm"] == 13.6
    assert size_1300["TEM_size_as_printed"] == "about 11 nm"
    size_1000 = rows["mamiya_2000_crystallite_size_1000c_1h"]
    assert size_1000["XRD_mean_size_nm"] == 3.67
    assert size_1000["TEM_size_as_printed"] == "about 4 nm"
