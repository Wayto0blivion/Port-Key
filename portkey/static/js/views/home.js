/*
 * home.js — owner: package 8a.
 *
 * The Home tab: Recent cards (up to 6), the sortable All connections table (Name,
 * Address, Folder path, Tags chips, Signs in with, Last connected, Notes, row menu) with row
 * checkboxes and the bulk bar (Move to folder…, Add tag…, Remove tag…), the suggestions panel,
 * reachability dots refreshed every 60 s while visible, and the first-run states (key found,
 * no key found, only unsupported keys).
 *
 * The table and bulk bar are parts/hometable.js and parts/bulk.js.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, clear, formatAddress, formatWhenLong, keyTypeLabel, copyText, preserveFocus, labelClass } from '../ui/dom.js';
import { renderStrip, shortFp } from '../ui/fpstrip.js';
import { toast } from '../ui/toast.js';
import { getActiveTabId, onTabChange } from '../ui/tabstrip.js';
import { icon } from '../parts/icons.js';
import { reachDot, refreshDots } from '../parts/rows.js';
import { addConnection, connect } from '../parts/actions.js';
import { renderConnectionsTable } from '../parts/hometable.js';
import { filterConnections } from './connections.js';
import { renderSuggestions, unsavedSuggestions } from './suggestions.js';
import { openGenerateKey } from './keys.js';
import { openEditConnection, newConnectionDraft } from './edit-connection.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../store.js').State} State */

const REACH_EVERY = 60_000;
const REACH_CHUNK = 500;
const FILTER_PAUSE = 150;
let lastReach = 0;

/** @param {HTMLElement} container @returns {() => void} cleanup (stops reachability polling) */
export function renderHome(container) {
  container.classList.add('home');
  // A few hundred table rows take a while to build, so Home re-renders at most once per task,
  // waits for a pause while the sidebar filter is being typed, only moves the dots when just
  // reachability changed, and does nothing while hidden (it catches up when shown).
  let dirty = true;
  /** @type {unknown[]} */
  let seen = [];
  /** @type {ReturnType<typeof setTimeout>|null} */
  let pending = null;
  const render = () => {
    if (pending) clearTimeout(pending);
    pending = null;
    dirty = false;
    preserveFocus(container, () => draw(container));
    if (!lastReach && store.getState().status.connections === 'ready') pollReachability(true);
  };
  const schedule = () => {
    const s = store.getState();
    const now = [s.connections, s.groups, s.tags, s.keys, s.settings, s.suggestions, s.sessionCounts, s.status,
      s.defaultFp, s.filter, s.selectedTags];
    const changed = now.map((v, i) => v !== seen[i]);
    seen = now;
    if (!changed.some(Boolean)) {
      refreshDots(container);
      return;
    }
    dirty = true;
    if (getActiveTabId() !== 'home') return;
    const typing = changed[9] || changed[10];
    if (pending) clearTimeout(pending);
    pending = setTimeout(render, typing ? FILTER_PAUSE : 0);
  };
  render();
  const unsubscribe = store.subscribe(schedule);
  const timer = setInterval(() => pollReachability(false), REACH_EVERY);
  const offTab = onTabChange((id) => {
    if (id !== 'home') return;
    if (dirty) render();
    pollReachability(true);
  });
  const onVisible = () => { if (document.visibilityState === 'visible') pollReachability(true); };
  document.addEventListener('visibilitychange', onVisible);
  pollReachability(true);
  return () => {
    unsubscribe();
    clearInterval(timer);
    offTab();
    document.removeEventListener('visibilitychange', onVisible);
  };
}

/** TCP checks on the SSH port while Home is visible. @param {boolean} soon only if the last check is old */
async function pollReachability(soon) {
  if (getActiveTabId() !== 'home' || document.visibilityState !== 'visible') return;
  if (soon && Date.now() - lastReach < REACH_EVERY - 1000) return;
  const ids = store.getState().connections.filter((c) => !c.jump_id).map((c) => c.id);
  if (!ids.length) return;
  lastReach = Date.now();
  try {
    for (let i = 0; i < ids.length; i += REACH_CHUNK) {
      store.setReach(await api.checkReachability(ids.slice(i, i + REACH_CHUNK)));
    }
  } catch {
    // dots stay "not checked"; the next round tries again
  }
}

