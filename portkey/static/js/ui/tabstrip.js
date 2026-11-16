/*
 * tabstrip.js — owner: package 8a.
 *
 * The main pane's tab strip: Home pinned at the far left (house icon), closable tabs for
 * Keys, Settings, Diagnostics and terminal sessions, middle-click close, drag reorder,
 * horizontal scroll when narrow. Panels stay mounted while hidden, so sessions never unmount.
 *
 * Mount point for package 8b (terminal tabs): call addTab() with a TabSpec whose render(panel)
 * builds the terminal inside `panel` once. The panel is a role="tabpanel" element that is
 * hidden (not removed) while another tab is active; onActivate(panel) runs each time the tab
 * becomes active (fit the terminal there). updateTab(id, {title, color, muted, dot, activity})
 * changes the title, the colour-label top edge (a Color such as "red"), the muted text of a
 * disconnected session, the status dot ("connecting" pulsing amber, "connected" green,
 * "disconnected" grey ring, or null) and the small blue activity dot after the title (new
 * output in a background tab; ignored for the active tab and cleared when it is shown).
 *
 * Keyboard: the strip is a tablist with roving focus; Left/Right/Home/End move and activate,
 * Delete closes a closable tab. Closing a tab activates the tab on its left.
 *
 * Exports: mountTabStrip (shell only), addTab, activateTab, closeTab, updateTab, hasTab,
 * getActiveTabId, listTabs, activateTabAt, onTabChange.
 */

import { h, clear, labelClass } from './dom.js';
import { icon } from '../parts/icons.js';

/**
 * @typedef {object} TabSpec
 * @property {string} id
 * @property {string} title
 * @property {boolean} [closable]
 * @property {string|null} [color] colour-label token name for the top edge
 * @property {(panel: HTMLElement) => void} render called once
 * @property {() => boolean|void} [onClose] return false to keep the tab
 * @property {(panel: HTMLElement) => void} [onActivate] called whenever the tab becomes active
 * @property {'home'} [icon]   show an icon instead of the title (the title becomes its label)
 * @property {boolean} [activate] default true: activate the new tab
 */

/** @typedef {{spec: TabSpec, tab: HTMLElement, panel: HTMLElement, title: HTMLElement, dot: HTMLElement, activity: HTMLElement}} TabRecord */

/** @type {Map<string, TabRecord>} */
const tabs = new Map();
/** @type {string[]} */
let order = [];
/** @type {string|null} */
let active = null;
/** @type {HTMLElement|null} */
let stripEl = null;
/** @type {HTMLElement|null} */
let panelsEl = null;
/** @type {Set<(id: string|null) => void>} */
const listeners = new Set();
/** @type {string|null} */
let dragging = null;

const DOT_TEXT = { connecting: 'connecting', connected: 'connected', disconnected: 'disconnected' };

/** Shell only: attach the strip and panel containers. @param {HTMLElement} strip @param {HTMLElement} panels */
export function mountTabStrip(strip, panels) {
  stripEl = strip;
  panelsEl = panels;
  strip.setAttribute('role', 'tablist');
  strip.setAttribute('aria-label', 'Open tabs');
  strip.addEventListener('keydown', onKeydown);
}

/** @param {string} id */
const domId = (id) => `tab-${id.replace(/[^A-Za-z0-9_-]/g, '_')}`;

/** @param {TabSpec} spec @returns {HTMLElement} the panel */
export function addTab(spec) {
  const existing = tabs.get(spec.id);
  if (existing) {
    if (spec.activate !== false) activateTab(spec.id);
    return existing.panel;
  }
  if (!stripEl || !panelsEl) throw new Error('tab strip is not mounted');
  const tid = domId(spec.id);
  const title = h('span', { class: 'tab__title' }, spec.title);
  const dot = h('span', { class: 'tab__dot', hidden: true });
  const activity = h('span', { class: 'tab__activity', hidden: true });
  const tab = h('div', {
    id: tid,
    class: `tab${spec.icon ? ' tab--icon' : ''}`,
    role: 'tab',
    tabindex: '-1',
    'aria-selected': 'false',
    'aria-controls': `${tid}-panel`,
    draggable: spec.closable ? 'true' : null,
    dataset: { tab: spec.id },
  });
  if (spec.icon) {
    tab.setAttribute('aria-label', spec.title);
    tab.appendChild(icon(spec.icon, { size: 15 }));
  } else {
    tab.append(dot, title, activity);
  }
  if (spec.closable) {
    tab.appendChild(h('button', {
      type: 'button',
      class: 'tab__close',
      tabindex: '-1',
      'aria-label': `Close ${spec.title}`,
      onClick: (/** @type {MouseEvent} */ e) => {
        e.stopPropagation();
        closeTab(spec.id);
      },
    }, icon('close', { size: 12 })));
  }
  tab.addEventListener('mousedown', (e) => {
    if (e.button === 1) e.preventDefault();
  });
  tab.addEventListener('click', () => activateTab(spec.id));
  tab.addEventListener('auxclick', (e) => {
    if (e.button === 1 && spec.closable) {
      e.preventDefault();
      closeTab(spec.id);
    }
  });
  if (spec.closable) wireDrag(tab, spec.id);
  const panel = h('div', {
    id: `${tid}-panel`, class: 'tabpanel', role: 'tabpanel', 'aria-labelledby': tid, hidden: true,
    dataset: { panel: spec.id },
  });
  const record = { spec, tab, panel, title, dot, activity };
  tabs.set(spec.id, record);
  order.push(spec.id);
  stripEl.appendChild(tab);
  panelsEl.appendChild(panel);
  applyColor(record, spec.color ?? null);
  spec.render(panel);
  if (spec.activate !== false || active === null) activateTab(spec.id);
  return panel;
}

