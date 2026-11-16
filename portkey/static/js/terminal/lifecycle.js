/*
 * terminal/lifecycle.js — owner: package 8b (private module of terminal/session.js).
 *
 * What a TerminalView does when its socket ends:
 * - while connecting: Cancel closes the tab; a failure replaces the terminal with a panel (the
 *   "refused your key" panel with Reinstall key / Sign in with password this time / Edit
 *   connection, or a generic one with Try again);
 * - with a session: `exit` shows the Disconnected bar, `detached` (attached elsewhere) offers
 *   Reattach here, a vanished session (4410) shows Disconnected, anything else (a drop, a dead
 *   link, a server restart, 1011, an error after `ready`, which leaves the session detached on
 *   the server) reattaches with backoff for up to the 15-minute detach grace.
 * - a refused token signs out (the shell shows the sign-in dialog); reattaching waits for it.
 */

import * as api from '../api.js';
import { closeState } from '../ws/protocol.js';
import { failureText, disconnectedText, offeredText } from './messages.js';

/** @typedef {import('./session.js').TerminalView} TerminalView */
/** @typedef {import('../ws/protocol.js').PortKeySocket} PortKeySocket */

const BACKOFF = [300, 1000, 2000, 4000, 8000, 15000];
const GIVE_UP_MS = 15 * 60 * 1000;
/** @type {WeakMap<TerminalView, number>} when the current run of reattach attempts began */
const droppedAt = new WeakMap();

function signOut() {
  api.clearToken();
  window.dispatchEvent(new CustomEvent('portkey:signed-out'));
}

/**
 * @param {TerminalView} view @param {PortKeySocket} socket @param {number} code @param {string} reason
 */
export function onSocketClose(view, socket, code, reason) {
  if (view.disposed || socket !== view.socket) return;
  view.socket = null;
  const err = socket.error;
  const state = closeState(code, err);
  if (view.state === 'connecting') {
    connectFailed(view, state, err);
    return;
  }
  if (view.exitReason) {
    showDisconnected(view, view.exitReason);
  } else if (view.detachedElsewhere) {
    showDetached(view);
  } else if (state === 'gone') {
    showDisconnected(view, vanished(view));
  } else {
    if (state === 'signed_out') signOut();
    scheduleReattach(view);
  }
}

/**
 * @param {TerminalView} view
 * @param {ReturnType<typeof closeState>} state @param {import('../types.js').WsError|null} err
 */
function connectFailed(view, state, err) {
  const c = view.connection;
  if (state === 'cancelled') {
    view.ctx.closeTab();
    return;
  }
  view.reset();
  view.chrome.finishSteps('fail');
  view.chrome.hideOverlay();
  view.setState('failed');
  const tryAgain = { label: 'Try again', kind: /** @type {const} */ ('primary'), key: 'retry', onClick: () => view.open() };
  const close = { label: 'Close tab', key: 'close', onClick: () => view.ctx.closeTab() };
  const edit = { label: 'Edit connection', key: 'edit', onClick: () => editConnection(view) };
  if (state === 'signed_out') {
    signOut();
    focusPanel(view.chrome.showPanel({
      title: 'Sign in to Port-Key', body: ['Port-Key signed you out. Sign in again, then select Try again.'], actions: [tryAgain, close],
    }));
    return;
  }
  if (!err || state === 'dropped' || state === 'done') {
    focusPanel(view.chrome.showPanel({
      title: 'Lost the connection to Port-Key',
      body: [`The browser lost its connection to Port-Key while connecting to ${c.name}. Check that Port-Key is running, then try again.`],
      actions: [tryAgain, close],
    }));
    return;
  }
  const text = failureText(err, { name: c.name, keyAuth: c.auth_method === 'key', askedPassword: view.askedPassword });
  if (text.kind === 'key_refused') {
    focusPanel(view.chrome.showPanel({
      title: text.title,
      body: keyRefusedBody(view),
      detail: offeredText(err.methods),
      actions: [
        { label: 'Reinstall key', kind: 'primary', key: 'reinstall', onClick: () => reinstall(view) },
        { label: 'Sign in with password this time', key: 'password', onClick: () => view.reconnect({ forcePassword: true }) },
        { label: 'Edit connection', kind: 'outline', key: 'edit', onClick: () => editConnection(view) },
      ],
    }));
    return;
  }
  focusPanel(view.chrome.showPanel({
    title: text.title,
    body: [text.body],
    detail: offeredText(err.methods),
    actions: text.kind === 'hostkey' ? [tryAgain, close] : [tryAgain, edit, close],
  }));
}

