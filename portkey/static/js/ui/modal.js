/*
 * modal.js — owner: package 8a.
 *
 * The modal primitive used by every dialog (8a's and 8b's): backdrop, focus trap on the
 * first field (safe button first in danger dialogs), Esc closes, full width below 600px.
 *
 * Built on a native <dialog> opened with showModal(), so the rest of the page is inert and a
 * later dialog (for example the sign-in dialog after a 401) stacks on top. Markup:
 *   dialog.modal > form.modal__box > header.modal__head (h2) + div.modal__body + footer.modal__foot
 *
 * openModal(spec) -> {element, body, result, close, setBusy, setError}
 * - spec.buttons render in order in the footer. A button with value "cancel" closes with null.
 *   The first primary or danger button is the form's submit button, so Enter in a field
 *   presses it.
 * - spec.onAction(value) runs before the dialog closes for any other button; return false (or
 *   a promise of false) to keep it open, for example after showing a validation error with
 *   setError(). Buttons are disabled while it runs.
 * - Esc and a click on the backdrop close with null unless spec.dismissible is false.
 * - Initial focus: spec.initialFocus, else the safe (first secondary) button when danger is
 *   set, else the first field in the body, else the first button. Focus returns to the element
 *   that had it when the dialog opened.
 * - result resolves with the button value, or null when dismissed.
 *
 * Helpers: confirmDialog() and promptDialog() for small yes/no and one-field dialogs.
 */

import { h, clear } from './dom.js';

/** @typedef {{label: string, kind?: 'primary'|'secondary'|'danger', value: string, disabled?: boolean}} ModalButton */

/**
 * @typedef {object} ModalSpec
 * @property {string} title
 * @property {Node} body
 * @property {ModalButton[]} [buttons]
 * @property {boolean} [danger]
 * @property {number} [width]          px; default 560 (--dialog-w), 460 for small dialogs
 * @property {boolean} [dismissible]   default true
 * @property {HTMLElement|string} [initialFocus]  element or selector inside the dialog
 * @property {(value: string) => (boolean|void|Promise<boolean|void>)} [onAction]
 * @property {string} [className]
 */

/**
 * @typedef {object} ModalHandle
 * @property {HTMLDialogElement} element
 * @property {HTMLElement} body
 * @property {Promise<string|null>} result
 * @property {(value?: string|null) => void} close
 * @property {(busy: boolean) => void} setBusy
 * @property {(message: string|null) => void} setError
 * @property {(value: string) => HTMLButtonElement|null} button
 */

let counter = 0;

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** @param {ModalSpec} spec @returns {ModalHandle} */
export function openModal(spec) {
  counter += 1;
  const titleId = `modal-title-${counter}`;
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const buttons = spec.buttons ?? [{ label: 'Close', kind: 'secondary', value: 'cancel' }];
  const dismissible = spec.dismissible ?? true;

  const errorBox = h('div', { class: 'modal__error', role: 'alert', hidden: true });
  const body = h('div', { class: 'modal__body' }, errorBox, spec.body);
  const foot = h('footer', { class: 'modal__foot' });
  const submitValue = buttons.find((b) => b.kind === 'primary' || b.kind === 'danger')?.value;
  for (const b of buttons) {
    foot.appendChild(h('button', {
      type: b.value === submitValue ? 'submit' : 'button',
      class: `btn btn--${b.kind ?? 'secondary'}`,
      dataset: { value: b.value },
      disabled: b.disabled,
    }, b.label));
  }
  const form = h('form', { class: 'modal__box', novalidate: true },
    h('header', { class: 'modal__head' }, h('h2', { id: titleId, class: 'modal__title' }, spec.title)),
    body,
    buttons.length ? foot : null);
  const dialog = /** @type {HTMLDialogElement} */ (h('dialog', {
    class: `modal${spec.danger ? ' modal--danger' : ''}${spec.className ? ` ${spec.className}` : ''}`,
    'aria-labelledby': titleId,
  }, form));
  if (spec.width) dialog.style.setProperty('--modal-w', `${spec.width}px`);

  /** @type {(value: string|null) => void} */
  let resolve = () => {};
  /** @type {Promise<string|null>} */
  const result = new Promise((r) => { resolve = r; });
  let closed = false;
  let busy = false;

  const close = (/** @type {string|null|undefined} */ value = null) => {
    if (closed) return;
    closed = true;
    if (dialog.open) dialog.close();
    dialog.remove();
    resolve(value ?? null);
    if (opener && opener.isConnected) opener.focus({ preventScroll: true });
  };
  /** @type {Map<HTMLButtonElement, boolean>} */
  const before = new Map();
  const setBusy = (/** @type {boolean} */ on) => {
    if (on === busy) return;
    busy = on;
    dialog.toggleAttribute('aria-busy', on);
    for (const btn of /** @type {NodeListOf<HTMLButtonElement>} */ (foot.querySelectorAll('button'))) {
      if (on) {
        before.set(btn, btn.disabled);
        btn.disabled = true;
      } else {
        btn.disabled = before.get(btn) ?? false;
      }
    }
  };
  const setError = (/** @type {string|null} */ message) => {
    clear(errorBox);
    errorBox.hidden = !message;
    if (message) errorBox.appendChild(document.createTextNode(message));
  };
  const button = (/** @type {string} */ value) => /** @type {HTMLButtonElement|null} */ (
    foot.querySelector(`button[data-value="${value.replace(/["\\]/g, '\\$&')}"]`));

  const act = async (/** @type {string} */ value) => {
    if (busy || closed) return;
    if (value === 'cancel') {
      close(null);
      return;
    }
    if (spec.onAction) {
      setBusy(true);
      let keep = false;
      try {
        keep = (await spec.onAction(value)) === false;
      } catch (err) {
        keep = true;
        setError(err instanceof Error ? err.message : String(err));
      }
      if (closed) return;
      setBusy(false);
      if (keep) return;
    }
    close(value);
  };

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const btn = submitValue ? button(submitValue) : null;
    if (submitValue && btn && !btn.disabled) act(submitValue);
  });
  foot.addEventListener('click', (e) => {
    const btn = /** @type {HTMLElement} */ (e.target).closest('button');
    if (!btn || btn.getAttribute('type') === 'submit') return;
    e.preventDefault();
    act(btn.dataset.value ?? 'cancel');
  });
  dialog.addEventListener('cancel', (e) => {
    e.preventDefault();
    if (dismissible && !busy) close(null);
  });
  dialog.addEventListener('close', () => close(null));
  dialog.addEventListener('mousedown', (e) => {
    if (e.target === dialog && dismissible && !busy) close(null);
  });
  dialog.addEventListener('keydown', (e) => trapTab(e, dialog));

  (document.getElementById('dialogs') ?? document.body).appendChild(dialog);
  dialog.showModal();
  focusInitial(dialog, body, foot, spec, buttons);
  return { element: dialog, body, result, close, setBusy, setError, button };
}

