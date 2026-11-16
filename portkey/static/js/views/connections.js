/*
 * connections.js — owner: package 8a.
 *
 * The sidebar: filter field (understands #tag tokens and matches folder paths), the tag
 * chip row (most used first, then More…, AND filter), the folder tree (Pinned virtual section
 * first, Ungrouped last, 12px indent per level, counts include descendants, collapsible, drag a
 * connection onto a folder to move it, folder menu New folder / Rename / Move / Delete), 44px
 * connection rows without tag chips, row menu, suggestions section and Add connection.
 * Folders with no matches are hidden while a filter is active.
 *
 * The tree itself is parts/tree.js. The filter field is built once and never re-rendered, so
 * typing keeps its caret; everything else re-renders from the store. Esc in the filter clears
 * it, Down moves into the tree. The "/" shortcut lives in shell.js (focusFilter()).
 */

import * as store from '../store.js';
import { h, clear, preserveFocus } from '../ui/dom.js';
import { buildFolderIndex } from '../parts/folders.js';
import { renderTree, wireTreeKeys } from '../parts/tree.js';
import { icon } from '../parts/icons.js';
import { refreshDots } from '../parts/rows.js';
import { addConnection } from '../parts/actions.js';
import { renderSuggestions, unsavedSuggestions } from './suggestions.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../types.js').Group} Group */

const CHIP_LIMIT = 6;
let showAllTags = false;

/** @param {HTMLElement} container @returns {void} */
export function renderSidebar(container) {
  clear(container);
  const filter = /** @type {HTMLInputElement} */ (h('input', {
    id: 'sidebar-filter', class: 'input sidebar__filter-input', type: 'search', placeholder: 'Filter connections',
    autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'sidebar-filter-hint',
  }));
  const hint = h('span', { id: 'sidebar-filter-hint', class: 'visually-hidden' },
    'Matches name, address, notes and folder. Type #tag to filter by tag.');
  const chips = h('div', { class: 'tagchips', role: 'group', 'aria-label': 'Filter by tag' });
  const tree = h('ul', { class: 'tree', role: 'tree', 'aria-label': 'Connections' });
  const status = h('div', { class: 'sidebar__status' });
  const suggest = h('section', { class: 'sidebar__suggest', 'aria-label': 'Suggested from known_hosts' });
  const add = h('button', { type: 'button', class: 'btn btn--primary btn--block', onClick: () => addConnection() }, 'Add connection');

  container.append(
    h('div', { class: 'sidebar__filter' }, h('label', { class: 'visually-hidden', for: 'sidebar-filter' }, 'Filter connections'), filter, hint),
    chips,
    h('div', { class: 'sidebar__scroll' }, tree, status, suggest),
    h('div', { class: 'sidebar__foot' }, add));

  filter.value = store.getState().filter;
  filter.addEventListener('input', () => store.setFilter(filter.value));
  filter.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (filter.value || store.getState().selectedTags.length)) {
      e.preventDefault();
      filter.value = '';
      store.clearFilter();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      /** @type {HTMLElement|null} */ (tree.querySelector('[role="treeitem"][tabindex="0"]'))?.focus();
    }
  });
  wireTreeKeys(tree);

  /** @type {unknown[]} */
  let seen = [];
  const render = () => {
    const state = store.getState();
    const now = [state.connections, state.groups, state.tags, state.keys, state.suggestions, state.sessionCounts,
      state.status, state.filter, state.selectedTags, state.collapsedVersion, state.defaultFp];
    const same = now.every((v, i) => v === seen[i]);
    seen = now;
    if (same) {
      refreshDots(tree); // only reachability changed
      return;
    }
    if (filter.value !== state.filter && document.activeElement !== filter) filter.value = state.filter;
    const loaded = state.status.connections === 'ready';
    filter.disabled = loaded && state.connections.length === 0;
    preserveFocus(chips, () => renderChips(chips, state));
    const active = Boolean(state.filter.trim()) || state.selectedTags.length > 0;
    const matches = active ? filterConnections(state.connections, state.groups, state.filter, state.selectedTags) : null;
    renderTree(tree, { matches });
    renderStatus(status, state, matches, filter);
    renderSuggestSection(suggest, state);
  };
  render();
  store.subscribe(render);
}

/** @param {HTMLElement} el @param {import('../store.js').State} state */
function renderChips(el, state) {
  clear(el);
  const tags = state.tags;
  el.hidden = tags.length === 0;
  const shown = showAllTags ? tags : tags.filter((t, i) => i < CHIP_LIMIT || state.selectedTags.includes(t.name));
  for (const t of shown) {
    const on = state.selectedTags.includes(t.name);
    el.appendChild(h('button', {
      type: 'button', class: `chip chip--filter${on ? ' is-on' : ''}`, 'aria-pressed': String(on),
      title: `${t.count} connection${t.count === 1 ? '' : 's'}`, dataset: { fkey: `tag:${t.name}` },
      onClick: () => store.toggleTag(t.name),
    }, on ? icon('check', { size: 12, strokeWidth: 3 }) : null, `#${t.name}`));
  }
  if (tags.length > CHIP_LIMIT) {
    el.appendChild(h('button', {
      type: 'button', class: 'chip chip--more', 'aria-expanded': String(showAllTags), dataset: { fkey: 'tag:__more' },
      onClick: () => {
        showAllTags = !showAllTags;
        renderChips(el, store.getState());
        /** @type {HTMLElement|null} */ (el.querySelector('[data-fkey="tag:__more"]'))?.focus();
      },
    }, showAllTags ? 'Fewer' : 'More…'));
  }
}

