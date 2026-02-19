// map.js — Canvas rendering, coordinate transforms, mouse interactions
import state from './state.js';

const ROBOT_COLOR = '#00bcd4';
const GHOST_ROBOT_COLOR = 'rgba(0, 188, 212, 0.5)';

let canvas, ctx, loadingOverlay;

export function initMap() {
    canvas = document.getElementById('map-canvas');
    ctx = canvas.getContext('2d');
    loadingOverlay = document.getElementById('map-loading');

    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);

    window.addEventListener('resize', () => resizeCanvas());

    loadMap();
}

export function resetMap() {
    state.mapImage.src = '';
    state.mapInfo = null;
    state.isMapLoaded = false;
    if (loadingOverlay) loadingOverlay.style.display = 'flex';
    loadMap();
}

function loadMap() {
    if (!state.selectedRobotId) {
        setTimeout(loadMap, 500);
        return;
    }
    const url = `/api/${state.selectedRobotId}/map?t=` + new Date().getTime();
    state.mapImage.src = url;
    state.mapImage.style.display = 'none';
    window.debugMapImage = state.mapImage;

    state.mapImage.onload = () => {
        state.isMapLoaded = true;
        if (loadingOverlay) loadingOverlay.style.display = 'none';
        resizeCanvas();
    };

    state.mapImage.onerror = () => {
        setTimeout(loadMap, 2000);
    };
}

export function resizeCanvas() {
    if (!state.isMapLoaded) return;

    canvas.width = state.mapImage.width;
    canvas.height = state.mapImage.height;

    const container = document.getElementById('map-container');
    const containerWidth = container.clientWidth;
    const containerHeight = container.clientHeight;
    const imageRatio = state.mapImage.width / state.mapImage.height;
    const containerRatio = containerWidth / containerHeight;

    let finalWidth, finalHeight;
    if (containerRatio > imageRatio) {
        finalHeight = containerHeight;
        finalWidth = finalHeight * imageRatio;
    } else {
        finalWidth = containerWidth;
        finalHeight = finalWidth / imageRatio;
    }

    canvas.style.width = `${finalWidth}px`;
    canvas.style.height = `${finalHeight}px`;
    state.canvasScale = finalWidth / canvas.width;

    draw();
}

