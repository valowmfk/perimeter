// app.js — Main entry point (ES module)
// Wires up all modules, event delegation, and rail view switching system.

import { showToast, appendLogLine } from './utils/dom.js';
import { toggleDnsFields, createDnsRecord } from './modules/dns.js';
import { tryLoadIpam, loadIpamData, refreshIpam, validateIpAddress, loadIpamSubnets, onIpamSubnetChange } from './modules/ipam.js';
import { loadPlaybook, runPlaybook, refreshPlaybooks, refreshInventories,
         onInventoryChange, onGroupChange, initAnsible } from './modules/ansible.js';
import { loadBridges, collectBridges, updateNicDropdowns, generateHostname,
         onVmTypeChange, startDeploy, pollStatus, loadVmList, rerunBootstrap,
         toggleVmProtect, destroyVm, promoteVm, loadVmIds, validateVmId,
         loadSubnets, onSubnetChange,
         openFleetDrawer, resetFleetDrawer, fleetSwitchMode, fleetAddRow,
         fleetRemoveRow, fleetDuplicateRow, fleetUpdatePreview, deployFleet,
         fleetPollStatus, fleetCancel, fleetCancelTask,
         toggleFleetStatus, fleetToggleLog, fleetVmTypeChange,
         openFleetFlyout, closeFleetFlyout,
         filterTemplatesByType, cacheTemplateOptions } from './modules/vms.js';
import { loadCertStats, toggleCertFlyout, toggleCertDomainExpand,
         openCertViewModal, certViewChangeFile, copyCertToClipboard,
         closeCertViewModal, downloadCert, deleteCert, updateCertPreview,
         refreshCertDomains, onCreatePlaybookChange, toggleVipNameField,
         toggleCertAdvanced, loadCertVthunderGroups, loadCertVthunderHosts,
         loadCertVthunderPartitions, executeCertAction,
         openDeployVthunderModal, closeDeployVthunderModal,
         onDeployGroupChange, onDeployHostChange, onDeployPartitionChange,
         executeDeployToVthunder } from './modules/certificates.js';
import { updateTelemetry } from './modules/telemetry.js';
import { toggleProxmoxFlyout, closeProxmoxFlyoutOnOutsideClick,
         refreshTemplate } from './modules/proxmox.js';
import { initVipManager, loadVips, openVipBuilder, closeVipBuilder,
         vipNext, vipPrev, addBackendRow, removeBackendRow,
         openVipDestroy, confirmVipDestroy, cancelVipDestroy } from './modules/vthunder.js';
import { loadUpdatesOverview, checkNodes, refreshArgocd,
         runNodeUpdate, refreshRuns, onUpdatesGroupChange,
         refreshRenovate, syncArgoApp,
         createSchedule, toggleSchedule, deleteSchedule, onSchedFreqChange } from './modules/updates.js';

/* ============================
   Rail View Switching System
   ============================ */

// Single source of truth for drawers. switchView derives from this; Tier B's
// labeled sidebar + ⌘K palette will too. Add a drawer here + its template
// include + (if it needs lazy-load) an ACTION_HANDLERS entry.
const DRAWERS = [
    { id: 'vmProvisionDrawer',           action: 'toggle-vm-provision', label: 'VM Orchestration', group: 'Compute' },
    { id: 'fleetDrawer',                 action: 'toggle-fleet',        label: 'Fleet',            group: 'Compute' },
    { id: 'ansibleOrchestrationDrawer',  action: 'toggle-ansible',      label: 'Ansible',          group: 'Config' },
    { id: 'certificateManagementDrawer', action: 'toggle-certificates', label: 'Certificates',     group: 'Config' },
    { id: 'dnsManagementDrawer',         action: 'toggle-dns',          label: 'DNS',              group: 'Config' },
    { id: 'ipamDrawer',                  action: 'toggle-ipam',         label: 'IPAM',             group: 'Config' },
    { id: 'vipManagerDrawer',            action: 'toggle-vip-manager',  label: 'vThunder',         group: 'Appliances' },
    { id: 'updatesDrawer',               action: 'toggle-updates',      label: 'Updates',          group: 'Operations' },
    { id: 'assetsDrawer',                action: 'toggle-assets',       label: 'Active Assets',    group: 'Operations' },
];