/** Ask for a check now (after connections load). */
export function refreshReachability() {
  lastReach = 0;
  pollReachability(true);
}

/** @param {HTMLElement} container */
function draw(container) {
  const state = store.getState();
  clear(container);
  const s = state.status.connections;
  if ((s === 'idle' || s === 'loading') && !state.connections.length) {
    container.appendChild(h('p', { class: 'home__loading muted', role: 'status' }, 'Loading connections…'));
    return;
  }
  if (s === 'error' && !state.connections.length) {
    container.appendChild(h('div', { class: 'empty' },
      h('h1', { class: 'empty__title' }, "Couldn't load your connections"),
      h('p', {}, state.errors.connections ?? ''),
      h('div', { class: 'empty__actions' },
        h('button', { type: 'button', class: 'btn btn--primary', onClick: () => store.refresh() }, 'Try again'))));
    return;
  }
  if (!state.connections.length) {
    container.appendChild(firstRun(state));
    return;
  }
  const page = h('div', { class: 'home__page' });
  const recent = recentCards(state);
  if (recent) page.appendChild(recent);
  const active = Boolean(state.filter.trim()) || state.selectedTags.length > 0;
  const matches = active ? filterConnections(state.connections, state.groups, state.filter, state.selectedTags) : null;
  const table = h('section', { class: 'home__section', 'aria-labelledby': 'all-connections' });
  renderConnectionsTable(table, { matches, rerender: () => preserveFocus(container, () => draw(container)) });
  page.appendChild(table);
  const unsaved = unsavedSuggestions(state.suggestions);
  if (unsaved.length) {
    const list = h('div', { class: 'suggest-panel' });
    page.appendChild(h('section', { class: 'home__section', 'aria-labelledby': 'home-suggest' },
      h('div', { class: 'section-head' },
        h('h2', { class: 'section-title', id: 'home-suggest' }, 'Suggested from known_hosts'),
        h('span', { class: 'muted' }, `Hosts this PC has connected to before that are not saved yet. ${Math.min(3, unsaved.length)} of ${unsaved.length} shown.`)),
      list));
    renderSuggestions(list, { limit: 3 });
  }
  container.appendChild(page);
}

/** @param {State} state @returns {HTMLElement|null} */
function recentCards(state) {
  const recent = state.connections.filter((c) => c.last_used_at)
    .sort((a, b) => Date.parse(b.last_used_at ?? '') - Date.parse(a.last_used_at ?? ''))
    .slice(0, 6);
  if (!recent.length) return null;
  const grid = h('div', { class: 'cards' });
  for (const c of recent) {
    const open = state.sessionCounts[c.id] ?? 0;
    const last = open ? `Connected now${open > 1 ? `, ${open} sessions` : ''}` : formatWhenLong(c.last_used_at);
    grid.appendChild(h('button', {
      type: 'button', class: `card ${labelClass(c.color)}`, dataset: { fkey: `card:${c.id}` },
      onClick: (/** @type {MouseEvent} */ e) => connect(c.id, { newSession: e.ctrlKey || e.metaKey }),
    },
      h('span', { class: 'card__top' }, h('span', { class: 'card__name' }, c.name), reachDot(c)),
      h('span', { class: 'card__addr mono' }, formatAddress(c)),
      h('span', { class: 'card__foot' }, h('span', { class: 'card__last' }, last), h('span', { class: 'card__go', 'aria-hidden': 'true' }, 'Connect'))));
  }
  return h('section', { class: 'home__section', 'aria-labelledby': 'home-recent' },
    h('h2', { class: 'section-title', id: 'home-recent' }, 'Recent'), grid);
}

