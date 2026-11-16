/*
 * tabstrip.js — owner: package 8a.
 *
 * The main pane's tab strip: Home pinned at the far left (house icon), closable tabs for
 * Keys, Settings, Diagnostics and terminal sessions, middle-click close, drag reorder,
 * horizontal scroll when narrow. Panels stay mounted while hidden, so sessions never unmount.
 */

const todo = () => {
  throw new Error('not implemented (package 8a)');
};

/**
 * @typedef {object} TabSpec
 * @property {string} id
 * @property {string} title
 * @property {boolean} [closable]
 * @property {string|null} [color] colour-label token name for the top edge
 * @property {(panel: HTMLElement) => void} render called once
 * @property {() => boolean|void} [onClose] return false to keep the tab
 */

/** @param {TabSpec} spec @returns {HTMLElement} the panel */
export function addTab(spec) { return todo(); }

/** @param {string} id */
export function activateTab(id) { todo(); }

/** @param {string} id */
export function closeTab(id) { todo(); }

/** @param {string} id @param {{title?: string, color?: string|null, muted?: boolean, dot?: string|null}} patch */
export function updateTab(id, patch) { todo(); }
