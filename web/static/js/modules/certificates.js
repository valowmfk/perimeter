// Certificate management UI

import { escapeHtml, showToast, appendLogLine } from '../utils/dom.js';
import { confirmModal } from '../utils/modal.js';
import { withBusy } from '../utils/busy.js';
import { refreshPlaybooks } from './ansible.js';

/* ============================
   Header Strip Stats
   ============================ */

export function loadCertStats() {
    fetch('/api/cert/domains')
        .then(resp => resp.json())
        .then(data => {
            const domains = data.domains || [];
            const validCerts = domains.filter(d => d.has_cert && d.status === 'valid');

            const stripCount = document.getElementById('certCountStrip');
            const stripExpiry = document.getElementById('nextExpiryStrip');
            if (stripCount) stripCount.textContent = `${validCerts.length}/${domains.length}`;

            let nextExpiry = null;
            domains.forEach(d => {
                if (d.expires) {
                    const expiryDate = new Date(d.expires);
                    if (!nextExpiry || expiryDate < nextExpiry.date) {
                        nextExpiry = { date: expiryDate, domain: d.domain };
                    }
                }
            });

            if (nextExpiry) {
                const daysUntil = Math.floor((nextExpiry.date - new Date()) / (1000 * 60 * 60 * 24));
                if (stripExpiry) {
                    stripExpiry.textContent = `${daysUntil}d`;
                    stripExpiry.className = 'q-strip-meta q-cert-days-' + certDaysClass(daysUntil);
                }
            } else {
                if (stripExpiry) stripExpiry.textContent = 'N/A';
            }
        })
        .catch(() => {
            const stripCount = document.getElementById('certCountStrip');
            const stripExpiry = document.getElementById('nextExpiryStrip');
            if (stripCount) stripCount.textContent = 'Error';
            if (stripExpiry) stripExpiry.textContent = 'Error';
        });
}

function certDaysClass(days) {
    if (days < 0) return 'expired';
    if (days < 7) return 'critical';
    if (days < 30) return 'warn';
    if (days < 60) return 'ok';
    return 'good';
}

function certStatusDot(status) {
    const colors = {valid:'#22c55e', expiring_soon:'#eab308', critical:'#ef4444', expired:'#ef4444', unknown:'#64748b'};
    return `<span class="q-cert-status-dot" style="background:${colors[status]||'#64748b'}"></span>`;
}

/* ============================
   Flyout
   ============================ */

export function toggleCertFlyout() {
    const flyout = document.getElementById('certFlyout');
    const chevron = document.getElementById('certChevron');
    const toggle = document.querySelector('[data-action="toggle-cert-flyout"]');
    if (flyout.style.display === 'none') {
        flyout.style.display = 'block';
        chevron.textContent = '\u25b4';
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
        loadCertDetail();

    } else {
        flyout.style.display = 'none';
        chevron.textContent = '\u25be';
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }
}