/** @param {string} path @returns {string} the folder of a Windows or POSIX path */
function dirname(path) {
  const i = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
  return i > 0 ? path.slice(0, i) : path;
}

/** @param {State} state */
function firstRun(state) {
  const sshDir = state.settings?.about.ssh_dir ?? '';
  const wrap = h('div', { class: 'empty empty--firstrun' }, icon('key', { size: 56, strokeWidth: 1.4, className: 'empty__icon' }));
  if (state.status.keys === 'loading' || state.status.keys === 'idle') {
    wrap.appendChild(h('p', { class: 'muted', role: 'status' }, 'Looking for your SSH keys…'));
    return wrap;
  }
  const usable = state.keys.filter((k) => !k.hidden);
  const key = store.defaultKey() && !store.defaultKey()?.hidden ? store.defaultKey() : usable[0];
  if (key) {
    wrap.append(
      h('h1', { class: 'empty__title' }, 'No connections yet'),
      h('p', { class: 'empty__lead' }, 'Port-Key found your key ', h('span', { class: 'mono brass' }, key.name), ' in ',
        h('span', { class: 'mono' }, dirname(key.path) || sshDir),
        ". Add a host and Port-Key installs this key on it, so you stop typing that host's password."),
      h('div', { class: 'fp-box' }, renderStrip(key.fp), h('span', { class: 'mono' }, `${keyTypeLabel(key).split(' ')[0]} ${shortFp(key.fp)}`)),
      h('div', { class: 'empty__actions' },
        h('button', { type: 'button', class: 'btn btn--primary btn--lg', onClick: () => addConnection() }, 'Add connection'),
        h('button', {
          type: 'button', class: 'btn btn--secondary btn--lg',
          onClick: async () => {
            const ok = await copyText(key.public_key);
            toast(ok ? 'Public key copied' : "Couldn't copy to the clipboard.", { kind: ok ? 'info' : 'error' });
          },
        }, 'Copy public key')));
    if (unsavedSuggestions(state.suggestions).length) {
      const list = h('div', { class: 'suggest-panel' });
      wrap.appendChild(h('section', { class: 'empty__suggest', 'aria-labelledby': 'fr-suggest' },
        h('h2', { class: 'empty__subtitle', id: 'fr-suggest' }, 'Or start from a host this PC has connected to before'), list));
      renderSuggestions(list, { limit: 3 });
    }
    return wrap;
  }
  const onlyUnsupported = state.keyProblems.length > 0 && state.keyProblems.every((p) => p.reason === 'ppk' || p.reason === 'unsupported_type');
  wrap.append(
    h('h1', { class: 'empty__title' }, 'No SSH key found'),
    onlyUnsupported
      ? h('p', { class: 'empty__lead' }, 'Port-Key found a PuTTY key (', h('span', { class: 'mono' }, '.ppk'),
        "), which it can't use. Generate a key here and install it with the wizard.")
      : h('p', { class: 'empty__lead' }, 'Port-Key looked in ', h('span', { class: 'mono' }, sshDir),
        ' and found no key pair. Generate one here to log in to your servers without passwords.'),
    h('div', { class: 'empty__actions' },
      h('button', {
        type: 'button', class: 'btn btn--primary btn--lg',
        onClick: async () => { if (await openGenerateKey()) await store.refreshKeys().catch(() => {}); },
      }, 'Generate key'),
      h('button', {
        type: 'button', class: 'btn btn--secondary btn--lg',
        onClick: () => openEditConnection(newConnectionDraft({ auth_method: 'password' })),
      }, 'Add connection with a password')),
    h('p', { class: 'muted' }, 'Have a key somewhere else? Copy it into ', h('span', { class: 'mono' }, sshDir), ', then select Check again. ',
      h('button', {
        type: 'button', class: 'link-btn',
        onClick: async () => {
          await store.refreshKeys().catch(() => {});
          if (!store.getState().keys.some((k) => !k.hidden)) toast(`Still no key pair in ${sshDir}.`);
        },
      }, 'Check again')));
  return wrap;
}
