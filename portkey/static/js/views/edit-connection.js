/*
 * edit-connection.js — owner: package 8a.
 *
 * The Edit connection dialog: Host, Port, User, Name, Folder picker (tree select with
 * "New folder…"), Tags token field with autocomplete, Colour label, Sign in with, Jump via
 * (excluding this connection and loops), Notes, Pinned; Save / Cancel. Also used for Duplicate.
 *
 * openEditConnection(connection) PATCHes only the fields that changed. A connection with an
 * empty id (see newConnectionDraft) is created with POST instead: that is the "Add connection
 * with a password" path on first run, which does not need the key install wizard.
 * Server errors land on the field they concern (name_taken, jump_*) or above the form.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h } from '../ui/dom.js';
import { openModal } from '../ui/modal.js';
import { createFolderPicker, pickerValue } from '../ui/folderpicker.js';
import { createTagField, commitTagField, TAG_HELP } from '../ui/tagfield.js';
import { field, setFieldError, colorSwatches } from '../parts/fields.js';
import { toast } from '../ui/toast.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../types.js').ConnectionInput} ConnectionInput */
/** @typedef {import('../types.js').Color} Color */

/** A blank connection for create mode. @param {Partial<Connection>} [partial] @returns {Connection} */
export function newConnectionDraft(partial = {}) {
  const settings = store.getState().settings;
  return {
    id: '', name: '', host: '', port: 22, username: settings?.default_user ?? '', auth_method: 'key',
    key_fp: null, jump_id: null, group_id: null, tags: [], color: 'grey', pinned: false, notes: '',
    sort_order: 0, created_at: '', updated_at: '', last_used_at: null, use_count: 0, ...partial,
  };
}

/** Ids that may not be this connection's jump host: itself and anything that reaches it. @param {Connection} self */
function loopIds(self) {
  const { connections } = store.getState();
  const byId = new Map(connections.map((c) => [c.id, c]));
  const out = new Set(self.id ? [self.id] : []);
  for (const c of connections) {
    let hop = c;
    for (let i = 0; i < 8 && hop; i += 1) {
      if (self.id && hop.jump_id === self.id) {
        out.add(c.id);
        break;
      }
      hop = hop.jump_id ? /** @type {Connection} */ (byId.get(hop.jump_id)) : /** @type {any} */ (null);
    }
  }
  return out;
}