function loadCertDetail() {
    const content = document.getElementById('certFlyoutContent');
    content.innerHTML = '<div class="q-cert-flyout-loading">Loading certificate data...</div>';

    fetch('/api/cert/domains/detail')
        .then(resp => resp.json())
        .then(data => {
            const domains = data.domains || [];
            if (domains.length === 0) {
                content.innerHTML = '<div class="q-cert-flyout-loading">No domains configured.</div>';
                return;
            }

            let totalCerts = 0, totalValid = 0, totalExpiring = 0;
            let html = '';

            domains.forEach(d => {
                const s = d.summary;
                totalCerts += s.total;
                totalValid += s.valid;
                totalExpiring += s.expiring_soon;

                let domStatus = 'unknown';
                if (s.total === 0) domStatus = 'unknown';
                else if (d.certs.some(c => c.status === 'expired')) domStatus = 'expired';
                else if (d.certs.some(c => c.status === 'critical')) domStatus = 'critical';
                else if (d.certs.some(c => c.status === 'expiring_soon')) domStatus = 'expiring_soon';
                else if (s.valid > 0) domStatus = 'valid';

                const daysText = s.earliest_days !== null
                    ? `<span class="q-cert-days q-cert-days-${certDaysClass(s.earliest_days)}">${s.earliest_days}d</span>`
                    : '<span class="q-cert-days q-cert-days-expired">\u2014</span>';

                const certCountText = s.total === 1 ? '1 cert' : `${s.total} certs`;
                const domainKey = d.domain.replace(/\./g,'_');
                const domainSafe = escapeHtml(d.domain);

                html += `<div class="q-cert-domain-row" data-action="cert-expand" data-domain="${domainSafe}">
                    ${certStatusDot(domStatus)}
                    <span class="q-cert-domain-name">${domainSafe}</span>
                    <span class="q-cert-domain-count">${certCountText}</span>
                    ${daysText}
                    <span class="q-cert-domain-chevron" id="certChevron_${escapeHtml(domainKey)}">${s.total > 0 ? '\u25be' : ''}</span>
                </div>`;

                html += `<div id="certSubs_${escapeHtml(domainKey)}" class="q-cert-subs" style="display:none;">`;
                d.certs.forEach((c, i) => {
                    const isLast = i === d.certs.length - 1;
                    const prefix = isLast ? '\u2514\u2500' : '\u251c\u2500';
                    const subDays = c.days_remaining !== null
                        ? `<span class="q-cert-days q-cert-days-${certDaysClass(c.days_remaining)}">${c.days_remaining}d</span>`
                        : '';

                    const statusLabel = c.status === 'valid' ? 'Valid'
                        : c.status === 'expiring_soon' ? 'Expiring'
                        : c.status === 'critical' ? 'Critical'
                        : c.status === 'expired' ? 'Expired' : '?';

                    const certNameSafe = escapeHtml(c.name);

                    html += `<div class="q-cert-sub-row">
                        <span class="q-cert-sub-prefix">${prefix}</span>
                        <span class="q-cert-sub-name">${certNameSafe}</span>
                        ${certStatusDot(c.status)}
                        <span class="q-cert-sub-status">${statusLabel}</span>
                        <span class="q-cert-sub-expiry">${escapeHtml(c.expires || '\u2014')}</span>
                        ${subDays}
                        <span class="q-cert-sub-actions">
                            <button title="Deploy to vThunder" data-action="deploy-to-vthunder" data-domain="${domainSafe}" data-cert="${certNameSafe}">&#x1f680;</button>
                            <button title="View / Copy" data-action="cert-view" data-domain="${domainSafe}" data-cert="${certNameSafe}">&#x1f4cb;</button>
                            <button title="Download" data-action="cert-download" data-domain="${domainSafe}" data-cert="${certNameSafe}">&#x2b07;</button>
                            <button title="Delete" data-action="cert-delete" data-domain="${domainSafe}" data-cert="${certNameSafe}" class="q-cert-btn-delete">&#x2716;</button>
                        </span>
                    </div>`;
                });
                html += '</div>';
            });

            content.innerHTML = html;

            const summary = document.getElementById('certFlyoutSummary');
            if (summary) {
                summary.textContent = `${totalValid}/${totalCerts} valid` +
                    (totalExpiring > 0 ? ` \u00b7 ${totalExpiring} expiring` : '');
            }
        })
        .catch(() => {
            content.innerHTML = '<div class="q-cert-flyout-loading">Error loading certificate data.</div>';
        });
}

export function toggleCertDomainExpand(domain) {
    const key = domain.replace(/\./g, '_');
    const subs = document.getElementById('certSubs_' + key);
    const chevron = document.getElementById('certChevron_' + key);
    if (!subs) return;
    if (subs.style.display === 'none') {
        subs.style.display = 'block';
        if (chevron) chevron.textContent = '\u25b4';
    } else {
        subs.style.display = 'none';
        if (chevron) chevron.textContent = '\u25be';
    }
}

/* ============================
   Cert View / Copy / Download
   ============================ */

let certModalPreviousFocus = null;
let certModalKeyHandler = null;