/** "The server rejected id_ed25519 for john. ..." with the key and user as marked text. @param {TerminalView} view */
function keyRefusedBody(view) {
  const c = view.connection;
  const key = view.keyName();
  const mark = (/** @type {string} */ text, /** @type {string} */ cls) => {
    const el = document.createElement('span');
    el.className = cls;
    el.textContent = text;
    return el;
  };
  return ['The server rejected ', mark(key, 'mono brass'), ' for ', mark(c.username, 'mono strong'),
    ". The key may have been removed from the host's authorized_keys, or the user name changed."];
}

/** @param {HTMLElement} heading */
function focusPanel(heading) {
  if (heading.offsetParent !== null && !document.querySelector('dialog[open]')) heading.focus();
}

/** @param {TerminalView} view */
async function reinstall(view) {
  const { openWizard } = await import('../views/wizard.js');
  const c = view.connection;
  const saved = await openWizard({
    host: c.host, port: c.port, username: c.username, name: c.name, group_id: c.group_id, tags: c.tags,
    key_fp: c.key_fp, jump_id: c.jump_id, connection_id: c.id,
  }, { startAt: 'signin' });
  if (saved) {
    const store = await import('../store.js');
    await store.refresh();
    view.refreshInfo(saved);
    view.open();
  }
}

/** @param {TerminalView} view */
async function editConnection(view) {
  const { openEditConnection } = await import('../views/edit-connection.js');
  const saved = await openEditConnection(view.connection);
  if (saved) view.refreshInfo(saved);
}

/**
 * Why a session this tab attaches to is gone: `gone` (the page was away: a restore after a
 * reload that never got `ready`) or `ended` (it was live here; most often Port-Key restarted).
 * @param {TerminalView} view @returns {'gone'|'ended'}
 */
function vanished(view) {
  return view.everReady ? 'ended' : 'gone';
}

/** @param {TerminalView} view @param {'closed'|'lost'|'killed'|'gone'|'ended'} reason */
export function showDisconnected(view, reason) {
  view.reset();
  view.sessionId = null;
  droppedAt.delete(view);
  view.chrome.hideOverlay();
  view.chrome.hidePanel();
  const text = disconnectedText(view.connection.name, reason, new Date());
  view.chrome.showBottom({
    title: text.title,
    body: text.body,
    actions: [
      { label: 'Reconnect', kind: 'primary', key: 'reconnect', onClick: () => view.reconnect() },
      { label: 'Close tab', key: 'close', onClick: () => view.ctx.closeTab() },
    ],
  });
  view.setState('disconnected');
}

/** The session is live in another tab or window. Close tab here leaves it running there. @param {TerminalView} view */
export function showDetached(view) {
  view.reset();
  view.chrome.showBottom({
    title: `${view.connection.name} is open in another tab`,
    body: 'The session is running in another tab or window. Reattach here to take it back; Close tab leaves it running there.',
    actions: [
      { label: 'Reattach here', kind: 'primary', key: 'reattach', onClick: () => view.attach(/** @type {string} */ (view.sessionId)) },
      { label: 'Close tab', key: 'close', onClick: () => view.ctx.closeTab() },
    ],
  });
  view.setState('detached');
}

/** Try to reattach after a drop, with backoff, until the detach grace runs out. @param {TerminalView} view */
export function scheduleReattach(view) {
  if (view.disposed) return;
  if (!view.sessionId) {
    showDisconnected(view, vanished(view));
    return;
  }
  const first = droppedAt.get(view) ?? Date.now();
  droppedAt.set(view, first);
  if (Date.now() - first > GIVE_UP_MS) {
    showDisconnected(view, vanished(view));
    return;
  }
  if (view.state !== 'reattaching') {
    view.setState('reattaching');
    view.chrome.showBottom({ title: `Reattaching to ${view.connection.name}…`, body: '', actions: [], tone: 'info' });
  }
  if (view.retryTimer) clearTimeout(view.retryTimer);
  if (!api.getToken()) {
    // Signed out: wait for the sign-in dialog, without spending attempts.
    view.retryTimer = setTimeout(() => scheduleReattach(view), 1000);
    return;
  }
  const delay = BACKOFF[Math.min(view.attempt, BACKOFF.length - 1)];
  view.attempt += 1;
  view.retryTimer = setTimeout(() => {
    view.retryTimer = null;
    if (!view.disposed && view.sessionId) view.attach(view.sessionId, { since: view.received });
  }, delay);
}

/** The view reached `ready`: the next drop starts a fresh run of attempts. @param {TerminalView} view */
export function attached(view) {
  droppedAt.delete(view);
}
