/*
 * terminal/chrome.js — owner: package 8b (private module of terminal/session.js).
 *
 * The DOM around one terminal (docs/design/Terminal, Disconnected, AuthFailed): the session bar
 * (colour chip, user@name, host:port, "via build-box", key name in brass, "Connected 12 min",
 * Find / Copy ssh command / A− / A+ / Disconnect), the 2px colour frame, the connecting overlay
 * with its live checklist and Cancel, the bottom bar (Disconnected, Reattaching, open elsewhere),
 * the panel that replaces the terminal after a failed connect, and the Find bar.
 *
 * Everything is built with ui/dom.js h(): connection names and server messages are text.
 */

import { h, clear, labelClass } from '../ui/dom.js';
import { icon } from '../parts/icons.js';

/** @typedef {{label: string, kind?: 'primary'|'secondary'|'ghost', onClick: () => void, key?: string}} Action */

/**
 * @typedef {object} ChromeHandlers
 * @property {() => void} onFind
 * @property {() => void} onCopySsh
 * @property {(delta: number) => void} onFont
 * @property {() => void} onDisconnect
 * @property {() => void} onCancel
 * @property {(text: string, back: boolean) => void} onSearch
 * @property {() => void} onFindClose
 */

/** @param {Action} a */
function actionButton(a) {
  return h('button', {
    type: 'button', class: `btn btn--${a.kind ?? 'secondary'}`, dataset: a.key ? { action: a.key } : {}, onClick: a.onClick,
  }, a.label);
}

