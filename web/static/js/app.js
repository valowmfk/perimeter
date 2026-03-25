// app.js — Main entry point (ES module)
// Wires up all modules, event delegation, and accordion drawer system.

import { showToast } from './utils/dom.js';
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
         toggleCertAdvanced, loadVthunderGroups, loadVthunderHosts,
         loadVthunderPartitions, executeCertAction,
         openDeployVthunderModal, closeDeployVthunderModal,
         onDeployGroupChange, onDeployHostChange, onDeployPartitionChange,
         executeDeployToVthunder } from './modules/certificates.js';
import { updateTelemetry } from './modules/telemetry.js';
import { toggleProxmoxFlyout, closeProxmoxFlyoutOnOutsideClick,
         refreshTemplate } from './modules/proxmox.js';
import { initVipManager, loadVips, openVipBuilder, closeVipBuilder,
         vipNext, vipPrev, addBackendRow, removeBackendRow,
         openVipDestroy, confirmVipDestroy, cancelVipDestroy } from './modules/vthunder.js';

/* ============================
   Accordion Drawer System
   ============================ */

function toggleAccordionDrawer(drawerId) {
    const drawer = document.getElementById(drawerId);
    if (!drawer) return;

    const button = drawer.previousElementSibling;
    const container = drawer.closest('.q-panel-controls');

    const accordionDrawers = [
        { id: 'vmProvisionDrawer',          button: document.querySelector('[data-action="toggle-vm-provision"]') },
        { id: 'fleetDrawer',               button: document.querySelector('[data-action="toggle-fleet"]') },
        { id: 'ansibleOrchestrationDrawer', button: document.querySelector('[data-action="toggle-ansible"]') },
        { id: 'certificateManagementDrawer',button: document.querySelector('[data-action="toggle-certificates"]') },
        { id: 'dnsManagementDrawer',        button: document.querySelector('[data-action="toggle-dns"]') },
        { id: 'ipamDrawer',                 button: document.querySelector('[data-action="toggle-ipam"]') },
        { id: 'vipManagerDrawer',            button: document.querySelector('[data-action="toggle-vip-manager"]') }
    ];

    // CLOSE: just remove open class
    if (drawer.classList.contains('open')) {
        drawer.classList.remove('open');
        if (button) button.setAttribute('aria-expanded', 'false');
        return;
    }

    // Close all other drawers
    accordionDrawers.forEach(item => {
        const d = document.getElementById(item.id);
        if (d && d !== drawer) {
            d.classList.remove('open');
            if (item.button) item.button.setAttribute('aria-expanded', 'false');
        }
    });

    // OPEN: move button + drawer to top of container, then open
    if (container && button) {
        container.prepend(drawer);
        container.prepend(button);
    }
    drawer.classList.add('open');
    if (button) button.setAttribute('aria-expanded', 'true');
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

function toggleAssets() {
    const drawer = document.getElementById('qAssetsDrawer');
    if (!drawer) return;
    drawer.classList.toggle('open');
    const btn = document.querySelector('[data-action="toggle-assets"]');
    if (btn) btn.setAttribute('aria-expanded', drawer.classList.contains('open'));
}


/* ============================
   IPAM Drawer (accordion + lazy load)
   ============================ */

function toggleIpamDrawer() {
    toggleAccordionDrawer('ipamDrawer');
    const drawer = document.getElementById('ipamDrawer');
    if (drawer && drawer.classList.contains('open')) {
        tryLoadIpam();
    }
}

/* ============================
   Action Registry + Event Delegation
   ============================ */

const ACTION_HANDLERS = {
    // Accordion drawers
    'toggle-vm-provision':   () => toggleAccordionDrawer('vmProvisionDrawer'),
    'toggle-ansible':        () => toggleAccordionDrawer('ansibleOrchestrationDrawer'),
    'toggle-certificates':   () => toggleAccordionDrawer('certificateManagementDrawer'),
    'toggle-dns':            () => toggleAccordionDrawer('dnsManagementDrawer'),
    'toggle-ipam':           toggleIpamDrawer,

    // Simple drawers
    'toggle-telemetry':      toggleTelemetry,
    'toggle-assets':         toggleAssets,
    'toggle-vip-manager':    () => toggleAccordionDrawer('vipManagerDrawer'),
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
    'toggle-fleet':          () => { toggleAccordionDrawer('fleetDrawer'); openFleetDrawer(); },
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
    if (certVthunderGroup) certVthunderGroup.addEventListener('change', loadVthunderHosts);

    const certVthunderHost = document.getElementById('certVthunderHost');
    if (certVthunderHost) certVthunderHost.addEventListener('change', loadVthunderPartitions);

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

    // Prevent clicks inside cert modal from closing it (overlay has close action)
    const certModal = document.querySelector('.q-cert-modal');
    if (certModal) certModal.addEventListener('click', e => e.stopPropagation());

    // Fleet flyout: Escape to close
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        const fleetOverlay = document.getElementById('fleetFlyoutOverlay');
        if (fleetOverlay && fleetOverlay.style.display !== 'none') {
            closeFleetFlyout();
        }
    });

    // Cert view modal: Escape to close + focus trap
    document.addEventListener('keydown', function(e) {
        const modal = document.getElementById('certViewModal');
        if (!modal || modal.style.display === 'none') return;

        if (e.key === 'Escape') {
            closeCertViewModal();
            return;
        }

        if (e.key === 'Tab') {
            const focusable = modal.querySelectorAll('button, select, [tabindex]:not([tabindex="-1"])');
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });
}

/* ============================
   Initialization
   ============================ */

document.addEventListener('DOMContentLoaded', () => {
    cacheTemplateOptions();  // Capture full unfiltered template list before any filtering
    bindEvents();
    onVmTypeChange();

    // Feature-guarded initialization (only if drawer exists in DOM)
    if (document.getElementById('certCountStrip'))        loadCertStats();
    if (document.getElementById('vipManagerDrawer'))       initVipManager();
    if (document.getElementById('ipamSubnetSelect'))       loadIpamSubnets();
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
});
