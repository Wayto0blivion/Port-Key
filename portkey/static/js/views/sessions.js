/*
 * sessions.js — owner: package 8b.
 *
 * Session orchestration: open a session for a connection (focus an existing one unless
 * a new one is asked for), restore tabs after a reload by reattaching (`since: null`), Close all
 * sessions with a count confirmation, and the "(deleted)" suffix for deleted connections.
 *
 * Each terminal tab is a terminal/session.js TerminalView in a ui/tabstrip.js tab. Open sessions
 * are remembered per browser tab in sessionStorage ("portkey.sessions": session ids in tab order
 * and the active one), so a reload brings the same tabs back: live sessions reattach with a full
 * replay, ended ones come back Disconnected. Detached sessions the server still holds that no
 * tab remembers (a closed window) are restored too; attached ones belong to another window and
 * are left alone. A remembered session that another page says it holds (a duplicated browser
 * tab, terminal/claims.js) is shown as "open in another tab", not taken. Every change reports
 * counts to store.setSessionInfo() (sidebar badges, Recent cards, the leave warning).
 *
 * A− / A+ change the font size of every terminal and save it as the Settings value.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { addTab, activateTab, closeTab, listTabs, getActiveTabId, updateTab, onTabChange } from '../ui/tabstrip.js';
import { toast } from '../ui/toast.js';
import { confirmCloseAll } from '../ui/dialogs.js';
import { TerminalView } from '../terminal/session.js';
import { sessionTitle, nextOrdinal, forgetTab } from '../terminal/tabs.js';
import { clampFont } from '../terminal/xterm.js';
import { answerClaims, claimedElsewhere } from '../terminal/claims.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {{tabId: string, connectionId: string, ordinal: number, view: TerminalView|null, title: string}} Entry */

const PERSIST_KEY = 'portkey.sessions';
/** @type {Map<string, Entry>} by tab id */
const entries = new Map();
let seq = 0;
let restored = false;
let restoring = false;
let subscribed = false;
/** @type {number|null} */
let fontOverride = null;
/** @type {ReturnType<typeof setTimeout>|null} */
let fontTimer = null;

/** Live sessions by connection id, for the palette. @param {string} connectionId */
export function sessionCount(connectionId) {
  let n = 0;
  for (const e of entries.values()) if (e.connectionId === connectionId && isLive(e)) n += 1;
  return n;
}

/** The TerminalView shown in a tab, or null (tests read its byte count). @param {string} tabId */
export function viewFor(tabId) {
  return entries.get(tabId)?.view ?? null;
}

/** @param {Entry} e */
function isLive(e) {
  const s = e.view?.state;
  return s === 'connecting' || s === 'connected' || s === 'reattaching' || s === 'detached';
}

/** @param {string} connectionId @param {{newSession?: boolean, forcePassword?: boolean}} [opts] @returns {Promise<void>} */
export async function openSession(connectionId, opts = {}) {
  watchStore();
  let conn = store.connectionById(connectionId);
  if (!conn) {
    await store.refreshConnections().catch(() => {});
    conn = store.connectionById(connectionId);
  }
  if (!conn) {
    toast('That connection no longer exists.', { kind: 'error' });
    return;
  }
  if (!opts.newSession && !opts.forcePassword) {
    const mine = [...entries.values()].filter((e) => e.connectionId === connectionId);
    const best = mine.find((e) => e.view?.state === 'connected') ?? mine.find(isLive) ?? mine[0];
    if (best) {
      activateTab(best.tabId);
      best.view?.focus();
      return;
    }
  }
  const entry = createTab(conn, true);
  await entry.view?.open({ forcePassword: Boolean(opts.forcePassword) });
}

