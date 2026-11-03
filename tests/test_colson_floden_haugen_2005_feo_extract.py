"""Render-pinned transcription and isolated migration for Colson et al. (2005)."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import AmountBasis, Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator, wt_pct_to_mole_fraction
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
SID = "colson-floden-haugen-2005-nio-feo-o2-activities"
EXTRACT = ROOT / f"data/literature/extracts/{SID}.yaml"

COMPOSITIONS = {
    "diop": ("56.0", "0.1", "17.8", "26.7", "0.66"),
    "gg": ("48.4", "8.0", "25.7", "16.1", "0.73"),
    "gg2%Al-A": ("51.7", "2.1", "28.9", "17.3", "0.69"),
    "gg2%Al-B": ("55.2", "2.4", "29.9", "11.6", "0.69"),
    "gg4%Al-A": ("51.1", "4.0", "27.4", "17.1", "0.70"),
    "gg4%Al-B": ("53.5", "4.6", "29.5", "11.2", "0.72"),
    "gg10Ca-A": ("51.6", "8.8", "29.5", "10.2", "0.58"),
    "gg14Ca-A": ("49.4", "8.2", "26.6", "14.4", "0.66"),
    "gg17Mg": ("55.8", "9.4", "16.5", "18.6", "0.78"),
    "gg22Mg": ("51.4", "8.7", "21.2", "17.8", "0.69"),
    "gg46Si": ("45.5", "8.3", "27.3", "17.7", "0.62"),
    "gg52Si*": ("51.7", "7.6", "25.4", "15.0", "0.60"),
    "PiFo13": ("49.5", "15.2", "14.4", "20.5", "0.65"),
    "P3AN13": ("48.0", "16.8", "10.4", "22.0", "0.64"),
    "P2Fo14": ("49.9", "11.2", "18.6", "18.8", "0.73"),
    "PiWo1350": ("49.0", "9.2", "4.2", "36.1", "0.69"),
}
OXIDES = ("SiO2", "Al2O3", "MgO", "CaO")
TABLE2_EPK = {
    "gg": ("1579", "-388", 3),
    "gg2%Al-A": ("1580", "-400", 5),
    "gg4%Al-A": ("1575", "-393", 7),
    "gg10Ca-A": ("1580", "-393", 9),
    "gg14Ca-A": ("1580", "-381", 10),
    "gg17Mg": ("1593", "-356", 11),
    "gg22Mg": ("1596", "-386", 12),
}
TABLE3_GAMMA = {
    "gg": ("1.00", 2, "gg"),
    "gg2%Al-A": ("0.99", 3, "gg2%Al-A"),
    "gg4%Al-A": ("0.78", 5, "gg4%Al-A"),
    "gg10Ca-A": ("0.92", 7, "gg10Ca-A"),
    "gg14Ca-A": ("1.02", 8, "gg14Ca-A"),
    "gg17Mg": ("0.81", 9, "gg17Mg"),
    "gg22Mg": ("0.70", 10, "gg22Mg"),
    "Diop": ("0.42", 17, "diop"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        observation["observation_id"]: observation
        for observation in document["species"]["FeO"]["observations"]
    }


def _assert_render_transcription(document: dict) -> None:
    contexts = document["species"]["FeO"]["context"]
    context_by_id = {row["observation_id"]: row for row in contexts}
    table1 = context_by_id[f"{SID}_table1_compositions"]["values"]["table_rows"]
    table2 = context_by_id[f"{SID}_table2_feo_rows"]["values"]["table_rows"]
    table3 = context_by_id[f"{SID}_table3_feo_rows"]["values"]["table_rows"]
    observations = _observations(document)

    assert len(table1) == 16, "Table 1 has 16 printed composition rows"
    assert len(table2) == 17, "Table 2 has 17 printed rows"
    assert len(table3) == 16, "Table 3 has 16 printed rows"
    assert len(observations) == 15  # seven typed-refused Epk and eight numeric gamma rows

    for row in table1:
        label = row["sample_as_published"]
        expected = COMPOSITIONS[label]
        comp = row["composition_as_published"]["components"]
        assert tuple(comp[oxide] for oxide in OXIDES) == expected[:4]
        assert row["NBO_as_printed"] == expected[4]
        assert row["composition_as_published"]["basis"] == "wt%"
    assert next(row for row in table1 if row["sample_as_published"] == "gg52Si*")[
        "marker_as_printed"
    ] == "*"

    t2_observations = _observations(document)
    assert sum(row["FeO_Epk_cell"]["state"] == "numeric_observation" for row in table2) == 7
    assert sum(row["FeO_Epk_cell"]["state"] == "absent" for row in table2) == 10
    assert next(row for row in table2 if row["sample_as_published"] == "ggᵃ")[
        "FeO_Epk_cell"
    ]["state"] == "absent"
    for sample, (temperature_c, epk, row_number) in TABLE2_EPK.items():
        oid = f"{SID}_p004_t02_r{row_number:02d}_c03"
        observation = t2_observations[oid]
        values = observation["values"]
        assert observation["locator"]["table"] == "2"
        assert observation["locator"]["page"] == 4
        assert observation["T_C"] == temperature_c
        assert values["value"] == epk
        assert values["Epk_as_printed"] == f"−{abs(int(epk))} mV"
        assert values["fO2_normalization_as_printed"] == (
            "calculated for fO2 = 1; values normalized for conditions in pure O2"
        )
        assert values["composition_as_published"]["components"] == dict(
            zip(OXIDES, COMPOSITIONS[sample][:4], strict=True)
        )

    assert sum(row["gamma_FeO_cell"]["state"] == "numeric_observation" for row in table3) == 8
    assert sum(row["gamma_FeO_cell"]["state"] == "absent" for row in table3) == 8
    for sample, (gamma, row_number, comp_key) in TABLE3_GAMMA.items():
        oid = f"{SID}_p007_t03_r{row_number:02d}_c04"
        observation = observations[oid]
        values = observation["values"]
        expected_comp = dict(
            zip(OXIDES, COMPOSITIONS[comp_key][:4], strict=True)
        )
        assert observation["locator"]["table"] == "3"
        assert observation["locator"]["page"] == 7
        assert values["gamma"] == gamma
        assert values["activity_coefficient_as_printed"] == gamma
        assert observation["standard_state_as_printed"] == 'Liquid wuestite ("FeO")'
        assert observation["standard_state"]["text_as_printed"] == 'Liquid wuestite ("FeO")'
        assert observation["standard_state"]["state"]["tag"] == "unknown"
        assert observation["standard_state"]["state"]["reason"] == (
            'liquid wuestite of unstated stoichiometry (paper writes "FeO" in quotes); '
            "no exact ReferenceStateConvention member; not substituted with pure liquid "
            "stoichiometric FeO."
        )
        assert observation["standard_state"]["free_energy_expression_source_as_printed"] == (
            "Barin (1989) after Holzheid et al. (1997)"
        )
        assert observation["standard_state"]["free_energy_expression_source_locator"] == {
            "page": 7,
            "pdf_page_index": 6,
            "published_page": 3067,
            "table": "3",
            "row": "footnote a",
        }
        assert observation["uncertainty_as_printed"] == "1σ = ±13%"
        assert values["composition_as_published"]["components"] == expected_comp
        assert values["composition_as_published"]["trace_FeO_addition_as_printed"].startswith("0.1–0.3 wt%")
        if sample == "Diop":
            assert values["marker_as_printed"] == "b"
            assert values["source_attribution"] == "Value calculated from measurements of Colson et al. (1990)."
            assert "T_C" not in observation
        else:
            temperature = observation["point_conditions"]["T_C"]
            assert temperature["state"]["value"] == TABLE2_EPK[sample][0]
            assert temperature["locator"]["table"] == "2"

    assert check_all_fidelity_samples_match(document) == []


def test_render_transcription_pins_all_cells_and_interior_mutations_fail() -> None:
    document = _document()
    _assert_render_transcription(document)

    changed = deepcopy(document)
    interior_gamma = changed["species"]["FeO"]["observations"]
    row = next(row for row in interior_gamma if row["observation_id"].endswith("_p007_t03_r08_c04"))
    row["values"]["gamma"] = "1.03"
    with pytest.raises(AssertionError):
        _assert_render_transcription(changed)

    changed_condition = deepcopy(document)
    row = next(
        row for row in changed_condition["species"]["FeO"]["observations"]
        if row["observation_id"].endswith("_p007_t03_r08_c04")
    )
    row["point_conditions"]["T_C"]["state"]["value"] = "1581"
    with pytest.raises(AssertionError):
        _assert_render_transcription(changed_condition)


def test_isolated_migration_keeps_every_numeric_value_and_printed_condition() -> None:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result

    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 15
    by_raw_id = {item.observation_id.rsplit("::", 1)[-1]: item for item in result.observations.values()}
    expected_unknown = {
        f"{SID}_p004_t02_r{row_no:02d}_c03" for _, _, row_no in TABLE2_EPK.values()
    }
    unknown = {
        raw_id for raw_id, item in by_raw_id.items()
        if item.identity.quantity.is_unknown
    }
    assert unknown == expected_unknown
    assert all(
        item.identity.species.phase.is_value and item.identity.species.phase.value is Phase.L
        for item in by_raw_id.values()
    )

    expected_k = {
        sample: Decimal(temperature_c) + Decimal("273.15")
        for sample, (temperature_c, _, _) in TABLE2_EPK.items()
    }
    expected_numeric = {}
    for sample, (gamma, row_no, comp_key) in TABLE3_GAMMA.items():
        raw_id = f"{SID}_p007_t03_r{row_no:02d}_c04"
        item = by_raw_id[raw_id]
        expected_numeric[raw_id] = Decimal(gamma)
        assert item.identity.quantity.is_value and item.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        assert item.value.kind is ValueKind.POINT and item.value.point == Decimal(gamma)
        assert item.evidence.original_method_class == "author_derived"
        assert item.evidence.class_.is_unknown
        assert "author derivation" in (item.evidence.class_.reason or "")
        assert not item.derived_from
        assert item.identity.species.phase.is_value and item.identity.species.phase.value is Phase.L
        assert item.identity.total_pressure_Pa.is_value and item.identity.total_pressure_Pa.value == Decimal("101325")
        assert item.identity.fO2_Pa.is_unknown
        assert item.identity.reference_state.is_unknown
        conditions = item.point_conditions
        assert conditions is not None
        assert conditions["composition"].state.is_value
        assert conditions["composition"].state.value.amount_basis is AmountBasis.MOLE_FRACTION
        assert conditions["total_pressure_Pa"].state.is_value
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["fO2_Pa"].state.is_unknown
        assert conditions["fO2_control"].state.is_unknown
        if sample == "Diop":
            assert item.identity.temperature_K.is_unknown
            assert conditions["temperature_K"].state.is_unknown
        else:
            assert item.identity.temperature_K.is_value
            assert item.identity.temperature_K.value == expected_k[sample]
            assert conditions["temperature_K"].state.is_value
            assert conditions["temperature_K"].state.value == expected_k[sample]
            assert conditions["temperature_K"].locator.table == "2"
        printed_comp = dict(zip(OXIDES, COMPOSITIONS[comp_key][:4], strict=True))
        expected_composition = wt_pct_to_mole_fraction(
            {name: Decimal(amount) for name, amount in printed_comp.items()}
        )
        assert conditions["composition"].state.value == expected_composition
        assert item.identity.composition.is_value
        assert item.identity.composition.value == expected_composition

    assert len(expected_numeric) == 8
    assert {
        raw_id: item.value.point
        for raw_id, item in by_raw_id.items()
        if item.value.kind is ValueKind.POINT
    } == expected_numeric
    unavailable = {
        raw_id: item for raw_id, item in by_raw_id.items()
        if item.value.kind is ValueKind.UNAVAILABLE
    }
    assert set(unavailable) == expected_unknown
    assert len(unavailable) == 7
    for sample, (temperature_c, epk, row_no) in TABLE2_EPK.items():
        raw_id = f"{SID}_p004_t02_r{row_no:02d}_c03"
        item = unavailable[raw_id]
        assert item.identity.temperature_K.is_value
        assert item.identity.temperature_K.value == Decimal(temperature_c) + Decimal("273.15")
        assert item.identity.total_pressure_Pa.is_value
        assert item.identity.total_pressure_Pa.value == Decimal("101325")
        assert item.identity.fO2_Pa is None or item.identity.fO2_Pa.is_unknown
        conditions = item.point_conditions
        assert conditions is not None
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].state.value == expected_k[sample]
        assert conditions["temperature_K"].locator.table == "2"
        printed_comp = dict(zip(OXIDES, COMPOSITIONS[sample][:4], strict=True))
        expected_composition = wt_pct_to_mole_fraction(
            {name: Decimal(amount) for name, amount in printed_comp.items()}
        )
        assert conditions["composition"].state.is_value
        assert conditions["composition"].state.value == expected_composition
        assert conditions["composition"].locator.table == "1"
        assert conditions["total_pressure_Pa"].state.is_value
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["fO2_Pa"].state.is_unknown
        assert conditions["fO2_control"].state.is_unknown

    assert all(item.admission.status.value == "admitted" for raw_id, item in by_raw_id.items() if raw_id not in expected_unknown)
    assert all(
        item.admission.status.value == "rejected"
        and "typed_refusal" in (item.admission.reason or "")
        for item in unavailable.values()
    )
