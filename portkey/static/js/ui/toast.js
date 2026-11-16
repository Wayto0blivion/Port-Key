/*
 * toast.js — owner: package 8a.
 *
 * Toasts: what was copied ("Public key copied"), errors, and 10-second Undo toasts.
 *
 * toast(message, {undo, timeout, kind}) shows a toast in #toasts and returns {dismiss}.
 * - Info toasts last 4 s, error toasts 8 s (and are announced as alerts), Undo toasts 10 s.
 * - The timer pauses while the pointer is over the toast or focus is inside it.
 * - At most 4 toasts are shown; the oldest goes first.
 * - `undo` adds an [Undo] button; it runs once and dismisses the toast.
 */

import { h } from './dom.js';
import { icon } from '../parts/icons.js';

const MAX_TOASTS = 4;
let seq = 0;

/** @returns {HTMLElement} */
function host() {
  let el = document.getElementById('toasts');
  if (!el) {
    el = h('div', { id: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(el);
  }
  return el;
}

/**
 * @param {string} message
 * @param {{undo?: () => void, timeout?: number, kind?: 'info'|'error'}} [opts]
 * @returns {{dismiss: () => void}}
 */
export function toast(message, opts = {}) {
  const kind = opts.kind ?? 'info';
  const timeout = opts.timeout ?? (opts.undo ? 10000 : kind === 'error' ? 8000 : 4000);
  const container = host();
  /** @type {ReturnType<typeof setTimeout>|null} */
  let timer = null;
  let remaining = timeout;
  let started = Date.now();
  let gone = false;

  seq += 1;
  const textId = `toast-${seq}`;
  const text = h('span', { id: textId, class: 'toast__text', role: kind === 'error' ? 'alert' : null }, message);
  const el = h('div', { class: `toast toast--${kind}` }, text);
  const dismiss = () => {
    if (gone) return;
    gone = true;
    if (timer) clearTimeout(timer);
    const hadFocus = el.contains(document.activeElement);
    el.remove();
    if (hadFocus) document.body.focus();
  };
  if (opts.undo) {
    const undo = opts.undo;
    el.appendChild(h('button', {
      type: 'button',
      class: 'toast__action',
      'aria-describedby': textId,
      onClick: () => {
        dismiss();
        undo();
      },
    }, 'Undo'));
  }
  el.appendChild(h('button', {
    type: 'button', class: 'toast__close', 'aria-label': 'Dismiss', onClick: dismiss,
  }, icon('close', { size: 14 })));

  const start = () => {
    if (gone || timeout <= 0) return;
    started = Date.now();
    timer = setTimeout(dismiss, remaining);
  };
  const pause = () => {
    if (!timer) return;
    clearTimeout(timer);
    timer = null;
    remaining = Math.max(1000, remaining - (Date.now() - started));
  };
  el.addEventListener('mouseenter', pause);
  el.addEventListener('mouseleave', () => { if (!el.contains(document.activeElement)) start(); });
  el.addEventListener('focusin', pause);
  el.addEventListener('focusout', (e) => {
    if (!el.contains(/** @type {Node|null} */ (e.relatedTarget))) start();
  });

  while (container.children.length >= MAX_TOASTS) container.firstElementChild?.remove();
  container.appendChild(el);
  start();
  return { dismiss };
}

/**
 * Error toast for a failed action: uses the server's user-facing message when there is one.
 * @param {unknown} err @param {string} [fallback]
 */
export function toastError(err, fallback = 'Something went wrong. Try again.') {
  const message = err instanceof Error && err.message ? err.message : fallback;
  return toast(message, { kind: 'error' });
}
