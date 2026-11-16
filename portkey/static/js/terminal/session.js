/*
 * session.js — owner: package 8b.
 *
 * One terminal tab: an xterm.js Terminal (DOM renderer, no clipboard/OSC 52 addon) with
 * the fit and search addons, bound to a /ws/terminal socket from ws/protocol.js. Tracks the
 * received byte count for `attach.since`, handles connecting / connected / reattaching /
 * disconnected / auth-failed states, Windows Terminal copy/paste, the multi-line paste guard,
 * Find (Ctrl+Shift+F), font size, and the 2px colour frame.
 *
 * Bytes: `received` is ready.offset plus every output byte since; a dropped socket reattaches
 * with since = received, so nothing is shown twice or lost. ready.reset on an attach clears the
 * terminal before the replay. A dead SSH connection is never reopened on its own: the tab shows
 * Disconnected and Enter or Reconnect opens a new shell in the same tab.
 *
 * The owner (views/sessions.js) passes callbacks in `ctx`; DOM pieces live in chrome.js, keys
 * and clipboard in input.js, the connect-failure panel and reattach loop in lifecycle.js.
 */

import * as store from '../store.js';
import { PortKeySocket } from '../ws/protocol.js';
import { answerPrompt } from '../ui/dialogs.js';
import { buildChrome } from './chrome.js';
import { createTerminal, DEFAULT_SIZE } from './xterm.js';
import { wireInput } from './input.js';
import { statusLine, connectedFor, hostPort } from './messages.js';
import { setSessionState, markActivity, restoreDot, movingThroughTabs } from './tabs.js';
import { onSocketClose, showDisconnected, showDetached, attached } from './lifecycle.js';
import { setFind, findIn, copySsh, killSession } from './tools.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {'idle'|'connecting'|'connected'|'reattaching'|'detached'|'disconnected'|'failed'} State */

/**
 * @typedef {object} SessionContext
 * @property {string} tabId
 * @property {() => void} onChange      state, session id or connection changed
 * @property {() => void} closeTab
 * @property {(delta: number) => void} onFont
 * @property {() => number} fontSize
 */

export class TerminalView {
  /** @param {HTMLElement} container @param {Connection} connection @param {SessionContext} [ctx] */
  constructor(container, connection, ctx) {
    this.container = container;
    this.connection = connection;
    /** @type {SessionContext} */
    this.ctx = ctx ?? { tabId: '', onChange: () => {}, closeTab: () => {}, onFont: () => {}, fontSize: () => 14 };
    /** @type {State} */
    this.state = 'idle';
    /** @type {string|null} */
    this.sessionId = null;
    this.received = 0;
    /** @type {Date|null} */
    this.startedAt = null;
    /** @type {PortKeySocket|null} */
    this.socket = null;
    /** @type {'closed'|'lost'|'killed'|null} */
    this.exitReason = null;
    this.detachedElsewhere = false;
    this.askedPassword = false;
    this.askedPassphrase = false;
    /** True while the current socket is an attach (a reattach or a restore), not an open. */
    this.attaching = false;
    /** True once this page has shown a live session here (a later 4410 is not "page away"). */
    this.everReady = false;
    this.opened = false;
    this.disposed = false;
    this.attempt = 0;
    this.quietUntil = 0;
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.retryTimer = null;
    /** @type {ReturnType<typeof setInterval>|null} */
    this.clock = null;
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.resizeTimer = null;
    this.lastSize = { cols: 0, rows: 0 };

    const { term, fit, search } = createTerminal(this.ctx.fontSize(), () => this.connection.name);
    this.term = term;
    this.fit = fit;
    this.search = search;
    this.chrome = buildChrome({
      onFind: () => setFind(this, true),
      onCopySsh: () => { copySsh(this.connection); },
      onFont: (delta) => this.ctx.onFont(delta),
      onDisconnect: () => { this.disconnect(); },
      onCancel: () => this.ctx.closeTab(),
      onSearch: (text, back) => findIn(this, text, back),
      onFindClose: () => setFind(this, false),
    });
    container.classList.add('tabpanel--session');
    container.appendChild(this.chrome.root);
    this.refreshInfo();

    term.onData((data) => {
      if (this.state === 'connected') this.socket?.sendInput(data);
    });
    term.onBinary((data) => {
      if (this.state !== 'connected') return;
      const bytes = new Uint8Array(data.length);
      for (let i = 0; i < data.length; i += 1) bytes[i] = data.charCodeAt(i) & 0xff;
      this.socket?.sendInput(bytes);
    });
    term.onResize(() => this.queueResize());
    this.unwire = wireInput(term, this.chrome.termHost, {
      copyPaste: () => store.getState().settings?.windows_copy_paste !== false,
      name: () => this.connection.name,
      onFind: () => setFind(this, true),
      onEnter: () => {
        if (this.state !== 'disconnected') return false;
        this.reconnect();
        return true;
      },
    });
    this.observer = new ResizeObserver(() => this.layout());
    this.observer.observe(this.chrome.termHost);
  }

