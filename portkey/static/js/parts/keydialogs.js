/*
 * parts/keydialogs.js — owner: package 8a (private module of views/keys.js).
 *
 * The Generate key dialog (type, live-checked file name, comment, optional passphrase with
 * confirmation) and the Hide key dialog (choose what connections that use the key switch to).
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, plural } from '../ui/dom.js';
import { openModal } from '../ui/modal.js';
import { field, setFieldError } from './fields.js';
import { toast, toastError } from '../ui/toast.js';

/** @typedef {import('../types.js').KeyInfo} KeyInfo */

const NAME_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;
const TYPES = [
  { value: 'ed25519', label: 'Ed25519', help: 'Recommended. Small, fast and accepted by every current server.', base: 'id_ed25519_portkey' },
  { value: 'rsa', label: 'RSA 4096', help: 'For old appliances that do not accept Ed25519.', base: 'id_rsa_portkey' },
];

/** @param {string} name @returns {string|null} why the name is not allowed */
export function keyNameProblem(name) {
  if (!name) return 'Enter a file name.';
  if (name.length > 64) return 'Use at most 64 characters.';
  if (!NAME_RE.test(name)) return 'Use letters, digits, dot, dash and underscore, starting with a letter, digit or underscore.';
  if (/\.(pub|ppk)$/i.test(name)) return "The name can't end in .pub or .ppk.";
  return null;
}

/** The first of base, base_2, base_3... that no listed key uses. @param {string} base */
function freeName(base) {
  const taken = new Set(store.getState().keys.map((k) => k.name));
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

/** "john@WORKPC port-key 2026-11-16": the user@PC part comes from an existing key comment. */
function defaultComment() {
  const { keys, settings } = store.getState();
  const date = new Date();
  const ymd = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const owner = keys.map((k) => k.comment.split(/\s+/)[0]).find((c) => /^[^@\s]+@[^@\s]+$/.test(c ?? ''));
  return `${owner ?? settings?.default_user ?? 'port-key'} port-key ${ymd}`.replace(/^port-key port-key/, 'port-key');
}

/** @returns {Promise<KeyInfo|null>} */
export async function openGenerateKey() {
  const keysDir = store.getState().settings?.about.keys_dir ?? '.ssh\\port-key';
  let type = 'ed25519';
  let nameTouched = false;
  const name = /** @type {HTMLInputElement} */ (h('input', { id: 'gk-name', class: 'input mono', value: freeName('id_ed25519_portkey'), autocomplete: 'off', spellcheck: 'false' }));
  const comment = /** @type {HTMLInputElement} */ (h('input', { id: 'gk-comment', class: 'input mono', value: defaultComment(), autocomplete: 'off', spellcheck: 'false', maxlength: '200' }));
  const pass = /** @type {HTMLInputElement} */ (h('input', { id: 'gk-pass', class: 'input', type: 'password', autocomplete: 'new-password' }));
  const pass2 = /** @type {HTMLInputElement} */ (h('input', { id: 'gk-pass2', class: 'input', type: 'password', autocomplete: 'new-password' }));
  const nameStatus = h('span', { class: 'field__status', 'aria-live': 'polite' });
  const radios = h('fieldset', { class: 'choice-list' }, h('legend', { class: 'field__label' }, 'Type'),
    ...TYPES.map((t) => h('label', { class: 'choice' },
      h('input', {
        type: 'radio', name: 'gk-type', value: t.value, checked: t.value === type,
        onChange: () => {
          type = t.value;
          if (!nameTouched) name.value = freeName(t.base);
          checkName();
        },
      }),
      h('span', {}, h('span', { class: 'choice__title' }, t.label), h('span', { class: 'choice__help' }, t.help)))));

  /** @type {ReturnType<typeof setTimeout>|null} */
  let timer = null;
  let checkSeq = 0;
  let nameOk = true;
  const checkName = () => {
    if (timer) clearTimeout(timer);
    const value = name.value.trim();
    const local = keyNameProblem(value);
    nameStatus.textContent = '';
    setFieldError(name, local);
    nameOk = !local;
    if (local) return;
    const mine = ++checkSeq;
    timer = setTimeout(async () => {
      try {
        const res = await api.checkKeyName(value);
        if (mine !== checkSeq) return;
        nameOk = res.available;
        if (res.reason === 'taken') setFieldError(name, `A key named ${value} already exists. Choose another name.`);
        else if (res.reason === 'invalid') setFieldError(name, keyNameProblem(value) ?? 'That file name is not allowed.');
        else nameStatus.textContent = 'Name is free.';
      } catch {
        // the server checks again on Generate
      }
    }, 250);
  };
  name.addEventListener('input', () => {
    nameTouched = true;
    checkName();
  });
  checkName();

  const body = h('div', { class: 'form' },
    radios,
    field({ id: 'gk-name', label: 'File name', control: name, help: `Saved to ${keysDir}. Port-Key never overwrites an existing file.` }),
    nameStatus,
    field({ id: 'gk-comment', label: 'Comment', control: comment }),
    h('div', { class: 'form__row' },
      field({ id: 'gk-pass', label: 'Passphrase (optional)', control: pass, className: 'field--grow' }),
      field({ id: 'gk-pass2', label: 'Confirm passphrase', control: pass2, className: 'field--grow' })),
    h('span', { class: 'field__help' }, 'A passphrase protects the key file if someone copies it. Port-Key asks for it once and can keep the key unlocked while it runs.'));

  /** @type {{key: KeyInfo|null}} */
  const out = { key: null };
  const handle = openModal({
    title: 'Generate key',
    body,
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: 'Generate key', kind: 'primary', value: 'generate' }],
    initialFocus: name,
    onAction: async () => {
      handle.setError(null);
      setFieldError(pass2, null);
      const value = name.value.trim();
      const problem = keyNameProblem(value);
      if (problem || !nameOk) {
        if (problem) setFieldError(name, problem);
        name.focus();
        return false;
      }
      if (pass.value !== pass2.value) {
        setFieldError(pass2, "The passphrases don't match.");
        pass2.focus();
        return false;
      }
      try {
        out.key = await api.generateKey({
          name: value, type: /** @type {'ed25519'|'rsa'} */ (type), bits: type === 'rsa' ? 4096 : null,
          comment: comment.value.trim(), passphrase: pass.value ? pass.value : null,
        });
      } catch (err) {
        pass.value = '';
        pass2.value = '';
        if (err instanceof api.ApiError && (err.code === 'key_name_taken' || err.code === 'key_name_invalid')) {
          setFieldError(name, err.code === 'key_name_taken' ? `A key named ${value} already exists. Choose another name.` : err.message);
          name.focus();
        } else handle.setError(err instanceof Error ? err.message : String(err));
        return false;
      }
      pass.value = '';
      pass2.value = '';
      return true;
    },
  });
  await handle.result;
  pass.value = '';
  pass2.value = '';
  if (out.key) {
    toast(`Generated ${out.key.name}.`);
    await store.refreshKeys().catch(() => {});
  }
  return out.key;
}

