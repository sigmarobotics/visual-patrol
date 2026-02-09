// patrol.js — Start/stop patrol, status polling, results display, camera stream
import state, { escapeHtml } from './state.js';
import { renderAIResultHTML } from './ai.js';

let btnStartPatrol, btnStopPatrol;
let isStreamActive = true;

export function initPatrol() {
    btnStartPatrol = document.getElementById('btn-start-patrol');
    btnStopPatrol = document.getElementById('btn-stop-patrol');

    if (btnStartPatrol) btnStartPatrol.addEventListener('click', startPatrol);
    if (btnStopPatrol) btnStopPatrol.addEventListener('click', stopPatrol);

    loadResults();
    startPatrolPolling();
}

async function startPatrol() {
    const resultsContainer = document.getElementById('results-container');
    if (resultsContainer) resultsContainer.innerHTML = '';

    const res = await fetch(`/api/${state.selectedRobotId}/patrol/start`, { method: 'POST' });
    if (!res.ok) {
        const err = await res.json();
        alert(err.error);
    }
}

async function stopPatrol() {
    await fetch(`/api/${state.selectedRobotId}/patrol/stop`, { method: 'POST' });
}

function startPatrolPolling() {
    if (state._intervals.patrolPolling) return; // Prevent duplicate intervals

    state._intervals.patrolPolling = setInterval(async () => {
        if (!state.selectedRobotId) return;
        const res = await fetch(`/api/${state.selectedRobotId}/patrol/status`);
        const data = await res.json();

        if (data.is_patrolling) {
            if (btnStartPatrol) btnStartPatrol.disabled = true;
            if (btnStopPatrol) btnStopPatrol.disabled = false;
        } else {
            if (btnStartPatrol) btnStartPatrol.disabled = false;
            if (btnStopPatrol) btnStopPatrol.disabled = true;
        }

        const shouldStream = data.is_patrolling || state.currentIdleStreamEnabled;
        updateCameraStream(shouldStream);

        loadResults();
        loadEdgeAIAlerts(data.is_patrolling);
    }, 1000);
}

let lastStreamRobotId = null;

function updateCameraStream(shouldStream) {
    const robotChanged = lastStreamRobotId !== state.selectedRobotId;
    if (shouldStream === isStreamActive && !robotChanged) return;
    isStreamActive = shouldStream;
    lastStreamRobotId = state.selectedRobotId;

    const cams = [
        document.getElementById('front-camera-img'),
        document.getElementById('robot-vision-img')
    ];

    cams.forEach(img => {
        if (img) {
            if (shouldStream && state.selectedRobotId) {
                img.src = `/api/${state.selectedRobotId}/camera/front?t=` + new Date().getTime();
                img.style.opacity = 1;
            } else {
                img.src = '';
                img.alt = 'Stream Paused (Idle Mode)';
                img.style.opacity = 0.5;
            }
        }
    });
}

export async function loadResults() {
    const resultsContainer = document.getElementById('results-container');

    if (!state.selectedRobotId) return;
    const res = await fetch(`/api/${state.selectedRobotId}/patrol/results`);
    const results = await res.json();

    if (resultsContainer) {
        resultsContainer.innerHTML = '';
        results.slice().slice(-10).reverse().forEach(r => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.background = 'rgba(0,0,0,0.03)';
            card.style.padding = '8px';
            card.style.borderRadius = '4px';

            const resultHTML = renderAIResultHTML(r.result);

            card.innerHTML = `
                 <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                     <span style="color:#006b56; font-weight:bold;">${escapeHtml(r.point_name)}</span>
                     <span style="font-size:0.7rem; color:#555;">${escapeHtml(r.timestamp)}</span>
                 </div>
                 ${resultHTML}
             `;
            resultsContainer.appendChild(card);
        });
    }

    // Update Latest Result Dashboard Widget
    const latestBoxes = document.querySelectorAll('.patrol-latest-result-display, #patrol-latest-result');
    latestBoxes.forEach(latestBox => {
        if (results.length > 0) {
            const newest = results[results.length - 1];
            const resultHTML = renderAIResultHTML(newest.result);

            latestBox.style.display = "";
            latestBox.style.alignItems = "";
            latestBox.style.justifyContent = "";
            latestBox.style.color = "";
            latestBox.innerHTML = `
                <div style="font-weight:bold; color:#006b56; margin-bottom:4px;">
                    ${escapeHtml(newest.point_name)}
                    <span style="font-weight:normal; color:#555; font-size:0.8rem; float:right;">(${escapeHtml(newest.timestamp)})</span>
                </div>
                ${resultHTML}
            `;
        } else {
            latestBox.textContent = "No analysis data yet.";
            latestBox.style.color = "#666";
            latestBox.style.display = "flex";
            latestBox.style.alignItems = "center";
            latestBox.style.justifyContent = "center";
        }
    });
}

async function loadEdgeAIAlerts(isPatrolling) {
    const frame = document.getElementById('edge-ai-alerts-frame');
    const list = document.getElementById('edge-ai-alerts-list');
    const badge = document.getElementById('edge-ai-alerts-badge');
    if (!frame || !list) return;

    if (!isPatrolling || !state.selectedRobotId) {
        // Hide when not patrolling (but keep visible if there were alerts)
        if (badge && badge.textContent === '0') {
            frame.style.display = 'none';
        }
        return;
    }

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/patrol/edge_ai_alerts`);
        const alerts = await res.json();

        if (alerts.length === 0) {
            frame.style.display = 'none';
            return;
        }

        frame.style.display = 'block';
        if (badge) {
            badge.textContent = alerts.length;
            badge.style.display = 'inline';
        }

        list.innerHTML = alerts.map(a => `
            <div style="padding: 8px; margin-bottom: 6px; background: rgba(220,53,69,0.08); border-left: 3px solid #dc3545; border-radius: 4px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                    <span style="color: #dc3545; font-weight: bold; font-size: 12px;">${escapeHtml(a.rule)}</span>
                    <span style="font-size: 10px; color: var(--text-muted);">${escapeHtml(a.timestamp)}</span>
                </div>
                <div style="font-size: 11px; color: var(--text-secondary);">Response: ${escapeHtml(a.response)}</div>
            </div>
        `).join('');
    } catch (e) {
        // Silently ignore fetch errors during polling
    }
}

window.toggleEdgeAIAlertsPanel = function() {
    const content = document.getElementById('edge-ai-alerts-content');
    const toggle = document.getElementById('edge-ai-alerts-toggle');
    if (content && toggle) {
        const hidden = content.style.display === 'none';
        content.style.display = hidden ? 'block' : 'none';
        toggle.textContent = hidden ? '▼' : '▶';
    }
};