export function draw() {
    if (!state.isMapLoaded) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(state.mapImage, 0, 0);

    // Draw Patrol Points
    if (state.showPointsOnMap && state.mapInfo && state.currentPatrolPoints) {
        state.currentPatrolPoints.forEach(p => {
            const u = worldToPixelX(p.x);
            const v = worldToPixelY(p.y);

            if (state.highlightedPoint && state.highlightedPoint.id === p.id) {
                ctx.beginPath();
                ctx.arc(u, v, 10, 0, 2 * Math.PI);
                ctx.fillStyle = 'rgba(255, 235, 59, 0.7)';
                ctx.fill();
                ctx.strokeStyle = 'white';
                ctx.lineWidth = 2;
                ctx.stroke();
            }
        });
    }

    if (state.mapInfo) {
        drawRobot(state.robotPose, ROBOT_COLOR);
        if (state.isDragging && state.dragStart && state.dragCurrent) {
            const ghostPose = {
                x: pixelToWorldX(state.dragStart.u),
                y: pixelToWorldY(state.dragStart.v),
                theta: Math.atan2(-(state.dragCurrent.v - state.dragStart.v), (state.dragCurrent.u - state.dragStart.u))
            };
            drawRobotFromPixels(state.dragStart.u, state.dragStart.v, ghostPose.theta, GHOST_ROBOT_COLOR);

            ctx.beginPath();
            ctx.moveTo(state.dragStart.u, state.dragStart.v);
            ctx.lineTo(state.dragCurrent.u, state.dragCurrent.v);
            ctx.strokeStyle = '#26c6da';
            ctx.setLineDash([5, 5]);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }
}

function drawRobot(pose, color) {
    const u = worldToPixelX(pose.x);
    const v = worldToPixelY(pose.y);
    drawRobotFromPixels(u, v, pose.theta, color);
}

function drawRobotFromPixels(u, v, theta, color) {
    ctx.save();
    ctx.translate(u, v);
    ctx.rotate(-theta);

    const visualSize = 24;
    const size = state.canvasScale > 0 ? visualSize / state.canvasScale : visualSize;
    const lineWidth = state.canvasScale > 0 ? 2 / state.canvasScale : 2;

    ctx.beginPath();
    ctx.moveTo(size, 0);
    ctx.lineTo(-size * 0.5, size * 0.6);
    ctx.lineTo(-size * 0.2, 0);
    ctx.lineTo(-size * 0.5, -size * 0.6);
    ctx.closePath();

    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = 'white';
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    ctx.restore();
}

// Coordinate transforms
function getMapHeight() {
    if (state.isMapLoaded && state.mapImage.height > 0) return state.mapImage.height;
    if (state.mapInfo && state.mapInfo.height > 0) return state.mapInfo.height;
    return 0;
}

export function worldToPixelX(x) {
    if (!state.mapInfo || state.mapInfo.resolution <= 0) return 0;
    return (x - state.mapInfo.origin_x) / state.mapInfo.resolution;
}

export function worldToPixelY(y) {
    if (!state.mapInfo || state.mapInfo.resolution <= 0) return 0;
    const imgHeight = getMapHeight();
    if (imgHeight <= 0) return 0;
    return imgHeight - (y - state.mapInfo.origin_y) / state.mapInfo.resolution;
}

export function pixelToWorldX(u) {
    if (!state.mapInfo || state.mapInfo.resolution <= 0) return 0;
    return u * state.mapInfo.resolution + state.mapInfo.origin_x;
}

export function pixelToWorldY(v) {
    if (!state.mapInfo || state.mapInfo.resolution <= 0) return 0;
    const imgHeight = getMapHeight();
    if (imgHeight <= 0) return 0;
    return (imgHeight - v) * state.mapInfo.resolution + state.mapInfo.origin_y;
}

// Mouse handlers
function handleMouseDown(e) {
    if (!state.isMapLoaded || !state.mapInfo) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const u = (e.clientX - rect.left) * scaleX;
    const v = (e.clientY - rect.top) * scaleY;
    state.isDragging = true;
    state.dragStart = { u, v };
    state.dragCurrent = { u, v };
}

function handleMouseMove(e) {
    if (!state.isDragging) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const u = (e.clientX - rect.left) * scaleX;
    const v = (e.clientY - rect.top) * scaleY;
    state.dragCurrent = { u, v };
    draw();
}

function handleMouseUp(e) {
    if (!state.isDragging) return;
    state.isDragging = false;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const u = (e.clientX - rect.left) * scaleX;
    const v = (e.clientY - rect.top) * scaleY;

    const targetX = pixelToWorldX(state.dragStart.u);
    const targetY = pixelToWorldY(state.dragStart.v);
    let theta = 0;
    const dx = u - state.dragStart.u;
    const dy = v - state.dragStart.v;
    const dist = Math.sqrt(dx * dx + dy * dy);

    if (dist > 10) {
        theta = Math.atan2(-dy, dx);
    } else {
        theta = state.robotPose.theta;
    }

    // Inline moveRobot call to avoid circular dep with controls.js
    fetch(`/api/${state.selectedRobotId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x: targetX, y: targetY, theta })
    }).catch(e => console.error("Move Failed", e));

    state.dragStart = null;
    state.dragCurrent = null;
    draw();
}

// Polling for robot state
export function startPolling() {
    if (state._intervals.statePolling) return; // Prevent duplicate intervals

    const batteryValue = document.getElementById('battery-value');
    const poseDisplay = document.getElementById('pose-display');
    const connectionStatus = document.getElementById('connection-status');

    state._intervals.statePolling = setInterval(async () => {
        if (!state.selectedRobotId) return;
        try {
            const response = await fetch(`/api/${state.selectedRobotId}/state`);
            if (response.ok) {
                const data = await response.json();
                if (data.battery !== undefined) batteryValue.textContent = Math.floor(data.battery) + '%';
                if (data.pose) {
                    state.robotPose = data.pose;
                    poseDisplay.textContent = `X: ${state.robotPose.x.toFixed(2)} Y: ${state.robotPose.y.toFixed(2)} T: ${state.robotPose.theta.toFixed(2)}`;
                }
                if (data.map_info) {
                    state.mapInfo = data.map_info;
                }
                draw();
                if (connectionStatus) connectionStatus.classList.add('connected');
            } else {
                if (connectionStatus) connectionStatus.classList.remove('connected');
            }
        } catch (e) {
            if (connectionStatus) connectionStatus.classList.remove('connected');
        }
    }, 100);
}
