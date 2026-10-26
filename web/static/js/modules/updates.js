// Updates page — read-only observe of node drift (OS/kernel/k3s) and pod drift
// (ArgoCD). No mutation happens here: the "check" action runs a read-only
// Ansible drift play; ArgoCD is observed only.

import { showToast, appendLogLine } from '../utils/dom.js';
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
        body.innerHTML = '<tr><td colspan="4" style="opacity:.6">Set ARGOCD_SERVER / ARGOCD_AUTH_TOKEN to enable.</td></tr>';
        return;
    }
    if (!block.ok) {
        if (meta) meta.textContent = 'ArgoCD: error';
        if (banner) banner.innerHTML = `<p class="q-summary-line"><span class="q-badge q-badge-red">ArgoCD unreachable</span> ${esc(block.error || '')}</p>`;
        body.innerHTML = '<tr><td colspan="4" style="opacity:.6">—</td></tr>';
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
            </tr>`).join('')
        : '<tr><td colspan="4" style="opacity:.6">No applications.</td></tr>';
}

export function loadUpdatesOverview() {
    fetch('/api/updates/overview')
        .then(r => r.json())
        .then(data => {
            renderNodes({ nodes: data.nodes, last_checked: data.nodes_last_checked });
            renderArgo(data.argocd || { configured: false });
        })
        .catch(err => showToast(`Updates load failed: ${err.message}`));
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
