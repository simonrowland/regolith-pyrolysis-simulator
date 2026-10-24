from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import EvidenceClass, RefusalReason
from tests.battery.test_migrate import _migrate_real_extract


def test_fe_neutral_table2_pressure_is_explicitly_model_derived(
    tmp_path: Path,
) -> None:
    source_id = "shornikov-2008-redox-oxide-evaporation"
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