  /** The tab became visible: open xterm on first show, fit, restore the status dot. */
  show() {
    if (this.disposed) return;
    if (!this.opened) {
      this.term.open(this.chrome.termHost);
      this.opened = true;
    }
    this.layout();
    restoreDot(this.ctx.tabId);
    // After the tab strip has done its own focus work: into the shell, unless the user is
    // moving through the tabs with the arrow keys.
    if (this.state === 'connected' || this.state === 'disconnected') {
      setTimeout(() => {
        if (!this.disposed && !movingThroughTabs() && this.container.offsetParent !== null && !document.querySelector('dialog[open]')) {
          this.term.focus();
        }
      }, 0);
    }
  }

  focus() {
    if (this.opened) this.term.focus();
  }

  layout() {
    if (!this.opened || this.disposed || !this.chrome.termHost.offsetWidth) return;
    try {
      this.fit.fit();
    } catch {
      // not measurable yet (hidden); the next show() fits
    }
  }

  /** @returns {{cols: number, rows: number}|null} */
  size() {
    if (this.opened && this.chrome.termHost.offsetWidth) {
      this.layout();
      return { cols: this.term.cols, rows: this.term.rows };
    }
    return null;
  }

  queueResize() {
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    this.resizeTimer = setTimeout(() => {
      this.resizeTimer = null;
      const { cols, rows } = this.term;
      if (this.state !== 'connected' || (cols === this.lastSize.cols && rows === this.lastSize.rows)) return;
      this.lastSize = { cols, rows };
      this.socket?.send({ type: 'resize', cols, rows });
    }, 120);
  }

  /** @param {State} state */
  setState(state) {
    this.state = state;
    const dot = state === 'connected' ? 'connected' : state === 'connecting' || state === 'reattaching' ? 'connecting' : 'disconnected';
    setSessionState(this.ctx.tabId, dot);
    this.chrome.root.classList.toggle('session--off', state === 'disconnected' || state === 'detached');
    this.chrome.setLive(state === 'connected' || state === 'reattaching');
    if (state === 'connected') this.chrome.setStatus(null);
    else if (state === 'reattaching') this.chrome.setStatus('Reattaching…');
    else if (state === 'disconnected' || state === 'detached') this.chrome.setStatus('Disconnected', 'off');
    else if (state === 'failed') this.chrome.setStatus('Not signed in', 'error');
    else if (state === 'connecting') this.chrome.setStatus('Connecting…');
    this.ctx.onChange();
  }

  /** The connection changed in the store (rename, colour, deleted). @param {Connection} [connection] @param {boolean} [deleted] */
  refreshInfo(connection, deleted = false) {
    if (connection) this.connection = connection;
    const c = this.connection;
    const s = store.getState();
    const byId = new Map(s.connections.map((x) => [x.id, x]));
    const chain = [];
    let hop = c.jump_id ? byId.get(c.jump_id) : undefined;
    while (hop && chain.length < 4) {
      chain.unshift(hop.name);
      hop = hop.jump_id ? byId.get(hop.jump_id) : undefined;
    }
    this.chrome.setInfo({
      color: c.color, user: c.username, name: deleted ? `${c.name} (deleted)` : c.name,
      where: hostPort(c.host, c.port), via: chain.join(' → '), password: c.auth_method === 'password',
      key: this.keyName(),
    });
  }

