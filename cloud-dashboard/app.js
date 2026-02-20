// ─── Config ───────────────────────────────────────────────────────────────────
const SUPABASE_URL = 'https://shnrmpapyfgorcccciqw.supabase.co';
const VERIFY_FUNCTION_URL = `${SUPABASE_URL}/functions/v1/verify-share`;
const DASHBOARD_API_URL = `${SUPABASE_URL}/functions/v1/dashboard-api`;

// ─── Module-level state ────────────────────────────────────────────────────────
let siteId = null;
let accessToken = null;
let tokenChart = null;
let pollTimer = null;

// ─── Utility helpers ───────────────────────────────────────────────────────────

function getShareToken() {
  const match = window.location.pathname.match(/^\/share\/([^/]+)/);
  return match ? match[1] : null;
}

function parseJwtPayload(jwt) {
  try {
    const base64 = jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

function formatDateTime(isoString) {
  if (!isoString) return '—';
  return new Date(isoString).toLocaleString();
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function statusClass(status) {
  if (!status) return '';
  return `status-${status.toLowerCase().replace(/\s+/g, '-')}`;
}

function isoDateDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

// ─── API helper ──────────────────────────────────────────────────────────────

async function apiCall(action, params = {}) {
  const res = await fetch(DASHBOARD_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ action, params }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `API error ${res.status}`);
  }
  return res.json();
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

async function authenticate(password) {
  const shareToken = getShareToken();
  if (!shareToken) {
    showAuthError('Invalid share link.');
    return;
  }

  const btn = document.getElementById('auth-submit');
  btn.disabled = true;
  btn.textContent = 'Verifying…';

  try {
    const res = await fetch(VERIFY_FUNCTION_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: shareToken, password }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showAuthError(body.error || 'Incorrect password.');
      return;
    }

    const { access_token } = await res.json();
    if (!access_token) {
      showAuthError('Authentication failed: no token returned.');
      return;
    }

    accessToken = access_token;
    const payload = parseJwtPayload(access_token);
    siteId = payload?.site_id ?? null;
    const siteName = payload?.site_name ?? null;

    showDashboard(siteName);
  } catch (err) {
    showAuthError('Network error. Please try again.');
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Access Dashboard';
  }
}

function showAuthError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.hidden = false;
}

// ─── Dashboard initialisation ─────────────────────────────────────────────────

async function showDashboard(siteName) {
  document.getElementById('auth-screen').hidden = true;
  document.getElementById('dashboard').hidden = false;

  if (siteName) {
    const titleEl = document.getElementById('header-title');
    const logoImg = titleEl.querySelector('.header-logo');
    titleEl.textContent = `Visual Patrol — ${siteName}`;
    if (logoImg) titleEl.prepend(logoImg);
    document.title = `Visual Patrol — ${siteName}`;
  }

  document.getElementById('tokens-date-from').value = isoDateDaysAgo(30);
  document.getElementById('tokens-date-to').value = todayIso();

  await loadRobots();
  await loadHistory();

  // Poll for updates every 30s (replaces real-time subscription)
  pollTimer = setInterval(() => {
    if (!document.getElementById('tab-history').hidden) loadHistory();
  }, 30000);
  document.getElementById('realtime-indicator').hidden = false;
}

// ─── Robots ───────────────────────────────────────────────────────────────────

async function loadRobots() {
  try {
    const robots = await apiCall('robots');

    ['history-robot-filter', 'tokens-robot-filter'].forEach(id => {
      const sel = document.getElementById(id);
      while (sel.options.length > 1) sel.remove(1);
      robots.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.robot_id;
        opt.textContent = r.robot_name || r.robot_id;
        sel.appendChild(opt);
      });
    });
  } catch (error) {
    console.error('loadRobots error:', error);
  }
}

// ─── History tab ─────────────────────────────────────────────────────────────