/** @param {TabRecord} record @param {string|null} color */
function applyColor(record, color) {
  record.tab.className = record.tab.className.replace(/\s?lbl-\w+/g, '').replace(/\s?tab--labelled/g, '');
  if (color) record.tab.classList.add('tab--labelled', labelClass(color));
}

/** @param {string} id */
export function activateTab(id) {
  const record = tabs.get(id);
  if (!record) return;
  const changed = active !== id;
  for (const [key, r] of tabs) {
    const on = key === id;
    r.tab.setAttribute('aria-selected', String(on));
    r.tab.tabIndex = on ? 0 : -1;
    r.tab.classList.toggle('tab--active', on);
    r.panel.hidden = !on;
  }
  active = id;
  showActivity(record, false);
  record.tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  record.spec.onActivate?.(record.panel);
  if (changed) for (const fn of listeners) fn(id);
}

/** @param {string} id */
export function closeTab(id) {
  const record = tabs.get(id);
  if (!record || !record.spec.closable) return;
  if (record.spec.onClose?.() === false) return;
  const index = order.indexOf(id);
  const hadFocus = record.tab.contains(document.activeElement) || record.panel.contains(document.activeElement);
  record.tab.remove();
  record.panel.remove();
  tabs.delete(id);
  order = order.filter((t) => t !== id);
  if (active === id) {
    active = null;
    const next = order[Math.max(0, index - 1)];
    if (next) {
      activateTab(next);
      if (hadFocus) tabs.get(next)?.tab.focus();
    } else {
      for (const fn of listeners) fn(null);
    }
  }
}

/** The activity dot; its screen-reader text exists only while it shows. @param {TabRecord} record @param {boolean} on */
function showActivity(record, on) {
  if (!record.activity.hidden === on) return;
  record.activity.hidden = !on;
  clear(record.activity);
  if (on) record.activity.appendChild(h('span', { class: 'visually-hidden' }, ', new output'));
}

/** @param {string} id @param {{title?: string, color?: string|null, muted?: boolean, dot?: string|null, activity?: boolean}} patch */
export function updateTab(id, patch) {
  const record = tabs.get(id);
  if (!record) return;
  if (patch.title != null) {
    record.spec.title = patch.title;
    record.title.textContent = patch.title;
    record.tab.querySelector('.tab__close')?.setAttribute('aria-label', `Close ${patch.title}`);
  }
  if (patch.color !== undefined) applyColor(record, patch.color);
  if (patch.muted !== undefined) record.tab.classList.toggle('tab--muted', patch.muted);
  if (patch.activity !== undefined) showActivity(record, patch.activity && active !== id);
  if (patch.dot !== undefined) {
    const state = patch.dot ?? '';
    record.dot.dataset.state = state;
    record.dot.className = `tab__dot${state ? ` tab__dot--${state}` : ''}`;
    record.dot.hidden = !state;
    clear(record.dot);
    const text = /** @type {Record<string, string>} */ (DOT_TEXT)[state];
    if (text) record.dot.appendChild(h('span', { class: 'visually-hidden' }, `, ${text}`));
  }
}

/** @param {string} id */
export function hasTab(id) {
  return tabs.has(id);
}

export function getActiveTabId() {
  return active;
}

/** @returns {string[]} tab ids in strip order */
export function listTabs() {
  return [...order];
}

/** Alt+1..9: the nth tab after Home (1-based); 0 is Home. @param {number} n */
export function activateTabAt(n) {
  const id = order[n];
  if (id) {
    activateTab(id);
    tabs.get(id)?.tab.focus();
  }
}

/** @param {(id: string|null) => void} listener @returns {() => void} unsubscribe */
export function onTabChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** @param {KeyboardEvent} e */
function onKeydown(e) {
  const target = /** @type {HTMLElement} */ (e.target);
  const id = target.closest('[role="tab"]') ? /** @type {HTMLElement} */ (target.closest('[role="tab"]')).dataset.tab : null;
  if (!id) return;
  const i = order.indexOf(id);
  /** @type {string|undefined} */
  let next;
  if (e.key === 'ArrowRight') next = order[(i + 1) % order.length];
  else if (e.key === 'ArrowLeft') next = order[(i - 1 + order.length) % order.length];
  else if (e.key === 'Home') next = order[0];
  else if (e.key === 'End') next = order[order.length - 1];
  else if (e.key === 'Delete') {
    e.preventDefault();
    closeTab(id);
    return;
  }
  if (next) {
    e.preventDefault();
    activateTab(next);
    tabs.get(next)?.tab.focus();
  }
}

/** @param {HTMLElement} tab @param {string} id */
function wireDrag(tab, id) {
  tab.addEventListener('dragstart', (e) => {
    dragging = id;
    e.dataTransfer?.setData('text/plain', '');
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    tab.classList.add('tab--dragging');
  });
  tab.addEventListener('dragend', () => {
    dragging = null;
    tab.classList.remove('tab--dragging');
  });
  tab.addEventListener('dragover', (e) => {
    if (!dragging || dragging === id) return;
    e.preventDefault();
    const from = tabs.get(dragging);
    if (!from || !stripEl) return;
    const rect = tab.getBoundingClientRect();
    const after = e.clientX > rect.left + rect.width / 2;
    stripEl.insertBefore(from.tab, after ? tab.nextSibling : tab);
    order = [...stripEl.querySelectorAll('[role="tab"]')].map((el) => /** @type {HTMLElement} */ (el).dataset.tab ?? '');
  });
}
