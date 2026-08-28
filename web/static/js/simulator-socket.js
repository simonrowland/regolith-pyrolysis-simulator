/**
 * Socket connection and simulator status events.
 */

/**
 * Simulator UI — Plotly charts + SocketIO real-time updates
 */

const socket = io({
    transports: ['polling'],
    upgrade: false,
    reconnection: true,
    reconnectionAttempts: 5,
    reconnectionDelay: 500,
});
window.socket = socket;

function setConnectionReady(ready) {
    const startBtn = document.getElementById('btn-start');
    const pauseBtn = document.getElementById('btn-pause');
    if (startBtn && (!pauseBtn || pauseBtn.disabled)) {
        startBtn.disabled = !ready;
    }
}

setConnectionReady(false);

const _backendBadgeState = {
    active: null,
    status: null,
    authoritative: null,
    message: '',
};

const SimulatorLifecycleState = Object.freeze({
    IDLE: 'idle',
    STARTING: 'starting',
    RUNNING: 'running',
    REPLACING: 'replacing',
    TERMINAL_COMPLETE: 'terminal-complete',
    TERMINAL_REFUSED: 'terminal-refused',
    TERMINAL_ERROR: 'terminal-error',
    DISCONNECTED: 'disconnected',
});

const SimulatorLifecycleEvent = Object.freeze({
    CONNECT: 'connect',
    DISCONNECT: 'disconnect',
    CONNECT_ERROR: 'connect-error',
    CONNECTION_NOT_READY: 'connection-not-ready',
    START_REQUEST: 'start-request',
    PAUSE_REQUEST: 'pause-request',
    RESUME_REQUEST: 'resume-request',
    STATUS: 'status',
    TICK: 'tick',
    COMPLETE: 'complete',
});

const TERMINAL_LIFECYCLE_STATES = new Set([
    SimulatorLifecycleState.TERMINAL_COMPLETE,
    SimulatorLifecycleState.TERMINAL_REFUSED,
    SimulatorLifecycleState.TERMINAL_ERROR,
]);

let _simulatorLifecycle = {
    phase: SimulatorLifecycleState.IDLE,
    generation: 0,
    activeRunId: null,
    priorTerminal: null,
    retiredRunId: null,
    terminalText: '',
    disconnectedFrom: null,
};

function _payloadHasBackendField(data, key) {
    return Object.prototype.hasOwnProperty.call(data, key)
        && data[key] !== undefined
        && data[key] !== null
        && data[key] !== '';
}

function resetBackendBadgeState() {
    _backendBadgeState.active = null;
    _backendBadgeState.status = null;
    _backendBadgeState.authoritative = null;
    _backendBadgeState.message = '';
    const badge = document.getElementById('status-backend');
    if (badge) {
        badge.textContent = 'Backend: —';
        badge.className = 'backend-badge backend-badge-unknown';
        badge.title = 'Backend status not selected';
    }
}

function updateBackendBadge(data) {
    const badge = document.getElementById('status-backend');
    if (!badge || !data) return;

    let touched = false;
    if (_payloadHasBackendField(data, 'backend_active')) {
        _backendBadgeState.active = String(data.backend_active);
        touched = true;
    }
    if (_payloadHasBackendField(data, 'backend_status')) {
        _backendBadgeState.status = String(data.backend_status);
        touched = true;
    }
    if (Object.prototype.hasOwnProperty.call(data, 'backend_authoritative')
        && typeof data.backend_authoritative === 'boolean') {
        _backendBadgeState.authoritative = data.backend_authoritative;
        touched = true;
    }
    if (Object.prototype.hasOwnProperty.call(data, 'backend_status_message')
        || Object.prototype.hasOwnProperty.call(data, 'backend_status_reason')
        || Object.prototype.hasOwnProperty.call(data, 'backend_message')) {
        const msg = data.backend_status_message
            || data.backend_status_reason
            || data.backend_message
            || '';
        _backendBadgeState.message = String(msg);
        touched = true;
    }

    if (!touched) {
        return;
    }

    const active = _backendBadgeState.active;
    const status = _backendBadgeState.status;
    if (active == null && status == null) {
        badge.textContent = 'Backend: not emitted';
        badge.className = 'backend-badge backend-badge-unknown';
        badge.title = _backendBadgeState.message || 'Backend status not selected';
        return;
    }

    const activeLabel = active != null ? active : '—';
    const statusLabel = status != null ? status : '—';
    badge.textContent = `Backend: ${activeLabel} / ${statusLabel}`;
    if (_backendBadgeState.authoritative === true) {
        badge.className = 'backend-badge backend-badge-ok';
    } else if (_backendBadgeState.authoritative === false) {
        badge.className = 'backend-badge backend-badge-internal-analytical';
    } else if (!/\bbackend-badge-(ok|internal-analytical)\b/.test(badge.className)) {
        badge.className = 'backend-badge backend-badge-unknown';
    }
    badge.title = _backendBadgeState.message || '';
}

