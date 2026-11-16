/*
 * wizard/form.js — owner: package 8b (private module of views/wizard.js).
 *
 * Step 1 "Host details" of the key install wizard (docs/ux-spec.md, docs/design/Wizard-1-Host):
 * Host, Port, User, Name (filled from the host until edited), Folder (ui/folderpicker.js),
 * Colour label, Tags (ui/tagfield.js), Sign in with, Jump via, and "My key is already on this
 * host". collect() validates like Edit connection does and returns the values for `start`.
 */

import * as store from '../store.js';
import { h, isIpAddress } from '../ui/dom.js';
import { createFolderPicker, pickerValue } from '../ui/folderpicker.js';
import { createTagField, commitTagField, TAG_HELP } from '../ui/tagfield.js';
import { field, setFieldError, colorSwatches } from '../parts/fields.js';

/** @typedef {import('../types.js').Color} Color */
/** @typedef {import('../types.js').Connection} Connection */

/**
 * @typedef {object} WizardValues
 * @property {string} host
 * @property {number} port
 * @property {string} username
 * @property {string} name
 * @property {string|null} group_id
 * @property {string[]} tags
 * @property {Color} color
 * @property {string|null} key_fp       null = the default key
 * @property {boolean} password_only    "Password only": save without installing a key
 * @property {string|null} jump_id
 * @property {boolean} assume_installed
 * @property {boolean} pinned
 * @property {string} notes
 * @property {string|null} connection_id  reinstall on this saved connection
 */

/** "web-prod.office.lan" -> "web-prod"; an address stays as it is. @param {string} host */
export function autoName(host) {
  const h0 = host.trim();
  if (!h0 || isIpAddress(h0)) return h0.slice(0, 64);
  return h0.split('.')[0].slice(0, 64);
}

/** Ids that may not be the jump for `selfId`: itself and anything reached through it. @param {string|null} selfId */
function loopIds(selfId) {
  const out = new Set();
  if (!selfId) return out;
  out.add(selfId);
  const byId = new Map(store.getState().connections.map((c) => [c.id, c]));
  for (const c of byId.values()) {
    let hop = c;
    for (let i = 0; i < 8 && hop; i += 1) {
      if (hop.jump_id === selfId) {
        out.add(c.id);
        break;
      }
      const next = hop.jump_id ? byId.get(hop.jump_id) : undefined;
      if (!next) break;
      hop = next;
    }
  }
  return out;
}

