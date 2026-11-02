from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

from tests.battery.test_migrate import _migrate_real_extract, _series_census
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/chatain-2021-nacro2-thermodynamics.yaml"
SOURCE_ID = "chatain-2021-nacro2-thermodynamics"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _by_id(rows: list[dict], observation_id: str) -> dict:
    return next(row for row in rows if row["observation_id"] == observation_id)


def test_render_pins_table_rows_and_detects_cell_mutation() -> None:
    document = _document()
    species = document["species"]["NaCrO2"]
    observations = species["observations"]
    table1 = [row for row in observations if row["locator"].get("table") == "1"]
    assert len(table1) == 15
    assert len({row["locator"]["row"] for row in table1}) == 11
    ref7_ii = _by_id(table1, "chatain_2021_table1_ref7_ii")
    assert ref7_ii["T_range_K"] == [809, 1040]
    assert ref7_ii["values"]["equation_as_printed"] == "-953.5+0.278xT (±3.3 kJ/mol)"

    experimental_table2 = _by_id(
        species["context"], "chatain_2021_table2_gdms_characterization"
    )
    commercial_table2 = _by_id(
        species["context"], "chatain_2021_table2_gdms_commercial_sample"
    )
    assert len(experimental_table2["values"]["experimental_rows"]) == 22
    assert len(commercial_table2["values"]["commercial_rows"]) == 22
    assert experimental_table2["locator"]["page"] == 9
    assert commercial_table2["locator"]["page"] == 9
    assert commercial_table2["caption_locator"]["page"] == 10
    assert experimental_table2["values"]["experimental_rows"][10] == {
        "element": "Fe",
        "impurity_content_ug_g": "250",
    }
    assert commercial_table2["values"]["commercial_rows"][10] == {
        "element": "Fe",
        "impurity_content_ug_g": "400",
    }
    synthesis = _by_id(
        species["context"], "chatain_2021_sample_synthesis_and_phase_identity"
    )["values"]["xrd_phase_identity"]
    assert synthesis["reference_cell_parameters_as_printed"].startswith(
        "a=2.9747±0.0001 Å"
    )
    assert synthesis["reference_cell_parameters_locator"] == {"page": 8, "figure": "3"}

    table3 = _by_id(species["context"], "chatain_2021_table3_dft_parameters")["values"]["rows"]
    assert len(table3) == 2
    table3_numeric_fields = (
        "Z",
        "a_A",
        "b_A",
        "c_A",
        "alpha_deg",
        "beta_deg",
        "gamma_deg",
        "volume_A3",
        "B0_GPa",
        "B0_prime",
    )
    assert sum(
        row[field] is not None for row in table3 for field in table3_numeric_fields
    ) == 17
    assert table3[1]["volume_A3"] == "127.022"
    assert table3[1]["B0_GPa"] == "121.14"

    table4 = _by_id(species["context"], "chatain_2021_table4_gamma_frequencies")["values"]["rows"]
    assert len(table4) == 14
    assert table4[-1] == {"frequency_cm_1": "580.187810", "degeneracy": "2"}
    entropy_function = _by_id(
        species["context"], "chatain_2021_theoretical_entropy_function"
    )
    assert "Σ[i=4..3n] x_i/(e^(x_i)-1)" in entropy_function["values"][
        "formula_as_printed"
    ]
    assert entropy_function["values"]["final_log_term_as_rendered"] == "x_D"

    observations = species["observations"]
    gross_enthalpy = _by_id(
        observations, "chatain_2021_gross_formation_enthalpy_298"
    )
    assert gross_enthalpy["locator"]["page"] == 3
    assert gross_enthalpy["point_conditions"]["temperature_K"]["locator"][
        "page"
    ] == 3
    reaction_enthalpies = _by_id(
        species["context"], "chatain_2021_reaction_enthalpy_context"
    )
    assert reaction_enthalpies["locator"]["page"] == 3
    assert reaction_enthalpies["values"]["gross_et_al_locator"]["page"] == 3
    assert reaction_enthalpies["values"]["van_vuuren_locator"]["page"] == 4

    entropy_29815 = _by_id(
        observations, "chatain_2021_absolute_standard_entropy_29815"
    )
    assert entropy_29815["locator"]["page"] == 13
    assert entropy_29815["point_conditions"]["temperature_K"]["state"][
        "value"
    ] == "298.15"
    entropy_298 = _by_id(
        observations, "chatain_2021_absolute_standard_entropy_298"
    )
    assert entropy_298["locator"]["page"] == 15
    assert entropy_298["point_conditions"]["temperature_K"]["state"][
        "value"
    ] == "298"
    assert "Both printed temperatures are retained" in entropy_298["values"][
        "temperature_discrepancy_note"
    ]

    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    mutated = copy.deepcopy(document)
    mutated_table3 = _by_id(
        mutated["species"]["NaCrO2"]["context"],
        "chatain_2021_table3_dft_parameters",
    )["values"]["rows"]
    mutated_table3[1]["volume_A3"] = "127.023"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 3")


