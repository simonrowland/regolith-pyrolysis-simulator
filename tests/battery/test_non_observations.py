"""Only positively declared context/reference rows leave observation work."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery import migrate as migrate_module
from simulator.battery import score as score_module
from simulator.battery.enums import AdmissionStatus, EvidenceClass, ValueKind
from simulator.battery.migrate import Migrator
from simulator.battery.non_observations import is_non_observation
from simulator.battery.records import Derivation, State, Value
from simulator.battery.score import ScoreContext, comparison_candidates
from tests.battery import factories as F


ROOT = Path(__file__).resolve().parents[2]
REAL_REFERENCE_RECORDS = (
    ("burcat", "records/BU-0001.json", "burcat:BU-0001"),
    (
        "fastchem-v3-1-3",
        "records/FC-C-0001.json",
        "fastchem-v3-1-3:FC-C-0001",
    ),
    (
        "kelley-1960-usbm-b584",
        "records/table-001.json",
        "kelley-1960-usbm-b584:table-001",
    ),
    (
        "kelley-king-1961-usbm-b592",
        "records/table-001-0001.json",
        "kelley-king-1961-usbm-b592:table-001-0001",
    ),
    (
        "pankratz-1984-usbm-b677",
        "records/table-0001.json",
        "pankratz-1984-usbm-b677:table-0001",
    ),
    (
        "pankratz-1987-usbm-b689",
        "records/page-0003.json",
        "pankratz-1987-usbm-b689:page-0003",
    ),
    (
        "robie-hemingway-1995-usgs-b2131",
        "auxiliary/atomic-weight-001.json",
        "robie-hemingway-1995-usgs-b2131:atomic-weight-001",
    ),
    ("sgte-unary", "records/AG-BCC_A2.yaml", "sgte-unary-5.0:AG-BCC_A2"),
)


def _unknown_unavailable(*, value_reason: str, admission: AdmissionStatus):
    work = F.work("source-stated")
    experiment = F.tabulation_experiment(work_id=work.work_id)
    observation = F.observation(
        "source-stated-row",
        experiment.experiment_id,
        replace(
            F.psat_identity("Na"),
            quantity=State.unknown("unsupported quantity 'source_observable'"),
        ),
        Decimal("12.9"),
        evidence=EvidenceClass.MEASURED_TABULATED,
        admission=admission,
        source_id="source-stated",
    )
    return replace(
        observation,
        value=Value(ValueKind.UNAVAILABLE, unavailable_reason=value_reason),
    )


@pytest.mark.parametrize(
    "value_reason,admission",
    (
        ("source value has no closed quantity token", AdmissionStatus.PENDING),
        (
            "unsupported quantity 'source_observable'",
            AdmissionStatus.ADMITTED,
        ),
        ("missing value", AdmissionStatus.REJECTED),
    ),
)
def test_unknown_quantity_and_unavailable_value_alone_do_not_mean_context(
    value_reason: str, admission: AdmissionStatus
) -> None:
    observation = _unknown_unavailable(
        value_reason=value_reason,
        admission=admission,
    )

    assert not is_non_observation(observation)

    work = F.work("numeric-unknown")
    experiment = F.tabulation_experiment(work_id=work.work_id)
    numeric_unknown = F.observation(
        "numeric-unknown-row",
        experiment.experiment_id,
        replace(
            F.psat_identity("Na"),
            quantity=State.unknown("quantity token is not mapped"),
        ),
        Decimal("12.9"),
        evidence=EvidenceClass.MEASURED_TABULATED,
        source_id="numeric-unknown",
    )
    known_unavailable = replace(
        F.observation(
            "known-unavailable-row",
            experiment.experiment_id,
            F.psat_identity("Na"),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="numeric-unknown",
        ),
        value=Value(ValueKind.UNAVAILABLE, unavailable_reason="value not mapped"),
    )
    assert not is_non_observation(numeric_unknown)
    assert not is_non_observation(known_unavailable)


def test_internal_ledgers_and_explicit_reference_roles_are_context() -> None:
    work = F.work("reference-role")
    experiment = F.tabulation_experiment(work_id=work.work_id)
    observation = F.observation(
        "declared-reference",
        experiment.experiment_id,
        F.psat_identity("Na"),
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="reference-role",
        derivation=Derivation(
            relation=(
                "compilation_role engine_reference_input=true, "
                "scoring_eligible=false"
            ),
            inputs=("record",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    unknown = _unknown_unavailable(
        value_reason="no closed quantity token",
        admission=AdmissionStatus.PENDING,
    )

    assert is_non_observation(observation)
    assert is_non_observation(
        unknown,
        "data/literature/species_rail_differential_ledger.yaml",
    )
    assert not is_non_observation(
        replace(
            observation,
            derivation=replace(
                observation.derivation,
                relation="compilation_role scoring_eligible=false",
            ),
        )
    )


def _migrate_source(source: str) -> tuple[Migrator, str]:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    if source == "yu_2025_hollow_anode_measurements":
        path = ROOT / "data/literature/mre_measurements.yaml"
        migrator._migrate_mre(path)
    else:
        filenames = {
            "shornikov-2002-aluminosilicate-solid-phases": (
                "shornikov-2002-aluminosilicate-solid-phases.yaml"
            ),
            "kume-2000-cao-activities": "kume-2000-cao-activities.yaml",
            "kang-2007-sio2-activity-cas-low-silica": (
                "kang-2007-sio2-activity-cas-low-silica.yaml"
            ),
        }
        path = ROOT / "data/literature/extracts" / filenames[source]
        migrator._migrate_extract(path)
    migrator.finalize()
    return migrator, path.relative_to(ROOT).as_posix()


YU_2025_IDS = {
    f"yu_2025_hollow_anode_measurements:{duration}:{observable}"
    for duration in ("one_hour", "three_hour", "twelve_hour")
    for observable in (
        "mre_anode_o2_mass_kg",
        "mre_applied_charge_C",
        "mre_extraction_efficiency_fraction",
        "mre_faradaic_efficiency_fraction",
    )
}


@pytest.mark.parametrize(
    "source,observation_id,expected_axes,expected_admission,cohort_suffixes,"
    "expected_candidate_count,expected_queue_count",
    (
        (
            "shornikov-2002-aluminosilicate-solid-phases",
            "shornikov_2002_mullite_delta_fH",
            {"value", "quantity", "temperature_K"},
            AdmissionStatus.PENDING,
            ("::shornikov_2002_mullite_delta_fH",),
            1,
            3,
        ),
        (
            "kume-2000-cao-activities",
            "kume_2000_table1_sample_1_metal_composition",
            {"value", "phase", "quantity", "method"},
            AdmissionStatus.PENDING,
            ("_metal_composition",),
            104,
            416,
        ),
        (
            "kang-2007-sio2-activity-cas-low-silica",
            "kang_2007_t2_sample_301_cu_assay",
            {"value", "quantity"},
            AdmissionStatus.ADMITTED,
            ("_cu_assay", "_slag_composition"),
            44,
            88,
        ),
    ),
)
def test_source_measurements_keep_queue_repairs_and_comparison_candidates(
    source: str,
    observation_id: str,
    expected_axes: set[str],
    expected_admission: AdmissionStatus,
    cohort_suffixes: tuple[str, ...],
    expected_candidate_count: int,
    expected_queue_count: int,
) -> None:
    migrator, source_path = _migrate_source(source)
    full_id = f"{source}::{observation_id}"
    queue = [entry for entry in migrator.result.queue if entry.source == source_path]
    cohort_ids = {
        key
        for key in migrator.result.observations
        if key.endswith(cohort_suffixes)
    }
    candidates = _assert_source_repair_and_candidate(
        migrator,
        source_path,
        full_id,
        expected_axes,
        expected_admission,
    )
    assert len(cohort_ids) == expected_candidate_count
    assert cohort_ids <= candidates
    assert len([entry for entry in queue if entry.observation_id in cohort_ids]) == (
        expected_queue_count
    )


def test_yu_2025_sidecar_keeps_all_twelve_repairs_and_candidates() -> None:
    source = "yu_2025_hollow_anode_measurements"
    migrator, source_path = _migrate_source(source)
    full_ids = YU_2025_IDS
    context = ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations=migrator.result.observations,
        origins={key: source_path for key in migrator.result.observations},
        root=ROOT,
    )
    candidates = {row.observation_id for row in comparison_candidates(context)}
    queue = [entry for entry in migrator.result.queue if entry.source == source_path]

    assert full_ids <= set(migrator.result.observations)
    assert full_ids <= candidates
    assert {
        migrator.result.observations[observation_id].admission.status
        for observation_id in full_ids
    } == {AdmissionStatus.PENDING}
    assert {entry.observation_id for entry in queue} == full_ids
    assert len(queue) == 36
    assert {axis for entry in queue for axis in entry.axes} == {
        "phase",
        "quantity",
        "value",
    }


def _assert_source_repair_and_candidate(
    migrator: Migrator,
    source_path: str,
    full_id: str,
    expected_axes: set[str],
    expected_admission: AdmissionStatus,
) -> set[str]:
    observation = migrator.result.observations[full_id]
    context = ScoreContext(
        works=migrator.result.works,
        experiments=migrator.result.experiments,
        observations=migrator.result.observations,
        origins={key: source_path for key in migrator.result.observations},
        root=ROOT,
    )
    candidates = {row.observation_id for row in comparison_candidates(context)}
    row_queue = [
        entry
        for entry in migrator.result.queue
        if entry.source == source_path and entry.observation_id == full_id
    ]

    assert observation.admission.status is expected_admission
    assert full_id in candidates
    assert {axis for entry in row_queue for axis in entry.axes} == expected_axes
    return candidates


def _assert_real_compilation_reference_rehomed(
    family: str,
    relative_record: str,
    observation_prefix: str,
) -> None:
    path = ROOT / "data/literature/compilations" / family / relative_record
    source_path = path.relative_to(ROOT).as_posix()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_compilation_file(path)
    before = [entry for entry in migrator.result.queue if entry.source == source_path]
    assert any("read_from" not in (entry.axes or ()) for entry in before)
    read_from_before = [
        entry for entry in before if "read_from" in (entry.axes or ())
    ]

    migrator.finalize()

    records = [
        observation
        for observation_id, observation in migrator.result.observations.items()
        if observation_id == observation_prefix
        or observation_id.startswith(observation_prefix + ":")
    ]
    assert records
    for observation in records:
        role = (observation.provenance or {}).get("compilation_role")
        assert isinstance(role, dict)
        assert role.get("engine_reference_input") is True
        assert role.get("validation_measurement") is False
        assert role.get("scoring_eligible") is False
        assert is_non_observation(observation, source_path)

    after = [entry for entry in migrator.result.queue if entry.source == source_path]
    assert all("read_from" in (entry.axes or ()) for entry in after)
    assert len(after) == len(read_from_before)


@pytest.mark.parametrize(
    "family,relative_record,observation_prefix", REAL_REFERENCE_RECORDS
)
def test_real_compilation_family_roles_rehome_only_comparison_repairs(
    family: str,
    relative_record: str,
    observation_prefix: str,
) -> None:
    _assert_real_compilation_reference_rehomed(
        family,
        relative_record,
        observation_prefix,
    )


def test_compilation_role_propagation_mutation_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migrate_module,
        "declares_non_scoring_compilation_reference",
        lambda _role: False,
    )
    with pytest.raises(AssertionError):
        _assert_real_compilation_reference_rehomed(*REAL_REFERENCE_RECORDS[0])


def test_queue_and_candidate_regressions_detect_unknown_unavailable_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "shornikov-2002-aluminosilicate-solid-phases"
    observation_id = f"{source}::shornikov_2002_mullite_delta_fH"
    expected_axes = {"value", "quantity", "temperature_K"}

    def wrongly_filter_unknown_unavailable(observation, _origin=None):
        return (
            observation.identity.quantity.is_unknown
            and observation.value.kind is ValueKind.UNAVAILABLE
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            migrate_module,
            "is_non_observation",
            wrongly_filter_unknown_unavailable,
        )
        migrator, source_path = _migrate_source(source)
        with pytest.raises(AssertionError):
            _assert_source_repair_and_candidate(
                migrator,
                source_path,
                observation_id,
                expected_axes,
                AdmissionStatus.PENDING,
            )

    migrator, source_path = _migrate_source(source)
    with monkeypatch.context() as patch:
        patch.setattr(
            score_module,
            "is_non_observation",
            wrongly_filter_unknown_unavailable,
        )
        with pytest.raises(AssertionError):
            _assert_source_repair_and_candidate(
                migrator,
                source_path,
                observation_id,
                expected_axes,
                AdmissionStatus.PENDING,
            )
