/*
 * folderpicker.js — owner: package 8a.
 *
 * Folder picker: a tree select of folders with "Ungrouped" and "New folder…" (creates
 * via the API), used by Edit connection, the bulk bar and the wizard (8b reuses it).
 */

/** @typedef {import('../types.js').Group} Group */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/** @param {{groups: Group[], value: string|null, onChange: (groupId: string|null) => void, allowNew?: boolean}} opts @returns {HTMLElement} */
export function createFolderPicker(opts) { return todo(); }
