"""In-process restorations for the three canon checks remote CI can run whole.

The catalog still names pytest node ids. These tests apply the same source
edits inside the process. Production passes the check; the restoration makes
that check raise AssertionError with its marker.
"""

from __future__ import annotations

from decimal import Decimal
import inspect
import textwrap

import pytest

from simulator.battery import fo2_ladder, score
from simulator.battery.enums import AmountBasis, Phase, PhaseRole, Quantity
from simulator.battery.identity import Identity
from simulator.battery.records import Composition, Species, State
from tests.battery import factories as F
from tests.battery.test_printed_oxygen_identity import (
    test_registered_method_pressure_precedes_the_unbound_default,
    test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands,
)
from tests.mutations.canon_fold6_restore import (
    MUTATIONS,
    _LIQUID_PAIR,
    _OXYGEN_FRAME,
    _PRESSURE,
    _count,
)


def _patched(module, name: str, edits):
    source = textwrap.dedent(inspect.getsource(getattr(module, name)))
    hits = 0
    for _path, old, new in edits:
        count = source.count(old)
        if count == 0:
            continue
        if count != 1:
            raise AssertionError(f"{name} anchor count {count}")
        source = source.replace(old, new, 1)
        hits += 1
    if hits == 0:
        raise AssertionError(f"{name} contained none of the edits")
    symbols = dict(vars(module))
    exec(
        compile(
            "from __future__ import annotations\n" + source,
            f"<mutation {name}>",
            "exec",
        ),
        symbols,
    )
    return symbols[name], hits


def test_fold6_anchors_each_occur_once():
    for name, (edits, _node, _marker) in MUTATIONS.items():
        for path, old, _new in edits:
            found = _count(path, old)
            assert found == 1, f"{name}: {path.name} anchor count {found}"


def test_liquid_pair_restoration_turns_the_unlabelled_check_red(monkeypatch):
    composition = Composition(
        "oxide_wt_pct",
        (("SiO2", Decimal("100")),),
        AmountBasis.MASS_PERCENT,
    )
    identity = Identity(
        quantity=Quantity.EQUILIBRIUM_PHASE_COMPOSITION,
        species=Species("SiO2", Phase.L),
        composition=State.of(composition),
        phase_role=State.of(PhaseRole.MELT),
        temperature_K=State.of(Decimal("1400")),
        fO2_Pa=State.of(Decimal("1")),
        total_pressure_Pa=State.of(Decimal("100000")),
        subtype=State.of("wt_percent"),
    )
    unlabelled = F.observation(
        "unlabelled-melt", "pair-exp", identity, Decimal("1")
    )
    assert score._liquid_pair_target(unlabelled) is None, "unlabelled"
    mutated, hits = _patched(score, "_liquid_pair_target", _LIQUID_PAIR)
    assert hits == 1
    monkeypatch.setattr(score, "_liquid_pair_target", mutated)
    with pytest.raises(AssertionError, match="unlabelled"):
        assert score._liquid_pair_target(unlabelled) is None, "unlabelled"


def test_oxygen_frame_restoration_turns_the_printed_log_check_red(monkeypatch):
    test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands()
    mutated_tier1, tier_hits = _patched(fo2_ladder, "_is_tier1", _OXYGEN_FRAME)
    mutated_admit, admit_hits = _patched(
        fo2_ladder, "admit_waypoint_oxygen", _OXYGEN_FRAME
    )
    assert tier_hits == 1
    assert admit_hits == 1
    monkeypatch.setattr(fo2_ladder, "_is_tier1", mutated_tier1)
    monkeypatch.setattr(fo2_ladder, "admit_waypoint_oxygen", mutated_admit)
    with pytest.raises(AssertionError, match="printed_fO2_log"):
        test_unknown_frame_and_uncalibrated_buffer_are_not_clean_commands()


def test_pressure_precedence_restoration_fails_at_the_dp002_assertion(monkeypatch):
    test_registered_method_pressure_precedes_the_unbound_default()
    mutated, hits = _patched(score, "_condensed_pressure_for_score", _PRESSURE)
    assert hits == 2
    monkeypatch.setattr(score, "_condensed_pressure_for_score", mutated)
    with pytest.raises(AssertionError, match="DP-002"):
        test_registered_method_pressure_precedes_the_unbound_default()
