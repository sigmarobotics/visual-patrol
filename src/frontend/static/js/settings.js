// settings.js — Settings load/save, clock, registered robots display
import state, { escapeHtml } from './state.js';

export function initSettings() {
    const btnSaveSettings = document.getElementById('btn-save-settings');
    if (btnSaveSettings) btnSaveSettings.addEventListener('click', saveSettings);

    loadSettings();
    startClock();
}

export async function loadSettings() {
    const res = await fetch('/api/settings');
    const data = await res.json();
    document.getElementById('setting-api-key').value = data.gemini_api_key || '';
    document.getElementById('setting-model').value = data.gemini_model || 'gemini-1.5-flash';

    const tz = data.timezone || 'UTC';
    document.getElementById('setting-timezone').value = tz;
    state.currentSettingsTimezone = tz;
    document.getElementById('setting-role').value = data.system_prompt || '';
    document.getElementById('setting-report-prompt').value = data.report_prompt || '';

    const multidayPrompt = document.getElementById('setting-multiday-report-prompt');
    if (multidayPrompt) multidayPrompt.value = data.multiday_report_prompt || '';

    const turboCheckbox = document.getElementById('setting-turbo-mode');
    if (turboCheckbox) turboCheckbox.checked = data.turbo_mode === true;

    const videoCheckbox = document.getElementById('setting-enable-video');
    if (videoCheckbox) videoCheckbox.checked = data.enable_video_recording === true;

    const videoPrompt = document.getElementById('setting-video-prompt');
    if (videoPrompt) videoPrompt.value = data.video_prompt || '';

    const idleStreamCheckbox = document.getElementById('setting-enable-idle-stream');
    if (idleStreamCheckbox) {
        idleStreamCheckbox.checked = data.enable_idle_stream !== false;
        state.currentIdleStreamEnabled = idleStreamCheckbox.checked;
    }

    const telegramCheckbox = document.getElementById('setting-enable-telegram');
    if (telegramCheckbox) telegramCheckbox.checked = data.enable_telegram === true;

    const telegramBotToken = document.getElementById('setting-telegram-bot-token');
    if (telegramBotToken) telegramBotToken.value = data.telegram_bot_token || '';

    const telegramUserId = document.getElementById('setting-telegram-user-id');
    if (telegramUserId) telegramUserId.value = data.telegram_user_id || '';

    const telegramMessagePrompt = document.getElementById('setting-telegram-message-prompt');
    if (telegramMessagePrompt) telegramMessagePrompt.value = data.telegram_message_prompt || '';

    // Edge AI settings
    const liveMonitorCheckbox = document.getElementById('setting-enable-edge-ai');
    if (liveMonitorCheckbox) liveMonitorCheckbox.checked = data.enable_edge_ai === true;

    const liveMonitorRules = document.getElementById('setting-edge-ai-rules');
    if (liveMonitorRules) {
        const rules = data.edge_ai_rules || [];
        liveMonitorRules.value = Array.isArray(rules) ? rules.join('\n') : '';
    }

    // Jetson host (auto-derives JPS, mediamtx, relay URLs)
    const jetsonHost = document.getElementById('setting-jetson-host');
    if (jetsonHost) jetsonHost.value = data.jetson_host || '';

    // Stream source radio (mutually exclusive — JPS supports 1 stream)
    const radioRobot = document.getElementById('setting-stream-source-robot');
    const radioExternal = document.getElementById('setting-stream-source-external');
    if (radioRobot && radioExternal) {
        if (data.enable_external_rtsp === true) {
            radioExternal.checked = true;
        } else if (data.enable_robot_camera_relay === true) {
            radioRobot.checked = true;
        }
    }

    const externalRtspUrl = document.getElementById('setting-external-rtsp-url');
    if (externalRtspUrl) externalRtspUrl.value = data.external_rtsp_url || '';

    // Load registered robots list
    loadRobotsList();
}

