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
    if (el) el.textContent = data.status;
    if (data.message) console.log(data.message);
});
