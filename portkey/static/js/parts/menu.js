/*
 * parts/menu.js — owner: package 8a (private module; 8b may import it for tab menus).
 *
 * A popup menu (role="menu") for row menus ("⋯", right-click, Shift+F10) and folder menus.
 * openMenu({items, anchor | x/y, label}) positions the menu at the anchor or the pointer,
 * focuses the first item, and closes on selection, Esc, Tab, a click outside or a scroll.
 * Arrow keys, Home and End move between items; a letter jumps to the next item starting
 * with it. Focus returns to the element that opened the menu.
 */

import { h } from '../ui/dom.js';

/** @typedef {{label: string, onSelect: () => void, danger?: boolean, disabled?: boolean} | 'separator'} MenuItem */

/** @type {(() => void)|null} */
let closeCurrent = null;

/**
 * @param {{items: MenuItem[], label: string, anchor?: HTMLElement, x?: number, y?: number}} opts
 * @returns {() => void} close
 */
export function openMenu(opts) {
  closeCurrent?.();
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : opts.anchor ?? null;
  const menu = h('div', { class: 'menu', role: 'menu', 'aria-label': opts.label, tabindex: '-1' });
  /** @type {HTMLButtonElement[]} */
  const buttons = [];
  for (const item of opts.items) {
    if (item === 'separator') {
      menu.appendChild(h('div', { class: 'menu__sep', role: 'separator' }));
      continue;
    }
    const btn = /** @type {HTMLButtonElement} */ (h('button', {
      type: 'button',
      role: 'menuitem',
      tabindex: '-1',
      class: `menu__item${item.danger ? ' menu__item--danger' : ''}`,
      'aria-disabled': item.disabled ? 'true' : null,
      onClick: () => {
        if (item.disabled) return;
        close(false);
        item.onSelect();
      },
    }, item.label));
    buttons.push(btn);
    menu.appendChild(btn);
  }

  const onDocDown = (/** @type {Event} */ e) => {
    if (!menu.contains(/** @type {Node} */ (e.target))) close(false);
  };
  const onScroll = (/** @type {Event} */ e) => {
    if (!menu.contains(/** @type {Node} */ (e.target))) close(false);
  };
  const close = (/** @type {boolean} */ restore = true) => {
    if (!menu.isConnected) return;
    menu.remove();
    document.removeEventListener('mousedown', onDocDown, true);
    document.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('resize', onResize);
    if (closeCurrent === closeFn) closeCurrent = null;
    if (restore && opener?.isConnected) opener.focus({ preventScroll: true });
  };
  const closeFn = () => close(false);
  const onResize = () => close(false);

  menu.addEventListener('keydown', (e) => {
    const i = buttons.indexOf(/** @type {HTMLButtonElement} */ (document.activeElement));
    const move = (/** @type {number} */ to) => {
      e.preventDefault();
      buttons[(to + buttons.length) % buttons.length]?.focus();
    };
    if (e.key === 'ArrowDown') move(i + 1);
    else if (e.key === 'ArrowUp') move(i - 1);
    else if (e.key === 'Home') move(0);
    else if (e.key === 'End') move(buttons.length - 1);
    else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Tab') {
      e.preventDefault();
      close(true);
    } else if (e.key.length === 1 && /\S/.test(e.key)) {
      const ch = e.key.toLowerCase();
      for (let step = 1; step <= buttons.length; step += 1) {
        const b = buttons[(i + step) % buttons.length];
        if (b.textContent?.toLowerCase().startsWith(ch)) {
          b.focus();
          break;
        }
      }
    }
  });

  (document.getElementById('dialogs')?.querySelector('dialog[open]:last-of-type') ?? document.body).appendChild(menu);
  position(menu, opts);
  document.addEventListener('mousedown', onDocDown, true);
  document.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', onResize);
  closeCurrent = closeFn;
  (buttons.find((b) => !b.getAttribute('aria-disabled')) ?? buttons[0])?.focus();
  return () => close(true);
}

/** @param {HTMLElement} menu @param {{anchor?: HTMLElement, x?: number, y?: number}} opts */
function position(menu, opts) {
  const rect = menu.getBoundingClientRect();
  let x = opts.x ?? 0;
  let y = opts.y ?? 0;
  if (opts.anchor && opts.x == null) {
    const a = opts.anchor.getBoundingClientRect();
    x = a.right - rect.width;
    y = a.bottom + 2;
  }
  x = Math.max(4, Math.min(x, window.innerWidth - rect.width - 4));
  if (y + rect.height > window.innerHeight - 4) y = Math.max(4, y - rect.height - (opts.anchor ? 30 : 0));
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
}
