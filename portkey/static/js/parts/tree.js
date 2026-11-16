/*
 * parts/tree.js — owner: package 8a (private module of views/connections.js).
 *
 * The sidebar folder tree as an ARIA tree: Pinned (virtual) first, folders depth-first with
 * sub-folders before connections, Ungrouped last. Folder counts include descendants (server
 * counts; match counts while filtering). Collapsed state comes from the store (per browser);
 * while a filter is active every folder with matches is shown open and the rest are hidden.
 *
 * Keyboard (roving focus): Up/Down move, Right opens a folder or enters it, Left closes it or
 * goes to the parent, Home/End, Enter connects (Ctrl+Enter: another session) or toggles a
 * folder, Shift+F10 / the Menu key opens the item's menu, and typing a letter jumps to the
 * next item starting with it. Mouse: click connects (a double click counts once), Ctrl+click
 * or middle-click opens another session, right-click or "⋯" opens the menu, and dragging a
 * connection onto a folder moves it (onto Pinned pins it).
 */

import * as store from '../store.js';
import { h, clear, formatAddress, labelClass } from '../ui/dom.js';
import { icon } from './icons.js';
import { openMenu } from './menu.js';
import { reachDot, authGlyph, sessionBadge } from './rows.js';
import { connect, connectionMenu, moveConnection, togglePin } from './actions.js';
import { folderMenu, newFolder } from './folderactions.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../types.js').Group} Group */

const DRAG_TYPE = 'application/x-portkey-connection';
const PINNED = '__pinned';
const UNGROUPED = '__ungrouped';

/** Key of the item that holds the roving tabindex. */
let currentKey = '';
let lastActivation = { id: '', at: 0 };

/**
 * @param {HTMLElement} tree  the ul[role=tree]
 * @param {{matches: Set<string>|null}} opts  null = no filter active
 */
export function renderTree(tree, opts) {
  const { connections } = store.getState();
  const index = store.folders();
  const filtering = opts.matches !== null;
  const visible = (/** @type {Connection} */ c) => !opts.matches || opts.matches.has(c.id);
  const hadFocus = tree.contains(document.activeElement);
  clear(tree);

  /** @type {Map<string|null, Connection[]>} */
  const byFolder = new Map();
  for (const c of connections) {
    const key = c.group_id && index.byId.has(c.group_id) ? c.group_id : null;
    if (!byFolder.has(key)) byFolder.set(key, []);
    byFolder.get(key)?.push(c);
  }
  const sortConns = (/** @type {Connection[]} */ list) =>
    [...list].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));

  /** @type {Map<string, number>} */
  const matchCount = new Map();
  const countMatches = (/** @type {string} */ id) => {
    if (matchCount.has(id)) return /** @type {number} */ (matchCount.get(id));
    let n = (byFolder.get(id) ?? []).filter(visible).length;
    for (const child of index.children(id)) n += countMatches(child.id);
    matchCount.set(id, n);
    return n;
  };

  const pinned = sortConns(connections.filter((c) => c.pinned && visible(c)));
  if (pinned.length) {
    tree.appendChild(folderItem({ key: PINNED, label: 'Pinned', count: pinned.length, level: 1, open: filtering || !store.isFolderCollapsed(PINNED),
      children: pinned.map((c) => connItem(c, 2, PINNED)), drop: (c) => { if (!c.pinned) togglePin(c); } }));
  }
  const walk = (/** @type {string|null} */ parent, /** @type {number} */ level, /** @type {HTMLElement} */ into) => {
    for (const g of index.children(parent)) {
      if (filtering && countMatches(g.id) === 0) continue;
      const open = filtering || !store.isFolderCollapsed(g.id);
      const sub = h('ul', { role: 'group' });
      if (open) {
        walk(g.id, level + 1, sub);
        for (const c of sortConns((byFolder.get(g.id) ?? []).filter(visible))) sub.appendChild(connItem(c, level + 1, g.id));
      }
      into.appendChild(folderItem({
        key: g.id, label: g.name, count: filtering ? countMatches(g.id) : g.connection_count, level,
        open, children: [...sub.children], group: g, drop: (c) => moveConnection(c, g.id),
      }));
    }
  };
  walk(null, 1, tree);
  const loose = sortConns((byFolder.get(null) ?? []).filter(visible));
  if (loose.length || (!filtering && index.ordered.length)) {
    tree.appendChild(folderItem({ key: UNGROUPED, label: 'Ungrouped', count: loose.length, level: 1,
      open: filtering || !store.isFolderCollapsed(UNGROUPED), children: loose.map((c) => connItem(c, 2, UNGROUPED)),
      drop: (c) => moveConnection(c, null) }));
  }

  const items = treeItems(tree);
  const current = items.find((el) => el.dataset.key === currentKey) ?? items[0];
  if (current) {
    current.tabIndex = 0;
    currentKey = current.dataset.key ?? '';
    if (hadFocus) current.focus({ preventScroll: true });
  }
}

