"""Regression tests for pause/resume idempotency at the decision gate.

Background. The simulator parks every run at engine-owned decision gates
(``sim.paused_for_decision`` / ``sim.pending_decision``). That gate is
ORTHOGONAL to the operator's pacing pause (``state['paused']`` in
``web/events.py``, ``SimSession._paused`` in ``simulator/session.py``): pacing
pause/resume only gate the wall-clock cadence of the background loop, never the
campaign branch routing.

A discarded fix (branch ``claude/interesting-kilby-4b44b5``, commit ``989110b``)
once coupled the two -- the gate set ``state['paused']`` and ``resume_simulation``
cleared it, so a resume issued while parked at a gate let the loop fall through
and re-emit the SAME pending ``decision_required``, opening a window for a stale
``make_decision`` to mis-route the campaign. ``main`` has since rewritten
``run_loop`` entirely: it drives one step at a time, and on reaching a gate it
sets ``state['paused'] = True``, emits ``decision_required`` exactly once, then
``break``s -- the background thread EXITS. ``make_decision`` is what applies the
answer to the *current* ``sim.pending_decision`` and restarts the loop.
``SimSession.pause()`` / ``resume()`` toggle only the pacing flag; neither
restarts the loop nor touches the gate (``simulator/session.py`` ``pause``/
``resume``), and ``resume_simulation`` deliberately does NOT restart the loop.

These tests drive the REAL handlers and the REAL background ``run_loop`` through
a Flask-SocketIO test client and assert that pacing pause/resume is idempotent
with respect to (a) re-emitting a parked gate or stepping past it unanswered,
and (b) routing / duplicating a campaign decision and the final ledger.

They are written against ``main``'s OBSERVABLE behavior, not the discarded
branch's: under ``main``, pause/resume DO emit a ``simulation_status`` even while
parked at a gate -- what they must NOT do is perturb the gate, advance the run,
or change the ledger. The old branch's "reject an out-of-gate choice" assertion
is intentionally not ported: ``apply_decision`` performs no option validation
(``simulator/core.py`` PATH_AB ``else`` -> Path B fallback), so routing
correctness is proven directly off ``sim.record.decisions`` instead.

Synchronization is STATE-driven, not sleep-timed: the negative assertions ("no
re-emit", "no tick") are taken only after the loop thread is provably dead
(``_wait_loop_settled``), so they can't false-pass on a fast box or false-fail
on a slow one. Determinism: the StubBackend is the deterministic baseline, so
two identical runs must produce a bit-identical completion payload; any
divergence under pause/resume churn is a real regression, surfaced loudly here.
"""

import time
from collections import Counter

import pytest

import app as app_module
from simulator.core import PyrolysisSimulator
from web.events import (
    _clear_simulation_state,
    _current_simulation_state,
    _simulations,
)


# SocketIO pause/resume loop has timing flakes under xdist coscheduling.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]


@pytest.fixture(autouse=True)
def _deterministic_liquidus_gate(monkeypatch):
    """Keep web state-machine tests independent of external liquidus latency."""
    curve = {
        'source': 'test_decision_pause_liquidus',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1700.0,
        'path': ((1000.0, 0.0), (1700.0, 1.0)),
    }
    monkeypatch.setattr(
        PyrolysisSimulator,
        '_freeze_gate_curve',
        lambda self: dict(curve),
    )

# StubBackend = deterministic baseline: no AlphaMELTS dependence (opt-in + slow
# here) and no float drift between runs. The decision-gate state machine is
# backend-independent, so the stub is both correct and fast for this test.
START_PARAMS = {
    'feedstock': 'lunar_mare_low_ti',
    'mass_kg': 1000,
    'backend': 'stub',
    'track': 'pyrolysis',
    'speed': 0,
    'c4_max_temp_C': 1670,
    'additives': {},
}

# Deterministic gate order for the params above (verified by driving the stub
# session to completion under AUTO_APPLY): three gates, each answered with its
# own recommendation; the run completes at hour 132.
EXPECTED_DECISIONS = ['PATH_AB', 'BRANCH_ONE_TWO', 'C6_PROCEED']

# Each recommendation, as routed by apply_decision to that exact gate. Read off
# the engine's own decision record to prove no mis-route / no duplicate. Exact
# equality is clean for a non-debug feedstock; a ``debug_*`` feedstock would also
# trip CampaignManager's auto-decision recorder and change what lands here.
EXPECTED_ROUTING = [
    ('PATH_AB', 'A_staged'),
    ('BRANCH_ONE_TWO', 'two'),
    ('C6_PROCEED', 'yes'),
]


def _make_client():
    app = app_module.create_app()
    c = app_module.socketio.test_client(app)
    assert c.is_connected()
    c.get_received()  # drain connect noise
    return c