/* ── Persistent Live Stream controls ───────────────────────── */
function clearStream() {
    const o = document.getElementById('terminalLog');
    if (o) { o.innerHTML = ''; const t = o.closest('.terminal'); if (t) t.classList.remove('has-output'); }
    const c = document.getElementById('streamLineCount'); if (c) c.textContent = '';
    const p = document.getElementById('jumpLivePill'); if (p) p.classList.remove('show');
    const d = document.getElementById('streamLiveDot'); if (d) d.className = 'terminal-live';
    const s = document.getElementById('streamStatus'); if (s) s.innerHTML = '○ idle';
}
function copyStream() {
    const o = document.getElementById('terminalLog'); if (!o) return;
    const text = o.innerText || o.textContent || '';
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => showToast('Stream copied')).catch(() => showToast('Copy failed'));
}
function toggleStreamWide() {
    const m = document.getElementById('mainContent'); if (m) m.classList.toggle('stream-wide');
}
function jumpToLive() {
    const o = document.getElementById('terminalLog'); if (o) o.scrollTop = o.scrollHeight;
    const p = document.getElementById('jumpLivePill'); if (p) p.classList.remove('show');
}

/* Collapsible labeled sidebar — expand to labels, collapse to icons. */
function toggleRail() {
    const expanded = document.body.classList.toggle('rail-expanded');
    try { localStorage.setItem('perimeter.railExpanded', expanded ? '1' : '0'); } catch (e) {}
}
function restoreRail() {
    try { if (localStorage.getItem('perimeter.railExpanded') === '1') document.body.classList.add('rail-expanded'); } catch (e) {}
}

/* Draggable gutter — resize the stream column, persisted per browser. */
function initStreamGutter() {
    const gutter = document.getElementById('mainGutter');
    const main = document.getElementById('mainContent');
    if (!gutter || !main) return;
    try { const saved = localStorage.getItem('perimeter.streamWidth'); if (saved) main.style.setProperty('--stream-width', saved); } catch (e) {}
    let dragging = false;
    gutter.addEventListener('mousedown', (e) => { dragging = true; gutter.classList.add('dragging'); e.preventDefault(); });
    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const rect = main.getBoundingClientRect();
        const w = Math.max(280, Math.min(rect.width - 360, rect.right - e.clientX));
        main.style.setProperty('--stream-width', w + 'px');
    });
    document.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false; gutter.classList.remove('dragging');
        try { const v = main.style.getPropertyValue('--stream-width'); if (v) localStorage.setItem('perimeter.streamWidth', v); } catch (e) {}
    });
}

/* Global job feed — surface UNATTENDED (scheduler-fired) runs in the stream even
   when their drawer isn't open. Only tails jobs tagged triggered_by=schedule:* ,
   so operator-triggered runs (streamed by their own drawer) never double-print. */
const _feedTailed = new Set();
function pollJobFeed() {
    fetch('/api/tasks')
        .then(r => r.json())
        .then(jobs => {
            (jobs || []).forEach(j => {
                const tb = j.triggered_by || '';
                if (j.status !== 'running' || !tb.startsWith('schedule:') || _feedTailed.has(j.task_id)) return;
                _feedTailed.add(j.task_id);
                showToast(`Scheduled run started: ${tb.slice('schedule:'.length)}`);
                const es = new EventSource(`/api/tasks/${j.task_id}/stream`);
                es.onmessage = (e) => {
                    if (e.data === '__COMPLETE__') {
                        es.close(); _feedTailed.delete(j.task_id);
                        if (document.getElementById('updatesDrawer')) loadUpdatesOverview();
                    } else { appendLogLine(e.data); }
                };
                es.onerror = () => { es.close(); _feedTailed.delete(j.task_id); };
            });
        })
        .catch(() => { /* feed is best-effort */ });
}
function startJobFeed() {
    pollJobFeed();
    setInterval(pollJobFeed, 10000);
}

