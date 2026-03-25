// Playbook loading and execution

import { showToast, appendLogLine } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';

export function loadPlaybook() {
    const file = document.getElementById("playbookSelect").value;
    if (!file) return;
    fetch(`/view?file=${encodeURIComponent(file)}`)
        .then(resp => resp.text())
        .then(text => {
            const codeBlock = document.getElementById("playbookView");
            codeBlock.textContent = text;
            codeBlock.removeAttribute('data-highlighted');
            codeBlock.className = 'yaml';
            hljs.highlightElement(codeBlock);
        });
}

export function runPlaybook() {
    const file = document.getElementById("playbookSelect").value;
    const inventory = document.getElementById("inventorySelect").value;
    const verbosity = document.getElementById("verbositySelect").value;
    const group = document.getElementById("groupSelect").value;

    if (!file || !inventory) {
        showToast("Select a playbook and inventory first");
        return;
    }

    const done = withBusy('run-playbook');

    const statusEl = document.getElementById("status");
    const spinnerEl = document.getElementById("spinner");
    const outputEl = document.getElementById("output");

    if (statusEl) statusEl.textContent = "Running playbook...";
    if (spinnerEl) spinnerEl.style.display = "block";
    if (outputEl) outputEl.innerHTML = "";

    fetch('/run', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({file, inventory, verbosity, group})
    })
    .then(resp => {
        if (!resp.ok) {
            return resp.json().then(err => {
                throw new Error(err.error || `HTTP ${resp.status}`);
            });
        }
        return resp.json();
    })
    .then(data => {
        const session = data.session_id;
        const eventSource = new EventSource(`/stream/${session}`);

        eventSource.onmessage = (event) => {
            if (event.data === "__COMPLETE__") {
                eventSource.close();
                done();
                const spinner = document.getElementById("spinner");
                const status = document.getElementById("status");
                if (spinner) spinner.style.display = "none";
                if (status) status.textContent = "";
            } else {
                appendLogLine(event.data);
            }
        };

        eventSource.onerror = () => {
            eventSource.close();
            done();
            const spinner = document.getElementById("spinner");
            const status = document.getElementById("status");
            if (spinner) spinner.style.display = "none";
            if (status) status.textContent = "";
            showToast("Error streaming playbook output");
        };
    })
    .catch(err => {
        done();
        const spinner = document.getElementById("spinner");
        const status = document.getElementById("status");
        if (spinner) spinner.style.display = "none";
        if (status) status.textContent = "";
        showToast(`Error: ${err.message}`);
        appendLogLine(`ERROR: ${err.message}`);
    });
}

export function refreshPlaybooks() {
    fetch('/list_playbooks')
        .then(resp => resp.json())
        .then(list => {
            const select = document.getElementById("playbookSelect");
            select.innerHTML = "";
            list.forEach(pb => {
                const opt = document.createElement("option");
                opt.value = pb;
                opt.textContent = pb;
                select.appendChild(opt);
            });
            loadPlaybook();
        });
}

export function refreshInventories() {
    fetch('/list_inventories')
        .then(resp => resp.json())
        .then(list => {
            const select = document.getElementById("inventorySelect");
            select.innerHTML = "";
            list.forEach(inv => {
                const opt = document.createElement("option");
                opt.value = inv;
                opt.textContent = inv;
                select.appendChild(opt);
            });
            onInventoryChange();
        });
}

export function onInventoryChange() {
    const inventory = document.getElementById("inventorySelect").value;

    document.getElementById("summaryInventory").textContent =
        inventory ? `Inventory: ${inventory}` : "Inventory: -";

    const groupSelect = document.getElementById("groupSelect");
    if (!groupSelect) return;

    groupSelect.innerHTML = "";
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "(none - use playbook hosts)";
    groupSelect.appendChild(noneOpt);

    document.getElementById("summaryGroup").textContent =
        "Group: (none - using playbook hosts)";
    document.getElementById("summaryHosts").textContent = "";

    if (!inventory) return;

    fetch(`/inventory/groups?file=${encodeURIComponent(inventory)}`)
        .then(resp => resp.json())
        .then(groups => {
            if (!Array.isArray(groups)) return;
            groups.forEach(g => {
                const opt = document.createElement("option");
                opt.value = g;
                opt.textContent = g;
                groupSelect.appendChild(opt);
            });
        })
        .catch(err => {
            showToast(`Error loading inventory groups: ${err.message}`);
        });
}

export function onGroupChange() {
    const inventory = document.getElementById("inventorySelect").value;
    const group = document.getElementById("groupSelect").value;

    if (!group) {
        document.getElementById("summaryGroup").textContent =
            "Group: (none - using playbook hosts)";
        document.getElementById("summaryHosts").textContent = "";
        return;
    }

    document.getElementById("summaryGroup").textContent =
        `Group: ${group} (loading hosts...)`;

    fetch(`/inventory/hosts?file=${encodeURIComponent(inventory)}&group=${encodeURIComponent(group)}`)
        .then(resp => resp.json())
        .then(data => {
            const hosts = data.hosts || [];
            document.getElementById("summaryGroup").textContent =
                `Group: ${group} (${hosts.length} host${hosts.length === 1 ? "" : "s"})`;

            if (hosts.length) {
                document.getElementById("summaryHosts").textContent =
                    "Hosts:\n - " + hosts.join("\n - ");
            } else {
                document.getElementById("summaryHosts").textContent =
                    "Hosts: (none found in this group)";
            }
        });
}

export function initAnsible() {
    refreshPlaybooks();
    refreshInventories();
}
