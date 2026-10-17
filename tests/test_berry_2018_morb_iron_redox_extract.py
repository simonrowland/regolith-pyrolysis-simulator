from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import (
    AdmissionStatus,
    Phase,
    Quantity,
    ValenceDenominatorKind,
    ValenceValueForm,
)
from simulator.battery.identity import ValenceBasis, profile_for
from simulator.battery.migrate import Migrator, _log_fO2_to_pa
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/berry-2018-morb-iron-redox.yaml"
SOURCE_ID = "berry-2018-morb-iron-redox"


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _migrate_extract():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_berry_table_rows_and_render_pins_are_preserved():
    source = _document()
    observations = {
        row["observation_id"]: row
        for row in source["species"]["Fe"]["observations"]
    }
    direct_295 = observations["berry_2018_table2_mossbauer_295K_fe3_sumfe"]
    direct_77 = observations["berry_2018_table2_mossbauer_77p5K_fe3_sumfe"]
    fitted = observations["berry_2018_table2_fit_fe3_sumfe"]
    model = observations["berry_2018_table2_model_a_fe3_sumfe"]

    assert len(direct_295["values"]["points"]) == 13
    assert len(direct_77["values"]["points"]) == 13
    assert len(fitted["values"]["points"]) == 13
    assert len(model["values"]["points"]) == 13
    assert len(source["species"]["Fe"]["context"][2]["values"]["centroid_edge_rows"]) == 13
    assert len(source["species"]["Fe"]["context"][1]["values"]["fit_conditions"]) == 9

    # Page 6 / printed page 119 render pins: fixed end-member (1), both
    # independent Mössbauer temperatures, the fitted value, and model^a.
    assert direct_295["values"]["points"][0]["value_as_published"] == "(1)"
    assert direct_295["values"]["points"][1]["value"] == 0.773
    assert direct_295["values"]["points"][11]["value_as_published"] == "(0)"
    assert direct_77["values"]["points"][1]["value"] == 0.769
    assert fitted["values"]["points"][2]["value"] == 0.670
    assert model["values"]["points"][12]["value_as_published"] == "0.006(1)"
    assert direct_295["locator"]["page"] == 6
    assert all(sample["locator"]["pdf_page_index"] == 5 for sample in source["fidelity_samples"])
    assert check_all_fidelity_samples_match(source, label=EXTRACT.stem) == []

    mutated = deepcopy(source)
    mutated["species"]["Fe"]["observations"][0]["values"]["points"][1]["value"] = 0.774
    assert check_all_fidelity_samples_match(mutated, label=EXTRACT.stem)


def test_berry_valence_migration_stamps_identity_conditions_and_quench():
    result = _migrate_extract()
    migrated = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith(f"{SOURCE_ID}::")
    ]

    assert len(migrated) == 52
    assert result.registry_issues == []
    assert result.validation.hard_issues == ()
    assert sum(obs.value.kind.value == "point" for obs in migrated) == 46
    assert sum(obs.value.kind.value == "unavailable" for obs in migrated) == 6
    assert all(obs.admission.status is AdmissionStatus.ADMITTED for obs in migrated)
    assert all(
        obs.identity.quantity.is_value
        and obs.identity.quantity.value is Quantity.VALENCE_RATIO
        for obs in migrated
    )
    expected_basis = ValenceBasis(
        numerator_oxidation_states=(3,),
        denominator_kind=ValenceDenominatorKind.TOTAL_ELEMENT,
        denominator_oxidation_state=None,
        value_form=ValenceValueForm.FRACTION,
    )
    assert all(
        obs.identity.valence_basis is not None
        and obs.identity.valence_basis.value == expected_basis
        for obs in migrated
    )
    required_axes = frozenset(
        {"composition", "fO2_Pa", "temperature_K", "total_pressure_Pa", "valence_basis"}
    )

    for obs in migrated:
        assert obs.identity.temperature_K is not None
        assert obs.identity.temperature_K.value == Decimal("1673.15")
        assert obs.identity.species.phase.value is Phase.GLASS
        assert obs.identity.fO2_Pa is not None and obs.identity.fO2_Pa.is_value
        assert obs.identity.total_pressure_Pa is not None
        assert obs.identity.total_pressure_Pa.is_value
        conditions = obs.point_conditions
        assert conditions is not None
        for name in (
            "T_C",
            "fO2_log",
            "delta_QFM",
            "total_pressure_Pa",
            "composition",
            "quench_state",
        ):
            assert name in conditions
            assert conditions[name].locator is not None
        assert conditions["T_C"].state.value == Decimal("1400")
        assert conditions["fO2_log"].state.is_value
        assert conditions["delta_QFM"].state.is_value
        assert conditions["total_pressure_Pa"].state.is_value
        assert conditions["composition"].state.is_value
        assert conditions["quench_state"].state.value == "not_stated"
        assert conditions["quench_state"].locator.page == 2
        assert obs.identity.composition is not None
        assert obs.identity.composition.is_value
        assert profile_for(obs.identity).required == required_axes

    row_1 = next(
        obs
        for obs in migrated
        if obs.point_conditions["fO2_log"].state.value == Decimal("4.8")
    )
    row_2 = next(
        obs
        for obs in migrated
        if obs.point_conditions["fO2_log"].state.value == Decimal("0.00")
    )
    assert row_1.point_conditions["total_pressure_Pa"].state.value == Decimal("1000000000")
    assert row_2.point_conditions["total_pressure_Pa"].state.value == Decimal("101325")
    row_3 = next(
        obs
        for obs in migrated
        if obs.point_conditions["fO2_log"].state.value == Decimal("-1.00")
    )
    for obs in (row_1, row_2, row_3):
        assert obs.identity.fO2_Pa.value == _log_fO2_to_pa(
            obs.point_conditions["fO2_log"].state.value
        )
        assert obs.identity.total_pressure_Pa.value == obs.point_conditions[
            "total_pressure_Pa"
        ].state.value