/**
 * @param {HTMLElement} el @param {import('../store.js').State} state
 * @param {Set<string>|null} matches @param {HTMLInputElement} filter
 */
function renderStatus(el, state, matches, filter) {
  clear(el);
  const s = state.status.connections;
  if (s === 'loading' || s === 'idle') {
    el.appendChild(h('p', { class: 'sidebar__note', role: 'status' }, 'Loading connections…'));
  } else if (s === 'error' && !state.connections.length) {
    el.append(
      h('p', { class: 'sidebar__note sidebar__note--error' }, `Couldn't load connections. ${state.errors.connections ?? ''}`),
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', onClick: () => store.refreshConnections().catch(() => {}) }, 'Try again'));
  } else if (!state.connections.length) {
    el.appendChild(h('p', { class: 'sidebar__note' }, 'Saved connections appear here, grouped the way you choose.'));
  } else if (matches && matches.size === 0) {
    el.append(
      h('p', { class: 'sidebar__note', role: 'status' }, 'No connections match this filter.'),
      h('button', {
        type: 'button', class: 'btn btn--secondary btn--sm',
        onClick: () => {
          filter.value = '';
          store.clearFilter();
          filter.focus();
        },
      }, 'Clear filter'));
  } else if (matches) {
    el.appendChild(h('p', { class: 'visually-hidden', role: 'status' }, `${matches.size} matching connections`));
  }
}

/** @param {HTMLElement} el @param {import('../store.js').State} state */
function renderSuggestSection(el, state) {
  const list = unsavedSuggestions(state.suggestions);
  if (!list.length) {
    clear(el);
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const open = !store.isFolderCollapsed('__suggestions');
  let head = /** @type {HTMLButtonElement|null} */ (el.querySelector('.sidebar__suggest-head'));
  let body = /** @type {HTMLElement|null} */ (el.querySelector('.sidebar__suggest-body'));
  if (!head || !body) {
    clear(el);
    head = /** @type {HTMLButtonElement} */ (h('button', {
      type: 'button', class: 'sidebar__suggest-head', 'aria-controls': 'sidebar-suggest-body',
      onClick: () => store.setFolderCollapsed('__suggestions', !store.isFolderCollapsed('__suggestions')),
    }));
    body = h('div', { id: 'sidebar-suggest-body', class: 'sidebar__suggest-body' });
    el.append(head, body);
  }
  head.setAttribute('aria-expanded', String(open));
  clear(head);
  head.append(icon('chevron', { size: 12, className: 'tree-chevron' }),
    h('span', { class: 'sidebar__suggest-label' }, 'Suggested from known_hosts'), h('span', {}, String(list.length)));
  body.hidden = !open;
  if (open && body.dataset.rendered !== String(state.suggestions?.suggestions.length)) {
    body.dataset.rendered = String(state.suggestions?.suggestions.length);
    renderSuggestions(body, { compact: true }).catch(() => {});
  }
}

/** @param {string} text @returns {{terms: string[], tags: string[]}} lower-case terms and #tags */
export function parseFilter(text) {
  /** @type {string[]} */
  const terms = [];
  /** @type {string[]} */
  const tags = [];
  for (const token of String(text).toLowerCase().split(/\s+/)) {
    if (!token) continue;
    if (token.startsWith('#')) {
      const tag = token.replace(/^#+/, '');
      if (tag && !tags.includes(tag)) tags.push(tag);
    } else if (!terms.includes(token)) terms.push(token);
  }
  return { terms, tags };
}

/**
 * Connections matching the filter text and every selected tag (pure; unit-testable).
 * @param {Connection[]} connections @param {Group[]} groups @param {string} text @param {string[]} tags
 * @returns {Set<string>} matching connection ids
 */
export function filterConnections(connections, groups, text, tags) {
  const parsed = parseFilter(text);
  const wanted = [...new Set([...parsed.tags, ...tags.map((t) => t.toLowerCase())])];
  const index = buildFolderIndex(groups);
  /** @type {Set<string>} */
  const out = new Set();
  for (const c of connections) {
    if (!wanted.every((t) => c.tags.includes(t))) continue;
    if (parsed.terms.length) {
      const port = c.port === 22 ? '' : `:${c.port}`;
      const hay = [c.name, c.host, c.username, `${c.username}@${c.host}${port}`, c.notes, index.path(c.group_id)]
        .join('\n').toLowerCase();
      if (!parsed.terms.every((t) => hay.includes(t))) continue;
    }
    out.add(c.id);
  }
  return out;
}