const ACTIVE_SIMULATION_STATUSES = new Set([
    'started',
    'running',
    'paused',
    'resumed',
    'decision_applied',
]);

function _effectiveLifecyclePhase(state) {
    return state.phase === SimulatorLifecycleState.DISCONNECTED
        ? (state.disconnectedFrom || SimulatorLifecycleState.IDLE)
        : state.phase;
}

function _lifecycleResult(
    state,
    statusText = null,
    resetBackend = false,
    acceptTelemetry = true,
) {
    return { state, statusText, resetBackend, acceptTelemetry };
}

function _lifecycleState(current, phase, fields = {}) {
    return {
        ...current,
        phase,
        disconnectedFrom: null,
        ...fields,
    };
}

function _liveLifecycleState(current, runId) {
    return _lifecycleState(current, SimulatorLifecycleState.RUNNING, {
        activeRunId: runId,
        priorTerminal: null,
        retiredRunId: null,
        terminalText: '',
    });
}

function _terminalLifecycleResult(current, terminalPhase, text, runId) {
    const phase = _effectiveLifecyclePhase(current);
    if (TERMINAL_LIFECYCLE_STATES.has(phase)) {
        return _lifecycleResult(current, current.terminalText, false, false);
    }
    if (!runId) {
        return _lifecycleResult(current, null, false, false);
    }
    if (
        phase === SimulatorLifecycleState.REPLACING
        && runId === current.activeRunId
    ) {
        return _lifecycleResult(_lifecycleState(
            current,
            SimulatorLifecycleState.REPLACING,
            { priorTerminal: { phase: terminalPhase, text, runId } },
        ), null, false, false);
    }
    if (
        current.activeRunId
        && runId !== current.activeRunId
        && phase !== SimulatorLifecycleState.REPLACING
    ) {
        return _lifecycleResult(current, null, false, false);
    }
    return _lifecycleResult(_lifecycleState(current, terminalPhase, {
        activeRunId: runId,
        priorTerminal: null,
        retiredRunId: null,
        terminalText: text,
    }), text);
}

