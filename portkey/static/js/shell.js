/*
 * shell.js — owner: package 8a.
 *
 * The app shell after sign-in: top bar (wordmark, quick-connect field that opens the
 * palette, unlocked-keys indicator with Lock, Keys and Settings buttons), the resizable sidebar
 * (200-420px, drawer below 900px) and the main pane hosting ui/tabstrip.js. Global shortcuts:
 * Ctrl+Shift+K / Ctrl+K palette, Alt+1..9 tabs, Alt+0 Home, "/" focuses the filter, and the
 * leave warning when sessions are connected.
 *
 * Mount points for package 8b:
 * - terminal tabs: ui/tabstrip.js addTab() (the Home tab is id "home");
 * - the palette: the quick-connect field and Ctrl+Shift+K (also Ctrl+Shift+Space, for Edge)
 *   call ui/palette.js openPalette(); its commands can call openToolTab('keys' | 'settings'),
 *   views/keys.js openGenerateKey() and lockAllKeys() from here;
 * - the wizard: every Add connection button calls views/wizard.js openWizard(prefill);
 * - sessions: rows call views/sessions.js openSession(id, {newSession}), and sessions report
 *   counts back with store.setSessionInfo().
 * The terminal counts as "focused" when focus is inside an element with class "xterm": then
 * Ctrl+K and "/" go to the shell, as docs/ux-spec.md requires.
 */

import * as api from './api.js';
import * as store from './store.js';
import { h } from './ui/dom.js';
import { icon } from './parts/icons.js';
import { toast, toastError } from './ui/toast.js';
import { mountTabStrip, addTab, activateTab, activateTabAt, onTabChange, getActiveTabId } from './ui/tabstrip.js';
import { renderSidebar } from './views/connections.js';
import { renderHome } from './views/home.js';
import { renderKeys } from './views/keys.js';
import { renderSettings, setDiagnosticsOpener } from './views/settings.js';
import { renderDiagnostics } from './views/diagnostics.js';
import { openQuickConnect } from './parts/actions.js';

const WIDTH_KEY = 'portkey.sidebar.width';
const MIN_W = 200;
const MAX_W = 420;
const NARROW = '(max-width: 899px)';

/** @type {Record<string, {title: string, render: (panel: HTMLElement) => void}>} */
const TOOLS = {
  keys: { title: 'Keys', render: renderKeys },
  settings: { title: 'Settings', render: renderSettings },
  diagnostics: { title: 'Diagnostics', render: renderDiagnostics },
};

/** Open (or focus) the Keys, Settings or Diagnostics tab. @param {'keys'|'settings'|'diagnostics'} kind */
export function openToolTab(kind) {
  const tool = TOOLS[kind];
  addTab({ id: kind, title: tool.title, closable: true, render: tool.render });
}

/** Focus the sidebar filter (opens the drawer in a narrow window). */
export function focusFilter() {
  document.dispatchEvent(new CustomEvent('portkey:open-drawer'));
  /** @type {HTMLElement|null} */ (document.getElementById('sidebar-filter'))?.focus();
}

/** "Lock keys": drop every unlocked key. */
export async function lockAllKeys() {
  try {
    await api.lockKeys();
    await store.refreshKeys();
    toast('Keys locked.');
  } catch (err) {
    if (!(err instanceof api.ApiError && err.code === 'unauthorized')) toastError(err);
  }
}

/** @returns {number} */
function savedWidth() {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY));
    return n >= MIN_W && n <= MAX_W ? n : 280;
  } catch {
    return 280;
  }
}

