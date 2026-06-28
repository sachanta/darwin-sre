/* Darwin SRE Supervisor — replay UI */
'use strict';

// ── Constants ──────────────────────────────────────────────────────────────
const THRESHOLD   = 0.85;
const GEN_COLORS  = ['#58a6ff','#3fb950','#bc8cff','#ffa657','#ff7b72','#56d364','#a5d6ff','#f2cc60','#ff9bce'];
const API         = '';   // same origin

// ── State ──────────────────────────────────────────────────────────────────
let timeline      = [];
let playIndex     = 0;
let playTimer     = null;
let chart         = null;
let currentRunId  = null;

let chartScores   = [];   // composite score per incident
let chartLabels   = [];   // incident index labels
let chartColors   = [];   // point color per incident
let chartGen      = [];   // generation at time of resolution
let alertAnnots   = [];   // {x: idx, label} for alert lines
let evoAnnots     = [];   // {x: idx, label} for recovery markers

let incidentCount = 0;
let alertCount    = 0;
let currentGen    = 0;

// ── Boot ───────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  initChart();
  await loadRuns();
  document.getElementById('runSelect').addEventListener('change', onRunChange);
  document.getElementById('playBtn').addEventListener('click', onPlay);
  document.getElementById('resetBtn').addEventListener('click', onReset);
});

// ── Load runs into dropdown ────────────────────────────────────────────────
async function loadRuns() {
  try {
    const data = await api('/runs');
    const sel = document.getElementById('runSelect');
    if (!data.runs || !data.runs.length) {
      sel.innerHTML = '<option>No runs yet — run main.py first</option>';
      return;
    }
    data.runs.forEach((r, i) => {
      const opt = document.createElement('option');
      opt.value = r.run_id;
      const dt = r.started_at ? r.started_at.slice(0, 16).replace('T', ' ') : '—';
      const gen = r.num_generations ?? '?';
      opt.textContent = `${r.run_id.slice(0, 20)}  · ${dt} · ${gen} gen`;
      if (i === 0) opt.selected = true;
      sel.appendChild(opt);
    });
    await loadTimeline(data.runs[0].run_id);
  } catch (e) {
    setStatus('Error loading runs: ' + e.message, 'idle');
  }
}

async function onRunChange() {
  onReset();
  const runId = document.getElementById('runSelect').value;
  await loadTimeline(runId);
}

async function loadTimeline(runId) {
  currentRunId = runId;
  setStatus('Loading…', 'idle');
  try {
    const data = await api(`/runs/${runId}/timeline`);
    timeline = data.events || [];
    setStatus(`${timeline.length} events · press ▶ Play`, 'idle');
    document.getElementById('playBtn').disabled = false;
    document.getElementById('resetBtn').disabled = false;
  } catch (e) {
    setStatus('Error: ' + e.message, 'idle');
  }
}

// ── Playback ───────────────────────────────────────────────────────────────
function onPlay() {
  const btn = document.getElementById('playBtn');
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
    btn.textContent = '▶ Play';
    setStatus('Paused', 'idle');
    return;
  }
  if (playIndex >= timeline.length) { onReset(); return; }
  btn.textContent = '⏸ Pause';
  setStatus('Playing…', 'playing');
  const speed = parseInt(document.getElementById('speedSelect').value, 10);
  playTimer = setInterval(step, speed);
}

function step() {
  if (playIndex >= timeline.length) {
    clearInterval(playTimer);
    playTimer = null;
    document.getElementById('playBtn').textContent = '▶ Play';
    setStatus('Complete', 'done');
    return;
  }
  processEvent(timeline[playIndex++]);
}

function onReset() {
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
  playIndex = 0;
  incidentCount = 0; alertCount = 0; currentGen = 0;
  chartScores = []; chartLabels = []; chartColors = []; chartGen = [];
  alertAnnots = []; evoAnnots = [];
  resetChart();
  document.getElementById('ticker').innerHTML = '';
  document.getElementById('evoList').innerHTML = '<div class="evo-empty">Waiting for degradation…</div>';
  document.getElementById('evoCount').textContent = '0 / 8';
  document.getElementById('tickerStats').textContent = '0 incidents · 0 alerts';
  document.getElementById('chartStats').innerHTML = '<span>Avg: —</span><span>Gen: 0</span><span>Incidents: 0</span>';
  document.getElementById('playBtn').textContent = '▶ Play';
  setStatus(`${timeline.length} events · press ▶ Play`, 'idle');
}

