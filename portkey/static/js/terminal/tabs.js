/*
 * tabs.js — owner: package 8b.
 *
 * Terminal-specific tab behaviour on top of ui/tabstrip.js: titles ("web-prod · 2"),
 * colour stripe, status dot (pulsing amber, green, grey ring), activity dot for background
 * output, focus moves left after close (tabstrip.js does that).
 *
 * The tab strip has one dot per tab, so the activity dot stands in for the status dot while a
 * background tab has unseen output; restoreDot() puts the status dot back when the tab is shown.
 * A disconnected tab also gets muted text, so its state stays visible either way.
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
  updateTab(tabId, { dot: state, muted: state === 'disconnected' });
}

/** New output in a tab: an activity dot unless the tab is in front or disconnected. @param {string} tabId */
export function markActivity(tabId) {
  if (getActiveTabId() === tabId || states.get(tabId) === 'disconnected') return;
  updateTab(tabId, { dot: 'activity' });
}

/** The tab was shown: the status dot replaces the activity dot. @param {string} tabId */
export function restoreDot(tabId) {
  const state = states.get(tabId);
  if (state) updateTab(tabId, { dot: state });
}

/** @param {string} tabId */
export function forgetTab(tabId) {
  states.delete(tabId);
}
