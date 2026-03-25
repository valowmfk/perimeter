// Safe DOM utilities

/** Get a Perimeter config value — reads from body data attributes (CSP-safe) or window.PERIMETER_CONFIG. */
export function getConfig(key, fallback = '') {
    // data-pm-node on <body> → key 'pmNode'
    const dataKey = key.replace(/([A-Z])/g, '-$1').toLowerCase(); // pmNode → pm-node
    const bodyVal = document.body?.dataset?.[key];
    if (bodyVal) return bodyVal;
    return window.PERIMETER_CONFIG?.[key] || fallback;
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
