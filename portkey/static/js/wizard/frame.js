/*
 * wizard/frame.js — owner: package 8b (private module of views/wizard.js and ui/palette.js).
 *
 * A bare native <dialog> opened with showModal(), for the two dialogs whose layout does not fit
 * ui/modal.js (fixed title and button row): the quick-connect palette (no header) and the key
 * install wizard (step indicator, buttons that change per step). Same behaviour as modal.js:
 * the page behind is inert, Tab stays inside, Esc and a backdrop click call onDismiss (which
 * decides whether to close), and focus returns to where it was.
 */

import { h, refind } from '../ui/dom.js';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/**
 * @typedef {object} Frame
 * @property {HTMLDialogElement} dialog
 * @property {() => void} close
 * @property {boolean} closed
 */

/**
 * @param {{className: string, label?: string, labelledBy?: string, onDismiss: () => void, content: Node}} opts
 * @returns {Frame}
 */
export function openFrame(opts) {
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const dialog = /** @type {HTMLDialogElement} */ (h('dialog', {
    class: opts.className, 'aria-label': opts.label ?? null, 'aria-labelledby': opts.labelledBy ?? null,
  }, opts.content));
  /** @type {Frame} */
  const frame = {
    dialog,
    closed: false,
    close() {
      if (frame.closed) return;
      frame.closed = true;
      if (dialog.open) dialog.close();
      dialog.remove();
      refind(opener)?.focus({ preventScroll: true });
    },
  };
  dialog.addEventListener('cancel', (e) => {
    e.preventDefault();
    opts.onDismiss();
  });
  dialog.addEventListener('mousedown', (e) => {
    if (e.target === dialog) opts.onDismiss();
  });
  dialog.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el instanceof HTMLElement && el.offsetParent !== null);
    if (!items.length) return;
    const first = /** @type {HTMLElement} */ (items[0]);
    const last = /** @type {HTMLElement} */ (items[items.length - 1]);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
  (document.getElementById('dialogs') ?? document.body).appendChild(dialog);
  dialog.showModal();
  return frame;
}