/** @param {WizardValues} v @returns {{el: HTMLElement, collect: () => WizardValues|null, focus: () => void, isPasswordOnly: () => boolean, onModeChange: (fn: () => void) => void}} */
export function buildForm(v) {
  const state = store.getState();
  let color = v.color;
  let groupId = v.group_id;
  let tags = [...v.tags];
  let nameEdited = Boolean(v.name) && v.name !== autoName(v.host);
  /** @type {() => void} */
  let modeChanged = () => {};

  const input = (/** @type {string} */ id, /** @type {string} */ value, /** @type {Record<string, unknown>} */ extra = {}) =>
    /** @type {HTMLInputElement} */ (h('input', { id, class: 'input', value, autocomplete: 'off', spellcheck: 'false', ...extra }));
  const host = input('wz-host', v.host, { class: 'input mono' });
  const port = input('wz-port', String(v.port), { class: 'input mono', inputmode: 'numeric' });
  const user = input('wz-user', v.username, { class: 'input mono' });
  const name = input('wz-name', v.name || autoName(v.host), { maxlength: '64' });
  host.addEventListener('input', () => {
    if (!nameEdited) name.value = autoName(host.value);
  });
  name.addEventListener('input', () => { nameEdited = name.value.trim() !== ''; });

  const folder = createFolderPicker({ groups: state.groups, value: groupId, onChange: (g) => { groupId = g; }, id: 'wz-folder' });
  const swatches = colorSwatches({ name: 'wz-color', value: color, labelId: 'wz-color-label', onChange: (c) => { color = c; } });
  const tagField = createTagField({
    value: tags, suggestions: state.tags.map((t) => t.name), onChange: (t) => { tags = t; }, id: 'wz-tags', describedBy: 'wz-tags-help',
  });

  const auth = /** @type {HTMLSelectElement} */ (h('select', { id: 'wz-auth', class: 'select' }));
  const visible = state.keys.filter((k) => !k.hidden);
  const def = store.defaultKey();
  if (visible.length) auth.appendChild(h('option', { value: '' }, def ? `Default key (${def.name})` : 'Default key'));
  for (const k of visible) if (!def || k.fp !== def.fp) auth.appendChild(h('option', { value: k.fp }, k.name));
  auth.appendChild(h('option', { value: 'password' }, 'Password only'));
  auth.value = v.password_only || !visible.length ? 'password' : v.key_fp && visible.some((k) => k.fp === v.key_fp) ? v.key_fp : '';
  if (v.key_fp && def && v.key_fp === def.fp) auth.value = '';
  auth.addEventListener('change', () => modeChanged());

  const jump = /** @type {HTMLSelectElement} */ (h('select', { id: 'wz-jump', class: 'select' }, h('option', { value: '' }, 'None, connect directly')));
  const blocked = loopIds(v.connection_id);
  for (const c of [...state.connections].sort((a, b) => a.name.localeCompare(b.name))) {
    if (!blocked.has(c.id)) jump.appendChild(h('option', { value: c.id }, c.name));
  }
  jump.value = v.jump_id ?? '';

  const assume = /** @type {HTMLInputElement} */ (h('input', { id: 'wz-assume', type: 'checkbox', checked: v.assume_installed }));
  const assumeRow = h('label', { class: 'check' }, assume,
    h('span', {}, 'My key is already on this host',
      h('span', { class: 'field__help check__help' }, 'Skips the password step. Port-Key checks key login either way.')));
  const noKey = visible.length ? null : h('p', { class: 'notice wizard__notice' },
    'Port-Key has no key to install. Generate one in Keys, or save this host with password sign-in.');

  const el = h('div', { class: 'form wizard__form' },
    h('div', { class: 'form__row' },
      field({ id: 'wz-host', label: 'Host', control: host, className: 'field--grow', help: 'Name or IP address, as reachable from this PC.' }),
      field({ id: 'wz-port', label: 'Port', control: port, className: 'field--port' })),
    h('div', { class: 'form__row' },
      field({ id: 'wz-user', label: 'User', control: user, className: 'field--grow' }),
      field({ id: 'wz-name', label: 'Name', control: name, className: 'field--grow' })),
    h('div', { class: 'form__row' },
      field({ id: 'wz-folder', label: 'Folder', control: folder, className: 'field--grow' }),
      field({
        id: 'wz-color', label: 'Colour label', labelTag: 'span', control: swatches, className: 'field--grow',
        help: 'The colour frames the terminal, so you can tell at a glance which host you are typing into.',
      })),
    field({ id: 'wz-tags', label: 'Tags', control: tagField, help: TAG_HELP }),
    h('div', { class: 'form__row' },
      field({ id: 'wz-auth', label: 'Sign in with', control: auth, className: 'field--grow' }),
      field({ id: 'wz-jump', label: 'Jump via', control: jump, className: 'field--grow',
        help: 'Port-Key connects to that host first and reaches this one through it.' })),
    noKey,
    assumeRow);
  const syncMode = () => { assumeRow.hidden = auth.value === 'password'; };
  auth.addEventListener('change', syncMode);
  syncMode();

  /** @returns {WizardValues|null} */
  const collect = () => {
    for (const c of [host, port, user, name, folder, tagField, auth, jump]) setFieldError(c, null);
    let ok = commitTagField(tagField);
    const err = (/** @type {HTMLElement} */ c, /** @type {string} */ msg) => { setFieldError(c, msg); ok = false; };
    const hostV = host.value.trim();
    if (!hostV) err(host, 'Enter a host name or IP address.');
    else if (/[\s@/]/.test(hostV)) err(host, 'A host has no spaces, @ or /. Put the user name in User.');
    const portV = Number(port.value.trim());
    if (!Number.isInteger(portV) || portV < 1 || portV > 65535) err(port, 'Use a port from 1 to 65535.');
    const userV = user.value.trim();
    if (!userV) err(user, 'Enter the user name to sign in as.');
    else if (/\s/.test(userV)) err(user, 'A user name has no spaces.');
    const nameV = name.value.trim();
    const clash = store.getState().connections.find((c) => c.id !== v.connection_id && c.name.toLowerCase() === nameV.toLowerCase());
    if (!nameV) err(name, 'Enter a name for this connection.');
    else if (clash) err(name, `A connection named ${clash.name} already exists. Choose another name.`);
    if (!ok) {
      /** @type {HTMLElement|null} */ (el.querySelector('[aria-invalid="true"]'))?.focus();
      return null;
    }
    return {
      host: hostV, port: portV, username: userV, name: nameV, group_id: pickerValue(folder), tags: [...tags].sort(), color,
      key_fp: auth.value && auth.value !== 'password' ? auth.value : null, password_only: auth.value === 'password',
      jump_id: jump.value || null, assume_installed: assume.checked && auth.value !== 'password',
      pinned: v.pinned, notes: v.notes, connection_id: v.connection_id,
    };
  };
  return {
    el,
    collect,
    focus: () => host.focus(),
    isPasswordOnly: () => auth.value === 'password',
    onModeChange: (fn) => { modeChanged = fn; },
  };
}