// ── Event processing ───────────────────────────────────────────────────────
function processEvent(ev) {
  switch (ev.type) {
    case 'incident_resolved': onIncidentResolved(ev); break;
    case 'alert_raised':      onAlertRaised(ev);      break;
    case 'alert_resolved':    onAlertResolved(ev);    break;
    case 'darwin_complete':   onDarwinComplete(ev);   break;
  }
}

function onIncidentResolved(ev) {
  incidentCount++;
  const score = ev.scores?.composite ?? 0;
  currentGen = ev.generation ?? currentGen;

  chartScores.push(score);
  chartLabels.push(incidentCount);
  chartColors.push(GEN_COLORS[Math.min(currentGen, GEN_COLORS.length - 1)]);
  chartGen.push(currentGen);
  updateChart();

  // Ticker row
  const icon = score < 0.5 ? '🔴' : score < THRESHOLD ? '🟡' : '🟢';
  const scoreClass = score < 0.5 ? 'score-red' : score < THRESHOLD ? 'score-yellow' : 'score-green';
  const family = ev.incident?.edge_case_family || '';
  const row = el('div', 'tick');
  row.dataset.incidentId = ev.incident_id;
  row.innerHTML = `
    <span class="tick-icon">${icon}</span>
    <span class="tick-id">${ev.incident_id?.slice(0,12) ?? '—'}</span>
    <span class="tick-family">${family}</span>
    <span class="tick-score ${scoreClass}">${score.toFixed(2)}</span>
    <span class="tick-gen">g${currentGen}</span>
  `;
  row.addEventListener('click', () => openDrawer(ev.incident_id));
  appendTicker(row);

  // Update chart stats
  const avg = (chartScores.reduce((a, b) => a + b, 0) / chartScores.length).toFixed(3);
  document.getElementById('chartStats').innerHTML =
    `<span>Avg: <b>${avg}</b></span><span>Gen: <b>${currentGen}</b></span><span>Incidents: <b>${incidentCount}</b></span>`;
  document.getElementById('tickerStats').textContent = `${incidentCount} incidents · ${alertCount} alerts`;
}

function onAlertRaised(ev) {
  alertCount++;
  alertAnnots.push({ x: incidentCount, label: `⬥ Alert (avg=${ev.rolling_avg?.toFixed(2)})` });
  updateChart();
  const row = el('div', 'tick-alert');
  row.textContent = `⚠️  ALERT — rolling avg ${ev.rolling_avg?.toFixed(2)} < ${THRESHOLD} · Darwin firing…`;
  appendTicker(row);
  document.getElementById('tickerStats').textContent = `${incidentCount} incidents · ${alertCount} alerts`;
}

function onAlertResolved(ev) {
  const row = el('div', 'tick-evo');
  row.textContent = `✅  Alert resolved — Darwin gen ${ev.generation} · scores recovered`;
  appendTicker(row);
}

function onDarwinComplete(ev) {
  const improved = (ev.score_after ?? 0) > (ev.score_before ?? 0);
  evoAnnots.push({ x: incidentCount, label: `★ Gen ${ev.generation}` });
  updateChart();

  // Remove empty placeholder
  const evoList = document.getElementById('evoList');
  const empty = evoList.querySelector('.evo-empty');
  if (empty) empty.remove();

  const card = el('div', `evo-card ${improved ? 'improved' : 'no-improve'}`);
  const families = (ev.failure_patterns || []).join(', ') || '—';
  card.innerHTML = `
    <div class="evo-header">
      <span class="evo-gen">Gen ${ev.generation}</span>
      <span class="${improved ? 'evo-badge-fixed' : 'evo-badge-partial'}">${improved ? '✅ FIXED' : '⚠️ PARTIAL'}</span>
    </div>
    <div class="evo-score">
      <span class="before">${(ev.score_before ?? 0).toFixed(2)}</span>
      → <span class="after">${(ev.score_after ?? 0).toFixed(2)}</span>
      (+${((ev.score_after ?? 0) - (ev.score_before ?? 0)).toFixed(2)})
    </div>
    ${ev.prompt_diff ? `<div class="evo-skill">🎯 ${ev.prompt_diff.replace('[skill written] ', '')}</div>` : ''}
    ${ev.new_kb_article_id ? `<div class="evo-kb">📄 KB article written: ${ev.new_kb_article_id.slice(0, 24)}</div>` : ''}
    <div class="evo-families">Families: ${families}</div>
  `;
  evoList.prepend(card);

  const total = Math.max(ev.generation, parseInt(document.getElementById('evoCount').textContent) || 0);
  document.getElementById('evoCount').textContent = `${ev.generation} / 8`;

  const row = el('div', 'tick-evo');
  row.textContent = `🧬 Darwin gen ${ev.generation}: ${(ev.score_before ?? 0).toFixed(2)} → ${(ev.score_after ?? 0).toFixed(2)} ${improved ? '↑ FIXED' : '→ partial'}`;
  appendTicker(row);
}

