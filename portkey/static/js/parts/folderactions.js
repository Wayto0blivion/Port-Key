/*
 * parts/folderactions.js — owner: package 8a (private module).
 *
 * Folder menu actions from the sidebar: New folder, Rename, Move and Delete (with a 10-second
 * Undo that re-creates the folder and moves its former contents back). Deleting a folder never
 * deletes connections; the server moves its contents up one level.
 *
 * Exports: folderMenu(group), newFolder(parentId), renameFolder, moveFolder, deleteFolder.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h } from '../ui/dom.js';
import { promptDialog, openModal } from '../ui/modal.js';
import { createFolderPicker, pickerValue } from '../ui/folderpicker.js';
import { toast, toastError } from '../ui/toast.js';
import { MAX_DEPTH } from './folders.js';

/** @typedef {import('../types.js').Group} Group */
/** @typedef {import('./menu.js').MenuItem} MenuItem */

/** @param {unknown} err */
function report(err) {
  if (err instanceof api.ApiError && err.code === 'unauthorized') return;
  toastError(err);
}

/** @param {string|null} parentId */
export async function newFolder(parentId) {
  const where = parentId ? store.folderPath(parentId) : '';
  await promptDialog({
    title: 'New folder',
    label: 'Folder name',
    help: where ? `Inside ${where}.` : 'At the top level of the sidebar.',
    confirmLabel: 'Create folder',
    submit: async (value) => {
      if (!value.trim()) throw new Error('Enter a folder name.');
      await api.createGroup(value.trim(), parentId);
      await store.refreshConnections();
    },
  });
}

/** @param {Group} group */
export async function renameFolder(group) {
  await promptDialog({
    title: `Rename ${group.name}`,
    label: 'Folder name',
    value: group.name,
    confirmLabel: 'Rename',
    submit: async (value) => {
      if (!value.trim()) throw new Error('Enter a folder name.');
      if (value.trim() !== group.name) await api.updateGroup(group.id, { name: value.trim() });
      await store.refreshConnections();
    },
  });
}

/** @param {Group} group */
export async function moveFolder(group) {
  const index = store.folders();
  const exclude = index.descendants(group.id);
  exclude.add(group.id);
  const picker = createFolderPicker({
    groups: store.getState().groups, value: group.parent_id, onChange: () => {}, id: 'move-folder-target',
    exclude, maxDepth: MAX_DEPTH - index.height(group.id), rootLabel: 'Top level', allowNew: false,
  });
  const handle = openModal({
    title: `Move ${group.name}`,
    body: h('div', { class: 'field' },
      h('label', { class: 'field__label', for: 'move-folder-target' }, 'Move into'), picker,
      h('span', { class: 'field__help' }, 'Folders nest at most 3 levels deep.')),
    width: 460,
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: 'Move', kind: 'primary', value: 'move' }],
    onAction: async () => {
      const target = pickerValue(picker);
      if (target === group.parent_id) return true;
      try {
        await api.updateGroup(group.id, { parent_id: target });
        await store.refreshConnections();
      } catch (err) {
        handle.setError(err instanceof Error ? err.message : String(err));
        return false;
      }
      return true;
    },
  });
  await handle.result;
}

/** Delete with Undo. @param {Group} group */
export async function deleteFolder(group) {
  const { connections } = store.getState();
  const index = store.folders();
  const childFolders = index.children(group.id).map((g) => g.id);
  const directConnections = connections.filter((c) => c.group_id === group.id).map((c) => c.id);
  const parentName = group.parent_id ? store.folderPath(group.parent_id) : 'Ungrouped';
  try {
    await api.deleteGroup(group.id);
    await store.refreshConnections();
  } catch (err) {
    report(err);
    return;
  }
  const moved = childFolders.length + directConnections.length;
  const message = moved
    ? `Deleted folder ${group.name}. Its contents moved to ${parentName}.`
    : `Deleted folder ${group.name}.`;
  toast(message, {
    undo: async () => {
      try {
        const again = await api.createGroup(group.name, group.parent_id);
        for (const id of childFolders) await api.updateGroup(id, { parent_id: again.id });
        if (directConnections.length) await api.bulkUpdateConnections({ ids: directConnections, set_group_id: again.id });
        await store.refreshConnections();
        toast(`Restored folder ${group.name}.`);
      } catch (err) {
        report(err);
        store.refreshConnections().catch(() => {});
      }
    },
  });
}

/** @param {Group} group @returns {MenuItem[]} */
export function folderMenu(group) {
  const depth = store.folders().depth(group.id);
  return [
    { label: 'New folder', disabled: depth >= MAX_DEPTH, onSelect: () => { newFolder(group.id).catch(report); } },
    { label: 'Rename', onSelect: () => { renameFolder(group).catch(report); } },
    { label: 'Move', onSelect: () => { moveFolder(group).catch(report); } },
    'separator',
    { label: 'Delete', danger: true, onSelect: () => { deleteFolder(group).catch(report); } },
  ];
}
