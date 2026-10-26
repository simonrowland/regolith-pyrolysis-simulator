from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import EvidenceClass, NoticeKind
from simulator.battery.migrate import Migrator, _notice_from_plain, to_plain
from simulator.battery.records import Derivation, Notice, State

from tests.battery.factories import (
    observation,
    o2_identity,
    tabulation_experiment,
    work,
)


def _migrator(tmp_path: Path) -> Migrator:
    return Migrator(tmp_path, aliases={}, index={})


def _finalize(migrator: Migrator) -> None:
    observation_work = work("observation-work")
    observation_work = replace(
        observation_work,
        source_files=replace(
            observation_work.source_files,
            files=tuple(
                asset
                for asset in observation_work.source_files.files
                if asset.asset_id != "pdf-1"
            ),
        ),
    )
    asset_work = work("asset-work")
    for work_record in (observation_work, asset_work):
        migrator.result.works[work_record.work_id] = work_record
        migrator._work_extra_assets[work_record.work_id].update(
            {asset.asset_id: asset for asset in work_record.source_files.files}
        )
    migrator.result.experiments["experiment-1"] = tabulation_experiment(
        "experiment-1", observation_work.work_id
    )
    migrator.finalize()


def _reduced(observation_id: str, parent_id: str):
    return observation(
        observation_id,
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=(parent_id,),
        derivation=Derivation(
            relation="author reduction",
            inputs=(parent_id,),
            parameters=(),
            output_unit="Pa",
        ),
    )


def test_measured_reduced_ancestry_downgrades_only_bad_observation_parents(
    tmp_path: Path,
) -> None:
    migrator = _migrator(tmp_path)
    measured_parent = observation(
        "measured-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    quoted_parent = observation(
        "quoted-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.QUOTED_ATTRIBUTED,
    )
    model_parent = observation(
        "model-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.MODEL_DERIVED,
    )
    unknown_parent = observation(
        "unknown-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    unknown_parent = replace(
        unknown_parent,
        evidence=replace(
            unknown_parent.evidence,
            class_=State.unknown("source does not state method_class"),
        ),
    )
    rows = {
        measured_parent.observation_id: measured_parent,
        quoted_parent.observation_id: quoted_parent,
        model_parent.observation_id: model_parent,
        unknown_parent.observation_id: unknown_parent,
        "reduced-measured-parent": _reduced("reduced-measured-parent", "measured-parent"),
        "reduced-quoted-parent": _reduced("reduced-quoted-parent", "quoted-parent"),
        "reduced-dangling-parent": _reduced("reduced-dangling-parent", "absent-parent"),
        "reduced-asset-parent": _reduced("reduced-asset-parent", "pdf-1"),
        "reduced-unknown-parent": _reduced("reduced-unknown-parent", "unknown-parent"),
        "model-child": observation(
            "model-child",
            "experiment-1",
            o2_identity(),
            "1",
            evidence=EvidenceClass.MODEL_DERIVED,
            derived_from=("model-parent",),
            derivation=Derivation(
                relation="model composition",
                inputs=("model-parent",),
                parameters=(),
                output_unit="Pa",
            ),
        ),
    }
    migrator.result.observations.update(rows)
    _finalize(migrator)
    assert all(
        asset.asset_id != "pdf-1"
        for asset in migrator.result.works["observation-work"].source_files.files
    )
    assert any(
        asset.asset_id == "pdf-1"
        for asset in migrator.result.works["asset-work"].source_files.files
    )

    unchanged_ids = (
        "reduced-measured-parent",
        "reduced-asset-parent",
        "reduced-unknown-parent",
        "model-child",
    )
    for observation_id in unchanged_ids:
        assert migrator.result.observations[observation_id].evidence.class_.value in {
            EvidenceClass.MEASURED_REDUCED,
            EvidenceClass.MODEL_DERIVED,
        }
        assert not any(
            notice.kind is NoticeKind.MEASURED_REDUCED_ANCESTRY_DOWNGRADED
            for notice in migrator.result.observations[observation_id].notices
        )

    for observation_id, parent_id in (
        ("reduced-quoted-parent", "quoted-parent"),
        ("reduced-dangling-parent", "absent-parent"),
    ):
        downgraded = migrator.result.observations[observation_id]
        assert downgraded.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert downgraded.evidence.attribution
        assert downgraded.value == rows[observation_id].value
        assert parent_id in (downgraded.derived_from or ())
        notice, = downgraded.notices
        assert notice.kind is NoticeKind.MEASURED_REDUCED_ANCESTRY_DOWNGRADED
        assert parent_id in notice.reason
        assert notice.origin == observation_id


