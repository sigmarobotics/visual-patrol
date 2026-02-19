// points.js — Patrol points CRUD, render tables, import/export, reorder, highlight
import state, { escapeHtml } from './state.js';
import { draw } from './map.js';
import { testAI } from './ai.js';

export function initPoints() {
    const btnAddPointQuick = document.getElementById('btn-add-point-quick');
    if (btnAddPointQuick) btnAddPointQuick.addEventListener('click', addCurrentPoint);

    const btnSavePoints = document.getElementById('btn-save-points');
    if (btnSavePoints) {
        btnSavePoints.addEventListener('click', async () => {
            const originalText = btnSavePoints.innerText;
            btnSavePoints.innerText = 'Saving...';
            try {
                await saveAllPoints();
                btnSavePoints.innerText = 'Saved!';
            } catch (e) {
                btnSavePoints.innerText = 'Error';
                alert('Save failed: ' + e);
            }
            setTimeout(() => {
                btnSavePoints.innerText = originalText;
            }, 1500);
        });
    }

    const btnExportPoints = document.getElementById('btn-export-points');
    if (btnExportPoints) {
        btnExportPoints.addEventListener('click', () => {
            window.location.href = `/api/${state.selectedRobotId}/points/export`;
        });
    }

    const btnImportPoints = document.getElementById('btn-import-points');
    const importFileInput = document.getElementById('import-file-input');
    if (btnImportPoints && importFileInput) {
        btnImportPoints.addEventListener('click', () => {
            importFileInput.click();
        });

        importFileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch(`/api/${state.selectedRobotId}/points/import`, {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.status === 'imported') {
                    alert(`Successfully imported ${data.count} points.`);
                    loadPoints();
                } else {
                    alert('Import failed: ' + (data.error || 'Unknown error'));
                }
            } catch (err) {
                alert('Import error: ' + err);
            }
            importFileInput.value = '';
        });
    }

    const btnGetRobotLocations = document.getElementById('btn-get-robot-locations');
    if (btnGetRobotLocations) {
        btnGetRobotLocations.addEventListener('click', getLocationsFromRobot);
    }

    // Expose to window for inline onclick handlers
    window.updatePoint = updatePoint;
    window.deletePoint = deletePoint;
    window.movePoint = movePoint;
    window.setHighlight = setHighlight;
    window.clearHighlight = clearHighlight;
    window.testPoint = testPoint;
    window.enableAllPoints = enableAllPoints;
    window.disableAllPoints = disableAllPoints;
    window.optimizeRoute = optimizeRoute;
    window.saveCurrentRoute = saveCurrentRoute;
    window.loadSelectedRoute = loadSelectedRoute;
    window.deleteSelectedRoute = deleteSelectedRoute;
}

export async function loadPoints() {
    if (!state.selectedRobotId) return;
    const res = await fetch(`/api/${state.selectedRobotId}/points`);
    state.currentPatrolPoints = await res.json();
    renderPointsTable();
    loadRouteList();
}