// ── Chart.js setup ─────────────────────────────────────────────────────────
function initChart() {
  const ctx = document.getElementById('scoreChart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Composite Score',
        data: [],
        borderColor: GEN_COLORS[0],
        backgroundColor: 'transparent',
        pointBackgroundColor: [],
        pointRadius: 4,
        pointHoverRadius: 6,
        tension: 0.2,
        borderWidth: 1.5,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 200 },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const score = ctx.parsed.y.toFixed(3);
              const gen = chartGen[ctx.dataIndex] ?? 0;
              return [`Score: ${score}`, `Gen: ${gen}`];
            }
          }
        }
      },
      scales: {
        x: {
          title: { display: true, text: 'Incident #', color: '#8b949e' },
          ticks: { color: '#8b949e', maxTicksLimit: 20 },
          grid: { color: '#21262d' },
        },
        y: {
          min: 0, max: 1,
          title: { display: true, text: 'Composite Score', color: '#8b949e' },
          ticks: { color: '#8b949e' },
          grid: { color: '#21262d' },
        }
      }
    },
    plugins: [{
      id: 'thresholdLine',
      afterDraw(chart) {
        const { ctx, scales: { x, y } } = chart;
        const yPos = y.getPixelForValue(THRESHOLD);
        ctx.save();
        ctx.strokeStyle = 'rgba(248,81,73,.5)';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x.left, yPos);
        ctx.lineTo(x.right, yPos);
        ctx.stroke();
        ctx.fillStyle = '#f85149';
        ctx.font = '10px sans-serif';
        ctx.fillText(`threshold ${THRESHOLD}`, x.left + 4, yPos - 4);
        ctx.restore();

        // Alert markers — red vertical dashed line + ⬥ label
        alertAnnots.forEach(a => {
          if (a.x > chartScores.length) return;
          const xPos = x.getPixelForValue(a.x);
          ctx.save();
          ctx.strokeStyle = 'rgba(248,81,73,0.5)';
          ctx.setLineDash([3, 3]);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(xPos, y.top);
          ctx.lineTo(xPos, y.bottom);
          ctx.stroke();
          ctx.fillStyle = '#f85149';
          ctx.font = '11px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('⬥ alert', xPos, y.top + 12);
          ctx.restore();
        });

        // Darwin evolve markers — green vertical solid line + ★ Gen N label
        evoAnnots.forEach(a => {
          if (a.x > chartScores.length) return;
          const xPos = x.getPixelForValue(a.x);
          ctx.save();
          ctx.strokeStyle = 'rgba(63,185,80,0.7)';
          ctx.setLineDash([]);
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(xPos, y.top);
          ctx.lineTo(xPos, y.bottom);
          ctx.stroke();
          ctx.fillStyle = '#3fb950';
          ctx.font = 'bold 11px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(`★ ${a.label}`, xPos, y.top + 12);
          ctx.restore();
        });
      }
    }]
  });
}

function updateChart() {
  chart.data.labels = chartLabels;
  chart.data.datasets[0].data = chartScores;
  chart.data.datasets[0].pointBackgroundColor = chartColors;
  chart.data.datasets[0].borderColor = chartColors.length
    ? chartColors[chartColors.length - 1]
    : GEN_COLORS[0];
  chart.update('none');
}

function resetChart() {
  chart.data.labels = [];
  chart.data.datasets[0].data = [];
  chart.data.datasets[0].pointBackgroundColor = [];
  chart.update('none');
}