export function openCertViewModal(baseDomain, name) {
    const modal = document.getElementById('certViewModal');
    const title = document.getElementById('certViewTitle');
    const sel = document.getElementById('certViewFileSelect');
    certModalPreviousFocus = document.activeElement;
    modal.dataset.baseDomain = baseDomain;
    modal.dataset.certName = name;
    title.textContent = name;
    sel.value = 'fullchain.pem';
    modal.style.display = 'flex';
    fetchCertContent(baseDomain, name, 'fullchain.pem');

    // Focus trap
    const focusableEls = modal.querySelectorAll('select, button, [tabindex]:not([tabindex="-1"])');
    if (focusableEls.length) focusableEls[0].focus();

    certModalKeyHandler = function(e) {
        if (e.key === 'Escape') {
            closeCertViewModal();
            return;
        }
        if (e.key === 'Tab') {
            const els = Array.from(modal.querySelectorAll('select, button, [tabindex]:not([tabindex="-1"])'));
            if (!els.length) return;
            const idx = els.indexOf(document.activeElement);
            if (e.shiftKey) {
                e.preventDefault();
                els[idx <= 0 ? els.length - 1 : idx - 1].focus();
            } else {
                e.preventDefault();
                els[(idx + 1) % els.length].focus();
            }
        }
    };
    document.addEventListener('keydown', certModalKeyHandler);
}

export function certViewChangeFile() {
    const modal = document.getElementById('certViewModal');
    const file = document.getElementById('certViewFileSelect').value;
    fetchCertContent(modal.dataset.baseDomain, modal.dataset.certName, file);
}

function fetchCertContent(baseDomain, name, file) {
    const pre = document.getElementById('certViewContent');
    pre.textContent = 'Loading...';
    fetch(`/api/cert/view?base_domain=${encodeURIComponent(baseDomain)}&name=${encodeURIComponent(name)}&file=${encodeURIComponent(file)}`)
        .then(r => r.json())
        .then(data => {
            pre.textContent = data.error ? 'Error: ' + data.error : data.content;
        })
        .catch(() => {
            pre.textContent = 'Error loading certificate file.';
        });
}

export function copyCertToClipboard() {
    const content = document.getElementById('certViewContent').textContent;
    if (!content || content === 'Loading...' || content.startsWith('Error')) return;
    navigator.clipboard.writeText(content).then(() => {
        showToast('Copied!');
    });
}

export function closeCertViewModal() {
    const modal = document.getElementById('certViewModal');
    document.getElementById('certViewContent').textContent = '';
    modal.style.display = 'none';
    if (certModalKeyHandler) {
        document.removeEventListener('keydown', certModalKeyHandler);
        certModalKeyHandler = null;
    }
    if (certModalPreviousFocus && certModalPreviousFocus.focus) {
        certModalPreviousFocus.focus();
    }
}

export function downloadCert(baseDomain, name) {
    const url = `/api/cert/download-all?base_domain=${encodeURIComponent(baseDomain)}&name=${encodeURIComponent(name)}`;
    window.open(url, '_blank');
}

export async function deleteCert(baseDomain, name) {
    const ok = await confirmModal({
        title: 'Delete Certificate',
        message: `Delete certificate "${name}" from ${baseDomain}? This removes the cert files and certbot tracking. This cannot be undone.`,
        confirm: 'Delete Certificate',
        variant: 'danger'
    });
    if (!ok) return;

    appendLogLine(`[INFO] Deleting certificate "${name}" from ${baseDomain}...`);

    try {
        const resp = await fetch('/api/cert/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_domain: baseDomain, name })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
            const msg = data.error || `Delete failed (HTTP ${resp.status})`;
            appendLogLine(`[ERROR] ${msg}`);
            showToast(msg);
            return;
        }
        appendLogLine(`[OK] ${data.message}`);
        showToast(data.message);
        loadCertStats();
        // Refresh the flyout content
        loadCertDetail();
    } catch (err) {
        appendLogLine(`[ERROR] Error deleting certificate: ${err.message}`);
        showToast(`Error deleting certificate: ${err.message}`);
    }
}

/* ============================
   Certificate Preview
   ============================ */

