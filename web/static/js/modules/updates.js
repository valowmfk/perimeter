// Updates page — read-only observe of node drift (OS/kernel/k3s) and pod drift
// (ArgoCD). No mutation happens here: the "check" action runs a read-only
// Ansible drift play; ArgoCD is observed only.

import { showToast, appendLogLine, setRailAttention } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
    ));
}

function syncBadge(status) {
    const map = { 'Synced': 'q-badge-green', 'OutOfSync': 'q-badge-yellow', 'Unknown': 'q-badge-grey' };
    return `<span class="q-badge ${map[status] || 'q-badge-orange'}">${esc(status || 'Unknown')}</span>`;
}

function healthBadge(status) {
    const map = {
        'Healthy': 'q-badge-green', 'Progressing': 'q-badge-yellow',
        'Degraded': 'q-badge-red', 'Missing': 'q-badge-red',
        'Suspended': 'q-badge-grey', 'Unknown': 'q-badge-grey'
    };
    return `<span class="q-badge ${map[status] || 'q-badge-grey'}">${esc(status || 'Unknown')}</span>`;
}

function pendingBadge(n) {
    const count = parseInt(n, 10) || 0;
    return count === 0
        ? '<span class="q-badge q-badge-green">up to date</span>'
        : `<span class="q-badge q-badge-orange">${count} pending</span>`;
}

function k3sShort(raw) {
    if (!raw) return '—';
    const m = String(raw).match(/v\d[\w.+-]*/);
    return m ? m[0] : '—';
}

function renderNodes(status) {
    const body = document.getElementById('updatesNodeBody');
    const meta = document.getElementById('updatesNodeMeta');
    if (!body) return;

    const nodes = status.nodes || [];
    if (!nodes.length) {
        body.innerHTML = '<tr><td colspan="5" style="opacity:.6">No data yet — run a drift check.</td></tr>';
    } else {
        body.innerHTML = nodes.map(n => `
            <tr>
                <td>${esc(n.host)}</td>
                <td>${esc(n.os)}</td>
                <td>${esc(n.kernel)}</td>
                <td>${esc(k3sShort(n.k3s_version))}</td>
                <td>${pendingBadge(n.pending_updates)}</td>
            </tr>`).join('');
    }
    if (meta) {
        const when = status.last_checked ? new Date(status.last_checked).toLocaleString() : 'never';
        meta.textContent = `Last checked: ${when}${nodes.length ? ` · ${nodes.length} host(s)` : ''}`;
    }
}

function renderArgo(block) {
    const body = document.getElementById('updatesArgoBody');
    const meta = document.getElementById('updatesArgoMeta');
    const banner = document.getElementById('updatesArgoBanner');
    if (!body) return;

    if (banner) banner.innerHTML = '';

    if (!block.configured) {
        if (meta) meta.textContent = 'ArgoCD: not configured';
        body.innerHTML = '<tr><td colspan="5" style="opacity:.6">Set ARGOCD_SERVER / ARGOCD_AUTH_TOKEN to enable.</td></tr>';
        return;
    }
    if (!block.ok) {
        if (meta) meta.textContent = 'ArgoCD: error';
        if (banner) banner.innerHTML = `<p class="q-summary-line"><span class="q-badge q-badge-red">ArgoCD unreachable</span> ${esc(block.error || '')}</p>`;
        body.innerHTML = '<tr><td colspan="5" style="opacity:.6">—</td></tr>';
        return;
    }

    const apps = block.applications || [];
    const s = block.summary || {};
    if (meta) {
        meta.textContent = `ArgoCD: ${s.total || 0} app(s) · ${s.out_of_sync || 0} out-of-sync · ${s.unhealthy || 0} unhealthy`;
    }
    body.innerHTML = apps.length
        ? apps.map(a => `
            <tr>
                <td>${esc(a.name)}</td>
                <td>${syncBadge(a.sync_status)}</td>
                <td>${healthBadge(a.health_status)}</td>
                <td><code>${esc(a.revision_current || '—')}</code> → ${esc(a.revision_target || '—')}</td>
                <td><button type="button" class="q-mini-btn" data-action="sync-argocd-app" data-app="${esc(a.name)}" title="Trigger ArgoCD sync">Sync</button></td>
            </tr>`).join('')
        : '<tr><td colspan="5" style="opacity:.6">No applications.</td></tr>';
}