function renderPointsTable() {
    const pointsTableBody = document.querySelector('#points-table tbody');
    const pointsTableQuickBody = document.querySelector('#points-table-quick tbody');

    // Detailed Table
    if (pointsTableBody) {
        pointsTableBody.innerHTML = '';
        state.currentPatrolPoints.forEach(p => {
            const tr = document.createElement('tr');
            const eid = escapeHtml(p.id);
            tr.innerHTML = `
                <td><input type="text" value="${escapeHtml(p.name || '')}" onchange="updatePoint('${eid}', 'name', this.value)" style="width:100px; background:rgba(0,0,0,0.03); border:1px solid #ccc; color:#333;"></td>
                <td style="font-family:monospace; font-size:0.8rem; color:#333;">X:${p.x.toFixed(2)} Y:${p.y.toFixed(2)} T:${p.theta.toFixed(2)}</td>
                <td><input type="text" value="${escapeHtml(p.prompt || '')}" onchange="updatePoint('${eid}', 'prompt', this.value)" style="width:200px; background:rgba(0,0,0,0.03); border:1px solid #ccc; color:#333;"></td>
                <td><input type="checkbox" ${p.enabled !== false ? 'checked' : ''} onchange="updatePoint('${eid}', 'enabled', this.checked)"></td>
                <td><button onclick="deletePoint('${eid}')" style="color:#dc3545; background:none; border:none; cursor:pointer;">del</button></td>
            `;
            pointsTableBody.appendChild(tr);
        });
    }

    // Quick Table (Control View)
    if (pointsTableQuickBody) {
        pointsTableQuickBody.innerHTML = '';
        state.currentPatrolPoints.forEach(p => {
            const tr = document.createElement('tr');
            const eqid = escapeHtml(p.id);
            tr.innerHTML = `
                <td>
                    <input type="text" value="${escapeHtml(p.name || '')}" onchange="updatePoint('${eqid}', 'name', this.value)"
                        style="width:100%; min-width:80px; background:rgba(0,0,0,0.03); border:1px solid #ccc; border-radius:4px; color:#333; padding:4px;">
                    <br>
                    <span style="font-size:0.7rem; color:#555;">X:${p.x.toFixed(1)} Y:${p.y.toFixed(1)}</span>
                </td>
                <td>
                    <textarea onchange="updatePoint('${eqid}', 'prompt', this.value)"
                        style="width:100%; height:50px; background:rgba(0,0,0,0.03); border:1px solid #ccc; border-radius:4px; color:#333; padding:4px; resize:vertical;"
                        placeholder="Prompt...">${escapeHtml(p.prompt || '')}</textarea>
                </td>
                <td>
                    <button onclick="testPoint('${eqid}')" class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;">Test</button>
                </td>
                <td>
                    <button onclick="deletePoint('${eqid}')" style="color:#dc3545; background:none; border:none; cursor:pointer;">🗑</button>
                </td>
            `;
            pointsTableQuickBody.appendChild(tr);
        });
    }

    // Patrol View Simplified Table
    const patrolViewBody = document.querySelector('#patrol-view-points-table tbody');
    if (patrolViewBody) {
        patrolViewBody.innerHTML = '';
        state.currentPatrolPoints.forEach((p, index) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="display:flex; align-items:center; gap:5px;">
                     <div style="display:flex; flex-direction:column; gap:2px;">
                         <button onclick="movePoint(${index}, -1)" class="btn-sm" style="font-size:0.7rem; padding:0 4px; line-height:1;" ${index === 0 ? 'disabled' : ''}>▲</button>
                         <button onclick="movePoint(${index}, 1)" class="btn-sm" style="font-size:0.7rem; padding:0 4px; line-height:1;" ${index === state.currentPatrolPoints.length - 1 ? 'disabled' : ''}>▼</button>
                     </div>
                    <button onmousedown="setHighlight('${escapeHtml(p.id)}')" onmouseup="clearHighlight()" onmouseleave="clearHighlight()"
                        class="btn-secondary" style="width:100%; text-align:left; font-size:0.85rem; margin:0;">
                        📍 ${escapeHtml(p.name || 'Unnamed Point')}
                    </button>
                </td>
                <td style="text-align:center;">
                    <input type="checkbox" ${p.enabled !== false ? 'checked' : ''} onchange="updatePoint('${escapeHtml(p.id)}', 'enabled', this.checked)">
                </td>
            `;
            patrolViewBody.appendChild(tr);
        });
    }
}

async function addCurrentPoint() {
    const name = `Point ${state.currentPatrolPoints.length + 1}`;
    const point = {
        name,
        x: state.robotPose.x,
        y: state.robotPose.y,
        theta: state.robotPose.theta,
        prompt: 'Is this normal?',
        enabled: true
    };

    await fetch(`/api/${state.selectedRobotId}/points`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(point)
    });
    loadPoints();
}

async function saveAllPoints() {
    await fetch(`/api/${state.selectedRobotId}/points/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.currentPatrolPoints)
    });
}

async function updatePoint(id, key, value) {
    const point = state.currentPatrolPoints.find(p => p.id === id);
    if (!point) return;
    point[key] = value;
    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(point)
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            alert('Failed to save point: ' + (data.error || 'Unknown error'));
            loadPoints();
        }
    } catch (e) {
        alert('Failed to save point: ' + e.message);
        loadPoints();
    }
}

async function deletePoint(id) {
    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points?id=${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok || data.error) {
            alert('Failed to delete point: ' + (data.error || 'Unknown error'));
            return;
        }
    } catch (e) {
        alert('Failed to delete point: ' + e.message);
        return;
    }
    loadPoints();
}

async function movePoint(index, direction) {
    if (direction === -1 && index > 0) {
        [state.currentPatrolPoints[index], state.currentPatrolPoints[index - 1]] = [state.currentPatrolPoints[index - 1], state.currentPatrolPoints[index]];
    } else if (direction === 1 && index < state.currentPatrolPoints.length - 1) {
        [state.currentPatrolPoints[index], state.currentPatrolPoints[index + 1]] = [state.currentPatrolPoints[index + 1], state.currentPatrolPoints[index]];
    } else {
        return;
    }

    renderPointsTable();
    await saveAllPoints();
}

async function enableAllPoints() {
    state.currentPatrolPoints.forEach(p => { p.enabled = true; });
    renderPointsTable();
    await saveAllPoints();
}

async function disableAllPoints() {
    state.currentPatrolPoints.forEach(p => { p.enabled = false; });
    renderPointsTable();
    await saveAllPoints();
}

