/*
 * wizard/run.js — owner: package 8b (private module of views/wizard.js).
 *
 * One run of the key install wizard over /ws/install (docs/ws-protocol.md): sends `start`,
 * turns `step` messages into the live checklist, shows prompts (the target's first-seen host key
 * inline as step 2, the target's password and verification codes inline as step 3, everything
 * else as a dialog from ui/dialogs.js that names its hop), shows `needs_action` panels whose
 * buttons send `action`, and ends with `done` (the final card) or `error` (a failure panel in
 * the step where it happened). stop() abandons the run: it sends `close` and drops the socket,
 * which drops the password on the server.
 */

import * as store from '../store.js';
import { h } from '../ui/dom.js';
import { PortKeySocket } from '../ws/protocol.js';
import { answerPrompt } from '../ui/dialogs.js';
import { firstSeenBody, firstSeenTitle } from './hostkey.js';
import { stepLine, wizardStepOf, failureCopy, needsCopy, INPUT_CODES } from './copy.js';
import { checklist, section, details, signinView, commandBox } from './views.js';

/** @typedef {import('./copy.js').Line} Line */
/** @typedef {import('./form.js').WizardValues} WizardValues */
/** @typedef {import('../types.js').Prompt} Prompt */
/** @typedef {import('../types.js').PromptReply} PromptReply */
/** @typedef {import('../views/wizard.js').Wizard} Wizard */
/** @typedef {import('../views/wizard.js').Btn} Btn */

const VERIFY_KEYS = ['password_login', 'install', 'verify', 'discard_password'];

export class WizardRun {
  /** @param {Wizard} wiz @param {WizardValues} values */
  constructor(wiz, values) {
    this.wiz = wiz;
    this.v = values;
    /** @type {Map<string, Line>} */
    this.lines = new Map();
    /** @type {2|3|4} */
    this.step = 2;
    /** @type {'connecting'|'identity'|'signin'|'verify'|'needs'|'failure'|'done'} */
    this.view = 'connecting';
    this.stopped = false;
    this.finished = false;
    /** @type {{kind: string, resolve: (r: PromptReply) => void}|null} */
    this.inline = null;
    /** @type {ReturnType<typeof signinView>|null} */
    this.signin = null;
    /** @type {HTMLInputElement[]} */
    this.codeInputs = [];
    this.passwordSent = false;
    this.socket = new PortKeySocket('/ws/install', {
      onMessage: (m) => this.onMessage(m),
      onPrompt: (p, signal) => this.onPrompt(p, signal),
      onClose: () => this.onClose(),
    });
  }

  async begin() {
    this.showConnecting();
    try {
      await this.socket.connect();
    } catch {
      this.fail(null);
      return;
    }
    const v = this.v;
    this.socket.send({
      type: 'start', host: v.host, port: v.port, username: v.username, key_fp: v.key_fp, name: v.name, jump_id: v.jump_id,
      group_id: v.group_id, tags: v.tags, color: v.color, pinned: v.pinned, notes: v.notes,
      assume_installed: v.assume_installed, connection_id: v.connection_id,
    });
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    this.settleInline();
    if (!this.finished) this.socket.send({ type: 'close' });
    this.socket.close();
  }

  settleInline() {
    const inline = this.inline;
    this.inline = null;
    inline?.resolve({ cancel: true });
  }

  /** @returns {Btn} */
  back() { return this.wiz.backBtn(); }

  /** @returns {Btn} */
  cancel() { return this.wiz.cancelBtn(); }

  /** @param {string[]} keys */
  linesFor(keys) {
    return [...this.lines.values()].filter((l) => keys.includes(l.key));
  }

  showConnecting() {
    this.view = 'connecting';
    this.step = 2;
    const early = [...this.lines.values()].filter((l) => !VERIFY_KEYS.includes(l.key));
    this.wiz.render({
      step: 2,
      content: section('Check host identity', checklist(early.length ? early : [{ key: 'start', text: 'Starting…', state: 'running' }])),
      left: [this.back()],
      right: [this.cancel()],
      focus: null,
    });
  }

  showVerify() {
    this.view = 'verify';
    this.step = 4;
    this.wiz.render({
      step: 4,
      content: section('Verify key login', checklist(this.linesFor(VERIFY_KEYS))),
      right: [this.cancel()],
      focus: null,
    });
  }

  /** @param {Record<string, any>} m */
  onMessage(m) {
    if (this.stopped) return;
    if (m.type === 'step') {
      const line = stepLine(/** @type {any} */ (m), this.v);
      const prev = line ? this.lines.get(line.key) : undefined;
      if (line && m.code === 'perms_warning' && prev) line.text = prev.text; // "already in" stays
      if (line) this.lines.set(line.key, line);
      const at = wizardStepOf(m.step);
      if (m.step === 'password_login' && m.state === 'running') this.showSignin();
      else if ((at === 4 || (m.step === 'password_login' && m.state === 'ok')) && this.view !== 'verify') this.showVerify();
      else if (this.view === 'verify') this.showVerify();
      else if (this.view === 'connecting') this.showConnecting();
    } else if (m.type === 'needs_action') {
      this.showNeeds(m);
    } else if (m.type === 'done') {
      this.finished = true;
      this.view = 'done';
      this.wiz.savedConnection(m.connection);
      if (m.outcome === 'already_installed') this.wiz.showAlready();
      else this.wiz.showFinal(m.outcome, m.outcome === 'installed' ? this.linesFor(VERIFY_KEYS) : []);
    }
  }

