// VM list, create, destroy, protect, bridges, hostname

import { escapeHtml, showToast, getConfig } from '../utils/dom.js';
import { ansiToHtml } from '../utils/ansi.js';
import { confirmModal } from '../utils/modal.js';
import { validateSubnetIp } from '../utils/validation.js';
import { withBusy } from '../utils/busy.js';
import {
    telemetryState, startMissionTimer, stopMissionTimer,
    updateTelemetry, updateProxmoxStripFromVms
} from './telemetry.js';

let availableBridges = ["vmbr0"];
let fleetPollTimer = null;
let fleetActiveMode = 'quick'; // 'quick' or 'custom'
let _allTemplateOptionsHtml = ''; // cached unfiltered template options HTML

/* ============================
   VM Type → Template Filtering
   ============================ */

/**
 * Filter a template <select> to show only templates matching the given VM type.
 * Uses the data-type attribute on each option (set by server-side Jinja).
 * Falls back to name-based matching if data-type is missing.
 * @param {HTMLSelectElement} selectEl - The template dropdown to filter
 * @param {string} vmType - 'linux', 'vthunder', or 'vyos'
 * @param {boolean} autoSelect - Auto-select first matching template (default: true)
 */
export function filterTemplatesByType(selectEl, vmType, autoSelect = true) {
    if (!selectEl) return;
    selectEl.querySelectorAll('option').forEach(opt => {
        if (opt.value === '') { opt.style.display = 'block'; opt.disabled = false; return; }
        const optType = opt.dataset.type || classifyTemplateName(opt.value);
        const show = (optType === vmType);
        opt.style.display = show ? 'block' : 'none';
        opt.disabled = !show;
    });
    if (autoSelect) {
        const firstMatch = [...selectEl.options].find(o => o.value !== '' && !o.disabled);
        if (firstMatch) selectEl.value = firstMatch.value;
        else selectEl.value = '';
    }
}

/** Classify a template name into a VM type (fallback when data-type is missing). */
function classifyTemplateName(name) {
    const lower = name.toLowerCase();
    if (lower.startsWith('acos')) return 'vthunder';
    if (lower.startsWith('vyos')) return 'vyos';
    return 'linux';
}

/* ============================
   Task SSE Streaming (Redis pub/sub)
   ============================ */

function streamTask(streamUrl, termEl, onComplete) {
    /**
     * Connect to a Celery task's SSE stream and display output in the terminal.
     * Calls onComplete() when the task finishes.
     */
    const evtSource = new EventSource(streamUrl);

    evtSource.onmessage = (event) => {
        const line = event.data;
        if (line === '__COMPLETE__') {
            evtSource.close();
            if (onComplete) onComplete();
            return;
        }
        termEl.innerHTML += ansiToHtml(line + '\n');
        termEl.scrollTop = termEl.scrollHeight;
    };

    evtSource.onerror = () => {
        evtSource.close();
        termEl.innerHTML += ansiToHtml('[PERIMETER] Stream connection lost\n');
        if (onComplete) onComplete();
    };

    return evtSource;
}

/* ============================
   NIC/Bridge Management
   ============================ */

export function loadBridges() {
    const nodeSel = document.getElementById("node");
    const node = nodeSel ? nodeSel.value : (document.body.dataset.pmNode || "pve");

    fetch(`/api/network_bridges?node=${encodeURIComponent(node)}`)
        .then(r => r.json())
        .then(data => {
            if (data.bridges && data.bridges.length > 0) {
                availableBridges = data.bridges;
            } else {
                availableBridges = ["vmbr0"];
            }
            updateNicDropdowns();
            telemetryState.bridges = collectBridges();
            updateTelemetry();
        })
        .catch(() => {
            availableBridges = ["vmbr0"];
            updateNicDropdowns();
            telemetryState.bridges = ["vmbr0"];
            updateTelemetry();
        });
}

/* ============================
   Subnet Management
   ============================ */

let subnetConfigs = [];

export function loadSubnets() {
    fetch('/api/subnets')
        .then(r => r.json())
        .then(data => {
            subnetConfigs = data;
            const sel = document.getElementById('subnet');
            if (!sel) return;
            sel.innerHTML = '';
            data.forEach((s, i) => {
                const opt = document.createElement('option');
                opt.value = s.network;
                opt.textContent = s.network;
                sel.appendChild(opt);
            });
            updateIpPlaceholder();
        })
        .catch(() => {});
}

export function getSelectedSubnet() {
    const sel = document.getElementById('subnet');
    if (!sel) return null;
    return subnetConfigs.find(s => s.network === sel.value) || null;
}

function updateIpPlaceholder() {
    const input = document.getElementById('ip');
    const subnet = getSelectedSubnet();
    if (input && subnet) {
        const prefix = subnet.network.replace('.0/24', '.').replace('.0/16', '.');
        input.placeholder = `${prefix}x`;
    }
}

export function onSubnetChange() {
    updateIpPlaceholder();
    // Re-validate current IP if one is entered
    const ipInput = document.getElementById('ip');
    if (ipInput && ipInput.value) {
        ipInput.dispatchEvent(new Event('input'));
    }
}

export function collectBridges() {
    const bridges = [];
    const nicCount = parseInt(document.getElementById("nicCount")?.value || "1");
    for (let i = 0; i < nicCount; i++) {
        const select = document.getElementById(`bridge_${i}`);
        if (select) {
            bridges.push(select.value);
        }
    }
    return bridges.length > 0 ? bridges : ["vmbr0"];
}

export function updateNicDropdowns() {
    const nicCount = parseInt(document.getElementById("nicCount")?.value || "1");
    const container = document.getElementById("nicContainer");
    if (!container) return;

    const optionsHtml = availableBridges.map(b =>
        `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`
    ).join("");

    let html = "";
    for (let i = 0; i < nicCount; i++) {
        const label = i === 0 ? "NIC 1 (mgmt)" : `NIC ${i + 1} (data)`;
        html += `
            <div class="q-nic-row">
                <span class="q-nic-label">${label}</span>
                <select class="q-input q-nic-select" id="bridge_${i}">
                    ${optionsHtml}
                </select>
            </div>
        `;
    }

    container.innerHTML = html;

    for (let i = 0; i < nicCount; i++) {
        const select = document.getElementById(`bridge_${i}`);
        if (select && availableBridges.includes(availableBridges[0])) {
            select.value = availableBridges[Math.min(i, availableBridges.length - 1)];
        }
    }
}

/* ============================
   Hostname Generator
   ============================ */

export function generateHostname() {
    const input = document.getElementById("hostname");
    if (!input) return;

    const prefixes = ["pnode", "ptest", "plab", "phost", "pagent"];
    const prefix = prefixes[Math.floor(Math.random() * prefixes.length)];
    const num = String(Math.floor(Math.random() * 900) + 100);
    input.value = `${prefix}${num}`;

    telemetryState.hostname = input.value;
    updateTelemetry();
}

