// Proxmox cluster flyout — template management, node status

import { showToast, appendLogLine, togglePanel } from '../utils/dom.js';


export function toggleProxmoxFlyout() {
    togglePanel('proxmoxFlyout', {
        chevronId: 'proxmoxChevron',
        onOpen: () => { loadNodeStatus(); loadTemplates(); },
    });
}

export function closeProxmoxFlyoutOnOutsideClick(e) {
    const flyout = document.getElementById('proxmoxFlyout');
    const toggle = document.querySelector('[data-action="toggle-proxmox-flyout"]');
    if (flyout && flyout.style.display !== 'none' &&
        !flyout.contains(e.target) &&
        (!toggle || !toggle.contains(e.target))) {
        flyout.style.display = 'none';
        const chevron = document.getElementById('proxmoxChevron');
        if (chevron) chevron.textContent = '\u25BE';
    }
}

function formatUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return `${days}d ${hours}h`;
}

function formatBytes(bytes) {
    const gb = bytes / (1024 * 1024 * 1024);
    return `${gb.toFixed(1)} GB`;
}

function loadNodeStatus() {
    const el = document.getElementById('proxmoxNodeStatus');
    fetch(`/api/proxmox/node-status?node=${document.body.dataset.pmNode || 'pve'}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) { el.textContent = data.error; return; }
            const memPct = data.memory_total ? Math.round(data.memory_used / data.memory_total * 100) : 0;
            const diskPct = data.rootfs_total ? Math.round(data.rootfs_used / data.rootfs_total * 100) : 0;
            el.innerHTML = `
                <div style="display:flex; gap:16px; flex-wrap:wrap;">
                    <span>Uptime: <strong>${formatUptime(data.uptime)}</strong></span>
                    <span>CPU: <strong>${data.cpu}%</strong></span>
                    <span>RAM: <strong>${formatBytes(data.memory_used)} / ${formatBytes(data.memory_total)}</strong> (${memPct}%)</span>
                    <span>Disk: <strong>${formatBytes(data.rootfs_used)} / ${formatBytes(data.rootfs_total)}</strong> (${diskPct}%)</span>
                </div>
            `;
        })
        .catch(() => { el.textContent = 'Failed to load node status'; });
}

export function loadTemplates() {
    const tbody = document.getElementById('proxmoxTemplateBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#64748b;">Loading...</td></tr>';

    fetch(`/api/proxmox/templates?node=${document.body.dataset.pmNode || 'pve'}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) { tbody.innerHTML = `<tr><td colspan="5">${data.error}</td></tr>`; return; }
            tbody.innerHTML = '';
            (data.templates || []).forEach(tpl => {
                const tr = document.createElement('tr');
                const typeLabel = tpl.refreshable ? 'Linux' : 'Vendor';
                const typeColor = tpl.refreshable ? '#10b981' : '#64748b';
                const lastUpdated = tpl.last_refreshed
                    ? new Date(tpl.last_refreshed * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                    : '—';
                const actionCell = tpl.refreshable
                    ? `<button class="q-button" style="font-size:11px; padding:2px 10px; background:#0d9488; color:white;"
                         data-action="template-refresh" data-template="${tpl.name}">Refresh</button>`
                    : '<span style="color:#64748b;">—</span>';
                tr.innerHTML = `
                    <td>${tpl.name}</td>
                    <td>${tpl.vmid}</td>
                    <td><span style="color:${typeColor}; font-size:11px;">${typeLabel}</span></td>
                    <td style="font-size:11px; color:#94a3b8;">${lastUpdated}</td>
                    <td>${actionCell}</td>
                `;
                tbody.appendChild(tr);
            });
        })
        .catch(() => { tbody.innerHTML = '<tr><td colspan="5">Failed to load templates</td></tr>'; });
}

export function refreshTemplate(templateName) {
    if (!confirm(`Refresh template "${templateName}"?\n\nThis will clone, update, clean, and re-template. Progress will appear in the live console.`)) {
        return;
    }

    // Disable the button
    const btn = document.querySelector(`[data-action="template-refresh"][data-template="${templateName}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Refreshing...';
        btn.style.background = '#64748b';
    }

    appendLogLine(`[INFO] Starting template refresh for ${templateName}...`);

    fetch('/api/template/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: templateName, node: document.body.dataset.pmNode || 'pve' })
    }).then(r => r.json()).then(data => {
        if (data.error) {
            showToast(data.error);
            if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; btn.style.background = '#0d9488'; }
            return;
        }

        const sessionId = data.session_id;
        // Stream via fetch ReadableStream (more reliable than EventSource through reverse proxies)
        fetch(`/api/template/refresh/stream/${sessionId}`)
            .then(response => {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                function read() {
                    reader.read().then(({ done, value }) => {
                        if (done) {
                            appendLogLine(`[SUCCESS] Template refresh for ${templateName} complete`);
                            if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; btn.style.background = '#0d9488'; }
                            loadTemplates();
                            return;
                        }
                        const text = decoder.decode(value, { stream: true });
                        const lines = text.split('\n');
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                const msg = line.slice(6);
                                if (msg === '__COMPLETE__') {
                                    appendLogLine(`[SUCCESS] Template refresh for ${templateName} complete`);
                                    if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; btn.style.background = '#0d9488'; }
                                    loadTemplates();
                                    reader.cancel();
                                    return;
                                }
                                appendLogLine(msg);
                            }
                        }
                        read();
                    });
                }
                read();
            })
            .catch(err => {
                appendLogLine(`[ERROR] Stream error: ${err.message}`);
                if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; btn.style.background = '#0d9488'; }
            });
    }).catch(err => {
        showToast(`Error: ${err.message}`);
        if (btn) { btn.disabled = false; btn.textContent = 'Refresh'; btn.style.background = '#0d9488'; }
    });
}
