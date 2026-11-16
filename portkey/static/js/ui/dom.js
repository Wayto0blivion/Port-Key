/*
 * dom.js — owner: package 8a.
 *
 * Small DOM helpers used by every view: element builder, clearing, query, and the
 * shared formatters for addresses ("john@192.168.10.42", port only when not 22), ssh commands
 * ("ssh -p 2222 admin@192.168.10.31") and times ("Today 14:02").
 */

/** @typedef {import('../types.js').Connection} Connection */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/**
 * @param {string} tag @param {Record<string, unknown>} [attrs] @param {...(Node|string|null|undefined)} children
 * @returns {HTMLElement}
 */
export function h(tag, attrs = {}, ...children) { return todo(); }

/** @param {Element} el */
export function clear(el) { todo(); }

/** @param {Connection} connection @returns {string} */
export function formatAddress(connection) { return todo(); }

/** @param {Connection} connection @returns {string} */
export function sshCommand(connection) { return todo(); }

/** @param {string|null} iso @returns {string} */
export function formatWhen(iso) { return todo(); }
