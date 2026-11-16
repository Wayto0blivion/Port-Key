/*
 * dialogs.js — owner: package 8b.
 *
 * Connection-time dialogs (copy in docs/ux-spec.md section 4): host key first seen (and
 * the jump-host variant), host key changed with typed-name confirmation ([Keep it closed] has
 * initial focus), passphrase (Keep unlocked for 8 hours), password, keyboard-interactive, paste
 * guard ("Paste 14 lines into web-prod?"), and close-all confirmation. Built on ui/modal.js.
 *
 * answerPrompt(prompt, ctx) resolves with the reply for prompt_reply. Secrets go from the field
 * straight into the reply; the fields are emptied when the dialog closes. ctx.signal (from
 * ws/protocol.js) closes the dialog when the prompt is overtaken (timeout, error, socket gone).
 * Prompts in a jump chain say which hop is asking.
 */

import { h } from './dom.js';
import { openModal, confirmDialog } from './modal.js';
import {
  firstSeenBody, firstSeenTitle, changedBody, changedTitle, hopLabel, nameMatches, addr,
} from '../wizard/hostkey.js';

/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').PromptReply} PromptReply */
/** @typedef {import('./modal.js').ModalHandle} ModalHandle */

/**
 * @typedef {object} PromptContext
 * @property {AbortSignal} [signal]
 * @property {string} [targetName]     the connection being opened (jump-host copy)
 * @property {number} [passphraseTtl]  seconds, for "Keep unlocked for 8 hours"
 */

let seq = 0;

/** "8 hours", "30 minutes", "1 day". @param {number} seconds */
export function ttlText(seconds) {
  const s = Math.max(60, Number(seconds) || 28800);
  if (s % 86400 === 0) return s === 86400 ? '1 day' : `${s / 86400} days`;
  if (s >= 3600 && s % 3600 === 0) return s === 3600 ? '1 hour' : `${s / 3600} hours`;
  const m = Math.round(s / 60);
  return m === 1 ? '1 minute' : `${m} minutes`;
}

/**
 * Close the dialog when the prompt is overtaken; resolve with the mapped value.
 * @param {ModalHandle} handle @param {AbortSignal|undefined} signal
 * @returns {Promise<string|null>}
 */
function settle(handle, signal) {
  if (signal) {
    if (signal.aborted) handle.close(null);
    else signal.addEventListener('abort', () => handle.close(null), { once: true });
  }
  return handle.result;
}

/** @param {Record<string, unknown>} attrs */
function secretInput(attrs) {
  seq += 1;
  return /** @type {HTMLInputElement} */ (h('input', {
    id: `prompt-secret-${seq}`, class: 'input input--lg', type: 'password', autocomplete: 'off', spellcheck: 'false', ...attrs,
  }));
}

/** @param {...HTMLInputElement} inputs */
function wipe(...inputs) {
  for (const input of inputs) input.value = '';
}

/** @param {Prompt} prompt @param {PromptContext} [ctx] @returns {Promise<PromptReply>} */
export async function answerPrompt(prompt, ctx = {}) {
  switch (prompt.kind) {
    case 'hostkey_new': return hostkeyNew(prompt, ctx);
    case 'hostkey_changed': return hostkeyChanged(prompt, ctx);
    case 'passphrase': return passphrase(prompt, ctx);
    case 'password': return password(prompt, ctx);
    case 'kbdint': return kbdint(prompt, ctx);
    default: return { cancel: true };
  }
}

/** @param {Prompt} p @param {PromptContext} ctx @returns {Promise<PromptReply>} */
async function hostkeyNew(p, ctx) {
  const handle = openModal({
    title: firstSeenTitle(p),
    body: firstSeenBody(p, ctx.targetName ?? p.hop.name),
    className: 'modal--prompt',
    buttons: [
      { label: 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: 'Trust this host', kind: 'primary', value: 'trust' },
    ],
  });
  return (await settle(handle, ctx.signal)) === 'trust' ? { accept: true } : { accept: false };
}

/** @param {Prompt} p @param {PromptContext} ctx @returns {Promise<PromptReply>} */
async function hostkeyChanged(p, ctx) {
  seq += 1;
  const input = /** @type {HTMLInputElement} */ (h('input', {
    id: `hk-confirm-${seq}`, class: 'input mono', autocomplete: 'off', spellcheck: 'false',
  }));
  const expected = p.confirm_name ?? p.hop.name;
  const handle = openModal({
    title: changedTitle(p),
    body: changedBody(p, input),
    danger: true,
    className: 'modal--prompt modal--hostkey',
    buttons: [
      { label: 'Replace key and connect', kind: 'danger', value: 'replace', disabled: true },
      { label: 'Keep it closed', kind: 'secondary', value: 'cancel' },
    ],
  });
  input.addEventListener('input', () => {
    const btn = handle.button('replace');
    if (btn) btn.disabled = !nameMatches(input.value, expected);
  });
  const value = await settle(handle, ctx.signal);
  const confirm = input.value;
  wipe(input);
  return value === 'replace' ? { accept: true, confirm_name: confirm.trim() } : { accept: false, confirm_name: '' };
}

