// app.js — Entry point: init, tab switching, robot selector
import state from './state.js';
import { initMap, resizeCanvas, startPolling, resetMap } from './map.js';
import { initControls } from './controls.js';
import { initAI } from './ai.js';
import { initPoints, loadPoints } from './points.js';
import { initPatrol } from './patrol.js';
import { initSchedule, loadSchedule } from './schedule.js';
import { initHistory, loadHistory } from './history.js';
import { initReports, loadReports } from './reports.js';
import { initSettings, loadSettings } from './settings.js';
import { initStats, loadStats } from './stats.js';

// --- TAB SWITCHING ---
window.switchTab = function (tabName) {
    document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

    const target = document.getElementById(`view-${tabName}`);
    if (target) target.style.display = 'block';

    const btns = document.querySelectorAll('.tab-btn');
    if (tabName === 'patrol' && btns[0]) btns[0].classList.add('active');
    if (tabName === 'control' && btns[1]) btns[1].classList.add('active');
    if (tabName === 'history' && btns[2]) btns[2].classList.add('active');
    if (tabName === 'reports' && btns[3]) btns[3].classList.add('active');
    if (tabName === 'stats' && btns[4]) btns[4].classList.add('active');
    if (tabName === 'settings' && btns[5]) btns[5].classList.add('active');

    // Load specific data
    if (tabName === 'history') loadHistory();
    if (tabName === 'reports') loadReports();
    if (tabName === 'stats') loadStats();
    if (tabName === 'settings') loadSettings();

    // Reparent Map Container
    const mapContainer = document.getElementById('map-container');
    if (tabName === 'control') {
        const dest = document.querySelector('#view-control .left-panel');
        if (dest && mapContainer.parentNode !== dest) {
            dest.prepend(mapContainer);
        }
        setTimeout(resizeCanvas, 50);
    } else if (tabName === 'patrol') {
        const dest = document.getElementById('patrol-left-panel');
        if (dest && mapContainer.parentNode !== dest) {
            dest.appendChild(mapContainer);
        }
        setTimeout(resizeCanvas, 50);
    }
};

// --- COLLAPSIBLE PANELS ---
window.toggleAnalysisHistory = function () {
    const container = document.getElementById('patrol-history-container');
    const icon = document.getElementById('history-toggle-icon');
    if (container) {
        const isCollapsed = container.style.display === 'none';
        container.style.display = isCollapsed ? 'block' : 'none';
        if (icon) icon.textContent = isCollapsed ? '▲' : '▼';
    }
};

window.toggleHistoryLog = function () {
    const frame = document.getElementById('history-log-frame');
    if (frame) frame.classList.toggle('collapsed');
};

window.toggleSchedulePanel = function () {
    const frame = document.getElementById('schedule-frame');
    if (frame) frame.classList.toggle('collapsed');
};

window.toggleAITestPanel = function () {
    const frame = document.getElementById('ai-test-frame');
    if (frame) frame.classList.toggle('collapsed');
};

window.togglePatrolPointsPanel = function () {
    const frame = document.getElementById('patrol-points-frame');
    if (frame) frame.classList.toggle('collapsed');
};

window.switchControlTab = function (tabName) {
    document.querySelectorAll('.control-tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.control-tab-btn').forEach(el => el.classList.remove('active'));

    const target = document.getElementById(`control-tab-${tabName}`);
    if (target) target.classList.add('active');

    document.querySelectorAll('.control-tab-btn').forEach(btn => {
        if (btn.textContent.trim().toLowerCase() === tabName) {
            btn.classList.add('active');
        }
    });
};

window.toggleShowPoints = function () {
    state.showPointsOnMap = !state.showPointsOnMap;
    const btn = document.getElementById('btn-toggle-show-points');
    if (btn) btn.textContent = state.showPointsOnMap ? 'Map: ON' : 'Map: OFF';
};

window.toggleFrontCameraPanel = function () {
    const frame = document.getElementById('front-camera-frame');
    if (frame) frame.classList.toggle('collapsed');
};

window.toggleRobotVisionPanel = function () {
    const frame = document.getElementById('robot-vision-frame');
    if (frame) frame.classList.toggle('collapsed');
};

// --- ROBOT SELECTOR ---
async function fetchRobots() {
    try {
        const res = await fetch('/api/robots');
        const robots = await res.json();
        state.availableRobots = robots;

        const select = document.getElementById('robot-select');
        if (!select) return;

        const currentVal = select.value;

        select.innerHTML = '';
        if (robots.length === 0) {
            select.innerHTML = '<option value="">No robots</option>';
            return;
        }

        robots.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.robot_id;
            opt.textContent = `${r.robot_name} ${r.status === 'online' ? '' : '(offline)'}`;
            select.appendChild(opt);
        });

        // Restore selection or auto-select first
        if (currentVal && robots.some(r => r.robot_id === currentVal)) {
            select.value = currentVal;
        } else {
            select.value = robots[0].robot_id;
        }

        // If selectedRobotId changed (or first load), trigger change
        if (state.selectedRobotId !== select.value) {
            state.selectedRobotId = select.value;
            onRobotChanged();
        }

        // Update connection dot
        updateConnectionDot();
    } catch (e) {
        console.error('Failed to fetch robots:', e);
    }
}

function updateConnectionDot() {
    const dot = document.getElementById('robot-connection-dot');
    if (!dot || !state.selectedRobotId) return;

    const robot = state.availableRobots.find(r => r.robot_id === state.selectedRobotId);
    if (robot && robot.status === 'online') {
        dot.className = 'connection-dot connected';
    } else {
        dot.className = 'connection-dot disconnected';
    }
}

function onRobotChanged() {
    // Reset map for new robot
    resetMap();

    // Reload robot-specific data
    loadPoints();
    loadSchedule();

    // Update camera streams
    refreshCameraStreams();
}

function refreshCameraStreams() {
    const robotId = state.selectedRobotId;
    if (!robotId) return;

    const frontCam = document.getElementById('front-camera-img');
    const visionCam = document.getElementById('robot-vision-img');

    const url = `/api/${robotId}/camera/front?t=${Date.now()}`;
    if (frontCam) frontCam.src = url;
    if (visionCam) visionCam.src = url;
}

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    initControls();
    initAI();
    initPoints();
    initPatrol();
    initSchedule();
    initHistory();
    initReports();
    initSettings();
    initStats();

    // Fetch robots first (sets selectedRobotId, triggers onRobotChanged -> loadPoints etc.)
    await fetchRobots();

    // Start polling after robot is selected
    startPolling();

    // Refresh robot list every 5s
    state._intervals.robotFetch = setInterval(fetchRobots, 5000);

    // Robot selector change event
    const select = document.getElementById('robot-select');
    if (select) {
        select.addEventListener('change', () => {
            state.selectedRobotId = select.value;
            onRobotChanged();
            updateConnectionDot();
        });
    }

    // Default tab
    window.switchTab('control');
});
