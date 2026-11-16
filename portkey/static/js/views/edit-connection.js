/*
 * edit-connection.js — owner: package 8a.
 *
 * The Edit connection dialog: Host, Port, User, Name, Folder picker (tree select with
 * "New folder…"), Tags token field with autocomplete, Colour label, Sign in with, Jump via
 * (excluding this connection and loops), Notes, Pinned; Save / Cancel. Also used for Duplicate.
 */

/** @typedef {import('../types.js').Connection} Connection */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @param {Connection} connection @returns {Promise<Connection|null>} the saved connection, or null if cancelled */
export async function openEditConnection(connection) { return todo(); }
