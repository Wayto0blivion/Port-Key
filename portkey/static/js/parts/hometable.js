/*
 * parts/hometable.js — owner: package 8a (private module of views/home.js).
 *
 * Home's "All connections" section: the heading with the bulk bar, and the sortable table
 * (checkbox, Name, Address, Folder path, Tags chips, Signs in with, Last connected, Notes, row
 * menu). The sidebar filter also narrows this table, so "#dell" then Select all then Add tag…
 * works on exactly what is shown. Selection survives re-renders; Shift+click on a checkbox
 * selects a range. Column headers are buttons that sort (aria-sort says which way).
 */

import * as store from '../store.js';
import { h, clear, formatAddress, formatWhen } from '../ui/dom.js';
import { icon } from './icons.js';
import { openMenu } from './menu.js';
import { labelChip, reachDot } from './rows.js';
import { connect, connectionMenu } from './actions.js';
import { bulkMove, bulkAddTags, bulkRemoveTags } from './bulk.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {'name'|'address'|'folder'|'auth'|'last'} SortKey */

/** @type {Set<string>} */
const selected = new Set();
/** @type {{key: SortKey, dir: 1|-1}} */
let sort = { key: 'name', dir: 1 };
let lastClicked = -1;
/**
 * What the last render built, so selection changes update in place instead of rebuilding
 * a few hundred rows.
 * @type {{head: HTMLElement|null, allBox: HTMLInputElement|null, rows: Connection[], total: number,
 *   matches: Set<string>|null, boxes: Map<string, {tr: HTMLElement, box: HTMLInputElement}>}}
 */
const view = { head: null, allBox: null, rows: [], total: 0, matches: null, boxes: new Map() };

function syncSelection() {
  for (const [id, { tr, box }] of view.boxes) {
    const on = selected.has(id);
    box.checked = on;
    tr.classList.toggle('is-selected', on);
  }
  const ids = view.rows.map((c) => c.id);
  if (view.allBox) {
    view.allBox.checked = ids.length > 0 && ids.every((id) => selected.has(id));
    view.allBox.indeterminate = !view.allBox.checked && ids.some((id) => selected.has(id));
  }
  if (view.head) {
    const fresh = header(view.total, view.rows, view.matches);
    view.head.replaceWith(fresh);
    view.head = fresh;
  }
}

/** @param {Connection} c @param {SortKey} key @returns {string|number} */
function sortValue(c, key) {
  if (key === 'address') return formatAddress(c).toLowerCase();
  if (key === 'folder') return store.folderPath(c.group_id).toLowerCase() || '￿';
  if (key === 'auth') return store.signsInWith(c).toLowerCase();
  if (key === 'last') return c.last_used_at ? Date.parse(c.last_used_at) : 0;
  return c.name.toLowerCase();
}

/**
 * @param {HTMLElement} section
 * @param {{matches: Set<string>|null, rerender: () => void}} opts
 */
