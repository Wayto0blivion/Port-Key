/*
 * palette.js — owner: package 8b.
 *
 * Quick-connect palette: fuzzy search over name, host, user, folder path, tags (#prod
 * narrows by tag) and notes, ranked by recent use; open-session hints; "Add <text> as a new
 * connection…"; commands (Generate key, Keys, Settings, Lock keys); Enter / Shift+Enter / Esc.
 */

/** @typedef {import('../types.js').Connection} Connection */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/** @returns {void} */
export function openPalette() { todo(); }

/** @param {Connection[]} connections @param {string} query @returns {Connection[]} ranked (pure; unit-testable) */
export function rankConnections(connections, query) { return todo(); }

/** @param {string} text @returns {{username: string|null, host: string, port: number|null}} parses user@host:port */
export function parseTarget(text) { return todo(); }