def test_isolated_real_migration_and_j01_series_census(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 26
    assert check_all_fidelity_samples_match(document, label="post-migration source pins") == []

    observations = list(result.observations.values())
    unavailable = [row for row in observations if row.value.kind.value == "unavailable"]
    points = [row for row in observations if row.value.kind.value == "point"]
    assert len(unavailable) == 20
    assert len(points) == 6
    assert all(row.identity.quantity.tag.value == "value" for row in observations)
    assert all(row.identity.species.phase.tag.value == "value" for row in observations)
    assert all(row.identity.total_pressure_Pa.tag.value != "value" for row in observations)
    assert all(row.identity.fO2_Pa.tag.value != "value" for row in observations)
    assert all("total_pressure_Pa" not in (row.point_conditions or {}) for row in observations)
    assert all("fO2_Pa" not in (row.point_conditions or {}) for row in observations)

    by_suffix = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in observations
    }
    assert str(by_suffix["chatain_2021_gross_formation_enthalpy_298"].value.point) == "-874.2"
    assert str(by_suffix["chatain_2021_jain_dft_formation_enthalpy_298"].value.point) == "-883.1"
    assert str(by_suffix["chatain_2021_theoretical_cp_29815"].value.point) == "82.9"
    assert str(by_suffix["chatain_2021_absolute_standard_entropy_29815"].value.point) == "94.5"
    assert str(by_suffix["chatain_2021_absolute_standard_entropy_298"].value.point) == "94.53"
    assert str(by_suffix["chatain_2021_dta_transition_temperature"].value.point) == "1092.15"

    for observation_id, temperature, page in (
        ("chatain_2021_gross_formation_enthalpy_298", "298", 3),
        ("chatain_2021_jain_dft_formation_enthalpy_298", "298", 4),
        ("chatain_2021_theoretical_cp_29815", "298.15", 13),
        ("chatain_2021_absolute_standard_entropy_29815", "298.15", 13),
        ("chatain_2021_absolute_standard_entropy_298", "298", 15),
    ):
        observation = by_suffix[observation_id]
        condition = observation.point_conditions["temperature_K"]
        assert condition.state.value == Decimal(temperature)
        assert condition.locator.page == page
        assert observation.locator.page == page
        assert observation.identity.temperature_K.value == Decimal(temperature)

    gross_identity = by_suffix["chatain_2021_gross_formation_enthalpy_298"].identity
    cp_identity = by_suffix["chatain_2021_theoretical_cp_29815"].identity
    transition_identity = by_suffix["chatain_2021_dta_transition_temperature"].identity
    transition_point = by_suffix["chatain_2021_dta_transition_temperature"]
    transition_temperature = transition_point.point_conditions["temperature_K"]
    assert transition_temperature.state.value == Decimal("1092.15")
    assert transition_temperature.locator.page == 2
    assert gross_identity.composition.is_not_applicable
    assert gross_identity.total_pressure_Pa.is_not_applicable
    assert gross_identity.fO2_Pa.is_not_applicable
    assert cp_identity.composition.is_not_applicable
    assert cp_identity.total_pressure_Pa.is_not_applicable
    assert cp_identity.fO2_Pa.is_not_applicable
    assert transition_identity.composition.is_not_applicable
    assert transition_identity.total_pressure_Pa.is_unknown
    assert transition_identity.fO2_Pa.is_not_applicable
    assert transition_identity.temperature_K.is_not_applicable

    literature_root = tmp_path / "tree" / "data" / "literature"
    census, mismatches, n_numeric, n_unavailable, per_source = _series_census(
        literature_root / "extracts",
        literature_root / "extracts-v2",
    )
    assert census == {}
    assert mismatches == []
    assert n_numeric == 0
    assert n_unavailable == 0
    assert per_source.get(SOURCE_ID, {}) == {}
