from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.default_policy import resolve
from simulator.battery.enums import MethodToken, NoticeKind, Phase, Quantity
from simulator.battery.migrate import (
    map_method,
    map_phase,
    migrate,
    phase_for_sample_form,
)
from simulator.battery.score import flagged_strata
from simulator.battery.records import NoticePolicy
from tests.battery.test_migrate import (
    FIXTURE_EXTRACT,
    _migrate_real_extract,
    _write_min_tree,
)


def _fixture_result(
    tmp_path: Path,
    *,
    phase: str | None = "gas",
    quantity: Quantity | None = None,
):
    extract = _scalar_fixture_extract()
    observation = extract["species"]["Na"]["observations"][0]
    if phase is None:
        observation.pop("phase", None)
    else:
        observation["phase"] = phase
    if quantity is not None:
        observation["values"]["quantity"] = quantity.value
    return migrate(_write_min_tree(tmp_path, extract), write=False)


def _scalar_fixture_extract():
    extract = deepcopy(FIXTURE_EXTRACT)
    values = extract["species"]["Na"]["observations"][0]["values"]
    values.pop("series")
    values["T_K"] = 1200.0
    values["pressure_atm"] = 1.0
    return extract


def _only_observation(result):
    assert len(result.observations) == 1
    return next(iter(result.observations.values()))


def test_unstated_method_is_a_flagged_unspecified_token_not_a_queue_entry(
    tmp_path: Path,
) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw.pop("regime")
    raw["values"].pop("method", None)
    raw.pop("phase", None)

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)
    experiment = result.experiments[observation.experiment_id]

    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.UNSPECIFIED
    method_notice = next(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.METHOD_UNSTATED
    )
    assert "default=unspecified" in method_notice.reason
    assert "original_reason=source does not state method" in method_notice.reason
    assert method_notice.policy is not None
    method_policy = resolve("DP-106")
    assert method_notice.policy == NoticePolicy(**method_policy.payload["policy"])
    assert "policy_id" not in method_notice.reason
    assert "policy_version" not in method_notice.reason
    phase_notice = next(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.PHASE_INFERRED
    )
    assert "value=unspecified" in phase_notice.reason
    assert "original_reason=source does not state phase" in phase_notice.reason
    assert phase_notice.policy is not None
    phase_policy = resolve("DP-016", inferred_phase=Phase.UNSPECIFIED.value)
    assert phase_notice.policy == NoticePolicy(**phase_policy.payload["policy"])
    assert "policy_id" not in phase_notice.reason
    assert "policy_version" not in phase_notice.reason
    assert flagged_strata(observation.notices) == (
        NoticeKind.METHOD_UNSTATED.value,
        NoticeKind.PHASE_INFERRED.value,
        "proposed-policy",
    )
    assert not any(
        "method" in entry.axes and entry.observation_id == observation.observation_id
        for entry in result.queue
    )


def test_printed_electron_microprobe_regime_is_inferred_and_flagged(
    tmp_path: Path,
) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw["regime"] = (
        "electron microprobe analysis; analytical procedures as described by "
        "Matzen et al. (2013)"
    )
    raw["values"].pop("method", None)

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)
    experiment = result.experiments[observation.experiment_id]
    inferred = [
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.METHOD_INFERRED
    ]

    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.ELECTRON_MICROPROBE
    assert len(inferred) == 1
    assert "electron microprobe analysis" in inferred[0].reason
    assert "original_reason=" in inferred[0].reason
    inferred_policy = resolve(
        "DP-107", inferred_method=MethodToken.ELECTRON_MICROPROBE.value
    )
    assert inferred[0].policy == NoticePolicy(**inferred_policy.payload["policy"])
    assert flagged_strata(observation.notices) == (
        NoticeKind.METHOD_INFERRED.value,
        "proposed-policy",
    )
    assert not any(
        "method" in entry.axes and entry.observation_id == observation.observation_id
        for entry in result.queue
    )


