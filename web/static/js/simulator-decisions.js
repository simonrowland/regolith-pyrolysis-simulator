/**
 * Decision modal and completion event handlers.
 */
// --- Decision handling ---

socket.on('decision_required', (data) => {
    console.log('Decision required:', data);
    showDecisionModal(data);
});

function showDecisionModal(data) {
    // Remove any existing modal
    const existing = document.getElementById('decision-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'decision-modal';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000;';

    const modal = document.createElement('div');
    modal.style.cssText = 'background:var(--surface);color:var(--text);border:1px solid var(--border);padding:24px;border-radius:12px;max-width:500px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.3);';

    const title = document.createElement('h3');
    title.textContent = 'Decision Required: ' + data.type;
    title.style.cssText = 'margin:0 0 12px 0;font-size:16px;color:var(--text);';
    modal.appendChild(title);

    const context = document.createElement('p');
    context.textContent = data.context;
    context.style.cssText = 'font-size:13px;color:var(--text-secondary);margin:0 0 16px 0;line-height:1.5;';
    modal.appendChild(context);

    const rec = document.createElement('p');
    rec.textContent = 'Recommended: ' + data.recommendation;
    rec.style.cssText = 'font-size:12px;color:var(--primary);font-weight:600;margin:0 0 16px 0;';
    modal.appendChild(rec);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;';

    for (const opt of data.options) {
        const btn = document.createElement('button');
        btn.textContent = opt;
        btn.className = 'btn' + (opt === data.recommendation ? ' btn-primary' : '');
        btn.style.cssText = 'padding:8px 20px;border-radius:6px;border:1px solid var(--border);cursor:pointer;font-size:14px;background:var(--surface);color:var(--text);' +
            (opt === data.recommendation ? 'background:var(--primary);color:#fff;border-color:var(--primary);' : '');
        btn.addEventListener('click', () => {
            socket.emit('make_decision', { choice: opt });
            overlay.remove();
        });
        btnRow.appendChild(btn);
    }

    modal.appendChild(btnRow);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

// --- Completion handler ---

socket.on('simulation_complete', (data) => {
    noteSimulationComplete(data);
    document.getElementById('btn-start').disabled = false;
    document.getElementById('btn-pause').disabled = true;
    console.log('Simulation complete:', data);
});
