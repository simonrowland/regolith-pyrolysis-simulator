from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from literature_extract_test_utils import isolated_root
from simulator.battery.enums import Phase, Quantity
from simulator.battery.identity import profile_for
from simulator.battery.migrate import migrate


ROOT = Path(__file__).resolve().parents[1]
SID = "hashimoto-1990-forsterite-evaporation-kinetics"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"
SCHEMA_GAP_ID = "hashimoto_1990_forsterite_residue_glass_volume_fraction"
OUT_OF_DOMAIN_ID = "hashimoto_1990_sio2_liquid_alternate_gas_alpha"

_ALPHA_PINS = {
    "hashimoto_1990_mgo_solid_alpha": ("range", ["0.12", "0.22"], 1),
    "hashimoto_1990_sio2_solid_alpha": ("range", ["0.011", "0.015"], 2),
    "hashimoto_1990_sio2_liquid_alpha": ("range", ["0.038", "0.048"], 2),
    "hashimoto_1990_sio2_previous_effusion_alpha": ("alpha", "0.01", 2),
    "hashimoto_1990_sio2_solid_alternate_gas_alpha": ("range", ["0.7", "0.9"], 3),
    "hashimoto_1990_sio2_liquid_alternate_gas_alpha": ("alpha", "1.2", 3),
    "hashimoto_1990_forsterite_solid_alpha": ("range", ["0.09", "0.16"], 3),
    "hashimoto_1990_forsterite_liquid_alpha": ("range", ["0.20", "0.21"], 3),
    "hashimoto_1990_forsterite_solid_sio2_mg_o_alpha": ("range", ["0.4", "0.6"], 3),
    "hashimoto_1990_forsterite_solid_sio2_mg_o2_alpha": ("range", ["0.3", "0.5"], 3),
    "hashimoto_1990_forsterite_liquid_sio2_mg_o_alpha": ("alpha", "0.8", 3),
    "hashimoto_1990_forsterite_liquid_sio2_mg_o2_alpha": ("alpha", "0.6", 3),
    "hashimoto_1990_forsterite_alpha_1700c_extrapolated": ("alpha", "0.12", 3),
    "hashimoto_1990_forsterite_alpha_1400c_extrapolated": ("alpha", "0.04", 3),
}

_FIGURE_SERIES = {
    "hashimoto_1990_mgo_fig1_fluxes": [
        "J_v,s: free evaporation rate from solid MgO",
        "J_e,s: theoretical maximum rate for Mg species",
        "J_e-,s: theoretical rate for the specific MgO gas mechanism",
    ],
    "hashimoto_1990_sio2_fig1_fluxes": [
        "J_v,s: free evaporation rate from solid silica",
        "J_v,l: free evaporation rate from liquid silica",
        "J_e,s and J_e,l: theoretical maximum rates for Si-bearing gases",
        "J_e-,s and J_e-,l: rates for the specific SiO2(g) mechanism",
    ],
    "hashimoto_1990_forsterite_fig1_fluxes": [
        "J_v,s and J_v,l: free evaporation rates from solid and liquid forsterite",
        "J_e,s and J_e,l: theoretical maximum rates for equilibrium vapour species",
        "J_e-,s and J_e-,l: rates for the specified MgO and SiO2 vapour mechanism",
    ],
}


def _observations(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        observation["observation_id"]: (formula, observation)
        for formula, species in document["species"].items()
        for observation in species.get("observations", [])
    }


