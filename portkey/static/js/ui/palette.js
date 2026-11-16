/*
 * palette.js — owner: package 8b.
 *
 * Quick-connect palette: fuzzy search over name, host, user, folder path, tags (#prod
 * narrows by tag) and notes, ranked by recent use; open-session hints; "Add <text> as a new
 * connection…"; commands (Generate key, Keys, Settings, Lock keys); Enter / Shift+Enter / Esc.
 *
 * The list is a listbox driven from the text field (aria-activedescendant): Up/Down move,
 * Enter connects (or focuses the open session), Shift+Enter opens another session, Esc closes.
 * The last row is always "Add <text> as a new connection…", which opens the wizard with the
 * text parsed by parseTarget(). Opened by the shell (top-bar field, Ctrl+Shift+K, Ctrl+Shift+Space,
 * Ctrl+K outside the terminal).
 */

import * as store from '../store.js';
import { h, clear, labelClass, formatAddress, formatWhen } from './dom.js';
import { toastError } from './toast.js';
import { icon } from '../parts/icons.js';
import { openFrame } from '../wizard/frame.js';
import { openSession, sessionCount, closeAllSessions } from '../views/sessions.js';
import { openWizard } from '../views/wizard.js';
import { openToolTab, lockAllKeys } from '../shell.js';
import { openGenerateKey } from '../views/keys.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {{kind: 'conn', conn: Connection} | {kind: 'cmd', label: string, run: () => unknown} | {kind: 'add', text: string}} Row */

const MAX_ROWS = 50;

