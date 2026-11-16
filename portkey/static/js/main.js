/*
 * main.js — owner: package 8a (frontend shell).
 *
 * Boot: ask /api/auth/status, show views/login.js when signed out (or not set up), otherwise
 * load the store, render shell.js and restore sessions (views/sessions.js, package 8b).
 * Exports: boot().
 *
 * A 401 from any later request (api.onUnauthorized) opens the sign-in dialog over the
 * running app; once signed in again the store reloads and nothing else is lost.
 */

import * as api from './api.js';
import * as store from './store.js';
import { h } from './ui/dom.js';
import { toast } from './ui/toast.js';
import { renderShell } from './shell.js';
import { renderLogin } from './views/login.js';
import { restoreSessions } from './views/sessions.js';

let shellShown = false;
/** @type {Promise<void>|null} */
let signingIn = null;

/** Show the sign-in dialog once, however many requests fail with 401 at the same time. */
function signIn(/** @type {{setup: boolean}} */ status, /** @type {boolean} */ expired) {
  if (!signingIn) {
    const host = document.getElementById('dialogs') ?? document.body;
    signingIn = renderLogin(host, status, { expired }).finally(() => { signingIn = null; });
  }
  return signingIn;
}

/** @param {HTMLElement} root */
function showShell(root) {
  if (shellShown) return;
  shellShown = true;
  root.replaceChildren();
  renderShell(root);
  restoreSessions().catch((err) => {
    console.warn('restoring sessions failed', err);
    toast("Port-Key couldn't bring back the terminal tabs from before the reload. Open them again from the sidebar.", { kind: 'error' });
  });
}

/** @param {HTMLElement} root */
function bootFailed(root) {
  root.replaceChildren(h('div', { class: 'boot-error', role: 'alert' },
    h('h1', {}, "Port-Key isn't responding"),
    h('p', {}, 'Check that its container is running, then reload this page.'),
    h('button', { type: 'button', class: 'btn btn--primary', onClick: () => window.location.reload() }, 'Reload')));
}

/** @returns {Promise<void>} */
export async function boot() {
  const root = document.getElementById('app');
  if (!root) return;
  api.onUnauthorized(() => {
    signIn({ setup: true }, shellShown).then(() => store.refresh()).catch(() => {});
  });
  window.addEventListener('portkey:signed-out', () => {
    signIn({ setup: true }, false).then(() => store.refresh()).catch(() => {});
  });

  root.replaceChildren(h('p', { class: 'boot', role: 'status' }, 'Starting Port-Key…'));
  /** @type {import('./types.js').AuthStatus} */
  let status;
  try {
    status = await api.authStatus();
  } catch {
    bootFailed(root);
    return;
  }
  if (!status.authenticated) {
    api.clearToken();
    await signIn(status, false);
  }
  showShell(root);
  await store.refresh();
}

boot();
