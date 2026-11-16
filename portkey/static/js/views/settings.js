/*
 * settings.js — owner: package 8a.
 *
 * The Settings tab: font size, default user, default key, Windows copy/paste, leave
 * warning, change password, trusted host keys with typed-name Forget, Diagnostics link, About.
 *
 * Each preference saves on change (PATCH /api/settings with that one field) and says
 * "Saved" next to it. The page is built once per settings load so typing is never disturbed:
 * when the key list arrives or changes later, only the Default key options are refreshed (a
 * full redraw wiped half-typed passwords and the "Saved" note when keys loaded after the tab
 * opened). The host-key list re-renders on its own.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, clear, formatDate, sshKeyTypeLabel } from '../ui/dom.js';
import { renderStrip, shortFp } from '../ui/fpstrip.js';
import { openModal } from '../ui/modal.js';
import { toast, toastError } from '../ui/toast.js';
import { field, setFieldError } from '../parts/fields.js';

/** @typedef {import('../types.js').Settings} Settings */
/** @typedef {import('../types.js').HostKey} HostKey */

/** Called by the Diagnostics link; set by the shell. @type {() => void} */
let openDiagnostics = () => {};
/** @param {() => void} fn */
export function setDiagnosticsOpener(fn) { openDiagnostics = fn; }

/** @param {HTMLElement} container @returns {void} */
export function renderSettings(container) {
  container.classList.add('page', 'settings');
  let built = '';
  let keysBuilt = '';
  /** @type {() => void} */
  let syncKeys = () => {};
  const render = () => {
    const state = store.getState();
    const sig = `${state.status.settings}|${state.settings ? 'loaded' : ''}`;
    const keysSig = `${state.status.keys}|${state.keys.map((k) => `${k.fp}${k.hidden}`).join()}`;
    if (sig !== built) {
      built = sig;
      keysBuilt = keysSig;
      syncKeys = draw(container);
    } else if (keysSig !== keysBuilt) {
      keysBuilt = keysSig;
      syncKeys();
    }
  };
  render();
  store.subscribe(render);
  if (store.getState().status.settings === 'idle') store.refreshSettings().catch(() => {});
}

/** @param {HTMLElement} container @returns {() => void} refreshes the Default key options */
function draw(container) {
  const state = store.getState();
  clear(container);
  container.appendChild(h('div', { class: 'page__head' }, h('h1', { class: 'page__title' }, 'Settings')));
  if (!state.settings) {
    if (state.status.settings === 'error') {
      container.append(h('p', { class: 'error-text' }, `Couldn't load settings. ${state.errors.settings ?? ''}`),
        h('button', { type: 'button', class: 'btn btn--secondary', onClick: () => store.refreshSettings().catch(() => {}) }, 'Try again'));
    } else container.appendChild(h('p', { class: 'muted', role: 'status' }, 'Loading settings…'));
    return () => {};
  }
  const s = state.settings;
  const prefs = preferences(s);
  container.append(prefs.section, passwordSection(), hostKeysSection(), about(s));
  return prefs.syncKeys;
}

/**
 * Save one setting and report next to the control.
 * @param {HTMLElement} control @param {HTMLElement} status @param {Partial<Omit<Settings, 'about'>>} patch
 */
async function save(control, status, patch) {
  status.textContent = 'Saving…';
  setFieldError(control, null);
  try {
    const next = await api.updateSettings(patch);
    store.setState({ settings: next });
    status.textContent = 'Saved';
    if ('default_key_fp' in patch) store.refreshKeys().catch(() => {});
  } catch (err) {
    status.textContent = '';
    if (!(err instanceof api.ApiError && err.code === 'unauthorized')) {
      setFieldError(control, err instanceof Error ? err.message : String(err));
    }
  }
}

