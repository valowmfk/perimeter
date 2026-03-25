// Shared vThunder cascade loaders — group → host → partition
// Used by VIP Manager (vthunder.js), cert creation, and cert deploy (certificates.js).

import { showToast, appendLogLine } from './dom.js';

/**
 * Load vThunder groups from inventory into a <select>.
 * @param {string} groupSelectId  — ID of the group <select>
 * @param {Object} [opts]
 * @param {boolean} [opts.log=false] — append log lines to the live console
 */
export function loadVthunderGroups(groupSelectId, { log = false } = {}) {
    const el = document.getElementById(groupSelectId);
    if (!el) return;
    el.innerHTML = '<option value="">-- Select Group --</option>';

    fetch('/inventory/groups?file=inventory.yml')
        .then(r => r.json())
        .then(groups => {
            const filtered = (groups || []).filter(g => g.toLowerCase().includes('vthunder'));
            filtered.forEach(g => {
                const opt = document.createElement('option');
                opt.value = g;
                opt.textContent = g;
                el.appendChild(opt);
            });
            if (log) appendLogLine(`[INFO] Loaded ${filtered.length} vThunder inventory groups`);
        })
        .catch(err => {
            if (log) {
                appendLogLine(`[ERROR] Failed to load vThunder groups: ${err.message}`);
                showToast(`Error loading vThunder groups: ${err.message}`);
            }
        });
}

/**
 * Load vThunder hosts for a selected group, resetting host + partition selects.
 * @param {string} groupSelectId     — ID of the group <select>
 * @param {string} hostSelectId      — ID of the host <select>
 * @param {string} partitionSelectId — ID of the partition <select>
 * @param {Object} [opts]
 * @param {boolean} [opts.log=false] — append log lines to the live console
 */
export function loadVthunderHosts(groupSelectId, hostSelectId, partitionSelectId, { log = false } = {}) {
    const group = document.getElementById(groupSelectId)?.value;
    const hostEl = document.getElementById(hostSelectId);
    const partEl = document.getElementById(partitionSelectId);

    if (hostEl) hostEl.innerHTML = '<option value="">-- Select Host --</option>';
    if (partEl) partEl.innerHTML = '<option value="">-- Select Partition --</option>';
    if (!group) return;

    if (log) appendLogLine(`[INFO] Loading vThunder hosts from group: ${group}`);

    fetch('/api/vthunder/hosts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group })
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            if (log) appendLogLine(`[ERROR] ${data.error}`);
            showToast(data.error);
            return;
        }
        (data.hosts || []).forEach(h => {
            const opt = document.createElement('option');
            opt.value = h;
            opt.textContent = h;
            hostEl.appendChild(opt);
        });
        if (log) appendLogLine(`[INFO] Loaded ${(data.hosts || []).length} vThunder host(s)`);
    })
    .catch(err => {
        if (log) {
            appendLogLine(`[ERROR] Failed to load hosts: ${err.message}`);
            showToast(`Error loading hosts: ${err.message}`);
        }
    });
}

/**
 * Load vThunder partitions for a selected group + host, resetting partition select.
 * @param {string} groupSelectId     — ID of the group <select>
 * @param {string} hostSelectId      — ID of the host <select>
 * @param {string} partitionSelectId — ID of the partition <select>
 * @param {Object} [opts]
 * @param {boolean} [opts.log=false]    — append log lines
 * @param {boolean} [opts.showId=true]  — show "(ID: N)" after partition name
 */
export function loadVthunderPartitions(groupSelectId, hostSelectId, partitionSelectId, { log = false, showId = true } = {}) {
    const group = document.getElementById(groupSelectId)?.value;
    const host = document.getElementById(hostSelectId)?.value;
    const partEl = document.getElementById(partitionSelectId);
    if (!partEl) return;

    partEl.innerHTML = '<option value="">-- Select Partition --</option>';
    if (!group || !host) return;

    if (log) appendLogLine(`[INFO] Connecting to ${host} to retrieve partitions...`);

    fetch('/api/vthunder/partitions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inventory_file: 'inventory.yml', group_name: group, host })
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            if (log) appendLogLine(`[ERROR] ${data.error}`);
            showToast(data.error);
            return;
        }
        (data.partitions || []).forEach(p => {
            const name = p['partition-name'] || p.name || '';
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = showId ? `${name} (ID: ${p.id})` : name;
            partEl.appendChild(opt);
        });
        if (log) appendLogLine(`[SUCCESS] Retrieved ${(data.partitions || []).length} partition(s) from ${host}`);
    })
    .catch(err => {
        partEl.innerHTML = '<option value="">-- Select Partition --</option>';
        if (log) {
            appendLogLine(`[ERROR] Failed to load partitions: ${err.message}`);
            showToast(`Error loading partitions: ${err.message}`);
        }
    });
}
