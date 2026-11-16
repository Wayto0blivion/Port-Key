/*
 * terminal/input.js — owner: package 8b (private module of terminal/session.js).
 *
 * Keys, copy and paste inside a terminal (docs/ux-spec.md "Copy and paste", "Shortcut rule"):
 * - Ctrl+C copies when text is selected and goes to the shell (interrupt) otherwise; Ctrl+V
 *   pastes. Both only with the "like Windows Terminal" setting on; off, they go to the shell.
 * - Ctrl+Insert copies and Shift+Insert pastes, always. Ctrl+Shift+C / Ctrl+Shift+V too.
 * - Right-click copies when text is selected and pastes otherwise.
 * - Every paste goes through one guard: more than one line asks "Paste 14 lines into web-prod?".
 * - Only Ctrl+Shift and Alt combinations belong to the app; Ctrl+Shift+F opens Find. Every
 *   other key (Esc, Ctrl+K, Ctrl+W's neighbours, Alt+letter for readline) goes to the shell.
 *   The shell's own Ctrl+Shift+K and Alt+digit listeners run before xterm sees the key.
 *
 * Keyboard pastes use the browser's native paste event (no clipboard permission needed); the
 * right-click paste reads the clipboard through the async API, which may ask once.
 */

import { copyText } from '../ui/dom.js';
import { toast } from '../ui/toast.js';
import { confirmPaste } from '../ui/dialogs.js';
import { pasteLines } from './messages.js';

/** @typedef {import('@xterm/xterm').Terminal} Terminal */

/**
 * @typedef {object} InputContext
 * @property {() => boolean} copyPaste       the Windows Terminal setting
 * @property {() => string} name             connection name for the paste question
 * @property {() => void} onFind
 * @property {() => boolean} onEnter         Enter pressed: return true when it was used (reconnect)
 */

/** @param {Terminal} term */
async function copySelection(term) {
  const text = term.getSelection();
  if (!text) return;
  term.clearSelection();
  const ok = await copyText(text);
  if (!ok) toast("Couldn't copy to the clipboard.", { kind: 'error' });
}

/**
 * Paste with the multi-line guard.
 * @param {Terminal} term @param {string} text @param {string} name
 */
export async function guardedPaste(term, text, name) {
  if (!text) return;
  const lines = pasteLines(text);
  if (lines > 1 && !(await confirmPaste(lines, name))) {
    term.focus();
    return;
  }
  term.paste(text);
  term.focus();
}

/**
 * @param {Terminal} term @param {HTMLElement} host the element xterm opened in
 * @param {InputContext} ctx
 * @returns {() => void} removes the document listener
 */
export function wireInput(term, host, ctx) {
  term.attachCustomKeyEventHandler((ev) => {
    if (ev.type !== 'keydown') return !(ev.ctrlKey && ev.shiftKey);
    const ctrl = ev.ctrlKey && !ev.metaKey;
    if (ctrl && ev.shiftKey && !ev.altKey) {
      if (ev.code === 'KeyF') {
        ev.preventDefault();
        ctx.onFind();
      } else if (ev.code === 'KeyC') {
        ev.preventDefault();
        copySelection(term);
      }
      // Ctrl+Shift+V: the browser's own paste fires a paste event. Anything else
      // with Ctrl+Shift belongs to the app or the browser, never to the shell.
      return false;
    }
    if (ctrl && !ev.shiftKey && !ev.altKey) {
      if (ev.code === 'Insert') {
        ev.preventDefault();
        copySelection(term);
        return false;
      }
      if (ctx.copyPaste() && ev.code === 'KeyC' && term.hasSelection()) {
        ev.preventDefault();
        copySelection(term);
        return false;
      }
      if (ctx.copyPaste() && ev.code === 'KeyV') return false; // native paste event follows
    }
    if (ev.shiftKey && !ctrl && !ev.altKey && ev.code === 'Insert') return false; // native paste
    if (ev.key === 'Enter' && !ctrl && !ev.altKey && !ev.shiftKey && ctx.onEnter()) {
      ev.preventDefault();
      return false;
    }
    return true;
  });

  // Every keyboard and menu paste lands here before xterm's own listener. It listens on the
  // document: after a dialog hands focus back, Chromium can target the paste at <body> even
  // though the terminal's textarea has focus.
  const onPaste = (/** @type {ClipboardEvent} */ e) => {
    if (!host.contains(/** @type {Node|null} */ (e.target)) && !host.contains(document.activeElement)) return;
    e.preventDefault();
    e.stopPropagation();
    guardedPaste(term, e.clipboardData?.getData('text/plain') ?? '', ctx.name());
  };
  document.addEventListener('paste', onPaste, true);

  host.addEventListener('contextmenu', async (e) => {
    e.preventDefault();
    if (term.hasSelection()) {
      await copySelection(term);
      return;
    }
    let text = '';
    try {
      text = await navigator.clipboard.readText();
    } catch {
      toast("Port-Key can't read the clipboard here. Press Ctrl+V to paste.", { kind: 'error' });
      return;
    }
    await guardedPaste(term, text, ctx.name());
  });
  return () => document.removeEventListener('paste', onPaste, true);
}
