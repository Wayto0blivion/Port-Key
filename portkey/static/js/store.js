/*
 * store.js — owner: package 8a.
 *
 * App state shared by every view: connections, folders (groups), tags, keys, settings,
 * open sessions and UI state (selected tab, filter text, selected tag chips). Views subscribe and
 * re-render; nothing else holds server data. Folder collapsed/expanded state is per-browser and
 * kept in localStorage under "portkey.folders.collapsed" (not in the database).
 *
 * getState() returns the live state object; treat it as read-only and change it only through
 * the functions here. Listeners run once per microtask however many changes happened.
 *
 * For package 8b: call setSessionInfo({counts, connected}) whenever sessions open, connect or
 * close. `counts` maps connection id -> open sessions (sidebar badge, Recent cards) and
 * `connected` is the number of connected sessions (leave warning).
 */

import * as api from './api.js';
import { buildFolderIndex } from './parts/folders.js';

/** @typedef {import('./types.js').Connection} Connection */
/** @typedef {import('./types.js').Group} Group */
/** @typedef {import('./types.js').TagInfo} TagInfo */
/** @typedef {import('./types.js').KeyInfo} KeyInfo */
/** @typedef {import('./types.js').KeyProblem} KeyProblem */
/** @typedef {import('./types.js').Settings} Settings */
/** @typedef {import('./types.js').SuggestionList} SuggestionList */
/** @typedef {import('./types.js').Reachability} Reachability */
/** @typedef {'idle'|'loading'|'ready'|'error'} LoadStatus */
/** @typedef {'connections'|'keys'|'settings'|'suggestions'} Area */

/**
 * @typedef {object} State
 * @property {Connection[]} connections
 * @property {Group[]} groups
 * @property {TagInfo[]} tags
 * @property {KeyInfo[]} keys          hidden keys included (hidden: true)
 * @property {KeyProblem[]} keyProblems
 * @property {string|null} defaultFp
 * @property {number} unlockedCount
 * @property {Settings|null} settings
 * @property {SuggestionList|null} suggestions
 * @property {Record<Area, LoadStatus>} status
 * @property {Record<Area, string|null>} errors  user-facing message of the last failed load
 * @property {Record<string, Reachability>} reach  by connection id
 * @property {Record<string, number>} sessionCounts  open sessions by connection id (from 8b)
 * @property {number} connectedSessions
 * @property {string} filter          sidebar filter text
 * @property {string[]} selectedTags  selected tag chips
 * @property {number} collapsedVersion bumps when a folder opens or closes
 */

/** @type {State} */
const state = {
  connections: [],
  groups: [],
  tags: [],
  keys: [],
  keyProblems: [],
  defaultFp: null,
  unlockedCount: 0,
  settings: null,
  suggestions: null,
  status: { connections: 'idle', keys: 'idle', settings: 'idle', suggestions: 'idle' },
  errors: { connections: null, keys: null, settings: null, suggestions: null },
  reach: {},
  sessionCounts: {},
  connectedSessions: 0,
  filter: '',
  selectedTags: [],
  collapsedVersion: 0,
};

/** @type {Set<(state: State) => void>} */
const listeners = new Set();
let scheduled = false;

/** @returns {State} */
export function getState() { return state; }

/** @param {(state: State) => void} listener @returns {() => void} unsubscribe */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    for (const fn of [...listeners]) {
      try {
        fn(state);
      } catch (err) {
        console.error('store listener failed', err);
      }
    }
  });
}

/** Merge a patch into the state and notify. @param {Partial<State>} patch */
export function setState(patch) {
  Object.assign(state, patch);
  notify();
}

/** @param {Area} area @param {LoadStatus} status @param {string|null} [error] */
function mark(area, status, error = null) {
  state.status = { ...state.status, [area]: status };
  state.errors = { ...state.errors, [area]: error };
  notify();
}

/** @param {unknown} err */
function messageOf(err) {
  if (err instanceof api.ApiError && err.code === 'unauthorized') return 'Sign in to Port-Key.';
  return err instanceof Error ? err.message : String(err);
}

/** Connections, folders and tags (their counts move together). @returns {Promise<void>} */
export async function refreshConnections() {
  if (state.status.connections !== 'ready') mark('connections', 'loading');
  try {
    const [connections, groups, tags] = await Promise.all([api.listConnections(), api.listGroups(), api.listTags()]);
    Object.assign(state, { connections, groups, tags });
    mark('connections', 'ready');
  } catch (err) {
    mark('connections', 'error', messageOf(err));
    throw err;
  }
}

/** @returns {Promise<void>} */
export async function refreshKeys() {
  if (state.status.keys !== 'ready') mark('keys', 'loading');
  try {
    const list = await api.listKeys();
    Object.assign(state, {
      keys: list.keys, keyProblems: list.problems, defaultFp: list.default_fp, unlockedCount: list.unlocked_count,
    });
    mark('keys', 'ready');
  } catch (err) {
    mark('keys', 'error', messageOf(err));
    throw err;
  }
}