  /** The key this connection signs in with: "id_ed25519". */
  keyName() {
    const c = this.connection;
    const key = c.key_fp ? store.keyByFp(c.key_fp) : store.defaultKey();
    return key?.name ?? (c.key_fp ? 'your key' : 'the default key');
  }

  // --- opening and attaching ----------------------------------------------------------------

  /** @param {{forcePassword?: boolean}} [opts] @returns {Promise<void>} */
  async open(opts = {}) {
    this.reset();
    this.askedPassword = false;
    this.chrome.hidePanel();
    this.chrome.hideBottom();
    this.chrome.showConnecting(`Connecting to ${this.connection.name}…`);
    this.chrome.step('reach', `Reaching ${hostPort(this.connection.host, this.connection.port)}…`);
    this.setState('connecting');
    const socket = this.newSocket(false);
    try {
      await socket.connect();
    } catch {
      onSocketClose(this, socket, 1006, '');
      return;
    }
    const size = this.size() ?? DEFAULT_SIZE;
    this.lastSize = size;
    socket.send({ type: 'open', connection_id: this.connection.id, cols: size.cols, rows: size.rows, force_password: Boolean(opts.forcePassword) });
  }

  /**
   * Attach to a running session: after a reload (`since` null, full replay) or a drop.
   * @param {string} sessionId @param {{since?: number|null, startedAt?: string|null}} [opts]
   * @returns {Promise<void>}
   */
  async attach(sessionId, opts = {}) {
    this.sessionId = sessionId;
    if (opts.startedAt) this.startedAt = new Date(opts.startedAt);
    const since = opts.since === undefined ? this.received : opts.since;
    if (this.state !== 'reattaching') {
      this.setState('reattaching');
      this.chrome.showBottom({ title: `Reattaching to ${this.connection.name}…`, body: '', actions: [], tone: 'info' });
    }
    const socket = this.newSocket(true);
    try {
      await socket.connect();
    } catch {
      onSocketClose(this, socket, 1006, '');
      return;
    }
    /** @type {Record<string, unknown>} */
    const msg = { type: 'attach', session_id: sessionId, since };
    const size = this.size();
    if (size) Object.assign(msg, size);
    socket.send(msg);
  }

  /** @param {boolean} attaching @returns {PortKeySocket} */
  newSocket(attaching) {
    const old = this.socket;
    this.socket = null; // detach first, so the old socket's close is not taken for a drop
    old?.close();
    this.exitReason = null;
    this.detachedElsewhere = false;
    const socket = new PortKeySocket('/ws/terminal', {
      onMessage: (msg) => this.onMessage(socket, msg),
      onPrompt: (prompt, signal) => answerPrompt(prompt, {
        signal, targetName: this.connection.name, passphraseTtl: store.getState().settings?.passphrase_ttl,
      }),
      onOutput: (data) => this.output(data),
      onClose: (code, reason) => onSocketClose(this, socket, code, reason),
    });
    this.attaching = attaching;
    this.socket = socket;
    return socket;
  }

  /** @param {PortKeySocket} socket @param {Record<string, any>} msg */
  onMessage(socket, msg) {
    if (socket !== this.socket) return;
    if (msg.type === 'status') {
      const line = statusLine(/** @type {any} */ (msg));
      if (line) this.chrome.step(line.key, line.text);
    } else if (msg.type === 'prompt') {
      if (msg.hop?.role === 'target' && (msg.kind === 'password' || msg.kind === 'kbdint')) this.askedPassword = true;
      if (msg.kind === 'passphrase') this.askedPassphrase = true;
    } else if (msg.type === 'ready') {
      this.ready(msg);
    } else if (msg.type === 'exit') {
      this.exitReason = msg.reason ?? 'closed';
    } else if (msg.type === 'detached') {
      this.detachedElsewhere = true;
    }
  }

