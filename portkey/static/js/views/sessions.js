/*
 * sessions.js — owner: package 8b.
 *
 * Session orchestration: open a session for a connection (focus an existing one unless
 * a new one is asked for), restore tabs after a reload by reattaching (`since: null`), Close all
 * sessions with a count confirmation, and the "(deleted)" suffix for deleted connections.
 */

const todo = () => {
  throw new Error('not implemented (package 8b)');
};

/** @param {string} connectionId @param {{newSession?: boolean, forcePassword?: boolean}} [opts] @returns {Promise<void>} */
export async function openSession(connectionId, opts = {}) { return todo(); }

/** @returns {Promise<void>} */
export async function restoreSessions() { return todo(); }

/** @returns {Promise<void>} */
export async function closeAllSessions() { return todo(); }
