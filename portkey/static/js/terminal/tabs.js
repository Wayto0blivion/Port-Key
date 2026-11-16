/*
 * tabs.js — owner: package 8b.
 *
 * Terminal-specific tab behaviour on top of ui/tabstrip.js: titles ("web-prod · 2"),
 * colour stripe, status dot (pulsing amber, green, grey ring), activity dot for background
 * output, focus moves left after close.
 */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/** @param {string} name @param {number} ordinal 1-based count of sessions to the same connection @returns {string} */
export function sessionTitle(name, ordinal) { return todo(); }

/** @param {string} tabId @param {'connecting'|'connected'|'disconnected'} state */
export function setSessionState(tabId, state) { todo(); }

/** @param {string} tabId */
export function markActivity(tabId) { todo(); }
