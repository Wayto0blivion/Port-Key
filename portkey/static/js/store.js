/*
 * store.js — owner: package 8a.
 *
 * App state shared by every view: connections, folders (groups), tags, keys, settings,
 * open sessions and UI state (selected tab, filter text, selected tag chips). Views subscribe and
 * re-render; nothing else holds server data. Folder collapsed/expanded state is per-browser and
 * kept in localStorage under "portkey.folders.collapsed" (not in the database).
 */

/** @typedef {import('./types.js').Connection} Connection */
/** @typedef {import('./types.js').Group} Group */
/** @typedef {import('./types.js').TagInfo} TagInfo */
/** @typedef {import('./types.js').KeyInfo} KeyInfo */
/** @typedef {import('./types.js').Settings} Settings */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @returns {{connections: Connection[], groups: Group[], tags: TagInfo[], keys: KeyInfo[], settings: Settings|null}} */
export function getState() { return todo(); }

/** @param {(state: ReturnType<typeof getState>) => void} listener @returns {() => void} unsubscribe */
export function subscribe(listener) { return todo(); }

/** Reload connections, groups, tags, keys and settings from the API. @returns {Promise<void>} */
export async function refresh() { return todo(); }

/** @param {string|null} groupId @returns {string} "Office LAN / Servers", or "" for ungrouped */
export function folderPath(groupId) { return todo(); }

/** @param {string} groupId @returns {boolean} */
export function isFolderCollapsed(groupId) { return todo(); }

/** @param {string} groupId @param {boolean} collapsed */
export function setFolderCollapsed(groupId, collapsed) { todo(); }
