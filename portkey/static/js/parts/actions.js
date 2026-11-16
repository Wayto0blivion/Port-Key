/*
 * parts/actions.js — owner: package 8a (private module).
 *
 * Connection actions shared by the sidebar, Home, Keys and the shell, and the bridge to
 * package 8b's modules (views/sessions.js, views/wizard.js, ui/palette.js). Until 8b lands
 * those modules throw "not implemented (package 8b)"; the bridge then shows a toast instead
 * of failing silently.
 *
 * Exports: connect, addConnection, openQuickConnect, connectionMenu, deleteWithUndo,
 * duplicate, togglePin, copySsh, moveConnection, notBuilt.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, sshCommand, copyText } from '../ui/dom.js';
import { toast, toastError } from '../ui/toast.js';
import { openModal } from '../ui/modal.js';
import { createFolderPicker, pickerValue } from '../ui/folderpicker.js';
import { openSession } from '../views/sessions.js';
import { openWizard } from '../views/wizard.js';
import { openPalette } from '../ui/palette.js';
import { openEditConnection } from '../views/edit-connection.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('./menu.js').MenuItem} MenuItem */

/** @param {unknown} err @returns {boolean} true when the error was a missing 8b feature (toast shown) */
export function notBuilt(err) {
  if (err instanceof Error && /not implemented \(package 8b\)/.test(err.message)) {
    toast('This part of Port-Key is not built yet.', { kind: 'error' });
    return true;
  }
  return false;
}

/** @param {unknown} err */
function report(err) {
  if (notBuilt(err)) return;
  if (err instanceof api.ApiError && err.code === 'unauthorized') return; // the sign-in dialog shows
  toastError(err);
}

/** Open a terminal (or focus the open one). @param {string} id @param {{newSession?: boolean, forcePassword?: boolean}} [opts] */
export async function connect(id, opts = {}) {
  try {
    await openSession(id, opts);
  } catch (err) {
    report(err);
  }
}

/**
 * The Add connection wizard (package 8b). Refreshes the list when it saved something.
 * @param {Parameters<typeof openWizard>[0]} [prefill] @param {Parameters<typeof openWizard>[1]} [opts]
 */
export async function addConnection(prefill = {}, opts = {}) {
  try {
    const saved = await openWizard(prefill, opts);
    if (saved) await store.refresh();
    return saved;
  } catch (err) {
    report(err);
    return null;
  }
}

/** The quick-connect palette (package 8b). */
export function openQuickConnect() {
  try {
    openPalette();
  } catch (err) {
    report(err);
  }
}

/** @param {Connection} c @returns {import('../types.js').ConnectionInput} */
export function toInput(c) {
  return {
    name: c.name, host: c.host, port: c.port, username: c.username, auth_method: c.auth_method,
    key_fp: c.key_fp, jump_id: c.jump_id, group_id: c.group_id, tags: [...c.tags], color: c.color,
    pinned: c.pinned, notes: c.notes,
  };
}

/** Delete now, Undo re-creates it. @param {Connection} c */
export async function deleteWithUndo(c) {
  try {
    await api.deleteConnection(c.id);
  } catch (err) {
    if (err instanceof api.ApiError && err.code === 'jump_in_use') {
      openModal({
        title: `Can't delete ${c.name}`,
        body: h('p', { class: 'modal__text' }, err.message),
        width: 460,
        buttons: [{ label: 'OK', kind: 'primary', value: 'ok' }],
      });
    } else report(err);
    return;
  }
  await store.refreshConnections().catch(() => {});
  toast(`Deleted ${c.name}.`, {
    undo: async () => {
      try {
        await api.createConnection(toInput(c));
        await store.refreshConnections();
        toast(`Restored ${c.name}.`);
      } catch (err) {
        report(err);
      }
    },
  });
}

/** Duplicate, then open the copy in Edit so it can be renamed. @param {Connection} c */
export async function duplicate(c) {
  try {
    const copy = await api.duplicateConnection(c.id);
    await store.refreshConnections();
    toast(`Duplicated ${c.name} as ${copy.name}.`);
    await openEditConnection(copy);
  } catch (err) {
    report(err);
  }
}

/** @param {Connection} c */
export async function togglePin(c) {
  try {
    await api.updateConnection(c.id, { pinned: !c.pinned });
    await store.refreshConnections();
  } catch (err) {
    report(err);
  }
}

/** @param {Connection} c */
export async function copySsh(c) {
  const ok = await copyText(sshCommand(c, store.getState().connections));
  toast(ok ? 'ssh command copied' : "Couldn't copy to the clipboard.", { kind: ok ? 'info' : 'error' });
}

/**
 * Move one connection to a folder, with Undo.
 * @param {Connection} c @param {string|null} groupId
 */
export async function moveConnection(c, groupId) {
  if (c.group_id === groupId) return;
  const before = c.group_id;
  try {
    await api.updateConnection(c.id, { group_id: groupId });
    await store.refreshConnections();
  } catch (err) {
    report(err);
    return;
  }
  const where = groupId ? store.folderPath(groupId) : 'Ungrouped';
  toast(`Moved ${c.name} to ${where}.`, {
    undo: async () => {
      try {
        await api.updateConnection(c.id, { group_id: before });
        await store.refreshConnections();
      } catch (err) {
        report(err);
      }
    },
  });
}

/** Ask for a folder, then move. @param {Connection} c */
async function chooseFolder(c) {
  const picker = createFolderPicker({ groups: store.getState().groups, value: c.group_id, onChange: () => {}, id: 'move-conn-folder' });
  const { result } = openModal({
    title: `Move ${c.name}`,
    body: h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'move-conn-folder' }, 'Folder'), picker),
    width: 460,
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: 'Move', kind: 'primary', value: 'move' }],
  });
  if ((await result) === 'move') await moveConnection(c, pickerValue(picker));
}

/** Menu items for a connection row (sidebar, Home table). @param {Connection} c @returns {MenuItem[]} */
export function connectionMenu(c) {
  return [
    { label: 'Connect', onSelect: () => connect(c.id) },
    { label: 'New session', onSelect: () => connect(c.id, { newSession: true }) },
    { label: 'Edit', onSelect: () => { openEditConnection(c).catch(report); } },
    { label: 'Duplicate', onSelect: () => duplicate(c) },
    { label: c.pinned ? 'Unpin' : 'Pin', onSelect: () => togglePin(c) },
    { label: 'Move to folder…', onSelect: () => chooseFolder(c) },
    { label: 'Copy ssh command', onSelect: () => copySsh(c) },
    'separator',
    { label: 'Delete', danger: true, onSelect: () => deleteWithUndo(c) },
  ];
}