/** @param {Connection} conn @param {boolean} activate @returns {Entry} */
function createTab(conn, activate) {
  seq += 1;
  const tabId = `session-${seq}`;
  const used = [...entries.values()].filter((e) => e.connectionId === conn.id).map((e) => e.ordinal);
  /** @type {Entry} */
  const entry = { tabId, connectionId: conn.id, ordinal: nextOrdinal(used), view: null, title: '' };
  entry.title = sessionTitle(conn.name, entry.ordinal);
  entries.set(tabId, entry);
  addTab({
    id: tabId,
    title: entry.title,
    closable: true,
    color: conn.color,
    activate,
    render: (panel) => {
      entry.view = new TerminalView(panel, conn, {
        tabId,
        onChange: () => sync(),
        closeTab: () => closeTab(tabId),
        onFont: (delta) => setFont(currentFont() + delta),
        fontSize: () => currentFont(),
      });
    },
    onActivate: () => entry.view?.show(),
    onClose: () => {
      entries.delete(tabId);
      forgetTab(tabId);
      entry.view?.kill();
      sync();
    },
  });
  sync();
  return entry;
}

/** Keep titles, colours, counts and the remembered list in step with the sessions. */
function sync() {
  const s = store.getState();
  /** @type {Record<string, number>} */
  const counts = {};
  let connected = 0;
  for (const e of entries.values()) {
    if (isLive(e)) counts[e.connectionId] = (counts[e.connectionId] ?? 0) + 1;
    if (e.view?.state === 'connected' || e.view?.state === 'reattaching') connected += 1;
    const conn = store.connectionById(e.connectionId) ?? e.view?.connection;
    if (!conn) continue;
    const deleted = s.status.connections === 'ready' && !store.connectionById(e.connectionId);
    const title = `${sessionTitle(conn.name, e.ordinal)}${deleted ? ' (deleted)' : ''}`;
    if (title !== e.title) {
      e.title = title;
      updateTab(e.tabId, { title });
    }
  }
  const prev = s.sessionCounts;
  const same = s.connectedSessions === connected && Object.keys(prev).length === Object.keys(counts).length
    && Object.entries(counts).every(([k, v]) => prev[k] === v);
  if (!same) store.setSessionInfo({ counts, connected });
  persist();
}

function persist() {
  if (restoring) return;
  const order = listTabs();
  const tabs = order.map((id) => entries.get(id)).filter((e) => e?.view?.sessionId)
    .map((e) => ({ s: /** @type {Entry} */ (e).view?.sessionId, c: /** @type {Entry} */ (e).connectionId }));
  const active = entries.get(getActiveTabId() ?? '')?.view?.sessionId ?? null;
  try {
    sessionStorage.setItem(PERSIST_KEY, JSON.stringify({ tabs, active }));
  } catch {
    // per-tab convenience only
  }
}

/** @returns {{tabs: {s: string, c: string}[], active: string|null}} */
function remembered() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(PERSIST_KEY) ?? '{}');
    const tabs = Array.isArray(raw.tabs) ? raw.tabs.filter((t) => typeof t?.s === 'string' && typeof t?.c === 'string') : [];
    return { tabs, active: typeof raw.active === 'string' ? raw.active : null };
  } catch {
    return { tabs: [], active: null };
  }
}

/** Sessions this page shows or is taking back, for another page that asks (terminal/claims.js). */
function heldHere() {
  const out = [];
  for (const e of entries.values()) {
    const s = e.view?.state;
    if (e.view?.sessionId && (s === 'connected' || s === 'reattaching')) out.push(e.view.sessionId);
  }
  return out;
}

/** React to store changes: renamed, recoloured or deleted connections, the font size setting. */
function watchStore() {
  if (subscribed) return;
  subscribed = true;
  answerClaims(heldHere);
  /** @type {unknown} */
  let lastConns = null;
  /** @type {number|undefined} */
  let lastFont;
  onTabChange(() => persist());
  store.subscribe((s) => {
    if (s.connections !== lastConns) {
      lastConns = s.connections;
      for (const e of entries.values()) {
        const conn = store.connectionById(e.connectionId);
        if (conn) {
          e.view?.refreshInfo(conn);
          updateTab(e.tabId, { color: conn.color });
        } else if (s.status.connections === 'ready') {
          e.view?.refreshInfo(undefined, true);
        }
      }
      sync();
    }
    const font = s.settings?.font_size;
    if (font !== lastFont) {
      lastFont = font;
      if (fontOverride === null) applyFont(currentFont());
    }
  });
}

function currentFont() {
  return clampFont(fontOverride ?? store.getState().settings?.font_size ?? 14);
}

