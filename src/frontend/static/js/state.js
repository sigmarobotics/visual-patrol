// state.js — Shared mutable state hub
// Every other module imports from here; this module imports nothing.

const state = {
    robotPose: { x: 0, y: 0, theta: 0 },
    mapInfo: null,
    isMapLoaded: false,
    mapImage: new Image(),
    canvasScale: 1,

    isDragging: false,
    dragStart: null,
    dragCurrent: null,

    currentPatrolPoints: [],
    highlightedPoint: null,
    showPointsOnMap: true,

    currentSettingsTimezone: 'UTC',
    currentIdleStreamEnabled: true,

    // Multi-robot
    selectedRobotId: null,
    availableRobots: [],

    // Interval tracking for cleanup
    _intervals: {
        statePolling: null,
        patrolPolling: null,
        robotFetch: null,
        clock: null,
        scheduleDisplay: null,
    },
};

export function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export default state;
