/*
 * dom.js — owner: package 8a.
 *
 * Small DOM helpers used by every view: element builder, clearing, query, and the
 * shared formatters for addresses ("john@192.168.10.42", port only when not 22), ssh commands
 * ("ssh -p 2222 admin@192.168.10.31") and times ("Today 14:02").
 *
 * h() never sets innerHTML: strings become text nodes, so host names, notes, tags and server
 * messages are always inert. It refuses a `style` attribute because the CSP blocks inline
 * styles; use classes, or element.style (CSSOM) for computed values.
 *
 * Also exported: svg(), qs(), labelClass(), installCommand(), copyText(), formatWhenLong(),
 * keyTypeLabel(), isIpAddress(), preserveFocus().
 */

/** @typedef {import('../types.js').Connection} Connection */

/** Properties that must be set as DOM properties, not attributes. */
const PROPS = new Set(['value', 'checked', 'selected', 'indeterminate', 'readOnly', 'multiple']);

/**
 * Build an element. attrs: `class`, `text`, `dataset`, `onClick`-style listeners, DOM
 * properties (value, checked...), and anything else as an attribute (`aria-*` keep `false`).
 * @param {string} tag @param {Record<string, unknown>} [attrs] @param {...(Node|string|null|undefined)} children
 * @returns {HTMLElement}
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

/**
 * An SVG element (for icons). Same attribute rules as h(), no listeners.
 * @param {string} tag @param {Record<string, string|number>} [attrs] @param {...Element} children
 * @returns {SVGElement}
 */
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  for (const child of children) el.appendChild(child);
  return el;
}

/** @param {Element} el @param {Record<string, unknown>} attrs */
function setAttrs(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null) continue;
    if (value === false && !key.startsWith('aria-')) continue;
    if (key === 'style') throw new Error('Use classes, not style attributes (the CSP blocks them).');
    if (key === 'class' || key === 'className') el.setAttribute('class', String(value));
    else if (key === 'text') el.textContent = String(value);
    else if (key === 'dataset') Object.assign(/** @type {HTMLElement} */ (el).dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), /** @type {EventListener} */ (value));
    } else if (PROPS.has(key)) /** @type {any} */ (el)[key] = value;
    else el.setAttribute(key, value === true ? '' : String(value));
  }
}

/** @param {Element} el @param {(Node|string|null|undefined|false)[]} children */
export function append(el, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    el.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}

/** @param {Element} el */
export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

/**
 * querySelector that throws when the element is missing (programming error, not user input).
 * @template {Element} T
 * @param {ParentNode} root @param {string} selector @returns {T}
 */
export function qs(root, selector) {
  const el = root.querySelector(selector);
  if (!el) throw new Error(`missing element ${selector}`);
  return /** @type {T} */ (el);
}

/**
 * The CSS class that sets --lbl to a connection's colour label ("lbl-red"). Used for sidebar
 * stripes, card and tab top edges and the terminal frame (classes live in layout.css).
 * @param {string|null|undefined} color
 */
export function labelClass(color) {
  return `lbl-${color || 'grey'}`;
}

/** @param {string} host */
export function isIpAddress(host) {
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(':');
}

/** @param {string} host @param {number} port */
function hostPort(host, port) {
  if (port === 22) return host;
  return host.includes(':') ? `[${host}]:${port}` : `${host}:${port}`;
}

/** @param {Connection} connection @returns {string} "john@192.168.10.42", port only when not 22 */
export function formatAddress(connection) {
  return `${connection.username}@${hostPort(connection.host, connection.port)}`;
}

/**
 * "ssh -p 2222 admin@192.168.10.31". With `connections`, a jump chain becomes `-J`.
 * @param {Connection} connection @param {Connection[]} [connections]
 * @returns {string}
 */