export function updateCertPreview() {
    const baseDomain = document.getElementById('certBaseDomainSelect').value;
    const commonName = document.getElementById('certCommonName').value.trim();
    const wildcard = document.getElementById('certWildcard').checked;
    const sansInput = document.getElementById('certSANs').value.trim();
    const preview = document.getElementById('certPreview');
    const previewContent = document.getElementById('certPreviewContent');

    if (!baseDomain) {
        preview.style.display = 'none';
        return;
    }

    let domains = [];
    let cn = '';
    if (wildcard) {
        cn = `*.${baseDomain}`;
    } else if (commonName) {
        cn = `${commonName}.${baseDomain}`;
    } else {
        cn = baseDomain;
    }
    domains.push(cn);

    if (sansInput) {
        const sans = sansInput.split(',').map(s => s.trim()).filter(s => s);
        sans.forEach(san => {
            const fullDomain = `${san}.${baseDomain}`;
            if (!domains.includes(fullDomain)) {
                domains.push(fullDomain);
            }
        });
    }

    let html = `<div class="preview-label">Common Name (CN):</div>`;
    html += `<div class="preview-cn">\ud83d\udd12 ${escapeHtml(cn)}</div>`;

    if (domains.length > 1) {
        html += `<div class="preview-label" style="margin-top: 12px;">Subject Alternative Names (SANs):</div>`;
        domains.slice(1).forEach(d => {
            html += `<div class="preview-san">\u251c\u2500 ${escapeHtml(d)}</div>`;
        });
    }

    html += `<div class="preview-label" style="margin-top: 12px;">Total Domains:</div>`;
    html += `<div style="color: #00A0DF; font-weight: 700;">${domains.length} domain${domains.length > 1 ? 's' : ''}</div>`;

    previewContent.innerHTML = html;
    preview.style.display = 'block';

    if (document.getElementById('certCreatePlaybook').checked) {
        const sanitized = cn.replace(/\*/g, 'wildcard').replace(/\./g, '-');
        document.getElementById('certPlaybookName').value = `deploy-cert-${sanitized}.yml`;
    }
}

export function refreshCertDomains() {
    showToast("Refreshing certificate information...");
    fetch('/api/cert/domains')
        .then(resp => resp.json())
        .then(data => {
            showToast(`Loaded ${data.domains.length} domains`);
        })
        .catch(err => {
            showToast(`Error loading domains: ${err.message}`);
        });
}

/* ============================
   Playbook Generation
   ============================ */

export function onCreatePlaybookChange() {
    const checked = document.getElementById('certCreatePlaybook').checked;
    const playbookSection = document.getElementById('certPlaybookNameSection');

    if (checked) {
        playbookSection.style.display = 'block';
        updateCertPreview();
        toggleVipNameField();
    } else {
        playbookSection.style.display = 'none';
    }
}

export function toggleVipNameField() {
    const targetType = document.getElementById('certTargetType');
    const vthunderSection = document.getElementById('certVthunderSection');

    if (!targetType || !vthunderSection) return;

    if (targetType.value === 'vthunder_vip') {
        vthunderSection.style.display = 'block';
        loadVthunderGroups();
    } else {
        vthunderSection.style.display = 'none';
    }
}

export function toggleCertAdvanced() {
    const content = document.getElementById('certAdvancedOptions');
    const toggleText = document.getElementById('certAdvancedToggleText');
    const toggleBtn = document.querySelector('[data-action="toggle-cert-advanced"]');

    if (content.style.display === 'none') {
        content.style.display = 'block';
        toggleText.textContent = '\u25bc Advanced Options';
        if (toggleBtn) toggleBtn.setAttribute('aria-expanded', 'true');
    } else {
        content.style.display = 'none';
        toggleText.textContent = '\u25b6 Advanced Options';
        if (toggleBtn) toggleBtn.setAttribute('aria-expanded', 'false');
    }
}

/* ============================
   vThunder Cascade
   ============================ */

export function loadVthunderGroups() {
    const groupSelect = document.getElementById('certVthunderGroup');
    if (!groupSelect) return;

    groupSelect.innerHTML = '<option value="">Select group...</option>';

    fetch('/inventory/groups?file=inventory.yml')
        .then(resp => resp.json())
        .then(groups => {
            const vthunderGroups = groups.filter(g => g.toLowerCase().includes('vthunder'));
            vthunderGroups.forEach(group => {
                const opt = document.createElement('option');
                opt.value = group;
                opt.textContent = group;
                groupSelect.appendChild(opt);
            });
            appendLogLine(`[INFO] Loaded ${vthunderGroups.length} vThunder inventory groups`);
        })
        .catch(err => {
            appendLogLine(`[ERROR] Failed to load vThunder groups: ${err.message}`);
            showToast(`Error loading vThunder groups: ${err.message}`);
        });
}

