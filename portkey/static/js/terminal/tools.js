/*
 * terminal/tools.js — owner: package 8b (private module of terminal/session.js).
 *
 * Small session-bar actions: Find in scrollback (search addon; the match is selected), Copy ssh
 * command, and ending a session over HTTP when its socket is not open.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { copyText, sshCommand } from '../ui/dom.js';
import { toast } from '../ui/toast.js';

/** @typedef {import('./session.js').TerminalView} TerminalView */

/** @param {TerminalView} view @param {boolean} open */
export function setFind(view, open) {
  view.chrome.showFind(open);
  if (!open) {
    view.term.clearSelection();
    view.chrome.setFindNote('');
    view.focus();
  }
}

/** @param {TerminalView} view @param {string} text @param {boolean} back */
export function findIn(view, text, back) {
  if (!text) {
    view.chrome.setFindNote('');
    view.term.clearSelection();
    return;
  }
  const found = back ? view.search.findPrevious(text) : view.search.findNext(text, { incremental: true });
  view.chrome.setFindNote(found ? '' : 'No matches');
}

/** @param {import('../types.js').Connection} connection */
export async function copySsh(connection) {
  const ok = await copyText(sshCommand(connection, store.getState().connections));
  toast(ok ? 'ssh command copied' : "Couldn't copy to the clipboard.", { kind: ok ? 'info' : 'error' });
}

/** DELETE /api/sessions/{id}; a session that is already gone is fine. @param {string|null} id */
export async function killSession(id) {
  if (!id) return;
  try {
    await api.killSession(id);
  } catch {
    // already gone
  }
}
