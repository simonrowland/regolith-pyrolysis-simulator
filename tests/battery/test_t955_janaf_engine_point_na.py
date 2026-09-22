"""t-955: engine_point is not_applicable for pure-substance engine-reference rows.

Owner ruling d-039: NIST-JANAF (and peer compilations that stamp the same
compilation_role + identity.composition:not_applicable markers) may score
MAGEMin/ThermoEngine via the pure-phase consumer; they are not engine_point
candidates. Detection is data-driven on those fields — never on a source-id
substring.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.enums import EvidenceClass
from simulator.battery.generators.bench import engine_point_requests
from simulator.battery.records import Derivation
from simulator.battery.waypoints import (
    GapReason,
    ReadinessStatus,
    consumer_readiness,
    is_pure_substance_engine_reference,
    pure_substance_engine_point_gap,
)
from tests.battery import factories


ROLE_RELATION = (
    "Direct tabulation; 0 printed cells blank or INFINITE; "
    "compilation_role engine_reference_input=true, scoring_eligible=false; "
    "circularity_warning=Do not validate an engine against a compilation it consumes."
)

# Peer USGS/USBM Gibbs tables stamp the same compilation_role three-tuple but
# omit the printed-cell accounting clause — must not flip under t-955.
PEER_USGS_RELATION = (
    "Direct B1259 tabulation; compilation_role engine_reference_input=true, "
    "scoring_eligible=false; circularity_warning=Do not validate an engine "
    "against a compilation it consumes."
)


def _pure_substance_observation(*, relation: str = ROLE_RELATION, composition_na: bool = True):
    from dataclasses import replace

    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition, State

    identity = factories.o2_identity()
    if not composition_na:
        identity = replace(
            identity,
            composition=State.of(
                Composition(
                    basis="oxide",
                    components=(("CaO", Decimal("1")),),
                    amount_basis=AmountBasis.MOLE_FRACTION,
                )
            ),
        )
    return factories.observation(
        "obs-pure-1",
        "exp-pure-1",
        identity,
        Decimal("0"),
        evidence=EvidenceClass.COMPILATION_ASSESSED,
        derivation=Derivation(
            relation=relation,
            inputs=("unknown:source",),
            parameters=(),
            output_unit="kJ_per_declared_mol_basis",
        ),
        source_id="some-compilation",
    )


def _bench_for(experiment):
    from simulator.battery.enums import BenchIdentityBasis
    from simulator.battery.records import Bench, BenchIdentity

    return Bench(
        id="bench-1",
        work_id=experiment.work_id or "work-1",
        identity=BenchIdentity(
            BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="test",
        ),
    )


def test_predicate_requires_all_three_conjuncts() -> None:
    obs = _pure_substance_observation()
    assert is_pure_substance_engine_reference(obs)

    # Missing engine_reference_input
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(relation="scoring_eligible=false only")
    )
    # scoring_eligible=true fails the conjunct
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(
            relation="compilation_role engine_reference_input=true, scoring_eligible=true"
        )
    )
    # composition is a value, not not_applicable
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(composition_na=False)
    )
    # Peer USGS/USBM relation (same compilation_role, no printed-cell clause).
    assert not is_pure_substance_engine_reference(
        _pure_substance_observation(relation=PEER_USGS_RELATION)
    )
    # Source-id substring must never be the detector.
    assert "janaf" not in obs.derivation.relation.lower()
    assert obs.source_id == "some-compilation"


def test_engine_point_not_applicable_with_typed_reason() -> None:
    experiment = factories.tabulation_experiment(experiment_id="exp-pure-1")
    bench = _bench_for(experiment)
    observation = _pure_substance_observation()
    readiness = {
        (item.consumer, item.engine): item
        for item in consumer_readiness(experiment, bench, observation)
    }
    engines = [item for key, item in readiness.items() if key[0] == "engine_point"]
    assert engines
    assert all(item.status is ReadinessStatus.NOT_APPLICABLE for item in engines)
    gap = pure_substance_engine_point_gap()
    for item in engines:
        assert item.gaps == (gap,)
        assert item.gaps[0].reason is GapReason.PURE_SUBSTANCE_REFERENCE
        assert "pure-substance reference table" in item.gaps[0].missing[0]
        assert "pure-phase" in item.gaps[0].missing[0]
    # No engine_point payload may be generated.
    assert all(result.payload is None for result in engine_point_requests(
        __import__("simulator.battery.consumer_inputs", fromlist=["collect_consumer_inputs"])
        .collect_consumer_inputs(experiment, bench, observation)
    ))


def test_without_markers_engine_point_still_gaps_on_missing_composition() -> None:
    experiment = factories.tabulation_experiment(experiment_id="exp-melt-1")
    bench = _bench_for(experiment)
    # No observation → no pure-substance markers → ordinary missing-composition gap.
    engines = [
        item for item in consumer_readiness(experiment, bench) if item.consumer == "engine_point"
    ]
    assert engines
    assert all(item.status is ReadinessStatus.GAP for item in engines)
    assert all(
        any(gap.reason is GapReason.MISSING_EVIDENCE for gap in item.gaps)
        for item in engines
    )


def test_mutation_predicate_is_what_drives_not_applicable(monkeypatch) -> None:
    """Revert the predicate → readiness falls back to GAP. Mutation proof."""

    experiment = factories.tabulation_experiment(experiment_id="exp-pure-1")
    bench = _bench_for(experiment)
    observation = _pure_substance_observation()

    import simulator.battery.waypoints as waypoints

    monkeypatch.setattr(waypoints, "is_pure_substance_engine_reference", lambda _obs: False)
    engines = [
        item
        for item in waypoints.consumer_readiness(experiment, bench, observation)
        if item.consumer == "engine_point"
    ]
    assert engines
    assert all(item.status is ReadinessStatus.GAP for item in engines), (
        "mutation: disabling the pure-substance predicate must restore GAP"
    )
