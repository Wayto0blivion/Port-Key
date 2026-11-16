/*
 * keys.js — owner: package 8a.
 *
 * The Keys tab: table (Name with Default pill, Type, Fingerprint with strip, Source,
 * Passphrase, Used by), detail pane (Windows path, public key box, Copy public key, Copy install
 * command, Install on a host…, Set as default, Hide from Port-Key), hidden keys behind "Show
 * hidden keys", the Generate key dialog and the hide-with-reassign dialog.
 *
 * Rows are selectable with the mouse or Up/Down; the dialogs live in parts/keydialogs.js.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, clear, keyTypeLabel, copyText, installCommand, plural, preserveFocus } from '../ui/dom.js';
import { renderStrip, shortFp } from '../ui/fpstrip.js';
import { toast, toastError } from '../ui/toast.js';
import { addConnection, connect } from '../parts/actions.js';
import { openGenerateKey as generateDialog, hideKey, unhideKey } from '../parts/keydialogs.js';

/** @typedef {import('../types.js').KeyInfo} KeyInfo */

/** @type {string|null} */
let selectedFp = null;
let showHidden = false;

const PROBLEM_TEXT = {
  ppk: 'PuTTY format, not supported', not_private_key: 'Not a private key', unreadable: 'Unreadable',
  unsupported_type: 'Key type not supported',
};

/** @param {HTMLElement} container @returns {void} */
export function renderKeys(container) {
  container.classList.add('page', 'keys');
  const render = () => preserveFocus(container, () => draw(container));
  render();
  store.subscribe(render);
  if (store.getState().status.keys === 'idle') store.refreshKeys().catch(() => {});
}

/** @returns {Promise<import('../types.js').KeyInfo|null>} */
export async function openGenerateKey() {
  const made = await generateDialog();
  if (made) {
    selectedFp = made.fp;
    store.setState({});
  }
  return made;
}

/** @param {KeyInfo} k */
function passphraseText(k) {
  if (!k.encrypted) return 'None';
  return k.unlocked ? 'Yes, unlocked' : 'Yes, locked';
}

/** @param {HTMLElement} container */
function draw(container) {
  const state = store.getState();
  clear(container);
  const keysDir = state.settings?.about.keys_dir ?? '';
  container.appendChild(h('div', { class: 'page__head' },
    h('div', { class: 'page__titles' },
      h('h1', { class: 'page__title' }, 'Keys'),
      h('p', { class: 'muted' }, 'Private keys stay on this PC. Port-Key only ever sends the public half to a server.')),
    h('button', { type: 'button', class: 'btn btn--primary', dataset: { fkey: 'gen' }, onClick: () => openGenerateKey() }, 'Generate key')));

  if (state.status.keys === 'loading' || state.status.keys === 'idle') {
    container.appendChild(h('p', { class: 'muted', role: 'status' }, 'Loading keys…'));
    return;
  }
  if (state.status.keys === 'error') {
    container.append(h('p', { class: 'error-text' }, `Couldn't list keys. ${state.errors.keys ?? ''}`),
      h('button', { type: 'button', class: 'btn btn--secondary', onClick: () => store.refreshKeys().catch(() => {}) }, 'Try again'));
    return;
  }
  const visible = state.keys.filter((k) => !k.hidden);
  const hidden = state.keys.filter((k) => k.hidden);
  if (!visible.find((k) => k.fp === selectedFp)) selectedFp = visible[0]?.fp ?? null;

  if (!visible.length) {
    container.appendChild(h('p', { class: 'muted' }, `No usable keys found in ${state.settings?.about.ssh_dir ?? 'your .ssh folder'}. Generate one to sign in without passwords.`));
  } else {
    container.appendChild(keyTable(visible));
    const key = visible.find((k) => k.fp === selectedFp);
    if (key) container.appendChild(detail(key, state));
  }
  if (state.keyProblems.length) {
    container.appendChild(h('div', { class: 'notice' },
      h('p', {}, `${plural(state.keyProblems.length, 'file')} in your .ssh folder could not be used:`),
      h('ul', { class: 'plain-list' }, ...state.keyProblems.map((p) =>
        h('li', {}, h('span', { class: 'mono' }, p.path), ` — ${PROBLEM_TEXT[p.reason] ?? p.reason}`)))));
  }
  container.appendChild(h('p', { class: 'muted page__note' },
    'Keys found in your .ssh folder are read-only here: Port-Key never changes or deletes them. Keys made in Port-Key are saved to ',
    h('span', { class: 'mono text' }, keysDir), ' so Windows ssh can use them too.'));
  if (hidden.length) {
    container.appendChild(h('button', {
      type: 'button', class: 'link-btn', 'aria-expanded': String(showHidden), dataset: { fkey: 'hidden-toggle' },
      onClick: () => { showHidden = !showHidden; preserveFocus(container, () => draw(container)); },
    }, showHidden ? 'Hide hidden keys' : `Show hidden keys (${hidden.length})`));
    if (showHidden) {
      container.appendChild(h('ul', { class: 'hidden-keys' }, ...hidden.map((k) => h('li', {},
        h('span', { class: 'mono' }, k.name), h('span', { class: 'muted' }, k.path),
        h('button', { type: 'button', class: 'btn btn--secondary btn--sm', 'aria-label': `Unhide ${k.name}`, onClick: () => unhideKey(k) }, 'Unhide')))));
    }
  }
}