/** @param {Prompt} p @param {PromptContext} ctx @returns {Promise<PromptReply>} */
async function passphrase(p, ctx) {
  const keyName = p.key_name ?? 'your key';
  const field = secretInput({});
  const keep = /** @type {HTMLInputElement} */ (h('input', { type: 'checkbox', checked: true }));
  /** @type {PromptReply|null} */
  let reply = null;
  const handle = openModal({
    title: `Unlock ${keyName}`,
    width: 460,
    className: 'modal--prompt modal--passphrase',
    body: h('div', { class: 'prompt' },
      hopLabel(p.hop),
      h('p', { class: 'modal__text' }, `Enter the passphrase for this key to connect to ${p.hop.name}.`),
      h('div', { class: 'field' }, h('label', { class: 'field__label', for: field.id }, 'Passphrase'), field),
      h('label', { class: 'check' }, keep,
        h('span', {}, `Keep unlocked for ${ttlText(ctx.passphraseTtl ?? 28800)}`,
          h('span', { class: 'field__help check__help' }, 'Held in memory only. Lock it any time from the top bar.')))),
    buttons: [
      { label: 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: 'Unlock and connect', kind: 'primary', value: 'unlock' },
    ],
    initialFocus: field,
    onAction: () => {
      if (!field.value) {
        handle.setError('Enter the passphrase.');
        field.focus();
        return false;
      }
      reply = { value: field.value, remember: keep.checked };
      return true;
    },
  });
  if (p.retry) handle.setError(`That passphrase doesn't unlock ${keyName}. Try again.`);
  const value = await settle(handle, ctx.signal);
  wipe(field);
  return value === 'unlock' && reply ? reply : { cancel: true };
}

/** @param {Prompt} p @param {PromptContext} ctx @returns {Promise<PromptReply>} */
async function password(p, ctx) {
  const user = p.user ?? '';
  const where = addr(p.host ?? p.hop.host, p.port ?? p.hop.port);
  const field = secretInput({});
  /** @type {PromptReply|null} */
  let reply = null;
  const handle = openModal({
    title: `Sign in to ${p.hop.name}`,
    width: 460,
    className: 'modal--prompt',
    body: h('div', { class: 'prompt' },
      hopLabel(p.hop),
      h('p', { class: 'modal__text' }, 'Enter the password for ', h('span', { class: 'mono strong' }, user),
        ` on ${p.hop.name}. Port-Key uses it for this sign-in only and never saves it.`),
      h('div', { class: 'field' }, h('label', { class: 'field__label', for: field.id }, `Password for ${user}@${where}`), field)),
    buttons: [
      { label: 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: 'Sign in', kind: 'primary', value: 'signin' },
    ],
    initialFocus: field,
    onAction: () => {
      reply = { value: field.value };
      return true;
    },
  });
  if (p.retry) handle.setError(`${p.hop.name} rejected the password for ${user}. Check the user name and password, then try again.`);
  const value = await settle(handle, ctx.signal);
  wipe(field);
  return value === 'signin' && reply ? reply : { cancel: true };
}

/** @param {Prompt} p @param {PromptContext} ctx @returns {Promise<PromptReply>} */
async function kbdint(p, ctx) {
  const questions = p.prompts ?? [];
  const inputs = questions.map((q) => (q.echo
    ? secretInput({ type: 'text', class: 'input input--lg mono' })
    : secretInput({})));
  /** @type {PromptReply|null} */
  let reply = null;
  // Everything the server chose (name, instructions, questions) is shown as the server's, under
  // a title Port-Key chose: a hostile server must not be able to dress this up as Port-Key's own
  // passphrase or sign-in dialog. The host is always named, also for a direct connection.
  const server = p.hop.name;
  const name = p.name?.trim() ?? '';
  const instructions = p.instructions?.trim() ?? '';
  const handle = openModal({
    title: `Verification for ${server}`,
    width: 460,
    className: 'modal--prompt',
    body: h('div', { class: 'prompt' },
      hopLabel(p.hop) ?? h('p', { class: 'prompt__hop' }, 'Server: ', h('strong', {}, server), h('span', { class: 'mono muted' }, ` ${addr(p.hop.host, p.hop.port)}`)),
      name || instructions ? h('div', { class: 'prompt__server' },
        h('p', { class: 'prompt__server-label' }, `Message from ${server}:`),
        name ? h('p', { class: 'modal__text prompt__server-name' }, name) : null,
        instructions ? h('p', { class: 'modal__text prompt__instructions' }, instructions) : null) : null,
      questions.length ? null : h('p', { class: 'modal__text muted' }, `${server} sent a message. Select Continue to go on.`),
      ...questions.map((q, i) => h('div', { class: 'field' },
        h('label', { class: 'field__label', for: inputs[i].id }, `${server} asks: `, h('span', { class: 'prompt__question' }, q.text.trim() || 'Answer')), inputs[i]))),
    buttons: [
      { label: 'Cancel', kind: 'secondary', value: 'cancel' },
      { label: 'Continue', kind: 'primary', value: 'continue' },
    ],
    onAction: () => {
      reply = { values: inputs.map((i) => i.value) };
      return true;
    },
  });
  if (p.retry) handle.setError("That didn't work. Try again.");
  const value = await settle(handle, ctx.signal);
  wipe(...inputs);
  return value === 'continue' && reply ? reply : { cancel: true };
}

/**
 * @param {number} lines @param {string} name
 * @param {boolean} [runsNow] one line that ends in a line break, in a shell that runs it at once
 * @returns {Promise<boolean>}
 */
export async function confirmPaste(lines, name, runsNow = false) {
  if (runsNow && lines === 1) {
    return confirmDialog({
      title: `Paste and run 1 line in ${name}?`,
      message: 'The text ends with a line break, and this shell runs pasted text at once.',
      confirmLabel: 'Paste and run',
    });
  }
  return confirmDialog({
    title: `Paste ${lines} lines into ${name}?`,
    message: 'The shell may run each line as a separate command.',
    confirmLabel: 'Paste',
  });
}

/** @param {number} count @returns {Promise<boolean>} */
export async function confirmCloseAll(count) {
  return confirmDialog({
    title: `Close all ${count} sessions?`,
    message: "The shell in every session ends. Port-Key can't bring them back.",
    confirmLabel: count === 1 ? 'Close session' : `Close ${count} sessions`,
    danger: true,
  });
}