/**
 * @param {HTMLDialogElement} dialog @param {HTMLElement} body @param {HTMLElement} foot
 * @param {ModalSpec} spec @param {ModalButton[]} buttons
 */
function focusInitial(dialog, body, foot, spec, buttons) {
  /** @type {HTMLElement|null} */
  let target = null;
  if (spec.initialFocus instanceof HTMLElement) target = spec.initialFocus;
  else if (typeof spec.initialFocus === 'string') target = dialog.querySelector(spec.initialFocus);
  else if (spec.danger) {
    const safe = buttons.find((b) => b.kind !== 'danger' && b.kind !== 'primary');
    if (safe) target = foot.querySelector(`button[data-value="${safe.value}"]`);
  }
  target ??= body.querySelector('input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])');
  target ??= dialog.querySelector(FOCUSABLE);
  target?.focus();
}

/** @param {KeyboardEvent} e @param {HTMLElement} root */
function trapTab(e, root) {
  if (e.key !== 'Tab') return;
  const items = [...root.querySelectorAll(FOCUSABLE)].filter(
    (el) => el instanceof HTMLElement && el.offsetParent !== null);
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
}

/**
 * Yes/no dialog. Resolves true when confirmed.
 * @param {{title: string, message: string|Node, confirmLabel: string, cancelLabel?: string, danger?: boolean}} opts
 * @returns {Promise<boolean>}
 */
export async function confirmDialog(opts) {
  const body = typeof opts.message === 'string' ? h('p', { class: 'modal__text' }, opts.message) : opts.message;
  const { result } = openModal({
    title: opts.title,
    body,
    danger: opts.danger,
    width: 460,
    buttons: [
      { label: opts.cancelLabel ?? 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: opts.confirmLabel, kind: opts.danger ? 'danger' : 'primary', value: 'ok' },
    ],
  });
  return (await result) === 'ok';
}

/**
 * One text field. `submit(value)` may throw (message shown in the dialog) to keep it open.
 * @param {{title: string, label: string, value?: string, help?: string, confirmLabel: string,
 *   mono?: boolean, submit: (value: string) => Promise<void>|void}} opts
 * @returns {Promise<string|null>} the submitted value
 */
export async function promptDialog(opts) {
  counter += 1;
  const id = `prompt-${counter}`;
  const input = /** @type {HTMLInputElement} */ (h('input', {
    id, class: `input${opts.mono ? ' mono' : ''}`, value: opts.value ?? '', autocomplete: 'off', spellcheck: 'false',
  }));
  const body = h('div', { class: 'field' },
    h('label', { class: 'field__label', for: id }, opts.label), input,
    opts.help ? h('span', { class: 'field__help' }, opts.help) : null);
  const handle = openModal({
    title: opts.title,
    body,
    width: 460,
    buttons: [
      { label: 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: opts.confirmLabel, kind: 'primary', value: 'ok' },
    ],
    onAction: async () => {
      handle.setError(null);
      await opts.submit(input.value);
    },
  });
  input.select();
  return (await handle.result) === 'ok' ? input.value : null;
}