function reduceSimulatorLifecycle(current, event) {
    const phase = _effectiveLifecyclePhase(current);

    if (event.type === SimulatorLifecycleEvent.CONNECT) {
        if (current.phase !== SimulatorLifecycleState.DISCONNECTED) {
            const text = current.phase === SimulatorLifecycleState.IDLE
                ? 'Ready'
                : null;
            return _lifecycleResult(current, text);
        }
        const restored = current.disconnectedFrom || SimulatorLifecycleState.IDLE;
        const next = _lifecycleState(current, restored);
        const text = TERMINAL_LIFECYCLE_STATES.has(restored)
            ? current.terminalText
            : (restored === SimulatorLifecycleState.IDLE
                ? 'Ready'
                : 'Connection restored');
        return _lifecycleResult(next, text);
    }

    if (
        event.type === SimulatorLifecycleEvent.DISCONNECT
        || event.type === SimulatorLifecycleEvent.CONNECT_ERROR
    ) {
        const from = current.phase === SimulatorLifecycleState.DISCONNECTED
            ? current.disconnectedFrom
            : current.phase;
        const next = {
            ...current,
            phase: SimulatorLifecycleState.DISCONNECTED,
            disconnectedFrom: from,
        };
        const text = TERMINAL_LIFECYCLE_STATES.has(from)
            ? current.terminalText
            : (event.type === SimulatorLifecycleEvent.CONNECT_ERROR
                ? 'Connection error'
                : 'Disconnected');
        return _lifecycleResult(next, text);
    }

    if (event.type === SimulatorLifecycleEvent.CONNECTION_NOT_READY) {
        return _lifecycleResult(current, 'Connection not ready');
    }

    if (event.type === SimulatorLifecycleEvent.START_REQUEST) {
        const replacing = (
            phase === SimulatorLifecycleState.RUNNING
            || phase === SimulatorLifecycleState.REPLACING
        ) && current.activeRunId !== null;
        const nextPhase = replacing
            ? SimulatorLifecycleState.REPLACING
            : SimulatorLifecycleState.STARTING;
        return _lifecycleResult(_lifecycleState(current, nextPhase, {
            generation: current.generation + 1,
            activeRunId: replacing ? current.activeRunId : null,
            priorTerminal: null,
            retiredRunId: null,
            terminalText: '',
        }), 'Running', !replacing);
    }

    if (
        event.type === SimulatorLifecycleEvent.PAUSE_REQUEST
        || event.type === SimulatorLifecycleEvent.RESUME_REQUEST
    ) {
        if (phase !== SimulatorLifecycleState.RUNNING) {
            return _lifecycleResult(current);
        }
        const text = event.type === SimulatorLifecycleEvent.PAUSE_REQUEST
            ? 'Paused'
            : 'Running';
        return _lifecycleResult(current, text);
    }

    if (event.type === SimulatorLifecycleEvent.COMPLETE) {
        return _terminalLifecycleResult(
            current,
            SimulatorLifecycleState.TERMINAL_COMPLETE,
            'Complete',
            (event.data || {}).run_id || null,
        );
    }

    if (event.type === SimulatorLifecycleEvent.TICK) {
        const data = event.data || {};
        const runId = data.run_id || null;
        if (!runId) {
            return _lifecycleResult(current, null, false, false);
        }
        if (
            TERMINAL_LIFECYCLE_STATES.has(phase)
            && (
                runId === current.activeRunId
                || runId === current.retiredRunId
            )
        ) {
            return _lifecycleResult(
                current,
                current.terminalText,
                false,
                false,
            );
        }
        const text = data.backend_fallback_active && data.backend_message
            ? data.backend_message
            : 'Running';
        if (
            phase === SimulatorLifecycleState.REPLACING
            && runId === current.activeRunId
        ) {
            return _lifecycleResult(current, text);
        }
        const changed = current.activeRunId !== null
            && runId !== current.activeRunId;
        return _lifecycleResult(
            _liveLifecycleState(current, runId),
            text,
            changed || phase === SimulatorLifecycleState.REPLACING,
        );
    }

    if (event.type !== SimulatorLifecycleEvent.STATUS) {
        return _lifecycleResult(current);
    }

    const data = event.data || {};
    const status = String(data.status || '').trim().toLowerCase();
    const text = event.statusText || String(data.status || '');
    const runId = data.run_id || null;
    const generation = Number.isInteger(data.lifecycle_generation)
        ? data.lifecycle_generation
        : null;
    const pending = (
        phase === SimulatorLifecycleState.STARTING
        || phase === SimulatorLifecycleState.REPLACING
    );
    const startCorrelated = status === 'started' || Boolean(data.error_type);
    if (
        startCorrelated
        && generation !== current.generation
        && (pending || generation !== null)
    ) {
        return _lifecycleResult(current, null, false, false);
    }

    if (status === 'started') {
        if (
            !runId
            || (
                phase === SimulatorLifecycleState.REPLACING
                && runId === current.activeRunId
            )
        ) {
            return _lifecycleResult(current, null, false, false);
        }
        return _lifecycleResult(
            _liveLifecycleState(current, runId),
            text,
            true,
        );
    }

    if (status === 'error' && data.error_type && pending) {
        if (data.prior_run_cancelled === true) {
            const next = _lifecycleState(
                current,
                SimulatorLifecycleState.TERMINAL_ERROR,
                {
                    activeRunId: null,
                    priorTerminal: null,
                    retiredRunId: data.prior_run_id || current.activeRunId,
                    terminalText: text,
                },
            );
            return _lifecycleResult(next, text, true);
        }
        if (phase === SimulatorLifecycleState.REPLACING) {
            if (current.priorTerminal) {
                const prior = current.priorTerminal;
                const next = _lifecycleState(current, prior.phase, {
                    activeRunId: prior.runId,
                    priorTerminal: null,
                    terminalText: prior.text,
                });
                return _lifecycleResult(next, prior.text);
            }
            return _lifecycleResult(
                _liveLifecycleState(current, current.activeRunId),
                text,
            );
        }
        const next = _lifecycleState(
            current,
            SimulatorLifecycleState.TERMINAL_ERROR,
            {
                activeRunId: null,
                priorTerminal: null,
                retiredRunId: null,
                terminalText: text,
            },
        );
        return _lifecycleResult(next, text, true);
    }

    if (status === 'refused') {
        return _terminalLifecycleResult(
            current,
            SimulatorLifecycleState.TERMINAL_REFUSED,
            text,
            runId,
        );
    }
    if (
        status === 'error'
        && (
            Object.prototype.hasOwnProperty.call(data, 'reason')
            || _payloadHasBackendField(data, 'backend_status')
        )
    ) {
        return _terminalLifecycleResult(
            current,
            SimulatorLifecycleState.TERMINAL_ERROR,
            text,
            runId,
        );
    }

    if (ACTIVE_SIMULATION_STATUSES.has(status)) {
        if (!runId) {
            return _lifecycleResult(current, null, false, false);
        }
        if (phase === SimulatorLifecycleState.REPLACING) {
            if (runId === current.activeRunId) {
                return _lifecycleResult(current, text);
            }
            return _lifecycleResult(
                _liveLifecycleState(current, runId),
                text,
                true,
            );
        }
        if (current.activeRunId && runId !== current.activeRunId) {
            return _lifecycleResult(current, null, false, false);
        }
        return _lifecycleResult(_liveLifecycleState(current, runId), text);
    }

    if (TERMINAL_LIFECYCLE_STATES.has(phase)) {
        return _lifecycleResult(current, current.terminalText, false, false);
    }
    return _lifecycleResult(current, text);
}