function switchView(drawerId) {
    const drawer = document.getElementById(drawerId);
    if (!drawer) return;

    // Drawer id → toggle action map (single source of truth: DRAWERS)
    const views = DRAWERS;

    // Close all drawers
    views.forEach(item => {
        const d = document.getElementById(item.id);
        if (d) d.classList.remove('open');
        const btn = document.querySelector(`[data-action="${item.action}"]`);
        if (btn) btn.setAttribute('aria-expanded', 'false');
    });

    // Open target drawer
    drawer.classList.add('open');
    const toggleBtn = document.querySelector(`[data-action="${views.find(v => v.id === drawerId)?.action}"]`);
    if (toggleBtn) toggleBtn.setAttribute('aria-expanded', 'true');

    // Update rail active state
    document.querySelectorAll('.rail-btn').forEach(btn => btn.classList.remove('active'));
    const railBtn = document.querySelector(`.rail-btn[data-action="${views.find(v => v.id === drawerId)?.action}"]`);
    if (railBtn) railBtn.classList.add('active');
}

/* ============================
   Simple Drawer Toggles
   ============================ */

function toggleTelemetry() {
    const drawer = document.getElementById('telemetryDrawer');
    if (!drawer) return;
    drawer.classList.toggle('open');
    const btn = document.querySelector('[data-action="toggle-telemetry"]');
    if (btn) btn.setAttribute('aria-expanded', drawer.classList.contains('open'));
}

// Assets panel is always visible in Mission Control layout — no toggle needed


/* ============================
   IPAM Drawer (view switch + lazy load)
   ============================ */

function toggleIpamDrawer() {
    switchView('ipamDrawer');
    tryLoadIpam();
}

/* ============================
   Action Registry + Event Delegation
   ============================ */