function tierBadge(tiers) {
    if (!tiers || !tiers.length) return '<span class="q-badge q-badge-grey">renovate</span>';
    const cls = {
        'tier-a': 'q-badge-green', 'tier-b': 'q-badge-yellow', 'tier-c': 'q-badge-red',
        'staged-manual': 'q-badge-red', 'major': 'q-badge-orange', 'needs-review': 'q-badge-yellow'
    };
    return tiers.map(t => `<span class="q-badge ${cls[t] || 'q-badge-grey'}">${esc(t)}</span>`).join(' ');
}

function renderRenovate(block) {
    const body = document.getElementById('updatesRenovateBody');
    const meta = document.getElementById('updatesRenovateMeta');
    const banner = document.getElementById('updatesRenovateBanner');
    const dash = document.getElementById('updatesRenovateDash');
    if (!body) return;
    if (banner) banner.innerHTML = '';
    if (dash) dash.hidden = true;
    block = block || { configured: false };

    if (!block.configured) {
        if (meta) meta.textContent = 'Renovate: not configured';
        body.innerHTML = '<tr><td colspan="4" style="opacity:.6">Set PERIMETER_GITHUB_TOKEN to enable, once Renovate is running.</td></tr>';
        return;
    }
    if (!block.ok) {
        if (meta) meta.textContent = 'Renovate: error';
        if (banner) banner.innerHTML = `<p class="q-summary-line"><span class="q-badge q-badge-red">GitHub read failed</span> ${esc(block.error || '')}</p>`;
        body.innerHTML = '<tr><td colspan="4" style="opacity:.6">—</td></tr>';
        return;
    }

    if (dash && block.dashboard && block.dashboard.url) { dash.hidden = false; dash.href = block.dashboard.url; }
    const prs = block.prs || [];
    if (meta) meta.textContent = `Renovate: ${prs.length} open PR(s)` + (block.dashboard ? ' · dashboard ⬈' : '');
    body.innerHTML = prs.length
        ? prs.map(renovateRow).join('')
        : '<tr><td colspan="4" style="opacity:.6">No open Renovate PRs.</td></tr>';
}

// One bundled dependency → a compact chip: "name type current→new".
function renovateDepChip(u) {
    const label = [u.name, u.type].filter(Boolean).join(' ');
    const ver = u.new ? (u.current ? `${u.current} → ${u.new}` : u.new) : (u.update || '');
    const title = [u.name, u.type && `(${u.type})`, u.current && u.new && `${u.current} → ${u.new}`]
        .filter(Boolean).join(' ');
    return `<span class="renovate-dep" title="${esc(title)}">${esc(label)}`
        + (ver ? ` <span class="renovate-dep-ver">${esc(ver)}</span>` : '')
        + `</span>`;
}

// A PR row. Grouped PRs (>1 parsed dep) get a muted sub-row listing what they
// bundle; single-dep PRs render exactly as before.
function renovateRow(p) {
    const ups = p.updates || [];
    const grouped = ups.length > 1;
    const countChip = grouped ? ` <span class="q-badge q-badge-grey">${ups.length} deps</span>` : '';
    const main = `
            <tr>
                <td><a href="${esc(p.url)}" target="_blank" rel="noopener">#${esc(p.number)}</a></td>
                <td>${esc(p.title)}${countChip}</td>
                <td>${tierBadge(p.tiers)}</td>
                <td><a class="q-mini-btn" href="${esc(p.url)}" target="_blank" rel="noopener" title="Review PR">Review ⬈</a></td>
            </tr>`;
    if (!grouped) return main;
    const sub = `
            <tr class="renovate-sub">
                <td></td>
                <td colspan="3"><div class="renovate-deps">${ups.map(renovateDepChip).join('')}</div></td>
            </tr>`;
    return main + sub;
}

export function refreshRenovate() {
    fetch('/api/updates/renovate?refresh=1')
        .then(r => r.json())
        .then(renderRenovate)
        .catch(err => showToast(`Renovate refresh failed: ${err.message}`));
}