export function onVmTypeChange() {
    const vmType = document.getElementById("vm_type").value;
    const provisionFields = document.getElementById("provisionFields");
    const templateSel = document.getElementById("template");

    if (!vmType) {
        provisionFields.style.display = "none";
        return;
    }

    provisionFields.style.display = "block";

    if (!templateSel) return;

    telemetryState.vm_type = vmType;

    const resourceFields = document.getElementById("resourceFields");

    filterTemplatesByType(templateSel, vmType);

    if (vmType === "vthunder") {
        telemetryState.cpu = 8;
        telemetryState.ram = 16384;
        telemetryState.disk = 40;
        if (resourceFields) resourceFields.style.display = "none";
    } else if (vmType === "vyos") {
        telemetryState.cpu = 2;
        telemetryState.ram = 2048;
        telemetryState.disk = 8;
        if (resourceFields) resourceFields.style.display = "none";
    } else if (vmType === "linux") {
        telemetryState.cpu = 2;
        telemetryState.ram = 4096;
        telemetryState.disk = 32;
        if (resourceFields) resourceFields.style.display = "block";
    }

    updateTelemetry();
}

/* ============================
   Health Badge
   ============================ */

function healthBadge(health) {
    if (!health) {
        return "<span class='q-badge q-badge-grey' role='status'><span aria-hidden='true'>\u26aa</span> unknown</span>";
    }
    if (health.status === "running" || health.running === true) {
        return "<span class='q-badge q-badge-green' role='status'><span aria-hidden='true'>\ud83d\udfe2</span> operational</span>";
    }
    if (health.status === "unreachable" || health.status === "error") {
        return "<span class='q-badge q-badge-red' role='status'><span aria-hidden='true'>\ud83d\udd34</span> offline</span>";
    }
    if (health.status === "stopped") {
        return "<span class='q-badge q-badge-yellow' role='status'><span aria-hidden='true'>\ud83d\udfe1</span> stopped</span>";
    }
    return `<span class='q-badge q-badge-yellow' role='status'><span aria-hidden='true'>\ud83d\udfe1</span> ${escapeHtml(health.status || "degraded")}</span>`;
}

/* ============================
   VMID Validation (reusable)
   ============================ */

const vmIdTimers = new WeakMap();

/**
 * Validate a VMID input against the Proxmox API.
 * Can be called from any panel — just pass the input element.
 * Looks for sibling .q-vmid-indicator and .q-vmid-msg elements,
 * or falls back to the main panel's fixed IDs.
 */
export function checkVmIdAvailability(inputEl) {
    if (!inputEl) return;
    const container = inputEl.closest('.q-fleet-card') || inputEl.closest('#provisionFields') || document;
    const indicator = container.querySelector('.q-vmid-indicator') || document.getElementById('vmIdValidationIndicator');
    const msg = container.querySelector('.q-vmid-msg') || document.getElementById('vmIdValidationMsg');
    if (!indicator || !msg) return;

    const val = inputEl.value.trim();

    // Clear previous timer and results immediately
    const prevTimer = vmIdTimers.get(inputEl);
    if (prevTimer) clearTimeout(prevTimer);
    indicator.textContent = '';
    indicator.className = 'q-ip-validation-indicator';
    msg.textContent = '';
    msg.className = 'q-ip-validation-msg';

    if (!val) {
        indicator.textContent = '';
        indicator.className = 'q-ip-validation-indicator';
        msg.textContent = '';
        msg.className = 'q-ip-validation-msg';
        return;
    }

    const vmid = parseInt(val, 10);
    if (isNaN(vmid) || vmid < 100 || vmid > 99999) {
        indicator.textContent = '!';
        indicator.className = 'q-ip-validation-indicator q-ip-invalid';
        msg.textContent = 'VMID must be 100-99999';
        msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
        return;
    }

    indicator.textContent = '...';
    indicator.className = 'q-ip-validation-indicator';
    msg.textContent = '';

    vmIdTimers.set(inputEl, setTimeout(() => {
        const checkedVmid = vmid; // capture for staleness check
        fetch(`/api/check_vmid/${checkedVmid}`)
            .then(r => r.json())
            .then(data => {
                // Don't update if user has typed more since this check started
                if (parseInt(inputEl.value.trim(), 10) !== checkedVmid) return;
                if (data.available) {
                    indicator.textContent = '\u2713';
                    indicator.className = 'q-ip-validation-indicator q-ip-available';
                    msg.textContent = `VMID ${vmid} is available`;
                    msg.className = 'q-ip-validation-msg q-ip-msg-available';
                } else {
                    indicator.textContent = '\u2717';
                    indicator.className = 'q-ip-validation-indicator q-ip-taken';
                    msg.textContent = data.reason || `VMID ${vmid} is already in use`;
                    msg.className = 'q-ip-validation-msg q-ip-msg-taken';
                }
            })
            .catch(() => {
                indicator.textContent = '?';
                indicator.className = 'q-ip-validation-indicator';
                msg.textContent = 'Could not verify VMID';
                msg.className = 'q-ip-validation-msg';
            });
    }, 400));
}

/** Wrapper for the main VM provision panel's VMID input */
export function validateVmId() {
    const input = document.getElementById('vm_id');
    if (input) checkVmIdAvailability(input);
}

/* ============================
   IP Validation (reusable)
   ============================ */

const ipTimers = new WeakMap();

/**
 * Validate an IP input against the selected subnet and check availability.
 * Can be called from any panel — just pass the input element and an optional subnet value.
 * Looks for sibling .q-ip-indicator and .q-ip-msg elements.
 */
export function checkIpAvailability(inputEl, subnetValue) {
    if (!inputEl) return;
    const container = inputEl.closest('.q-fleet-card') || inputEl.closest('#provisionFields') || document;
    const indicator = container.querySelector('.q-ip-indicator') || document.getElementById('ipValidationIndicator');
    const msg = container.querySelector('.q-ip-msg') || document.getElementById('ipValidationMsg');
    if (!indicator || !msg) return;

    const val = inputEl.value.trim();

    const prevTimer = ipTimers.get(inputEl);
    if (prevTimer) clearTimeout(prevTimer);

    // Immediately clear previous results when input changes
    indicator.textContent = '';
    indicator.className = 'q-ip-validation-indicator';
    msg.textContent = '';
    msg.className = 'q-ip-validation-msg';
    inputEl.classList.remove('q-input-ip-available', 'q-input-ip-taken', 'q-input-ip-invalid');

    if (!val) {
        indicator.textContent = '';
        indicator.className = 'q-ip-validation-indicator';
        msg.textContent = '';
        msg.className = 'q-ip-validation-msg';
        inputEl.classList.remove('q-input-ip-available', 'q-input-ip-taken', 'q-input-ip-invalid');
        return;
    }

    // Determine subnet — from parameter, sibling select, or main panel
    const subnet = subnetValue
        || container.querySelector('[data-field="subnet"]')?.value
        || document.getElementById('subnet')?.value
        || getConfig('defaultSubnet');

    const result = validateSubnetIp(val, subnet);

    if (result.status === 'typing') return;
    if (result.status === 'invalid') {
        indicator.textContent = '!';
        indicator.className = 'q-ip-validation-indicator q-ip-invalid';
        msg.textContent = result.error;
        msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
        inputEl.classList.remove('q-input-ip-available', 'q-input-ip-taken');
        inputEl.classList.add('q-input-ip-invalid');
        return;
    }

    indicator.textContent = '...';
    indicator.className = 'q-ip-validation-indicator q-ip-checking';
    msg.textContent = '';

    ipTimers.set(inputEl, setTimeout(() => {
        const checkedVal = val; // capture for staleness check
        fetch('/api/netbox/ipam')
            .then(r => r.json())
            .then(data => {
                // Don't update if user has typed more since this check started
                if (inputEl.value.trim() !== checkedVal) return;
                if (data.error) {
                    indicator.textContent = '\u2713';
                    indicator.className = 'q-ip-validation-indicator q-ip-available';
                    msg.textContent = 'IPAM unavailable — assuming available';
                    msg.className = 'q-ip-validation-msg q-ip-msg-available';
                    return;
                }
                const found = (data.ips || []).find(item => item.address === checkedVal);
                if (found) {
                    indicator.textContent = '\u2715';
                    indicator.className = 'q-ip-validation-indicator q-ip-taken';
                    const who = found.hostname || found.dns_name || found.device || '';
                    msg.textContent = who ? `Taken by ${who}` : 'Already in use';
                    msg.className = 'q-ip-validation-msg q-ip-msg-taken';
                    inputEl.classList.remove('q-input-ip-available', 'q-input-ip-invalid');
                    inputEl.classList.add('q-input-ip-taken');
                } else {
                    indicator.textContent = '\u2713';
                    indicator.className = 'q-ip-validation-indicator q-ip-available';
                    msg.textContent = `${val} is available`;
                    msg.className = 'q-ip-validation-msg q-ip-msg-available';
                    inputEl.classList.remove('q-input-ip-taken', 'q-input-ip-invalid');
                    inputEl.classList.add('q-input-ip-available');
                }
            })
            .catch(() => {
                indicator.textContent = '\u2713';
                indicator.className = 'q-ip-validation-indicator q-ip-available';
                msg.textContent = 'IPAM unavailable — assuming available';
                msg.className = 'q-ip-validation-msg q-ip-msg-available';
            });
    }, 300));
}