export function loadVthunderHosts() {
    const groupSelect = document.getElementById('certVthunderGroup');
    const hostSelect = document.getElementById('certVthunderHost');
    const partitionSelect = document.getElementById('certVthunderPartition');

    if (!groupSelect || !hostSelect) return;

    const groupName = groupSelect.value;

    hostSelect.innerHTML = '<option value="">Select host...</option>';
    partitionSelect.innerHTML = '<option value="">Select partition...</option>';

    if (!groupName) return;

    appendLogLine(`[INFO] Loading vThunder hosts from group: ${groupName}`);

    fetch('/api/vthunder/hosts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: groupName })
    })
    .then(resp => resp.json())
    .then(data => {
        if (data.error) {
            appendLogLine(`[ERROR] ${data.error}`);
            showToast(data.error);
            return;
        }
        data.hosts.forEach(host => {
            const opt = document.createElement('option');
            opt.value = host;
            opt.textContent = host;
            hostSelect.appendChild(opt);
        });
        appendLogLine(`[INFO] Loaded ${data.hosts.length} vThunder host(s)`);
    })
    .catch(err => {
        appendLogLine(`[ERROR] Failed to load hosts: ${err.message}`);
        showToast(`Error loading hosts: ${err.message}`);
    });
}

export function loadVthunderPartitions() {
    const groupSelect = document.getElementById('certVthunderGroup');
    const hostSelect = document.getElementById('certVthunderHost');
    const partitionSelect = document.getElementById('certVthunderPartition');

    if (!hostSelect || !partitionSelect) return;

    const groupName = groupSelect.value;
    const host = hostSelect.value;

    partitionSelect.innerHTML = '<option value="">Loading partitions...</option>';

    if (!host || !groupName) return;

    appendLogLine(`[INFO] Connecting to ${host} to retrieve partitions...`);

    fetch('/api/vthunder/partitions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: groupName, host: host })
    })
    .then(resp => resp.json())
    .then(data => {
        partitionSelect.innerHTML = '<option value="">Select partition...</option>';
        if (data.error) {
            appendLogLine(`[ERROR] ${data.error}`);
            showToast(data.error);
            return;
        }
        data.partitions.forEach(partition => {
            const opt = document.createElement('option');
            opt.value = partition['partition-name'];
            opt.textContent = `${partition['partition-name']} (ID: ${partition.id})`;
            partitionSelect.appendChild(opt);
        });
        appendLogLine(`[SUCCESS] Retrieved ${data.partitions.length} partition(s) from ${host}`);
    })
    .catch(err => {
        partitionSelect.innerHTML = '<option value="">Select partition...</option>';
        appendLogLine(`[ERROR] Failed to load partitions: ${err.message}`);
        showToast(`Error loading partitions: ${err.message}`);
    });
}

/* ============================
   Execute Certificate Action
   ============================ */

