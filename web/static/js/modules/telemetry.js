// Telemetry state and mission timer

export const telemetryState = {
    hostname: "",
    ip: "",
    vm_id: "",
    template: "",
    cpu: "",
    ram: "",
    disk: "",
    vm_type: "linux",
    acos_version: "",
    bridges: ["vmbr0"],
    lastStatus: "Idle",
    lastUpdated: null
};

let missionTimerInterval = null;
let missionStartTime = null;

export function startMissionTimer() {
    missionStartTime = Date.now();
    const el = document.getElementById("missionTimer");
    if (!el) return;

    if (missionTimerInterval) {
        clearInterval(missionTimerInterval);
    }

    missionTimerInterval = setInterval(() => {
        const diff = Date.now() - missionStartTime;
        const sec = Math.floor(diff / 1000);
        const mm = String(Math.floor(sec / 60)).padStart(2, "0");
        const ss = String(sec % 60).padStart(2, "0");
        el.textContent = `${mm}:${ss}`;
    }, 1000);
}

export function stopMissionTimer() {
    if (missionTimerInterval) {
        clearInterval(missionTimerInterval);
        missionTimerInterval = null;
    }
}

export function updateTelemetry() {
    const list = document.getElementById("telemetryList");
    if (!list) return;

    list.innerHTML = "";

    const rows = [
        ["Hostname", telemetryState.hostname],
        ["IP Address", telemetryState.ip],
        ["VM ID", telemetryState.vm_id],
        ["Workload Type", telemetryState.vm_type],
        ["Template", telemetryState.template],
        ["CPU / RAM / Disk",
            telemetryState.cpu && telemetryState.ram && telemetryState.disk
                ? `${telemetryState.cpu} vCPU \u00b7 ${telemetryState.ram} MB \u00b7 ${telemetryState.disk} GB`
                : ""],
        ["NICs", telemetryState.bridges ? telemetryState.bridges.join(", ") : "vmbr0"],
        ["Last Status", telemetryState.lastStatus],
        ["Last Updated", telemetryState.lastUpdated || "\u2014"]
    ];

    rows.forEach(([label, value]) => {
        if (value === "" || value == null) return;
        const li = document.createElement("li");

        const spanLabel = document.createElement("span");
        spanLabel.className = "q-telemetry-label";
        spanLabel.textContent = label;

        const spanValue = document.createElement("span");
        spanValue.className = "q-telemetry-value";
        spanValue.textContent = value;

        li.appendChild(spanLabel);
        li.appendChild(spanValue);
        list.appendChild(li);
    });
}

export function updateProxmoxStripFromVms(vms) {
    const badge = document.getElementById("proxmoxStatusBadge");
    const meta  = document.getElementById("proxmoxStatusMeta");
    if (!badge || !meta) return;

    if (!Array.isArray(vms) || vms.length === 0) {
        badge.className = "q-strip-badge q-strip-badge-unknown";
        badge.textContent = "no data";
        meta.textContent = "No VMs found in Terraform state.";
        return;
    }

    let running = 0;
    let stopped = 0;
    let errs    = 0;

    vms.forEach(vm => {
        const h = vm.health || {};
        const status = h.status || (h.running ? "running" : "unknown");

        if (status === "running") running++;
        else if (status === "stopped") stopped++;
        else if (status === "unreachable" || status === "error") errs++;
    });

    if (errs > 0) {
        badge.className = "q-strip-badge q-strip-badge-err";
        badge.textContent = "degraded";
        meta.textContent = `Terraform VMs: ${running} running, ${stopped} stopped, ${errs} unreachable`;
    } else if (running > 0) {
        badge.className = "q-strip-badge q-strip-badge-ok";
        badge.textContent = "healthy";
        meta.textContent = `Terraform VMs: ${running} running, ${stopped} stopped`;
    } else {
        badge.className = "q-strip-badge q-strip-badge-warn";
        badge.textContent = "idle";
        meta.textContent = `No Terraform VMs in state.`;
    }
}