const ACTION_HANDLERS = {
    // Rail view switches
    'toggle-vm-provision':   () => switchView('vmProvisionDrawer'),
    'toggle-ansible':        () => switchView('ansibleOrchestrationDrawer'),
    'toggle-certificates':   () => switchView('certificateManagementDrawer'),
    'toggle-dns':            () => switchView('dnsManagementDrawer'),
    'toggle-ipam':           () => { switchView('ipamDrawer'); tryLoadIpam(); },
    'toggle-vip-manager':    () => switchView('vipManagerDrawer'),
    'toggle-updates':        () => { switchView('updatesDrawer'); loadUpdatesOverview(); },

    // Updates page actions (read-only observe)
    'check-nodes':           checkNodes,
    'refresh-argocd':        refreshArgocd,
    // Updates page actions (node execute)
    'run-node-update':       runNodeUpdate,
    'refresh-runs':          refreshRuns,
    // Updates page actions (pod orchestrate)
    'refresh-renovate':      refreshRenovate,
    'sync-argocd-app':       (el) => syncArgoApp(el),
    // Updates page actions (schedules)
    'create-schedule':       createSchedule,
    'toggle-schedule':       (el) => toggleSchedule(el),
    'delete-schedule':       (el) => deleteSchedule(el),

    // Simple drawers
    'toggle-telemetry':      toggleTelemetry,
    // Active Assets moved out of the always-on right panel into a drawer
    'toggle-assets':         () => { switchView('assetsDrawer'); loadVmList(); },
    // Persistent Live Stream controls
    'clear-stream':          clearStream,
    'copy-stream':           copyStream,
    'stream-wide':           toggleStreamWide,
    'jump-to-live':          jumpToLive,
    'toggle-rail':           toggleRail,
    'toggle-proxmox-flyout': toggleProxmoxFlyout,

    // Template management
    'template-refresh':      (el) => refreshTemplate(el.dataset.template),

    // VM actions (delegated from dynamic table rows)
    'bootstrap':             (el) => rerunBootstrap(el.dataset.vmid, el),
    'destroy':               (el) => destroyVm(el.dataset.vmid, el),
    'promote':               (el) => promoteVm(el.dataset.hostname),

    // Cert flyout actions (delegated from dynamic content)
    'cert-expand':           (el) => toggleCertDomainExpand(el.dataset.domain),
    'cert-view':             (el) => { openCertViewModal(el.dataset.domain, el.dataset.cert); },
    'cert-download':         (el) => { downloadCert(el.dataset.domain, el.dataset.cert); },
    'cert-delete':           (el) => { deleteCert(el.dataset.domain, el.dataset.cert); },
    'deploy-to-vthunder':    (el) => { openDeployVthunderModal(el.dataset.domain, el.dataset.cert); },

    // Cert flyout toggle
    'toggle-cert-flyout':    toggleCertFlyout,

    // Cert modal
    'close-cert-modal':      closeCertViewModal,

    // Deploy to vThunder modal
    'deploy-vth-cancel':       closeDeployVthunderModal,
    'deploy-vth-execute':      executeDeployToVthunder,
    'copy-cert':             copyCertToClipboard,

    // Cert advanced toggle
    'toggle-cert-advanced':  toggleCertAdvanced,

    // VIP Manager
    'vip-load':              loadVips,
    'vip-create-open':       openVipBuilder,
    'vip-cancel':            closeVipBuilder,
    'vip-next':              vipNext,
    'vip-prev':              vipPrev,
    'vip-add-backend':       addBackendRow,
    'vip-remove-backend':    (el) => removeBackendRow(el),
    'vip-destroy':           (el) => openVipDestroy(el.dataset.vipName),
    'vip-destroy-cancel':    cancelVipDestroy,
    'vip-destroy-confirm':   confirmVipDestroy,

    // Fleet
    'toggle-fleet':          () => { switchView('fleetDrawer'); openFleetDrawer(); },
    'fleet-mode-quick':      () => fleetSwitchMode('quick'),
    'fleet-mode-custom':     () => fleetSwitchMode('custom'),
    'fleet-add-row':         fleetAddRow,
    'fleet-remove-row':      (el) => fleetRemoveRow(el),
    'fleet-duplicate-row':   (el) => fleetDuplicateRow(el),
    'fleet-deploy':          deployFleet,
    'fleet-cancel-all':      () => fleetCancel(),
    'fleet-cancel-task':     (el) => fleetCancelTask(el.dataset.taskId),
    'fleet-reset':           resetFleetDrawer,
    'fleet-close-flyout':    closeFleetFlyout,
    'toggle-fleet-status':   toggleFleetStatus,
    'fleet-toggle-log':      (el) => fleetToggleLog(el.dataset.taskId),

    // Buttons
    'generate-hostname':     generateHostname,
    'start-deploy':          startDeploy,
    'run-playbook':          runPlaybook,
    'refresh-playbooks':     refreshPlaybooks,
    'refresh-inventories':   refreshInventories,
    'refresh-cert-domains':  refreshCertDomains,
    'refresh-ipam':          refreshIpam,
    'create-dns':            createDnsRecord,
    'execute-cert':          executeCertAction,
};

// Click delegation
document.addEventListener('click', function(e) {
    const actionEl = e.target.closest('[data-action]');
    if (!actionEl) {
        // Close flyouts on outside click
        closeCertFlyoutOnOutsideClick(e);
        closeProxmoxFlyoutOnOutsideClick(e);
        // Close fleet flyout on overlay click
        const fleetOverlay = document.getElementById('fleetFlyoutOverlay');
        if (fleetOverlay && e.target === fleetOverlay) closeFleetFlyout();
        // Close cert modal on overlay (outside-box) click — e.target check,
        // not stopPropagation on inner elements (see tasks/lessons.md)
        const certOverlay = document.getElementById('certViewModal');
        if (certOverlay && e.target === certOverlay) closeCertViewModal();
        return;
    }

    const action = actionEl.dataset.action;

    // Clicks inside flyouts should never trigger the parent toggle
    if (action === 'toggle-cert-flyout') {
        const flyout = document.getElementById('certFlyout');
        if (flyout && flyout.contains(e.target)) return;
    }
    if (action === 'toggle-proxmox-flyout') {
        const flyout = document.getElementById('proxmoxFlyout');
        if (flyout && flyout.contains(e.target)) return;
    }

    // Respect disabled buttons (protect toggle sets disabled on Destroy)
    if (actionEl.disabled) return;

    const handler = ACTION_HANDLERS[action];
    if (handler) {
        handler(actionEl);
    }
});

