/*
 * protocol.js — owner: package 8b.
 *
 * The client for /ws/terminal and /ws/install (docs/ws-protocol.md):
 * - connects to ws(s)://<this page's host><path> (no query string: tokens never go in URLs) and
 *   sends {type: "auth", token} as the first message;
 * - text frames are JSON and go to handlers.onMessage; binary frames are terminal output and go
 *   to handlers.onOutput as a Uint8Array;
 * - a "prompt" goes to handlers.onPrompt(prompt, signal); its answer is sent as prompt_reply. The
 *   signal aborts when the prompt is overtaken (error, close, a newer prompt), so a dialog can
 *   close itself. Replies are never kept or logged;
 * - "error" goes to handlers.onError, then the server closes with error.close;
 * - ping every 25 s; two pings without any answer (pong or any other frame) count as a dead
 *   link: the socket is dropped at once and onClose gets 1006 with reason "dead link";
 * - input is sent as binary frames of at most 64 KB.
 *
 * closeState(code, error) maps a close code (and the error before it) to the user-facing state.
 * setTimings() lets tests shorten the ping interval.
 */

import { getToken } from '../api.js';

/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').PromptReply} PromptReply */
/** @typedef {import('../types.js').WsError} WsError */

/** Close codes (frozen, docs/ws-protocol.md). */
export const CLOSE = Object.freeze({
  NORMAL: 1000,
  TOO_BIG: 1009,
  INTERNAL: 1011,
  BAD_MESSAGE: 4400,
  AUTH: 4401,
  ORIGIN: 4403,
  UNKNOWN_CONNECTION: 4404,
  PROMPT_TIMEOUT: 4408,
  HOSTKEY_CHANGED: 4409,
  SESSION_GONE: 4410,
  TOO_MANY_SESSIONS: 4429,
  SSH_FAILURE: 4502,
});

/** Close code reported for a socket that vanished (network drop, dead link). */
export const DROPPED = 1006;
export const MAX_FRAME = 64 * 1024;

const timings = { ping: 25000, missedPongs: 2 };

/** Tests only: shorten the ping interval. @param {{ping?: number}} t */
export function setTimings(t) {
  if (t.ping) timings.ping = t.ping;
}

/**
 * @typedef {object} SocketHandlers
 * @property {(msg: Record<string, any>) => void} [onMessage]  every JSON message except pong
 * @property {(prompt: Prompt, signal: AbortSignal) => Promise<PromptReply>} [onPrompt]
 * @property {(data: Uint8Array) => void} [onOutput]
 * @property {(err: WsError) => void} [onError]
 * @property {(code: number, reason: string) => void} [onClose]  once per socket
 */

/**
 * What a close means for the user.
 * - "done": normal end (exit, done, detached elsewhere)
 * - "cancelled": the user cancelled (cancelled, hostkey_rejected)
 * - "signed_out": the token was refused (sign in again)
 * - "gone": the session no longer exists (attach after it ended)
 * - "dropped": the socket vanished; a session can be reattached
 * - "failed": anything else: show error.message
 * @param {number} code @param {WsError|null} [error]
 * @returns {'done'|'cancelled'|'signed_out'|'gone'|'dropped'|'failed'}
 */
export function closeState(code, error = null) {
  if (error) {
    if (error.code === 'cancelled' || error.code === 'hostkey_rejected') return 'cancelled';
    if (error.code === 'unauthorized' || error.code === 'auth_timeout') return 'signed_out';
    if (error.code === 'session_gone') return 'gone';
    return 'failed';
  }
  if (code === CLOSE.NORMAL) return 'done';
  if (code === CLOSE.AUTH) return 'signed_out';
  if (code === CLOSE.SESSION_GONE) return 'gone';
  if (code === DROPPED || code === 1001 || code === 1005 || code === 1012 || code === 1013) return 'dropped';
  return 'failed';
}