/** @param {Settings} s @returns {{section: HTMLElement, syncKeys: () => void}} */
function preferences(s) {
  const status = () => h('span', { class: 'save-status', 'aria-live': 'polite' });
  const fontStatus = status();
  const font = /** @type {HTMLInputElement} */ (h('input', { id: 'st-font', class: 'input input--short', type: 'number', min: '8', max: '32', value: String(s.font_size) }));
  font.addEventListener('change', () => {
    const n = Number(font.value);
    if (!Number.isInteger(n) || n < 8 || n > 32) {
      setFieldError(font, 'Use a size from 8 to 32.');
      return;
    }
    save(font, fontStatus, { font_size: n });
  });
  const userStatus = status();
  const user = /** @type {HTMLInputElement} */ (h('input', { id: 'st-user', class: 'input mono', value: s.default_user, autocomplete: 'off', spellcheck: 'false' }));
  user.addEventListener('change', () => {
    if (/\s/.test(user.value.trim())) {
      setFieldError(user, 'A user name has no spaces.');
      return;
    }
    save(user, userStatus, { default_user: user.value.trim() });
  });
  const keyStatus = status();
  const key = /** @type {HTMLSelectElement} */ (h('select', { id: 'st-key', class: 'select' }));
  // The stored setting decides the selection, so options that arrive late still show it.
  const syncKeys = () => {
    const state = store.getState();
    key.replaceChildren(h('option', { value: '' }, 'Automatic: id_ed25519, id_ecdsa or id_rsa'),
      ...state.keys.filter((k) => !k.hidden).map((k) => h('option', { value: k.fp }, k.name)));
    key.value = state.settings?.default_key_fp ?? s.default_key_fp ?? '';
  };
  syncKeys();
  key.addEventListener('change', () => save(key, keyStatus, { default_key_fp: key.value || null }));

  const toggle = (/** @type {string} */ id, /** @type {string} */ label, /** @type {boolean} */ value, /** @type {'windows_copy_paste'|'leave_warning'} */ prop) => {
    const st = status();
    const box = /** @type {HTMLInputElement} */ (h('input', { id, type: 'checkbox', checked: value }));
    box.addEventListener('change', () => save(box, st, { [prop]: box.checked }));
    return h('div', { class: 'field' }, h('label', { class: 'check' }, box, h('span', {}, label)), st,
      h('span', { class: 'field__error', hidden: true }));
  };
  const section = h('section', { class: 'panel settings__section', 'aria-labelledby': 'st-prefs' },
    h('h2', { id: 'st-prefs', class: 'section-title' }, 'Preferences'),
    h('div', { class: 'form' },
      h('div', { class: 'form__row' },
        field({ id: 'st-font', label: 'Terminal font size', control: font, className: 'field--short' }), fontStatus),
      h('div', { class: 'form__row' },
        field({ id: 'st-user', label: 'Default user', control: user, className: 'field--grow', help: 'Filled in when you add a connection.' }), userStatus),
      h('div', { class: 'form__row' },
        field({ id: 'st-key', label: 'Default key', control: key, className: 'field--grow' }), keyStatus),
      toggle('st-copy', 'Ctrl+C copies and Ctrl+V pastes, like Windows Terminal', s.windows_copy_paste, 'windows_copy_paste'),
      toggle('st-leave', 'Warn before leaving with connected sessions', s.leave_warning, 'leave_warning')));
  return { section, syncKeys };
}

function passwordSection() {
  const mk = (/** @type {string} */ id, /** @type {string} */ ac) =>
    /** @type {HTMLInputElement} */ (h('input', { id, class: 'input', type: 'password', autocomplete: ac }));
  const old = mk('st-old', 'current-password');
  const next = mk('st-new', 'new-password');
  const confirm = mk('st-confirm', 'new-password');
  const button = /** @type {HTMLButtonElement} */ (h('button', { type: 'submit', class: 'btn btn--primary' }, 'Change password'));
  const form = h('form', { class: 'form', novalidate: true },
    field({ id: 'st-old', label: 'Current password', control: old }),
    h('div', { class: 'form__row' },
      field({ id: 'st-new', label: 'New password', control: next, className: 'field--grow', help: 'At least 8 characters.' }),
      field({ id: 'st-confirm', label: 'Confirm', control: confirm, className: 'field--grow' })),
    h('div', { class: 'btn-row' }, button,
      h('button', {
        type: 'button', class: 'btn btn--ghost',
        onClick: async () => {
          try {
            await api.logout();
          } finally {
            window.dispatchEvent(new CustomEvent('portkey:signed-out'));
          }
        },
      }, 'Sign out')));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    for (const el of [old, next, confirm]) setFieldError(el, null);
    if (!old.value) return fail(old, 'Enter your current password.');
    if (next.value.length < 8) return fail(next, 'Use at least 8 characters.');
    if (next.value !== confirm.value) return fail(confirm, "The passwords don't match.");
    button.disabled = true;
    try {
      await api.changePassword(old.value, next.value);
      for (const el of [old, next, confirm]) el.value = '';
      toast('Password changed. Other browsers are signed out.');
    } catch (err) {
      if (err instanceof api.ApiError && err.code === 'bad_password') fail(old, 'That password is not correct. Try again.');
      else if (err instanceof api.ApiError && err.code === 'rate_limited') fail(old, err.message);
      else if (!(err instanceof api.ApiError && err.code === 'unauthorized')) toastError(err);
    } finally {
      button.disabled = false;
    }
  });
  const fail = (/** @type {HTMLInputElement} */ el, /** @type {string} */ msg) => {
    setFieldError(el, msg);
    el.value = '';
    el.focus();
  };
  return h('section', { class: 'panel settings__section', 'aria-labelledby': 'st-pass' },
    h('h2', { id: 'st-pass', class: 'section-title' }, 'Change password'),
    h('p', { class: 'muted' }, 'Changing the password signs out every other browser.'), form);
}