export function syncArgoApp(el) {
    const app = el && el.dataset ? el.dataset.app : '';
    if (!app) return;
    if (!window.confirm(`Trigger ArgoCD sync for "${app}"?\nThis reconciles the app to its git state.`)) return;
    showToast(`Syncing ${app}…`);
    fetch('/api/updates/argocd/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app })
    })
    .then(resp => {
        if (!resp.ok) return resp.json().then(e => { throw new Error(e.error || `HTTP ${resp.status}`); });
        return resp.json();
    })
    .then(() => { showToast(`Sync triggered for ${app}`); setTimeout(refreshArgocd, 1500); })
    .catch(err => showToast(`Sync failed: ${err.message}`));
}

export function loadUpdatesOverview() {
    fetch('/api/updates/overview')
        .then(r => r.json())
        .then(data => {
            renderNodes({ nodes: data.nodes, last_checked: data.nodes_last_checked });
            renderArgo(data.argocd || { configured: false });
            renderRenovate(data.renovate || { configured: false });

            // Roll-up onto the rail Updates icon: err if any app unhealthy,
            // warn if nodes have pending updates / apps out-of-sync / open PRs.
            let level = 'ok';
            const nodes = data.nodes || [];
            const argo = data.argocd || {};
            const s = argo.summary || {};
            const prs = (data.renovate && data.renovate.prs) ? data.renovate.prs.length : 0;
            if (argo.ok && Number(s.unhealthy) > 0) level = 'err';
            else if (nodes.some(n => Number(n.pending_updates) > 0) ||
                     (argo.ok && Number(s.out_of_sync) > 0) || prs > 0) level = 'warn';
            setRailAttention('toggle-updates', level);
        })
        .catch(err => showToast(`Updates load failed: ${err.message}`));
    loadRunHistory();
    syncRunControls();
    loadSchedules();
}

// ── Schedules (node patch windows) ──────────────────────────

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function schedWhen(s) {
    const t = `${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`;
    return s.frequency === 'weekly'
        ? `Weekly · ${DOW[s.day_of_week] || '?'} ${t}`
        : `Daily · ${t}`;
}

function renderSchedules(schedules) {
    const body = document.getElementById('updatesSchedBody');
    if (!body) return;
    if (!schedules.length) {
        body.innerHTML = '<tr><td colspan="6" style="opacity:.6">No schedules.</td></tr>';
        return;
    }
    body.innerHTML = schedules.map(s => {
        const next = s.enabled && s.next_run ? new Date(s.next_run).toLocaleString() : '—';
        const onBadge = s.enabled
            ? '<span class="q-badge q-badge-green">on</span>'
            : '<span class="q-badge q-badge-grey">off</span>';
        return `
            <tr>
                <td>${esc(s.name)}</td>
                <td>${esc(schedWhen(s))}</td>
                <td>${esc(s.group_name)}</td>
                <td>${esc(next)}</td>
                <td><button type="button" class="q-mini-btn" data-action="toggle-schedule" data-id="${s.id}" data-enabled="${s.enabled ? 1 : 0}" title="Toggle">${onBadge}</button></td>
                <td><button type="button" class="q-mini-btn" data-action="delete-schedule" data-id="${s.id}" title="Delete">✕</button></td>
            </tr>`;
    }).join('');
}

export function loadSchedules() {
    fetch('/api/updates/schedules')
        .then(r => r.json())
        .then(d => renderSchedules(d.schedules || []))
        .catch(() => { /* best-effort */ });
}

export function onSchedFreqChange() {
    const freq = document.getElementById('schedFreq')?.value;
    const wrap = document.getElementById('schedDowWrap');
    if (wrap) wrap.hidden = freq !== 'weekly';
}

