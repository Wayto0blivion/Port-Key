/*
 * wizard/views.js — owner: package 8b (private module of views/wizard.js).
 *
 * DOM for the wizard's steps after step 1 (docs/design/Wizard-2-Identity, Wizard-3-SignIn,
 * Wizard-4-Saved, Wizard-PasswordDisabled): the step indicator, the live checklist, "Sign in
 * once" with its password and verification-code fields, the failure and needs-action panels with
 * the collapsed Details block, and the "Connection saved" summary card.
 */

import * as store from '../store.js';
import { h, clear, labelClass, formatAddress, copyText, installCommand } from '../ui/dom.js';
import { toast } from '../ui/toast.js';
import { icon } from '../parts/icons.js';
import { addr } from './hostkey.js';

/** @typedef {import('./copy.js').Line} Line */
/** @typedef {import('./form.js').WizardValues} WizardValues */
/** @typedef {import('../types.js').Connection} Connection */

const STEP_NAMES = ['Host', 'Identity', 'Sign in', 'Verify'];

/** @param {number|'final'} current @param {number|null} problem @returns {HTMLElement} */
export function stepIndicator(current, problem) {
  const ol = h('ol', { class: 'wsteps', 'aria-label': 'Steps' });
  STEP_NAMES.forEach((name, i) => {
    const n = i + 1;
    const done = current === 'final' || n < /** @type {number} */ (current);
    const on = n === current;
    const bad = on && problem === n;
    ol.appendChild(h('li', {
      class: `wsteps__item${done ? ' is-done' : ''}${on ? ' is-current' : ''}${bad ? ' is-problem' : ''}`,
      'aria-current': on ? 'step' : null,
    }, done ? h('span', { class: 'wsteps__mark', 'aria-hidden': 'true' }, '✓ ') : `${n} `, name,
    done ? h('span', { class: 'visually-hidden' }, ', done') : null));
  });
  return ol;
}

/** @param {Line[]} lines */
export function checklist(lines) {
  return h('ul', { class: 'wcheck', 'aria-live': 'polite' }, ...lines.map((l) => h('li', { class: `wcheck__line wcheck__line--${l.state}` },
    h('span', { class: 'wcheck__mark', 'aria-hidden': 'true' },
      l.state === 'done' ? icon('check', { size: 14, strokeWidth: 3 }) : l.state === 'fail' ? icon('cross', { size: 14, strokeWidth: 3 }) : l.state === 'warn' ? '!' : ''),
    h('span', { class: 'wcheck__text' }, l.text,
      l.state === 'done' ? h('span', { class: 'visually-hidden' }, ', done') : null,
      l.note ? h('span', { class: 'wcheck__note' }, l.note) : null))));
}

/** @param {string} title @param {...(Node|string|null)} rest */
export function section(title, ...rest) {
  return h('div', { class: 'wstep' }, h('h2', { class: 'wstep__title', tabindex: '-1' }, title), ...rest);
}

/** Collapsed Details block for server output. @param {string|undefined|null} text */
export function details(text) {
  if (!text || !text.trim()) return null;
  return h('details', { class: 'wdetails' }, h('summary', {}, 'Details'), h('pre', { class: 'wdetails__text mono' }, text.trim()));
}

/** @param {string} text @param {string} done */
export async function copyWithToast(text, done) {
  const ok = await copyText(text);
  toast(ok ? done : "Couldn't copy to the clipboard.", { kind: ok ? 'info' : 'error' });
}

/** The key the wizard installs, for copy and the summary. @param {WizardValues} v */
export function chosenKey(v) {
  return v.key_fp ? store.keyByFp(v.key_fp) : store.defaultKey();
}

/**
 * Step 3 "Sign in once": the password field, then server verification fields as they arrive.
 * @param {WizardValues} v @param {() => void} submit
 */
