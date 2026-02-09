// reports.js — Multi-run report generation, list, collapsible cards, PDF save
import state, { escapeHtml } from './state.js';

export function initReports() {
    const btn = document.getElementById('btn-generate-report');
    if (btn) btn.addEventListener('click', generateReport);

    // Default date range: last 7 days
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 7);

    const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

    const startInput = document.getElementById('report-start-date');
    const endInput = document.getElementById('report-end-date');
    if (startInput) startInput.value = fmt(start);
    if (endInput) endInput.value = fmt(end);

    window.toggleReportCard = toggleReportCard;
    window.saveReportPDF = saveReportPDF;
}

export async function loadReports() {
    const list = document.getElementById('reports-list');
    if (!list) return;

    list.innerHTML = '<div style="color:#666; text-align:center;">Loading reports...</div>';

    try {
        const res = await fetch('/api/reports');
        const reports = await res.json();

        list.innerHTML = '';

        if (reports.length === 0) {
            list.innerHTML = '<div style="color:#666; text-align:center; padding: 24px;">No reports generated yet.</div>';
            return;
        }

        reports.forEach(r => {
            const card = document.createElement('div');
            card.className = 'glass-panel';
            card.style.marginBottom = '12px';
            card.style.overflow = 'hidden';

            const header = document.createElement('div');
            header.style.cssText = 'display:flex; justify-content:space-between; align-items:center; padding:14px 16px; cursor:pointer; border-bottom:1px solid var(--border-subtle);';
            header.onclick = () => toggleReportCard(r.id);

            const dateRange = `${escapeHtml(r.start_date)} ~ ${escapeHtml(r.end_date)}`;
            const timestamp = r.timestamp ? escapeHtml(r.timestamp) : '';

            header.innerHTML = `
                <div>
                    <span style="font-weight:bold; color:var(--cyan-glow); font-size:1rem;">${dateRange}</span>
                    <span style="margin-left:12px; font-size:0.8rem; color:var(--text-muted);">${timestamp}</span>
                </div>
                <div style="display:flex; align-items:center; gap:12px;">
                    <span style="font-size:0.75rem; color:var(--text-muted);">
                        In: ${r.input_tokens || 0} / Out: ${r.output_tokens || 0} / Total: <b style="color:var(--cyan-dim);">${r.total_tokens || 0}</b>
                    </span>
                    <span class="toggle-icon" id="report-toggle-${r.id}">▶</span>
                </div>
            `;

            const body = document.createElement('div');
            body.id = `report-body-${r.id}`;
            body.style.display = 'none';
            body.style.padding = '16px';

            const content = r.report_content || '';
            body.innerHTML = `
                <div class="markdown-content" style="max-height:400px; overflow-y:auto; color:var(--text-primary); margin-bottom:12px;">
                    ${content ? marked.parse(content) : '<span style="color:var(--text-muted);">No content.</span>'}
                </div>
                <button onclick="saveReportPDF('${escapeHtml(r.start_date)}', '${escapeHtml(r.end_date)}')" class="btn-primary" style="padding:6px 14px; font-size:11px;">
                    Save PDF
                </button>
            `;

            card.appendChild(header);
            card.appendChild(body);
            list.appendChild(card);
        });
    } catch (e) {
        list.innerHTML = `<div style="color:#dc3545; text-align:center;">Error loading reports: ${escapeHtml(String(e))}</div>`;
    }
}

function toggleReportCard(id) {
    const body = document.getElementById(`report-body-${id}`);
    const icon = document.getElementById(`report-toggle-${id}`);
    if (!body) return;
    const isHidden = body.style.display === 'none';
    body.style.display = isHidden ? 'block' : 'none';
    if (icon) icon.textContent = isHidden ? '▼' : '▶';
}

function saveReportPDF(startDate, endDate) {
    if (!startDate || !endDate) {
        alert('No date range for this report.');
        return;
    }
    window.location.href = `/api/reports/generate/pdf?start_date=${startDate}&end_date=${endDate}`;
}

async function generateReport() {
    const startInput = document.getElementById('report-start-date');
    const endInput = document.getElementById('report-end-date');
    const btn = document.getElementById('btn-generate-report');

    if (!startInput.value || !endInput.value) {
        alert('Please select a date range.');
        return;
    }

    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="icon">&#x23F3;</span> Generating...';

    try {
        const res = await fetch('/api/reports/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_date: startInput.value,
                end_date: endInput.value
            })
        });

        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Failed to generate report');
        }

        // Refresh the list to show the new report
        await loadReports();
    } catch (e) {
        alert('Error: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}
