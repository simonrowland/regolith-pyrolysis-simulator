/**
 * Socket connection and simulator status events.
 */

/**
 * Simulator UI — Plotly charts + SocketIO real-time updates
 */

const socket = io();

socket.on('connect', () => {
    console.log('Connected to simulator server');
});

socket.on('simulation_status', (data) => {
    const el = document.getElementById('status-text');
    const backend = data.backend_message ? ` — ${data.backend_message}` : '';
    if (el) el.textContent = `${data.status}${backend}`;
    if (data.message) console.log(data.message);
    if (data.backend_message) console.log(data.backend_message);
});