/** @param {ChromeHandlers} handlers */
export function buildChrome(handlers) {
  const chip = h('span', { class: 'sbar__chip', 'aria-hidden': 'true' });
  const who = h('span', { class: 'sbar__who' });
  const where = h('span', { class: 'sbar__where mono' });
  const via = h('span', { class: 'sbar__via mono', hidden: true });
  const keyName = h('span', { class: 'sbar__key mono' });
  const uptime = h('span', { class: 'sbar__uptime' });
  const status = h('span', { class: 'sbar__status', hidden: true });
  const small = (/** @type {string} */ label, /** @type {() => void} */ fn, /** @type {Record<string, unknown>} */ extra = {}) =>
    h('button', { type: 'button', class: 'btn btn--outline btn--xs', onClick: fn, ...extra }, label);
  const disconnectBtn = /** @type {HTMLButtonElement} */ (small('Disconnect', handlers.onDisconnect));
  const actions = h('div', { class: 'sbar__actions' },
    small('Find', handlers.onFind, { 'aria-keyshortcuts': 'Control+Shift+F' }),
    small('Copy ssh command', handlers.onCopySsh),
    small('A−', () => handlers.onFont(-1), { 'aria-label': 'Smaller text', class: 'btn btn--outline btn--xs sbar__font' }),
    small('A+', () => handlers.onFont(1), { 'aria-label': 'Larger text', class: 'btn btn--outline btn--xs sbar__font' }),
    disconnectBtn);
  const bar = h('div', { class: 'sbar' }, h('span', { class: 'sbar__id' }, chip, who), where, via, keyName, uptime, status, actions);

  const termHost = h('div', { class: 'session__term' });
  const checklist = h('ul', { class: 'checklist-live', 'aria-live': 'polite' });
  const overlayTitle = h('p', { class: 'session__overlay-title' });
  const cancelBtn = h('button', { type: 'button', class: 'btn btn--secondary', onClick: handlers.onCancel }, 'Cancel');
  const overlay = h('div', { class: 'session__overlay', hidden: true, role: 'status' },
    h('div', { class: 'session__overlay-box' }, overlayTitle, checklist, h('div', { class: 'btn-row' }, cancelBtn)));
  const panel = h('div', { class: 'session__panel', hidden: true });
  const findInput = /** @type {HTMLInputElement} */ (h('input', {
    class: 'input find__input', type: 'search', 'aria-label': 'Find in scrollback', placeholder: 'Find in scrollback', spellcheck: 'false',
  }));
  const findNote = h('span', { class: 'find__note', role: 'status' });
  const find = h('div', { class: 'find', hidden: true, role: 'search' }, findInput, findNote,
    h('button', { type: 'button', class: 'btn btn--outline btn--xs', 'aria-label': 'Previous match', onClick: () => handlers.onSearch(findInput.value, true) }, '↑'),
    h('button', { type: 'button', class: 'btn btn--outline btn--xs', 'aria-label': 'Next match', onClick: () => handlers.onSearch(findInput.value, false) }, '↓'),
    h('button', { type: 'button', class: 'icon-btn find__close', 'aria-label': 'Close Find', onClick: handlers.onFindClose }, icon('close', { size: 14 })));
  findInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handlers.onSearch(findInput.value, e.shiftKey);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      handlers.onFindClose();
    }
  });
  findInput.addEventListener('input', () => handlers.onSearch(findInput.value, false));
  const bottom = h('div', { class: 'session__bottom', hidden: true, role: 'status' });
  const screen = h('div', { class: 'session__screen' }, termHost, overlay, panel, find);
  const frame = h('div', { class: 'session__frame' }, screen, bottom);
  const root = h('div', { class: 'session' }, bar, frame);

  /** @type {Map<string, HTMLElement>} */
  const lines = new Map();

  return {
    root, termHost, findInput, disconnectBtn,

    /** @param {{color: string, user: string, name: string, where: string, via: string, key: string, password: boolean}} info */
    setInfo(info) {
      root.className = `session ${labelClass(info.color)}`;
      who.textContent = `${info.user}@${info.name}`;
      where.textContent = info.where;
      via.hidden = !info.via;
      via.textContent = info.via ? `via ${info.via}` : '';
      clear(keyName);
      keyName.classList.toggle('sbar__key--pw', info.password);
      if (info.password) keyName.append('Password');
      else keyName.append(icon('key', { size: 14 }), info.key);
    },
    /** @param {string} text */
    setUptime(text) { uptime.textContent = text; },
    /** @param {string|null} text @param {'off'|'error'|null} [kind] */
    setStatus(text, kind = null) {
      status.hidden = !text;
      status.textContent = text ?? '';
      status.className = `sbar__status${kind ? ` sbar__status--${kind}` : ''}`;
      uptime.hidden = Boolean(text);
    },
    /** @param {boolean} on */
    setLive(on) { disconnectBtn.disabled = !on; },

    /** @param {string} title */
    showConnecting(title) {
      overlayTitle.textContent = title;
      clear(checklist);
      lines.clear();
      overlay.hidden = false;
      root.classList.add('session--dim');
    },
    /** Mark every running line done, then add or update `key`. @param {string} key @param {string} text */
    step(key, text) {
      for (const [k, li] of lines) if (k !== key && li.dataset.state === 'running') setLine(li, 'done');
      let li = lines.get(key);
      if (!li) {
        li = h('li', { class: 'check-line' }, h('span', { class: 'check-line__mark', 'aria-hidden': 'true' }), h('span', { class: 'check-line__text' }));
        lines.set(key, li);
        checklist.appendChild(li);
      }
      /** @type {HTMLElement} */ (li.querySelector('.check-line__text')).textContent = text;
      setLine(li, 'running');
    },
    /** @param {'done'|'fail'} state */
    finishSteps(state) {
      for (const li of lines.values()) if (li.dataset.state === 'running') setLine(li, state);
    },
    hideOverlay() {
      overlay.hidden = true;
      root.classList.remove('session--dim');
    },

    /** @param {{title: string, body: (string|Node)[], detail?: string, actions: Action[]}} spec */
    showPanel(spec) {
      clear(panel);
      const heading = h('h2', { class: 'session__panel-title', tabindex: '-1' }, spec.title);
      panel.appendChild(h('div', { class: 'session__panel-box', role: 'alert' },
        heading,
        h('p', { class: 'session__panel-text' }, ...spec.body),
        h('div', { class: 'btn-row' }, ...spec.actions.map(actionButton)),
        spec.detail ? h('span', { class: 'session__panel-detail mono' }, spec.detail) : null));
      panel.hidden = false;
      termHost.classList.add('is-covered');
      return heading;
    },
    hidePanel() {
      panel.hidden = true;
      clear(panel);
      termHost.classList.remove('is-covered');
    },

    /** @param {{title: string, body: string, actions: Action[], tone?: 'info'|'off'}} spec */
    showBottom(spec) {
      clear(bottom);
      bottom.className = `session__bottom session__bottom--${spec.tone ?? 'off'}`;
      bottom.append(
        h('div', { class: 'session__bottom-text' },
          h('span', { class: 'session__bottom-title' }, spec.title),
          spec.body ? h('span', { class: 'session__bottom-body' }, spec.body) : null),
        ...spec.actions.map(actionButton));
      bottom.hidden = false;
    },
    hideBottom() {
      bottom.hidden = true;
      clear(bottom);
    },

    /** @param {boolean} open */
    showFind(open) {
      find.hidden = !open;
      if (open) {
        findInput.focus();
        findInput.select();
      }
    },
    get findOpen() { return !find.hidden; },
    /** @param {string} text */
    setFindNote(text) { findNote.textContent = text; },
  };
}

/** @param {HTMLElement} li @param {'running'|'done'|'fail'} state */
function setLine(li, state) {
  li.dataset.state = state;
  li.className = `check-line check-line--${state}`;
  const mark = /** @type {HTMLElement} */ (li.querySelector('.check-line__mark'));
  clear(mark);
  if (state === 'done') mark.appendChild(icon('check', { size: 14, strokeWidth: 3 }));
  else if (state === 'fail') mark.appendChild(icon('cross', { size: 14, strokeWidth: 3 }));
  const text = /** @type {HTMLElement} */ (li.querySelector('.check-line__text'));
  li.setAttribute('aria-label', `${text.textContent}${state === 'done' ? ', done' : state === 'fail' ? ', failed' : ''}`);
}

export { actionButton };