async function optimizeRoute(direction) {
    const sortFns = {
        'bottom-to-top': (a, b) => a.y - b.y,
        'top-to-bottom': (a, b) => b.y - a.y,
        'left-to-right': (a, b) => a.x - b.x,
        'right-to-left': (a, b) => b.x - a.x,
    };
    const fn = sortFns[direction];
    if (!fn) return;

    state.currentPatrolPoints.sort(fn);
    renderPointsTable();
    await saveAllPoints();
}

async function loadRouteList() {
    if (!state.selectedRobotId) return;
    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points/routes`);
        const routes = await res.json();
        const select = document.getElementById('route-select');
        if (!select) return;

        const currentVal = select.value;
        select.innerHTML = '<option value="">-- Saved Routes --</option>';
        routes.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        });
        if (currentVal && routes.includes(currentVal)) {
            select.value = currentVal;
        }
    } catch (e) {
        console.error('Failed to load route list:', e);
    }
}

async function saveCurrentRoute() {
    const name = prompt('Enter route name (alphanumeric, hyphen, underscore):');
    if (!name || !name.trim()) return;
    const cleanName = name.trim();

    if (!/^[\w\-]+$/.test(cleanName)) {
        alert('Invalid name. Use alphanumeric, underscore, or hyphen only.');
        return;
    }

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points/routes/${cleanName}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(state.currentPatrolPoints)
        });
        const data = await res.json();
        if (res.ok) {
            await loadRouteList();
            const select = document.getElementById('route-select');
            if (select) select.value = cleanName;
        } else {
            alert('Save failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Save error: ' + e.message);
    }
}

async function loadSelectedRoute() {
    const select = document.getElementById('route-select');
    if (!select || !select.value) {
        alert('Please select a route to load.');
        return;
    }

    if (!confirm(`Load route "${select.value}"? This will replace the current patrol points.`)) return;

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points/routes/${select.value}`);
        if (!res.ok) {
            alert('Failed to load route.');
            return;
        }
        const points = await res.json();

        await fetch(`/api/${state.selectedRobotId}/points/reorder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(points)
        });

        await loadPoints();
    } catch (e) {
        alert('Load error: ' + e.message);
    }
}

async function deleteSelectedRoute() {
    const select = document.getElementById('route-select');
    if (!select || !select.value) {
        alert('Please select a route to delete.');
        return;
    }

    if (!confirm(`Delete route "${select.value}"?`)) return;

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points/routes/${select.value}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            await loadRouteList();
        } else {
            alert('Delete failed.');
        }
    } catch (e) {
        alert('Delete error: ' + e.message);
    }
}

function setHighlight(id) {
    const point = state.currentPatrolPoints.find(p => p.id === id);
    if (point) {
        state.highlightedPoint = point;
        draw();
    }
}

function clearHighlight() {
    if (state.highlightedPoint) {
        state.highlightedPoint = null;
        draw();
    }
}

async function testPoint(id) {
    const point = state.currentPatrolPoints.find(p => p.id === id);
    if (!point) return;

    const promptInput = document.getElementById('ai-test-prompt-input');
    if (promptInput) promptInput.value = point.prompt || '';

    const outputResult = document.getElementById('ai-output-result');
    if (outputResult) {
        outputResult.textContent = `Moving to point "${point.name}"...`;
        outputResult.style.color = "#006b56";
    }

    try {
        const moveRes = await fetch(`/api/${state.selectedRobotId}/move`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x: point.x, y: point.y, theta: point.theta })
        });

        if (!moveRes.ok) {
            throw new Error("Failed to move to point");
        }
    } catch (e) {
        if (outputResult) {
            outputResult.textContent = "Move Error: " + e.message;
            outputResult.style.color = "#dc3545";
        }
        return;
    }

    await testAI(point.prompt);
}

async function getLocationsFromRobot() {
    const btn = document.getElementById('btn-get-robot-locations');
    const originalText = btn.innerHTML;

    btn.disabled = true;
    btn.innerHTML = '<span style="font-size: 12px;">⏳</span> Loading...';

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/points/from_robot`);
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Failed to fetch locations');
        }

        let message = '';
        if (data.added && data.added.length > 0) {
            message += `Added ${data.added.length} location(s):\n• ${data.added.join('\n• ')}\n\n`;
        }
        if (data.skipped && data.skipped.length > 0) {
            message += `Skipped ${data.skipped.length} duplicate(s):\n• ${data.skipped.join('\n• ')}`;
        }
        if (data.added.length === 0 && data.skipped.length === 0) {
            message = 'No locations found on robot.';
        }

        alert(message || 'Operation completed.');

        if (data.added && data.added.length > 0) {
            loadPoints();
        }
    } catch (e) {
        alert('Error: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}