def test_calorimetry_table_method_is_inferred_and_flagged(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw.pop("regime")
    raw["values"]["table_type"] = "calorimetry"

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)
    experiment = result.experiments[observation.experiment_id]

    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.CALORIMETRY
    method_notices = [
        notice for notice in observation.notices
        if notice.kind is NoticeKind.METHOD_INFERRED
    ]
    assert len(method_notices) == 1
    method_notice = method_notices[0]
    inferred_policy = resolve("DP-107", inferred_method=MethodToken.CALORIMETRY.value)
    assert method_notice.policy == NoticePolicy(**inferred_policy.payload["policy"])


def test_knudsen_cell_in_source_citation_does_not_infer_method(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    extract["source"]["citation"] = "Knudsen cell measurements of oxide vapours"
    raw = extract["species"]["Na"]["observations"][0]
    raw.pop("regime")
    second = deepcopy(raw)
    second["observation_id"] = "na_psat_second"
    extract["species"]["Na"]["observations"].append(second)

    root = _write_min_tree(tmp_path, extract)
    index_path = root / "data" / "literature" / "INDEX.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    index["sources"][0]["citation"] = extract["source"]["citation"]
    index_path.write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    result = migrate(root, write=False)
    observations = tuple(result.observations.values())

    assert len(observations) == 2
    assert all(
        result.experiments[observation.experiment_id].method.value
        is MethodToken.UNSPECIFIED
        for observation in observations
    )
    assert all(
        any(notice.kind is NoticeKind.METHOD_UNSTATED for notice in observation.notices)
        for observation in observations
    )
    assert all(
        next(
            notice for notice in observation.notices
            if notice.kind is NoticeKind.METHOD_UNSTATED
        ).policy == NoticePolicy(**resolve("DP-106").payload["policy"])
        for observation in observations
    )
    assert not any(
        notice.kind is NoticeKind.METHOD_INFERRED
        for observation in observations
        for notice in observation.notices
    )


def test_pending_cacl2_method_stays_unspecified_when_citation_names_two_methods(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(
        tmp_path, "pending-cacl2-kems-or-transpiration.yaml"
    )
    observation = next(
        row
        for row in result.observations.values()
        if "anchor_CaCl2_pure_Psat" in row.observation_id
    )
    experiment = result.experiments[observation.experiment_id]

    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.UNSPECIFIED
    method_notice = next(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.METHOD_UNSTATED
    )
    assert method_notice.policy == NoticePolicy(**resolve("DP-106").payload["policy"])
    assert not any(
        entry.observation_id == observation.observation_id and "method" in entry.axes
        for entry in result.queue
    )


def test_conflicting_method_phrases_remain_queued(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw["regime"] = "electron microprobe analysis and calorimetry"
    raw["values"].pop("method", None)

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)
    experiment = result.experiments[observation.experiment_id]

    assert experiment.method.is_unknown
    method_entries = [
        entry
        for entry in result.queue
        if entry.observation_id == observation.observation_id
        and "method" in entry.axes
    ]
    assert len(method_entries) == 1


def test_partial_pressure_without_phase_is_inferred_as_gas_and_flagged(
    tmp_path: Path,
) -> None:
    result = _fixture_result(tmp_path, phase=None, quantity=Quantity.P_PARTIAL)
    observation = _only_observation(result)

    assert observation.identity.species.phase.is_value
    assert observation.identity.species.phase.value is Phase.G
    assert any(
        notice.kind is NoticeKind.PHASE_INFERRED
        for notice in observation.notices
    )
    assert any(
        "original_reason=source does not state phase" in notice.reason
        for notice in observation.notices
        if notice.kind is NoticeKind.PHASE_INFERRED
    )
    assert flagged_strata(observation.notices) == (
        NoticeKind.PHASE_INFERRED.value,
    )


def test_activity_above_printed_liquidus_infers_liquid(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw.pop("phase", None)
    raw["values"]["quantity"] = Quantity.ACTIVITY.value
    raw["values"]["T_K"] = 1200.0
    raw["values"]["liquidus_K"] = 1100.0

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)

    assert observation.identity.species.phase.is_value
    assert observation.identity.species.phase.value is Phase.L
    phase_notice = next(
        notice
        for notice in observation.notices
        if notice.kind is NoticeKind.PHASE_INFERRED
    )
    assert "above liquidus" in phase_notice.reason
    assert phase_notice.policy is not None
    assert phase_notice.policy.value == Phase.L.value
    assert flagged_strata(observation.notices) == (NoticeKind.PHASE_INFERRED.value,)


def test_activity_at_printed_liquidus_remains_unspecified(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw.pop("phase", None)
    raw["values"]["quantity"] = Quantity.ACTIVITY.value
    raw["values"]["T_K"] = 1100.0
    raw["values"]["liquidus_K"] = 1100.0

    observation = _only_observation(
        migrate(_write_min_tree(tmp_path, extract), write=False)
    )
    assert observation.identity.species.phase.is_value
    assert observation.identity.species.phase.value is Phase.UNSPECIFIED


def test_phase_with_no_single_side_preserves_explicit_unknown(
    tmp_path: Path,
) -> None:
    result = _fixture_result(tmp_path, phase="cr,l")
    observation = _only_observation(result)

    assert observation.identity.species.phase.is_unknown
    assert observation.identity.species.phase.reason == (
        "phase string 'cr,l' is not in the closed automatic map"
    )
    assert not any(
        notice.kind is NoticeKind.PHASE_INFERRED
        for notice in observation.notices
    )
    phase_entries = [
        entry
        for entry in result.queue
        if "phase" in entry.axes
        and entry.observation_id == observation.observation_id
    ]
    assert len(phase_entries) == 1
    assert phase_entries[0].why == observation.identity.species.phase.reason


def test_transition_phase_defaults_to_flagged_unspecified(
    tmp_path: Path,
) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw["type"] = "transition_point"
    raw.pop("phase")
    raw["values"] = {
        "quantity": Quantity.TRANSITION_TEMPERATURE.value,
        "property_kind": "melting_point",
        "T_m_K": 1200.0,
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)
    phase_entries = [
        entry
        for entry in result.queue
        if entry.observation_id == observation.observation_id
        and "phase" in (entry.axes or ())
    ]

    assert observation.identity.species.phase.is_unknown
    assert "transition property_kind" in observation.identity.species.phase.reason
    assert len(phase_entries) == 1


def test_two_phase_label_uses_explicit_table_side_evidence(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw["phase"] = "cr,l"
    raw["values"]["T_m_K"] = 1100.0

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)

    assert observation.identity.species.phase.is_value
    assert observation.identity.species.phase.value is Phase.L
    assert any(
        notice.kind is NoticeKind.PHASE_INFERRED
        for notice in observation.notices
    )


def test_two_phase_labels_allow_spacing_before_temperature_inference(
    tmp_path: Path,
) -> None:
    for index, spelling in enumerate(("cr, l", "(c, l)")):
        extract = _scalar_fixture_extract()
        raw = extract["species"]["Na"]["observations"][0]
        raw["phase"] = spelling
        raw["values"]["T_m_K"] = 1100.0

        result = migrate(_write_min_tree(tmp_path / str(index), extract), write=False)
        observation = _only_observation(result)

        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.L
        assert any(
            notice.kind is NoticeKind.PHASE_INFERRED
            for notice in observation.notices
        )


def test_two_phase_transition_temperature_preserves_explicit_unknown(tmp_path: Path) -> None:
    extract = _scalar_fixture_extract()
    raw = extract["species"]["Na"]["observations"][0]
    raw["phase"] = "cr,l"
    raw["values"]["T_m_K"] = 1200.0

    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observation = _only_observation(result)

    assert observation.identity.species.phase.is_unknown
    assert observation.identity.species.phase.reason == (
        "phase string 'cr,l' is not in the closed automatic map"
    )
    assert not any(
        notice.kind is NoticeKind.PHASE_INFERRED
        for notice in observation.notices
    )


def test_added_phase_spellings_have_single_closed_phases() -> None:
    for spelling, expected in (
        ("g, singlet", Phase.G),
        ("g, triplet", Phase.G),
        ("g, doublet", Phase.G),
        ("liquid", Phase.L),
        ("melt", Phase.L),
        ("crystal", Phase.CR),
        ("aq, 100 H2O", Phase.AQ),
        ("aq, 1000 H2O", Phase.AQ),
        ("graphite", Phase.CR),
        ("condensed", Phase.UNSPECIFIED),
    ):
        phase, unmapped = map_phase(spelling)
        assert phase.is_value and phase.value is expected
        assert unmapped is None
    unspecified_method = map_method("unspecified")
    assert unspecified_method.is_value
    assert unspecified_method.value is MethodToken.UNSPECIFIED
    assert phase_for_sample_form("unspecified") is None
    assert phase_for_sample_form("condensed") is None


def test_willenborg_activity_assemblages_do_not_fix_liquid_phase(
    tmp_path: Path,
) -> None:
    name = "willenborg-2006-alkali-removal-thermodynamics.yaml"
    source = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "literature"
        / "extracts"
        / name
    )
    extract = yaml.safe_load(source.read_text(encoding="utf-8"))
    original = _migrate_real_extract(tmp_path / "original", name)
    original_rows = [
        row
        for row in original.observations.values()
            if any(
                local_id in row.observation_id
                for local_id in (
                    "willenborg_2006_alsi8na1_na2o_activity_1400",
                    "willenborg_2006_alsi8mg8na1_na2o_activity_1400",
                )
            )
    ]
    assert len(original_rows) == 2
    phase_facts = [
        (
            row.observation_id,
            row.identity.species.phase,
            tuple((notice.kind.value, notice.reason) for notice in row.notices),
        )
        for row in original_rows
    ]
    assert all(
        row.identity.species.phase.value is Phase.UNSPECIFIED for row in original_rows
    ), phase_facts
    assert all(
        any(notice.kind is NoticeKind.PHASE_INFERRED for notice in row.notices)
        for row in original_rows
    )
    phase_policy = resolve("DP-016", inferred_phase=Phase.UNSPECIFIED.value)
    assert all(
        next(
            notice for notice in row.notices
            if notice.kind is NoticeKind.PHASE_INFERRED
        ).policy == NoticePolicy(**phase_policy.payload["policy"])
        for row in original_rows
    )

    mutated = deepcopy(extract)
    rows_by_id = {
        row["observation_id"]: row
        for row in mutated["species"]["Na2O"]["observations"]
    }
    rows_by_id[
        "willenborg_2006_alsi8na1_na2o_activity_1400"
    ]["note"] = "The analyzed sample was partly molten."
    rows_by_id[
        "willenborg_2006_alsi8mg8na1_na2o_activity_1400"
    ]["note"] = "The analyzed sample contains mullite and cristobalite."
    changed = _migrate_real_extract(
        tmp_path / "mutated", name, extract_doc=mutated
    )
    changed_rows = [
        row
        for row in changed.observations.values()
        if row.observation_id in {item.observation_id for item in original_rows}
    ]
    assert len(changed_rows) == 2
    changed_by_id = {
        local_id: next(
            row for row in changed_rows if local_id in row.observation_id
        )
        for local_id in (
            "willenborg_2006_alsi8na1_na2o_activity_1400",
            "willenborg_2006_alsi8mg8na1_na2o_activity_1400",
        )
    }
    assert all(
        row.identity.species.phase.value is Phase.UNSPECIFIED
        for row in changed_by_id.values()
    )
