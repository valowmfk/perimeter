// IPAM data loading and IP validation

import { escapeHtml, showToast } from '../utils/dom.js';

let ipamData = null;
let ipamLoaded = false;
let ipValidateTimer = null;

export function loadIpamSubnets() {
    fetch('/api/subnets')
        .then(r => r.json())
        .then(data => {
            const sel = document.getElementById('ipamSubnetSelect');
            if (!sel) return;
            sel.innerHTML = '';
            data.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.network;
                opt.textContent = s.network;
                sel.appendChild(opt);
            });
        })
        .catch(() => {});
}

export function onIpamSubnetChange() {
    ipamData = null;
    ipamLoaded = false;
    loadIpamData();
}

export function tryLoadIpam() {
    if (!ipamLoaded) loadIpamData();
}

export function loadIpamData() {
    const loading = document.getElementById('ipamLoading');
    const error = document.getElementById('ipamError');
    const table = document.getElementById('ipamTableContainer');
    const subnetSel = document.getElementById('ipamSubnetSelect');
    const subnet = subnetSel ? subnetSel.value : '10.1.55.0/24';

    if (loading) loading.style.display = 'block';
    if (error) error.style.display = 'none';
    if (table) table.style.display = 'none';

    fetch(`/api/netbox/ipam?subnet=${encodeURIComponent(subnet)}`)
        .then(r => r.json())
        .then(data => {
            if (loading) loading.style.display = 'none';

            if (data.error) {
                if (error) {
                    error.textContent = data.error;
                    error.style.display = 'block';
                }
                return;
            }

            ipamData = data;
            ipamLoaded = true;

            const badge = document.getElementById('ipamCount');
            if (badge) badge.textContent = `${data.count} used`;

            renderIpamTable(data.ips);
            if (table) table.style.display = 'block';
        })
        .catch(err => {
            if (loading) loading.style.display = 'none';
            if (error) {
                error.textContent = `Failed to load IPAM data: ${err.message}`;
                error.style.display = 'block';
            }
        });
}

function renderIpamTable(ips) {
    const tbody = document.getElementById('ipamTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    ips.forEach(ip => {
        const tr = document.createElement('tr');
        const statusClass = `q-ipam-status-${ip.status || 'active'}`;

        tr.innerHTML = `
            <td><span class="q-ipam-ip">${escapeHtml(ip.address)}</span></td>
            <td>${ip.hostname ? escapeHtml(ip.hostname) : '<span style="opacity:0.4">\u2014</span>'}</td>
            <td><span class="q-ipam-status ${escapeHtml(statusClass)}">${escapeHtml(ip.status_label || ip.status)}</span></td>
        `;
        tbody.appendChild(tr);
    });
}

export function refreshIpam() {
    ipamData = null;
    ipamLoaded = false;
    loadIpamData();
}

export function validateIpAddress() {
    const input = document.getElementById('ip');
    const indicator = document.getElementById('ipValidationIndicator');
    const msg = document.getElementById('ipValidationMsg');
    if (!input || !indicator || !msg) return;

    const val = input.value.trim();

    if (ipValidateTimer) clearTimeout(ipValidateTimer);

    if (!val) {
        indicator.textContent = '';
        indicator.className = 'q-ip-validation-indicator';
        msg.textContent = '';
        msg.className = 'q-ip-validation-msg';
        input.classList.remove('q-input-ip-available', 'q-input-ip-taken', 'q-input-ip-invalid');
        return;
    }

    // Dynamic subnet validation based on selected subnet
    const subnetSel = document.getElementById('subnet');
    const selectedSubnet = subnetSel ? subnetSel.value : '10.1.55.0/24';
    const subnetPrefix = selectedSubnet.split('/')[0].replace(/\.0$/, '.');
    const prefixRegex = new RegExp('^' + subnetPrefix.replace(/\./g, '\\.') + '(\\d{1,3})$');
    const match = val.match(prefixRegex);

    if (!match) {
        // Check if user is still typing a valid prefix
        if (val.length < subnetPrefix.length && subnetPrefix.startsWith(val)) {
            indicator.textContent = '';
            indicator.className = 'q-ip-validation-indicator';
            msg.textContent = '';
            input.classList.remove('q-input-ip-available', 'q-input-ip-taken', 'q-input-ip-invalid');
        } else if (val.startsWith(subnetPrefix)) {
            indicator.textContent = '';
            indicator.className = 'q-ip-validation-indicator';
            msg.textContent = '';
            input.classList.remove('q-input-ip-available', 'q-input-ip-taken', 'q-input-ip-invalid');
        } else {
            indicator.textContent = '!';
            indicator.className = 'q-ip-validation-indicator q-ip-invalid';
            msg.textContent = `Expected format: ${subnetPrefix}x`;
            msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
            input.classList.remove('q-input-ip-available', 'q-input-ip-taken');
            input.classList.add('q-input-ip-invalid');
        }
        return;
    }

    const octet = parseInt(match[1], 10);
    if (octet < 1 || octet > 254) {
        indicator.textContent = '!';
        indicator.className = 'q-ip-validation-indicator q-ip-invalid';
        msg.textContent = 'Last octet must be 1\u2013254';
        msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
        input.classList.remove('q-input-ip-available', 'q-input-ip-taken');
        input.classList.add('q-input-ip-invalid');
        return;
    }

    indicator.textContent = '...';
    indicator.className = 'q-ip-validation-indicator q-ip-checking';
    msg.textContent = '';

    ipValidateTimer = setTimeout(() => checkIpAvailability(val), 300);
}

function checkIpAvailability(ip) {
    const input = document.getElementById('ip');
    const indicator = document.getElementById('ipValidationIndicator');
    const msg = document.getElementById('ipValidationMsg');
    if (!indicator || !msg) return;

    function doCheck(data) {
        const found = data.ips.find(item => item.address === ip);

        if (found) {
            indicator.textContent = '\u2715';
            indicator.className = 'q-ip-validation-indicator q-ip-taken';
            const who = found.hostname || found.dns_name || found.device || '';
            msg.textContent = who ? `Taken by ${who}` : 'Already in use';
            msg.className = 'q-ip-validation-msg q-ip-msg-taken';
            if (input) {
                input.classList.remove('q-input-ip-available', 'q-input-ip-invalid');
                input.classList.add('q-input-ip-taken');
            }
        } else {
            indicator.textContent = '\u2713';
            indicator.className = 'q-ip-validation-indicator q-ip-available';
            msg.textContent = 'Available';
            msg.className = 'q-ip-validation-msg q-ip-msg-available';
            if (input) {
                input.classList.remove('q-input-ip-taken', 'q-input-ip-invalid');
                input.classList.add('q-input-ip-available');
            }
        }
    }

    if (ipamData) {
        doCheck(ipamData);
        return;
    }

    fetch('/api/netbox/ipam')
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                indicator.textContent = '?';
                indicator.className = 'q-ip-validation-indicator q-ip-invalid';
                msg.textContent = 'Could not verify';
                msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
                return;
            }
            ipamData = data;
            doCheck(data);
        })
        .catch(() => {
            indicator.textContent = '?';
            indicator.className = 'q-ip-validation-indicator q-ip-invalid';
            msg.textContent = 'Could not verify';
            msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
        });
}