async function loadHistory() {
  const listEl = document.getElementById('history-list');
  listEl.innerHTML = '<p class="state-loading">Loading history…</p>';

  const robotFilter = document.getElementById('history-robot-filter').value;

  try {
    const params = {};
    if (robotFilter) params.robot_id = robotFilter;
    const runs = await apiCall('history', params);

    if (runs.length === 0) {
      listEl.innerHTML = '<p class="state-empty">No patrol runs found.</p>';
      return;
    }

    listEl.innerHTML = runs.map(run => renderRunCard(run)).join('');

    listEl.querySelectorAll('.run-card').forEach(card => {
      card.addEventListener('click', () => {
        showRunDetail(card.dataset.cloudRunId);
      });
    });
  } catch (error) {
    listEl.innerHTML = `<p class="state-error">Failed to load history: ${escapeHtml(error.message)}</p>`;
  }
}

function renderRunCard(run) {
  const robotName = run.robot_name || run.robot_id;
  const tokens = run.total_tokens != null ? `${run.total_tokens.toLocaleString()} tokens` : '';
  return `
    <div class="run-card" data-cloud-run-id="${escapeHtml(run.id)}">
      <div class="run-card-header">
        <span class="run-id">#${escapeHtml(String(run.local_id ?? run.id))}</span>
        <span class="badge ${statusClass(run.status)}">${escapeHtml(run.status ?? 'unknown')}</span>
      </div>
      <div class="run-card-meta">
        <span class="run-robot">${escapeHtml(robotName)}</span>
        <span class="run-time">${formatDateTime(run.start_time)}</span>
        ${tokens ? `<span class="run-tokens">${tokens}</span>` : ''}
      </div>
    </div>
  `;
}

// ─── Run detail modal ─────────────────────────────────────────────────────────

async function showRunDetail(cloudRunId) {
  const modal = document.getElementById('run-modal');
  const content = document.getElementById('modal-content');
  content.innerHTML = '<p class="state-loading">Loading…</p>';
  modal.hidden = false;
  document.body.style.overflow = 'hidden';

  try {
    const { run, inspections, alerts } = await apiCall('run-detail', { run_id: cloudRunId });

    let html = `
      <h2 class="modal-run-title">Patrol Run #${escapeHtml(String(run.local_id ?? run.id))}</h2>
      <div class="modal-run-meta">
        <span><strong>Robot:</strong> ${escapeHtml(run.robot_id)}</span>
        <span><strong>Status:</strong> <span class="badge ${statusClass(run.status)}">${escapeHtml(run.status ?? '—')}</span></span>
        <span><strong>Start:</strong> ${formatDateTime(run.start_time)}</span>
        <span><strong>End:</strong> ${formatDateTime(run.end_time)}</span>
      </div>
    `;

    if (run.report_content) {
      html += `
        <section class="modal-section">
          <h3>Report</h3>
          <div class="markdown-body">${marked.parse(run.report_content)}</div>
        </section>
      `;
    }

    if (inspections && inspections.length > 0) {
      html += `<section class="modal-section"><h3>Inspection Points (${inspections.length})</h3>`;
      html += inspections.map(insp => renderInspectionCard(insp)).join('');
      html += `</section>`;
    }

    if (alerts && alerts.length > 0) {
      html += `<section class="modal-section"><h3>Edge AI Alerts (${alerts.length})</h3>`;
      html += alerts.map(alert => renderAlertCard(alert)).join('');
      html += `</section>`;
    }

    content.innerHTML = html;
  } catch (error) {
    content.innerHTML = `<p class="state-error">Failed to load run details.</p>`;
  }
}

function renderInspectionCard(insp) {
  const isNg = insp.is_ng;
  let description = '';
  if (insp.ai_response) {
    try {
      const parsed = typeof insp.ai_response === 'string'
        ? JSON.parse(insp.ai_response)
        : insp.ai_response;
      description = parsed.description || parsed.result || JSON.stringify(parsed);
    } catch {
      description = String(insp.ai_response);
    }
  }

  const imgHtml = insp.image_url
    ? `<img class="inspection-image" src="${escapeHtml(insp.image_url)}" alt="Inspection image" loading="lazy" />`
    : '';

  return `
    <div class="inspection-card ${isNg ? 'ng' : 'ok'}">
      <div class="inspection-header">
        <span class="inspection-point">${escapeHtml(insp.point_name ?? '—')}</span>
        <span class="inspection-verdict ${isNg ? 'verdict-ng' : 'verdict-ok'}">${isNg ? 'NG' : 'OK'}</span>
      </div>
      ${description ? `<p class="inspection-description">${escapeHtml(description)}</p>` : ''}
      ${imgHtml}
    </div>
  `;
}

