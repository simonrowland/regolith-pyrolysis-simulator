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

function resetDecisionModal() {
    const modal = document.getElementById('decision-modal');
    if (modal) modal.remove();
}

function setConnectionReady(ready) {
    const startBtn = document.getElementById('btn-start');
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    if (startBtn) startBtn.disabled = !ready;
    if (pauseBtn) pauseBtn.disabled = true;
    if (resumeBtn) resumeBtn.disabled = true;
    resetDecisionModal();
}

setConnectionReady(false);

// ---------------------------------------------------------------------------
// Backend badge — merge-only updates
//
// Root cause of "Backend: unknown / unknown" after a decision gate: the
// server emits simulation_status {status: 'decision_applied', choice} with
// NO backend_* fields. The old renderer defaulted missing fields to
// 'unknown', clobbering a badge that already knew InternalAnalyticalBackend.
// Ticks carry backend_status / authoritative / message but often omit
// backend_active — so the badge must merge, never invent "unknown".
// ---------------------------------------------------------------------------
const _backendBadgeState = {
    active: null,
    status: null,
    authoritative: null,
    message: '',
};

// True once a simulation_tick has painted live readouts this page session.
// Used so a mid-run reconnect does not claim "Ready" over stale telemetry
// (same failure class as sticky decision_applied: status strip asserting
// a state that contradicts the rest of the strip).
let _hadLiveTelemetry = false;

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
        || Object.prototype.hasOwnProperty.call(data, 'backend_message')) {
        const msg = data.backend_status_message || data.backend_message || '';
        _backendBadgeState.message = String(msg);
        touched = true;
    }

    // Payload has no backend fields at all (e.g. decision_applied) — leave
    // the badge showing whatever it already knows.
    if (!touched) {
        return;
    }

    const active = _backendBadgeState.active;
    const status = _backendBadgeState.status;

    // Honest empty state: we were told about backend fields but still have
    // nothing to show (should be rare). Never print "unknown / unknown".
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

// Status labels that must yield once live ticks prove the run is advancing.
// Includes reconnect-era labels so the strip cannot claim Ready/Disconnected
// while hour/temp keep moving (reconnect P0 is the same write-only strip).
const TRANSIENT_LIVE_STATUSES = new Set([
    'decision_applied',
    'resumed',
    'started',
    'Ready',
    'Disconnected',
    'Connection error',
    'Connection not ready',
    'Connection restored',
]);

function isTransientLiveStatus(text) {
    if (!text) return false;
    const base = String(text).split(' — ')[0].trim();
    return TRANSIENT_LIVE_STATUSES.has(base);
}

/**
 * Called from the simulation_tick path. Live telemetry is the authority for
 * "the run is advancing" — recover sticky labels and refresh the badge from
 * whatever backend fields the tick actually carries.
 */
function noteLiveSimulationTick(data) {
    _hadLiveTelemetry = true;
    if (data) {
        updateBackendBadge(data);
    }
    const el = document.getElementById('status-text');
    if (!el) return;
    // Fallback-active ticks set status-text to backend_message themselves;
    // only recover sticky labels when that path did not take over.
    if (data && data.backend_fallback_active && data.backend_message) {
        return;
    }
    if (isTransientLiveStatus(el.textContent)) {
        el.textContent = 'Running';
    }
}

function markFreshRunStarted() {
    _hadLiveTelemetry = false;
    resetBackendBadgeState();
}

socket.on('connect', () => {
    console.log('Connected to simulator server');
    const el = document.getElementById('status-text');
    if (el && (
        el.textContent === 'Disconnected'
        || el.textContent === 'Connection error'
        || el.textContent === 'Connection not ready'
    )) {
        // Mid-run reconnect leaves last-tick hour/temp on the strip. Claiming
        // "Ready" over those numbers is the reconnect P0 (stale assertion).
        // Say connection restored instead; operator Start still clears charts.
        el.textContent = _hadLiveTelemetry ? 'Connection restored' : 'Ready';
    }
    setConnectionReady(true);
});

socket.on('disconnect', (reason) => {
    console.warn(`Disconnected from simulator server: ${reason}`);
    const el = document.getElementById('status-text');
    if (el && el.textContent !== 'Complete') {
        el.textContent = 'Disconnected';
    }
    setConnectionReady(false);
});

socket.on('connect_error', (error) => {
    console.error('Simulator connection error', error);
    const el = document.getElementById('status-text');
    if (el) el.textContent = 'Connection error';
    setConnectionReady(false);
});

socket.on('simulation_status', (data) => {
    const el = document.getElementById('status-text');
    const detail = data.message || data.backend_message || '';
    const suffix = detail ? ` — ${detail}` : '';
    if (el) el.textContent = `${data.status}${suffix}`;
    // Resolve on globalThis so harness mutations (and any later rebinding)
    // take effect; a closed-over lexical call would pin the first definition.
    const badgeUpdater = (typeof globalThis !== 'undefined' && globalThis.updateBackendBadge)
        || updateBackendBadge;
    badgeUpdater(data);
    if (data.message) console.log(data.message);
    if (data.backend_message) console.log(data.backend_message);
    if (data.status === 'error') {
        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-pause').disabled = true;
        document.getElementById('btn-resume').disabled = true;
    }
});

// Publish for cross-script ticks/controls and for the DOM harness mutations.
if (typeof globalThis !== 'undefined') {
    globalThis.updateBackendBadge = updateBackendBadge;
    globalThis.noteLiveSimulationTick = noteLiveSimulationTick;
    globalThis.markFreshRunStarted = markFreshRunStarted;
    globalThis.resetBackendBadgeState = resetBackendBadgeState;
    globalThis.isTransientLiveStatus = isTransientLiveStatus;
}
