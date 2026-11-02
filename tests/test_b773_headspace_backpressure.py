from scripts.reproduce_b773_evap_oscillation import (
    _NONZERO_KG_HR,
    _cases,
    _run_case,
)


def test_lunar_c2a_hot_entry_pins_pre_fix_period_two_hours():
    """Record the old alternating Na/K/Fe hours before changing the solver."""
    case = _cases("reproduction")[0]
    result = _run_case(case)
    rows = result["hours"]

    expected_on_hours = list(range(1, 25, 2))
    expected_off_hours = list(range(2, 25, 2))
    for species in ("Na", "K", "Fe"):
        on_hours = [
            row["hour"]
            for row in rows
            if float(row["flux_kg_hr"].get(species, 0.0)) > _NONZERO_KG_HR
        ]
        off_hours = [
            row["hour"]
            for row in rows
            if float(row["flux_kg_hr"].get(species, 0.0)) <= _NONZERO_KG_HR
        ]
        assert on_hours == expected_on_hours, species
        assert off_hours == expected_off_hours, species
