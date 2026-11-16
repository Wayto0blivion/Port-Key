/*
 * login.js — owner: package 8a.
 *
 * "Set a password for Port-Key" (first visit) and "Sign in to Port-Key" screens,
 * including wrong-password and rate-limit copy from docs/ux-spec.md.
 *
 * The screen is a modal <dialog> that cannot be dismissed, so after a 401 it sits on top of
 * everything (open dialogs included) and the page behind it keeps its state and unsaved
 * input. renderLogin resolves once a token is stored; the caller then removes nothing: the
 * dialog closes itself.
 */

import * as api from '../api.js';
import { h, clear } from '../ui/dom.js';
import { icon } from '../parts/icons.js';
import { field, setFieldError } from '../parts/fields.js';

const MIN_PASSWORD = 8;

/**
 * @param {HTMLElement} root @param {{setup: boolean}} status
 * @param {{expired?: boolean}} [opts] expired: the session ended while Port-Key was open
 * @returns {Promise<void>} resolves once signed in
 */
export async function renderLogin(root, status, opts = {}) {
  /** @type {(value: void) => void} */
  let done = () => {};
  const finished = new Promise((r) => { done = r; });
  const card = h('div', { class: 'login__card' });
  const dialog = /** @type {HTMLDialogElement} */ (h('dialog', { class: 'login', 'aria-labelledby': 'login-title' }, card));
  dialog.addEventListener('cancel', (e) => e.preventDefault());
  root.appendChild(dialog);
  dialog.showModal();

  let setupMode = !status.setup;
  /** @type {ReturnType<typeof setInterval>|null} */
  let countdown = null;

  const draw = (/** @type {string|null} */ notice = null) => {
    clear(card);
    if (countdown) clearInterval(countdown);
    const pass = /** @type {HTMLInputElement} */ (h('input', {
      id: 'login-pass', class: 'input input--lg', type: 'password', autocomplete: setupMode ? 'new-password' : 'current-password',
    }));
    const confirm = /** @type {HTMLInputElement} */ (h('input', { id: 'login-confirm', class: 'input input--lg', type: 'password', autocomplete: 'new-password' }));
    const submit = /** @type {HTMLButtonElement} */ (h('button', { type: 'submit', class: 'btn btn--primary btn--lg btn--block' },
      setupMode ? 'Set password' : 'Sign in'));
    const message = h('p', { class: 'login__error', role: 'alert', hidden: !notice }, notice ?? '');
    const form = h('form', { class: 'form login__form', novalidate: true },
      h('span', { class: 'wordmark wordmark--lg' }, 'Port', icon('key', { size: 28, strokeWidth: 2.2, className: 'wordmark__key' }), 'Key'),
      h('h1', { id: 'login-title', class: 'login__title' }, setupMode ? 'Set a password for Port-Key' : 'Sign in to Port-Key'),
      setupMode
        ? h('p', {}, 'Port-Key can open shells on your servers, so it asks for a password even though it only listens on this PC.')
        : opts.expired ? h('p', {}, 'Your sign-in has ended. Sign in again to carry on; what you were doing is still here.') : null,
      field({ id: 'login-pass', label: 'Password', control: pass, help: setupMode ? `At least ${MIN_PASSWORD} characters.` : undefined }),
      setupMode ? field({ id: 'login-confirm', label: 'Confirm password', control: confirm }) : null,
      message,
      submit,
      h('p', { class: 'login__foot' }, setupMode
        ? 'This browser stays signed in for 30 days. The password is separate from any server password and is stored only as a hash on this PC.'
        : 'A sign-in lasts 30 days and renews itself while Port-Key is in use.'));
    card.appendChild(form);

    const say = (/** @type {string|null} */ text) => {
      message.textContent = text ?? '';
      message.hidden = !text;
    };
    const lockFor = (/** @type {number} */ seconds) => {
      let left = Math.max(1, Math.ceil(seconds));
      submit.disabled = true;
      say(`Too many attempts. Try again in ${left} seconds.`);
      countdown = setInterval(() => {
        left -= 1;
        if (left <= 0) {
          if (countdown) clearInterval(countdown);
          submit.disabled = false;
          say(null);
          pass.focus();
        } else say(`Too many attempts. Try again in ${left} seconds.`);
      }, 1000);
    };

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      say(null);
      setFieldError(pass, null);
      setFieldError(confirm, null);
      if (!pass.value) {
        setFieldError(pass, 'Enter a password.');
        pass.focus();
        return;
      }
      if (setupMode) {
        if (pass.value.length < MIN_PASSWORD) {
          setFieldError(pass, `Use at least ${MIN_PASSWORD} characters.`);
          pass.focus();
          return;
        }
        if (pass.value !== confirm.value) {
          setFieldError(confirm, "The passwords don't match.");
          confirm.value = '';
          confirm.focus();
          return;
        }
      }
      submit.disabled = true;
      try {
        if (setupMode) await api.setupPassword(pass.value);
        else await api.login(pass.value);
        pass.value = '';
        confirm.value = '';
        if (countdown) clearInterval(countdown);
        dialog.close();
        dialog.remove();
        done();
        return;
      } catch (err) {
        pass.value = '';
        confirm.value = '';
        submit.disabled = false;
        if (!(err instanceof api.ApiError)) {
          say(err instanceof Error ? err.message : String(err));
        } else if (err.code === 'bad_password') {
          say('That password is not correct. Try again.');
        } else if (err.code === 'rate_limited') {
          lockFor(Number(err.details.retry_after ?? 30));
        } else if (err.code === 'not_setup') {
          setupMode = true;
          draw('No password is set yet. Choose one now.');
          return;
        } else if (err.code === 'already_setup') {
          setupMode = false;
          draw('A password is already set. Sign in with it.');
          return;
        } else if (err.code === 'validation') {
          setFieldError(pass, `Use ${MIN_PASSWORD} to 1024 characters.`);
        } else {
          say(err.message);
        }
        pass.focus();
      }
    });
    pass.focus();
  };
  draw();
  return finished;
}