export async function executeCertAction() {
    const baseDomain = document.getElementById('certBaseDomainSelect').value;
    const commonName = document.getElementById('certCommonName').value.trim();
    const wildcard = document.getElementById('certWildcard').checked;
    const sans = document.getElementById('certSANs').value.trim();
    const action = document.getElementById('certActionSelect').value;
    const createPlaybook = document.getElementById('certCreatePlaybook').checked;
    const playbookName = document.getElementById('certPlaybookName').value;
    const email = document.getElementById('certEmail').value || 'mattklouda@proton.me';
    const environment = document.getElementById('certEnvSelect').value;
    const dryRun = document.getElementById('certDryRun').checked;
    const extraFlags = document.getElementById('certExtraFlags').value.trim();

    if (!baseDomain) {
        showToast("Please select a base domain");
        return;
    }

    if (createPlaybook && !playbookName) {
        showToast("Please enter a playbook name");
        return;
    }

    if (action === 'revoke') {
        const ok = await confirmModal({
            title: 'Revoke Certificate',
            message: `Are you sure you want to REVOKE the certificate for ${baseDomain}? This action cannot be undone!`,
            confirm: 'Revoke Certificate',
            variant: 'danger'
        });
        if (!ok) return;
    }

    const busyDone = withBusy('execute-cert');

    const terminalLog = document.getElementById('terminalLog');
    if (terminalLog) terminalLog.innerHTML = '';

    document.getElementById('certExecuteStatus').textContent = `Executing ${action} for ${baseDomain}...`;
    document.getElementById('certSpinner').style.display = 'block';

    const payload = {
        base_domain: baseDomain, common_name: commonName, wildcard, sans,
        action, create_playbook: createPlaybook, playbook_name: playbookName,
        email, environment, dry_run: dryRun, extra_flags: extraFlags
    };

    fetch('/api/cert/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(resp => resp.json())
    .then(data => {
        if (data.error) {
            busyDone();
            showToast(`Error: ${data.error}`);
            document.getElementById('certSpinner').style.display = 'none';
            document.getElementById('certExecuteStatus').textContent = '';
            return;
        }

        showToast(`Certificate operation started for: ${data.domains.join(', ')}`);
        appendLogLine(`\n${'='.repeat(80)}`);
        appendLogLine(`\ud83d\udd10 CERTIFICATE OPERATION STARTED`);
        appendLogLine(`${'='.repeat(80)}`);
        appendLogLine(`Session ID: ${data.session_id}`);
        appendLogLine(`Domains: ${data.domains.join(', ')}`);
        appendLogLine(`Action: ${action}`);
        appendLogLine(`${'='.repeat(80)}\n`);

        streamCertOutput(data.session_id, busyDone);
    })
    .catch(err => {
        busyDone();
        showToast(`Error starting certificate operation: ${err.message}`);
        document.getElementById('certSpinner').style.display = 'none';
        document.getElementById('certExecuteStatus').textContent = '';
        appendLogLine(`[ERROR] ${err.message}`);
    });
}

function streamCertOutput(sessionId, busyDone) {
    const eventSource = new EventSource(`/api/cert/stream/${sessionId}`);

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);

        if (data.error) {
            appendLogLine(`[ERROR] ${data.error}`);
            eventSource.close();
            if (busyDone) busyDone();
            document.getElementById('certSpinner').style.display = 'none';
            document.getElementById('certExecuteStatus').textContent = '';
            return;
        }

        if (data.line) appendLogLine(data.line);

        if (data.done) {
            eventSource.close();
            if (busyDone) busyDone();
            document.getElementById('certSpinner').style.display = 'none';

            if (data.status === 'completed') {
                document.getElementById('certExecuteStatus').textContent = '\u2705 Operation completed successfully!';
                document.getElementById('certExecuteStatus').style.color = '#50fa7b';
                showToast("Certificate operation completed successfully!");
                loadCertStats();

                if (document.getElementById('certCreatePlaybook').checked) {
                    generateCertPlaybook();
                }
            } else if (data.status === 'failed') {
                document.getElementById('certExecuteStatus').textContent = '\u274c Operation failed';
                document.getElementById('certExecuteStatus').style.color = '#ff5555';
                showToast("Certificate operation failed");
            } else if (data.status === 'error') {
                document.getElementById('certExecuteStatus').textContent = '\u274c Error occurred';
                document.getElementById('certExecuteStatus').style.color = '#ff5555';
                showToast("Error during certificate operation");
            }

            setTimeout(() => {
                document.getElementById('certExecuteStatus').textContent = '';
                document.getElementById('certExecuteStatus').style.color = '#00A0DF';
            }, 5000);
        }
    };

    eventSource.onerror = function() {
        eventSource.close();
        if (busyDone) busyDone();
        appendLogLine('[ERROR] Connection lost to certificate operation stream');
        document.getElementById('certSpinner').style.display = 'none';
        document.getElementById('certExecuteStatus').textContent = '';
    };
}

