// ─── Config ───────────────────────────────────────────────────────────────────
const SUPABASE_URL = 'https://shnrmpapyfgorcccciqw.supabase.co';
const SUPABASE_ANON_KEY = '__SUPABASE_ANON_KEY__'; // placeholder — replaced at deploy time
const VERIFY_FUNCTION_URL = `${SUPABASE_URL}/functions/v1/verify-share`;

// ─── Module-level state ────────────────────────────────────────────────────────
let supabase = null;
let siteId = null;
let accessToken = null;
let tokenChart = null;
let realtimeChannel = null;

// ─── Utility helpers ───────────────────────────────────────────────────────────

function getShareToken() {
  // URL shape: /share/{token}
  const match = window.location.pathname.match(/^\/share\/([^/]+)/);
  return match ? match[1] : null;
}

function parseJwtPayload(jwt) {
  try {
    const base64 = jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64));
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

    // Init Supabase client with custom auth header so RLS can read JWT claims
    supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      global: {
        headers: { Authorization: 'Bearer ' + accessToken },
      },
    });

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
    document.getElementById('header-title').textContent = `Visual Patrol — ${siteName}`;
    document.title = `Visual Patrol — ${siteName}`;
  }

  // Set default token date range
  document.getElementById('tokens-date-from').value = isoDateDaysAgo(30);
  document.getElementById('tokens-date-to').value = todayIso();

  await loadRobots();
  await loadHistory();
  setupRealtimeSubscription();
}

// ─── Robots ───────────────────────────────────────────────────────────────────

async function loadRobots() {
  const { data, error } = await supabase
    .from('robots')
    .select('robot_id, robot_name')
    .eq('site_id', siteId)
    .order('robot_name');

  if (error) {
    console.error('loadRobots error:', error);
    return;
  }

  const robots = data ?? [];

  // Populate both robot filter dropdowns
  ['history-robot-filter', 'tokens-robot-filter'].forEach(id => {
    const sel = document.getElementById(id);
    // Remove old dynamic options (keep the first "All robots" option)
    while (sel.options.length > 1) sel.remove(1);
    robots.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.robot_id;
      opt.textContent = r.robot_name || r.robot_id;
      sel.appendChild(opt);
    });
  });
}

// ─── History tab ─────────────────────────────────────────────────────────────

async function loadHistory() {
  const listEl = document.getElementById('history-list');
  listEl.innerHTML = '<p class="state-loading">Loading history…</p>';

  const robotFilter = document.getElementById('history-robot-filter').value;

  let query = supabase
    .from('patrol_runs')
    .select('id, local_id, robot_id, status, start_time, end_time, total_tokens, robots(robot_name)')
    .eq('site_id', siteId)
    .order('local_id', { ascending: false })
    .limit(100);

  if (robotFilter) query = query.eq('robot_id', robotFilter);

  const { data, error } = await query;

  if (error) {
    listEl.innerHTML = `<p class="state-error">Failed to load history: ${escapeHtml(error.message)}</p>`;
    return;
  }

  const runs = data ?? [];
  if (runs.length === 0) {
    listEl.innerHTML = '<p class="state-empty">No patrol runs found.</p>';
    return;
  }

  listEl.innerHTML = runs.map(run => renderRunCard(run)).join('');

  // Attach click handlers
  listEl.querySelectorAll('.run-card').forEach(card => {
    card.addEventListener('click', () => {
      showRunDetail(card.dataset.cloudRunId);
    });
  });
}