/** @param {HTMLElement} root @returns {void} */
export function renderShell(root) {
  root.removeAttribute('aria-live');
  const menuBtn = h('button', {
    type: 'button', class: 'icon-btn topbar__menu', 'aria-label': 'Connections', 'aria-expanded': 'false', 'aria-controls': 'sidebar',
  }, icon('menu', { size: 18 }));
  const quick = h('button', { type: 'button', class: 'quick', 'aria-keyshortcuts': 'Control+Shift+K', onClick: () => openQuickConnect() },
    icon('search', { size: 14, className: 'quick__icon' }), h('span', { class: 'quick__text' }, 'Connect to…'),
    h('kbd', { class: 'quick__kbd' }, 'Ctrl+Shift+K'));
  const unlocked = h('span', { class: 'unlocked', hidden: true });
  const lock = h('button', { type: 'button', class: 'btn btn--outline btn--xs', hidden: true, onClick: () => lockAllKeys() }, 'Lock');
  const keysBtn = h('button', { type: 'button', class: 'topbar__btn', onClick: () => openToolTab('keys') }, 'Keys');
  const settingsBtn = h('button', { type: 'button', class: 'topbar__btn', onClick: () => openToolTab('settings') }, 'Settings');
  const topbar = h('header', { class: 'topbar' },
    menuBtn,
    h('button', { type: 'button', class: 'wordmark', 'aria-label': 'Port-Key, go to Home', onClick: () => activateTab('home') },
      'Port', icon('key', { size: 22, strokeWidth: 2.2, className: 'wordmark__key' }), 'Key'),
    quick,
    h('div', { class: 'topbar__right' }, unlocked, lock, keysBtn, settingsBtn));

  const sidebar = h('nav', { id: 'sidebar', class: 'sidebar', 'aria-label': 'Connections' });
  const resizer = h('div', {
    class: 'sidebar__resizer', role: 'separator', tabindex: '0', 'aria-orientation': 'vertical', 'aria-label': 'Resize sidebar',
    'aria-controls': 'sidebar', 'aria-valuemin': String(MIN_W), 'aria-valuemax': String(MAX_W),
  });
  const backdrop = h('div', { class: 'drawer-backdrop', hidden: true });
  const strip = h('div', { class: 'tabstrip' });
  const panels = h('div', { class: 'tabpanels' });
  const main = h('main', { class: 'main' }, strip, panels);
  const app = h('div', { class: 'app' }, topbar, h('div', { class: 'app__body' }, sidebar, resizer, backdrop, main));
  root.appendChild(app);

  const setWidth = (/** @type {number} */ w, /** @type {boolean} */ persist) => {
    const width = Math.round(Math.min(MAX_W, Math.max(MIN_W, w)));
    app.style.setProperty('--sidebar-w', `${width}px`);
    resizer.setAttribute('aria-valuenow', String(width));
    if (persist) {
      try { localStorage.setItem(WIDTH_KEY, String(width)); } catch { /* per-browser only */ }
    }
    return width;
  };
  let width = setWidth(savedWidth(), false);
  wireResize(resizer, () => width, (w, persist) => { width = setWidth(w, persist); });

  renderSidebar(sidebar);
  mountTabStrip(strip, panels);
  addTab({ id: 'home', title: 'Home', icon: 'home', closable: false, render: (panel) => { renderHome(panel); } });
  setDiagnosticsOpener(() => openToolTab('diagnostics'));

  wireDrawer(app, sidebar, menuBtn, backdrop);
  const syncTop = () => {
    const { unlockedCount } = store.getState();
    unlocked.hidden = unlockedCount === 0;
    lock.hidden = unlockedCount === 0;
    unlocked.replaceChildren(icon('lock', { size: 14 }), `${unlockedCount} key${unlockedCount === 1 ? '' : 's'} unlocked`);
    const active = getActiveTabId();
    keysBtn.classList.toggle('is-active', active === 'keys');
    settingsBtn.classList.toggle('is-active', active === 'settings');
  };
  syncTop();
  store.subscribe(syncTop);
  onTabChange(syncTop);
  document.addEventListener('keydown', onShortcut, true);
  window.addEventListener('beforeunload', (e) => {
    const { settings, connectedSessions } = store.getState();
    if (connectedSessions > 0 && settings?.leave_warning !== false) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

/**
 * @param {HTMLElement} resizer @param {() => number} get
 * @param {(w: number, persist: boolean) => void} set
 */
function wireResize(resizer, get, set) {
  resizer.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    resizer.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const start = get();
    resizer.classList.add('is-dragging');
    const move = (/** @type {PointerEvent} */ ev) => set(start + ev.clientX - startX, false);
    const up = (/** @type {PointerEvent} */ ev) => {
      set(start + ev.clientX - startX, true);
      resizer.classList.remove('is-dragging');
      resizer.removeEventListener('pointermove', move);
      resizer.removeEventListener('pointerup', up);
    };
    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', up);
  });
  resizer.addEventListener('keydown', (e) => {
    const steps = { ArrowLeft: -16, ArrowRight: 16 };
    if (e.key in steps) set(get() + steps[/** @type {'ArrowLeft'|'ArrowRight'} */ (e.key)], true);
    else if (e.key === 'Home') set(MIN_W, true);
    else if (e.key === 'End') set(MAX_W, true);
    else return;
    e.preventDefault();
  });
  resizer.addEventListener('dblclick', () => set(280, true));
}

/**
 * Below 900px the sidebar is an overlay drawer opened from the menu button.
 * @param {HTMLElement} app @param {HTMLElement} sidebar @param {HTMLElement} menuBtn @param {HTMLElement} backdrop
 */
function wireDrawer(app, sidebar, menuBtn, backdrop) {
  const media = window.matchMedia(NARROW);
  let open = false;
  const apply = () => {
    const narrow = media.matches;
    app.classList.toggle('app--narrow', narrow);
    app.classList.toggle('app--drawer-open', narrow && open);
    backdrop.hidden = !(narrow && open);
    sidebar.inert = narrow && !open;
    menuBtn.setAttribute('aria-expanded', String(narrow && open));
  };
  const set = (/** @type {boolean} */ value, /** @type {boolean} */ restoreFocus) => {
    open = value;
    apply();
    if (open) /** @type {HTMLElement|null} */ (sidebar.querySelector('#sidebar-filter'))?.focus();
    else if (restoreFocus && media.matches) menuBtn.focus();
  };
  menuBtn.addEventListener('click', () => set(!open, true));
  backdrop.addEventListener('click', () => set(false, true));
  sidebar.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open && media.matches && !e.defaultPrevented) {
      e.preventDefault();
      set(false, true);
    }
  });
  document.addEventListener('portkey:navigate', () => { if (open) set(false, false); });
  document.addEventListener('portkey:open-drawer', () => { if (media.matches && !open) set(true, false); });
  media.addEventListener('change', apply);
  apply();
}

/** @returns {boolean} */
function terminalHasFocus() {
  const el = document.activeElement;
  return Boolean(el && el.closest('.xterm'));
}

/** @returns {boolean} */
function editingText() {
  const el = document.activeElement;
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || el.matches('input, textarea, select');
}

/** @param {KeyboardEvent} e */
function onShortcut(e) {
  if (document.querySelector('dialog[open]')) return;
  const key = e.key.toLowerCase();
  if (e.ctrlKey && e.shiftKey && !e.altKey && (key === 'k' || e.code === 'Space')) {
    e.preventDefault();
    e.stopPropagation();
    openQuickConnect();
  } else if (e.ctrlKey && !e.shiftKey && !e.altKey && key === 'k' && !terminalHasFocus()) {
    e.preventDefault();
    openQuickConnect();
  } else if (e.altKey && !e.ctrlKey && !e.shiftKey && /^Digit[0-9]$/.test(e.code)) {
    e.preventDefault();
    e.stopPropagation();
    activateTabAt(Number(e.code.slice(5)));
  } else if (e.key === '/' && !e.ctrlKey && !e.altKey && !e.metaKey && !terminalHasFocus() && !editingText()) {
    e.preventDefault();
    focusFilter();
  }
}