function renderAlertCard(alert) {
  return `
    <div class="alert-card">
      <div class="alert-header">
        <span class="alert-rule">${escapeHtml(alert.rule ?? '—')}</span>
        <span class="alert-source">${escapeHtml(alert.stream_source ?? '')}</span>
      </div>
      <p class="alert-response">${escapeHtml(alert.response ?? '')}</p>
      ${alert.image_url ? `<img class="inspection-image" src="${escapeHtml(alert.image_url)}" alt="Alert image" loading="lazy" />` : ''}
    </div>
  `;
}

function closeModal() {
  document.getElementById('run-modal').hidden = true;
  document.body.style.overflow = '';
}

// ─── Reports tab ─────────────────────────────────────────────────────────────

async function loadReports() {
  const listEl = document.getElementById('reports-list');
  listEl.innerHTML = '<p class="state-loading">Loading reports…</p>';

  try {
    const reports = await apiCall('reports');

    if (reports.length === 0) {
      listEl.innerHTML = '<p class="state-empty">No reports found.</p>';
      return;
    }

    listEl.innerHTML = reports.map(r => renderReportCard(r)).join('');

    listEl.querySelectorAll('.report-header').forEach(header => {
      header.addEventListener('click', () => {
        const card = header.closest('.report-card');
        card.classList.toggle('collapsed');
      });
    });
  } catch (error) {
    listEl.innerHTML = `<p class="state-error">Failed to load reports: ${escapeHtml(error.message)}</p>`;
  }
}

function renderReportCard(report) {
  const robotName = report.robot_id || '—';
  const dateRange = `${report.start_date ?? '?'} → ${report.end_date ?? '?'}`;
  const tokens = report.total_tokens != null ? `${report.total_tokens.toLocaleString()} tokens` : '';
  const bodyHtml = report.report_content
    ? marked.parse(report.report_content)
    : '<em>No content</em>';

  return `
    <div class="report-card collapsed">
      <div class="report-header">
        <span class="report-id">#${escapeHtml(String(report.local_id ?? report.id))}</span>
        <span class="report-range">${escapeHtml(dateRange)}</span>
        <span class="report-robot">${escapeHtml(robotName)}</span>
        ${tokens ? `<span class="report-tokens">${tokens}</span>` : ''}
        <span class="report-toggle-icon">&#9660;</span>
      </div>
      <div class="report-body">
        <div class="markdown-body">${bodyHtml}</div>
      </div>
    </div>
  `;
}

// ─── Token stats tab ──────────────────────────────────────────────────────────

function toMillions(n) {
  return (n / 1_000_000).toFixed(2);
}

async function loadTokenStats() {
  const fromDate = document.getElementById('tokens-date-from').value;
  const toDate = document.getElementById('tokens-date-to').value;
  const robotFilter = document.getElementById('tokens-robot-filter').value;

  try {
    const params = {};
    if (fromDate) params.date_from = fromDate;
    if (toDate) params.date_to = toDate;
    if (robotFilter) params.robot_id = robotFilter;

    // API returns date-aggregated { date, input, output, total }
    const data = await apiCall('token-stats', params);

    // Fill missing dates in range
    const filledData = [];
    if (fromDate && toDate) {
      const dataMap = {};
      data.forEach(d => { dataMap[d.date] = d; });
      const cur = new Date(fromDate);
      const end = new Date(toDate);
      end.setHours(23, 59, 59, 999);
      while (cur <= end) {
        const ds = cur.toISOString().slice(0, 10);
        filledData.push(dataMap[ds] || { date: ds, input: 0, output: 0, total: 0 });
        cur.setDate(cur.getDate() + 1);
      }
    } else {
      filledData.push(...data);
    }

    renderTokenChart(filledData);
    updateTokensSummary(filledData);
  } catch (error) {
    console.error('loadTokenStats error:', error);
  }
}

