// Custom confirm modal — replaces native confirm() dialogs
// Returns a Promise<boolean> that resolves when the user clicks confirm or cancel.

let modalEl = null;

function getModal() {
    if (!modalEl) {
        modalEl = document.getElementById('confirmModal');
    }
    return modalEl;
}

/**
 * Show a styled confirm dialog.
 * @param {Object} opts
 * @param {string} opts.title     — Modal title text
 * @param {string} opts.message   — Body message (plain text, not HTML)
 * @param {string} [opts.confirm] — Confirm button label (default: "Confirm")
 * @param {string} [opts.cancel]  — Cancel button label (default: "Cancel")
 * @param {string} [opts.variant] — "danger" | "warning" | "default"
 * @returns {Promise<boolean>} true if confirmed, false if cancelled
 */
export function confirmModal({ title, message, confirm = 'Confirm', cancel = 'Cancel', variant = 'default' }) {
    const modal = getModal();
    if (!modal) {
        // Fallback if modal HTML is missing
        return Promise.resolve(window.confirm(message));
    }

    const titleEl   = modal.querySelector('.q-confirm-title');
    const messageEl = modal.querySelector('.q-confirm-message');
    const confirmBtn = modal.querySelector('[data-action="confirm-yes"]');
    const cancelBtn  = modal.querySelector('[data-action="confirm-no"]');

    titleEl.textContent = title;
    messageEl.textContent = message;
    confirmBtn.textContent = confirm;
    cancelBtn.textContent = cancel;

    // Set variant class for styling (danger = red, warning = yellow)
    confirmBtn.className = 'q-button q-confirm-btn';
    if (variant === 'danger') {
        confirmBtn.classList.add('q-danger');
    } else if (variant === 'warning') {
        confirmBtn.classList.add('q-warning');
    }

    // Store the element that had focus before the modal opened
    const previousFocus = document.activeElement;

    modal.style.display = 'flex';

    return new Promise(resolve => {
        const focusableEls = [cancelBtn, confirmBtn];

        function cleanup() {
            modal.style.display = 'none';
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('click', onOverlay);
            document.removeEventListener('keydown', onKey);
            // Restore focus to the element that triggered the modal
            if (previousFocus && previousFocus.focus) previousFocus.focus();
        }

        function onConfirm() {
            cleanup();
            resolve(true);
        }

        function onCancel() {
            cleanup();
            resolve(false);
        }

        function onOverlay(e) {
            if (e.target === modal) {
                cleanup();
                resolve(false);
            }
        }

        function onKey(e) {
            if (e.key === 'Escape') {
                cleanup();
                resolve(false);
            }
            // Focus trap: Tab cycles between cancel and confirm buttons
            if (e.key === 'Tab') {
                const currentIndex = focusableEls.indexOf(document.activeElement);
                if (e.shiftKey) {
                    e.preventDefault();
                    focusableEls[currentIndex <= 0 ? focusableEls.length - 1 : currentIndex - 1].focus();
                } else {
                    e.preventDefault();
                    focusableEls[(currentIndex + 1) % focusableEls.length].focus();
                }
            }
        }

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('click', onOverlay);
        document.addEventListener('keydown', onKey);

        // Focus the cancel button by default (safer for destructive actions)
        cancelBtn.focus();
    });
}
