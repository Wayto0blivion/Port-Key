/*
 * wizard/copy.js — owner: package 8b (private module of views/wizard.js; pure, no DOM).
 *
 * The wizard's words (docs/ux-spec.md "Add connection: the key install wizard"): the live
 * checklist line for each `step` message, which wizard step it belongs to, the failure panels
 * for each `error` code and the `needs_action` panels. Server text is only ever shown as text.
 */

import { addr } from './hostkey.js';

/** @typedef {import('./form.js').WizardValues} WizardValues */
/** @typedef {import('../types.js').WsError} WsError */
/** @typedef {{step: string, state: string, code?: string, detail?: string, hop?: import('../types.js').Hop}} StepMsg */
/** @typedef {'running'|'done'|'warn'|'fail'} LineState */
/** @typedef {{key: string, text: string, state: LineState, note?: string}} Line */

/** Which of the four wizard steps a server step belongs to. @param {string} step @returns {1|2|3|4} */
export function wizardStepOf(step) {
  if (step === 'password_login') return 3;
  if (step === 'install' || step === 'verify' || step === 'discard_password' || step === 'save') return 4;
  return 2;
}

/** @param {StepMsg} m @param {WizardValues} v @returns {Line|null} */
export function stepLine(m, v) {
  /** @type {LineState} */
  const state = m.state === 'ok' ? 'done' : m.state === 'warn' ? 'warn' : m.state === 'fail' ? 'fail' : 'running';
  if (m.state === 'skipped') return null;
  switch (m.step) {
    case 'connect': {
      const who = m.hop && m.hop.role === 'jump' ? m.hop.name : addr(v.host, v.port);
      return { key: `connect${m.hop?.index ?? 0}`, text: `Reaching ${who}…`, state };
    }
    case 'hostkey': {
      const text = {
        known: 'Host key matches your known_hosts entry.',
        trusted: 'Host key matches the one Port-Key trusted before.',
        accepted: 'Host key trusted.',
      }[m.code ?? ''] ?? (state === 'done' ? 'Host key checked' : 'Checking host key');
      return { key: 'hostkey', text, state };
    }
    case 'try_key':
      if (m.code === 'already_installed') return { key: 'try_key', text: 'Your key is already installed', state };
      if (m.code === 'refused') return { key: 'try_key', text: 'Key not installed yet', state: 'done' };
      return { key: 'try_key', text: 'Trying your key…', state };
    case 'password_login':
      return { key: 'password_login', text: state === 'done' ? 'Signed in with password' : 'Signing in with password…', state };
    case 'install':
      // A loose-permissions warning arrives as a second `install` step after ok added or
      // already_present; the caller keeps the earlier text and adds the note.
      if (m.code === 'already_present') return { key: 'install', text: 'Key was already in authorized_keys', state };
      if (m.code === 'perms_warning') {
        return {
          key: 'install', text: 'Added key to ~/.ssh/authorized_keys', state: 'warn',
          note: 'Other users can write to the home folder, ~/.ssh or authorized_keys, so sshd may ignore the key.',
        };
      }
      if (state === 'fail') return { key: 'install', text: "Couldn't add the key to ~/.ssh/authorized_keys", state };
      return { key: 'install', text: state === 'running' ? 'Adding key to ~/.ssh/authorized_keys…' : 'Added key to ~/.ssh/authorized_keys', state };
    case 'verify':
      if (state === 'fail') return { key: 'verify', text: 'Key login with the key only was refused', state };
      return { key: 'verify', text: state === 'running' ? 'Signing in again with the key only…' : 'Signed in again with the key only', state };
    case 'discard_password':
      return { key: 'discard_password', text: 'Password discarded', state };
    case 'save':
      return { key: 'save', text: state === 'running' ? 'Saving the connection…' : 'Connection saved', state };
    default:
      return null;
  }
}

/**
 * @typedef {object} FailureCopy
 * @property {string} title
 * @property {string} body
 * @property {('retry'|'edit'|'password_session')[]} actions
 * @property {string} details  the server's own text, for the collapsed Details block
 */

