// vThunder SLB Manager — VIP creation, management, and teardown

import { showToast, appendLogLine } from '../utils/dom.js';

// ── Shared cascade helpers ──────────────────────────────────

function loadGroupsInto(selectId) {
    const el = document.getElementById(selectId);
    if (!el) return;
    el.innerHTML = '<option value="">-- Select Group --</option>';
    fetch('/inventory/groups?file=inventory.yml')
        .then(r => r.json())
        .then(groups => {
            groups.filter(g => g.toLowerCase().includes('vthunder')).forEach(g => {
                const opt = document.createElement('option');
                opt.value = g;
                opt.textContent = g;
                el.appendChild(opt);
            });
        });
}

function loadHostsInto(groupSelectId, hostSelectId, partitionSelectId) {
    const group = document.getElementById(groupSelectId)?.value;
    const hostEl = document.getElementById(hostSelectId);
    const partEl = document.getElementById(partitionSelectId);
    if (hostEl) hostEl.innerHTML = '<option value="">-- Select Host --</option>';
    if (partEl) partEl.innerHTML = '<option value="">-- Select Partition --</option>';
    if (!group) return;
    fetch('/api/vthunder/hosts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group })
    }).then(r => r.json()).then(data => {
        if (data.error) { showToast(data.error); return; }
        data.hosts.forEach(h => {
            const opt = document.createElement('option');
            opt.value = h;
            opt.textContent = h;
            hostEl.appendChild(opt);
        });
    });
}

function loadPartitionsInto(groupSelectId, hostSelectId, partitionSelectId) {
    const group = document.getElementById(groupSelectId)?.value;
    const host = document.getElementById(hostSelectId)?.value;
    const partEl = document.getElementById(partitionSelectId);
    if (!partEl) return;
    partEl.innerHTML = '<option value="">-- Select Partition --</option>';
    if (!group || !host) return;
    fetch('/api/vthunder/partitions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host })
    }).then(r => r.json()).then(data => {
        if (data.error) { showToast(data.error); return; }
        data.partitions.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p['partition-name'];
            opt.textContent = `${p['partition-name']} (ID: ${p.id})`;
            partEl.appendChild(opt);
        });
    });
}

// ── VIP Manager drawer ──────────────────────────────────

export function initVipManager() {
    const groupEl = document.getElementById('vipMgrGroup');
    const hostEl = document.getElementById('vipMgrHost');
    const partEl = document.getElementById('vipMgrPartition');
    if (!groupEl) return;
    groupEl.addEventListener('change', () => loadHostsInto('vipMgrGroup', 'vipMgrHost', 'vipMgrPartition'));
    hostEl.addEventListener('change', () => loadPartitionsInto('vipMgrGroup', 'vipMgrHost', 'vipMgrPartition'));
    loadGroupsInto('vipMgrGroup');
    document.getElementById('vipEmptyMessage').style.display = 'block';
}