export function signinView(v, submit) {
  const key = chosenKey(v);
  const pw = /** @type {HTMLInputElement} */ (h('input', { id: 'wz-password', class: 'input input--lg', type: 'password', autocomplete: 'off', disabled: true }));
  const status = h('p', { class: 'wstep__status', role: 'status' });
  const error = h('p', { class: 'field__error wstep__error', role: 'alert', hidden: true });
  const codes = h('div', { class: 'wstep__codes' });
  const sent = h('p', { class: 'wstep__sent', hidden: true }, icon('check', { size: 14, strokeWidth: 3 }), ' Password sent');
  pw.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
    }
  });
  const el = section('Sign in once',
    h('p', { class: 'wstep__lead' }, 'Your key is not on ', h('strong', {}, v.name), ' yet. Enter the password for ',
      h('span', { class: 'mono strong' }, v.username), ' and Port-Key installs it for you.'),
    h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'wz-password' }, `Password for ${v.username}@${addr(v.host, v.port)}`), pw),
    sent, codes, error,
    h('div', { class: 'wbox' },
      h('span', { class: 'wbox__title' }, 'What happens next'),
      h('ol', { class: 'wbox__list' },
        h('li', {}, 'Port-Key signs in with this password.'),
        h('li', {}, 'It adds the public half of ', h('span', { class: 'mono brass' }, key?.name ?? 'your key'), ' to ',
          h('span', { class: 'mono' }, '~/.ssh/authorized_keys'), ' on the host.'),
        h('li', {}, 'It signs in again using only the key, to prove it works.'))),
    h('p', { class: 'muted' }, 'The password stays in memory until this wizard closes. It is never saved and never written to a log.'),
    status);
  return {
    el, pw, codes, sent,
    /** @param {string|null} text */
    setError(text) {
      error.textContent = text ?? '';
      error.hidden = !text;
    },
    /** @param {string} text */
    setStatus(text) { status.textContent = text; },
    /**
     * Show the server's verification questions; returns their inputs.
     * @param {{text: string, echo: boolean}[]} questions @param {string} instructions
     */
    showCodes(questions, instructions) {
      clear(codes);
      if (instructions.trim()) codes.appendChild(h('p', { class: 'wstep__lead' }, instructions.trim()));
      return questions.map((q, i) => {
        const input = /** @type {HTMLInputElement} */ (h('input', {
          id: `wz-code-${i}`, class: `input input--lg${q.echo ? ' mono' : ''}`, type: q.echo ? 'text' : 'password', autocomplete: 'off', spellcheck: 'false',
        }));
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            submit();
          }
        });
        codes.appendChild(h('div', { class: 'field' }, h('label', { class: 'field__label', for: input.id }, q.text.trim() || 'Answer'), input));
        return input;
      });
    },
  };
}

/** @param {Connection|null} c @param {WizardValues} v @param {'installed'|'already_installed'|'saved_password_only'} outcome */
export function summaryCard(c, v, outcome) {
  const name = c?.name ?? v.name;
  const folder = store.folderPath(c?.group_id ?? v.group_id);
  const address = c ? formatAddress(c) : `${v.username}@${addr(v.host, v.port)}`;
  const key = chosenKey(v);
  return h('div', { class: `wsummary ${labelClass(c?.color ?? v.color)}` },
    h('div', { class: 'wsummary__text' },
      h('span', { class: 'wsummary__name' }, name),
      h('span', { class: 'wsummary__addr mono' }, folder ? `${address} · ${folder}` : address)),
    outcome === 'saved_password_only'
      ? h('span', { class: 'wsummary__pw' }, 'Password')
      : h('span', { class: 'wsummary__key mono brass' }, icon('key', { size: 14 }), key?.name ?? 'your key'));
}

/**
 * The manual install command box ("Run this on backup-nas as admin").
 * @param {WizardValues} v @param {{public_key?: string, install_command?: string}} m
 */
export function commandBox(v, m) {
  const pub = m.public_key ?? chosenKey(v)?.public_key ?? '';
  const cmd = m.install_command ?? (pub ? installCommand(pub) : '');
  const area = /** @type {HTMLTextAreaElement} */ (h('textarea', { id: 'wz-cmd', class: 'textarea mono wcmd', readonly: true, rows: '4', spellcheck: 'false' }));
  area.value = cmd;
  return {
    el: h('div', { class: 'field' }, h('label', { class: 'field__label', for: 'wz-cmd' }, `Run this on ${v.name} as ${v.username}`), area),
    copyCommand: () => copyWithToast(cmd, 'Install command copied'),
    copyKey: () => copyWithToast(pub, 'Public key copied'),
  };
}