  /** @param {Prompt} p @param {AbortSignal} signal @returns {Promise<PromptReply>} */
  onPrompt(p, signal) {
    if (this.stopped) return Promise.resolve({ cancel: true });
    const target = p.hop.role === 'target';
    if (p.kind === 'hostkey_new') return this.inlinePrompt(p, signal, () => this.showIdentity(p));
    if (target && (p.kind === 'password' || p.kind === 'kbdint')) return this.inlinePrompt(p, signal, () => this.askSignin(p));
    return answerPrompt(p, { signal, targetName: this.v.name, passphraseTtl: store.getState().settings?.passphrase_ttl });
  }

  /** @param {Prompt} p @param {AbortSignal} signal @param {() => void} show @returns {Promise<PromptReply>} */
  inlinePrompt(p, signal, show) {
    return new Promise((resolve) => {
      this.inline = { kind: p.kind, resolve };
      signal.addEventListener('abort', () => {
        if (this.inline?.resolve === resolve) this.inline = null;
        resolve({ cancel: true });
      }, { once: true });
      show();
    });
  }

  /** @param {PromptReply} reply */
  answer(reply) {
    const inline = this.inline;
    this.inline = null;
    inline?.resolve(reply);
  }

  /** @param {Prompt} p */
  showIdentity(p) {
    this.view = 'identity';
    this.step = 2;
    this.wiz.render({
      step: 2,
      content: section(firstSeenTitle(p), firstSeenBody(p, this.v.name)),
      left: [{ label: 'Back', key: 'back', onClick: () => { this.answer({ accept: false }); this.wiz.showForm(); } }],
      right: [this.cancel(), {
        label: 'Trust this host', kind: 'primary', key: 'trust',
        onClick: () => {
          this.answer({ accept: true });
          this.showConnecting();
        },
      }],
      focus: null,
    });
    /** @type {HTMLElement|null} */ (this.wiz.right.querySelector('[data-key="trust"]'))?.focus();
  }

  showSignin() {
    if (this.view === 'signin' && this.signin) return;
    this.view = 'signin';
    this.step = 3;
    this.signin = signinView(this.v, () => this.submitSignin());
    const known = this.lines.get('hostkey');
    const note = known && known.state === 'done' ? h('p', { class: 'wstep__note' }, `✓ ${known.text}`) : null;
    if (note) this.signin.el.insertBefore(note, this.signin.el.children[1]);
    this.wiz.render({
      step: 3,
      content: this.signin.el,
      left: [this.back()],
      right: [this.cancel(), { label: 'Install key', kind: 'primary', key: 'install', disabled: true, onClick: () => this.submitSignin() }],
      focus: null,
    });
    this.signin.setStatus(`Waiting for ${this.v.name}…`);
  }

  /** @param {Prompt} p */
  askSignin(p) {
    this.showSignin();
    const s = /** @type {ReturnType<typeof signinView>} */ (this.signin);
    const btn = /** @type {HTMLButtonElement|null} */ (this.wiz.right.querySelector('[data-key="install"]'));
    if (btn) btn.disabled = false;
    s.setStatus('');
    if (p.kind === 'password') {
      s.pw.disabled = false;
      s.pw.value = '';
      s.setError(p.retry ? `${this.v.name} rejected the password for ${this.v.username}. Check the user name and password, then try again.` : null);
      s.pw.focus();
      return;
    }
    if (!this.passwordSent) /** @type {HTMLElement} */ (s.pw.closest('.field')).hidden = true;
    s.sent.hidden = !this.passwordSent;
    this.codeInputs = s.showCodes(p.prompts ?? [], p.instructions ?? '');
    s.setError(p.retry ? "That didn't work. Try again." : null);
    (this.codeInputs[0] ?? btn)?.focus();
  }

  submitSignin() {
    const s = this.signin;
    if (!s || !this.inline) return;
    const btn = /** @type {HTMLButtonElement|null} */ (this.wiz.right.querySelector('[data-key="install"]'));
    if (this.inline.kind === 'password') {
      const value = s.pw.value;
      s.pw.value = '';
      s.pw.disabled = true;
      this.passwordSent = true;
      this.answer({ value });
    } else {
      const values = this.codeInputs.map((i) => i.value);
      for (const i of this.codeInputs) {
        i.value = '';
        i.disabled = true;
      }
      this.answer({ values });
    }
    if (btn) btn.disabled = true;
    s.setError(null);
    s.setStatus('Signing in…');
  }