async function loadRobotsList() {
    const container = document.getElementById('registered-robots-list');
    if (!container) return;

    try {
        const res = await fetch('/api/robots');
        const robots = await res.json();

        if (robots.length === 0) {
            container.innerHTML = '<div style="color: var(--text-muted); font-size: 12px;">No robots registered yet.</div>';
            return;
        }

        container.innerHTML = robots.map(r => `
            <div class="robot-info-row">
                <span class="robot-info-name">${escapeHtml(r.robot_name)}</span>
                <span class="robot-info-id">${escapeHtml(r.robot_id)}</span>
                <span class="robot-info-ip">${escapeHtml(r.robot_ip || '-')}</span>
                <span class="robot-info-status ${r.status === 'online' ? 'online' : 'offline'}">${escapeHtml(r.status)}</span>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '<div style="color: var(--coral); font-size: 12px;">Failed to load robots.</div>';
    }
}

async function saveSettings() {
    const apiKeyVal = document.getElementById('setting-api-key').value;
    const telegramTokenVal = document.getElementById('setting-telegram-bot-token').value;
    const telegramUserVal = document.getElementById('setting-telegram-user-id').value;

    const settings = {
        gemini_api_key: apiKeyVal,
        gemini_model: document.getElementById('setting-model').value,
        timezone: document.getElementById('setting-timezone').value,
        system_prompt: document.getElementById('setting-role').value,
        report_prompt: document.getElementById('setting-report-prompt').value,
        multiday_report_prompt: document.getElementById('setting-multiday-report-prompt')?.value || '',
        turbo_mode: document.getElementById('setting-turbo-mode').checked,
        enable_video_recording: document.getElementById('setting-enable-video').checked,
        video_prompt: document.getElementById('setting-video-prompt').value,
        enable_idle_stream: document.getElementById('setting-enable-idle-stream').checked,
        enable_telegram: document.getElementById('setting-enable-telegram').checked,
        telegram_bot_token: document.getElementById('setting-telegram-bot-token').value,
        telegram_user_id: document.getElementById('setting-telegram-user-id').value,
        telegram_message_prompt: document.getElementById('setting-telegram-message-prompt')?.value || '',
        enable_edge_ai: document.getElementById('setting-enable-edge-ai')?.checked || false,
        edge_ai_rules: (document.getElementById('setting-edge-ai-rules')?.value || '')
            .split('\n').map(s => s.trim()).filter(s => s.length > 0),
        jetson_host: document.getElementById('setting-jetson-host')?.value || '',
        enable_robot_camera_relay: document.getElementById('setting-stream-source-robot')?.checked || false,
        enable_external_rtsp: document.getElementById('setting-stream-source-external')?.checked || false,
        external_rtsp_url: document.getElementById('setting-external-rtsp-url')?.value || '',
    };
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            alert('Failed to save settings: ' + (data.error || 'Unknown error'));
            return;
        }
        state.currentSettingsTimezone = settings.timezone;
        state.currentIdleStreamEnabled = settings.enable_idle_stream;
        alert('Settings Saved!');
    } catch (e) {
        alert('Failed to save settings: ' + e.message);
    }
}

// --- Test Edge AI (relay → VILA JPS → WebSocket alerts) ---
let _testStatusPollId = null;

export async function testEdgeAI() {
    const btn = document.getElementById('btn-test-edge-ai');
    const statusEl = document.getElementById('edge-ai-test-status');
    const resultsEl = document.getElementById('edge-ai-test-results');

    // If already running, stop it
    if (_testStatusPollId) {
        await fetch(`/api/${state.selectedRobotId}/test_edge_ai/stop`, { method: 'POST' });
        clearInterval(_testStatusPollId);
        _testStatusPollId = null;
        btn.textContent = 'Test Edge AI';
        btn.classList.replace('btn-danger', 'btn-premium');
        statusEl.textContent = 'Stopped';
        return;
    }

    // Read current form values
    const jetsonHost = document.getElementById('setting-jetson-host')?.value || '';
    const rulesText = document.getElementById('setting-edge-ai-rules')?.value || '';
    const rules = rulesText.split('\n').map(s => s.trim()).filter(s => s.length > 0);

    // Determine stream source from radio buttons
    const radioExternal = document.getElementById('setting-stream-source-external');
    const streamSource = radioExternal?.checked ? 'external_rtsp' : 'robot_camera';
    const externalRtspUrl = document.getElementById('setting-external-rtsp-url')?.value || '';

    if (!jetsonHost) {
        alert('Please enter the Jetson Host IP first.');
        return;
    }
    if (rules.length === 0) {
        alert('Please enter at least one alert rule.');
        return;
    }

    // Start test
    statusEl.textContent = 'Starting relay...';
    resultsEl.style.display = 'block';
    const streamSuffix = streamSource === 'external_rtsp' ? 'external' : 'camera';
    const rtspUrl = `rtsp://${jetsonHost}:8555/${state.selectedRobotId}/${streamSuffix}`;
    resultsEl.innerHTML =
        `<div style="margin-bottom:8px; padding:8px 10px; background:rgba(0,200,180,0.08); border:1px solid var(--cyan-dim); border-radius:4px; font-size:12px;">` +
        `請用VLC打開這個網址: <code style="user-select:all; color:var(--cyan-glow); font-weight:bold;">${escapeHtml(rtspUrl)}</code>` +
        `</div>` +
        `<div id="test-ws-log"></div>`;

    try {
        const res = await fetch(`/api/${state.selectedRobotId}/test_edge_ai/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jetson_host: jetsonHost, rules, stream_source: streamSource, external_rtsp_url: externalRtspUrl }),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            statusEl.textContent = 'Error: ' + (data.error || 'Unknown');
            return;
        }
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        return;
    }

    btn.textContent = 'Stop Test';
    btn.classList.replace('btn-premium', 'btn-danger');
    statusEl.textContent = 'Starting relay...';

    let lastMsgCount = 0;

    // Poll for status + WS messages every 2s
    _testStatusPollId = setInterval(async () => {
        try {
            const res = await fetch(`/api/${state.selectedRobotId}/test_edge_ai/status`);
            const status = await res.json();

            if (!status.active && _testStatusPollId) {
                clearInterval(_testStatusPollId);
                _testStatusPollId = null;
                btn.textContent = 'Test Edge AI';
                btn.classList.replace('btn-danger', 'btn-premium');
                statusEl.textContent = status.error ? 'Error: ' + status.error : 'Stopped';
                return;
            }

            const wsLabel = status.ws_connected ? 'WS Connected' : 'WS Connecting...';
            statusEl.textContent = `${wsLabel} | Messages: ${(status.ws_messages || []).length} | Alerts: ${status.alert_count}`;
            if (status.error) statusEl.textContent += ` | ${status.error}`;

            // Append new WS messages
            const logEl = document.getElementById('test-ws-log');
            const msgs = status.ws_messages || [];
            if (logEl && msgs.length > lastMsgCount) {
                const newMsgs = msgs.slice(lastMsgCount);
                for (const m of newMsgs) {
                    const div = document.createElement('div');
                    const isAlert = m.event && (m.event.rule_string || m.event.alert || m.event.rule);
                    div.style.cssText = isAlert
                        ? 'margin-bottom:4px; padding:4px 8px; background:rgba(231,76,60,0.1); border-left:3px solid var(--coral); border-radius:3px; font-size:12px; font-family:monospace; word-break:break-all;'
                        : 'margin-bottom:4px; padding:4px 8px; background:var(--bg-secondary); border-left:3px solid var(--border-subtle); border-radius:3px; font-size:12px; font-family:monospace; word-break:break-all;';
                    const content = m.event ? JSON.stringify(m.event) : (m.raw || '?');
                    div.innerHTML =
                        `<span style="color:var(--text-muted); margin-right:6px;">${escapeHtml(m.timestamp)}</span>` +
                        `<span>${escapeHtml(content)}</span>`;
                    logEl.appendChild(div);
                }
                lastMsgCount = msgs.length;
                resultsEl.scrollTop = resultsEl.scrollHeight;
            }
        } catch (e) { /* ignore */ }
    }, 2000);
}
window.testEdgeAI = testEdgeAI;

export function switchSettingsTab(tabName) {
    document.querySelectorAll('.settings-tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.settings-tab-content').forEach(div => div.classList.remove('active'));
    document.getElementById(`settings-tab-${tabName}`)?.classList.add('active');
    document.querySelector(`.settings-tab-btn[onclick="switchSettingsTab('${tabName}')"]`)?.classList.add('active');
}
window.switchSettingsTab = switchSettingsTab;

function startClock() {
    if (state._intervals.clock) return; // Prevent duplicate intervals

    const timeValue = document.getElementById('time-value');
    state._intervals.clock = setInterval(() => {
        if (timeValue) {
            try {
                timeValue.textContent = new Date().toLocaleTimeString('en-US', {
                    timeZone: state.currentSettingsTimezone,
                    hour12: false,
                    hour: '2-digit',
                    minute: '2-digit'
                });
            } catch (e) {
                timeValue.textContent = "--:--";
            }
        }
    }, 1000);
}