/** @param {Connection} connection @returns {Promise<Connection|null>} the saved connection, or null if cancelled */
export async function openEditConnection(connection) {
  const state = store.getState();
  const creating = !connection.id;
  /** @type {Color} */
  let color = connection.color;
  let groupId = connection.group_id;
  let tags = [...connection.tags];

  const input = (/** @type {string} */ id, /** @type {string} */ value, /** @type {Record<string, unknown>} */ extra = {}) =>
    /** @type {HTMLInputElement} */ (h('input', { id, class: 'input', value, autocomplete: 'off', spellcheck: 'false', ...extra }));
  const host = input('ec-host', connection.host, { class: 'input mono' });
  const port = input('ec-port', String(connection.port), { class: 'input mono', inputmode: 'numeric' });
  const user = input('ec-user', connection.username, { class: 'input mono' });
  const name = input('ec-name', connection.name, { maxlength: '64' });
  const folder = createFolderPicker({ groups: state.groups, value: groupId, onChange: (g) => { groupId = g; }, id: 'ec-folder' });
  const tagField = createTagField({
    value: tags, suggestions: state.tags.map((t) => t.name), onChange: (t) => { tags = t; }, id: 'ec-tags', describedBy: 'ec-tags-help',
  });
  const swatches = colorSwatches({ name: 'ec-color', value: color, labelId: 'ec-color-label', onChange: (c) => { color = c; } });

  const auth = /** @type {HTMLSelectElement} */ (h('select', { id: 'ec-auth', class: 'select' }));
  const def = store.defaultKey();
  auth.appendChild(h('option', { value: 'key:' }, def ? `Default key (${def.name})` : 'Default key'));
  for (const k of state.keys) {
    if (k.hidden && k.fp !== connection.key_fp) continue;
    auth.appendChild(h('option', { value: `key:${k.fp}` }, k.hidden ? `${k.name} (hidden)` : k.name));
  }
  auth.appendChild(h('option', { value: 'password' }, 'Password only'));
  auth.value = connection.auth_method === 'password' ? 'password' : `key:${connection.key_fp ?? ''}`;
  if (!auth.value) auth.value = 'key:';

  const jump = /** @type {HTMLSelectElement} */ (h('select', { id: 'ec-jump', class: 'select' },
    h('option', { value: '' }, 'None, connect directly')));
  const blocked = loopIds(connection);
  for (const c of [...state.connections].sort((a, b) => a.name.localeCompare(b.name))) {
    if (!blocked.has(c.id)) jump.appendChild(h('option', { value: c.id }, c.name));
  }
  jump.value = connection.jump_id ?? '';

  const notes = /** @type {HTMLTextAreaElement} */ (h('textarea', { id: 'ec-notes', class: 'textarea', rows: '3', maxlength: '2000' }));
  notes.value = connection.notes;
  const pinned = /** @type {HTMLInputElement} */ (h('input', { id: 'ec-pinned', type: 'checkbox', checked: connection.pinned }));

  const body = h('div', { class: 'form form--edit' },
    h('div', { class: 'form__row' },
      field({ id: 'ec-host', label: 'Host', control: host, className: 'field--grow', help: 'Name or IP address, as reachable from this PC.' }),
      field({ id: 'ec-port', label: 'Port', control: port, className: 'field--port' })),
    h('div', { class: 'form__row' },
      field({ id: 'ec-user', label: 'User', control: user, className: 'field--grow' }),
      field({ id: 'ec-name', label: 'Name', control: name, className: 'field--grow' })),
    h('div', { class: 'form__row' },
      field({ id: 'ec-folder', label: 'Folder', control: folder, className: 'field--grow' }),
      field({
        id: 'ec-color', label: 'Colour label', labelTag: 'span', control: swatches, className: 'field--grow',
        help: 'The colour frames the terminal, so you can tell at a glance which host you are typing into.',
      })),
    field({ id: 'ec-tags', label: 'Tags', control: tagField, help: TAG_HELP }),
    h('div', { class: 'form__row' },
      field({ id: 'ec-auth', label: 'Sign in with', control: auth, className: 'field--grow' }),
      field({ id: 'ec-jump', label: 'Jump via', control: jump, className: 'field--grow',
        help: 'Port-Key connects to that host first and reaches this one through it.' })),
    field({ id: 'ec-notes', label: 'Notes', control: notes }),
    h('label', { class: 'check' }, pinned, h('span', {}, 'Pinned', h('span', { class: 'field__help check__help' }, 'Pinned connections stay at the top of the sidebar.'))));

  const fields = { name, host, port, username: user, notes, jump_id: jump, group_id: folder, key_fp: auth, auth_method: auth, tags: tagField };
  const clearErrors = () => {
    for (const el of [host, port, user, name, notes, jump, auth, folder, tagField]) setFieldError(el, null);
  };

  /** @returns {ConnectionInput|null} */
  const collect = () => {
    clearErrors();
    let ok = commitTagField(tagField);
    const err = (/** @type {HTMLElement} */ el, /** @type {string} */ msg) => { setFieldError(el, msg); ok = false; };
    const hostV = host.value.trim();
    if (!hostV) err(host, 'Enter a host name or IP address.');
    else if (/[\s@/]/.test(hostV)) err(host, 'A host has no spaces, @ or /. Put the user name in User.');
    const portV = Number(port.value.trim());
    if (!Number.isInteger(portV) || portV < 1 || portV > 65535) err(port, 'Use a port from 1 to 65535.');
    const userV = user.value.trim();
    if (!userV) err(user, 'Enter the user name to sign in as.');
    else if (/\s/.test(userV)) err(user, 'A user name has no spaces.');
    const nameV = name.value.trim();
    if (!nameV) err(name, 'Enter a name for this connection.');
    if (!ok) return null;
    const [method, fp] = auth.value.split(':');
    return {
      name: nameV, host: hostV, port: portV, username: userV,
      auth_method: method === 'password' ? 'password' : 'key',
      key_fp: method === 'password' ? null : fp || null,
      jump_id: jump.value || null, group_id: pickerValue(folder), tags: [...tags].sort(), color,
      pinned: pinned.checked, notes: notes.value,
    };
  };

  /** @type {Connection|null} */
  let saved = null;
  const handle = openModal({
    title: creating ? 'Add connection' : `Edit ${connection.name}`,
    body,
    className: 'modal--edit',
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: creating ? 'Save connection' : 'Save', kind: 'primary', value: 'save' }],
    initialFocus: creating ? host : name,
    onAction: async () => {
      handle.setError(null);
      const data = collect();
      if (!data) {
        /** @type {HTMLElement|null} */ (body.querySelector('[aria-invalid="true"]'))?.focus();
        return false;
      }
      try {
        if (creating) saved = await api.createConnection(data);
        else {
          /** @type {Record<string, unknown>} */
          const patch = {};
          for (const [k, v] of Object.entries(data)) {
            const before = /** @type {any} */ (connection)[k];
            if (JSON.stringify(before) !== JSON.stringify(v)) patch[k] = v;
          }
          saved = Object.keys(patch).length ? await api.updateConnection(connection.id, patch) : connection;
        }
      } catch (e) {
        showServerError(e, handle, fields);
        return false;
      }
      await store.refreshConnections().catch(() => {});
      toast(creating ? `Saved ${saved.name}.` : `Saved changes to ${saved.name}.`);
      return true;
    },
  });
  await handle.result;
  return saved;
}

/**
 * @param {unknown} e @param {{setError: (m: string|null) => void}} handle
 * @param {Record<string, HTMLElement>} fields
 */
function showServerError(e, handle, fields) {
  if (!(e instanceof api.ApiError)) {
    handle.setError(e instanceof Error ? e.message : String(e));
    return;
  }
  if (e.code === 'unauthorized') {
    handle.setError('Your sign-in expired. Sign in again, then select Save.');
  } else if (e.code === 'name_taken') {
    setFieldError(fields.name, e.message);
    fields.name.focus();
  } else if (e.code === 'jump_too_deep') {
    setFieldError(fields.jump_id, 'Jump chains can be at most 3 hosts long.');
  } else if (e.code === 'jump_cycle' || e.code === 'jump_unknown') {
    setFieldError(fields.jump_id, e.message);
  } else if (e.code === 'validation' && Array.isArray(e.details.fields)) {
    let placed = false;
    for (const f of /** @type {{loc: (string|number)[], msg: string}[]} */ (e.details.fields)) {
      const key = String(f.loc[f.loc.length - 1] ?? '');
      const el = fields[key] ?? (typeof f.loc[f.loc.length - 2] === 'string' ? fields[String(f.loc[f.loc.length - 2])] : undefined);
      if (el) {
        setFieldError(el, f.msg);
        placed = true;
      }
    }
    if (!placed) handle.setError(e.message);
  } else {
    handle.setError(e.message);
  }
}