  /** @param {Record<string, any>} msg */
  ready(msg) {
    this.sessionId = msg.session_id;
    this.everReady = true;
    if (msg.reattached && msg.reset) this.term.reset();
    this.received = Number(msg.offset) || 0;
    this.attempt = 0;
    if (this.askedPassphrase) {
      // A key may now be held unlocked: the top bar's "1 key unlocked" comes from the key list.
      this.askedPassphrase = false;
      store.refreshKeys().catch(() => {});
    }
    // The replay that follows a reattach is not new output: no activity dot for it.
    this.quietUntil = msg.reattached ? Date.now() + 400 : 0;
    attached(this);
    if (msg.connection && msg.connection.id === this.connection.id) this.refreshInfo(msg.connection);
    if (!msg.reattached || !this.startedAt) this.startedAt = new Date();
    this.chrome.finishSteps('done');
    this.chrome.hideOverlay();
    this.chrome.hidePanel();
    this.chrome.hideBottom();
    this.setState('connected');
    this.tick();
    if (!this.clock) this.clock = setInterval(() => this.tick(), 30000);
    const size = this.size();
    if (size && (size.cols !== this.lastSize.cols || size.rows !== this.lastSize.rows)) this.queueResize();
    if (this.container.offsetParent !== null && !document.querySelector('dialog[open]')) this.term.focus();
  }

  tick() {
    if (this.startedAt && this.state === 'connected') this.chrome.setUptime(connectedFor(Date.now() - this.startedAt.getTime()));
  }

  /** @param {Uint8Array} data */
  output(data) {
    this.term.write(data);
    this.received += data.length;
    if (Date.now() >= this.quietUntil) markActivity(this.ctx.tabId);
  }

  // --- user actions ---------------------------------------------------------------------------

  /** Open a new shell in this tab after Disconnected. @param {{forcePassword?: boolean}} [opts] */
  reconnect(opts = {}) {
    if (this.opened) this.term.write(`\r\n\x1b[2m--- Reconnecting to ${this.connection.name} ---\x1b[0m\r\n`);
    this.sessionId = null;
    this.startedAt = null;
    this.open(opts);
  }

  /** @returns {Promise<void>} kills the shell */
  async disconnect() {
    if (this.state === 'connected' && this.socket?.isOpen) {
      this.socket.send({ type: 'close' });
      return;
    }
    // Not from 'detached': the session is live in another tab or window, which keeps it.
    if (this.state === 'reattaching') {
      await killSession(this.sessionId);
      this.reset();
      showDisconnected(this, 'killed');
    }
  }

  /**
   * Close the tab: end the shell (or the attempt) and free everything. A tab whose session is
   * open in another tab or window ('detached') only goes away here; the shell stays there.
   */
  async kill() {
    const id = this.sessionId;
    const live = this.socket?.isOpen;
    if (live && this.socket) this.socket.send({ type: 'close' });
    else if (id && this.state === 'reattaching') await killSession(id);
    this.dispose();
  }

  /**
   * Show a session another page holds (a duplicated browser tab) as "open in another tab",
   * without attaching: Reattach here takes it over, Close tab leaves it alone.
   * @param {string} sessionId @param {string|null} [startedAt]
   */
  showElsewhere(sessionId, startedAt) {
    this.sessionId = sessionId;
    if (startedAt) this.startedAt = new Date(startedAt);
    this.detachedElsewhere = true;
    showDetached(this);
  }

  /** @param {number} size */
  setFontSize(size) {
    if (this.term.options.fontSize === size) return;
    this.term.options.fontSize = size;
    this.layout();
  }

  /** Stop timers and the socket, keep the terminal (for a new attempt in the same tab). */
  reset() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (this.clock) clearInterval(this.clock);
    this.clock = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.reset();
    if (this.resizeTimer) clearTimeout(this.resizeTimer);
    this.unwire();
    this.observer.disconnect();
    this.term.dispose();
    this.state = 'disconnected';
  }
}