export function loadVips() {
    const group = document.getElementById('vipMgrGroup')?.value;
    const host = document.getElementById('vipMgrHost')?.value;
    const partition = document.getElementById('vipMgrPartition')?.value;
    if (!group || !host || !partition) {
        showToast('Select group, host, and partition first');
        return;
    }

    appendLogLine('[INFO] Loading VIPs...');
    fetch('/api/vthunder/vips', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host, partition })
    }).then(r => r.json()).then(data => {
        if (data.error) { showToast(data.error); return; }
        const vips = data.vips || [];
        const table = document.getElementById('vipTable');
        const tbody = document.getElementById('vipTableBody');
        const empty = document.getElementById('vipEmptyMessage');

        if (vips.length === 0) {
            table.style.display = 'none';
            empty.style.display = 'block';
            empty.textContent = 'No VIPs found on this device/partition.';
            return;
        }

        table.style.display = 'table';
        empty.style.display = 'none';
        tbody.innerHTML = '';

        vips.forEach(vip => {
            const ports = (vip['port-list'] || []).map(p => `${p['port-number']}/${p.protocol}`).join(', ');
            const sg = (vip['port-list'] || []).map(p => p['service-group']).filter(Boolean)[0] || '—';
            const ssl = (vip['port-list'] || []).map(p => p['template-client-ssl']).filter(Boolean)[0] || '—';
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${vip.name}</td>
                <td>${vip['ip-address'] || '—'}</td>
                <td>${ports || '—'}</td>
                <td>${sg}</td>
                <td>${ssl}</td>
                <td><button class="q-button" style="font-size:11px; background:#ef4444; color:white; padding:3px 8px;"
                    data-action="vip-destroy" data-vip-name="${vip.name}">Destroy</button></td>
            `;
            tbody.appendChild(tr);
        });
        appendLogLine(`[SUCCESS] Loaded ${vips.length} VIP(s)`);
    }).catch(err => {
        showToast(`Error loading VIPs: ${err.message}`);
    });
}

// ── VIP Builder Wizard ──────────────────────────────────

let currentStep = 1;
const TOTAL_STEPS = 4;

function showStep(step) {
    currentStep = step;
    for (let i = 1; i <= TOTAL_STEPS; i++) {
        const el = document.getElementById(`vipStep${i}`);
        if (el) el.style.display = i === step ? 'block' : 'none';
    }
    document.getElementById('vipPrevBtn').style.display = step > 1 ? 'inline-block' : 'none';
    const nextBtn = document.getElementById('vipNextBtn');
    if (step === TOTAL_STEPS) {
        nextBtn.textContent = 'Deploy';
        nextBtn.style.background = '#0d9488';
    } else {
        nextBtn.textContent = 'Next';
        nextBtn.style.background = '';
    }
    document.getElementById('vipResult').style.display = 'none';
    if (step === TOTAL_STEPS) buildReviewSummary();
}

export function openVipBuilder() {
    const modal = document.getElementById('vipBuilderModal');
    modal.style.display = 'flex';

    // Reset Next button state (may have been overridden to "Close" from previous deploy)
    const nextBtn = document.getElementById('vipNextBtn');
    nextBtn.onclick = null;
    nextBtn.disabled = false;
    nextBtn.textContent = 'Next';
    nextBtn.style.background = '';
    nextBtn.dataset.action = 'vip-next';

    showStep(1);

    // Init cascade for wizard
    const groupEl = document.getElementById('vipVthGroup');
    const hostEl = document.getElementById('vipVthHost');
    const partEl = document.getElementById('vipVthPartition');

    // Remove old listeners by replacing elements
    groupEl.onchange = () => loadHostsInto('vipVthGroup', 'vipVthHost', 'vipVthPartition');
    hostEl.onchange = () => loadPartitionsInto('vipVthGroup', 'vipVthHost', 'vipVthPartition');
    partEl.onchange = () => loadCertsOnDevice();

    loadGroupsInto('vipVthGroup');

    // Reset form
    document.getElementById('vipName').value = '';
    document.getElementById('vipIp').value = '';
    document.getElementById('vipCertName').innerHTML = '<option value="">(none - no SSL)</option>';
    document.getElementById('vipHttpRedirect').checked = true;
    document.getElementById('vipHealthMonitorEnable').checked = false;
    document.getElementById('vipHealthMonitorConfig').style.display = 'none';
    document.getElementById('vipHmName').value = '';
    document.getElementById('vipHealthMonitorEnable').onchange = () => {
        document.getElementById('vipHealthMonitorConfig').style.display =
            document.getElementById('vipHealthMonitorEnable').checked ? 'block' : 'none';
    };
    document.getElementById('vipBackendList').innerHTML = '';
    addBackendRow();
}

function loadCertsOnDevice() {
    const group = document.getElementById('vipVthGroup')?.value;
    const host = document.getElementById('vipVthHost')?.value;
    const partition = document.getElementById('vipVthPartition')?.value;
    const certSelect = document.getElementById('vipCertName');
    if (!group || !host || !partition) return;

    fetch('/api/vthunder/certs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host, partition })
    }).then(r => r.json()).then(data => {
        certSelect.innerHTML = '<option value="">(none - no SSL)</option>';
        if (data.error) { console.warn('Cert load error:', data.error); return; }
        const certs = data.certs || [];
        const seen = new Set();
        certs.forEach(cert => {
            // aXAPI returns file-list items — the cert name is the 'file' field
            const rawName = cert.file || cert.name || cert['file-name'] || '';
            if (!rawName) return;
            // Strip any extension and deduplicate (cert + key share same name)
            const certName = rawName.replace(/\.(pem|cert|crt|key)$/, '');
            if (seen.has(certName)) return;
            seen.add(certName);
            const opt = document.createElement('option');
            opt.value = certName;
            opt.textContent = certName;
            certSelect.appendChild(opt);
        });
        appendLogLine(`[INFO] Loaded ${seen.size} certificate(s) from device`);
    });
}

export function addBackendRow() {
    const list = document.getElementById('vipBackendList');
    const idx = list.children.length + 1;
    const row = document.createElement('div');
    row.className = 'vip-backend-row';
    row.style.cssText = 'background:rgba(15,23,42,0.4); border:1px solid rgba(71,85,105,0.4); border-radius:6px; padding:10px; margin-bottom:8px; position:relative;';
    row.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span style="font-size:12px; font-weight:600; color:#94a3b8;">Server ${idx}</span>
            <button type="button" class="q-button" data-action="vip-remove-backend"
                style="font-size:10px; background:#ef4444; color:white; padding:2px 8px; line-height:1.4;">Remove</button>
        </div>
        <div style="display:flex; gap:8px; margin-bottom:6px;">
            <div style="flex:1;">
                <label style="font-size:11px; color:#64748b;">Server Name</label>
                <input type="text" class="q-input backend-name" placeholder="e.g. web-server-1" style="width:100%;">
            </div>
            <div style="flex:1;">
                <label style="font-size:11px; color:#64748b;">IP Address</label>
                <input type="text" class="q-input backend-ip" placeholder="e.g. 10.127.70.31" style="width:100%;">
            </div>
        </div>
        <div style="display:flex; gap:8px; align-items:flex-end;">
            <div style="flex:0 0 100px;">
                <label style="font-size:11px; color:#64748b;">Port</label>
                <input type="number" class="q-input backend-port" value="80" style="width:100%;">
            </div>
            <div style="flex:0 0 100px;">
                <label style="font-size:11px; color:#64748b;">Protocol</label>
                <select class="q-input backend-protocol" style="width:100%;">
                    <option value="tcp" selected>TCP</option>
                    <option value="udp">UDP</option>
                </select>
            </div>
            <div style="flex:1; display:flex; align-items:center; gap:6px; padding-bottom:4px;">
                <label style="font-size:11px; color:#64748b; display:flex; align-items:center; gap:4px; cursor:pointer; white-space:nowrap;">
                    <input type="checkbox" class="backend-hc-disable" style="accent-color:#06b6d4;">
                    Disable health check
                </label>
            </div>
        </div>
    `;
    list.appendChild(row);
}

export function removeBackendRow(el) {
    const row = el.closest('.vip-backend-row');
    if (row) {
        row.remove();
        // Re-number remaining rows
        document.querySelectorAll('#vipBackendList .vip-backend-row').forEach((r, i) => {
            const label = r.querySelector('span');
            if (label) label.textContent = `Server ${i + 1}`;
        });
    }
}

function getBackends() {
    const rows = document.querySelectorAll('#vipBackendList .vip-backend-row');
    const backends = [];
    rows.forEach(row => {
        const name = row.querySelector('.backend-name')?.value?.trim();
        const ip = row.querySelector('.backend-ip')?.value?.trim();
        const port = parseInt(row.querySelector('.backend-port')?.value, 10);
        const protocol = row.querySelector('.backend-protocol')?.value || 'tcp';
        const hcDisable = row.querySelector('.backend-hc-disable')?.checked || false;
        if (name && ip && port) backends.push({ name, ip, port, protocol, health_check_disable: hcDisable });
    });
    return backends;
}

function getWizardConfig() {
    const hmEnabled = document.getElementById('vipHealthMonitorEnable')?.checked;
    let health_monitor = null;
    if (hmEnabled) {
        health_monitor = {
            name: document.getElementById('vipHmName')?.value?.trim() || '',
            interval: parseInt(document.getElementById('vipHmInterval')?.value, 10) || 5,
            timeout: parseInt(document.getElementById('vipHmTimeout')?.value, 10) || 1,
            retry: parseInt(document.getElementById('vipHmRetry')?.value, 10) || 2,
            up_retry: parseInt(document.getElementById('vipHmUpRetry')?.value, 10) || 4,
            http_port: parseInt(document.getElementById('vipHmPort')?.value, 10) || 80,
            url_path: document.getElementById('vipHmUrlPath')?.value?.trim() || '/',
            expect: document.getElementById('vipHmExpect')?.value?.trim() || '',
        };
    }
    return {
        group: document.getElementById('vipVthGroup')?.value,
        host: document.getElementById('vipVthHost')?.value,
        partition: document.getElementById('vipVthPartition')?.value,
        vip_name: document.getElementById('vipName')?.value?.trim(),
        vip_ip: document.getElementById('vipIp')?.value?.trim(),
        cert_name: document.getElementById('vipCertName')?.value,
        http_redirect: document.getElementById('vipHttpRedirect')?.checked,
        health_monitor: health_monitor,
        backends: getBackends(),
    };
}

function buildReviewSummary() {
    const cfg = getWizardConfig();
    const lines = [
        `Device:         ${cfg.host}`,
        `Partition:      ${cfg.partition}`,
        `VIP Name:       ${cfg.vip_name}`,
        `VIP IP:         ${cfg.vip_ip}`,
        `Certificate:    ${cfg.cert_name || '(none)'}`,
        `HTTP Redirect:  ${cfg.http_redirect ? 'Yes' : 'No'}`,
        cfg.health_monitor
            ? `Health Monitor: ${cfg.health_monitor.name} (HTTP :${cfg.health_monitor.http_port} ${cfg.health_monitor.url_path}${cfg.health_monitor.expect ? ' expect=' + cfg.health_monitor.expect : ''})`
            : `Health Monitor: (none)`,
        ``,
        `Backends (${cfg.backends.length}):`,
        ...cfg.backends.map(b => `  ${b.name} → ${b.ip}:${b.port}/${b.protocol}${b.health_check_disable ? ' (HC disabled)' : ''}`),
    ];
    document.getElementById('vipReviewSummary').textContent = lines.join('\n');
}

export function vipNext() {
    const cfg = getWizardConfig();
    // Validate each step before advancing
    if (currentStep === 1) {
        if (!cfg.group || !cfg.host || !cfg.partition) {
            showToast('Select group, host, and partition');
            return;
        }
    } else if (currentStep === 2) {
        if (!cfg.vip_name) { showToast('VIP name is required'); return; }
        if (!cfg.vip_ip) { showToast('VIP IP is required'); return; }
    } else if (currentStep === 3) {
        if (cfg.backends.length === 0) { showToast('Add at least one backend server'); return; }
    } else if (currentStep === TOTAL_STEPS) {
        deployVip();
        return;
    }
    showStep(currentStep + 1);
}

export function vipPrev() {
    if (currentStep > 1) showStep(currentStep - 1);
}

function deployVip() {
    const cfg = getWizardConfig();
    const nextBtn = document.getElementById('vipNextBtn');
    const resultDiv = document.getElementById('vipResult');
    nextBtn.disabled = true;
    nextBtn.textContent = 'Deploying...';

    fetch('/api/vthunder/create-vip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            inventory_file: 'inventory.yml',
            group_name: cfg.group,
            host: cfg.host,
            partition: cfg.partition,
            config: {
                vip_name: cfg.vip_name,
                vip_ip: cfg.vip_ip,
                cert_name: cfg.cert_name,
                http_redirect: cfg.http_redirect,
                health_monitor: cfg.health_monitor,
                backends: cfg.backends,
            }
        })
    }).then(r => r.json()).then(data => {
        resultDiv.style.display = 'block';
        if (data.success) {
            const steps = (data.steps || []).map(s => `${s.success ? '✔' : '✖'} ${s.name}`).join('\n');
            resultDiv.innerHTML = `<div style="color:#10b981; margin-bottom:8px;">VIP created successfully!</div><pre style="font-size:11px; color:#94a3b8; white-space:pre-wrap;">${steps}</pre>`;
            appendLogLine(`[SUCCESS] VIP '${cfg.vip_name}' created on ${cfg.host}/${cfg.partition}`);
        } else {
            const steps = (data.steps || []).map(s => `${s.success ? '✔' : '✖'} ${s.name}${s.detail ? ': ' + s.detail : ''}`).join('\n');
            resultDiv.innerHTML = `<div style="color:#ef4444; margin-bottom:8px;">VIP creation failed</div><pre style="font-size:11px; color:#94a3b8; white-space:pre-wrap;">${steps}</pre>`;
            appendLogLine(`[ERROR] VIP '${cfg.vip_name}' creation failed on ${cfg.host}/${cfg.partition}`);
        }
        nextBtn.disabled = false;
        nextBtn.textContent = 'Close';
        nextBtn.dataset.action = 'vip-cancel';
    }).catch(err => {
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `<div style="color:#ef4444;">Error: ${err.message}</div>`;
        nextBtn.disabled = false;
        nextBtn.textContent = 'Deploy';
    });
}