/** @param {string} query @returns {{terms: string[], tags: string[]}} */
export function parseQuery(query) {
  const terms = [];
  const tags = [];
  for (const raw of query.toLowerCase().split(/\s+/)) {
    if (!raw) continue;
    if (raw.startsWith('#')) {
      const tag = raw.replace(/^#+/, '');
      if (tag) tags.push(tag);
    } else {
      terms.push(raw);
    }
  }
  return { terms, tags };
}

/** Characters of `needle` appear in order in `hay`. @param {string} needle @param {string} hay */
function subsequence(needle, hay) {
  let i = 0;
  for (const ch of hay) if (ch === needle[i]) i += 1;
  return i === needle.length;
}

/** @param {string} term @param {string} value @param {number[]} w [equal, prefix, contains, subsequence] */
function fieldScore(term, value, w) {
  const v = value.toLowerCase();
  if (!v) return 0;
  if (v === term) return w[0];
  if (v.startsWith(term)) return w[1];
  if (v.includes(term)) return w[2];
  return w[3] && term.length > 1 && subsequence(term, v) ? w[3] : 0;
}

/** @param {Connection} c @param {number} now */
function recency(c, now) {
  let r = Math.min(10, Math.log2(1 + (c.use_count || 0)) * 2);
  const t = c.last_used_at ? Date.parse(c.last_used_at) : NaN;
  if (!Number.isNaN(t)) r += Math.max(0, 30 - (now - t) / 86400000) / 2;
  return r;
}

/**
 * @param {Connection[]} connections @param {string} query
 * @param {(groupId: string|null) => string} [pathOf] folder path for a group id
 * @returns {Connection[]} ranked (pure; unit-testable)
 */
export function rankConnections(connections, query, pathOf = (id) => store.folderPath(id)) {
  const { terms, tags } = parseQuery(query);
  const now = Date.now();
  /** @type {{c: Connection, score: number}[]} */
  const scored = [];
  for (const c of connections) {
    if (!tags.every((t) => c.tags.some((ct) => ct === t || ct.startsWith(t)))) continue;
    let score = 0;
    let ok = true;
    for (const term of terms) {
      const best = Math.max(
        fieldScore(term, c.name, [100, 80, 60, 30]),
        fieldScore(term, c.host, [70, 55, 45, 0]),
        fieldScore(term, `${c.username}@${c.host}`, [70, 50, 40, 0]),
        fieldScore(term, c.username, [40, 30, 20, 0]),
        fieldScore(term, pathOf(c.group_id), [25, 20, 20, 0]),
        ...c.tags.map((t) => fieldScore(term, t, [35, 25, 15, 0])),
        fieldScore(term, c.notes, [10, 10, 10, 0]),
      );
      if (!best) {
        ok = false;
        break;
      }
      score += best;
    }
    if (ok) scored.push({ c, score: score + recency(c, now) });
  }
  scored.sort((a, b) => b.score - a.score || a.c.name.localeCompare(b.c.name));
  return scored.map((s) => s.c);
}

/** @param {string} text @returns {{username: string|null, host: string, port: number|null}} parses user@host:port */
export function parseTarget(text) {
  let s = text.trim();
  let username = null;
  /** @type {number|null} */
  let port = null;
  if (/^ssh\s/.test(s)) {
    const tokens = s.split(/\s+/).slice(1);
    let dest = '';
    for (let i = 0; i < tokens.length; i += 1) {
      const t = tokens[i];
      if (t === '-p' || t === '-l') {
        const v = tokens[i + 1] ?? '';
        if (t === '-p' && /^\d+$/.test(v)) port = Number(v);
        if (t === '-l' && v) username = v;
        i += 1;
      } else if (/^-[ioJFEFLRDWw]$/.test(t)) {
        i += 1;
      } else if (!t.startsWith('-')) {
        dest = t;
      }
    }
    s = dest;
  }
  s = s.replace(/^ssh:\/\//, '').replace(/\/+$/, '');
  const at = s.lastIndexOf('@');
  if (at > 0) {
    username = s.slice(0, at);
    s = s.slice(at + 1);
  }
  let host = s;
  const bracket = /^\[([^\]]+)\](?::(\d+))?$/.exec(s);
  if (bracket) {
    host = bracket[1];
    if (bracket[2]) port = Number(bracket[2]);
  } else if ((s.match(/:/g) ?? []).length === 1) {
    const [hPart, pPart] = s.split(':');
    host = hPart;
    if (/^\d+$/.test(pPart)) port = Number(pPart);
  }
  if (port !== null && (port < 1 || port > 65535)) port = null;
  return { username: username || null, host, port };
}

/** @type {{input: HTMLInputElement}|null} */
let current = null;

/** @returns {void} */
export function openPalette() {
  if (current) {
    current.input.focus();
    return;
  }
  const input = /** @type {HTMLInputElement} */ (h('input', {
    class: 'palette__input', role: 'combobox', 'aria-label': 'Connect to', 'aria-expanded': 'true',
    'aria-controls': 'palette-list', 'aria-autocomplete': 'list', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'Name, host, user, folder or #tag',
  }));
  const list = h('ul', { id: 'palette-list', class: 'palette__list', role: 'listbox', 'aria-label': 'Matches' });
  const hints = h('div', { class: 'palette__hints', 'aria-hidden': 'true' },
    h('span', {}, '↑↓ to move'), h('span', {}, 'Enter to connect'), h('span', {}, 'Shift+Enter for another session'),
    h('span', { class: 'palette__esc' }, 'Esc to close'));
  const frame = openFrame({
    className: 'palette', label: 'Quick connect', onDismiss: () => close(),
    content: h('div', { class: 'palette__box' }, input, list, hints),
  });
  current = { input };
  /** @type {Row[]} */
  let rows = [];
  let active = 0;

  const close = () => {
    current = null;
    frame.close();
  };
  const render = () => {
    rows = buildRows(input.value);
    active = Math.min(active, rows.length - 1);
    clear(list);
    rows.forEach((row, i) => list.appendChild(rowElement(row, i, i === active, () => {
      active = i;
      paint();
    }, (shift) => run(row, shift))));
    paint();
  };
  const paint = () => {
    for (const el of list.children) {
      const on = Number(/** @type {HTMLElement} */ (el).dataset.index) === active;
      el.classList.toggle('is-active', on);
      el.setAttribute('aria-selected', String(on));
      if (on) {
        input.setAttribute('aria-activedescendant', el.id);
        el.scrollIntoView({ block: 'nearest' });
      }
    }
  };
  /** @param {Row} row @param {boolean} shift */
  const run = (row, shift) => {
    close();
    if (row.kind === 'conn') {
      openSession(row.conn.id, { newSession: shift }).catch((err) => toastError(err));
    } else if (row.kind === 'cmd') {
      Promise.resolve(row.run()).catch((err) => toastError(err));
    } else {
      const t = row.text.trim() ? parseTarget(row.text) : null;
      /** @type {Parameters<typeof openWizard>[0]} */
      const prefill = {};
      if (t?.host) Object.assign(prefill, { host: t.host }, t.port ? { port: t.port } : {}, t.username ? { username: t.username } : {});
      openWizard(prefill).then((saved) => (saved ? store.refresh() : undefined)).catch((err) => toastError(err));
    }
  };

  input.addEventListener('input', () => {
    active = 0;
    render();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      active = (active + step + rows.length) % rows.length;
      paint();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const row = rows[active];
      if (row) run(row, e.shiftKey);
    }
  });
  render();
  input.focus();
}

