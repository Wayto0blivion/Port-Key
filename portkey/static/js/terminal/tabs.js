/*
 * tabs.js — owner: package 8b.
 *
 * Terminal-specific tab behaviour on top of ui/tabstrip.js: titles ("web-prod · 2"),
 * colour stripe, status dot (pulsing amber, green, grey ring), activity dot for background
 * output, focus moves left after close (tabstrip.js does that).
 *
 * The activity dot sits beside the status dot (as in the mockups) and goes when the tab is
 * shown. A disconnected tab gets muted text and no activity dot.
 */

import { updateTab, getActiveTabId } from '../ui/tabstrip.js';

/** @typedef {'connecting'|'connected'|'disconnected'} SessionState */

/** @type {Map<string, SessionState>} */
const states = new Map();

/** @param {string} name @param {number} ordinal 1-based count of sessions to the same connection @returns {string} */
export function sessionTitle(name, ordinal) {
  return ordinal > 1 ? `${name} · ${ordinal}` : name;
}

/** The lowest ordinal not in use. @param {number[]} used @returns {number} */
export function nextOrdinal(used) {
  let n = 1;
  while (used.includes(n)) n += 1;
  return n;
}

/** @param {string} tabId @param {SessionState} state */
export function setSessionState(tabId, state) {
  states.set(tabId, state);
  updateTab(tabId, { dot: state, muted: state === 'disconnected', ...(state === 'disconnected' ? { activity: false } : {}) });
}

/** New output in a tab: an activity dot unless the tab is in front or disconnected. @param {string} tabId */
export function markActivity(tabId) {
  if (getActiveTabId() === tabId || states.get(tabId) === 'disconnected') return;
  updateTab(tabId, { activity: true });
}

/** The tab was shown: its output has been seen. @param {string} tabId */
export function restoreDot(tabId) {
  updateTab(tabId, { activity: false });
}

/** @param {string} tabId */
export function forgetTab(tabId) {
  states.delete(tabId);
}

let lastStripKey = 0;
document.addEventListener('keydown', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  if (target?.closest('[role="tablist"]') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) lastStripKey = Date.now();
}, true);

/** True right after an arrow key in the tab strip: a shown terminal then leaves focus on the tab. */
export function movingThroughTabs() {
  return Date.now() - lastStripKey < 60;
}
