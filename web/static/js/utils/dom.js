// Safe DOM utilities

/** Get a Perimeter config value — reads from body data attributes (CSP-safe) or window.PERIMETER_CONFIG. */
export function getConfig(key, fallback = '') {
    // data-pm-node on <body> → key 'pmNode'
    const dataKey = key.replace(/([A-Z])/g, '-$1').toLowerCase(); // pmNode → pm-node
    const bodyVal = document.body?.dataset?.[key];
    if (bodyVal) return bodyVal;
    return window.PERIMETER_CONFIG?.[key] || fallback;
}

/**
 * Toggle a flyout/panel element between display:none and display:block.
 * Handles chevron update and optional aria-expanded + onOpen callback.
 */
export function togglePanel(elementId, { chevronId, toggleSelector, onOpen, chevronOpen = '\u25b4', chevronClosed = '\u25be' } = {}) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const isOpening = el.style.display === 'none';
    el.style.display = isOpening ? 'block' : 'none';

    const chevron = chevronId ? document.getElementById(chevronId) : null;
    if (chevron) chevron.textContent = isOpening ? chevronOpen : chevronClosed;

    const toggle = toggleSelector ? document.querySelector(toggleSelector) : null;
    if (toggle) toggle.setAttribute('aria-expanded', isOpening ? 'true' : 'false');

    if (isOpening && onOpen) onOpen();
}

export function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function showToast(message) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2000);
}

export function appendLogLine(line) {
    const output = document.getElementById("terminalLog");
    if (!output) {
        console.error("terminalLog element not found");
        return;
    }

    const span = document.createElement("span");
    span.textContent = line;

    const lower = line.toLowerCase();

    if (lower.includes("failed") || lower.includes("fatal:") || lower.includes("error")) {
        span.className = "log-line log-error";
    } else if (lower.includes("changed")) {
        span.className = "log-line log-changed";
    } else if (lower.includes("ok:") || lower.includes("success")) {
        span.className = "log-line log-ok";
    } else {
        span.className = "log-line log-info";
    }

    output.appendChild(span);
    output.appendChild(document.createElement("br"));
    output.scrollTop = output.scrollHeight;
}