/** @returns {Promise<void>} */
export async function refreshSettings() {
  if (state.status.settings !== 'ready') mark('settings', 'loading');
  try {
    state.settings = await api.getSettings();
    mark('settings', 'ready');
  } catch (err) {
    mark('settings', 'error', messageOf(err));
    throw err;
  }
}

/** @returns {Promise<void>} */
export async function refreshSuggestions() {
  if (state.status.suggestions !== 'ready') mark('suggestions', 'loading');
  try {
    state.suggestions = await api.listSuggestions();
    mark('suggestions', 'ready');
  } catch (err) {
    mark('suggestions', 'error', messageOf(err));
    throw err;
  }
}

/** Reload connections, groups, tags, keys and settings from the API. @returns {Promise<void>} */
export async function refresh() {
  await Promise.allSettled([refreshConnections(), refreshKeys(), refreshSettings(), refreshSuggestions()]);
}

// --- folders ------------------------------------------------------------------------------------

/** @type {{groups: Group[]|null, index: ReturnType<typeof buildFolderIndex>|null}} */
const memo = { groups: null, index: null };

/** The folder index for the current groups (memoised). */
export function folders() {
  if (memo.groups !== state.groups || !memo.index) {
    memo.groups = state.groups;
    memo.index = buildFolderIndex(state.groups);
  }
  return memo.index;
}

/** @param {string|null} groupId @returns {string} "Office LAN / Servers", or "" for ungrouped */
export function folderPath(groupId) { return folders().path(groupId); }

const COLLAPSED_KEY = 'portkey.folders.collapsed';
/** Sections collapsed until the owner opens them. */
const DEFAULT_COLLAPSED = new Set(['__suggestions']);
/** @type {Record<string, boolean>|null} */
let collapsed = null;

/** @returns {Record<string, boolean>} */
function loadCollapsed() {
  if (collapsed) return collapsed;
  collapsed = {};
  try {
    const raw = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? '{}');
    if (Array.isArray(raw)) for (const id of raw) collapsed[String(id)] = true;
    else if (raw && typeof raw === 'object') {
      for (const [k, v] of Object.entries(raw)) collapsed[k] = Boolean(v);
    }
  } catch {
    collapsed = {};
  }
  return collapsed;
}

/** @param {string} groupId @returns {boolean} */
export function isFolderCollapsed(groupId) {
  const map = loadCollapsed();
  return groupId in map ? map[groupId] : DEFAULT_COLLAPSED.has(groupId);
}

/** @param {string} groupId @param {boolean} value */
export function setFolderCollapsed(groupId, value) {
  const map = loadCollapsed();
  map[groupId] = value;
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify(map));
  } catch {
    // per-browser state only; nothing to do
  }
  state.collapsedVersion += 1;
  notify();
}

// --- UI state -----------------------------------------------------------------------------------

/** @param {string} text */
export function setFilter(text) {
  if (text === state.filter) return;
  setState({ filter: text });
}

/** @param {string} tag */
export function toggleTag(tag) {
  const has = state.selectedTags.includes(tag);
  setState({ selectedTags: has ? state.selectedTags.filter((t) => t !== tag) : [...state.selectedTags, tag] });
}

export function clearFilter() {
  setState({ filter: '', selectedTags: [] });
}

/** @param {Reachability[]} results */
export function setReach(results) {
  const reach = { ...state.reach };
  for (const r of results) reach[r.id] = r;
  setState({ reach });
}

/** For package 8b. @param {{counts: Record<string, number>, connected: number}} info */
export function setSessionInfo(info) {
  setState({ sessionCounts: { ...info.counts }, connectedSessions: info.connected });
}

// --- lookups ------------------------------------------------------------------------------------

/** @param {string|null|undefined} id @returns {Connection|undefined} */
export function connectionById(id) {
  return id ? state.connections.find((c) => c.id === id) : undefined;
}

/** @param {string|null|undefined} fp @returns {KeyInfo|undefined} */
export function keyByFp(fp) {
  return fp ? state.keys.find((k) => k.fp === fp) : undefined;
}

/** The key used when a connection has key_fp null. @returns {KeyInfo|undefined} */
export function defaultKey() {
  return keyByFp(state.defaultFp) ?? state.keys.find((k) => k.is_default);
}

/** "id_ed25519", "Password" or "Default key" for the Signs in with column. @param {Connection} c */
export function signsInWith(c) {
  if (c.auth_method === 'password') return 'Password';
  const key = c.key_fp ? keyByFp(c.key_fp) : defaultKey();
  if (key) return key.name;
  return c.key_fp ? 'Unknown key' : 'Default key';
}