/**
 * @param {{key: string, label: string, count: number, level: number, open: boolean, children: Element[],
 *   group?: Group, drop: (c: Connection) => void}} f
 */
function folderItem(f) {
  const virtual = f.key === PINNED || f.key === UNGROUPED;
  const head = h('div', { class: `tree-folder__head${virtual ? ' tree-folder__head--virtual' : ''}` },
    icon('chevron', { size: 12, className: 'tree-chevron' }),
    h('span', { class: 'tree-folder__name' }, f.label),
    h('span', { class: 'tree-folder__count' }, String(f.count), h('span', { class: 'visually-hidden' }, ' connections')));
  if (f.key !== PINNED) {
    head.appendChild(h('button', {
      type: 'button', class: 'icon-btn tree-more', tabindex: '-1', 'aria-label': `${f.label} folder menu`,
      onClick: (/** @type {MouseEvent} */ e) => {
        e.stopPropagation();
        openFolderMenu(f.group, /** @type {HTMLElement} */ (e.currentTarget));
      },
    }, icon('more', { size: 16 })));
  }
  const li = h('li', {
    role: 'treeitem', class: 'tree-folder', tabindex: '-1', 'aria-level': String(f.level),
    'aria-expanded': String(f.open), 'aria-label': `${f.label}, ${f.count}`, dataset: { key: `f:${f.key}`, folder: f.key },
  }, head);
  const group = h('ul', { role: 'group' });
  for (const child of f.children) group.appendChild(child);
  if (f.open) li.appendChild(group);
  head.addEventListener('click', () => {
    setCurrent(li);
    toggle(li);
  });
  head.addEventListener('contextmenu', (e) => {
    if (f.key === PINNED) return;
    e.preventDefault();
    openFolderMenu(f.group, null, e.clientX, e.clientY);
  });
  head.addEventListener('dragover', (e) => {
    if (!e.dataTransfer?.types.includes(DRAG_TYPE)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    head.classList.add('is-drop');
  });
  head.addEventListener('dragleave', () => head.classList.remove('is-drop'));
  head.addEventListener('drop', (e) => {
    head.classList.remove('is-drop');
    const id = e.dataTransfer?.getData(DRAG_TYPE);
    const c = store.connectionById(id);
    if (!c) return;
    e.preventDefault();
    f.drop(c);
  });
  return li;
}

/** @param {Group|undefined} group @param {HTMLElement|null} anchor @param {number} [x] @param {number} [y] */
function openFolderMenu(group, anchor, x, y) {
  const items = group ? folderMenu(group) : [{ label: 'New folder', onSelect: () => { newFolder(null); } }];
  openMenu({ items, label: group ? `${group.name} folder` : 'Ungrouped', anchor: anchor ?? undefined, x, y });
}

/** @param {Connection} c @param {number} level @param {string} section */
function connItem(c, level, section) {
  const more = h('button', {
    type: 'button', class: 'icon-btn row-more', tabindex: '-1', 'aria-label': `${c.name} menu`,
    onClick: (/** @type {MouseEvent} */ e) => {
      e.stopPropagation();
      openMenu({ items: connectionMenu(c), label: c.name, anchor: /** @type {HTMLElement} */ (e.currentTarget) });
    },
  }, icon('more', { size: 16 }));
  const row = h('div', { class: `conn-row ${labelClass(c.color)}` },
    h('span', { class: 'conn-row__stripe' }),
    reachDot(c),
    h('span', { class: 'conn-row__text' },
      h('span', { class: 'conn-row__name' }, c.name),
      h('span', { class: 'conn-row__addr mono' }, formatAddress(c))),
    sessionBadge(c),
    authGlyph(c),
    more);
  const li = h('li', {
    role: 'treeitem', class: 'tree-conn', tabindex: '-1', 'aria-level': String(level), draggable: 'true',
    dataset: { key: `c:${section}:${c.id}`, id: c.id },
  }, row);
  li.addEventListener('click', (e) => {
    setCurrent(li);
    activate(c, e.ctrlKey || e.metaKey);
  });
  li.addEventListener('auxclick', (e) => {
    if (e.button !== 1) return;
    e.preventDefault();
    activate(c, true);
  });
  li.addEventListener('mousedown', (e) => {
    if (e.button === 1) e.preventDefault();
  });
  li.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    setCurrent(li);
    openMenu({ items: connectionMenu(c), label: c.name, x: e.clientX, y: e.clientY });
  });
  li.addEventListener('dragstart', (e) => {
    e.dataTransfer?.setData(DRAG_TYPE, c.id);
    e.dataTransfer?.setData('text/plain', c.name);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  });
  return li;
}

