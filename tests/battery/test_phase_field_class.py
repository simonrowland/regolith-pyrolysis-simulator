"""t-1123a pins for kems-053 Stolyarova 1991 (regolith-main ruling 2026-10-06).

These pins were committed before the phase_field_class reader change. They
hold on green and must keep holding after it: classifying the x(SiO2) 0.33 and
0.25 rows as outside the single-liquid field must never retype a gas species,
never promote the point composition into identity.composition, never hide an
identity advisory, and never turn the x(SiO2) 0.36-0.40 rows away from numeric.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from simulator.battery.enums import Engine, Phase, Quantity, ResidualStatus
from simulator.battery.identity import quantity_token

SOURCE = "kems-053-stolyarova-1991"
EXTRACT = "kems-053-stolyarova-1991.yaml"
CLASS_S_X = frozenset({Decimal("0.33"), Decimal("0.25")})
CONTESTED_X = frozenset({Decimal("0.36"), Decimal("0.38"), Decimal("0.39"), Decimal("0.40")})


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    from tests.battery.test_migrate import _migrate_real_extract

    return _migrate_real_extract(tmp_path_factory.mktemp("stolyarova1991"), EXTRACT)


@pytest.fixture(scope="module")
def internal_analytical_residuals(migrated):
    # The internal-analytical vapour path needs openimcc (engine_reason
    # openimcc_unavailable otherwise), so these pins run where it is importable.
    pytest.importorskip("openimcc", reason="openimcc is not importable")
    from simulator.battery.score import ScoreContext, score_store

    context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
        extract_review={SOURCE: "draft"},
    )
    residuals, _candidates = score_store(
        context, engines=(Engine.INTERNAL_ANALYTICAL,), include_diagnostics=True
    )
    return {residual.reference: residual for residual in residuals}


def _x_sio2(observation) -> Decimal | None:
    located = (observation.point_conditions or {}).get("composition")
    if located is None or not located.state.is_value:
        return None
    return dict(located.state.value.components).get("SiO2")


def _printed_rows(migrated):
    return [
        observation
        for observation in migrated.observations.values()
        if observation.source_id == SOURCE and _x_sio2(observation) is not None
    ]


def test_stolyarova_1991_gas_rows_keep_phase_g(migrated) -> None:
    gas = [
        observation
        for observation in _printed_rows(migrated)
        if quantity_token(observation.identity) is Quantity.P_PARTIAL
    ]
    # Table Ia 4 x 11 + Table Ib SiO 2 x 9, SiO2 5, O 9.
    assert len(gas) == 76
    assert sum(_x_sio2(observation) in CLASS_S_X for observation in gas) == 16
    for observation in gas:
        phase = observation.identity.species.phase
        assert phase.is_value and phase.value is Phase.G, (
            observation.observation_id,
            phase,
        )


def test_stolyarova_1991_point_composition_is_not_promoted(migrated) -> None:
    rows = _printed_rows(migrated)
    assert len(rows) == 130
    assert sum(_x_sio2(observation) in CLASS_S_X for observation in rows) == 28
    for observation in rows:
        composition = observation.identity.composition
        assert composition is None or not composition.is_value, observation.observation_id


def test_stolyarova_1991_identity_advisories_stay_434_138_138_84(migrated) -> None:
    detail = Counter(
        str(issue.detail)
        for issue in migrated.validation.issues
        if str(issue.reason) == "identity_incomplete"
    )
    assert sum(detail.values()) == 434
    assert detail["required axis composition is unknown"] == 138
    assert detail["required axis fO2_Pa is unknown"] == 138
    assert detail["required axis total_pressure_Pa is unknown"] == 84


def test_stolyarova_1991_x036_to_x040_rows_stay_numeric(
    migrated, internal_analytical_residuals
) -> None:
    contested = [
        observation
        for observation in _printed_rows(migrated)
        if _x_sio2(observation) in CONTESTED_X
    ]
    assert len(contested) == 44
    numeric = [
        internal_analytical_residuals[observation.observation_id]
        for observation in contested
        if internal_analytical_residuals[observation.observation_id].numeric is not None
    ]
    # Green: 14 internal-analytical numeric residuals on these 44 rows
    # (t-1123 proposal section 4: 40 single-liquid numeric = 26 openimcc + 14 here).
    assert len(numeric) == 14
    assert all(residual.status is not ResidualStatus.REFUSED for residual in numeric)