export function renderConnectionsTable(section, opts) {
  const state = store.getState();
  const all = state.connections;
  for (const id of [...selected]) if (!all.some((c) => c.id === id)) selected.delete(id);
  const rows = all.filter((c) => !opts.matches || opts.matches.has(c.id));
  rows.sort((a, b) => {
    const x = sortValue(a, sort.key);
    const y = sortValue(b, sort.key);
    return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * sort.dir;
  });
  clear(section);
  Object.assign(view, { head: header(all.length, rows, opts.matches), allBox: null, rows, total: all.length, matches: opts.matches, boxes: new Map() });
  section.appendChild(/** @type {HTMLElement} */ (view.head));
  if (!rows.length) {
    section.appendChild(h('p', { class: 'muted' }, 'No connections match the sidebar filter.'));
    return;
  }
  const visibleIds = rows.map((c) => c.id);
  const allBox = /** @type {HTMLInputElement} */ (h('input', {
    type: 'checkbox', 'aria-label': 'Select all connections shown', dataset: { fkey: 'sel:all' },
    checked: visibleIds.every((id) => selected.has(id)),
    onChange: (/** @type {Event} */ e) => {
      const on = /** @type {HTMLInputElement} */ (e.target).checked;
      for (const id of visibleIds) on ? selected.add(id) : selected.delete(id);
      syncSelection();
    },
  }));
  view.allBox = allBox;
  allBox.indeterminate = !allBox.checked && visibleIds.some((id) => selected.has(id));
  const th = (/** @type {string} */ label, /** @type {SortKey|null} */ key) => {
    if (!key) return h('th', { scope: 'col' }, label);
    const on = sort.key === key;
    return h('th', { scope: 'col', 'aria-sort': on ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none' },
      h('button', {
        type: 'button', class: 'th-sort', dataset: { fkey: `sort:${key}` },
        onClick: () => {
          sort = on ? { key, dir: sort.dir === 1 ? -1 : 1 } : { key, dir: key === 'last' ? -1 : 1 };
          opts.rerender();
        },
      }, label, h('span', { class: `th-sort__arrow${on ? ' is-on' : ''}`, 'aria-hidden': 'true' }, on && sort.dir === -1 ? '↓' : '↑')));
  };
  const body = h('tbody');
  rows.forEach((c, i) => body.appendChild(row(c, i, rows)));
  section.appendChild(h('div', { class: 'table-wrap' },
    h('table', { class: 'table table--connections' },
      h('thead', {}, h('tr', {},
        h('th', { scope: 'col', class: 'col-check' }, allBox),
        th('Name', 'name'), th('Address', 'address'), th('Folder', 'folder'), th('Tags', null),
        th('Signs in with', 'auth'), th('Last connected', 'last'), th('Notes', null),
        h('th', { scope: 'col', class: 'col-menu' }, h('span', { class: 'visually-hidden' }, 'Menu')))),
      body)));
}

/** @param {number} total @param {Connection[]} rows @param {Set<string>|null} matches */
function header(total, rows, matches) {
  const head = h('div', { class: 'section-head' },
    h('h2', { class: 'section-title', id: 'all-connections' }, 'All connections'),
    h('span', { class: 'muted' }, matches ? `${rows.length} of ${total} shown, filtered in the sidebar` : String(total)));
  if (matches) {
    head.appendChild(h('button', { type: 'button', class: 'link-btn', onClick: () => store.clearFilter() }, 'Clear filter'));
  }
  if (selected.size) {
    const picked = () => store.getState().connections.filter((c) => selected.has(c.id));
    head.appendChild(h('div', { class: 'bulkbar', role: 'toolbar', 'aria-label': 'Selected connections' },
      h('span', { class: 'bulkbar__count', role: 'status' }, `${selected.size} selected`),
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', dataset: { fkey: 'bulk:move' }, onClick: () => bulkMove(picked()) }, 'Move to folder…'),
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', dataset: { fkey: 'bulk:add' }, onClick: () => bulkAddTags(picked()) }, 'Add tag…'),
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', dataset: { fkey: 'bulk:remove' }, onClick: () => bulkRemoveTags(picked()) }, 'Remove tag…'),
      h('button', {
        type: 'button', class: 'icon-btn', 'aria-label': 'Clear selection', title: 'Clear selection',
        onClick: () => {
          selected.clear();
          syncSelection();
          /** @type {HTMLElement|null} */ (document.querySelector('[data-fkey="sel:all"]'))?.focus();
        },
      }, icon('close', { size: 14 }))));
  }
  return head;
}

/** @param {Connection} c @param {number} i @param {Connection[]} rows */
function row(c, i, rows) {
  const on = selected.has(c.id);
  const check = /** @type {HTMLInputElement} */ (h('input', {
    type: 'checkbox', checked: on, 'aria-label': `Select ${c.name}`, dataset: { fkey: `sel:${c.id}` },
    onClick: (/** @type {MouseEvent} */ e) => {
      const box = /** @type {HTMLInputElement} */ (e.target);
      if (e.shiftKey && lastClicked >= 0) {
        const [a, b] = [Math.min(lastClicked, i), Math.max(lastClicked, i)];
        for (const r of rows.slice(a, b + 1)) box.checked ? selected.add(r.id) : selected.delete(r.id);
      } else if (box.checked) selected.add(c.id);
      else selected.delete(c.id);
      lastClicked = i;
      syncSelection();
    },
  }));
  const folder = store.folderPath(c.group_id);
  const auth = store.signsInWith(c);
  const tr = h('tr', { class: on ? 'is-selected' : '' },
    h('td', { class: 'col-check' }, check),
    h('td', { class: 'col-name' }, h('span', { class: 'name-cell' }, labelChip(c.color), reachDot(c),
      h('button', {
        type: 'button', class: 'name-btn', title: `Connect to ${c.name}`, dataset: { fkey: `name:${c.id}` },
        onClick: (/** @type {MouseEvent} */ e) => connect(c.id, { newSession: e.ctrlKey || e.metaKey }),
      }, c.name))),
    h('td', { class: 'mono col-addr' }, formatAddress(c)),
    h('td', { class: folder ? 'col-folder' : 'col-folder muted' }, folder || 'Ungrouped'),
    h('td', { class: 'col-tags' }, ...c.tags.map((t) => h('span', { class: 'chip chip--small' }, `#${t}`))),
    h('td', { class: c.auth_method === 'password' ? 'muted' : 'mono brass' }, auth),
    h('td', { class: 'muted col-when' }, formatWhen(c.last_used_at)),
    h('td', { class: 'muted col-notes', title: c.notes || null }, c.notes),
    h('td', { class: 'col-menu' }, h('button', {
      type: 'button', class: 'icon-btn', 'aria-label': `${c.name} menu`, dataset: { fkey: `menu:${c.id}` },
      onClick: (/** @type {MouseEvent} */ e) => openMenu({ items: connectionMenu(c), label: c.name, anchor: /** @type {HTMLElement} */ (e.currentTarget) }),
    }, icon('more', { size: 16 }))));
  view.boxes.set(c.id, { tr, box: check });
  tr.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    openMenu({ items: connectionMenu(c), label: c.name, x: e.clientX, y: e.clientY });
  });
  return tr;
}

/** Number of selected rows (tests and the shell). */
export function selectedCount() {
  return selected.size;
}
