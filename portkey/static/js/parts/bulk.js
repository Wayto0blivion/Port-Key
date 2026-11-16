/*
 * parts/bulk.js — owner: package 8a (private module of views/home.js).
 *
 * The Home bulk bar actions: Move to folder…, Add tag…, Remove tag…. Each asks in a small
 * dialog, applies to every selected connection in one POST /api/connections/bulk (all or
 * nothing on the server) and shows a 10-second Undo toast that restores each connection's
 * previous folder or tags.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, plural } from '../ui/dom.js';
import { openModal } from '../ui/modal.js';
import { createFolderPicker, pickerValue } from '../ui/folderpicker.js';
import { createTagField, commitTagField, TAG_HELP } from '../ui/tagfield.js';
import { toast, toastError } from '../ui/toast.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../types.js').BulkChange} BulkChange */

/** @param {BulkChange[]} changes */
async function applyAll(changes) {
  for (const change of changes) if (change.ids.length) await api.bulkUpdateConnections(change);
  await store.refreshConnections();
}

/** @param {BulkChange[]} undo @param {string} message */
function undoToast(undo, message) {
  toast(message, {
    undo: async () => {
      try {
        await applyAll(undo);
      } catch (err) {
        toastError(err);
        store.refreshConnections().catch(() => {});
      }
    },
  });
}

/**
 * @param {string} title @param {HTMLElement} body @param {string} action
 * @param {() => Promise<boolean|void>} apply
 */
async function ask(title, body, action, apply) {
  const handle = openModal({
    title, body, width: 460,
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: action, kind: 'primary', value: 'ok' }],
    onAction: async () => {
      handle.setError(null);
      try {
        return await apply();
      } catch (err) {
        if (err instanceof api.ApiError && err.code === 'unauthorized') {
          handle.setError('Your sign-in expired. Sign in again, then try once more.');
        } else handle.setError(err instanceof Error ? err.message : String(err));
        return false;
      }
    },
  });
  return handle.result;
}

/** @param {Connection[]} selected */
export async function bulkMove(selected) {
  const ids = selected.map((c) => c.id);
  const common = selected.every((c) => c.group_id === selected[0]?.group_id) ? selected[0]?.group_id ?? null : null;
  const picker = createFolderPicker({ groups: store.getState().groups, value: common, onChange: () => {}, id: 'bulk-folder' });
  await ask(`Move ${plural(ids.length, 'connection')}`,
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'bulk-folder' }, 'Folder'), picker),
    'Move', async () => {
      const target = pickerValue(picker);
      await applyAll([{ ids, set_group_id: target }]);
      /** @type {Map<string|null, string[]>} */
      const back = new Map();
      for (const c of selected) {
        if (!back.has(c.group_id)) back.set(c.group_id, []);
        back.get(c.group_id)?.push(c.id);
      }
      const where = target ? store.folderPath(target) : 'Ungrouped';
      undoToast([...back].map(([group, list]) => ({ ids: list, set_group_id: group })),
        `Moved ${plural(ids.length, 'connection')} to ${where}.`);
    });
}

/** @param {Connection[]} selected */
export async function bulkAddTags(selected) {
  /** @type {string[]} */
  let tags = [];
  const field = createTagField({
    value: [], suggestions: store.getState().tags.map((t) => t.name), onChange: (t) => { tags = t; }, id: 'bulk-tags',
  });
  await ask(`Add tags to ${plural(selected.length, 'connection')}`,
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'bulk-tags' }, 'Tags to add'), field,
      h('span', { class: 'field__help' }, TAG_HELP)),
    'Add tags', async () => {
      if (!commitTagField(field)) return false;
      if (!tags.length) throw new Error('Type at least one tag.');
      await applyAll([{ ids: selected.map((c) => c.id), add_tags: tags }]);
      const undo = tags.map((t) => ({ ids: selected.filter((c) => !c.tags.includes(t)).map((c) => c.id), remove_tags: [t] }));
      undoToast(undo, `Added ${tags.map((t) => `#${t}`).join(' ')} to ${plural(selected.length, 'connection')}.`);
      return true;
    });
}

/** @param {Connection[]} selected */
export async function bulkRemoveTags(selected) {
  /** @type {Map<string, number>} */
  const counts = new Map();
  for (const c of selected) for (const t of c.tags) counts.set(t, (counts.get(t) ?? 0) + 1);
  if (!counts.size) {
    toast('None of the selected connections have tags.');
    return;
  }
  const boxes = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([tag, n]) =>
    h('label', { class: 'check' },
      h('input', { type: 'checkbox', value: tag }),
      h('span', {}, h('span', { class: 'mono' }, `#${tag}`), h('span', { class: 'muted' }, ` on ${n} of ${selected.length}`))));
  const list = h('fieldset', { class: 'checklist' }, h('legend', { class: 'field__label' }, 'Tags to remove'), ...boxes);
  await ask(`Remove tags from ${plural(selected.length, 'connection')}`, list, 'Remove tags', async () => {
    const tags = /** @type {HTMLInputElement[]} */ ([...list.querySelectorAll('input:checked')]).map((i) => i.value);
    if (!tags.length) throw new Error('Choose at least one tag.');
    await applyAll([{ ids: selected.map((c) => c.id), remove_tags: tags }]);
    const undo = tags.map((t) => ({ ids: selected.filter((c) => c.tags.includes(t)).map((c) => c.id), add_tags: [t] }));
    undoToast(undo, `Removed ${tags.map((t) => `#${t}`).join(' ')} from ${plural(selected.length, 'connection')}.`);
    return true;
  });
}