/** @param {number} size */
function applyFont(size) {
  for (const e of entries.values()) e.view?.setFontSize(size);
}

/** A− / A+: every terminal now, saved as the setting a moment later. @param {number} size */
function setFont(size) {
  const next = clampFont(size);
  fontOverride = next;
  applyFont(next);
  if (fontTimer) clearTimeout(fontTimer);
  fontTimer = setTimeout(async () => {
    fontTimer = null;
    try {
      const settings = await api.updateSettings({ font_size: next });
      store.setState({ settings });
    } catch {
      // keep the local size; the setting stays as it was
    }
    fontOverride = null;
  }, 600);
}

/** Wait until the first connections load has finished (main.js calls restore before it). */
function connectionsLoaded() {
  return new Promise((resolve) => {
    const done = () => {
      const st = store.getState().status.connections;
      return st === 'ready' || st === 'error';
    };
    if (done()) {
      resolve(undefined);
      return;
    }
    const off = store.subscribe(() => {
      if (done()) {
        off();
        resolve(undefined);
      }
    });
  });
}

/** @param {string} id @returns {Connection} a stand-in until `ready` brings the real one */
function standIn(id) {
  return {
    id, name: 'Deleted connection', host: '', port: 22, username: '', auth_method: 'key', key_fp: null, jump_id: null,
    group_id: null, tags: [], color: 'grey', pinned: false, notes: '', sort_order: 0, created_at: '', updated_at: '',
    last_used_at: null, use_count: 0,
  };
}

/** @returns {Promise<void>} */
export async function restoreSessions() {
  if (restored) return;
  restored = true;
  // Read the list before anything can write it, and keep it until the tabs are back.
  const saved = remembered();
  restoring = true;
  try {
    await restoreFrom(saved);
  } finally {
    restoring = false;
    persist();
  }
}

/** @param {{tabs: {s: string, c: string}[], active: string|null}} saved */
async function restoreFrom(saved) {
  watchStore();
  await connectionsLoaded();
  /** @type {import('../types.js').SessionInfo[]|null} */
  let live = null;
  try {
    live = await api.listSessions();
  } catch {
    live = null;
  }
  // Remembered tabs always try to attach: a session that ended while the page was away is not
  // listed any more but still hands over its last output and `exit` once; a gone one answers
  // 4410 and the tab shows Disconnected.
  /** @type {{sessionId: string, connectionId: string, startedAt: string|null}[]} */
  const items = [];
  const seen = new Set();
  for (const t of saved.tabs) {
    if (seen.has(t.s)) continue;
    seen.add(t.s);
    const info = live?.find((x) => x.session_id === t.s);
    items.push({ sessionId: t.s, connectionId: t.c, startedAt: info?.started_at ?? null });
  }
  // A duplicated browser tab starts with this list too: sessions another page is showing stay
  // there (listed as attached and claimed by that page), instead of being taken from it.
  const attachedIds = items.filter((i) => live?.some((x) => x.session_id === i.sessionId && x.attached)).map((i) => i.sessionId);
  const elsewhere = await claimedElsewhere(attachedIds);
  for (const s of [...(live ?? [])].sort((a, b) => a.started_at.localeCompare(b.started_at))) {
    if (seen.has(s.session_id) || s.attached) continue;
    seen.add(s.session_id);
    items.push({ sessionId: s.session_id, connectionId: s.connection_id, startedAt: s.started_at });
  }
  /** @type {string|null} */
  let focusTab = null;
  for (const item of items) {
    const conn = store.connectionById(item.connectionId) ?? standIn(item.connectionId);
    const entry = createTab(conn, false);
    const view = entry.view;
    if (!view) continue;
    if (item.sessionId === saved.active) focusTab = entry.tabId;
    if (elsewhere.has(item.sessionId)) view.showElsewhere(item.sessionId, item.startedAt);
    else view.attach(item.sessionId, { since: null, startedAt: item.startedAt });
  }
  if (focusTab) activateTab(focusTab);
  sync();
}

/** @returns {Promise<void>} */
export async function closeAllSessions() {
  const ids = [...entries.keys()];
  if (!ids.length) return;
  if (!(await confirmCloseAll(ids.length))) return;
  for (const id of ids) closeTab(id);
}
