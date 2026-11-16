/*
 * wizard.js — owner: package 8b.
 *
 * The key install wizard dialog over /ws/install: step 1 Host details (including the
 * Folder picker and Tags field from ui/folderpicker.js and ui/tagfield.js), 2 Check host
 * identity, 3 Sign in once (password and 2FA fields from prompts), 4 Verify key login checklist,
 * the final "Connection saved" card, the manual path, and every failure panel in
 * docs/ux-spec.md with its actions (sent as `action` messages).
 *
 * Entry points (called by the shell): openWizard({}) from Add connection, {host, port, name}
 * from a known_hosts suggestion, {key_fp} from Keys "Install on a host…", {host, port, username}
 * from the palette, and {..., connection_id} with {startAt: 'signin'} from "Reinstall key".
 * It resolves with the last connection it saved (null if none) when it closes.
 *
 * The run itself (socket, prompts, steps) is in wizard/run.js; this file owns the dialog, the
 * footer buttons and the moves between views. "Password only" saves over the HTTP API without
 * a socket, since there is no key to install.
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { h, clear } from '../ui/dom.js';
import { openFrame } from '../wizard/frame.js';
import { buildForm, autoName } from '../wizard/form.js';
import { stepIndicator, section, summaryCard, checklist } from '../wizard/views.js';
import { WizardRun } from '../wizard/run.js';

/** @typedef {import('../types.js').Connection} Connection */
/** @typedef {import('../wizard/form.js').WizardValues} WizardValues */
/** @typedef {{label: string, kind?: 'primary'|'secondary'|'danger', onClick: () => void, disabled?: boolean, key?: string}} Btn */
/** @typedef {{step: number|'final', problem?: number|null, content: Node, left?: Btn[], right?: Btn[], focus?: HTMLElement|null}} ViewSpec */

/**
 * @typedef {{host?: string, port?: number, username?: string, name?: string, group_id?: string|null,
 *   tags?: string[], key_fp?: string|null, jump_id?: string|null, connection_id?: string}} Prefill
 */

/** @param {Prefill} p @returns {WizardValues} */
function initialValues(p) {
  const existing = p.connection_id ? store.connectionById(p.connection_id) : undefined;
  const host = p.host ?? existing?.host ?? '';
  return {
    host,
    port: p.port ?? existing?.port ?? 22,
    username: p.username ?? existing?.username ?? store.getState().settings?.default_user ?? '',
    name: p.name ?? existing?.name ?? autoName(host),
    group_id: p.group_id !== undefined ? p.group_id : existing?.group_id ?? null,
    tags: p.tags ?? existing?.tags ?? [],
    color: existing?.color ?? 'grey',
    key_fp: p.key_fp !== undefined ? p.key_fp : existing?.key_fp ?? null,
    password_only: false,
    jump_id: p.jump_id !== undefined ? p.jump_id : existing?.jump_id ?? null,
    assume_installed: false,
    pinned: existing?.pinned ?? false,
    notes: existing?.notes ?? '',
    connection_id: p.connection_id ?? null,
  };
}

/** @type {Wizard|null} */
let openNow = null;

/**
 * @param {Prefill} [prefill]
 * @param {{startAt?: 'host'|'signin'}} [opts]
 * @returns {Promise<Connection|null>} the saved connection, or null if closed
 */
export async function openWizard(prefill = {}, opts = {}) {
  if (openNow) {
    openNow.frame.dialog.focus();
    return null;
  }
  return new Promise((resolve) => {
    openNow = new Wizard(prefill, opts, (c) => {
      openNow = null;
      resolve(c);
    });
  });
}

export class Wizard {
  /** @param {Prefill} prefill @param {{startAt?: 'host'|'signin'}} opts @param {(c: Connection|null) => void} done */
  constructor(prefill, opts, done) {
    this.values = initialValues(prefill);
    this.done = done;
    /** @type {Connection|null} */
    this.saved = null;
    /** @type {WizardRun|null} */
    this.run = null;
    /** @type {ReturnType<typeof buildForm>|null} */
    this.form = null;
    const reinstall = Boolean(this.values.connection_id);
    this.steps = h('div', { class: 'wizard__steps' });
    this.body = h('div', { class: 'modal__body wizard__body' });
    this.error = h('div', { class: 'modal__error', role: 'alert', hidden: true });
    this.left = h('div', { class: 'wizard__left' });
    this.right = h('div', { class: 'wizard__right' });
    this.frame = openFrame({
      className: 'modal wizard',
      labelledBy: 'wizard-title',
      onDismiss: () => this.close(),
      content: h('div', { class: 'modal__box' },
        h('header', { class: 'modal__head wizard__head' },
          h('h2', { id: 'wizard-title', class: 'modal__title' }, reinstall ? `Reinstall key on ${this.values.name}` : 'Add connection'),
          this.steps),
        this.error,
        this.body,
        h('footer', { class: 'modal__foot wizard__foot' }, this.left, this.right)),
    });
    if (opts.startAt === 'signin' && this.values.host && this.values.username && this.values.name) this.start();
    else this.showForm();
  }

