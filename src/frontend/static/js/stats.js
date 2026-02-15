// stats.js — Token usage chart with robot filter

let tokenChart = null;
let statsRobotsCache = null;

async function loadRobotsForStats() {
    if (statsRobotsCache) return statsRobotsCache;
    try {
        const res = await fetch('/api/robots');
        statsRobotsCache = await res.json();
        return statsRobotsCache;
    } catch (e) {
        return [];
    }
}

async function populateStatsRobotFilter() {
    const select = document.getElementById('stats-robot-filter');
    if (!select) return;

    const robots = await loadRobotsForStats();
    const currentVal = select.value;

    while (select.options.length > 1) {
        select.remove(1);
    }

    robots.forEach(robot => {
        const opt = document.createElement('option');
        opt.value = robot.robot_id;
        opt.textContent = robot.robot_name;
        select.appendChild(opt);
    });

    if (currentVal) select.value = currentVal;
}

export function initStats() {
    const startInput = document.getElementById('stats-start-date');
    const endInput = document.getElementById('stats-end-date');

    if (startInput) {
        startInput.addEventListener('change', loadStats);
    }
    if (endInput) {
        endInput.addEventListener('change', loadStats);
    }

    const robotFilter = document.getElementById('stats-robot-filter');
    if (robotFilter) {
        robotFilter.addEventListener('change', loadStats);
    }
}

export async function loadStats() {
    const startInput = document.getElementById('stats-start-date');
    const endInput = document.getElementById('stats-end-date');

    await populateStatsRobotFilter();

    if (!startInput.value) {
        const end = new Date();
        const start = new Date();
        start.setMonth(start.getMonth() - 1);

        const formatDate = (date) => {
            const y = date.getFullYear();
            const m = String(date.getMonth() + 1).padStart(2, '0');
            const d = String(date.getDate()).padStart(2, '0');
            return `${y}-${m}-${d}`;
        };

        endInput.value = formatDate(end);
        startInput.value = formatDate(start);
    }

    try {
        const robotFilter = document.getElementById('stats-robot-filter');
        const robotId = robotFilter ? robotFilter.value : '';
        const url = robotId ? `/api/stats/token_usage?robot_id=${encodeURIComponent(robotId)}` : '/api/stats/token_usage';

        const res = await fetch(url);
        const data = await res.json();

        const startDate = new Date(startInput.value);
        const endDate = new Date(endInput.value);
        const endDateInclusive = new Date(endDate);
        endDateInclusive.setHours(23, 59, 59, 999);

        const dataMap = {};
        data.forEach(d => {
            dataMap[d.date] = { input: d.input || 0, output: d.output || 0, total: d.total || 0 };
        });

        const filledData = [];
        let currentDate = new Date(startDate);

        while (currentDate <= endDateInclusive) {
            const y = currentDate.getFullYear();
            const m = String(currentDate.getMonth() + 1).padStart(2, '0');
            const d = String(currentDate.getDate()).padStart(2, '0');
            const dateStr = `${y}-${m}-${d}`;

            filledData.push({
                date: dateStr,
                input: dataMap[dateStr]?.input || 0,
                output: dataMap[dateStr]?.output || 0,
                total: dataMap[dateStr]?.total || 0
            });

            currentDate.setDate(currentDate.getDate() + 1);
        }

        renderChart(filledData);
        updateStatsSummary(filledData);
    } catch (e) {
        console.error("Failed to load stats:", e);
    }
}

function toMillions(n) {
    return (n / 1_000_000).toFixed(2);
}

