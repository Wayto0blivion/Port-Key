/*
 * session.js — owner: package 8b.
 *
 * One terminal tab: an xterm.js Terminal (DOM renderer, no clipboard/OSC 52 addon) with
 * the fit and search addons, bound to a /ws/terminal socket from ws/protocol.js. Tracks the
 * received byte count for `attach.since`, handles connecting / connected / reattaching /
 * disconnected / auth-failed states, Windows Terminal copy/paste, the multi-line paste guard,
 * Find (Ctrl+Shift+F), font size, and the 2px colour frame.
 */

/** @typedef {import('../types.js').Connection} Connection */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

export class TerminalView {
  /** @param {HTMLElement} container @param {Connection} connection */
  constructor(container, connection) {
    this.container = container;
    this.connection = connection;
  }

  /** @param {{forcePassword?: boolean}} [opts] @returns {Promise<void>} */
  async open(opts = {}) { return todo(); }

  /** @param {string} sessionId @returns {Promise<void>} */
  async attach(sessionId) { return todo(); }

  /** @returns {Promise<void>} kills the shell */
  async disconnect() { return todo(); }

  dispose() { todo(); }
}