@pytest.fixture
def client():
    before = set(_simulations)
    c = _make_client()
    try:
        yield c
    finally:
        if c.is_connected():
            c.disconnect()
        # Clear only the runs THIS test created -- never touch a neighbour's live
        # state in the shared module-global registry.
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def _start_and_get_sid(client, *, timeout=10.0):
    """Emit ``start_simulation`` and return the sid the run registered under."""
    before = set(_simulations)
    client.emit('start_simulation', START_PARAMS)
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = set(_simulations) - before
        if new:
            assert len(new) == 1, f'expected one new run, got {new}'
            return new.pop()
        time.sleep(0.02)
    raise AssertionError('simulation state never registered after start')


def _wait_for_event(client, name, *, timeout=20.0):
    """Poll until one queued event matches ``name``.

    Returns ``(matching_payload, drained_events)`` where ``drained_events`` is
    every event seen up to and including the match, in arrival order.
    """
    drained = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in client.get_received():
            drained.append(msg)
            if msg['name'] == name:
                return msg['args'][0], drained
        time.sleep(0.02)
    raise AssertionError(
        f'timed out waiting for {name!r}; saw {[m["name"] for m in drained]}')


def _wait_loop_settled(sid, *, timeout=10.0):
    """Block until the run's background loop thread has exited.

    The loop stores its thread in ``state['thread']`` and ``break``s out of the
    run loop at a decision gate (and on completion/error). A dead loop thread
    provably cannot emit another ``decision_required`` / ``simulation_tick``, so
    absence assertions taken AFTER this are race-free -- they do not depend on a
    fixed sleep being "long enough" under load. A regressed ``resume`` that
    re-couples the loop spawns a NEW live thread (stored back into
    ``state['thread']`` synchronously by the handler before its emit returns),
    which this call waits out and whose stray emits the caller's subsequent drain
    then catches.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state, _ = _current_simulation_state(sid)
        if state is None:
            return
        thread = state.get('thread')
        if thread is None or not thread.is_alive():
            return
        time.sleep(0.02)
    raise AssertionError('background loop thread never settled at the gate')


def _drive_to_completion(client, *, sid=None, perturb_each_gate=False, timeout=60.0):
    """Answer every gate with its recommendation.

    Returns ``(decisions, counts, completion, status_counts)`` where
    ``status_counts`` tallies ``simulation_status`` payloads by their ``status``.

    With ``perturb_each_gate`` (requires ``sid``) the test injects pacing
    pause+resume churn while parked at each gate BEFORE answering it, then waits
    for the loop to settle (``_wait_loop_settled``). Under ``main`` the loop has
    already broken out, so the churn is inert; a re-coupled loop's stray emit is
    observed deterministically rather than raced against a sleep.
    """
    if perturb_each_gate and sid is None:
        raise ValueError('perturb_each_gate requires sid')
    counts = Counter()
    status_counts = Counter()
    decisions = []
    completion = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in client.get_received():
            counts[msg['name']] += 1
            if msg['name'] == 'simulation_status':
                status_counts[msg['args'][0].get('status')] += 1
            if msg['name'] == 'decision_required':
                d = msg['args'][0]
                decisions.append(d['type'])
                if perturb_each_gate:
                    client.emit('pause_simulation')
                    client.emit('resume_simulation')
                    _wait_loop_settled(sid)
                client.emit('make_decision', {'choice': d['recommendation']})
            elif msg['name'] == 'simulation_complete':
                completion = msg['args'][0]
        if completion is not None:
            break
        time.sleep(0.02)
    assert completion is not None, 'run did not reach simulation_complete'
    return decisions, counts, completion, status_counts


def test_resume_while_parked_at_decision_does_not_re_emit(client):
    """(a) Resume while parked at a gate must not re-emit it or step past it."""
    sid = _start_and_get_sid(client)

    first, _ = _wait_for_event(client, 'decision_required')
    assert first['type'] == 'PATH_AB'
    _wait_loop_settled(sid)  # loop has parked + broken out at the gate

    # Hammer the pacing controls -- repeated resume is the exact gesture the
    # discarded bug re-emitted on. Correct code must NOT restart the loop.
    client.emit('pause_simulation')
    client.emit('resume_simulation')
    client.emit('resume_simulation')
    client.emit('pause_simulation')
    client.emit('resume_simulation')
    # Deterministic sync: wait until the loop is provably dead again. In correct
    # code resume never restarts it (returns at once); a re-coupled resume spawns
    # a live thread we wait out, whose stray decision_required the drain catches.
    _wait_loop_settled(sid)

    during = client.get_received()
    # Positive control: the churn must actually have reached a LIVE run (loop
    # parked at the gate but state['running'] still True, so handlers emit).
    # Without it the absence assertions below could pass vacuously against a
    # torn-down run.
    statuses = [
        m['args'][0].get('status') for m in during
        if m['name'] == 'simulation_status'
    ]
    assert 'paused' in statuses and 'resumed' in statuses, (
        f'pause/resume churn never reached a live run: {statuses}')
    re_emits = [m for m in during if m['name'] == 'decision_required']
    assert re_emits == [], f'gate was re-emitted after resume: {re_emits}'
    # An unanswered gate must not advance: the loop owes the operator a decision,
    # so not a single tick may be produced by the pacing churn.
    ticks = [m for m in during if m['name'] == 'simulation_tick']
    assert ticks == [], f'loop stepped past an unanswered gate: {len(ticks)} ticks'

    # The original gate is still answerable and routes the run to completion.
    client.emit('make_decision', {'choice': first['recommendation']})
    decisions, counts, completion, _ = _drive_to_completion(client)

    assert [first['type'], *decisions] == EXPECTED_DECISIONS
    # First gate was drained + answered above; the remainder come through here.
    assert counts['decision_required'] == len(EXPECTED_DECISIONS) - 1
    assert completion['mass_balance_error_pct'] == pytest.approx(0.0, abs=1e-3)



def test_null_make_decision_while_parked_at_path_ab_is_rejected(client):
    sid = _start_and_get_sid(client)

    first, _ = _wait_for_event(client, 'decision_required')
    assert first['type'] == 'PATH_AB'
    _wait_loop_settled(sid)

    client.emit('make_decision', None)
    received = client.get_received()
    statuses = [
        m['args'][0] for m in received
        if m['name'] == 'simulation_status'
    ]
    assert statuses
    assert statuses[-1]['status'] == 'error'
    assert 'make_decision payload must be an object' in statuses[-1]['message']
    applied = [
        m for m in received
        if (m['name'] == 'simulation_status'
            and m['args'][0].get('status') == 'decision_applied')
    ]
    assert applied == []

    state, _ = _current_simulation_state(sid)
    session = state['session']
    pending = session.pending_decision()
    assert pending is not None
    assert pending.decision_type.name == 'PATH_AB'
    assert session.simulator.record.decisions == []

    client.emit('make_decision', {'choice': first['recommendation']})
    decisions, counts, completion, _ = _drive_to_completion(client)

    assert [first['type'], *decisions] == EXPECTED_DECISIONS
    assert counts['decision_required'] == len(EXPECTED_DECISIONS) - 1
    assert completion['mass_balance_error_pct'] == pytest.approx(0.0, abs=1e-3)


def test_pause_resume_around_every_gate_is_ledger_identical(client):
    """(b) pause/resume churn at every gate -> one gate each, correct routing, identical ledger."""
    base_decisions, base_counts, base_completion, _ = _drive_to_completion(
        _started(client))
    assert base_decisions == EXPECTED_DECISIONS
    assert base_counts['decision_required'] == len(EXPECTED_DECISIONS)

    perturbed = _make_client()
    try:
        pert_sid = _start_and_get_sid(perturbed)
        (pert_decisions, pert_counts, pert_completion,
         pert_status_counts) = _drive_to_completion(
            perturbed, sid=pert_sid, perturb_each_gate=True)
        # Routing proof: every operator answer landed on the CORRECT gate, in
        # order, exactly once -- read straight off the engine's decision record,
        # which apply_decision appends to per applied choice.
        pert_state, _ = _current_simulation_state(pert_sid)
        routed = [
            (decision_type.name, choice)
            for decision_type, choice
            in pert_state['session'].simulator.record.decisions
        ]
    finally:
        if perturbed.is_connected():
            perturbed.disconnect()

    # Same gates, each surfaced exactly once despite pause/resume churn.
    assert pert_decisions == base_decisions == EXPECTED_DECISIONS
    assert pert_counts['decision_required'] == len(EXPECTED_DECISIONS)
    # Per-gate positive control: pause+resume reached a live run at EACH gate
    # (exactly one of each per gate), so the no-duplicate / identical-ledger
    # checks below are not vacuously satisfied by churn that silently no-op'd.
    assert pert_status_counts['paused'] == len(EXPECTED_DECISIONS)
    assert pert_status_counts['resumed'] == len(EXPECTED_DECISIONS)
    # No mis-route, no duplicate: each recommendation routed to its own gate.
    assert routed == EXPECTED_ROUTING
    # Bit-identical final ledger -- the strongest "no perturbation" guarantee.
    assert pert_completion == base_completion


def _started(client):
    """Start a run on ``client`` and hand the same client back for driving."""
    _start_and_get_sid(client)
    return client
