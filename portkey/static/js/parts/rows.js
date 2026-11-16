/*
 * parts/rows.js — owner: package 8a (private module; 8b may reuse reachDot for the palette).
 *
 * Pieces shared by sidebar rows, Recent cards and the Home table:
 *   reachInfo(c) / reachDot(c)  the reachability dot: filled green (up), red ring (down), grey
 *                               ring (not checked), filled grey (behind a jump host); each with
 *                               the tooltip copy from docs/ux-spec.md and hidden text, so the
 *                               state never depends on colour alone
 *   authGlyph(c)                brass key or grey "pw"
 *   sessionBadge(c)             open-sessions count
 *   labelChip(c)                the small square colour chip
 */

import { h, labelClass } from '../ui/dom.js';
import { icon } from './icons.js';
import * as store from '../store.js';

/** @typedef {import('../types.js').Connection} Connection */

/** @param {Connection} c @returns {{state: 'up'|'down'|'unknown'|'skipped', text: string, short: string}} */
export function reachInfo(c) {
  const { reach, connections } = store.getState();
  if (c.jump_id) {
    const via = connections.find((x) => x.id === c.jump_id);
    return { state: 'skipped', text: `Checked when you connect, reached via ${via ? via.name : 'a jump host'}`, short: 'checked when you connect' };
  }
  const r = reach[c.id];
  if (!r) return { state: 'unknown', text: 'Not checked yet', short: 'not checked' };
  if (r.state === 'up') return { state: 'up', text: `Port ${c.port} reachable, ${r.ms ?? 0} ms`, short: 'reachable' };
  if (r.state === 'down') return { state: 'down', text: `No response on port ${c.port}`, short: 'no response' };
  return { state: 'skipped', text: 'Checked when you connect', short: 'checked when you connect' };
}

/** @param {Connection} c @returns {HTMLElement} */
export function reachDot(c) {
  const info = reachInfo(c);
  return h('span', { class: `dot dot--${info.state}`, title: info.text },
    h('span', { class: 'visually-hidden' }, `${info.short}.`));
}

/** @param {Connection} c @returns {Element} */
export function authGlyph(c) {
  if (c.auth_method === 'password') {
    return h('span', { class: 'pw-glyph', title: 'Password only', 'aria-label': 'Password only', role: 'img' }, 'pw');
  }
  const glyph = h('span', { class: 'key-glyph', title: `Signs in with ${store.signsInWith(c)}` });
  glyph.appendChild(icon('key', { size: 16, label: 'Signs in with a key' }));
  return glyph;
}

/** @param {Connection} c @returns {HTMLElement|null} */
export function sessionBadge(c) {
  const n = store.getState().sessionCounts[c.id] ?? 0;
  if (!n) return null;
  return h('span', { class: 'badge', title: `${n} open session${n === 1 ? '' : 's'}` },
    String(n), h('span', { class: 'visually-hidden' }, ` open session${n === 1 ? '' : 's'}`));
}

/** @param {string|null|undefined} color @returns {HTMLElement} */
export function labelChip(color) {
  return h('span', { class: `label-chip ${labelClass(color)}`, 'aria-hidden': 'true' });
}
