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

socket.on('connect', () => {
    console.log('Connected to simulator server');
    const el = document.getElementById('status-text');
    if (el && (
        el.textContent === 'Disconnected'
        || el.textContent === 'Connection error'
        || el.textContent === 'Connection not ready'
    )) {
        el.textContent = 'Ready';
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
    if (data.message) console.log(data.message);
    if (data.backend_message) console.log(data.backend_message);
    if (data.status === 'error') {
        document.getElementById('btn-start').disabled = false;
        document.getElementById('btn-pause').disabled = true;
        document.getElementById('btn-resume').disabled = true;
    }
});