// ── Evidence Drawer ────────────────────────────────────────────────────────
async function openDrawer(incidentId) {
  if (!incidentId) return;
  document.getElementById('drawer').classList.remove('hidden');
  document.getElementById('drawerOverlay').classList.remove('hidden');
  document.getElementById('drawerTitle').textContent = incidentId;
  document.getElementById('drawerContent').innerHTML = '<div class="loading">Loading evidence…</div>';

  try {
    const data = await api(`/incidents/${incidentId}`);
    renderDrawer(data);
  } catch (e) {
    document.getElementById('drawerContent').innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

function renderDrawer(data) {
  const { incident, log, kb_articles, resolution } = data;
  const parts = [];

  // Scores
  if (resolution?.scores) {
    const s = resolution.scores;
    parts.push(`
      <div class="drawer-section">
        <div class="drawer-section-title">Judge Scores</div>
        <div class="score-grid">
          <div class="score-cell">
            <div class="val ${scoreColor(s.composite)}">${(s.composite ?? 0).toFixed(2)}</div>
            <div class="lbl">Composite</div>
          </div>
          <div class="score-cell">
            <div class="val ${scoreColor(s.root_cause_accuracy)}">${(s.root_cause_accuracy ?? 0).toFixed(2)}</div>
            <div class="lbl">Root Cause</div>
          </div>
          <div class="score-cell">
            <div class="val ${scoreColor(s.remediation_quality)}">${(s.remediation_quality ?? 0).toFixed(2)}</div>
            <div class="lbl">Remediation</div>
          </div>
          <div class="score-cell">
            <div class="val ${scoreColor(s.severity_accuracy)}">${(s.severity_accuracy ?? 0).toFixed(2)}</div>
            <div class="lbl">Severity</div>
          </div>
        </div>
      </div>
    `);
  }

  // Incident
  if (incident) {
    parts.push(`
      <div class="drawer-section">
        <div class="drawer-section-title">Incident</div>
        <div class="resolution-block">
          <div class="res-field"><span class="res-label">Title</span><span class="res-value">${esc(incident.title)}</span></div>
          <div class="res-field"><span class="res-label">Service</span><span class="res-value">${esc(incident.service)}</span></div>
          <div class="res-field"><span class="res-label">Category</span><span class="res-value">${esc(incident.category)}</span></div>
          ${incident.edge_case_family ? `<div class="res-field"><span class="res-label">Family</span><span class="res-value" style="color:var(--purple)">${esc(incident.edge_case_family)}</span></div>` : ''}
        </div>
      </div>
    `);
  }

  // Log
  if (log?.lines?.length) {
    const lines = log.lines.slice(0, 10).map(l =>
      `<div class="log-line"><span class="ts">${(l.ts || '').slice(11, 19)}</span> <span class="lvl-${l.level}">${l.level}</span> ${esc(l.msg)}</div>`
    ).join('');
    parts.push(`
      <div class="drawer-section">
        <div class="drawer-section-title">Recent Logs (${log.lines.length} lines)</div>
        <div class="log-block">${lines}</div>
      </div>
    `);
  }

  // KB articles
  if (kb_articles?.length) {
    const cards = kb_articles.map(a => {
      const isDarwin = a.source === 'darwin';
      const sim = a.similarity != null ? `<span class="kb-sim">${(a.similarity * 100).toFixed(0)}% match</span>` : '';
      return `
        <div class="kb-card ${isDarwin ? 'darwin-kb' : ''}">
          <div class="kb-title">${esc(a.title)}${sim}</div>
          <div class="kb-body">${esc(a.body?.slice(0, 200))}${(a.body?.length > 200) ? '…' : ''}</div>
        </div>
      `;
    }).join('');
    parts.push(`
      <div class="drawer-section">
        <div class="drawer-section-title">Retrieved Runbooks (${kb_articles.length})</div>
        ${cards}
      </div>
    `);
  }

  // Resolution
  if (resolution?.resolution) {
    const r = resolution.resolution;
    const steps = (r.remediation_steps || []).map(s => `<li>${esc(s)}</li>`).join('');
    parts.push(`
      <div class="drawer-section">
        <div class="drawer-section-title">Agent Resolution</div>
        <div class="resolution-block">
          <div class="res-field"><span class="res-label">Severity</span><span class="severity-${r.severity}">${r.severity}</span></div>
          <div class="res-field"><span class="res-label">Root Cause</span><span class="res-value">${esc(r.root_cause)}</span></div>
          <div class="res-field"><span class="res-label">Confidence</span><span class="res-value">${esc(r.confidence)}</span></div>
          <div class="res-field"><span class="res-label">Steps</span>
            <ul class="remediation-list">${steps}</ul>
          </div>
        </div>
      </div>
    `);
  }

  document.getElementById('drawerContent').innerHTML = parts.join('') || '<div class="loading">No evidence found.</div>';
}

function closeDrawer() {
  document.getElementById('drawer').classList.add('hidden');
  document.getElementById('drawerOverlay').classList.add('hidden');
}

// ── Utilities ──────────────────────────────────────────────────────────────
async function api(path) {
  const resp = await fetch(API + path);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function appendTicker(row) {
  const ticker = document.getElementById('ticker');
  ticker.appendChild(row);
  ticker.scrollTop = ticker.scrollHeight;
}

function esc(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function scoreColor(v) {
  if (!v && v !== 0) return '';
  return v < 0.5 ? 'score-red' : v < THRESHOLD ? 'score-yellow' : 'score-green';
}

function setStatus(msg, state) {
  const el = document.getElementById('statusText');
  el.textContent = msg;
  el.className = `status-${state}`;
}