function updateStatsSummary(data) {
    const totalInput = data.reduce((sum, d) => sum + d.input, 0);
    const totalOutput = data.reduce((sum, d) => sum + d.output, 0);
    const totalAll = data.reduce((sum, d) => sum + d.total, 0);

    const outputAndThinking = totalAll - totalInput;
    const inputCost = (totalInput / 1_000_000 * 0.5).toFixed(2);
    const outputCost = (outputAndThinking / 1_000_000 * 3).toFixed(2);
    const totalCost = (parseFloat(inputCost) + parseFloat(outputCost)).toFixed(2);

    const summaryEl = document.getElementById('stats-summary');
    if (summaryEl) {
        summaryEl.style.position = 'relative';
        summaryEl.innerHTML = `
            <span style="position:absolute; top:8px; right:12px; font-size:11px; color:var(--text-muted);">Unit: USD</span>
            <div class="stat-item">
                <span class="stat-label">Input Tokens</span>
                <span class="stat-value" style="color: #28a745;">${toMillions(totalInput)} M</span>
                <span style="display:block; font-size:11px; color:var(--text-muted); margin-top:2px;">$0.50 / 1M &rarr; $${inputCost}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Output + Thinking</span>
                <span class="stat-value" style="color: #e67e22;">${toMillions(outputAndThinking)} M</span>
                <span style="display:block; font-size:11px; color:var(--text-muted); margin-top:2px;">$3.00 / 1M &rarr; $${outputCost}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Total Tokens</span>
                <span class="stat-value" style="color: #007bff;">${toMillions(totalAll)} M</span>
                <span style="display:block; font-size:11px; color:var(--text-muted); margin-top:2px;">Est. Cost: $${totalCost}</span>
            </div>
        `;
    }
}

function renderChart(data) {
    const ctx = document.getElementById('tokenUsageChart').getContext('2d');

    if (tokenChart) {
        tokenChart.destroy();
    }

    const labels = data.map(d => d.date);
    const inputValues = data.map(d => d.input / 1_000_000);
    const outputValues = data.map(d => d.output / 1_000_000);
    const totalValues = data.map(d => d.total / 1_000_000);

    tokenChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Input Tokens',
                    data: inputValues,
                    borderColor: '#28a745',
                    backgroundColor: 'rgba(40, 167, 69, 0.1)',
                    borderWidth: 2,
                    fill: false,
                    tension: 0.4,
                    pointBackgroundColor: '#28a745',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 5
                },
                {
                    label: 'Output Tokens',
                    data: outputValues,
                    borderColor: '#e67e22',
                    backgroundColor: 'rgba(230, 126, 34, 0.1)',
                    borderWidth: 2,
                    fill: false,
                    tension: 0.4,
                    pointBackgroundColor: '#e67e22',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 5
                },
                {
                    label: 'Total Tokens',
                    data: totalValues,
                    borderColor: '#007bff',
                    backgroundColor: 'rgba(0, 123, 255, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#007bff',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            scales: {
                x: {
                    ticks: {
                        color: '#555',
                        font: { family: "'IBM Plex Mono', monospace", size: 10 }
                    },
                    grid: { color: 'rgba(0, 0, 0, 0.08)' }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Million Tokens',
                        color: '#555',
                        font: { family: "'Chakra Petch', sans-serif", size: 11, weight: 600 }
                    },
                    ticks: {
                        color: '#555',
                        font: { family: "'IBM Plex Mono', monospace", size: 10 },
                        callback: v => v.toFixed(2) + ' M'
                    },
                    grid: { color: 'rgba(0, 0, 0, 0.08)' },
                    beginAtZero: true
                }
            },
            plugins: {
                legend: {
                    labels: {
                        color: '#333',
                        font: { family: "'Chakra Petch', sans-serif", size: 11, weight: 600 }
                    }
                },
                tooltip: {
                    backgroundColor: '#f7f5f2',
                    titleColor: '#007bff',
                    bodyColor: '#333',
                    borderColor: 'rgba(0, 0, 0, 0.15)',
                    borderWidth: 1,
                    cornerRadius: 4,
                    padding: 12,
                    titleFont: { family: "'Chakra Petch', sans-serif", size: 11 },
                    bodyFont: { family: "'IBM Plex Mono', monospace", size: 12 },
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(3)} M`
                    }
                }
            }
        }
    });
}