function generateCertPlaybook() {
    const baseDomain = document.getElementById('certBaseDomainSelect').value;
    const commonName = document.getElementById('certCommonName').value.trim();
    const wildcard = document.getElementById('certWildcard').checked;
    const targetType = document.getElementById('certTargetType').value;
    const playbookName = document.getElementById('certPlaybookName').value.trim();

    let domain = baseDomain;
    if (wildcard) {
        domain = `*.${baseDomain}`;
    } else if (commonName) {
        domain = `${commonName}.${baseDomain}`;
    }

    let partition, vthunderHost;
    if (targetType === 'vthunder_vip') {
        const hostElement = document.getElementById('certVthunderHost');
        vthunderHost = hostElement ? hostElement.value : '';
        partition = document.getElementById('certVthunderPartition').value;

        if (!vthunderHost || !partition) {
            appendLogLine(`[ERROR] vThunder host and partition are required for vThunder deployments`);
            showToast('Please select vThunder host and partition for vThunder deployment');
            return;
        }
    }

    appendLogLine(`\n${'='.repeat(80)}`);
    appendLogLine(`\ud83d\udccb GENERATING ANSIBLE PLAYBOOK`);
    appendLogLine(`${'='.repeat(80)}`);
    appendLogLine(`Domain: ${domain}`);
    appendLogLine(`Target Type: ${targetType}`);
    if (targetType === 'vthunder_vip') {
        appendLogLine(`vThunder Host: ${vthunderHost}`);
        appendLogLine(`Partition: ${partition}`);
    }
    appendLogLine(`Playbook Name: ${playbookName || 'auto-generated'}`);
    appendLogLine(`${'='.repeat(80)}\n`);

    const payload = { domain, target_type: targetType, playbook_name: playbookName };
    if (targetType === 'vthunder_vip') {
        payload.vthunder_host = vthunderHost;
        payload.partition = partition;
    }

    fetch('/api/cert/generate_playbook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(resp => resp.json())
    .then(data => {
        if (data.error) {
            appendLogLine(`[ERROR] Playbook generation failed: ${data.error}`);
            showToast(`Playbook generation failed: ${data.error}`);
            return;
        }
        if (data.success) {
            appendLogLine(`[SUCCESS] Playbook created: ${data.playbook}`);
            appendLogLine(`[INFO] Path: ${data.path}`);
            appendLogLine(`[INFO] Target Type: ${data.target_type}`);
            showToast(`Playbook created: ${data.playbook}`);
            refreshPlaybooks();
        }
    })
    .catch(err => {
        appendLogLine(`[ERROR] Failed to generate playbook: ${err.message}`);
        showToast(`Error generating playbook: ${err.message}`);
    });
}


/* ============================
   Deploy to vThunder Modal
   ============================ */

let deployState = { baseDomain: '', domain: '' };

export function openDeployVthunderModal(baseDomain, domain) {
    deployState = { baseDomain, domain };

    const modal = document.getElementById('deployVthunderModal');
    const domainEl = document.getElementById('deployDomain');
    const resultEl = document.getElementById('deployResult');
    const renewalEl = document.getElementById('deployRenewalStatus');
    const groupSel = document.getElementById('deployVthGroup');
    const hostSel = document.getElementById('deployVthHost');
    const partSel = document.getElementById('deployVthPartition');

    if (!modal) return;

    domainEl.textContent = domain;
    resultEl.style.display = 'none';
    renewalEl.style.display = 'none';
    groupSel.innerHTML = '<option value="">-- Select Group --</option>';
    hostSel.innerHTML = '<option value="">-- Select Host --</option>';
    partSel.innerHTML = '<option value="">-- Select Partition --</option>';

    modal.style.display = 'flex';

    // Load vThunder groups from inventory
    fetch('/inventory/groups?file=inventory.yml')
        .then(r => r.json())
        .then(groups => {
            const vthunderGroups = (groups || []).filter(g => g.toLowerCase().includes('vthunder'));
            vthunderGroups.forEach(g => {
                const opt = document.createElement('option');
                opt.value = g;
                opt.textContent = g;
                groupSel.appendChild(opt);
            });
        });
}

export function closeDeployVthunderModal() {
    const modal = document.getElementById('deployVthunderModal');
    if (modal) modal.style.display = 'none';
}

export function onDeployGroupChange() {
    const group = document.getElementById('deployVthGroup').value;
    const hostSel = document.getElementById('deployVthHost');
    const partSel = document.getElementById('deployVthPartition');
    const renewalEl = document.getElementById('deployRenewalStatus');

    hostSel.innerHTML = '<option value="">-- Select Host --</option>';
    partSel.innerHTML = '<option value="">-- Select Partition --</option>';
    renewalEl.style.display = 'none';

    if (!group) return;

    fetch('/api/vthunder/hosts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group })
    })
    .then(r => r.json())
    .then(data => {
        (data.hosts || []).forEach(h => {
            const opt = document.createElement('option');
            opt.value = h;
            opt.textContent = h;
            hostSel.appendChild(opt);
        });
    });
}