/* ============================
   Provisioning Flow
   ============================ */

export function startDeploy() {
    const hostname = document.getElementById("hostname").value.trim();
    const ip       = document.getElementById("ip").value.trim();
    const vm_id    = document.getElementById("vm_id").value.trim();
    const templateSel = document.getElementById("template");
    const template = templateSel.value;
    const selectedOpt = templateSel.selectedOptions[0];
    const template_id = selectedOpt?.dataset?.vmid || "";
    const node     = document.getElementById("node").value;
    const vm_type  = document.getElementById("vm_type").value;
    const acos_version = (document.getElementById("acos_version") || {}).value || "";

    const bridges = collectBridges();

    const statusEl = document.getElementById("deployStatus");
    const termEl   = document.getElementById("terminalLog");

    if (!hostname || !ip || !vm_id) {
        statusEl.innerText = "Hostname, IP, and VM ID are required.";
        return;
    }
    if (!template_id) {
        statusEl.innerText = "Selected template has no VMID. Please re-select.";
        return;
    }

    const done = withBusy('start-deploy');

    let cpu, ram, disk;
    if (vm_type === "vthunder") {
        cpu = 8; ram = 16384; disk = 40;
    } else if (vm_type === "linux") {
        cpu = parseInt(document.getElementById("cpu")?.value) || 2;
        ram = parseInt(document.getElementById("ram")?.value) || 4096;
        disk = parseInt(document.getElementById("disk")?.value) || 32;
        if (cpu < 1 || cpu > 16) { statusEl.innerText = "CPU must be 1-16"; done(); return; }
        if (ram < 512 || ram > 65536) { statusEl.innerText = "RAM must be 512-65536 MB"; done(); return; }
        if (disk < 10 || disk > 500) { statusEl.innerText = "Disk must be 10-500 GB"; done(); return; }
    } else {
        cpu = 2; ram = 4096; disk = 32;
    }

    statusEl.innerText = "Initializing provisioning sequence...";
    if (termEl) termEl.innerText = "";

    telemetryState.hostname = hostname;
    telemetryState.ip       = ip;
    telemetryState.vm_id    = vm_id;
    telemetryState.template = template;
    telemetryState.cpu      = cpu;
    telemetryState.ram      = ram;
    telemetryState.disk     = disk;
    telemetryState.vm_type      = vm_type;
    telemetryState.acos_version = acos_version;
    telemetryState.bridges      = bridges;
    telemetryState.lastStatus = "Starting";
    telemetryState.lastUpdated = new Date().toLocaleString();
    updateTelemetry();
    startMissionTimer();

    fetch("/api/create_vm", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ hostname, ip, vm_id, template, template_id, cpu, ram, disk, node, vm_type, acos_version, bridges })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(body => {
                throw new Error(body.error || `HTTP ${response.status}`);
            }, () => {
                throw new Error(`HTTP ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        statusEl.innerText = "Provisioning in progress...";
        termEl.innerHTML += ansiToHtml(`[INFO] ${data.message}\n`);

        // Connect to SSE stream via Redis pub/sub
        streamTask(data.stream_url, termEl, () => {
            statusEl.innerText = "Provisioning complete";
            telemetryState.lastStatus = "Complete";
            telemetryState.lastUpdated = new Date().toLocaleString();
            updateTelemetry();
            stopMissionTimer();
            done();
            loadVmList();
            loadVmIds();
        });
    })
    .catch(err => {
        statusEl.innerText = "Error starting provisioning: " + err;
        telemetryState.lastStatus = "Error (startDeploy)";
        telemetryState.lastUpdated = new Date().toLocaleString();
        updateTelemetry();
        stopMissionTimer();
        done();
    });
}

export function pollStatus(jobId) {
    fetch(`/api/vm_status/${jobId}`)
        .then(r => r.json())
        .then(data => {
            const status = data.status || "Unknown";
            const log = data.log || [];

            const statusEl = document.getElementById("deployStatus");
            const term = document.getElementById("terminalLog");

            statusEl.innerText = "Status: " + status;
            if (term) {
                term.innerHTML = ansiToHtml(log.join("\n"));
                term.scrollTop = term.scrollHeight;
            }

            telemetryState.lastStatus = status;
            telemetryState.lastUpdated = new Date().toLocaleString();
            updateTelemetry();

            if (status === "Running" || status === "Starting") {
                setTimeout(() => pollStatus(jobId), 2000);
            } else {
                stopMissionTimer();
                loadVmList();
                loadVmIds();
            }
        })
        .catch(err => {
            document.getElementById("deployStatus").innerText = "Error polling status: " + err;
            telemetryState.lastStatus = "Error (pollStatus)";
            telemetryState.lastUpdated = new Date().toLocaleString();
            updateTelemetry();
            stopMissionTimer();
        });
}

/* ============================
   VM List + Actions
   ============================ */

export function loadVmList() {
    fetch("/api/list_vms")
        .then(r => r.json())
        .then(data => {
            const tbody = document.getElementById("vmTableBody");
            const vms   = data.vms   || [];
            const track = data.track || {};

            if (!tbody) return;
            tbody.innerHTML = "";

            if (vms.length === 0) {
                const tr = document.createElement("tr");
                const td = document.createElement("td");
                td.colSpan = 8;
                td.innerText = "No VMs currently tracked in Terraform state.";
                tr.appendChild(td);
                tbody.appendChild(tr);

                const assetCount = document.getElementById("qAssetsCount");
                if (assetCount) assetCount.textContent = "0 assets";

                updateProxmoxStripFromVms([]);
                return;
            }

            vms.forEach(vm => {
                const tr = document.createElement("tr");
                const ip = vm.ip || "";
                const isProtected = track[String(vm.vm_id)]?.protected || false;

                const firstSeenRaw = track[String(vm.vm_id)]?.first_seen;
                const lastSeenRaw  = track[String(vm.vm_id)]?.last_seen;

                const firstSeen = firstSeenRaw
                    ? new Date(firstSeenRaw * 1000).toLocaleString()
                    : "\u2014";
                const lastSeen = lastSeenRaw
                    ? new Date(lastSeenRaw * 1000).toLocaleString()
                    : "\u2014";

                const vmIdSafe = escapeHtml(vm.vm_id || "");
                const nameSafe = escapeHtml(vm.name || "");
                const group = vm.inventory_group || "";
                const isStaging = group.startsWith("staging_");
                const groupLabel = group ? escapeHtml(group) : '<span style="opacity:0.4">—</span>';
                const promoteBtn = isStaging
                    ? `<button class="q-button q-promote" data-action="promote" data-hostname="${nameSafe}" data-group="${escapeHtml(group)}" aria-label="Promote ${nameSafe}">Promote</button>`
                    : '';

                tr.innerHTML = `
                    <td>${vmIdSafe}</td>
                    <td>${nameSafe}</td>
                    <td>${escapeHtml(ip)}</td>
                    <td>${groupLabel}</td>
                    <td>${healthBadge(vm.health)}</td>
                    <td class="q-vm-actions">
                        <label class="q-protect-toggle" title="${isProtected ? 'VM is protected \u2014 disable to allow destruction' : 'Protect VM from destruction'}">
                            <input type="checkbox"
                                   ${isProtected ? 'checked' : ''}
                                   data-action="protect" data-vmid="${vmIdSafe}"
                                   aria-label="${isProtected ? 'Unprotect' : 'Protect'} VM ${vmIdSafe}">
                            <span class="q-protect-slider"></span>
                            <span class="q-protect-label">${isProtected ? '\u{1F512}' : '\u{1F513}'}</span>
                        </label>
                        ${promoteBtn}
                        <button class="q-button" data-action="bootstrap" data-vmid="${vmIdSafe}"
                                aria-label="Re-bootstrap VM ${vmIdSafe}">
                            Re-Bootstrap
                        </button>
                        <button class="q-button q-danger"
                                data-action="destroy" data-vmid="${vmIdSafe}"
                                ${isProtected ? 'disabled' : ''}
                                aria-label="Destroy VM ${vmIdSafe}">
                            Destroy
                        </button>
                    </td>
                    <td>${escapeHtml(firstSeen)}</td>
                    <td>${escapeHtml(lastSeen)}</td>
                `;

                tbody.appendChild(tr);
            });

            tbody.classList.add("q-refresh");
            setTimeout(() => tbody.classList.remove("q-refresh"), 500);

            const assetCount = document.getElementById("qAssetsCount");
            if (assetCount) {
                assetCount.textContent = `${vms.length} asset${vms.length === 1 ? '' : 's'}`;
            }

            // Update stat cards
            const running = vms.filter(v => v.health && (v.health.status === 'running' || v.health.running === true)).length;
            const stopped = vms.filter(v => !v.health || v.health.status === 'stopped' || v.health.status === 'unreachable' || v.health.status === 'error').length;
            const runEl = document.getElementById("assetRunningCount");
            const stopEl = document.getElementById("assetStoppedCount");
            const tplEl = document.getElementById("assetTemplateCount");
            if (runEl) runEl.textContent = running;
            if (stopEl) stopEl.textContent = stopped;
            if (tplEl) {
                const tplSelect = document.getElementById("template");
                tplEl.textContent = tplSelect ? tplSelect.options.length : '-';
            }

            updateProxmoxStripFromVms(vms);
        })
        .catch(err => {
            const tbody = document.getElementById("vmTableBody");
            if (tbody) {
                tbody.innerHTML = "";
                const tr = document.createElement("tr");
                const td = document.createElement("td");
                td.colSpan = 8;
                td.innerText = "Error loading VM list: " + err;
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
        });
}

export async function rerunBootstrap(vmid, triggerEl) {
    const ok = await confirmModal({
        title: 'Re-run Bootstrap',
        message: `Re-run bootstrap for VMID ${vmid}?`,
        confirm: 'Run Bootstrap',
        variant: 'warning'
    });
    if (!ok) return;

    const done = triggerEl ? withBusy(triggerEl) : () => {};
    const statusEl = document.getElementById("deployStatus");
    const term     = document.getElementById("terminalLog");

    statusEl.innerText = `Bootstrap re-run started for VMID ${vmid}\u2026`;
    if (term) term.innerText = "";

    fetch("/api/rerun_bootstrap", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ vm_id: vmid })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(body => { throw new Error(body.error || `HTTP ${response.status}`); });
        }
        return response.json();
    })
    .then(data => {
        term.innerHTML += ansiToHtml(`[INFO] ${data.message}\n`);
        streamTask(data.stream_url, term, () => {
            statusEl.innerText = `Bootstrap complete for VMID ${vmid}`;
            done();
        });
    })
    .catch(err => {
        statusEl.innerText = "Bootstrap error: " + err;
        done();
    });
}

export function toggleVmProtect(vmId, protect) {
    fetch('/api/vm/protect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({vm_id: vmId, protected: protect})
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) loadVmList();
    });
}

export async function destroyVm(vmId, triggerEl) {
    const ok = await confirmModal({
        title: 'Destroy Virtual Machine',
        message: `Are you sure you want to DESTROY VM ${vmId}? This cannot be undone.`,
        confirm: 'Destroy VM',
        variant: 'danger'
    });
    if (!ok) return;

    const done = triggerEl ? withBusy(triggerEl) : () => {};
    const term = document.getElementById("terminalLog");
    term.innerText = "";
    document.getElementById("deployStatus").innerText = `Destroying VM ${vmId}\u2026`;

    fetch("/api/destroy_vm", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({vm_id: vmId})
    })
    .then(response => {
        if (!response.ok) {
            return response.text().then(t => { throw new Error(t); });
        }
        return response.json();
    })
    .then(data => {
        term.innerHTML += ansiToHtml(`[INFO] ${data.message}\n`);
        streamTask(data.stream_url, term, () => {
            document.getElementById("deployStatus").innerText =
                `Destroy complete for VM ${vmId}`;
            done();
            loadVmList();
            loadVmIds();
        });
    })
    .catch(err => {
        term.innerHTML += ansiToHtml(`[ERROR] ${err.message}\n`);
        done();
    });
}

/* ============================
   Inventory Promote
   ============================ */

export function promoteVm(hostname) {
    const modal = document.getElementById('promoteModal');
    const select = document.getElementById('promoteGroupSelect');
    const nameEl = document.getElementById('promoteHostname');

    if (!modal || !select || !nameEl) return;

    nameEl.textContent = hostname;
    select.innerHTML = '<option value="">Loading groups…</option>';
    modal.style.display = 'flex';

    fetch('/api/inventory/groups')
        .then(r => r.json())
        .then(groups => {
            select.innerHTML = '';
            groups.forEach(g => {
                const opt = document.createElement('option');
                opt.value = g.name;
                opt.textContent = g.name;
                if (g.suggested_for && g.suggested_for.length > 0) {
                    opt.textContent += ` (suggested for ${g.suggested_for.join(', ')})`;
                }
                select.appendChild(opt);
            });
        });

    return new Promise(resolve => {
        function cleanup() {
            modal.style.display = 'none';
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onOverlay);
            document.removeEventListener('keydown', onKey);
        }

        const confirmBtn = modal.querySelector('[data-action="promote-confirm"]');
        const cancelBtn = modal.querySelector('[data-action="promote-cancel"]');

        function onConfirm() {
            const targetGroup = select.value;
            if (!targetGroup) return;
            cleanup();

            fetch('/api/inventory/promote', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({hostname, target_group: targetGroup})
            })
            .then(r => r.json())
            .then(data => {
                if (data.ok) {
                    showToast(`${hostname} promoted to ${data.to_group}`);
                    loadVmList();
                } else {
                    showToast(`Promote failed: ${data.error || 'unknown error'}`);
                }
            })
            .catch(err => showToast(`Promote error: ${err}`));

            resolve(true);
        }

        function onCancel() { cleanup(); resolve(false); }
        function onOverlay(e) { if (e.target === modal) { cleanup(); resolve(false); } }
        function onKey(e) { if (e.key === 'Escape') { cleanup(); resolve(false); } }

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('click', onOverlay);
        document.addEventListener('keydown', onKey);

        cancelBtn.focus();
    });
}


/* ============================
   VMID Sidebar
   ============================ */

export function loadVmIds() {
    fetch("/api/list_vmids")
        .then(r => r.json())
        .then(data => {
            const ul = document.getElementById("vmidList");
            if (!ul) return;
            ul.innerHTML = "";

            const vms = data.vms || [];
            const ids = data.vmids || [];

            if (vms.length === 0 && ids.length === 0) {
                const li = document.createElement("li");
                li.innerText = "No VMs found.";
                ul.appendChild(li);
                return;
            }

            if (vms.length > 0) {
                vms.forEach(vm => {
                    const li = document.createElement("li");
                    li.innerHTML = `<span class="vmid-num">${vm.vmid}</span>${vm.name || ""}`;
                    ul.appendChild(li);
                });
            } else {
                ids.forEach(id => {
                    const li = document.createElement("li");
                    li.innerText = id;
                    ul.appendChild(li);
                });
            }
        })
        .catch(() => {
            const ul = document.getElementById("vmidList");
            if (!ul) return;
            ul.innerHTML = "";
            const li = document.createElement("li");
            li.innerText = "Error loading VMIDs";
            ul.appendChild(li);
        });
}

/* ============================
   Fleet Deployment
   ============================ */

function getTemplateOptions() {
    // Return the cached unfiltered template HTML (captured before any filtering)
    if (_allTemplateOptionsHtml) return _allTemplateOptionsHtml;
    // Fallback: read from main select (may be filtered — last resort)
    const mainSel = document.getElementById('template');
    return mainSel ? mainSel.innerHTML : '';
}

/** Capture the full unfiltered template list from the main select at page load. */
export function cacheTemplateOptions() {
    const mainSel = document.getElementById('template');
    if (mainSel) _allTemplateOptionsHtml = mainSel.innerHTML;
}

function populateFleetDropdowns() {
    // Templates
    const templateHtml = getTemplateOptions();
    const quickTpl = document.getElementById('fleetQuickTemplate');
    if (quickTpl) quickTpl.innerHTML = templateHtml;

    // Subnets
    fetch('/api/subnets')
        .then(r => r.json())
        .then(data => {
            const subnetHtml = data.map(s =>
                `<option value="${escapeHtml(s.network)}">${escapeHtml(s.network)}</option>`
            ).join('');

            const quickSub = document.getElementById('fleetQuickSubnet');
            if (quickSub) quickSub.innerHTML = subnetHtml;
        })
        .catch(() => {});

    // Bridges
    const bridgeHtml = availableBridges.map(b =>
        `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`
    ).join('');
    const quickBr = document.getElementById('fleetQuickBridge');
    if (quickBr) quickBr.innerHTML = bridgeHtml;

    // Apply initial template filter based on current vm type
    fleetFilterTemplates();
}

// fleetActiveStreams defined near fleetToggleLog
let fleetCurrentFleetId = null;

export function openFleetDrawer() {
    // Toggle is handled by the accordion system in app.js
    // This is called when the drawer opens to populate dropdowns
    const drawer = document.getElementById('fleetDrawer');
    if (!drawer) return;
    if (drawer.classList.contains('open')) {
        populateFleetDropdowns();
        fleetUpdatePreview();
    }
}

export function resetFleetDrawer() {
    // Clear fleet state
    if (fleetPollTimer) {
        clearInterval(fleetPollTimer);
        fleetPollTimer = null;
    }
    // Close all active log streams
    if (typeof fleetActiveStreams !== 'undefined') {
        Object.values(fleetActiveStreams).forEach(s => { try { s.close(); } catch(e) {} });
        Object.keys(fleetActiveStreams).forEach(k => delete fleetActiveStreams[k]);
    }
    fleetCurrentFleetId = null;

    // Reset form inputs
    const nameInput = document.getElementById('fleetName');
    if (nameInput) nameInput.value = '';
    const customNameInput = document.getElementById('fleetCustomName');
    if (customNameInput) customNameInput.value = '';

    // Reset custom fleet cards
    const customRows = document.getElementById('fleetCustomRows');
    if (customRows) customRows.innerHTML = '';

    // Close flyout
    closeFleetFlyout();
    fleetSwitchMode('quick');
    fleetUpdatePreview();

    // Reset status elements for BOTH modes
    for (const id of ['fleetStatusContainer', 'fleetQuickStatusContainer']) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }
    for (const id of ['fleetQuickStatusDrawer']) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('open');
    }
    for (const id of ['fleetProgressFill', 'fleetQuickProgressFill']) {
        const el = document.getElementById(id);
        if (el) el.style.width = '0%';
    }
    for (const id of ['fleetStatusCounter', 'fleetQuickStatusCounter']) {
        const el = document.getElementById(id);
        if (el) el.textContent = '0/0';
    }
    for (const id of ['fleetStatusName', 'fleetQuickStatusName']) {
        const el = document.getElementById(id);
        if (el) el.textContent = '';
    }
    for (const id of ['fleetVmList', 'fleetQuickVmList']) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    }

    const badge = document.getElementById('fleetStatusBadge');
    if (badge) badge.textContent = 'Ready';
}

export function fleetSwitchMode(mode) {
    const quickTab = document.getElementById('fleetTabQuick');
    const customTab = document.getElementById('fleetTabCustom');
    const quickMode = document.getElementById('fleetQuickMode');
    const quickNameRow = document.getElementById('fleetQuickNameRow');

    if (mode === 'quick') {
        fleetActiveMode = 'quick';
        if (quickTab) { quickTab.style.background = '#06b6d4'; quickTab.style.color = '#0c1624'; quickTab.style.fontWeight = '700'; }
        if (customTab) { customTab.style.background = 'transparent'; customTab.style.color = '#94a3b8'; customTab.style.fontWeight = '400'; }
        if (quickMode) quickMode.style.display = 'block';
        if (quickNameRow) quickNameRow.style.display = 'block';
        closeFleetFlyout();
    } else {
        fleetActiveMode = 'custom';
        if (customTab) { customTab.style.background = '#06b6d4'; customTab.style.color = '#0c1624'; customTab.style.fontWeight = '700'; }
        if (quickTab) { quickTab.style.background = 'transparent'; quickTab.style.color = '#94a3b8'; quickTab.style.fontWeight = '400'; }
        if (quickMode) quickMode.style.display = 'none';
        if (quickNameRow) quickNameRow.style.display = 'none';
        openFleetFlyout();
    }
}

export function openFleetFlyout() {
    const overlay = document.getElementById('fleetFlyoutOverlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    populateFleetDropdowns();

    // Ensure at least one row exists
    const rows = document.getElementById('fleetCustomRows');
    if (rows && rows.children.length === 0) fleetAddRow();

    // Refresh bridges then repopulate templates + bridges in existing custom cards
    refreshFleetCardDropdowns();
}

function refreshFleetCardDropdowns() {
    const tplOptions = getTemplateOptions();
    const bridgeHtml = availableBridges.map(b =>
        `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`
    ).join('');
    document.querySelectorAll('#fleetCustomRows .q-fleet-card').forEach(card => {
        const tplSel = card.querySelector('[data-field="template"]');
        if (tplSel) {
            const currentVal = tplSel.value;
            tplSel.innerHTML = tplOptions;
            if (currentVal) tplSel.value = currentVal;
            const vmType = card.querySelector('[data-field="vm_type"]')?.value || 'linux';
            filterTemplatesByType(tplSel, vmType, !currentVal);
        }
        const brSel = card.querySelector('[data-field="bridge"]');
        if (brSel) {
            const currentVal = brSel.value;
            brSel.innerHTML = bridgeHtml;
            if (currentVal) brSel.value = currentVal;
        }
    });
}

export function closeFleetFlyout() {
    const overlay = document.getElementById('fleetFlyoutOverlay');
    if (overlay) overlay.style.display = 'none';
}

/** Filter fleet template dropdown based on selected VM type */
function fleetFilterTemplates() {
    const vmType = document.getElementById('fleetQuickVmType')?.value || 'linux';
    const templateSel = document.getElementById('fleetQuickTemplate');
    const resourceFields = document.getElementById('fleetQuickResourceFields');
    filterTemplatesByType(templateSel, vmType);
    if (resourceFields) {
        resourceFields.style.display = (vmType === 'vthunder' || vmType === 'vyos') ? 'none' : 'grid';
    }
}

export function fleetVmTypeChange() {
    fleetFilterTemplates();
    fleetUpdatePreview();
}

function buildCustomCardHtml(index) {
    const tplOptions = getTemplateOptions();
    const subnetSel = document.getElementById('fleetQuickSubnet');
    const defSub = getConfig('defaultSubnet');
    const subnetHtml = subnetSel ? subnetSel.innerHTML : (defSub ? `<option value="${defSub}">${defSub}</option>` : '');
    const bridgeHtml = availableBridges.map(b =>
        `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`
    ).join('');

    return `
        <div class="q-fleet-card-header">
            <span class="q-fleet-card-title">VM ${index}</span>
            <span style="margin-left:auto; display:flex; gap:4px;">
                <button type="button" class="q-mini-btn" data-action="fleet-duplicate-row">Duplicate</button>
                <button type="button" class="q-mini-btn" style="color:#f87171; border-color:rgba(239,68,68,0.5);" data-action="fleet-remove-row">Remove</button>
            </span>
        </div>
        <div class="q-fleet-card-body">
            <div class="q-fleet-card-row">
                <label>Type <select class="q-input" data-field="vm_type">
                    <option value="linux">Linux</option>
                    <option value="vthunder">vThunder</option>
                    <option value="vyos">VyOS</option>
                </select></label>
                <label>Template <select class="q-input" data-field="template">${tplOptions}</select></label>
                <label>Subnet <select class="q-input" data-field="subnet">${subnetHtml}</select></label>
                <label>Bridge <select class="q-input" data-field="bridge">${bridgeHtml}</select></label>
            </div>
            <div class="q-fleet-card-row">
                <label>Hostname <input type="text" class="q-input" placeholder="web01.home.klouda.co" data-field="hostname"></label>
                <label style="position:relative;">IP <input type="text" class="q-input" placeholder="10.1.55.x" data-field="ip">
                    <span class="q-ip-indicator q-ip-validation-indicator"></span>
                    <span class="q-ip-msg q-ip-validation-msg" style="display:block;"></span>
                </label>
                <label style="position:relative;">VMID <input type="number" class="q-input" placeholder="8000" data-field="vmid" style="width:80px;">
                    <span class="q-vmid-indicator q-ip-validation-indicator"></span>
                    <span class="q-vmid-msg q-ip-validation-msg" style="display:block;"></span>
                </label>
            </div>
            <div class="q-fleet-card-row q-fleet-resource-fields">
                <label>CPU <input type="number" class="q-input" value="2" data-field="cpu" style="width:60px;"></label>
                <label>RAM <input type="number" class="q-input" value="4096" data-field="ram" style="width:80px;"></label>
                <label>Disk <input type="number" class="q-input" value="32" data-field="disk" style="width:70px;"></label>
            </div>
        </div>
    `;
}

let fleetCardCounter = 0;

export function fleetAddRow() {
    const container = document.getElementById('fleetCustomRows');
    if (!container) return;
    fleetCardCounter++;
    const card = document.createElement('div');
    card.className = 'q-fleet-card';
    card.innerHTML = buildCustomCardHtml(fleetCardCounter);
    container.appendChild(card);
    _attachCardValidation(card);
    // Apply initial template filter (default type is Linux)
    const tplSel = card.querySelector('[data-field="template"]');
    const vmType = card.querySelector('[data-field="vm_type"]')?.value || 'linux';
    if (tplSel) filterTemplatesByType(tplSel, vmType);
}

function _attachCardValidation(card) {
    const ipInput = card.querySelector('[data-field="ip"]');
    const vmidInput = card.querySelector('[data-field="vmid"]');
    const vmTypeSelect = card.querySelector('[data-field="vm_type"]');
    if (ipInput) ipInput.addEventListener('input', () => checkIpAvailability(ipInput));
    if (vmidInput) vmidInput.addEventListener('input', () => checkVmIdAvailability(vmidInput));
    if (vmTypeSelect) {
        vmTypeSelect.addEventListener('change', () => {
            const vmType = vmTypeSelect.value || 'linux';
            const tplSel = card.querySelector('[data-field="template"]');
            const resourceRow = card.querySelector('.q-fleet-resource-fields');
            // Repopulate with full template list then filter
            if (tplSel) {
                tplSel.innerHTML = getTemplateOptions();
                filterTemplatesByType(tplSel, vmType);
            }
            if (resourceRow) {
                resourceRow.style.display = (vmType === 'vthunder' || vmType === 'vyos') ? 'none' : 'flex';
            }
        });
    }
}

export function fleetRemoveRow(el) {
    const card = el.closest('.q-fleet-card');
    if (card) card.remove();
}

export function fleetDuplicateRow(el) {
    const card = el.closest('.q-fleet-card');
    if (!card) return;
    const container = document.getElementById('fleetCustomRows');
    if (!container) return;
    fleetCardCounter++;
    const newCard = document.createElement('div');
    newCard.className = 'q-fleet-card';
    newCard.innerHTML = buildCustomCardHtml(fleetCardCounter);
    // Copy values from source card
    const srcInputs = card.querySelectorAll('input, select');
    const dstInputs = newCard.querySelectorAll('input, select');
    srcInputs.forEach((src, i) => {
        if (dstInputs[i]) dstInputs[i].value = src.value;
    });
    container.appendChild(newCard);
    _attachCardValidation(newCard);
    // Apply template filter based on duplicated card's VM type
    const tplSel = newCard.querySelector('[data-field="template"]');
    const vmType = newCard.querySelector('[data-field="vm_type"]')?.value || 'linux';
    if (tplSel) filterTemplatesByType(tplSel, vmType, false);
}

export function fleetUpdatePreview() {
    const tbody = document.getElementById('fleetQuickPreview');
    if (!tbody) return;

    const count = parseInt(document.getElementById('fleetQuickCount')?.value) || 3;
    const baseHostname = document.getElementById('fleetQuickHostname')?.value || 'vm';
    const startIp = parseInt(document.getElementById('fleetQuickStartIp')?.value) || 100;
    const startVmid = parseInt(document.getElementById('fleetQuickStartVmid')?.value) || 8000;
    const cpu = document.getElementById('fleetQuickCpu')?.value || '2';
    const ram = document.getElementById('fleetQuickRam')?.value || '4096';
    const disk = document.getElementById('fleetQuickDisk')?.value || '32';

    const subnetSel = document.getElementById('fleetQuickSubnet');
    const subnet = subnetSel ? subnetSel.value : getConfig('defaultSubnet');
    const prefix = subnet ? subnet.split('/')[0].replace(/\.0$/, '.') : '';

    let html = '';
    for (let i = 0; i < Math.min(count, 20); i++) {
        const hostname = baseHostname.includes('.')
            ? `${baseHostname.split('.')[0]}${String(i + 1).padStart(2, '0')}.${baseHostname.split('.').slice(1).join('.')}`
            : `${baseHostname}${String(i + 1).padStart(2, '0')}`;
        const ip = `${prefix}${startIp + i}`;
        const vmid = startVmid + i;
        html += `<tr>
            <td>${escapeHtml(hostname)}</td>
            <td>${escapeHtml(ip)}</td>
            <td>${vmid}</td>
            <td>${escapeHtml(cpu)}</td>
            <td>${escapeHtml(ram)}</td>
            <td>${escapeHtml(disk)}</td>
        </tr>`;
    }
    tbody.innerHTML = html;
}

function collectQuickFleetVms() {
    const count = parseInt(document.getElementById('fleetQuickCount')?.value) || 3;
    const baseHostname = document.getElementById('fleetQuickHostname')?.value || 'vm';
    const startIp = parseInt(document.getElementById('fleetQuickStartIp')?.value) || 100;
    const startVmid = parseInt(document.getElementById('fleetQuickStartVmid')?.value) || 8000;
    const cpu = parseInt(document.getElementById('fleetQuickCpu')?.value) || 2;
    const ram = parseInt(document.getElementById('fleetQuickRam')?.value) || 4096;
    const disk = parseInt(document.getElementById('fleetQuickDisk')?.value) || 32;
    const templateSel = document.getElementById('fleetQuickTemplate');
    const template = templateSel?.value || '';
    const templateOpt = templateSel?.selectedOptions[0];
    const template_id = templateOpt?.dataset?.vmid || '';
    const vm_type = document.getElementById('fleetQuickVmType')?.value || 'linux';
    const subnetSel = document.getElementById('fleetQuickSubnet');
    const subnet = subnetSel?.value || getConfig('defaultSubnet');
    const prefix = subnet.split('/')[0].replace(/\.0$/, '.');
    const bridge = document.getElementById('fleetQuickBridge')?.value || 'vmbr0';

    const vms = [];
    for (let i = 0; i < Math.min(count, 20); i++) {
        vms.push({
            hostname: baseHostname.includes('.')
                ? `${baseHostname.split('.')[0]}${String(i + 1).padStart(2, '0')}.${baseHostname.split('.').slice(1).join('.')}`
                : `${baseHostname}${String(i + 1).padStart(2, '0')}`,
            ip: `${prefix}${startIp + i}`,
            vm_id: String(startVmid + i),
            template,
            template_id,
            vm_type,
            cpu, ram, disk,
            bridges: [bridge],
            node: document.body.dataset.pmNode || 'pve'
        });
    }
    return vms;
}

function collectCustomFleetVms() {
    const cards = document.querySelectorAll('#fleetCustomRows .q-fleet-card');
    const vms = [];
    cards.forEach(card => {
        const get = (field) => card.querySelector(`[data-field="${field}"]`)?.value || '';
        const templateEl = card.querySelector('[data-field="template"]');
        const templateOpt = templateEl?.selectedOptions[0];
        vms.push({
            hostname: get('hostname'),
            ip: get('ip'),
            vm_id: get('vmid'),
            template: get('template'),
            template_id: templateOpt?.dataset?.vmid || '',
            vm_type: get('vm_type'),
            cpu: parseInt(get('cpu')) || 2,
            ram: parseInt(get('ram')) || 4096,
            disk: parseInt(get('disk')) || 32,
            bridges: [get('bridge') || 'vmbr0'],
            subnet: get('subnet'),
            node: document.body.dataset.pmNode || 'pve'
        });
    });
    return vms;
}

export function deployFleet() {
    const isQuick = fleetActiveMode === 'quick';
    const fleetName = isQuick
        ? (document.getElementById('fleetName')?.value?.trim() || '')
        : (document.getElementById('fleetCustomName')?.value?.trim() || '');

    const vms = isQuick ? collectQuickFleetVms() : collectCustomFleetVms();

    if (vms.length === 0) {
        showToast('No VMs configured for fleet deployment');
        return;
    }

    // Validate
    for (const vm of vms) {
        if (!vm.hostname || !vm.ip || !vm.vm_id) {
            showToast(`Missing hostname, IP, or VMID for one or more VMs`);
            return;
        }
        if (!vm.template_id) {
            showToast(`No template selected for ${vm.hostname}`);
            return;
        }
    }

    const payload = {
        fleet_name: fleetName || `fleet-${Date.now()}`,
        vms
    };

    fetch('/api/fleet/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => {
        if (!r.ok) return r.json().then(b => { throw new Error(b.error || `HTTP ${r.status}`); });
        return r.json();
    })
    .then(data => {
        showToast(`Fleet "${payload.fleet_name}" deployment started`);

        // Log to the main live terminal
        const termEl = document.getElementById('terminalLog');
        if (termEl) {
            const vmCount = (data.tasks || []).length;
            termEl.innerHTML += ansiToHtml(`[INFO] Fleet "${payload.fleet_name}" deployment started (${vmCount} VMs)\n`);
            termEl.innerHTML += ansiToHtml(`[INFO] Track progress in the Fleet Deployment drawer\n`);
            termEl.scrollTop = termEl.scrollHeight;
        }

        // Update status badge on the main drawer toggle
        const badge = document.getElementById('fleetStatusBadge');
        if (badge) badge.textContent = 'Deploying';

        // Store fleet ID
        fleetCurrentFleetId = data.fleet_id;

        // Determine which status elements to use based on active mode
        const statusContainerId = isQuick ? 'fleetQuickStatusContainer' : 'fleetStatusContainer';
        const statusNameId = isQuick ? 'fleetQuickStatusName' : 'fleetStatusName';
        const vmListId = isQuick ? 'fleetQuickVmList' : 'fleetVmList';

        // Show the fleet status container
        const statusContainer = document.getElementById(statusContainerId);
        if (statusContainer) statusContainer.style.display = 'block';

        const statusName = document.getElementById(statusNameId);
        if (statusName) statusName.textContent = payload.fleet_name;

        // For quick mode, auto-expand the sub-drawer
        if (isQuick) {
            const statusDrawer = document.getElementById('fleetQuickStatusDrawer');
            if (statusDrawer) statusDrawer.classList.add('open');
            const statusBtn = document.querySelector('[data-action="toggle-fleet-status"]');
            if (statusBtn) statusBtn.setAttribute('aria-expanded', 'true');
        }

        // Build VM rows with inline log panels
        const vmList = document.getElementById(vmListId);
        if (vmList) {
            vmList.innerHTML = '';
            (data.tasks || []).forEach(t => {
                const row = document.createElement('div');
                row.className = 'q-fleet-vm-row';
                row.id = `fleet-vm-${t.task_id}`;
                row.innerHTML = `
                    <div class="q-fleet-vm-header" data-action="fleet-toggle-log" data-task-id="${t.task_id}">
                        <span class="q-fleet-vm-name">${escapeHtml(t.hostname || '')}</span>
                        <span class="q-fleet-vm-ip">${escapeHtml(t.ip || '')}</span>
                        <span class="q-badge q-badge-grey" id="fleet-badge-${t.task_id}">queued</span>
                    </div>
                    <div class="q-fleet-vm-log" style="display:none;">
                        <pre class="q-fleet-log-output"></pre>
                    </div>
                `;
                vmList.appendChild(row);
            });
        }

        // Reset progress bar
        const fillId = isQuick ? 'fleetQuickProgressFill' : 'fleetProgressFill';
        const fill = document.getElementById(fillId);
        if (fill) fill.style.width = '0%';

        const counterId = isQuick ? 'fleetQuickStatusCounter' : 'fleetStatusCounter';
        const counter = document.getElementById(counterId);
        if (counter) counter.textContent = `0/${(data.tasks || []).length}`;

        // Start polling
        fleetPollStatus(data.fleet_id);

        // Auto-open log for the first task
        const firstTask = (data.tasks || [])[0];
        if (firstTask) {
            fleetToggleLog(firstTask.task_id);
        }
    })
    .catch(err => {
        showToast(`Fleet deploy error: ${err.message}`);
    });
}

export function fleetPollStatus(fleetId) {
    if (fleetPollTimer) clearInterval(fleetPollTimer);
    console.log('[FLEET] Starting poll for fleet:', fleetId);

    function poll() {
        console.log('[FLEET] Polling fleet status:', fleetId);
        fetch(`/api/fleet/${fleetId}`)
            .then(r => r.json())
            .then(data => {
                console.log('[FLEET] Poll response:', JSON.stringify(data));
                const tasks = data.vms || data.tasks || [];
                let done = 0;
                let total = tasks.length;

                tasks.forEach(t => {
                    const badgeEl = document.getElementById(`fleet-badge-${t.task_id}`);
                    if (!badgeEl) return;

                    let badgeClass = 'q-badge-grey';
                    let badgeText = t.status;

                    switch (t.status) {
                        case 'queued':
                        case 'PENDING':
                            badgeClass = 'q-badge-grey';
                            badgeText = 'queued';
                            break;
                        case 'running':
                        case 'STARTED':
                            badgeClass = 'q-badge-yellow';
                            badgeText = 'running';
                            break;
                        case 'success':
                        case 'done':
                        case 'complete':
                        case 'SUCCESS':
                            badgeClass = 'q-badge-green';
                            badgeText = 'done';
                            done++;
                            break;
                        case 'failed':
                        case 'error':
                        case 'FAILURE':
                            badgeClass = 'q-badge-red';
                            badgeText = 'failed';
                            done++;
                            break;
                        case 'cancelled':
                        case 'REVOKED':
                            badgeClass = 'q-badge-orange';
                            badgeText = 'cancelled';
                            done++;
                            break;
                        default:
                            badgeText = t.status;
                    }
                    badgeEl.className = `q-badge ${badgeClass}`;
                    badgeEl.textContent = badgeText;

                    // Hide cancel button for terminal states
                    if (['done', 'success', 'complete', 'SUCCESS', 'failed', 'error', 'FAILURE', 'cancelled', 'REVOKED'].includes(t.status)) {
                        const row = document.getElementById(`fleet-vm-${t.task_id}`);
                        if (row) {
                            const cancelBtn = row.querySelector('[data-action="fleet-cancel-task"]');
                            if (cancelBtn) cancelBtn.style.display = 'none';
                        }
                    }
                });

                // Update progress counter (mode-aware)
                const counterId = fleetActiveMode === 'quick' ? 'fleetQuickStatusCounter' : 'fleetStatusCounter';
                const counter = document.getElementById(counterId);
                if (counter) counter.textContent = `${done}/${total}`;

                // Update progress bar (mode-aware)
                const fillId = fleetActiveMode === 'quick' ? 'fleetQuickProgressFill' : 'fleetProgressFill';
                const fill = document.getElementById(fillId);
                if (fill && total > 0) {
                    fill.style.width = `${Math.round((done / total) * 100)}%`;
                }

                // Update main drawer badge
                const badge = document.getElementById('fleetStatusBadge');

                if (done >= total) {
                    clearInterval(fleetPollTimer);
                    fleetPollTimer = null;
                    if (badge) badge.textContent = 'Complete';
                    // Refresh VM list after fleet completes
                    loadVmList();
                    loadVmIds();
                }
            })
            .catch(err => {
                console.error('[FLEET] Poll error:', err);
            });
    }

    poll();
    fleetPollTimer = setInterval(poll, 3000);
}

export function toggleFleetStatus() {
    const drawer = document.getElementById('fleetQuickStatusDrawer');
    if (!drawer) return;
    drawer.classList.toggle('open');
    const btn = document.querySelector('[data-action="toggle-fleet-status"]');
    if (btn) btn.setAttribute('aria-expanded', drawer.classList.contains('open'));
}

const fleetActiveStreams = {};

export function fleetToggleLog(taskId) {
    const row = document.getElementById(`fleet-vm-${taskId}`);
    if (!row) return;

    const logPanel = row.querySelector('.q-fleet-vm-log');
    const term = row.querySelector('.q-fleet-log-output');
    if (!logPanel || !term) return;

    // If already open, close it
    if (logPanel.style.display === 'block') {
        logPanel.style.display = 'none';
        if (fleetActiveStreams[taskId]) {
            fleetActiveStreams[taskId].close();
            delete fleetActiveStreams[taskId];
        }
        return;
    }

    // Open and connect SSE stream
    logPanel.style.display = 'block';
    term.innerHTML = '';

    const streamUrl = `/api/tasks/${taskId}/stream`;
    fleetActiveStreams[taskId] = streamTask(streamUrl, term, () => {
        term.innerHTML += ansiToHtml('[PERIMETER] Stream ended\n');
        delete fleetActiveStreams[taskId];
    });
}

export function fleetCancel(fleetId) {
    const id = fleetId || fleetCurrentFleetId;
    if (!id) { showToast('No active fleet to cancel'); return; }
    fetch(`/api/fleet/${id}/cancel`, {
        method: 'POST'
    })
    .then(r => r.json())
    .then(data => {
        showToast(data.message || 'Fleet cancellation requested');
    })
    .catch(err => showToast(`Cancel error: ${err.message}`));
}

export function fleetCancelTask(taskId) {
    fetch(`/api/tasks/${taskId}/cancel`, {
        method: 'POST'
    })
    .then(r => r.json())
    .then(data => {
        showToast(data.cancelled ? 'Task cancelled' : 'Cancel requested');
        // Immediately update the badge
        const badgeEl = document.getElementById(`fleet-badge-${taskId}`);
        if (badgeEl) {
            badgeEl.className = 'q-badge q-badge-orange';
            badgeEl.textContent = 'cancelled';
        }
        // Hide the cancel button
        const row = document.getElementById(`fleet-vm-${taskId}`);
        if (row) {
            const cancelBtn = row.querySelector('[data-action="fleet-cancel-task"]');
            if (cancelBtn) cancelBtn.style.display = 'none';
        }
    })
    .catch(err => showToast(`Cancel error: ${err.message}`));
}
