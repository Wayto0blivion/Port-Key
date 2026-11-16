/*
 * login.js — owner: package 8a.
 *
 * "Set a password for Port-Key" (first visit) and "Sign in to Port-Key" screens,
 * including wrong-password and rate-limit copy from docs/ux-spec.md.
 *
 * The first visit also asks for the one-time setup code that Port-Key printed to its container
 * log when it started (docs/design/SetPassword.dc.html). Wrong-code, restart and rate-limit
 * errors show inline, in the form.
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
/** The compose.yaml service name; tests/unit/security/test_setup_code_log.py checks it. */
const LOGS_COMMAND = 'docker compose logs portkey';
const RESTART_COMMAND = 'docker compose restart portkey';
const WRONG_CODE = `That setup code is not correct. Use the newest code in the log (${LOGS_COMMAND}).`;
const NEEDS_RESTART = `Port-Key has no setup code right now: its password was cleared while it was running. Restart it on this PC (${RESTART_COMMAND}), then use the code that ${LOGS_COMMAND} shows.`;

/**
 * @param {HTMLElement} root
 * @param {{setup: boolean, setup_code?: 'required'|'restart'|null}} status
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
    const code = /** @type {HTMLInputElement} */ (h('input', {
      id: 'login-code', class: 'input input--lg input--code', type: 'text', placeholder: 'XXXX-XXXX-XXXX',
      autocomplete: 'off', autocapitalize: 'characters', spellcheck: 'false', maxlength: '32',
    }));
    const pass = /** @type {HTMLInputElement} */ (h('input', {
      id: 'login-pass', class: 'input input--lg', type: 'password', autocomplete: setupMode ? 'new-password' : 'current-password',
    }));
    const confirm = /** @type {HTMLInputElement} */ (h('input', { id: 'login-confirm', class: 'input input--lg', type: 'password', autocomplete: 'new-password' }));
    const submit = /** @type {HTMLButtonElement} */ (h('button', { type: 'submit', class: 'btn btn--primary btn--lg btn--block' },
      setupMode ? 'Set password' : 'Sign in'));
    const message = h('p', { class: 'login__error', role: 'alert', hidden: !notice }, notice ?? '');
    const codeHelp = h('span', {},
      'Port-Key printed this code when it started. To see it, run ', h('code', {}, LOGS_COMMAND), ' on this PC.');
    const form = h('form', { class: 'form login__form', novalidate: true },
      h('span', { class: 'wordmark wordmark--lg' }, 'Port', icon('key', { size: 28, strokeWidth: 2.2, className: 'wordmark__key' }), 'Key'),
      h('h1', { id: 'login-title', class: 'login__title' }, setupMode ? 'Set a password for Port-Key' : 'Sign in to Port-Key'),
      setupMode
        ? h('p', {}, 'Port-Key can open shells on your servers, so it asks for a password even though it only listens on this PC.')
        : opts.expired ? h('p', {}, 'Your sign-in has ended. Sign in again to carry on; what you were doing is still here.') : null,
      setupMode ? field({ id: 'login-code', label: 'Setup code', control: code, help: codeHelp }) : null,
      field({ id: 'login-pass', label: 'Password', control: pass, help: setupMode ? `At least ${MIN_PASSWORD} characters.` : undefined }),
      setupMode ? field({ id: 'login-confirm', label: 'Confirm password', control: confirm }) : null,
      message,
      submit,
      h('p', { class: 'login__foot' }, setupMode
        ? 'This browser stays signed in for 30 days. The password is separate from any server password and is stored only as a hash on this PC.'
        : 'A sign-in lasts 30 days and renews itself while Port-Key is in use.'));
    card.appendChild(form);
    const first = setupMode ? code : pass;

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
          first.focus();
        } else say(`Too many attempts. Try again in ${left} seconds.`);
      }, 1000);
    };

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      say(null);
      setFieldError(code, null);
      setFieldError(pass, null);
      setFieldError(confirm, null);
      if (setupMode && !code.value.trim()) {
        setFieldError(code, `Enter the setup code. ${LOGS_COMMAND} shows it.`);
        code.focus();
        return;
      }
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
        if (setupMode) await api.setupPassword(pass.value, code.value);
        else await api.login(pass.value);
        pass.value = '';
        confirm.value = '';
        code.value = '';
        if (countdown) clearInterval(countdown);
        dialog.close();
        dialog.remove();
        done();
        return;
      } catch (err) {
        submit.disabled = false;
        if (err instanceof api.ApiError && err.code === 'bad_setup_code') {
          // Only the code was wrong: keep the chosen password, fix the code.
          setFieldError(code, WRONG_CODE);
          code.focus();
          code.select();
          return;
        }
        pass.value = '';
        confirm.value = '';
        if (!(err instanceof api.ApiError)) {
          say(err instanceof Error ? err.message : String(err));
        } else if (err.code === 'bad_password') {
          say('That password is not correct. Try again.');
        } else if (err.code === 'rate_limited') {
          lockFor(Number(err.details.retry_after ?? 30));
        } else if (err.code === 'setup_needs_restart') {
          say(NEEDS_RESTART);
        } else if (err.code === 'not_setup') {
          // The password was cleared while this page was open: Port-Key now holds no setup
          // code until it restarts, which status says.
          setupMode = true;
          const now = await api.authStatus().catch(() => null);
          draw(now?.setup_code === 'restart' ? NEEDS_RESTART : 'No password is set yet. Choose one now.');
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
        first.focus();
      }
    });
    first.focus();
  };
  draw(setupMode && status.setup_code === 'restart' ? NEEDS_RESTART : null);
  return finished;
}