function transitionSimulatorLifecycle(event) {
    const result = reduceSimulatorLifecycle(_simulatorLifecycle, event);
    _simulatorLifecycle = result.state;
    if (result.resetBackend) {
        resetBackendBadgeState();
    }
    const el = document.getElementById('status-text');
    if (el && result.statusText !== null) {
        el.textContent = result.statusText;
    }
    return result;
}

function noteLiveSimulationTick(data) {
    const result = transitionSimulatorLifecycle({
        type: SimulatorLifecycleEvent.TICK,
        data,
    });
    if (data && result.acceptTelemetry) {
        updateBackendBadge(data);
    }
    return result;
}

function noteSimulationComplete(data) {
    return transitionSimulatorLifecycle({
        type: SimulatorLifecycleEvent.COMPLETE,
        data,
    });
}

function simulatorLifecycleSnapshot() {
    return {
        ..._simulatorLifecycle,
        priorTerminal: _simulatorLifecycle.priorTerminal
            ? { ..._simulatorLifecycle.priorTerminal }
            : null,
    };
}

socket.on('connect', () => {
    console.log('Connected to simulator server');
    transitionSimulatorLifecycle({ type: SimulatorLifecycleEvent.CONNECT });
    setConnectionReady(true);
});

socket.on('disconnect', (reason) => {
    console.warn(`Disconnected from simulator server: ${reason}`);
    transitionSimulatorLifecycle({ type: SimulatorLifecycleEvent.DISCONNECT });
    setConnectionReady(false);
});

socket.on('connect_error', (error) => {
    console.error('Simulator connection error', error);
    transitionSimulatorLifecycle({ type: SimulatorLifecycleEvent.CONNECT_ERROR });
    setConnectionReady(false);
});

socket.on('simulation_status', (data) => {
    const detail = data.message || data.backend_message || '';
    const suffix = detail ? ` — ${detail}` : '';
    const renderedText = `${data.status}${suffix}`;
    const lifecycle = transitionSimulatorLifecycle({
        type: SimulatorLifecycleEvent.STATUS,
        data,
        statusText: renderedText,
    });
    if (lifecycle.acceptTelemetry) {
        const badgeUpdater = (
            typeof globalThis !== 'undefined'
            && globalThis.updateBackendBadge
        ) || updateBackendBadge;
        badgeUpdater(data);
    }
    if (data.message) console.log(data.message);
    if (data.backend_message) console.log(data.backend_message);
    // ANY TERMINAL OUTCOME MUST HAND THE CONTROLS BACK, NOT JUST A CRASH.
    // This tested `data.status === 'error'` only, so a run that ERRORED
    // re-enabled Start while a run that lawfully REFUSED left it disabled
    // forever -- the operator could not retry without reloading the page.
    // That is backwards: a refusal is the model declining to extrapolate (a
    // RESULT), an error is a fault, and the fault case recovered while the
    // lawful one latched. It is also how a correct fail-close came to be
    // reported as "the run stalled": terminal-refused, Start greyed out,
    // ledger showing n/a, nothing to do but reload.
    //
    // Keyed on the LIFECYCLE reaching a terminal phase rather than on a list
    // of status strings, so a terminal state added later cannot silently
    // re-introduce the latch. simulation_complete keeps its own handler in
    // simulator-decisions.js; re-enabling is idempotent, so the overlap is
    // harmless and the two paths do not need to know about each other.
    if (
        lifecycle.acceptTelemetry
        && TERMINAL_LIFECYCLE_STATES.has(lifecycle.state.phase)
    ) {
        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-pause').disabled = true;
        document.getElementById('btn-resume').disabled = true;
    }
});

if (typeof globalThis !== 'undefined') {
    globalThis.updateBackendBadge = updateBackendBadge;
    globalThis.noteLiveSimulationTick = noteLiveSimulationTick;
    globalThis.noteSimulationComplete = noteSimulationComplete;
    globalThis.transitionSimulatorLifecycle = transitionSimulatorLifecycle;
    globalThis.reduceSimulatorLifecycle = reduceSimulatorLifecycle;
    globalThis.simulatorLifecycleSnapshot = simulatorLifecycleSnapshot;
    globalThis.SimulatorLifecycleState = SimulatorLifecycleState;
    globalThis.SimulatorLifecycleEvent = SimulatorLifecycleEvent;
    globalThis.resetBackendBadgeState = resetBackendBadgeState;
}
