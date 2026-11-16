/*
 * folderpicker.js — owner: package 8a.
 *
 * Folder picker: a tree select of folders with "Ungrouped" and "New folder…" (creates
 * via the API), used by Edit connection, the bulk bar and the wizard (8b reuses it).
 *
 * createFolderPicker(opts) returns a wrapper element holding a native <select> (keyboard and
 * screen-reader friendly) whose options are full folder paths in tree order
 * ("Office LAN / Servers"). Give opts.id to tie it to a <label for>. onChange receives the
 * folder id, or null for Ungrouped. Read the current value with pickerValue(wrapper).
 *
 * Choosing "New folder…" opens an inline row (name, where to put it, Create / Cancel). Create
 * calls POST /api/groups, refreshes the store and selects the new folder. Enter in the name
 * field creates the folder without submitting the surrounding dialog.
 *
 * opts.exclude hides folders (moving a folder: itself and its descendants); opts.maxDepth
 * limits selectable depth (moving a 2-level folder: targets at depth 1 at most).
 * opts.rootLabel replaces "Ungrouped" (moving a folder: "Top level").
 */

import { h, clear } from './dom.js';
import * as api from '../api.js';
import { buildFolderIndex, MAX_DEPTH } from '../parts/folders.js';
import { refreshConnections } from '../store.js';

/** @typedef {import('../types.js').Group} Group */

const NEW = '__new__';
let seq = 0;

/**
 * @param {{groups: Group[], value: string|null, onChange: (groupId: string|null) => void, allowNew?: boolean,
 *   id?: string, exclude?: Set<string>, maxDepth?: number, rootLabel?: string}} opts
 * @returns {HTMLElement}
 */
export function createFolderPicker(opts) {
  seq += 1;
  let groups = [...opts.groups];
  let current = opts.value;
  const select = /** @type {HTMLSelectElement} */ (h('select', { id: opts.id ?? `folder-${seq}`, class: 'select' }));
  const wrap = h('div', { class: 'folderpicker' }, select);

  const fill = () => {
    clear(select);
    const index = buildFolderIndex(groups);
    select.appendChild(h('option', { value: '' }, opts.rootLabel ?? 'Ungrouped'));
    for (const { group, depth, path } of index.ordered) {
      if (opts.exclude?.has(group.id)) continue;
      if (opts.maxDepth != null && depth > opts.maxDepth) continue;
      select.appendChild(h('option', { value: group.id }, path));
    }
    if (opts.allowNew !== false) select.appendChild(h('option', { value: NEW }, 'New folder…'));
    select.value = current ?? '';
    if (select.value !== (current ?? '')) select.value = '';
    wrap.dataset.value = current ?? '';
  };
  fill();

  /** @type {HTMLElement|null} */
  let creator = null;
  const closeCreator = () => {
    creator?.remove();
    creator = null;
    select.value = current ?? '';
  };
  select.addEventListener('change', () => {
    if (select.value === NEW) {
      if (!creator) {
        creator = buildCreator(groups, current, async (group) => {
          groups = [...groups, group];
          current = group.id;
          closeCreator();
          fill();
          select.focus();
          opts.onChange(current);
          refreshConnections().catch(() => {});
        }, () => {
          closeCreator();
          select.focus();
        });
        wrap.appendChild(creator);
      }
      /** @type {HTMLInputElement|null} */ (creator.querySelector('input'))?.focus();
      return;
    }
    closeCreator();
    current = select.value || null;
    wrap.dataset.value = select.value;
    opts.onChange(current);
  });
  return wrap;
}

/** @param {HTMLElement} wrapper from createFolderPicker @returns {string|null} */
export function pickerValue(wrapper) {
  return wrapper.dataset.value || null;
}

/**
 * @param {Group[]} groups @param {string|null} near
 * @param {(group: Group) => Promise<void>|void} onCreated @param {() => void} onCancel
 */
function buildCreator(groups, near, onCreated, onCancel) {
  seq += 1;
  const nameId = `new-folder-${seq}`;
  const parentId = `new-folder-parent-${seq}`;
  const index = buildFolderIndex(groups);
  const name = /** @type {HTMLInputElement} */ (h('input', { id: nameId, class: 'input', autocomplete: 'off', maxlength: '64' }));
  const parent = /** @type {HTMLSelectElement} */ (h('select', { id: parentId, class: 'select' },
    h('option', { value: '' }, 'Top level')));
  for (const { group, depth, path } of index.ordered) {
    if (depth < MAX_DEPTH) parent.appendChild(h('option', { value: group.id }, path));
  }
  parent.value = near && index.depth(near) < MAX_DEPTH ? near : '';
  const error = h('span', { class: 'field__error', role: 'alert', hidden: true });
  const create = async () => {
    const value = name.value.trim();
    error.hidden = true;
    if (!value) {
      showError('Enter a folder name.');
      return;
    }
    try {
      const group = await api.createGroup(value, parent.value || null);
      await onCreated(group);
    } catch (err) {
      showError(err instanceof Error ? err.message : String(err));
    }
  };
  const showError = (/** @type {string} */ message) => {
    error.textContent = message;
    error.hidden = false;
    name.setAttribute('aria-invalid', 'true');
    name.focus();
  };
  name.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      create();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onCancel();
    }
  });
  return h('div', { class: 'folderpicker__new', role: 'group', 'aria-label': 'New folder' },
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: nameId }, 'New folder name'), name),
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: parentId }, 'Inside'), parent),
    h('div', { class: 'folderpicker__actions' },
      h('button', { type: 'button', class: 'btn btn--secondary btn--sm', onClick: onCancel }, 'Cancel'),
      h('button', { type: 'button', class: 'btn btn--primary btn--sm', onClick: create }, 'Create folder')),
    error);
}