function renderRunCard(run) {
  const robotName = run.robots?.robot_name || run.robot_id;
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

  // Fetch run
  const { data: run, error: runError } = await supabase
    .from('patrol_runs')
    .select('*, robots(robot_name)')
    .eq('id', cloudRunId)
    .single();

  if (runError || !run) {
    content.innerHTML = `<p class="state-error">Failed to load run details.</p>`;
    return;
  }

  // Fetch inspection results
  const { data: inspections } = await supabase
    .from('inspection_results')
    .select('*')
    .eq('run_id', cloudRunId)
    .order('id');

  // Fetch edge AI alerts
  const { data: alerts } = await supabase
    .from('edge_ai_alerts')
    .select('*')
    .eq('run_id', cloudRunId)
    .order('id');

  const robotName = run.robots?.robot_name || run.robot_id;

  let html = `
    <h2 class="modal-run-title">Patrol Run #${escapeHtml(String(run.local_id ?? run.id))}</h2>
    <div class="modal-run-meta">
      <span><strong>Robot:</strong> ${escapeHtml(robotName)}</span>
      <span><strong>Status:</strong> <span class="badge ${statusClass(run.status)}">${escapeHtml(run.status ?? '—')}</span></span>
      <span><strong>Start:</strong> ${formatDateTime(run.start_time)}</span>
      <span><strong>End:</strong> ${formatDateTime(run.end_time)}</span>
    </div>
  `;

  // Report content (markdown)
  if (run.report_content) {
    html += `
      <section class="modal-section">
        <h3>Report</h3>
        <div class="markdown-body">${marked.parse(run.report_content)}</div>
      </section>
    `;
  }

  // Inspection results
  if (inspections && inspections.length > 0) {
    html += `<section class="modal-section"><h3>Inspection Points (${inspections.length})</h3>`;
    html += inspections.map(insp => renderInspectionCard(insp)).join('');
    html += `</section>`;
  }

  // Edge AI alerts
  if (alerts && alerts.length > 0) {
    html += `<section class="modal-section"><h3>Edge AI Alerts (${alerts.length})</h3>`;
    html += alerts.map(alert => renderAlertCard(alert)).join('');
    html += `</section>`;
  }

  content.innerHTML = html;
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

  const { data, error } = await supabase
    .from('generated_reports')
    .select('id, local_id, start_date, end_date, report_content, total_tokens, robot_id, robots(robot_name)')
    .eq('site_id', siteId)
    .order('local_id', { ascending: false })
    .limit(50);

  if (error) {
    listEl.innerHTML = `<p class="state-error">Failed to load reports: ${escapeHtml(error.message)}</p>`;
    return;
  }

  const reports = data ?? [];
  if (reports.length === 0) {
    listEl.innerHTML = '<p class="state-empty">No reports found.</p>';
    return;
  }

  listEl.innerHTML = reports.map(r => renderReportCard(r)).join('');

  // Toggle collapse on header click
  listEl.querySelectorAll('.report-header').forEach(header => {
    header.addEventListener('click', () => {
      const card = header.closest('.report-card');
      card.classList.toggle('collapsed');
    });
  });
}