export function createSchedule() {
    const name = document.getElementById('schedName')?.value.trim();
    const group = document.getElementById('schedGroup')?.value;
    const frequency = document.getElementById('schedFreq')?.value;
    const hour = parseInt(document.getElementById('schedHour')?.value, 10);
    const minute = parseInt(document.getElementById('schedMin')?.value, 10);
    const day_of_week = parseInt(document.getElementById('schedDow')?.value, 10);
    const allow_reboot = document.getElementById('schedReboot')?.checked ?? true;
    const statusEl = document.getElementById('schedStatus');

    if (!name) { showToast('Give the schedule a name'); return; }
    const body = { name, group, frequency, hour, minute, allow_reboot };
    if (frequency === 'weekly') body.day_of_week = day_of_week;

    const done = withBusy('create-schedule');
    fetch('/api/updates/schedules', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(resp => { if (!resp.ok) return resp.json().then(e => { throw new Error(e.error || `HTTP ${resp.status}`); }); return resp.json(); })
    .then(() => {
        showToast('Schedule added');
        const n = document.getElementById('schedName'); if (n) n.value = '';
        if (statusEl) statusEl.textContent = '';
        loadSchedules();
    })
    .catch(err => { if (statusEl) statusEl.textContent = ''; showToast(`Add failed: ${err.message}`); })
    .finally(() => done());
}

export function toggleSchedule(el) {
    const id = el?.dataset?.id;
    if (!id) return;
    const enabled = el.dataset.enabled === '1';
    fetch(`/api/updates/schedules/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !enabled })
    })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(() => loadSchedules())
    .catch(err => showToast(`Toggle failed: ${err.message}`));
}

export function deleteSchedule(el) {
    const id = el?.dataset?.id;
    if (!id) return;
    if (!window.confirm('Delete this schedule?')) return;
    fetch(`/api/updates/schedules/${id}`, { method: 'DELETE' })
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(() => { showToast('Schedule deleted'); loadSchedules(); })
    .catch(err => showToast(`Delete failed: ${err.message}`));
}

// ── Node execution (patch) + run history ────────────────────

function runStatusBadge(status) {
    const map = { 'running': 'q-badge-yellow', 'success': 'q-badge-green',
                  'failed': 'q-badge-red', 'error': 'q-badge-red' };
    return `<span class="q-badge ${map[status] || 'q-badge-grey'}">${esc(status || '?')}</span>`;
}

function rebootBadge(r) {
    if (r === 1 || r === true) return '<span class="q-badge q-badge-orange">rebooted</span>';
    if (r === 0 || r === false) return '<span class="q-badge q-badge-grey">no</span>';
    return '<span style="opacity:.5">—</span>';
}

function fmtWhen(started, ended) {
    if (!started) return '—';
    const s = new Date(started).toLocaleString();
    return ended ? s : `${s} …`;
}

function renderRuns(runs) {
    const body = document.getElementById('updatesRunsBody');
    const meta = document.getElementById('updatesRunsMeta');
    if (!body) return;
    if (!runs.length) {
        body.innerHTML = '<tr><td colspan="5" style="opacity:.6">No runs yet.</td></tr>';
    } else {
        body.innerHTML = runs.map(r => `
            <tr>
                <td>${esc(fmtWhen(r.started_at, r.ended_at))}</td>
                <td>${esc(r.group_name || '—')}</td>
                <td>${esc((r.playbook || '').replace(/\.ya?ml$/, ''))}</td>
                <td>${runStatusBadge(r.status)}</td>
                <td>${rebootBadge(r.reboot)}</td>
            </tr>`).join('');
    }
    if (meta) meta.textContent = runs.length ? `${runs.length} run(s)` : 'No runs yet';
}

export function loadRunHistory() {
    fetch('/api/updates/nodes/runs')
        .then(r => r.json())
        .then(d => renderRuns(d.runs || []))
        .catch(() => { /* history is best-effort */ });
}

export function refreshRuns() {
    loadRunHistory();
}

function setRunDisabled(disabled, label) {
    const btn = document.querySelector('[data-action="run-node-update"]');
    if (!btn) return;
    btn.disabled = disabled;
    btn.style.opacity = disabled ? '0.5' : '';
    if (label) btn.textContent = label;
}

// Toggle the k3s-only drain field + reflect whether the selected group is
// already running an update (lock held elsewhere).
export function syncRunControls() {
    const group = (document.getElementById('updatesGroupSelect') || {}).value || '';
    const drainWrap = document.getElementById('updatesDrainWrap');
    if (drainWrap) drainWrap.hidden = group !== 'k8s_linux';

    fetch('/api/updates/nodes/running')
        .then(r => r.json())
        .then(d => {
            const running = (d.running_groups || []).includes(group);
            const statusEl = document.getElementById('updatesRunStatus');
            setRunDisabled(running, running ? 'UPDATE RUNNING…' : '⚠ RUN UPDATES');
            if (statusEl) statusEl.textContent = running ? `A run is in progress for ${group}.` : '';
        })
        .catch(() => { /* non-fatal */ });
}

export function onUpdatesGroupChange() {
    syncRunControls();
}

export function runNodeUpdate() {
    const group = (document.getElementById('updatesGroupSelect') || {}).value || '';
    if (!group) { showToast('Select an inventory group first'); return; }

    const allowReboot = document.getElementById('updatesAllowReboot')?.checked ?? true;
    const minUptime = parseInt(document.getElementById('updatesMinUptime')?.value, 10);
    const drainTimeout = parseInt(document.getElementById('updatesDrainTimeout')?.value, 10);
    const isK3s = group === 'k8s_linux';

    const msg = `Run updates on "${group}"?\n\nThis PATCHES` +
        (allowReboot ? ' and may REBOOT' : '') + ` those hosts.` +
        (isK3s ? '\nk3s nodes drain one at a time (serial:1).' : '');
    if (!window.confirm(msg)) return;

    const body = { group, allow_reboot: allowReboot };
    if (!Number.isNaN(minUptime)) body.min_uptime_minutes = minUptime;
    if (isK3s && !Number.isNaN(drainTimeout)) body.drain_timeout_seconds = drainTimeout;

    const done = withBusy('run-node-update');
    const statusEl = document.getElementById('updatesRunStatus');
    if (statusEl) statusEl.textContent = `Starting update on ${group}…`;
    appendLogLine(`[updates] node update: ${group} (${isK3s ? 'k3s-updates' : 'linux-updates'})`);

    fetch('/api/updates/nodes/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(resp => {
        if (resp.status === 409) { throw new Error('A node update is already running for this group'); }
        if (!resp.ok) return resp.json().then(e => { throw new Error(e.error || `HTTP ${resp.status}`); });
        return resp.json();
    })
    .then(data => {
        const es = new EventSource(data.stream_url);
        es.onmessage = (event) => {
            if (event.data === '__COMPLETE__') {
                es.close();
                done();
                if (statusEl) statusEl.textContent = 'Finalizing (drift re-check)…';
                showToast('Update finished — recording result');
                loadUpdatesOverview();                       // refresh drift
                // kernel-after + status land after the post-run drift check; poll.
                [2000, 6000, 12000].forEach(t => setTimeout(loadRunHistory, t));
                setTimeout(syncRunControls, 2000);
            } else {
                appendLogLine(event.data);
            }
        };
        es.onerror = () => {
            es.close(); done();
            if (statusEl) statusEl.textContent = '';
            showToast('Error streaming update output');
            syncRunControls();
        };
    })
    .catch(err => {
        done();
        if (statusEl) statusEl.textContent = '';
        showToast(`Update failed: ${err.message}`);
        appendLogLine(`ERROR: ${err.message}`);
        syncRunControls();
    });
}

export function refreshArgocd() {
    const done = withBusy('refresh-argocd');
    fetch('/api/updates/argocd?refresh=1')
        .then(r => r.json())
        .then(block => renderArgo(block))
        .catch(err => showToast(`ArgoCD refresh failed: ${err.message}`))
        .finally(() => done());
}

export function checkNodes() {
    const groupEl = document.getElementById('updatesGroupSelect');
    const group = groupEl ? groupEl.value : '';
    const statusEl = document.getElementById('updatesCheckStatus');
    const done = withBusy('check-nodes');

    if (statusEl) statusEl.textContent = `Checking ${group || 'hosts'}…`;
    appendLogLine(`[updates] drift check: ${group || 'all'}`);

    fetch('/api/updates/nodes/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group })
    })
    .then(resp => {
        if (!resp.ok) return resp.json().then(e => { throw new Error(e.error || `HTTP ${resp.status}`); });
        return resp.json();
    })
    .then(data => {
        const es = new EventSource(data.stream_url);
        es.onmessage = (event) => {
            if (event.data === '__COMPLETE__') {
                es.close();
                done();
                if (statusEl) statusEl.textContent = '';
                loadUpdatesOverview();      // refresh drift table from fresh fragments
                showToast('Drift check complete');
            } else {
                appendLogLine(event.data);
            }
        };
        es.onerror = () => {
            es.close();
            done();
            if (statusEl) statusEl.textContent = '';
            showToast('Error streaming drift check');
        };
    })
    .catch(err => {
        done();
        if (statusEl) statusEl.textContent = '';
        showToast(`Check failed: ${err.message}`);
        appendLogLine(`ERROR: ${err.message}`);
    });
}