export function sshCommand(connection, connections = []) {
  const parts = ['ssh'];
  const jumps = [];
  const byId = new Map(connections.map((c) => [c.id, c]));
  let hop = connection.jump_id ? byId.get(connection.jump_id) : undefined;
  while (hop && jumps.length < 4) {
    jumps.unshift(`${hop.username}@${hostPort(hop.host, hop.port)}`);
    hop = hop.jump_id ? byId.get(hop.jump_id) : undefined;
  }
  if (jumps.length) parts.push('-J', jumps.join(','));
  if (connection.port !== 22) parts.push('-p', String(connection.port));
  parts.push(`${connection.username}@${connection.host}`);
  return parts.join(' ');
}

/** The manual install command from docs/ux-spec.md (wizard manual path, Keys tab). */
export function installCommand(publicKey) {
  const line = publicKey.trim().replace(/'/g, '');
  return `mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '${line}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** @param {Date} a @param {Date} b */
function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** @param {Date} d */
function clock(d) {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * @param {string|null} iso @param {Date} [now]
 * @returns {{kind: 'never'|'today'|'yesterday'|'date', time: string, date: string}}
 */
function when(iso, now = new Date()) {
  if (!iso) return { kind: 'never', time: '', date: '' };
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { kind: 'never', time: '', date: '' };
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let date = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  if (d.getFullYear() !== now.getFullYear()) date += ` ${d.getFullYear()}`;
  if (sameDay(d, now)) return { kind: 'today', time: clock(d), date };
  if (sameDay(d, yesterday)) return { kind: 'yesterday', time: clock(d), date };
  return { kind: 'date', time: clock(d), date };
}

/** @param {string|null} iso @param {Date} [now] @returns {string} "Today 14:02", "Yesterday", "9 Nov", "Never" */
export function formatWhen(iso, now = new Date()) {
  const w = when(iso, now);
  if (w.kind === 'never') return 'Never';
  if (w.kind === 'today') return `Today ${w.time}`;
  if (w.kind === 'yesterday') return 'Yesterday';
  return w.date;
}

/** @param {string|null} iso @param {Date} [now] @returns {string} "Last connected 14:02 today" */
export function formatWhenLong(iso, now = new Date()) {
  const w = when(iso, now);
  if (w.kind === 'never') return 'Never connected';
  if (w.kind === 'today') return `Last connected ${w.time} today`;
  if (w.kind === 'yesterday') return 'Last connected yesterday';
  return `Last connected ${w.date}`;
}

/** @param {string|null|undefined} iso @returns {string} "2 March 2026" */
export function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const long = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];
  return `${d.getDate()} ${long[d.getMonth()]} ${d.getFullYear()}`;
}

/** @param {{type: string, bits: number}} key @returns {string} "ED25519", "RSA 4096", "ECDSA 256" */
export function keyTypeLabel(key) {
  if (key.type === 'ed25519') return 'ED25519';
  return `${key.type.toUpperCase()} ${key.bits}`;
}

/** "ssh-ed25519" -> "ED25519", "ecdsa-sha2-nistp256" -> "ECDSA" @param {string} keyType */
export function sshKeyTypeLabel(keyType) {
  const t = keyType.replace(/^ssh-/, '');
  if (t.startsWith('ecdsa')) return 'ECDSA';
  if (t.startsWith('sk-')) return t.toUpperCase();
  return t.toUpperCase();
}

/**
 * Copy text to the clipboard. Falls back to a hidden textarea and execCommand when the async
 * clipboard API is unavailable or refused.
 * @param {string} text @returns {Promise<boolean>}
 */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the textarea path
  }
  const area = /** @type {HTMLTextAreaElement} */ (h('textarea', { class: 'clip-buffer', readonly: true, 'aria-hidden': 'true' }));
  area.value = text;
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/**
 * Re-render inside `container` and put focus back on the element with the same data-fkey.
 * @param {Element} container @param {() => void} render
 */
export function preserveFocus(container, render) {
  const active = document.activeElement;
  const key = active instanceof HTMLElement && container.contains(active) ? active.dataset.fkey : undefined;
  render();
  if (key) {
    const again = container.querySelector(`[data-fkey="${key.replace(/["\\]/g, '\\$&')}"]`);
    if (again instanceof HTMLElement) again.focus({ preventScroll: true });
  }
}

/** Plural helper: plural(3, 'connection') -> "3 connections". @param {number} n @param {string} word */
export function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}