/** Hide a key; when connections use it, ask what they switch to. @param {KeyInfo} key */
export async function hideKey(key) {
  const users = key.used_by;
  if (!users.length) {
    try {
      await api.updateKey(key.id, { hidden: true });
      await store.refreshKeys();
      toast(`Hid ${key.name} from Port-Key.`, { undo: () => unhideKey(key) });
    } catch (err) {
      toastError(err);
    }
    return;
  }
  const others = store.getState().keys.filter((k) => !k.hidden && k.fp !== key.fp);
  const select = /** @type {HTMLSelectElement} */ (h('select', { id: 'hide-reassign', class: 'select' },
    ...others.map((k) => h('option', { value: `key:${k.fp}` }, k.name)),
    h('option', { value: 'password' }, 'Password only')));
  const names = users.map((u) => u.name).join(', ');
  const handle = openModal({
    title: `Hide ${key.name}`,
    body: h('div', { class: 'form' },
      h('p', { class: 'modal__text' }, `${key.name} is used by ${plural(users.length, 'connection')}: ${names}. Choose what they use instead:`),
      field({ id: 'hide-reassign', label: 'Sign in with', control: select }),
      h('p', { class: 'field__help' }, 'Port-Key never deletes the key file. You can unhide the key later from the Keys tab.')),
    width: 460,
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: 'Hide key', kind: 'primary', value: 'hide' }],
    onAction: async () => {
      const [method, fp] = select.value.split(/:(.*)/s);
      try {
        await api.updateKey(key.id, {
          hidden: true,
          reassign: method === 'password' ? { auth_method: 'password' } : { auth_method: 'key', key_fp: fp },
        });
        await Promise.all([store.refreshKeys(), store.refreshConnections()]);
      } catch (err) {
        handle.setError(err instanceof Error ? err.message : String(err));
        return false;
      }
      toast(`Hid ${key.name}. ${plural(users.length, 'connection')} switched.`);
      return true;
    },
  });
  await handle.result;
}

/** @param {KeyInfo} key */
export async function unhideKey(key) {
  try {
    await api.updateKey(key.id, { hidden: false });
    await store.refreshKeys();
  } catch (err) {
    toastError(err);
  }
}