// Keyboard delegation: make role="button" elements respond to Enter/Space
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const actionEl = e.target.closest('[data-action][role="button"]');
    if (!actionEl) return;
    e.preventDefault();
    actionEl.click();
});

/* ── Command palette (⌘K / Ctrl+K) — jump to any drawer ─────── */
let _cmdkActive = 0;
let _cmdkMatches = [];
function paletteOpen() {
    const ov = document.getElementById('cmdkOverlay');
    const input = document.getElementById('cmdkInput');
    if (!ov || !input) return;
    ov.style.display = 'flex';
    input.value = '';
    renderPalette('');
    input.focus();
}
function paletteClose() {
    const ov = document.getElementById('cmdkOverlay');
    if (ov) ov.style.display = 'none';
}
function paletteIsOpen() {
    const ov = document.getElementById('cmdkOverlay');
    return ov && ov.style.display === 'flex';
}
function renderPalette(query) {
    const list = document.getElementById('cmdkList');
    if (!list) return;
    const q = (query || '').trim().toLowerCase();
    _cmdkMatches = DRAWERS.filter(d => !q || d.label.toLowerCase().includes(q) || d.group.toLowerCase().includes(q));
    _cmdkActive = 0;
    if (!_cmdkMatches.length) { list.innerHTML = '<li class="cmdk-empty">No matches</li>'; return; }
    list.innerHTML = _cmdkMatches.map((d, i) =>
        `<li class="cmdk-item${i === 0 ? ' active' : ''}" role="option" data-cmdk-index="${i}"><span>${d.label}</span><span class="cmdk-group">${d.group}</span></li>`
    ).join('');
}
function paletteMove(delta) {
    if (!_cmdkMatches.length) return;
    _cmdkActive = (_cmdkActive + delta + _cmdkMatches.length) % _cmdkMatches.length;
    const items = document.querySelectorAll('#cmdkList .cmdk-item');
    items.forEach((el, i) => el.classList.toggle('active', i === _cmdkActive));
    if (items[_cmdkActive]) items[_cmdkActive].scrollIntoView({ block: 'nearest' });
}
function paletteActivate(index) {
    const d = _cmdkMatches[index];
    if (!d) return;
    paletteClose();
    const handler = ACTION_HANDLERS[d.action];
    if (handler) handler(); else switchView(d.id);
}

document.addEventListener('keydown', function(e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        paletteIsOpen() ? paletteClose() : paletteOpen();
        return;
    }
    if (!paletteIsOpen()) return;
    if (e.key === 'Escape') { e.preventDefault(); paletteClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); paletteMove(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); paletteMove(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); paletteActivate(_cmdkActive); }
});
document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'cmdkInput') renderPalette(e.target.value);
});
document.addEventListener('click', function(e) {
    const item = e.target.closest('#cmdkList .cmdk-item');
    if (item) { paletteActivate(parseInt(item.dataset.cmdkIndex, 10)); return; }
    const ov = document.getElementById('cmdkOverlay');
    if (ov && e.target === ov) paletteClose();  // click outside the box (e.target check, not stopPropagation)
});

// Change delegation (protect toggle + select elements)
document.addEventListener('change', function(e) {
    const protectEl = e.target.closest('[data-action="protect"]');
    if (protectEl) {
        toggleVmProtect(protectEl.dataset.vmid, protectEl.checked);
    }
});