  /** @param {ViewSpec} spec */
  render(spec) {
    this.steps.replaceChildren(stepIndicator(spec.step, spec.problem ?? null));
    this.body.replaceChildren(spec.content);
    this.body.scrollTop = 0;
    const btn = (/** @type {Btn} */ b) => h('button', {
      type: 'button', class: `btn btn--${b.kind ?? 'secondary'}`, disabled: b.disabled, dataset: b.key ? { key: b.key } : {}, onClick: b.onClick,
    }, b.label);
    clear(this.left);
    clear(this.right);
    for (const b of spec.left ?? []) this.left.appendChild(btn(b));
    for (const b of spec.right ?? []) this.right.appendChild(btn(b));
    const target = spec.focus === undefined
      ? /** @type {HTMLElement|null} */ (this.body.querySelector('.wstep__title')) : spec.focus;
    target?.focus();
  }

  /** @param {string|null} message */
  setError(message) {
    this.error.textContent = message ?? '';
    this.error.hidden = !message;
  }

  /** @returns {Btn} */
  cancelBtn() {
    return { label: 'Cancel', key: 'cancel', onClick: () => this.close() };
  }

  /** @returns {Btn} */
  backBtn() {
    return { label: 'Back', key: 'back', onClick: () => this.showForm() };
  }

  showForm() {
    this.abandon();
    this.setError(null);
    const form = buildForm(this.values);
    this.form = form;
    const draw = () => this.render({
      step: 1,
      content: form.el,
      right: [this.cancelBtn(), form.isPasswordOnly()
        ? { label: 'Save connection', kind: 'primary', key: 'save', onClick: () => this.next() }
        : { label: 'Next', kind: 'primary', key: 'next', onClick: () => this.next() }],
      focus: null,
    });
    form.onModeChange(() => {
      const had = document.activeElement;
      draw();
      if (had instanceof HTMLElement && form.el.contains(had)) had.focus();
    });
    draw();
    form.el.addEventListener('keydown', (e) => {
      const t = /** @type {HTMLElement} */ (e.target);
      if (e.key === 'Enter' && t.matches('input:not([type=checkbox]):not([type=radio]):not(.tagfield__input)') && !e.defaultPrevented) {
        e.preventDefault();
        this.next();
      }
    });
    form.focus();
  }

  next() {
    const v = this.form?.collect();
    if (!v) return;
    this.values = v;
    if (v.password_only) this.savePasswordOnly(false);
    else this.start();
  }

  start() {
    this.abandon();
    this.setError(null);
    this.run = new WizardRun(this, this.values);
    this.run.begin();
  }

  /** Stop a run in flight (Back, Cancel, a new run). The password is dropped with it. */
  abandon() {
    const run = this.run;
    this.run = null;
    run?.stop();
  }

  close() {
    this.abandon();
    this.frame.close();
    this.done(this.saved);
  }

  /**
   * Save without installing a key (Password only, or before "Open password session").
   * @param {boolean} thenOpen open a password session right after
   */
  async savePasswordOnly(thenOpen) {
    const v = this.values;
    const body = {
      name: v.name, host: v.host, port: v.port, username: v.username, auth_method: /** @type {const} */ ('password'), key_fp: null,
      jump_id: v.jump_id, group_id: v.group_id, tags: v.tags, color: v.color, pinned: v.pinned, notes: v.notes,
    };
    try {
      this.saved = v.connection_id ? await api.updateConnection(v.connection_id, body) : await api.createConnection(body);
    } catch (err) {
      this.setError(err instanceof Error ? err.message : String(err));
      return;
    }
    this.values = { ...v, connection_id: this.saved.id };
    await store.refreshConnections().catch(() => {});
    if (thenOpen) {
      const c = this.saved;
      this.close();
      const { openSession } = await import('./sessions.js');
      await openSession(c.id, { forcePassword: true, newSession: true });
      return;
    }
    this.showFinal('saved_password_only', []);
  }

  /** @param {Connection} c */
  savedConnection(c) {
    this.saved = c;
    this.values = { ...this.values, connection_id: c.id };
    store.refresh().catch(() => {});
  }

  /** Step 2 after try_key found the key working: [Save connection] moves to the final card. */
  showAlready() {
    this.render({
      step: 2,
      content: section('Check host identity',
        h('p', { class: 'wstep__lead' }, 'Your key is already installed on ', h('strong', {}, this.values.name),
          '. Port-Key signed in with it, so no password is needed.')),
      right: [this.cancelBtn(), { label: 'Save connection', kind: 'primary', key: 'save', onClick: () => this.showFinal('already_installed', []) }],
    });
  }

  /**
   * @param {'installed'|'already_installed'|'saved_password_only'} outcome
   * @param {import('../wizard/copy.js').Line[]} lines
   */
  showFinal(outcome, lines) {
    this.abandon();
    const v = this.values;
    const text = outcome === 'saved_password_only'
      ? `${v.name} is saved with password sign-in. Port-Key asks for the password each time you connect.`
      : 'From now on, one click opens this host with your key. No password needed.';
    const open = { label: 'Open terminal', kind: /** @type {const} */ ('primary'), key: 'open', onClick: () => this.openTerminal() };
    this.render({
      step: 'final',
      content: section('Connection saved',
        lines.length ? checklist(lines) : null,
        summaryCard(this.saved, v, outcome),
        h('p', { class: 'muted' }, text)),
      right: [{ label: 'Add another', key: 'another', onClick: () => this.addAnother() }, open],
      focus: null,
    });
    /** @type {HTMLElement|null} */ (this.right.querySelector('[data-key="open"]'))?.focus();
  }

  addAnother() {
    this.values = initialValues({});
    this.showForm();
  }

  async openTerminal() {
    const c = this.saved;
    this.close();
    if (!c) return;
    await store.refreshConnections().catch(() => {});
    const { openSession } = await import('./sessions.js');
    await openSession(c.id, { newSession: true });
  }
}
