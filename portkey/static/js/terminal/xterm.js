/*
 * terminal/xterm.js — owner: package 8b (private module of terminal/session.js).
 *
 * Builds an xterm.js Terminal from the vendored UMD builds (window.Terminal, FitAddon,
 * SearchAddon; see js/globals.d.ts) with the DOM renderer, so Playwright can read the rows. No
 * clipboard (OSC 52) or web-links addon is loaded; xterm's built-in OSC 8 hyperlinks go through
 * terminal/links.js (Ctrl/Cmd+click and a dialog, never a plain click). Colours come from the
 * CSS tokens in base.css, read at runtime, so the terminal follows the theme without literal
 * colours in code.
 */

import { linkHandler } from './links.js';

/** @typedef {import('@xterm/xterm').Terminal} Terminal */
/** @typedef {import('@xterm/addon-fit').FitAddon} FitAddon */
/** @typedef {import('@xterm/addon-search').SearchAddon} SearchAddon */

export const MIN_FONT = 8;
export const MAX_FONT = 32;
export const DEFAULT_SIZE = { cols: 120, rows: 40 };

/** @param {string} name @param {string} fallback */
function token(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** "#1f6feb" -> "#1f6feb66" (xterm accepts #RRGGBBAA). @param {string} hex @param {string} alpha */
function withAlpha(hex, alpha) {
  return /^#[0-9a-f]{6}$/i.test(hex) ? `${hex}${alpha}` : hex;
}

/**
 * @param {number} fontSize @param {() => string} [name] the session's connection name, for the
 * link dialog @returns {{term: Terminal, fit: FitAddon, search: SearchAddon}}
 */
export function createTerminal(fontSize, name = () => 'this session') {
  const fg = token('--text', '#c9d1d9');
  const term = new window.Terminal({
    fontFamily: token('--font-term', 'monospace'),
    fontSize: clampFont(fontSize),
    lineHeight: 1.2,
    scrollback: 10000,
    cursorBlink: true,
    allowProposedApi: false,
    // Links a server prints (OSC 8) open only on Ctrl/Cmd+click, after Port-Key's own dialog.
    linkHandler: linkHandler(name),
    theme: {
      background: token('--term-bg', '#010409'),
      foreground: fg,
      cursor: token('--text-strong', fg),
      cursorAccent: token('--term-bg', '#010409'),
      selectionBackground: withAlpha(token('--accent-fill', '#1f6feb'), '66'),
    },
  });
  const fit = new window.FitAddon.FitAddon();
  const search = new window.SearchAddon.SearchAddon();
  term.loadAddon(fit);
  term.loadAddon(search);
  return { term, fit, search };
}

/** @param {number} size */
export function clampFont(size) {
  return Math.min(MAX_FONT, Math.max(MIN_FONT, Math.round(Number(size) || 14)));
}

/** Text of the rendered rows (tests and debugging read the same thing). @param {HTMLElement} host */
export function renderedText(host) {
  return [...host.querySelectorAll('.xterm-rows > div')].map((row) => row.textContent ?? '').join('\n');
}