def _context(document: dict) -> dict[str, tuple[str, dict]]:
    return {
        item["observation_id"]: (formula, item)
        for formula, species in document["species"].items()
        for item in species.get("context", [])
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 16
    assert len(_ALPHA_PINS) == 14
    for observation_id, (field, expected, page) in _ALPHA_PINS.items():
        observation = observations[observation_id][1]
        values = observation["values"]
        actual = values["alpha_range"] if field == "range" else values["alpha"]
        assert actual == expected, observation_id
        assert observation["locator"]["page"] == page, observation_id
        assert values["quantity"] == "evaporation_coefficient_alpha"
        assert values["coefficient_definition_as_printed"] == "alpha_v = J_v/J_e"

    alpha_12 = observations[OUT_OF_DOMAIN_ID][1]
    assert alpha_12["values"]["status"] == "typed_refusal"
    assert alpha_12["values"]["admission_status"] == "typed_refusal"
    assert (
        "impossible, according to the definition of alpha_v"
        in alpha_12["values"]["reason"]
    )
    assert (
        "underestimation of the experimental surface area"
        in alpha_12["values"]["reason"]
    )
    assert alpha_12["locator"]["page"] == 3
    assert alpha_12["T_range_K"] == ["2173.15", "2373.15"]
    assert alpha_12["values"]["T_range_C_as_printed"] == ["1900", "2100"]
    assert alpha_12["T_range_locator"]["page"] == 2

    residue = observations["hashimoto_1990_forsterite_residue_glass_sio2"][1]
    assert residue["values"]["points"][0]["SiO2_wt_pct"] == "65"
    assert residue["units"] == "wt%"
    assert residue["locator"]["page"] == 3
    assert "Approximately 65 wt%" in residue["values"]["note"]
    assert residue["point_conditions"]["quench_state"]["state"]["value"] == "not_stated"

    volume_fraction = observations[SCHEMA_GAP_ID][1]
    assert volume_fraction["values"]["value_as_printed"] == "~5 vol%"
    assert volume_fraction["values"]["quantity"] == "forsterite_residue_glass_volume_fraction"
    assert volume_fraction["values"]["status"] == "typed_refusal"
    assert volume_fraction["point_conditions"]["quench_state"]["state"]["value"] == "not_stated"

    contexts = _context(document)
    assert set(contexts) == set(_FIGURE_SERIES) | {
        "hashimoto_1990_forsterite_residue_stoichiometry"
    }
    for observation_id, expected_series in _FIGURE_SERIES.items():
        figure = contexts[observation_id][1]
        assert figure["values"]["series_as_printed"] == expected_series
        assert "no values read" in figure["values"]["digitisation"]

    stoichiometry = contexts["hashimoto_1990_forsterite_residue_stoichiometry"][1]
    assert stoichiometry["values"]["solid_state_as_printed"].startswith("stoichiometric")
    assert stoichiometry["values"]["solid_T_range_C_as_printed"] == ["1600", "1800"]
    assert stoichiometry["values"]["molten_state_as_printed"].startswith("almost stoichiometric")


def test_render_checked_coefficients_residue_and_complete_row_count() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)

    mutated = deepcopy(document)
    _observations(mutated)["hashimoto_1990_forsterite_solid_alpha"][1]["values"]["alpha_range"][1] = "0.15"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_residue_glass_sio2_wt_percent_migrates_without_manual_division(tmp_path: Path) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    row = next(
        row
        for row in result.observations.values()
        if row.source_id == SID
        and row.observation_id.startswith(
            f"{SID}::hashimoto_1990_forsterite_residue_glass_sio2::"
        )
    )
    assert row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    assert row.identity.species.phase.value is Phase.GLASS
    assert row.value.point == Decimal("65")


def test_isolated_migration_keeps_printed_axes_and_ruled_quantity_refusals(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    result = migrate(isolated_root(tmp_path, document), write=False)
    assert result.validation is not None
    assert not result.validation.hard_issues

    rows = {
        row.observation_id.split("::", 2)[1]: row
        for row in result.observations.values()
        if row.source_id == SID
    }
    assert len(rows) == 16
    assert sum(row.value.kind.value == "unavailable" for row in rows.values()) == 2
    for observation_id, page in {
        "hashimoto_1990_mgo_solid_alpha": 1,
        "hashimoto_1990_sio2_solid_alpha": 2,
        OUT_OF_DOMAIN_ID: 3,
    }.items():
        assert rows[observation_id].locator is not None
        assert rows[observation_id].locator.page == page
    assert {key for key, row in rows.items() if row.identity.quantity.is_unknown} == {
        SCHEMA_GAP_ID,
        OUT_OF_DOMAIN_ID,
    }
    assert all(row.identity.species.phase.is_value for row in rows.values())

    for observation_id, (_, observation) in _observations(document).items():
        migrated = rows[observation_id]
        assert migrated.identity.species.formula == _observations(document)[observation_id][0]
        assert migrated.identity.species.phase.value in {Phase.CR, Phase.L, Phase.GLASS}
        if migrated.identity.quantity.is_value and (
            migrated.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
        ):
            composition = migrated.identity.composition
            assert composition is not None and composition.is_unknown
            assert "no composition mapped from source" in composition.reason
        assert "total_pressure_Pa" not in (observation.get("point_conditions") or {})
        assert "fO2_Pa" not in (observation.get("point_conditions") or {})
        assert not (
            migrated.identity.total_pressure_Pa
            and migrated.identity.total_pressure_Pa.is_value
        )
        assert not (migrated.identity.fO2_Pa and migrated.identity.fO2_Pa.is_value)
        temperature = migrated.identity.temperature_K
        if observation_id == OUT_OF_DOMAIN_ID:
            assert observation["T_range_K"] == ["2173.15", "2373.15"]
            assert observation["T_range_locator"]["page"] == 2
            assert temperature is None or not temperature.is_value
            continue
        if observation.get("T_range_K") is not None:
            assert "temperature_K" in profile_for(migrated.identity).required
            assert temperature is not None and temperature.is_unknown
            assert "no midpoint" in temperature.reason
        elif observation.get("T_C") is not None:
            assert temperature is not None and temperature.is_value
        else:
            assert temperature is None or not temperature.is_value

    assert rows["hashimoto_1990_forsterite_alpha_1700c_extrapolated"].identity.temperature_K.value == Decimal("1973.15")
    assert rows["hashimoto_1990_forsterite_alpha_1400c_extrapolated"].identity.temperature_K.value == Decimal("1673.15")
    assert rows["hashimoto_1990_forsterite_residue_glass_sio2"].identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    assert rows["hashimoto_1990_forsterite_residue_glass_sio2"].value.point == Decimal("65")

    out_of_domain = rows[OUT_OF_DOMAIN_ID]
    assert "source alpha 1.2 lies outside (0, 1]" in out_of_domain.identity.quantity.reason
    assert out_of_domain.value.unavailable_reason is not None
