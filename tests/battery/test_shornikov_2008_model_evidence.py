from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    Quantity,
    RefusalReason,
)
from simulator.battery.fo2_ladder import resolve_row_fo2
from simulator.battery.records import Located, State
from simulator.battery.score import ScoreContext, _bench_for_score, compile_residual
from simulator.battery.source_oxygen import SourceOxygenCondition, resolve_source_oxygen
from tests.battery.test_migrate import _migrate_real_extract


SOURCE_ID = "shornikov-2008-redox-oxide-evaporation"


def test_fe_neutral_table2_pressure_is_explicitly_model_derived(
    tmp_path: Path,
) -> None:
    source_id = SOURCE_ID
    result = _migrate_real_extract(tmp_path, f"{source_id}.yaml", write=False)
    rows = [
        observation
        for observation in result.observations.values()
        if observation.source_id == source_id
        and "shornikov_2008_t2_fe_neutral" in observation.observation_id
    ]
    assert len(rows) == 2
    assert result.validation is not None
    # b-979 / d-053: a stated derivation whose inputs are not machine-resolvable
    # is a hard conditional_field, and the prose is not minted as a pointer.
    # The five Table 1 neutral rows each name Gibbs-energy minimization and the
    # printed input "thermodynamic data cited as [19]":
    # shornikov_2008_t1_ca_neutral, shornikov_2008_t1_mg_neutral,
    # shornikov_2008_t1_fe_neutral, shornikov_2008_t1_al_neutral,
    # shornikov_2008_t1_sio_neutral.
    prose = "thermodynamic data cited as [19]"
    detail = (
        f"derived_from {prose!r} is prose, not a resolvable "
        "observation id; retained here, never minted as a pointer"
    )
    flagged = (
        "shornikov_2008_t1_ca_neutral",
        "shornikov_2008_t1_mg_neutral",
        "shornikov_2008_t1_fe_neutral",
        "shornikov_2008_t1_al_neutral",
        "shornikov_2008_t1_sio_neutral",
    )
    assert [
        (issue.path, issue.reason, issue.detail)
        for issue in result.validation.hard_issues
    ] == [
        (
            f"observation[{source_id}::{row}].derivation.inputs",
            RefusalReason.CONDITIONAL_FIELD,
            detail,
        )
        for row in flagged
    ]
    assert [row.value.point for row in rows] == [
        Decimal("0.014793450"),
        Decimal("0.043265775"),
    ]
    assert all(row.evidence.class_.is_value for row in rows)
    assert all(
        row.evidence.class_.value is EvidenceClass.MODEL_DERIVED for row in rows
    )
    parent_prefix = f"{source_id}::shornikov_2008_t2_feo_activity::T=2000.0:"
    assert all(row.derived_from for row in rows)
    assert all(
        all(parent.startswith(parent_prefix) for parent in row.derived_from)
        for row in rows
    )


def test_all_neutral_pressures_are_admitted_literature_model_rows(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, f"{SOURCE_ID}.yaml", write=False)
    neutral_ids = {
        *(
            f"shornikov_2008_t1_{oxide}_neutral"
            for oxide in ("ca", "mg", "fe", "al", "sio")
        ),
        *(
            f"shornikov_2008_t2_{oxide}_neutral"
            for oxide in ("o", "ca", "mg", "fe", "al", "sio")
        ),
    }
    rows = [
        row
        for row in result.observations.values()
        if row.source_id == SOURCE_ID
        and any(
            f"::{observation_id}::" in row.observation_id
            for observation_id in neutral_ids
        )
    ]

    assert len(rows) == 17
    assert all(row.admission.status is AdmissionStatus.ADMITTED for row in rows)
    assert all(
        row.evidence.class_.value is EvidenceClass.MODEL_DERIVED for row in rows
    )