function hostKeysSection() {
  const list = h('div', { class: 'hostkeys' }, h('p', { class: 'muted', role: 'status' }, 'Loading trusted host keys…'));
  const load = async () => {
    try {
      drawHostKeys(list, await api.listHostKeys(), load);
    } catch (err) {
      clear(list);
      list.append(h('p', { class: 'error-text' }, `Couldn't load host keys. ${err instanceof Error ? err.message : ''}`),
        h('button', { type: 'button', class: 'btn btn--secondary btn--sm', onClick: load }, 'Try again'));
    }
  };
  load();
  return h('section', { class: 'panel settings__section', 'aria-labelledby': 'st-hostkeys' },
    h('h2', { id: 'st-hostkeys', class: 'section-title' }, 'Trusted host keys'),
    h('p', { class: 'muted' }, "Host keys you accepted in Port-Key. Keys in your own known_hosts are trusted too and aren't listed here."),
    list);
}

/** @param {HTMLElement} list @param {HostKey[]} keys @param {() => Promise<void>} reload */
function drawHostKeys(list, keys, reload) {
  clear(list);
  if (!keys.length) {
    list.appendChild(h('p', { class: 'muted' }, 'No host keys trusted in Port-Key yet.'));
    return;
  }
  const body = h('tbody');
  for (const k of keys) {
    const host = k.port === 22 ? k.host : `${k.host}:${k.port}`;
    body.appendChild(h('tr', {},
      h('td', { class: 'mono' }, host),
      h('td', {}, sshKeyTypeLabel(k.key_type)),
      h('td', {}, h('span', { class: 'fp-cell' }, renderStrip(k.fp_sha256), h('span', { class: 'mono small' }, shortFp(k.fp_sha256)))),
      h('td', { class: 'muted' }, formatDate(k.first_seen_at)),
      h('td', {}, h('button', { type: 'button', class: 'btn btn--secondary btn--sm', 'aria-label': `Forget host key for ${host}`, onClick: () => forget(k, reload) }, 'Forget'))));
  }
  list.appendChild(h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', {}, h('tr', {}, ...['Host', 'Type', 'Fingerprint', 'Trusted since', ''].map((t) => h('th', { scope: 'col' }, t)))), body)));
}

/** @param {HostKey} k @param {() => Promise<void>} reload */
async function forget(k, reload) {
  const input = /** @type {HTMLInputElement} */ (h('input', { id: 'forget-host', class: 'input mono', autocomplete: 'off', spellcheck: 'false' }));
  const handle = openModal({
    title: `Forget the host key for ${k.host}?`,
    danger: true,
    width: 460,
    body: h('div', { class: 'form' },
      h('p', { class: 'modal__text' }, `Port-Key will ask you to check this host's identity the next time you connect. This can't be undone.`),
      h('div', { class: 'fp-box' }, renderStrip(k.fp_sha256), h('span', { class: 'mono' }, `${sshKeyTypeLabel(k.key_type)} ${shortFp(k.fp_sha256)}`)),
      field({ id: 'forget-host', label: `Type ${k.host} to confirm`, control: input })),
    buttons: [{ label: 'Cancel', kind: 'secondary', value: 'cancel' }, { label: 'Forget host key', kind: 'danger', value: 'forget', disabled: true }],
    onAction: async () => {
      try {
        await api.forgetHostKey(k.id, input.value.trim());
      } catch (err) {
        handle.setError(err instanceof Error ? err.message : String(err));
        return false;
      }
      toast(`Forgot the host key for ${k.host}.`);
      reload();
      return true;
    },
  });
  const go = handle.button('forget');
  input.addEventListener('input', () => {
    if (go) go.disabled = input.value.trim().toLowerCase() !== k.host.toLowerCase();
  });
  await handle.result;
}

/** @param {Settings} s */
function about(s) {
  const row = (/** @type {string} */ label, /** @type {string} */ value) =>
    h('div', { class: 'about__row' }, h('dt', {}, label), h('dd', { class: 'mono' }, value));
  return h('section', { class: 'panel settings__section', 'aria-labelledby': 'st-about' },
    h('h2', { id: 'st-about', class: 'section-title' }, 'About'),
    h('dl', { class: 'about' },
      row('.ssh folder', s.about.ssh_dir), row('Generated keys', s.about.keys_dir),
      row('Data', s.about.data_dir), row('Version', s.about.version)),
    h('button', { type: 'button', class: 'link-btn', onClick: () => openDiagnostics() }, 'Open Diagnostics'));
}