function updateTokensSummary(data) {
  const totalInput = data.reduce((s, d) => s + d.input, 0);
  const totalAll = data.reduce((s, d) => s + d.total, 0);
  const outputAndThinking = totalAll - totalInput;
  const inputCost = (totalInput / 1_000_000 * 0.5).toFixed(2);
  const outputCost = (outputAndThinking / 1_000_000 * 3).toFixed(2);
  const totalCost = (parseFloat(inputCost) + parseFloat(outputCost)).toFixed(2);

  const el = document.getElementById('tokens-summary');
  if (!el) return;
  el.innerHTML = `
    <div class="stat-item">
      <span class="stat-label">Input Tokens</span>
      <span class="stat-value" style="color:#28a745">${toMillions(totalInput)} M</span>
      <span class="stat-detail">$0.50 / 1M &rarr; $${inputCost}</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Output + Thinking</span>
      <span class="stat-value" style="color:#e67e22">${toMillions(outputAndThinking)} M</span>
      <span class="stat-detail">$3.00 / 1M &rarr; $${outputCost}</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Total Tokens</span>
      <span class="stat-value" style="color:#007bff">${toMillions(totalAll)} M</span>
      <span class="stat-detail">Est. Cost: $${totalCost}</span>
    </div>
  `;
}

function renderTokenChart(data) {
  const ctx = document.getElementById('tokens-chart').getContext('2d');

  if (tokenChart) {
    tokenChart.destroy();
    tokenChart = null;
  }

  const labels = data.map(d => d.date);
  const inputValues = data.map(d => d.input / 1_000_000);
  const outputValues = data.map(d => d.output / 1_000_000);
  const totalValues = data.map(d => d.total / 1_000_000);

  tokenChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Input Tokens', data: inputValues, borderColor: '#28a745', backgroundColor: 'rgba(40,167,69,0.1)', borderWidth: 2, fill: false, tension: 0.4, pointRadius: 3 },
        { label: 'Output Tokens', data: outputValues, borderColor: '#e67e22', backgroundColor: 'rgba(230,126,34,0.1)', borderWidth: 2, fill: false, tension: 0.4, pointRadius: 3 },
        { label: 'Total Tokens', data: totalValues, borderColor: '#007bff', backgroundColor: 'rgba(0,123,255,0.1)', borderWidth: 2, fill: true, tension: 0.4, pointRadius: 4 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: {
          title: { display: true, text: 'Million Tokens' },
          ticks: { callback: v => v.toFixed(2) + ' M' },
          beginAtZero: true,
        },
      },
      plugins: {
        legend: { position: 'top' },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(3)} M` } },
      },
    },
  });
}

// ─── Tab switching ────────────────────────────────────────────────────────────

function switchTab(tabName) {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-content').forEach(section => {
    section.hidden = section.id !== `tab-${tabName}`;
  });

  if (tabName === 'reports') loadReports();
  else if (tabName === 'tokens') loadTokenStats();
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────

function init() {
  const shareToken = getShareToken();

  if (!shareToken) {
    document.getElementById('auth-screen').innerHTML = `
      <div class="auth-card">
        <img src="/favicon.png" alt="logo" class="auth-logo" />
        <h1 class="auth-title">Visual Patrol</h1>
        <p class="auth-error" style="display:block">Invalid link. Please use the share URL you were provided.</p>
      </div>
    `;
    return;
  }

  document.getElementById('auth-submit').addEventListener('click', () => {
    authenticate(document.getElementById('password-input').value);
  });

  document.getElementById('password-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') authenticate(e.target.value);
  });

  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  document.getElementById('history-robot-filter').addEventListener('change', loadHistory);
  document.getElementById('tokens-load-btn').addEventListener('click', loadTokenStats);

  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('run-modal').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
  });
}

init();
