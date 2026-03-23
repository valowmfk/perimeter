// Button busy-state utility — prevents double-submit on long operations.
//
// Usage:
//   import { withBusy } from '../utils/busy.js';
//
//   // For a static button (by ID or selector):
//   const done = withBusy('start-deploy');     // disables button, shows spinner
//   try { await longOperation(); } finally { done(); }  // re-enables
//
//   // For a dynamic button (element reference):
//   const done = withBusy(buttonElement);
//   try { ... } finally { done(); }

/**
 * Put a button into "busy" state: disabled, spinner shown, original text saved.
 * Returns a `done()` function that restores the button.
 *
 * @param {string|HTMLElement} target — button ID, data-action value, or element
 * @returns {function} done — call to restore the button
 */
export function withBusy(target) {
    const btn = resolveButton(target);
    if (!btn) return () => {};

    // Guard: already busy
    if (btn.dataset.busy === '1') return () => {};

    btn.dataset.busy = '1';
    btn.disabled = true;
    btn.dataset.originalText = btn.textContent;
    btn.classList.add('q-btn-busy');

    return function done() {
        btn.dataset.busy = '';
        btn.disabled = false;
        btn.textContent = btn.dataset.originalText || btn.textContent;
        btn.classList.remove('q-btn-busy');
    };
}

function resolveButton(target) {
    if (target instanceof HTMLElement) return target;
    if (typeof target === 'string') {
        return document.getElementById(target)
            || document.querySelector(`[data-action="${target}"]`);
    }
    return null;
}
