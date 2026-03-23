// VM list, create, destroy, protect, bridges, hostname

import { escapeHtml, showToast } from '../utils/dom.js';
import { ansiToHtml } from '../utils/ansi.js';
import { confirmModal } from '../utils/modal.js';
import { withBusy } from '../utils/busy.js';
import {
    telemetryState, startMissionTimer, stopMissionTimer,
    updateTelemetry, updateProxmoxStripFromVms
} from './telemetry.js';

let availableBridges = ["vmbr0"];

/* ============================
   NIC/Bridge Management
   ============================ */

export function loadBridges() {
    const nodeSel = document.getElementById("node");
    const node = nodeSel ? nodeSel.value : "goldfinger";

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

    const allOptions = templateSel.querySelectorAll("option");

    const resourceFields = document.getElementById("resourceFields");

    if (vmType === "vthunder") {
        allOptions.forEach(opt => {
            if (opt.value === "") {
                opt.style.display = "block";
            } else if (opt.value.startsWith("acos")) {
                opt.style.display = "block";
                opt.disabled = false;
            } else {
                opt.style.display = "none";
                opt.disabled = true;
            }
        });

        telemetryState.cpu = 8;
        telemetryState.ram = 16384;
        telemetryState.disk = 40;
        if (resourceFields) resourceFields.style.display = "none";

        const firstMatch = [...allOptions].find(o => o.value !== "" && o.value.startsWith("acos"));
        if (firstMatch) templateSel.value = firstMatch.value;

    } else if (vmType === "vyos") {
        allOptions.forEach(opt => {
            if (opt.value === "") {
                opt.style.display = "block";
            } else if (opt.value.startsWith("vyos")) {
                opt.style.display = "block";
                opt.disabled = false;
            } else {
                opt.style.display = "none";
                opt.disabled = true;
            }
        });

        telemetryState.cpu = 2;
        telemetryState.ram = 2048;
        telemetryState.disk = 8;
        if (resourceFields) resourceFields.style.display = "none";

        const firstMatch = [...allOptions].find(o => o.value !== "" && o.value.startsWith("vyos"));
        if (firstMatch) templateSel.value = firstMatch.value;

    } else if (vmType === "linux") {
        allOptions.forEach(opt => {
            if (opt.value === "") {
                opt.style.display = "block";
            } else if (!opt.value.startsWith("acos") && !opt.value.startsWith("vyos")) {
                opt.style.display = "block";
                opt.disabled = false;
            } else {
                opt.style.display = "none";
                opt.disabled = true;
            }
        });

        telemetryState.cpu = 2;
        telemetryState.ram = 4096;
        telemetryState.disk = 32;
        if (resourceFields) resourceFields.style.display = "block";

        const firstMatch = [...allOptions].find(o =>
            o.value !== "" && !o.value.startsWith("acos") && !o.value.startsWith("vyos")
        );
        if (firstMatch) templateSel.value = firstMatch.value;
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
   VMID Validation
   ============================ */

let vmIdValidateTimer = null;

export function validateVmId() {
    const input = document.getElementById('vm_id');
    const indicator = document.getElementById('vmIdValidationIndicator');
    const msg = document.getElementById('vmIdValidationMsg');
    if (!input || !indicator || !msg) return;

    const val = input.value.trim();

    if (vmIdValidateTimer) clearTimeout(vmIdValidateTimer);

    if (!val) {
        indicator.textContent = '';
        indicator.className = 'q-ip-validation-indicator';
        msg.textContent = '';
        msg.className = 'q-ip-validation-msg';
        return;
    }

    const vmid = parseInt(val, 10);
    if (isNaN(vmid) || vmid < 100 || vmid > 9999) {
        indicator.textContent = '!';
        indicator.className = 'q-ip-validation-indicator q-ip-invalid';
        msg.textContent = 'VMID must be 100-9999';
        msg.className = 'q-ip-validation-msg q-ip-msg-invalid';
        return;
    }

    indicator.textContent = '...';
    indicator.className = 'q-ip-validation-indicator';
    msg.textContent = '';

    vmIdValidateTimer = setTimeout(() => {
        fetch(`/api/check_vmid/${vmid}`)
            .then(r => r.json())
            .then(data => {
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
    }, 400);
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

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");

        statusEl.innerText = "Provisioning in progress...";

        function readChunk() {
            reader.read().then(({done: streamDone, value}) => {
                if (streamDone) {
                    statusEl.innerText = "Provisioning complete";
                    telemetryState.lastStatus = "Complete";
                    telemetryState.lastUpdated = new Date().toLocaleString();
                    updateTelemetry();
                    stopMissionTimer();
                    done();
                    loadVmList();
                    loadVmIds();
                    return;
                }

                const chunk = decoder.decode(value, {stream: true});
                termEl.innerHTML += ansiToHtml(chunk);
                termEl.scrollTop = termEl.scrollHeight;

                telemetryState.lastStatus = "Running";
                telemetryState.lastUpdated = new Date().toLocaleString();
                updateTelemetry();

                readChunk();
            });
        }

        readChunk();
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
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");

        function readChunk() {
            reader.read().then(({done: streamDone, value}) => {
                if (streamDone) {
                    statusEl.innerText = `Bootstrap complete for VMID ${vmid}`;
                    done();
                    return;
                }

                const chunk = decoder.decode(value, {stream:true});
                term.innerHTML += ansiToHtml(chunk);
                term.scrollTop = term.scrollHeight;

                readChunk();
            });
        }

        readChunk();
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
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");

        function readChunk() {
            reader.read().then(({done: streamDone, value}) => {
                if (streamDone) {
                    document.getElementById("deployStatus").innerText =
                        `Destroy complete for VM ${vmId}`;
                    done();
                    loadVmList();
                    loadVmIds();
                    return;
                }

                const chunk = decoder.decode(value, {stream:true});
                term.innerHTML += ansiToHtml(chunk);
                term.scrollTop = term.scrollHeight;

                readChunk();
            });
        }

        readChunk();
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
                    li.innerText = `${vm.vmid} \u2014 ${vm.name || ""}`;
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