export function onDeployHostChange() {
    const group = document.getElementById('deployVthGroup').value;
    const host = document.getElementById('deployVthHost').value;
    const partSel = document.getElementById('deployVthPartition');
    const renewalEl = document.getElementById('deployRenewalStatus');

    partSel.innerHTML = '<option value="">-- Select Partition --</option>';
    renewalEl.style.display = 'none';

    if (!host) return;

    fetch('/api/vthunder/partitions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host })
    })
    .then(r => r.json())
    .then(data => {
        (data.partitions || []).forEach(p => {
            const name = p['partition-name'] || p.name || '';
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            partSel.appendChild(opt);
        });
    });
}

export function onDeployPartitionChange() {
    const host = document.getElementById('deployVthHost').value;
    const group = document.getElementById('deployVthGroup').value;
    const partition = document.getElementById('deployVthPartition').value;
    const renewalEl = document.getElementById('deployRenewalStatus');

    renewalEl.style.display = 'none';
    if (!partition || !host) return;

    const certName = deployState.domain.replace(/\./g, '_').replace(/\*/g, 'wildcard');

    fetch('/api/vthunder/certs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host, partition })
    })
    .then(r => r.json())
    .then(data => {
        const certs = data.certs || [];
        const exists = certs.some(c => (c.file || '').includes(certName));
        renewalEl.style.display = 'block';
        if (exists) {
            renewalEl.style.background = 'rgba(34,197,94,0.15)';
            renewalEl.style.color = '#22c55e';
            renewalEl.innerHTML = `&#x26a1; <strong>Renewal</strong> — <code>${certName}</code> already exists on ${host} / ${partition}. Upload will replace the existing cert.`;
        } else {
            renewalEl.style.background = 'rgba(59,130,246,0.15)';
            renewalEl.style.color = '#60a5fa';
            renewalEl.innerHTML = `&#x2728; <strong>New certificate</strong> for ${host} / ${partition}. You may need to create an SSL template and VIP after deployment.`;
        }
    })
    .catch(() => {
        renewalEl.style.display = 'block';
        renewalEl.style.background = 'rgba(234,179,8,0.15)';
        renewalEl.style.color = '#eab308';
        renewalEl.textContent = 'Could not check existing certs — will attempt upload anyway.';
    });
}

export function executeDeployToVthunder() {
    const group = document.getElementById('deployVthGroup').value;
    const host = document.getElementById('deployVthHost').value;
    const partition = document.getElementById('deployVthPartition').value;
    const certFile = document.getElementById('deployCertFile').value;
    const resultEl = document.getElementById('deployResult');
    const btn = document.getElementById('deployVthExecuteBtn');

    if (!group || !host || !partition) {
        resultEl.style.display = 'block';
        resultEl.style.color = '#ef4444';
        resultEl.textContent = 'Please select group, host, and partition.';
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Deploying...';
    resultEl.style.display = 'block';
    resultEl.style.color = '#94a3b8';
    resultEl.textContent = 'Uploading certificate...';

    fetch('/api/cert/deploy-vthunder', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            base_domain: deployState.baseDomain,
            domain: deployState.domain,
            cert_file: certFile,
            vthunder_host: host,
            vthunder_group: group,
            partition,
        })
    })
    .then(r => r.json())
    .then(data => {
        btn.disabled = false;
        btn.textContent = 'Deploy';
        if (data.success) {
            resultEl.style.color = '#22c55e';
            resultEl.textContent = data.message;
            showToast(data.message);
        } else {
            resultEl.style.color = '#ef4444';
            resultEl.textContent = data.error || 'Deployment failed';
        }
    })
    .catch(err => {
        btn.disabled = false;
        btn.textContent = 'Deploy';
        resultEl.style.color = '#ef4444';
        resultEl.textContent = `Error: ${err.message}`;
    });
}