  /** @param {Record<string, any>} m */
  showNeeds(m) {
    this.view = 'needs';
    const copy = needsCopy(/** @type {{code: string, output?: string}} */ (m), this.v);
    this.step = copy.step;
    const allowed = new Set(Array.isArray(m.actions) ? m.actions : []);
    const act = (/** @type {string} */ action, /** @type {string} */ label, /** @type {'primary'|'secondary'} */ kind) => (
      allowed.has(action) ? [{ label, kind, key: action, onClick: () => this.sendAction(action) }] : []);
    const box = commandBox(this.v, m);
    const copyBtn = (/** @type {string} */ label, /** @type {() => void} */ fn) => h('button', { type: 'button', class: 'btn btn--secondary', onClick: fn }, label);
    /** @type {(Node|null)[]} */
    let extra = [];
    /** @type {Btn[]} */
    let buttons = [];
    if (m.code === 'key_refused') {
      extra = [h('div', { class: 'btn-row' }, copyBtn('Copy public key', box.copyKey), copyBtn('Copy install command', box.copyCommand))];
      buttons = [...act('save_password_only', 'Save as password-only', 'secondary'), ...act('install_with_password', 'Install it with a password', 'primary')];
    } else if (m.code === 'password_not_allowed') {
      extra = [box.el, h('div', { class: 'btn-row' }, copyBtn('Copy install command', box.copyCommand), copyBtn('Copy public key only', box.copyKey))];
      buttons = act('check_key', 'Check key login', 'primary');
    } else if (m.code === 'key_still_refused') {
      extra = [h('p', { class: 'muted' }, `Fix permissions and retry runs chmod go-w on ~/.ssh and ~/.ssh/authorized_keys as ${this.v.username}.`)];
      buttons = [...act('save_password_only', 'Save as password-only', 'secondary'), ...act('fix_permissions', 'Fix permissions and retry', 'primary')];
    } else if (m.code === 'authorized_keys_readonly') {
      buttons = act('save_password_only', 'Save as password-only', 'primary');
    } else if (m.code === 'non_posix') {
      extra = [h('div', { class: 'btn-row' }, copyBtn('Copy public key', box.copyKey))];
      buttons = act('check_key', 'Check key login', 'primary');
    }
    this.wiz.render({
      step: this.step,
      problem: this.step,
      content: section(copy.title, h('p', { class: 'wstep__lead' }, copy.body), ...extra, details(m.output)),
      left: [this.back()],
      right: [this.cancel(), ...buttons],
    });
  }

  /** @param {string} action */
  sendAction(action) {
    if (action === 'cancel') {
      this.wiz.close();
      return;
    }
    for (const b of this.wiz.right.querySelectorAll('button')) {
      if (/** @type {HTMLElement} */ (b).dataset.key !== 'cancel') /** @type {HTMLButtonElement} */ (b).disabled = true;
    }
    this.wiz.body.querySelector('.wstep')?.appendChild(h('p', { class: 'wstep__status', role: 'status' }, 'Working…'));
    if (action === 'install_with_password') this.lines.delete('try_key');
    this.socket.send({ type: 'action', action });
  }

  onClose() {
    if (this.stopped || this.finished) return;
    this.settleInline();
    const err = this.socket.error;
    if (err && (err.code === 'cancelled' || err.code === 'hostkey_rejected')) {
      this.wiz.showForm();
      return;
    }
    this.fail(err);
  }

  /** @param {import('../types.js').WsError|null} err */
  fail(err) {
    if (this.stopped) return;
    this.stopped = true;
    this.view = 'failure';
    if (err?.code === 'unauthorized' || err?.code === 'auth_timeout') window.dispatchEvent(new CustomEvent('portkey:signed-out'));
    if (err && INPUT_CODES.has(err.code)) {
      // The form is at fault (a name taken meanwhile, a folder gone, a jump loop): back to it.
      this.wiz.showForm();
      this.wiz.setError(err.message);
      return;
    }
    const copy = err ? failureCopy(err, this.v) : {
      title: 'Lost the connection to Port-Key',
      body: 'The browser lost its connection to Port-Key while the wizard was running. Check whether the connection was saved, then try again.',
      actions: /** @type {('retry'|'edit'|'password_session')[]} */ (['retry', 'edit']),
      details: '',
    };
    /** @type {Btn[]} */
    const right = [this.cancel()];
    if (copy.actions.includes('edit')) right.push({ label: 'Edit host', key: 'edit', onClick: () => this.wiz.showForm() });
    if (copy.actions.includes('retry')) right.push({ label: 'Try again', kind: 'primary', key: 'retry', onClick: () => this.wiz.start() });
    if (copy.actions.includes('password_session')) {
      right.push({ label: 'Open password session', kind: 'primary', key: 'password_session', onClick: () => this.wiz.savePasswordOnly(true) });
    }
    this.wiz.render({
      step: this.step,
      problem: this.step,
      content: section(copy.title, h('p', { class: 'wstep__lead' }, copy.body), details(copy.details)),
      right,
    });
  }
}