export function closeVipBuilder() {
    document.getElementById('vipBuilderModal').style.display = 'none';
    // Refresh VIP list if manager drawer is visible
    const table = document.getElementById('vipTable');
    if (table && table.style.display === 'table') loadVips();
}

// ── VIP Destroy ──────────────────────────────────

let destroyTarget = null;

export function openVipDestroy(vipName) {
    destroyTarget = vipName;
    document.getElementById('vipDestroyName').textContent = vipName;
    document.getElementById('vipDestroyResult').style.display = 'none';
    document.getElementById('vipDestroyModal').style.display = 'flex';
}

export function confirmVipDestroy() {
    if (!destroyTarget) return;

    const group = document.getElementById('vipMgrGroup')?.value;
    const host = document.getElementById('vipMgrHost')?.value;
    const partition = document.getElementById('vipMgrPartition')?.value;
    const cleanupServers = document.getElementById('vipDestroyCleanupServers')?.checked;

    if (!group || !host || !partition) {
        showToast('Device context lost — close and re-select');
        return;
    }

    const resultDiv = document.getElementById('vipDestroyResult');

    fetch('/api/vthunder/destroy-vip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            inventory_file: 'inventory.yml',
            group_name: group,
            host,
            partition,
            vip_name: destroyTarget,
            cleanup_servers: cleanupServers,
        })
    }).then(r => r.json()).then(data => {
        resultDiv.style.display = 'block';
        if (data.success) {
            const steps = (data.steps || []).map(s => `${s.success ? '✔' : '✖'} ${s.name}`).join('\n');
            resultDiv.innerHTML = `<div style="color:#10b981; margin-bottom:8px;">VIP destroyed.</div><pre style="font-size:11px; color:#94a3b8; white-space:pre-wrap;">${steps}</pre>`;
            appendLogLine(`[SUCCESS] VIP '${destroyTarget}' destroyed on ${host}/${partition}`);
            setTimeout(() => {
                document.getElementById('vipDestroyModal').style.display = 'none';
                loadVips();
            }, 1500);
        } else {
            const steps = (data.steps || []).map(s => `${s.success ? '✔' : '✖'} ${s.name}${s.detail ? ': ' + s.detail : ''}`).join('\n');
            resultDiv.innerHTML = `<div style="color:#ef4444; margin-bottom:8px;">Destroy failed</div><pre style="font-size:11px; color:#94a3b8; white-space:pre-wrap;">${steps}</pre>`;
        }
    }).catch(err => {
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = `<div style="color:#ef4444;">Error: ${err.message}</div>`;
    });
}

export function cancelVipDestroy() {
    document.getElementById('vipDestroyModal').style.display = 'none';
    destroyTarget = null;
}