/** @param {KeyInfo[]} keys */
function keyTable(keys) {
  const body = h('tbody');
  for (const k of keys) {
    const on = k.fp === selectedFp;
    const users = k.used_by.length;
    const tr = h('tr', { class: `is-clickable${on ? ' is-selected' : ''}`, 'aria-selected': String(on) },
      h('td', {}, h('button', {
        type: 'button', class: 'name-btn mono', 'aria-pressed': String(on), dataset: { fkey: `key:${k.fp}` },
        onClick: () => select(k.fp, true),
        onKeydown: (/** @type {KeyboardEvent} */ e) => {
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
          e.preventDefault();
          const i = keys.indexOf(k) + (e.key === 'ArrowDown' ? 1 : -1);
          if (keys[i]) select(keys[i].fp, true);
        },
      }, k.name), k.is_default ? h('span', { class: 'pill pill--brass' }, 'Default') : null,
      k.label ? h('span', { class: 'muted' }, ` ${k.label}`) : null),
      h('td', {}, keyTypeLabel(k)),
      h('td', {}, h('span', { class: 'fp-cell' }, renderStrip(k.fp), h('span', { class: 'mono small' }, shortFp(k.fp)))),
      h('td', { class: k.source === 'generated' ? 'brass' : '' }, k.source === 'generated' ? 'Made in Port-Key' : 'Found in .ssh'),
      h('td', { class: 'muted' }, passphraseText(k)),
      h('td', {}, users ? plural(users, 'connection') : h('span', { class: 'muted' }, 'Not used')));
    tr.addEventListener('click', (e) => {
      if (!(/** @type {HTMLElement} */ (e.target).closest('button'))) select(k.fp, false);
    });
    body.appendChild(tr);
  }
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table table--keys' },
    h('thead', {}, h('tr', {}, ...['Name', 'Type', 'Fingerprint', 'Source', 'Passphrase', 'Used by'].map((t) => h('th', { scope: 'col' }, t)))),
    body));
}

/** @param {string} fp @param {boolean} focus */
function select(fp, focus) {
  selectedFp = fp;
  store.setState({});
  if (focus) queueMicrotask(() => /** @type {HTMLElement|null} */ (document.querySelector(`[data-fkey="key:${fp}"]`))?.focus());
}

/** @param {KeyInfo} k @param {import('../store.js').State} state */
function detail(k, state) {
  const pubId = `pub-${k.id}`;
  const area = /** @type {HTMLTextAreaElement} */ (h('textarea', { id: pubId, class: 'textarea mono pubkey', readonly: true, rows: '2', spellcheck: 'false' }));
  area.value = k.public_key;
  const copy = async (/** @type {string} */ text, /** @type {string} */ done) => {
    const ok = await copyText(text);
    toast(ok ? done : "Couldn't copy to the clipboard.", { kind: ok ? 'info' : 'error' });
  };
  const isDefault = k.fp === state.defaultFp || k.is_default;
  return h('section', { class: 'panel key-detail', 'aria-label': `${k.name} details` },
    h('div', { class: 'key-detail__head' }, h('h2', { class: 'key-detail__name mono' }, k.name), h('span', { class: 'mono muted small' }, k.path)),
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: pubId }, 'Public key'), area),
    h('div', { class: 'btn-row' },
      h('button', { type: 'button', class: 'btn btn--secondary btn--strong', dataset: { fkey: 'copy-pub' }, onClick: () => copy(k.public_key, 'Public key copied') }, 'Copy public key'),
      h('button', { type: 'button', class: 'btn btn--secondary', dataset: { fkey: 'copy-cmd' }, onClick: () => copy(installCommand(k.public_key), 'Install command copied') }, 'Copy install command'),
      h('button', { type: 'button', class: 'btn btn--secondary', dataset: { fkey: 'install' }, onClick: () => addConnection({ key_fp: k.fp }) }, 'Install on a host…'),
      h('button', {
        type: 'button', class: 'btn btn--secondary', disabled: isDefault, dataset: { fkey: 'default' },
        onClick: async () => {
          try {
            await api.updateSettings({ default_key_fp: k.fp });
            await Promise.all([store.refreshKeys(), store.refreshSettings()]);
            toast(`${k.name} is now the default key.`);
          } catch (err) {
            toastError(err);
          }
        },
      }, isDefault ? 'Default key' : 'Set as default'),
      h('button', { type: 'button', class: 'btn btn--ghost', dataset: { fkey: 'hide' }, onClick: () => hideKey(k) }, 'Hide from Port-Key')),
    h('div', { class: 'used-by' }, h('span', { class: 'muted' }, 'Used by'),
      ...(k.used_by.length
        ? k.used_by.map((u) => h('button', { type: 'button', class: 'link-btn', title: `Connect to ${u.name}`, onClick: () => connect(u.id) }, u.name))
        : [h('span', { class: 'muted' }, 'no connections yet')])));
}