@pytest.mark.parametrize(
    ("sample_cao_mole_fraction", "expected_fo2_pa", "expected_log10_fo2_bar"),
    (
        (Decimal("0.0907"), 1.1079083279633113e-5, -9.955496173097435),
        (Decimal("0.2129"), 1.4026448576148981e-5, -9.853052276157967),
    ),
    ids=("sample-1", "sample-2"),
)
def test_measured_atomic_oxygen_maps_the_mo_cell_fo2(
    tmp_path: Path,
    sample_cao_mole_fraction: Decimal,
    expected_fo2_pa: float,
    expected_log10_fo2_bar: float,
) -> None:
    result = _migrate_real_extract(tmp_path, f"{SOURCE_ID}.yaml", write=False)
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
    )

    def sample_row(observation_id: str):
        return next(
            row
            for row in context.observations.values()
            if row.source_id == SOURCE_ID
            and observation_id in row.observation_id
            and row.identity.composition is not None
            and row.identity.composition.is_value
            and dict(row.identity.composition.value.components)["CaO"]
            == sample_cao_mole_fraction
        )

    measured_o = sample_row("shornikov_2008_t2_o_reducing")
    target = sample_row("shornikov_2008_t2_sio_reducing")
    assert measured_o.evidence.class_.value is EvidenceClass.MEASURED_DIRECT
    assert not measured_o.derived_from
    assert result.validation is not None
    assert not any(
        issue.path.startswith(f"observation[{measured_o.observation_id}]")
        for issue in result.validation.hard_issues
    )
    experiment = context.experiments[target.experiment_id]
    bench = _bench_for_score(experiment, context.benches)
    selected = resolve_source_oxygen(target, context.observations.values(), bench)
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.identity.species.formula == "O2"
    assert float(selected.observation.value.point) == pytest.approx(
        expected_fo2_pa, rel=1e-10
    )
    assert (
        "fO2 from measured p(O), Mo effusion cell"
        in selected.observation.derivation.relation
    )
    assert selected.observation.provenance["source_oxygen_method"] == (
        "fO2 from measured p(O), Mo effusion cell"
    )

    mapped = replace(
        target,
        point_conditions={
            **(target.point_conditions or {}),
            "fO2_Pa": Located(
                State.of(selected.observation.value),
                locator=selected.observation.locator,
                inference=selected.observation.derivation,
            ),
        },
    )
    resolved = resolve_row_fo2(
        experiment, bench, mapped, quantity=Quantity.P_PARTIAL
    )
    assert resolved is not None
    assert resolved.tier == 1
    assert resolved.log10_fO2_bar == pytest.approx(expected_log10_fo2_bar, abs=1e-10)


def test_incomplete_sample_oxide_input_is_labeled_and_refused(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, f"{SOURCE_ID}.yaml", write=False)
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        benches=result.benches,
    )
    row = next(
        observation
        for observation in context.observations.values()
        if observation.source_id == SOURCE_ID
        and "shornikov_2008_t2_sio_neutral" in observation.observation_id
        and observation.identity.composition is not None
        and observation.identity.composition.is_value
        and dict(observation.identity.composition.value.components)["CaO"]
        == Decimal("0.0907")
    )
    incomplete = row.provenance["incomplete_composition"]
    assert incomplete["omitted_oxide_wt_pct_by_sample"] == {
        "1": "14.5",
        "2": "29.85",
    }
    assert incomplete["omitted_oxides"] == [
        "K2O",
        "Na2O",
        "TiO2",
        "Cr2O3",
        "P4O10",
    ]
    assert incomplete["engine_input"] == "wrong"
    assert row.provenance["source_disagreement"]["implied_gamma_FeO_by_sample"] == {
        "1": "0.35",
        "2": "0.027",
    }

    residual, _candidate = compile_residual(
        row,
        Engine.OPENIMCC,
        context=context,
        point_observations=tuple(context.observations.values()),
    )
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert residual.refusal.detail["reason"] == "missing_input"
    assert residual.refusal.detail["input"] == "composition"
    assert "source_oxygen_condition" in residual.refusal.detail