function renderReportCard(report) {
  const robotName = report.robots?.robot_name || report.robot_id || '—';
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

async function loadTokenStats() {
  const fromDate = document.getElementById('tokens-date-from').value;
  const toDate = document.getElementById('tokens-date-to').value;
  const robotFilter = document.getElementById('tokens-robot-filter').value;

  // Query patrol_runs for token columns
  let query = supabase
    .from('patrol_runs')
    .select([
      'start_time',
      'inspection_input_tokens', 'inspection_output_tokens',
      'report_input_tokens', 'report_output_tokens',
      'telegram_input_tokens', 'telegram_output_tokens',
      'video_input_tokens', 'video_output_tokens',
      'total_tokens',
    ].join(','))
    .eq('site_id', siteId)
    .not('start_time', 'is', null);

  if (fromDate) query = query.gte('start_time', fromDate);
  if (toDate) {
    // Include the full toDate day by using < toDate+1
    const nextDay = new Date(toDate);
    nextDay.setDate(nextDay.getDate() + 1);
    query = query.lt('start_time', nextDay.toISOString().slice(0, 10));
  }
  if (robotFilter) query = query.eq('robot_id', robotFilter);

  const { data, error } = await query.order('start_time');

  if (error) {
    console.error('loadTokenStats error:', error);
    return;
  }

  const runs = data ?? [];

  // Group by date (local date from start_time)
  const byDate = {};
  for (const run of runs) {
    const date = run.start_time.slice(0, 10);
    if (!byDate[date]) byDate[date] = { inspection: 0, report: 0, telegram: 0, video: 0 };
    const g = byDate[date];

    const reportToks = (run.report_input_tokens ?? 0) + (run.report_output_tokens ?? 0);
    const telegramToks = (run.telegram_input_tokens ?? 0) + (run.telegram_output_tokens ?? 0);
    const videoToks = (run.video_input_tokens ?? 0) + (run.video_output_tokens ?? 0);
    const total = run.total_tokens ?? 0;
    // Inspection = total minus the rest
    const inspectionToks = Math.max(0, total - reportToks - telegramToks - videoToks);

    g.inspection += inspectionToks;
    g.report += reportToks;
    g.telegram += telegramToks;
    g.video += videoToks;
  }

  const labels = Object.keys(byDate).sort();
  const inspectionData = labels.map(d => byDate[d].inspection);
  const reportData = labels.map(d => byDate[d].report);
  const telegramData = labels.map(d => byDate[d].telegram);
  const videoData = labels.map(d => byDate[d].video);

  renderTokenChart(labels, inspectionData, reportData, telegramData, videoData);
}

function renderTokenChart(labels, inspectionData, reportData, telegramData, videoData) {
  const ctx = document.getElementById('tokens-chart').getContext('2d');

  if (tokenChart) {
    tokenChart.destroy();
    tokenChart = null;
  }

  tokenChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Inspection',
          data: inspectionData,
          backgroundColor: 'rgba(40, 167, 69, 0.8)',
          stack: 'tokens',
        },
        {
          label: 'Report',
          data: reportData,
          backgroundColor: 'rgba(26, 115, 232, 0.8)',
          stack: 'tokens',
        },
        {
          label: 'Telegram',
          data: telegramData,
          backgroundColor: 'rgba(255, 152, 0, 0.8)',
          stack: 'tokens',
        },
        {
          label: 'Video',
          data: videoData,
          backgroundColor: 'rgba(156, 39, 176, 0.8)',
          stack: 'tokens',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString()}`,
          },
        },
      },
      scales: {
        x: { stacked: true },
        y: {
          stacked: true,
          ticks: {
            callback: v => v.toLocaleString(),
          },
        },
      },
    },
  });
}

// ─── Real-time subscription ───────────────────────────────────────────────────

function setupRealtimeSubscription() {
  if (!supabase || !siteId) return;

  realtimeChannel = supabase
    .channel(`site-${siteId}-updates`)
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'patrol_runs',
        filter: `site_id=eq.${siteId}`,
      },
      () => {
        // New run started — refresh history if that tab is visible
        if (!document.getElementById('tab-history').hidden) {
          loadHistory();
        }
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'patrol_runs',
        filter: `site_id=eq.${siteId}`,
      },
      () => {
        if (!document.getElementById('tab-history').hidden) {
          loadHistory();
        }
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'inspection_results',
        filter: `site_id=eq.${siteId}`,
      },
      () => {
        // New inspection result — just refresh history list badges/tokens
        if (!document.getElementById('tab-history').hidden) {
          loadHistory();
        }
      }
    )
    .subscribe(status => {
      if (status === 'SUBSCRIBED') {
        document.getElementById('realtime-indicator').hidden = false;
      }
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

  // Lazy-load tab content
  if (tabName === 'reports') {
    loadReports();
  } else if (tabName === 'tokens') {
    loadTokenStats();
  }
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────

function init() {
  const shareToken = getShareToken();

  if (!shareToken) {
    document.getElementById('auth-screen').innerHTML = `
      <div class="auth-card">
        <h1 class="auth-title">Visual Patrol</h1>
        <p class="auth-error" style="display:block">Invalid link. Please use the share URL you were provided.</p>
      </div>
    `;
    return;
  }

  // Password submit
  document.getElementById('auth-submit').addEventListener('click', () => {
    const pwd = document.getElementById('password-input').value;
    authenticate(pwd);
  });

  // Enter key on password field
  document.getElementById('password-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') authenticate(e.target.value);
  });

  // Tab click handlers
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // History robot filter
  document.getElementById('history-robot-filter').addEventListener('change', loadHistory);

  // Token load button
  document.getElementById('tokens-load-btn').addEventListener('click', loadTokenStats);

  // Modal close
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('run-modal').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
  });
}

init();
