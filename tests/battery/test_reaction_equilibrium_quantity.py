from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from simulator.battery.enums import (
    BoundDirection,
    BoundInclusivity,
    Engine,
    ExecutionState,
    IdentityEqualKind,
    Quantity,
    RefusalReason,
    ReferenceStateConvention,
    ResidualStatus,
    ValueKind,
)
from simulator.battery.identity import identity_equal, profile_for, quantity_token
from simulator.battery.migrate import (
    map_quantity,
    migrate,
    observation_from_plain,
    select_declared_source,
    to_plain,
)
from simulator.battery.records import Reaction, State
from simulator.battery.score import ScoreContext, compile_residual
from simulator.battery.validate import validate_observation


FIXTURE = Path(__file__).parent / "fixtures" / "t1522_reaction_equilibrium.yaml"


def _migrate_fixture(tmp_path: Path):
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    root = tmp_path
    extracts = root / "data" / "literature" / "extracts"
    extracts.mkdir(parents=True)
    (root / "data" / "literature" / "compilations").mkdir(parents=True)
    (extracts / FIXTURE.name).write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    source = doc["source"]
    (root / "data" / "literature" / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [
                    {
                        "source_id": doc["source_id"],
                        "citation": source["citation"],
                        "doi": source["doi"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return migrate(root, write=False)


def test_reaction_equilibrium_migrates_exchange_compound_and_bound(
    tmp_path: Path,
) -> None:
    result = _migrate_fixture(tmp_path)
    exchange = result.observations[
        "t1522-reaction-fixture::exchange-point"
    ]
    compound = result.observations[
        "t1522-reaction-fixture::compound-point"
    ]
    bound = result.observations[
        "t1522-reaction-fixture::compound-minimum"
    ]

    assert quantity_token(exchange.identity) is Quantity.LOG10_K_REACTION
    assert exchange.identity.species is None
    assert exchange.value.kind is ValueKind.POINT
    assert exchange.value.point == -1.25
    assert exchange.identity.temperature_K.value == 1200
    assert exchange.provenance["reaction_as_printed"] == (
        "NaCl(l) + K(l) = KCl(l) + Na(l)"
    )
    assert quantity_token(compound.identity) is Quantity.LOG10_K_REACTION
    assert [term.coefficient for term in compound.identity.reaction.value.terms] == [
        -1,
        -1,
        1,
    ]
    assert all(term.standard_state.is_value for term in compound.identity.reaction.value.terms)
    assert bound.value.kind is ValueKind.BOUND
    assert bound.value.bound_value == 2
    assert bound.value.bound_direction is BoundDirection.LOWER
    assert bound.value.bound_inclusivity is BoundInclusivity.UNKNOWN
    assert bound.provenance["qualifier_as_published"] == "Min."
    restored = observation_from_plain(to_plain(bound))
    assert restored.identity.species is None
    assert restored.identity.reaction == bound.identity.reaction
    assert restored.value == bound.value


def test_reaction_bound_comparator_sets_direction_and_inclusivity() -> None:
    selection = select_declared_source(
        Quantity.LOG10_K_REACTION,
        "dimensionless",
        {"log10_K_reaction": 1, "bound_operator": ">="},
    )

    assert selection.value.kind is ValueKind.BOUND
    assert selection.value.bound_operator == ">="
    assert selection.value.bound_direction is BoundDirection.LOWER
    assert selection.value.bound_inclusivity is BoundInclusivity.INCLUDED


def test_reaction_identity_includes_term_standard_state_and_is_not_log10_kf(
    tmp_path: Path,
) -> None:
    result = _migrate_fixture(tmp_path)
    observation = result.observations[
        "t1522-reaction-fixture::compound-point"
    ]
    identity = observation.identity
    assert identity_equal(identity, identity).kind is IdentityEqualKind.EQUAL
    reaction_profile = profile_for(identity)
    assert {"reaction", "temperature_K"} <= reaction_profile.required
    assert "formation_elements" not in reaction_profile.required
    assert "per" in reaction_profile.permitted_not_applicable

    reaction = identity.reaction.value
    reordered = replace(
        identity, reaction=State.of(Reaction(terms=tuple(reversed(reaction.terms))))
    )
    assert identity_equal(identity, reordered).kind is IdentityEqualKind.EQUAL
    first_standard = reaction.terms[0].standard_state.value
    changed_standard = replace(
        reaction.terms[0],
        standard_state=State.of(
            replace(
                first_standard,
                convention=ReferenceStateConvention.HENRIAN_LIQUID,
            )
        ),
    )
    changed_standard_identity = replace(
        identity,
        reaction=State.of(
            Reaction(terms=(changed_standard, *reaction.terms[1:]))
        ),
    )
    assert (
        identity_equal(identity, changed_standard_identity).kind
        is IdentityEqualKind.IDENTITY_MISMATCH
    )
    changed_term = replace(reaction.terms[0], standard_state=None)
    changed = replace(
        identity,
        reaction=State.of(
            Reaction(terms=(changed_term, *reaction.terms[1:]))
        ),
    )
    assert identity_equal(identity, changed).kind is IdentityEqualKind.IDENTITY_UNKNOWN

    kf_profile = profile_for(replace(identity, quantity=Quantity.LOG10_KF))
    assert "formation_elements" in kf_profile.required
    assert identity_equal(
        replace(identity, quantity=Quantity.LOG10_KF),
        replace(identity, quantity=Quantity.LOG10_KF),
    ).kind is not IdentityEqualKind.EQUAL
    mapped, reason = map_quantity(
        "reaction_equilibrium_constant",
        {"quantity": "reaction_equilibrium_constant"},
    )
    assert reason is None
    assert mapped.value is Quantity.LOG10_K_REACTION


def test_missing_reaction_term_standard_state_is_identity_incomplete(
    tmp_path: Path,
) -> None:
    result = _migrate_fixture(tmp_path)
    observation = result.observations[
        "t1522-reaction-fixture::compound-point"
    ]
    identity = observation.identity
    reaction = identity.reaction.value
    incomplete = replace(
        observation,
        identity=replace(
            identity,
            reaction=State.of(
                Reaction(
                    terms=(
                        replace(reaction.terms[0], standard_state=None),
                        *reaction.terms[1:],
                    )
                )
            ),
        ),
    )
    issues = validate_observation(
        incomplete,
        result.experiments,
        {**result.observations, incomplete.observation_id: incomplete},
        result.works,
    )
    assert any(
        issue.reason is RefusalReason.IDENTITY_INCOMPLETE
        and "standard_state" in issue.path
        for issue in issues
    )


def test_reaction_equilibrium_scoring_refuses_without_dropping_source_value(
    tmp_path: Path,
) -> None:
    result = _migrate_fixture(tmp_path)
    observation = result.observations[
        "t1522-reaction-fixture::exchange-point"
    ]
    context = ScoreContext(
        works=result.works,
        experiments=result.experiments,
        observations=result.observations,
        root=tmp_path,
    )
    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        predict=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reaction K must not call a prediction route")
        ),
    )

    assert observation.value.point == -1.25
    assert residual.status is ResidualStatus.REFUSED
    assert residual.execution.state is ExecutionState.UNSUPPORTED
    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.UNSUPPORTED
    assert residual.refusal.detail["reason"] == "quantity_not_predicted"