/** @param {WsError} err @param {WizardValues} v @returns {FailureCopy} */
export function failureCopy(err, v) {
  const where = v.host;
  const retryEdit = /** @type {FailureCopy['actions']} */ (['retry', 'edit']);
  const details = [err.message, `Code: ${err.code}`].filter(Boolean).join('\n');
  switch (err.code) {
    case 'timeout':
      return { title: `Couldn't reach ${where}`, details, actions: retryEdit,
        body: `Couldn't reach ${where} on port ${v.port}. The connection timed out after 10 seconds. Check that the host is on and, for VPN hosts, that the VPN is connected.` };
    case 'refused':
      return { title: `${where} refused the connection`, details, actions: retryEdit,
        body: `${where} refused the connection on port ${v.port}. The host is up but nothing is listening for SSH on that port. Check the port number or start sshd on the host.` };
    case 'dns':
      return { title: `Couldn't find ${where}`, details, actions: retryEdit,
        body: `Couldn't find a host named ${where}. Check the spelling or use its IP address. Port-Key looks names up from inside its container, so names that exist only in the Windows hosts file may not resolve.` };
    case 'jump_failed':
      return { title: "The jump host didn't connect", details, actions: retryEdit,
        body: `Couldn't reach ${v.name} because the jump host ${err.hop?.name ?? 'on the way'} didn't connect. ${err.message}` };
    case 'auth_failed':
      return { title: 'Wrong password', details, actions: retryEdit,
        body: `${v.name} rejected the password for ${v.username}. Check the user name and password, then try again.` };
    case 'password_expired':
      return { title: 'The password has expired', details, actions: ['password_session'],
        body: `${v.username}'s password on ${v.name} has expired and must be changed. Open a password session to change it, then run this wizard again.` };
    case 'hostkey_changed':
      return { title: `${err.hop?.name ?? v.name}'s host key has changed`, details, actions: retryEdit,
        body: 'Port-Key kept the connection closed and sent no password and no key. If you know why the key changed, try again and type the name to replace the stored key.' };
    case 'hostkey_revoked':
      return { title: `${err.hop?.name ?? v.name}'s host key is revoked`, details, actions: ['edit'], body: err.message };
    case 'key_locked':
      return { title: 'The key stayed locked', details, actions: retryEdit, body: err.message };
    case 'not_found':
      return { title: 'That jump host no longer exists', details, actions: ['edit'],
        body: 'The jump host or connection was deleted while the wizard was open. Choose another Jump via setting.' };
    case 'prompt_timeout':
      return { title: 'No answer in time', details, actions: retryEdit, body: err.message };
    default:
      return { title: "Couldn't install the key", details, actions: retryEdit, body: err.message };
  }
}

/** Codes of a `start` the server could not accept: the form is at fault (close 4400). */
export const INPUT_CODES = new Set(['name_taken', 'group_unknown', 'jump_cycle', 'jump_too_deep', 'tag_invalid', 'validation']);

/**
 * The panel for a `needs_action`. The server's message is the same copy with more context (a
 * read-only reason, a Windows hint, "already fixed"), so it is the body when present; a leading
 * sentence that repeats the title is dropped. The copy here is the fallback.
 * @param {{code: string, message?: string, output?: string}} m @param {WizardValues} v
 * @returns {{title: string, body: string, step: 2|3|4}}
 */
export function needsCopy(m, v) {
  const local = localNeeds(m, v);
  let server = (m.message ?? '').trim();
  if (!/\s/.test(server)) return local; // empty, or a bare code
  const lead = `${local.title}.`;
  if (server.toLowerCase().startsWith(lead.toLowerCase())) server = server.slice(lead.length).trim();
  return { ...local, body: server || local.body };
}

/** @param {{code: string}} m @param {WizardValues} v @returns {{title: string, body: string, step: 2|3|4}} */
function localNeeds(m, v) {
  switch (m.code) {
    case 'key_refused':
      return { step: 3, title: 'Key login was refused',
        body: `Port-Key tried your key on ${v.name} and the server refused it, so the key is not on this host yet. Choose how to go on:` };
    case 'password_not_allowed':
      return { step: 3, title: `${v.name} does not accept passwords`,
        body: "The server only allows key logins, so Port-Key can't install your key for you. Add the key on the host yourself, for example from its console, then check again." };
    case 'key_still_refused':
      return { step: 4, title: `${v.name} still refused the key`,
        body: `Port-Key added your key, but ${v.name} still refused it. The usual cause is permissions: sshd ignores authorized_keys if other users can write to it or to ~/.ssh.` };
    case 'authorized_keys_readonly':
      return { step: 4, title: "Port-Key couldn't write authorized_keys",
        body: "Port-Key signed in but couldn't write ~/.ssh/authorized_keys. Some appliances store SSH keys elsewhere. Check the device's documentation for how to add a key." };
    case 'non_posix':
      return { step: 3, title: `${v.name} doesn't offer a standard shell`,
        body: `${v.name} doesn't offer a standard shell, so Port-Key can't install the key for you. Add the public key on the host yourself, then select Check key login.` };
    default:
      return { step: 3, title: 'Port-Key needs a decision', body: '' };
  }
}