/** @param {string} text @returns {Row[]} */
function buildRows(text) {
  const s = store.getState();
  const ranked = text.trim()
    ? rankConnections(s.connections, text)
    : [...s.connections].sort((a, b) => (b.last_used_at ?? '').localeCompare(a.last_used_at ?? '') || b.use_count - a.use_count || a.name.localeCompare(b.name));
  /** @type {Row[]} */
  const rows = ranked.slice(0, MAX_ROWS).map((conn) => ({ kind: 'conn', conn }));
  const { terms, tags } = parseQuery(text);
  const open = [...s.connections].reduce((n, c) => n + sessionCount(c.id), 0);
  const commands = [
    { label: 'Generate key', run: () => openGenerateKey() },
    { label: 'Keys', run: () => openToolTab('keys') },
    { label: 'Settings', run: () => openToolTab('settings') },
    { label: 'Lock keys', run: () => lockAllKeys() },
    ...(open ? [{ label: 'Close all sessions', run: () => closeAllSessions() }] : []),
  ];
  if (!tags.length) {
    for (const cmd of commands) {
      const label = cmd.label.toLowerCase();
      if (terms.every((t) => label.includes(t))) rows.push({ kind: 'cmd', ...cmd });
    }
  }
  rows.push({ kind: 'add', text });
  return rows;
}

/**
 * @param {Row} row @param {number} i @param {boolean} on
 * @param {() => void} hover @param {(shift: boolean) => void} pick
 */
function rowElement(row, i, on, hover, pick) {
  const li = h('li', {
    id: `palette-opt-${i}`, class: `palette__row palette__row--${row.kind}${on ? ' is-active' : ''}`, role: 'option',
    'aria-selected': String(on), dataset: { index: String(i) },
  });
  li.addEventListener('mousemove', hover);
  li.addEventListener('mousedown', (e) => e.preventDefault());
  li.addEventListener('click', (e) => pick(e.shiftKey));
  if (row.kind === 'conn') {
    const c = row.conn;
    const n = sessionCount(c.id);
    li.append(
      h('span', { class: `palette__chip ${labelClass(c.color)}`, 'aria-hidden': 'true' }),
      h('span', { class: 'palette__name' }, c.name),
      h('span', { class: 'palette__addr mono' }, formatAddress(c)),
      h('span', { class: 'palette__meta' }, n
        ? `${n} session${n === 1 ? '' : 's'} open · Enter to focus`
        : formatWhen(c.last_used_at)));
  } else if (row.kind === 'cmd') {
    li.append(h('span', { class: 'palette__icon', 'aria-hidden': 'true' }, icon('chevron', { size: 12 })),
      h('span', { class: 'palette__name palette__name--cmd' }, row.label), h('span', { class: 'palette__meta' }, 'Command'));
  } else {
    li.append(h('span', { class: 'palette__icon', 'aria-hidden': 'true' }, icon('plus', { size: 12 })),
      row.text.trim()
        ? h('span', {}, 'Add ', h('span', { class: 'mono palette__text' }, row.text.trim()), ' as a new connection…')
        : h('span', {}, 'Add connection…'));
  }
  return li;
}