/** Connect once per gesture: a double click is one action. @param {Connection} c @param {boolean} extra */
function activate(c, extra) {
  const now = Date.now();
  if (!extra && lastActivation.id === c.id && now - lastActivation.at < 500) return;
  lastActivation = { id: c.id, at: now };
  document.dispatchEvent(new CustomEvent('portkey:navigate'));
  connect(c.id, { newSession: extra });
}

/** @param {HTMLElement} tree @returns {HTMLElement[]} visible treeitems in order */
function treeItems(tree) {
  return /** @type {HTMLElement[]} */ ([...tree.querySelectorAll('[role="treeitem"]')]);
}

/** @param {HTMLElement} li */
function setCurrent(li) {
  const tree = li.closest('[role="tree"]');
  if (tree) for (const el of treeItems(/** @type {HTMLElement} */ (tree))) el.tabIndex = -1;
  li.tabIndex = 0;
  currentKey = li.dataset.key ?? '';
}

/** @param {HTMLElement} li @param {boolean} [open] */
function toggle(li, open) {
  const folder = li.dataset.folder;
  if (!folder) return;
  const isOpen = li.getAttribute('aria-expanded') === 'true';
  const next = open ?? !isOpen;
  if (next === isOpen) return;
  store.setFolderCollapsed(folder, !next);
}

/** Wire keyboard handling once on the tree element. @param {HTMLElement} tree */
export function wireTreeKeys(tree) {
  tree.addEventListener('focusin', (e) => {
    const li = /** @type {HTMLElement} */ (e.target).closest('[role="treeitem"]');
    if (li instanceof HTMLElement) setCurrent(li);
  });
  tree.addEventListener('keydown', (e) => {
    const li = /** @type {HTMLElement} */ (e.target).closest('[role="treeitem"]');
    if (!(li instanceof HTMLElement) || e.altKey) return;
    const items = treeItems(tree);
    const i = items.indexOf(li);
    const go = (/** @type {HTMLElement|undefined} */ el) => {
      e.preventDefault();
      if (!el) return;
      setCurrent(el);
      el.focus();
    };
    const isFolder = Boolean(li.dataset.folder);
    const open = li.getAttribute('aria-expanded') === 'true';
    const c = store.connectionById(li.dataset.id);
    if (e.key === 'ArrowDown') go(items[i + 1]);
    else if (e.key === 'ArrowUp') go(items[i - 1]);
    else if (e.key === 'Home') go(items[0]);
    else if (e.key === 'End') go(items[items.length - 1]);
    else if (e.key === 'ArrowRight') {
      e.preventDefault();
      if (isFolder && !open) toggle(li, true);
      else if (isFolder) go(items[i + 1]);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (isFolder && open) toggle(li, false);
      else go(/** @type {HTMLElement|undefined} */ (li.parentElement?.closest('[role="treeitem"]') ?? undefined));
    } else if (e.key === 'Enter' || (e.key === ' ' && isFolder)) {
      e.preventDefault();
      if (isFolder) toggle(li);
      else if (c) activate(c, e.ctrlKey || e.metaKey || e.shiftKey);
    } else if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
      e.preventDefault();
      if (c) openMenu({ items: connectionMenu(c), label: c.name, anchor: li });
      else if (li.dataset.folder !== PINNED) openFolderMenu(store.folders().byId.get(li.dataset.folder ?? ''), li);
    } else if (e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey) {
      const ch = e.key.toLowerCase();
      const name = (/** @type {HTMLElement} */ el) => (el.querySelector('.conn-row__name, .tree-folder__name')?.textContent ?? '').toLowerCase();
      for (let step = 1; step <= items.length; step += 1) {
        const el = items[(i + step) % items.length];
        if (name(el).startsWith(ch)) {
          go(el);
          break;
        }
      }
    }
  });
}
