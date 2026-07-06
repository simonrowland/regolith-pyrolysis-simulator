/**
 * Lunar Operator Game — UI Logic
 */

const socket = io();

socket.on('connect', () => {
    console.log('Game connected to server');
});

// Game state
let selectedLine = null;

// --- Furnace card click → expand detail ---
document.querySelectorAll('.furnace-card').forEach(card => {
    card.addEventListener('click', (e) => {
        // Don't expand if clicking on select or button
        if (e.target.tagName === 'SELECT' || e.target.tagName === 'BUTTON') return;

        const lineId = card.dataset.line;
        selectedLine = lineId;

        const detail = document.getElementById('line-detail');
        detail.classList.remove('hidden');
        document.getElementById('detail-title').textContent = `Line ${lineId}`;

        // TODO Phase 4: Load line charts via SocketIO
    });
});

// Close detail panel
document.getElementById('detail-close').addEventListener('click', () => {
    document.getElementById('line-detail').classList.add('hidden');
    selectedLine = null;
});

// --- Load button → assign feedstock to line ---
document.querySelectorAll('.btn-load').forEach(btn => {
    btn.addEventListener('click', () => {
        const lineId = btn.dataset.line;
        const card = document.getElementById(`line-${lineId}`);
        const select = card.querySelector('.line-feedstock-select');
        const feedstock = select.value;

        if (!feedstock) {
            alert('Select a feedstock first.');
            return;
        }

        socket.emit('game_add_line', {
            line_id: lineId,
            feedstock: feedstock,
            mass_kg: 1000, // default
        });

        // Update card UI
        card.querySelector('.line-feedstock').textContent = select.options[select.selectedIndex].text;
        card.querySelector('.line-status').className = 'line-status running';
        card.querySelector('.line-status').textContent = 'Running';
    });
});

// --- Game controls ---
document.getElementById('game-start').addEventListener('click', () => {
    socket.emit('game_start', { num_lines: 15 });
    document.getElementById('game-step').disabled = false;
    document.getElementById('game-auto').disabled = false;
});

document.getElementById('game-step').addEventListener('click', () => {
    socket.emit('game_step');
});

// Auto-run toggle
let autoRunning = false;
let autoInterval = null;

document.getElementById('game-auto').addEventListener('click', () => {
    autoRunning = !autoRunning;
    const btn = document.getElementById('game-auto');

    if (autoRunning) {
        btn.textContent = 'Stop';
        btn.classList.add('btn-primary');
        autoInterval = setInterval(() => {
            socket.emit('game_step');
        }, 1000);
    } else {
        btn.textContent = 'Auto-Run';
        btn.classList.remove('btn-primary');
        clearInterval(autoInterval);
    }
});

// --- Game tick handler ---
socket.on('game_tick', (data) => {
    document.getElementById('game-clock').textContent = `Hour: ${data.game_hour}`;

    // Update each line's card
    for (const [lineId, snapshot] of Object.entries(data.lines || {})) {
        const card = document.getElementById(`line-${lineId}`);
        if (!card) continue;

        // Temperature bar (0-1800 C range)
        const tempPct = Math.min(100, (snapshot.temperature_C / 1800) * 100);
        card.querySelector('.temp-fill').style.width = tempPct + '%';

        // Campaign label
        card.querySelector('.line-campaign').textContent = snapshot.campaign;
    }

    // Update inventory
    if (data.inventory) {
        const setInv = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = (val || 0).toFixed(0);
        };
        setInv('inv-na', data.inventory.Na);
        setInv('inv-k', data.inventory.K);
        setInv('inv-mg', data.inventory.Mg);
        setInv('inv-o2', data.inventory.O2);
        setInv('inv-kwh', data.inventory.energy_kWh);
    }
});

// Decision alert — uses safe DOM methods
socket.on('game_decision', (data) => {
    const queue = document.getElementById('decision-queue');
    const alertDiv = document.createElement('div');
    alertDiv.className = 'decision-alert';

    const label = document.createElement('strong');
    label.textContent = `Line ${data.line_id}`;
    alertDiv.appendChild(label);

    const msg = document.createTextNode(`: ${data.type} `);
    alertDiv.appendChild(msg);

    const dismissBtn = document.createElement('button');
    dismissBtn.className = 'btn btn-sm';
    dismissBtn.textContent = 'Dismiss';
    dismissBtn.addEventListener('click', () => alertDiv.remove());
    alertDiv.appendChild(dismissBtn);

    queue.appendChild(alertDiv);

    // Update card status
    const card = document.getElementById(`line-${data.line_id}`);
    if (card) {
        card.querySelector('.line-status').className = 'line-status decision';
        card.querySelector('.line-status').textContent = 'Decision';
    }
});