def test_changing_a_measured_parent_to_quoted_mutates_child_class(tmp_path: Path) -> None:
    migrator = _migrator(tmp_path)
    parent = observation(
        "parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )
    child = _reduced("child", "parent")
    migrator.result.observations.update({"parent": parent, "child": child})

    _finalize(migrator)
    assert (
        migrator.result.observations["child"].evidence.class_.value
        is EvidenceClass.MEASURED_REDUCED
    )

    migrator.result.observations["parent"] = replace(
        parent,
        evidence=replace(parent.evidence, class_=State.of(EvidenceClass.QUOTED_ATTRIBUTED)),
    )
    _finalize(migrator)
    assert (
        migrator.result.observations["child"].evidence.class_.value
        is EvidenceClass.QUOTED_ATTRIBUTED
    )


def test_ancestry_notice_allows_unknown_quantity_only_for_its_kind(
    tmp_path: Path,
) -> None:
    migrator = _migrator(tmp_path)
    quoted_parent = observation(
        "quoted-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.QUOTED_ATTRIBUTED,
    )
    unknown_identity = replace(
        o2_identity(), quantity=State.unknown("extract quantity is not mapped")
    )
    child = observation(
        "unknown-quantity-child",
        "experiment-1",
        unknown_identity,
        "1",
        evidence=EvidenceClass.MEASURED_REDUCED,
        derived_from=(quoted_parent.observation_id,),
        derivation=Derivation(
            relation="author reduction",
            inputs=(quoted_parent.observation_id,),
            parameters=(),
            output_unit="Pa",
        ),
    )
    migrator.result.observations.update(
        {quoted_parent.observation_id: quoted_parent, child.observation_id: child}
    )

    _finalize(migrator)

    notice, = migrator.result.observations[child.observation_id].notices
    assert notice.kind is NoticeKind.MEASURED_REDUCED_ANCESTRY_DOWNGRADED
    assert notice.affected_quantities == ()
    assert _notice_from_plain(to_plain(notice)) == notice
    with pytest.raises(ValueError, match="affected_quantities is required"):
        Notice(
            kind=NoticeKind.FALLBACK,
            affected_quantities=(),
            reason="fallback notice requires a quantity",
            origin="test",
        )


def test_downgrade_propagates_through_measured_reduced_chain_independent_of_order(
    tmp_path: Path,
) -> None:
    migrator = _migrator(tmp_path)
    quoted_parent = observation(
        "quoted-parent",
        "experiment-1",
        o2_identity(),
        "1",
        evidence=EvidenceClass.QUOTED_ATTRIBUTED,
    )
    reduced_parent = _reduced("reduced-parent", quoted_parent.observation_id)
    reduced_child = _reduced("reduced-child", reduced_parent.observation_id)
    # Child-first order ensures the parent is downgraded later in the first pass.
    migrator.result.observations.update(
        {
            reduced_child.observation_id: reduced_child,
            reduced_parent.observation_id: reduced_parent,
            quoted_parent.observation_id: quoted_parent,
        }
    )

    _finalize(migrator)

    for observation_id in ("reduced-parent", "reduced-child"):
        downgraded = migrator.result.observations[observation_id]
        assert downgraded.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
        assert any(
            notice.kind is NoticeKind.MEASURED_REDUCED_ANCESTRY_DOWNGRADED
            for notice in downgraded.notices
        )
