"""Source-grounded schema-gap inventory for Taniguchi et al. (1997)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import NoticeKind
from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _mass_percent_composition_state,
    wt_pct_to_mole_fraction,
)
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/taniguchi-1997-feo-cas.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _contexts(document: dict) -> list[dict]:
    return [
        context
        for body in document["species"].values()
        for context in body["context"]
    ]


def test_extract_preserves_input_tables_and_all_41_activity_coefficients() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    observations = [
        (species, observation)
        for species, body in document["species"].items()
        for observation in body["observations"]
    ]
    assert len(observations) == 141
    assert Counter(
        observation["locator"]["table"] for _, observation in observations
    ) == {"1": 7, "4": 100, "5": 34}
    assert all(
        observation["type"] == "equilibrium_phase_composition"
        and observation["values"]["quantity"] == "equilibrium_phase_composition"
        and observation["values"]["basis"] == "wt_percent"
        and observation["locator"]["page"]
        and observation["locator"]["published_page"]
        and observation["locator"]["pdf_page_index"] is not None
        for _, observation in observations
    )
    assert all(
        observation["values"]["component_amount"]
        == observation["values"]["value_as_published"]
        for _, observation in observations
    )
    for _, observation in observations:
        oxygen_locator = observation["values"]["oxygen_partial_pressure"]["locator"]
        assert oxygen_locator["page"] == observation["locator"]["page"]
        assert oxygen_locator["published_page"] == observation["locator"]["published_page"]
        if observation["locator"]["table"] == "1":
            assert oxygen_locator["column"] == "pO2"
        else:
            assert oxygen_locator["section"] == (
                f"Table {observation['locator']['table']} caption"
            )
    experiments = {experiment["experiment_id"]: experiment for experiment in document["experiments"]}
    for experiment_id, experiment in experiments.items():
        if "sample" not in experiment:
            continue
        sample_map = experiment["sample"]["printed_composition"]["state"]["value"]
        component_rows = {
            ("Fe" if observation["values"]["component_as_published"] == "T.Fe" else observation["values"]["component_as_published"]): observation["values"]["component_amount"]
            for _, observation in observations
            if observation["experiment"] == experiment_id
        }
        assert sample_map == component_rows

    sample_213_silica = next(
        observation
        for _, observation in observations
        if observation["observation_id"]
        == "taniguchi_1997_table4_sample_213_slag_sio2_composition"
    )
    assert sample_213_silica["values"]["component_amount"] == "6.52"
    assert sample_213_silica["values"]["value_as_published"] == "6.52"

    contexts = _contexts(document)
    coefficient_record = next(
        context
        for context in contexts
        if context["observation_id"]
        == "taniguchi_1997_activity_coefficients_schema_gap_t1382"
    )
    assert coefficient_record["schema_gap"] == "t-1382"
    coefficients = coefficient_record["values"]["rows"]
    assert len(coefficients) == 41
    assert Counter(row["method_class_as_published"] for row in coefficients) == {
        "measured_reduced": 35,
        "quoted_attributed": 6,
    }
    assert {
        (row["observation_id"], row["value_as_printed"])
        for row in coefficients
        if row["observation_id"]
        in {
            "taniguchi_1997_table1_sample_101_fe_activity_coefficient",
            "taniguchi_1997_table4_sample_201_feo_activity_coefficient",
            "taniguchi_1997_table5_sample_407_feo_activity_coefficient",
        }
    } == {
        ("taniguchi_1997_table1_sample_101_fe_activity_coefficient", "15.8"),
        ("taniguchi_1997_table4_sample_201_feo_activity_coefficient", "2.91"),
        ("taniguchi_1997_table5_sample_407_feo_activity_coefficient", "33.2"),
    }
    assert all(row["locator"]["page"] for row in coefficients)
    assert all(
        row["locator"]["page"] == 5
        for row in coefficients
        if row["locator"].get("table") == "5"
    )
    assert all(row["reduction_as_published"] for row in coefficients)
    assert all(
        "Eq. (8)" in row["reduction_as_published"]
        for row in coefficients
        if row["locator"].get("table") == "4" and row["T_K"] == "1873"
    )
    assert all(
        "Eqs. (6)-(7)" in row["reduction_as_published"]
        for row in coefficients
        if row["locator"].get("table") == "5"
    )
    assert all(
        row["method_class_as_published"] != "quoted_attributed" or row["attribution"]
        for row in coefficients
    )

    assert not any(context.get("schema_gap") == "t-1385" for context in contexts)
    zero = next(
        context
        for context in contexts
        if context["observation_id"]
        == "taniguchi_1997_table5_row401_caf2_zero_context"
    )
    assert zero["values"]["CaF2_mass_percent"] == "0"
    assert zero["locator"]["published_page"] == 960
    assert not any(
        observation["locator"].get("table") == "5"
        and observation["locator"].get("row") == "401"
        and observation["values"]["component_as_published"] == "CaF2"
        for _, observation in observations
    )
    assert len(document["experiments"]) == 64

    figures = [context for context in contexts if context.get("type") == "figure_record"]
    assert len(figures) == 8
    assert all(
        context["values"]["digitisation"] == "deferred to a figure pass"
        and context["values"]["series"]
        and all("marker_count" in series for series in context["values"]["series"])
        for context in figures
    )


def test_isolated_migration_retains_typed_rows_and_located_conditions() -> None:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = list(migrator.result.observations.values())
    assert len(observations) == 141
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert all(observation.admission.status.value == "admitted" for observation in observations)
    assert all(
        observation.identity.temperature_K.is_value
        and observation.identity.temperature_K.value.is_finite()
        and observation.point_conditions["T_C"].locator is not None
        and observation.point_conditions["T_C"].state.is_value
        and observation.point_conditions["fO2_Pa"].locator is not None
        and observation.point_conditions["fO2_Pa"].inference.relation == "atm_to_Pa"
        and observation.identity.fO2_Pa.is_value
        and observation.identity.total_pressure_Pa.is_unknown
        and observation.identity.total_pressure_Pa.reason
        == "no total_pressure_Pa mapped from source"
        and observation.point_conditions["composition"].locator is not None
        for observation in observations
    )
    accepted_compositions = [
        observation
        for observation in observations
        if observation.identity.composition.is_value
    ]
    refused_compositions = [
        observation
        for observation in observations
        if observation.identity.composition.is_unknown
    ]
    # b-704 refusals remain for partial/non-oxide assays; b-1128 admits the
    # four 102.35 wt% kept-oxide maps after renormalization.
    assert len(accepted_compositions) == 35
    assert len(refused_compositions) == 106
    assert all(
        "mass-percent composition is not usable:" in item.identity.composition.reason
        and (
            "kept sum=" in item.identity.composition.reason
            or "printed oxide wt% map has no positive mass"
            in item.identity.composition.reason
            or "unsupported component(s) above 0.5 wt%" in item.identity.composition.reason
            or "mass-percent composition exceeds the accepted kept-sum limit" in item.identity.composition.reason
        )
        for item in refused_compositions
    )
    renormalized_rows = 0
    for observation in observations:
        experiment = migrator.result.experiments[observation.experiment_id]
        printed_map = experiment.sample.printed_composition.state.value
        expected = {
            ("Fe" if name == "T.Fe" else name): Decimal(amount)
            for name, amount in printed_map.items()
        }
        # b-704 projects only the printed metallic-iron trace at or below the
        # 0.5 wt% dropped-component threshold from an otherwise closed assay.
        if expected.get("Fe", Decimal("0")) <= Decimal("0.5"):
            expected.pop("Fe", None)
        kept_total = sum(expected.values(), Decimal("0"))
        printed_oxides = dict(expected)
        if Decimal("102") < kept_total <= Decimal("105"):
            scale = Decimal("100") / kept_total
            expected = {name: amount * scale for name, amount in expected.items()}
        if observation.identity.composition.is_value:
            assert observation.identity.composition.value.amount_basis.value == "mass_percent"
            assert observation.identity.composition.value.as_map() == expected
            if kept_total > Decimal("102"):
                renormalized_rows += 1
                expected_moles = wt_pct_to_mole_fraction(printed_oxides)
                actual_moles = wt_pct_to_mole_fraction(
                    observation.identity.composition.value.as_map()
                )
                assert set(actual_moles.as_map()) == set(expected_moles.as_map())
                for species, expected_fraction in expected_moles.as_map().items():
                    assert abs(
                        actual_moles.as_map()[species] - expected_fraction
                    ) < Decimal("1e-26")
                notice = next(
                    notice
                    for notice in observation.notices
                    if notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
                    and notice.band == "95-102 wt% kept oxide total"
                )
                assert notice.original == kept_total
                assert "renormalized to 100 wt%" in notice.reason
                assert "d-336 item 2" in notice.reason
        elif (
            "unsupported component(s) above 0.5 wt%"
            in observation.identity.composition.reason
        ):
            iron = printed_map.get("T.Fe", printed_map.get("Fe"))
            assert iron is not None
            assert f"Fe={iron} wt%" in observation.identity.composition.reason
    assert renormalized_rows == 4
    # Sample 406 has S=102.35 after its 0.102 wt% Fe trace is dropped.
    printed = {
        "Fe": Decimal("0.102"),
        "CaO": Decimal("38.5"),
        "Al2O3": Decimal("57.5"),
        "CaF2": Decimal("6.35"),
    }
    normalized_state = _mass_percent_composition_state(
        printed, basis="printed_oxides", closure_context=printed
    )
    assert normalized_state.is_value
    normalized = normalized_state.value.as_map()
    assert normalized == {
        "CaO": Decimal("37.61602344894968246213971666"),
        "Al2O3": Decimal("56.17977528089887640449438202"),
        "CaF2": Decimal("6.204201270151441133365901319"),
    }
    molar_masses = {
        "CaO": Decimal("56.077000000000005"),
        "Al2O3": Decimal("101.960077"),
        "CaF2": Decimal("78.074806326"),
    }
    moles = {name: amount / molar_masses[name] for name, amount in normalized.items()}
    total_moles = sum(moles.values(), Decimal("0"))
    independent_mole_fractions = {
        name: amount / total_moles for name, amount in moles.items()
    }
    actual_mole_fractions = wt_pct_to_mole_fraction(normalized).as_map()
    assert set(actual_mole_fractions) == set(independent_mole_fractions)
    assert all(
        abs(actual_mole_fractions[name] - expected) < Decimal("1e-12")
        for name, expected in independent_mole_fractions.items()
    )