/** ws://host/path for this page. @param {string} path */
export function socketUrl(path) {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}${path}`;
}

const encoder = new TextEncoder();

/** @param {Uint8Array|string} data @returns {Uint8Array[]} frames of at most MAX_FRAME bytes */
export function inputFrames(data) {
  const bytes = typeof data === 'string' ? encoder.encode(data) : data;
  const out = [];
  for (let i = 0; i < bytes.length; i += MAX_FRAME) out.push(bytes.subarray(i, i + MAX_FRAME));
  return out;
}

export class PortKeySocket {
  /** @param {'/ws/terminal'|'/ws/install'} path @param {SocketHandlers} handlers */
  constructor(path, handlers) {
    this.path = path;
    this.handlers = handlers;
    /** @type {WebSocket|null} */
    this.ws = null;
    /** @type {WsError|null} the error the server sent before closing */
    this.error = null;
    this.closed = false;
    this.missed = 0;
    /** @type {ReturnType<typeof setInterval>|null} */
    this.pinger = null;
    /** @type {{id: string, abort: AbortController}|null} */
    this.pending = null;
  }

  /** True while the socket is open and authenticated. */
  get isOpen() {
    return Boolean(this.ws && this.ws.readyState === WebSocket.OPEN && !this.closed);
  }

  /**
   * Open and authenticate. Resolves once auth is sent; rejects when the socket cannot open (then
   * onClose is not called). After that, every end of the socket is reported once to onClose.
   * @returns {Promise<void>}
   */
  connect() {
    return new Promise((resolve, reject) => {
      let opened = false;
      let ws;
      try {
        ws = new WebSocket(socketUrl(this.path));
      } catch (err) {
        reject(err);
        return;
      }
      ws.binaryType = 'arraybuffer';
      this.ws = ws;
      ws.addEventListener('open', () => {
        opened = true;
        ws.send(JSON.stringify({ type: 'auth', token: getToken() ?? '' }));
        this.startPing();
        resolve();
      });
      ws.addEventListener('message', (e) => this.receive(e.data));
      ws.addEventListener('close', (e) => {
        if (!opened) {
          this.closed = true;
          reject(new Error('socket closed before it opened'));
          return;
        }
        this.finish(e.code || DROPPED, e.reason || '');
      });
    });
  }

  /** @param {Record<string, unknown>} msg */
  send(msg) {
    if (this.isOpen) /** @type {WebSocket} */ (this.ws).send(JSON.stringify(msg));
  }

  /** @param {Uint8Array|string} data terminal input */
  sendInput(data) {
    if (!this.isOpen) return;
    for (const frame of inputFrames(data)) /** @type {WebSocket} */ (this.ws).send(frame);
  }

  /** Drop the socket (on /ws/terminal this only detaches). onClose is still called once. */
  close() {
    if (this.closed || !this.ws) return;
    const ws = this.ws;
    try {
      ws.close(1000);
    } catch {
      // already closing
    }
    this.finish(1000, 'client');
  }

  startPing() {
    this.pinger = setInterval(() => {
      if (this.missed >= timings.missedPongs) {
        this.dead();
        return;
      }
      this.missed += 1;
      this.send({ type: 'ping' });
    }, timings.ping);
  }

  dead() {
    const ws = this.ws;
    try {
      ws?.close(4000, 'dead link');
    } catch {
      // nothing to close
    }
    this.finish(DROPPED, 'dead link');
  }

  /** @param {unknown} data */
  receive(data) {
    if (this.closed) return;
    this.missed = 0;
    if (data instanceof ArrayBuffer) {
      this.handlers.onOutput?.(new Uint8Array(data));
      return;
    }
    if (typeof data !== 'string') return;
    /** @type {any} */
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') return;
    if (msg.type === 'pong') return;
    if (msg.type === 'prompt') {
      this.prompt(/** @type {Prompt} */ (msg));
      return;
    }
    this.settlePrompt();
    if (msg.type === 'error') {
      // The server closes right after an error; the first one is the reason.
      this.error ??= /** @type {WsError} */ (msg);
      this.handlers.onError?.(/** @type {WsError} */ (msg));
      return;
    }
    this.handlers.onMessage?.(msg);
  }

  /** @param {Prompt} prompt */
  async prompt(prompt) {
    this.settlePrompt();
    this.handlers.onMessage?.(/** @type {any} */ (prompt));
    const abort = new AbortController();
    const entry = { id: prompt.id, abort };
    this.pending = entry;
    /** @type {PromptReply} */
    let reply;
    try {
      reply = this.handlers.onPrompt ? await this.handlers.onPrompt(prompt, abort.signal) : { cancel: true };
    } catch {
      reply = { cancel: true };
    }
    if (this.pending !== entry || abort.signal.aborted) return;
    this.pending = null;
    this.send({ type: 'prompt_reply', id: prompt.id, ...reply });
  }

  /** Abort a dialog whose prompt is no longer current. */
  settlePrompt() {
    if (!this.pending) return;
    this.pending.abort.abort();
    this.pending = null;
  }

  /** @param {number} code @param {string} reason */
  finish(code, reason) {
    if (this.closed) return;
    this.closed = true;
    if (this.pinger) clearInterval(this.pinger);
    this.pinger = null;
    this.settlePrompt();
    this.handlers.onClose?.(code, reason);
  }
}