function closeCertFlyoutOnOutsideClick(e) {
    const flyout = document.getElementById('certFlyout');
    const toggle = document.querySelector('[data-action="toggle-cert-flyout"]');
    if (flyout && flyout.style.display !== 'none' &&
        !flyout.contains(e.target) &&
        (!toggle || !toggle.contains(e.target))) {
        flyout.style.display = 'none';
        const chevron = document.getElementById('certChevron');
        if (chevron) chevron.textContent = '\u25BE';
    }
}

/* ============================
   Static Element Bindings
   ============================ */

function bindEvents() {
    // VM type
    const vmType = document.getElementById('vm_type');
    if (vmType) vmType.addEventListener('change', onVmTypeChange);

    // Subnet selection
    const subnetSel = document.getElementById('subnet');
    if (subnetSel) subnetSel.addEventListener('change', onSubnetChange);

    // IPAM subnet selector
    const ipamSubnetSel = document.getElementById('ipamSubnetSelect');
    if (ipamSubnetSel) ipamSubnetSel.addEventListener('change', onIpamSubnetChange);

    // IP address validation
    const ipInput = document.getElementById('ip');
    if (ipInput) ipInput.addEventListener('input', validateIpAddress);

    // VMID validation
    const vmIdInput = document.getElementById('vm_id');
    if (vmIdInput) vmIdInput.addEventListener('input', validateVmId);

    // NIC count
    const nicCount = document.getElementById('nicCount');
    if (nicCount) nicCount.addEventListener('change', updateNicDropdowns);

    // Ansible selects
    const playbookSelect = document.getElementById('playbookSelect');
    if (playbookSelect) playbookSelect.addEventListener('change', loadPlaybook);

    const inventorySelect = document.getElementById('inventorySelect');
    if (inventorySelect) inventorySelect.addEventListener('change', onInventoryChange);

    const groupSelect = document.getElementById('groupSelect');
    if (groupSelect) groupSelect.addEventListener('change', onGroupChange);

    // Updates page: group change toggles k3s drain field + lock state
    const updatesGroupSelect = document.getElementById('updatesGroupSelect');
    if (updatesGroupSelect) updatesGroupSelect.addEventListener('change', onUpdatesGroupChange);

    // Updates page: schedule frequency toggles the day-of-week field
    const schedFreq = document.getElementById('schedFreq');
    if (schedFreq) schedFreq.addEventListener('change', onSchedFreqChange);

    // Certificate form inputs
    const certBaseDomain = document.getElementById('certBaseDomainSelect');
    if (certBaseDomain) certBaseDomain.addEventListener('change', updateCertPreview);

    const certCommonName = document.getElementById('certCommonName');
    if (certCommonName) certCommonName.addEventListener('input', updateCertPreview);

    const certWildcard = document.getElementById('certWildcard');
    if (certWildcard) certWildcard.addEventListener('change', updateCertPreview);

    const certSANs = document.getElementById('certSANs');
    if (certSANs) certSANs.addEventListener('input', updateCertPreview);

    const certCreatePlaybook = document.getElementById('certCreatePlaybook');
    if (certCreatePlaybook) certCreatePlaybook.addEventListener('change', onCreatePlaybookChange);

    const certTargetType = document.getElementById('certTargetType');
    if (certTargetType) certTargetType.addEventListener('change', toggleVipNameField);

    const certVthunderGroup = document.getElementById('certVthunderGroup');
    if (certVthunderGroup) certVthunderGroup.addEventListener('change', loadCertVthunderHosts);

    const certVthunderHost = document.getElementById('certVthunderHost');
    if (certVthunderHost) certVthunderHost.addEventListener('change', loadCertVthunderPartitions);

    // Deploy to vThunder modal cascade
    const deployVthGroup = document.getElementById('deployVthGroup');
    if (deployVthGroup) deployVthGroup.addEventListener('change', onDeployGroupChange);

    const deployVthHost = document.getElementById('deployVthHost');
    if (deployVthHost) deployVthHost.addEventListener('change', onDeployHostChange);

    const deployVthPartition = document.getElementById('deployVthPartition');
    if (deployVthPartition) deployVthPartition.addEventListener('change', onDeployPartitionChange);

    const certViewFileSelect = document.getElementById('certViewFileSelect');
    if (certViewFileSelect) certViewFileSelect.addEventListener('change', certViewChangeFile);

    // Fleet quick-mode preview updates
    ['fleetQuickCount', 'fleetQuickHostname', 'fleetQuickStartIp',
     'fleetQuickStartVmid', 'fleetQuickCpu', 'fleetQuickRam',
     'fleetQuickDisk', 'fleetQuickSubnet'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', fleetUpdatePreview);
        if (el) el.addEventListener('change', fleetUpdatePreview);
    });

    // Fleet VM type change — filter templates + toggle resource fields (Quick Fleet)
    const fleetVmTypeSel = document.getElementById('fleetQuickVmType');
    if (fleetVmTypeSel) fleetVmTypeSel.addEventListener('change', fleetVmTypeChange);

    // Fleet custom card VM type change — filter templates per card
    document.addEventListener('fleet-type-change', function(e) {
        const card = e.target.closest('.q-fleet-card');
        if (!card) return;
        const vmType = card.querySelector('[data-field="vm_type"]')?.value || 'linux';
        const tplSel = card.querySelector('[data-field="template"]');
        const resourceRow = card.querySelector('.q-fleet-resource-fields');
        filterTemplatesByType(tplSel, vmType);
        if (resourceRow) {
            resourceRow.style.display = (vmType === 'vthunder' || vmType === 'vyos') ? 'none' : 'flex';
        }
    });

    // DNS record type
    const dnsRecordType = document.getElementById('dnsRecordType');
    if (dnsRecordType) dnsRecordType.addEventListener('change', toggleDnsFields);

    // Cert modal outside-click close is handled in the click-delegation branch
    // via an e.target === overlay check — no stopPropagation on inner elements
    // (that would kill event delegation; see tasks/lessons.md).

    // Fleet flyout: Escape to close
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        const fleetOverlay = document.getElementById('fleetFlyoutOverlay');
        if (fleetOverlay && fleetOverlay.style.display !== 'none') {
            closeFleetFlyout();
        }
    });

    // Cert view modal: Escape + focus trap handled by certificates.js openCertViewModal/closeCertViewModal
}

/* ============================
   Initialization
   ============================ */

document.addEventListener('DOMContentLoaded', () => {
    cacheTemplateOptions();  // Capture full unfiltered template list before any filtering
    bindEvents();
    onVmTypeChange();
    restoreRail();

    // Feature-guarded initialization (only if drawer exists in DOM)
    if (document.getElementById('certCountStrip'))        loadCertStats();
    if (document.getElementById('vipManagerDrawer'))       initVipManager();
    if (document.getElementById('ipamSubnetSelect'))       loadIpamSubnets();

    // Default to VM Orchestration view on load
    switchView('vmProvisionDrawer');

    // Persistent stream column — make the divider draggable
    initStreamGutter();

    // Global job feed — surface scheduler-fired runs in the stream
    startJobFeed();
});

window.addEventListener('load', () => {
    Promise.all([
        loadVmList(),
        loadVmIds(),
        loadBridges(),
        loadSubnets()
    ]).catch(err => console.error('Initial load error:', err));

    // Hourly heartbeat polls
    const intervals = [
        setInterval(loadVmList, 3600000),
        setInterval(loadVmIds, 3600000),
    ];

    window.addEventListener('beforeunload', () => {
        intervals.forEach(clearInterval);
    });

    // Feature-guarded init
    if (document.getElementById('ansibleOrchestrationDrawer')) initAnsible();
    if (document.getElementById('updatesDrawer')) loadUpdatesOverview();
});
